"""Post-run advisory: turn a failed or expensively-recovered step into a concrete
"fix your recording" suggestion that the HTML report renders next to the step.

Two tiers, both ADVISORY ONLY -- recordings stay read-only (a human applies the edit):

  Tier 1 (deterministic, no LLM): when a step recovers, the runner has ALREADY discovered a
    locator that works (experience / AI / Oracle recovery records its ``locator_strategy``), so
    we echo it. For hard failures we classify the failure signature and point at the visible DOM
    candidates captured at failure time. Always available, zero cost, no network.

  Tier 2 (LLM, opt-in): for the same flagged steps, send a COMPACT package (the raw recorded
    line, the failure signature, the recovery outcome, DOM candidates, page context) to the model
    and ask for {root_cause, suggested_edit, confidence}. Gated by ACT_AI_SUGGESTIONS_ENABLED +
    OPENAI_API_KEY, fail-soft (any error -> no AI block, Tier 1 still renders), and budgeted per
    report via ACT_AI_SUGGESTIONS_MAX.

This runs at REPORT-GENERATION time off the already-captured action log, so it never touches or
slows the execution path and never runs on a clean run. It reuses the runner's existing OpenAI
transport (the certifi-backed urlopen + /responses endpoint), it does not open a second client.
"""

from __future__ import annotations

import json
import os
from typing import Any

# A step that succeeded only after this much wasted wall-clock is worth flagging even when it
# eventually recovered (the AR_Credit_Memo Quantity step burned ~71s on a hidden .first match).
_SLOW_STEP_MS_DEFAULT = 20000


def _env_flag(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except Exception:
        return default


def _slow_step_ms() -> int:
    return _env_int("ACT_SUGGESTION_SLOW_MS", _SLOW_STEP_MS_DEFAULT)


def _raw_line(action: dict[str, Any]) -> str:
    return str((action.get("script_data") or {}).get("raw") or "").strip()


def _duration_ms(action: dict[str, Any]) -> int:
    try:
        return int(action.get("duration_ms") or 0)
    except Exception:
        return 0


def _secs(duration_ms: int) -> str:
    return f"{duration_ms / 1000.0:.0f}"


def _has_fallback(action: dict[str, Any]) -> bool:
    # Mirror html_report_generator._action_has_fallback so "flagged" means the same thing.
    strategy = str(action.get("strategy") or "").strip().lower()
    return (action.get("fallback_strategy_count") or 1) > 1 or strategy == "raw_inline"


def _walk_debug(node: Any):
    if isinstance(node, dict):
        yield node
        for child in node.values():
            yield from _walk_debug(child)
    elif isinstance(node, list):
        for child in node:
            yield from _walk_debug(child)


def _direct_error(action: dict[str, Any]) -> str:
    for node in _walk_debug(action.get("debug")):
        direct = node.get("direct_attempt")
        if isinstance(direct, dict):
            error = str(direct.get("error") or "").strip()
            if error:
                return error
    return str(action.get("error") or "").strip()


def _resolved_by(action: dict[str, Any]) -> str:
    for node in _walk_debug(action.get("debug")):
        value = node.get("resolved_by")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _locator_strategy_to_text(strategy: Any) -> str:
    if isinstance(strategy, str):
        return strategy.strip()
    if isinstance(strategy, dict):
        for key in ("expr", "locator", "selector", "css", "value", "target"):
            value = strategy.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        steps = strategy.get("steps")
        if isinstance(steps, list) and steps:
            rendered = _steps_to_text(steps)
            if rendered:
                return rendered
        try:
            return json.dumps(strategy, ensure_ascii=False)[:240]
        except Exception:
            return str(strategy)[:240]
    return str(strategy or "").strip()


def _steps_to_text(steps: list[Any]) -> str:
    parts: list[str] = ["page"]
    for step in steps:
        if not isinstance(step, dict):
            continue
        method = str(step.get("method") or "").strip()
        if not method:
            continue
        if step.get("is_property"):
            parts.append(method)
            continue
        args = [repr(arg) for arg in (step.get("args") or [])]
        for key, value in (step.get("kwargs") or {}).items():
            args.append(f"{key}={value!r}")
        parts.append(f"{method}({', '.join(args)})")
    return ".".join(parts) if len(parts) > 1 else ""


def _recovered_locator_text(action: dict[str, Any]) -> str:
    recovery = action.get("recovery")
    if isinstance(recovery, dict):
        details = recovery.get("details")
        if isinstance(details, dict):
            text = _locator_strategy_to_text(details.get("locator_strategy"))
            if text:
                return text
    return ""


def _classify(direct_error: str) -> str:
    lowered = direct_error.lower()
    if "is disabled" in lowered or "not enabled" in lowered or "did not become enabled" in lowered:
        return "disabled"
    if "to be visible" in lowered or "not visible" in lowered or "be enabled" in lowered:
        return "not_visible"
    if any(
        marker in lowered
        for marker in (
            "no postcondition",
            "did not open",
            "did not reflect",
            "had no effect",
            "completed but",
            "did not change",
        )
    ):
        return "no_effect"
    return "other"


def _dom_candidate_lines(action: dict[str, Any], limit: int) -> list[str]:
    context = action.get("failure_context") or {}
    candidates = ((context.get("dom_context") or {}).get("candidates") or [])[:limit]
    lines: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        head = str(candidate.get("head") or candidate.get("title") or candidate.get("text") or "")
        head = " ".join(head.split())[:60]
        cid = str(candidate.get("id") or "").strip()
        if head and cid:
            lines.append(f"{head} (id={cid})")
        elif head:
            lines.append(head)
        elif cid:
            lines.append(f"id={cid}")
    return lines


def _should_suggest(action: dict[str, Any]) -> bool:
    if str(action.get("status") or "") == "failed":
        return True
    if _has_fallback(action):
        return True
    return _duration_ms(action) >= _slow_step_ms()


def build_deterministic_suggestion(action: dict[str, Any]) -> dict[str, Any] | None:
    """Tier 1: derive an advisory suggestion purely from the captured action log. None when the
    step is healthy (resolved strict, fast). Never calls the network."""
    if not isinstance(action, dict) or not _should_suggest(action):
        return None
    raw = _raw_line(action)
    if not raw:
        return None
    status = str(action.get("status") or "")
    direct_error = _direct_error(action)
    resolved_by = _resolved_by(action)
    recovered = _recovered_locator_text(action)
    duration_ms = _duration_ms(action)
    slow = duration_ms >= _slow_step_ms()

    if status == "failed":
        kind = _classify(direct_error)
        if kind == "disabled":
            # Dependent field that stayed disabled AND whose value differs from the request (an
            # auto-derived field already holding the requested value is auto-skipped upstream, so a
            # disabled *failure* means the value was wrong). A corrected locator can't help here.
            return {
                "severity": "error",
                "title": "Recording step failed — target field was disabled",
                "recorded": raw,
                "diagnosis": (
                    "The target field was disabled and its value did not match the requested one "
                    "— a dependent field whose controlling field wasn't set to derive this value."
                ),
                "recommended": (
                    "Set the controlling field to the value that derives the requested one. "
                    "(If the field is auto-derived and already shows the requested value, the "
                    "runner now skips it automatically — you don't need to remove the step.)"
                ),
                "recovered_locator": "",
                "ai": None,
            }
        if kind == "not_visible":
            diagnosis = "The recorded locator never became visible, so every recovery layer failed."
        elif kind == "no_effect":
            diagnosis = "The recorded action ran but changed nothing; no recovery target matched."
        else:
            diagnosis = "The recorded locator could not be resolved and no recovery layer worked."
        recommended = "Re-record this step against the exact element."
        candidates = _dom_candidate_lines(action, 4)
        if candidates:
            recommended += " Visible candidates at failure: " + "; ".join(candidates) + "."
        return {
            "severity": "error",
            "title": "Recording step failed — needs a corrected locator",
            "recorded": raw,
            "diagnosis": diagnosis,
            "recommended": recommended,
            "recovered_locator": "",
            "ai": None,
        }

    recovered_or_fellback = bool(recovered) or resolved_by not in ("", "strict", "direct")
    if recovered_or_fellback:
        kind = _classify(direct_error)
        if kind == "not_visible":
            diagnosis = "The recorded locator didn't resolve (its match wasn't visible)"
        elif kind == "no_effect":
            diagnosis = "The recorded action had no measurable effect (likely a wrong target)"
        else:
            diagnosis = "The recorded locator failed on the first attempt"
        diagnosis += f", so the runner recovered via {resolved_by or 'a recovery layer'}"
        if slow:
            diagnosis += f" after ~{_secs(duration_ms)}s of wasted waiting"
        diagnosis += "."
        if recovered:
            recommended = f"Use the locator the runner recovered with instead: {recovered}"
        else:
            recommended = (
                "Re-record this step against the element the runner recovered with "
                "(see the Execution Path / Debug Trace below)."
            )
        return {
            "severity": "warning",
            "title": "Recording step is fragile — it recovered, not on the recorded locator",
            "recorded": raw,
            "diagnosis": diagnosis,
            "recommended": recommended,
            "recovered_locator": recovered,
            "ai": None,
        }

    if slow:
        return {
            "severity": "info",
            "title": "Recording step is slow",
            "recorded": raw,
            "diagnosis": (
                f"The step succeeded on the recorded locator but took ~{_secs(duration_ms)}s "
                "(likely a heavy Oracle page refresh)."
            ),
            "recommended": (
                "If this recurs, target a more specific element or add a scoped wait; "
                "otherwise no change is needed."
            ),
            "recovered_locator": "",
            "ai": None,
        }
    return None


def ai_suggestions_enabled() -> bool:
    return _env_flag("ACT_AI_SUGGESTIONS_ENABLED")


_AI_SYSTEM_PROMPT = (
    "You review Playwright test recordings for Oracle Fusion (ADF / Redwood) flows. You are given "
    "ONE recorded step that failed or recovered slowly, plus the runner's failure signature, the "
    "locator the runner actually used to recover (if any), and the visible DOM candidates at that "
    "moment. Propose how to fix the RECORDING so the step works on the first attempt.\n"
    "Rules:\n"
    "- If the runner already recovered with a locator, prefer recommending that locator.\n"
    "- Prefer stable scoped Playwright locators (get_by_role / get_by_label, row-scoped) over "
    "brittle .first or get_by_title matches.\n"
    "- If the recorded step looks redundant with the surrounding flow, say so plainly.\n"
    "- Never invent element ids or attributes you were not given.\n"
    'Respond as compact JSON: {"root_cause": "<one sentence>", "suggested_edit": "<a concrete '
    'Playwright line or instruction>", "confidence": <number 0..1>}.'
)


def _ai_payload(action: dict[str, Any], suggestion: dict[str, Any]) -> dict[str, Any]:
    context = action.get("failure_context") or {}
    active = context.get("active_element") or {}
    active_text = ", ".join(
        f"{key}={value}" for key, value in active.items() if value not in (None, "", [], {})
    )
    return {
        "recorded_step": suggestion.get("recorded"),
        "action_type": action.get("action"),
        "label": action.get("label"),
        "status": action.get("status"),
        "duration_ms": _duration_ms(action),
        "failure_error": _direct_error(action)[:400],
        "resolved_by": _resolved_by(action),
        "runner_recovered_locator": suggestion.get("recovered_locator") or "",
        "page_title": str(context.get("page_title") or "").strip(),
        "active_element": active_text[:300],
        "dom_candidates": _dom_candidate_lines(action, 6),
        "deterministic_diagnosis": suggestion.get("diagnosis"),
    }


def enrich_with_ai(action: dict[str, Any], suggestion: dict[str, Any]) -> dict[str, Any] | None:
    """Tier 2: ask the model for a root-cause + concrete recording edit. Returns the AI block or
    None on any failure / when disabled (Tier 1 still renders). Reuses the runner's transport."""
    try:
        from src.runtime import helpers_v2 as facade
    except Exception:  # pragma: no cover - packaging fallback
        try:
            from runtime import helpers_v2 as facade
        except Exception:
            return None
    if not facade.get_runner_env_value("OPENAI_API_KEY"):
        return None

    model = os.getenv("ACT_AI_SUGGESTION_MODEL", "").strip() or facade._act_ai_self_repair_model()
    user_text = json.dumps(_ai_payload(action, suggestion), ensure_ascii=False, indent=2)
    body = {
        "model": model,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": _AI_SYSTEM_PROMPT}]},
            {"role": "user", "content": [{"type": "input_text", "text": user_text}]},
        ],
        "text": {"format": {"type": "json_object"}},
        "max_output_tokens": 500,
    }
    request = facade.Request(
        f"{facade._act_openai_base_url()}/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {facade.get_runner_env_value('OPENAI_API_KEY')}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        timeout_s = max(5.0, _env_int("ACT_AI_SUGGESTION_TIMEOUT_MS", 20000) / 1000.0)
        with facade.urlopen(request, timeout=timeout_s) as response:
            parsed = json.loads(response.read().decode("utf-8", errors="replace"))
        response_text = facade._act_extract_ai_output_text(parsed)
        data = facade._act_parse_ai_json_response(response_text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    root_cause = str(data.get("root_cause") or "").strip()
    suggested_edit = str(data.get("suggested_edit") or data.get("suggested_locator") or "").strip()
    if not root_cause and not suggested_edit:
        return None
    try:
        confidence: float | None = round(float(data.get("confidence")), 2)
    except Exception:
        confidence = None
    return {
        "root_cause": root_cause,
        "suggested_edit": suggested_edit,
        "confidence": confidence,
        "model": str(model),
    }


def attach_recording_suggestions(results: list[dict[str, Any]]) -> None:
    """Single pass over every result's action log: attach action["recording_suggestion"] for each
    flagged step (Tier 1 always; Tier 2 for error/warning steps while the per-report AI budget
    lasts). Mutates the actions in place so report rendering stays a pure read."""
    ai_budget = _env_int("ACT_AI_SUGGESTIONS_MAX", 5) if ai_suggestions_enabled() else 0
    for result in results or []:
        if not isinstance(result, dict):
            continue
        for action in result.get("action_log") or []:
            if not isinstance(action, dict):
                continue
            suggestion = build_deterministic_suggestion(action)
            if not suggestion:
                continue
            if ai_budget > 0 and suggestion.get("severity") in ("error", "warning"):
                ai_block = enrich_with_ai(action, suggestion)
                if ai_block is not None:
                    suggestion["ai"] = ai_block
                    ai_budget -= 1
            action["recording_suggestion"] = suggestion
