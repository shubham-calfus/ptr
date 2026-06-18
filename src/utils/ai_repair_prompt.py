from __future__ import annotations

import json
import re
from typing import Any

AI_REPAIR_SYSTEM_PROMPT = "You are a senior Playwright locator repair assistant. Return concise JSON only."


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _truncate(value: Any, limit: int) -> str:
    text = _normalize_text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _action_kind_for_helper(helper: str) -> str:
    normalized = _normalize_text(helper).lower()
    if normalized == "fill_textbox":
        return "fill"
    if normalized == "select_option_target":
        return "select_option"
    if normalized == "select_search_trigger_option":
        return "select_search_option"
    if normalized == "select_adf_menu_panel_option":
        return "select_menu_option"
    if normalized == "click_combobox":
        return "open_combobox"
    return "click"


def _select_request_value(helper: str, value: str | None, script_data: dict[str, Any]) -> dict[str, Any]:
    normalized_helper = _normalize_text(helper).lower()
    if normalized_helper == "select_option_target":
        return {
            "type": "select_option",
            "option_args": list(script_data.get("option_args") or []),
            "option_kwargs": dict(script_data.get("option_kwargs") or {}),
            "parsed_option_value": _normalize_text(((script_data.get("parsed_action") or {}).get("option_value"))),
        }
    if normalized_helper == "fill_textbox":
        return {"type": "fill", "value": _normalize_text(value or script_data.get("value"))}
    if value is not None:
        return {"type": "raw", "value": _normalize_text(value)}
    return {}


def _summarize_candidate(candidate: dict[str, Any], *, origin: str = "dom") -> dict[str, Any]:
    return {
        "origin": origin,
        "tag": _normalize_text(candidate.get("tag")),
        "role": _normalize_text(candidate.get("role")),
        "id": _normalize_text(candidate.get("id")),
        "name": _normalize_text(candidate.get("name")),
        "aria_label": _normalize_text(candidate.get("aria_label")),
        "labelledby_text": _normalize_text(candidate.get("labelledby_text")),
        "title": _normalize_text(candidate.get("title")),
        "placeholder": _normalize_text(candidate.get("placeholder")),
        "label_hint": _normalize_text(candidate.get("label_hint")),
        "data_oj_field": _normalize_text(candidate.get("data_oj_field")),
        "oracle_host_tag": _normalize_text(candidate.get("oracle_host_tag")),
        "oracle_host_id": _normalize_text(candidate.get("oracle_host_id")),
        "oracle_host_text": _truncate(candidate.get("oracle_host_text"), 220),
        "text": _truncate(candidate.get("text"), 220),
        "html": _truncate(candidate.get("html"), 360),
    }


def _recorded_target_candidate(locator_context: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(locator_context, dict) or not locator_context:
        return None
    summary = _summarize_candidate(locator_context, origin="recorded_target")
    oracle_host = locator_context.get("oracle_host") or {}
    if isinstance(oracle_host, dict):
        summary["oracle_host_tag"] = _normalize_text(oracle_host.get("tag")) or summary["oracle_host_tag"]
        summary["oracle_host_id"] = _normalize_text(oracle_host.get("id")) or summary["oracle_host_id"]
        summary["oracle_host_text"] = _truncate(oracle_host.get("text"), 220) or summary["oracle_host_text"]
        if not summary["html"]:
            summary["html"] = _truncate(oracle_host.get("html"), 360)
    if not any(summary.get(key) for key in ("tag", "role", "id", "aria_label", "title", "text", "oracle_host_id", "html")):
        return None
    return summary


def _select_relevant_candidates(
    helper: str,
    locator_context: dict[str, Any],
    dom_context: dict[str, Any],
    *,
    max_candidates: int = 6,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    def push(candidate: dict[str, Any] | None) -> None:
        if not isinstance(candidate, dict) or not candidate:
            return
        key = "|".join(
            [
                _normalize_text(candidate.get("origin")),
                _normalize_text(candidate.get("id")),
                _normalize_text(candidate.get("oracle_host_id")),
                _normalize_text(candidate.get("aria_label")),
                _normalize_text(candidate.get("title")),
                _normalize_text(candidate.get("text")),
            ]
        )
        if key in seen_keys:
            return
        seen_keys.add(key)
        candidates.append(candidate)

    push(_recorded_target_candidate(locator_context))

    raw_candidates = list((dom_context or {}).get("candidates") or [])
    normalized_helper = _normalize_text(helper).lower()
    for item in raw_candidates:
        if len(candidates) >= max_candidates:
            break
        summary = _summarize_candidate(item, origin="dom")
        if normalized_helper == "select_option_target" and not any(
            summary.get(key) for key in ("tag", "id", "aria_label", "title", "html")
        ):
            continue
        push(summary)

    return candidates[:max_candidates]


def build_ai_repair_prompt(
    *,
    helper: str,
    label: str,
    last_error: Any,
    value: str | None,
    page_title: str,
    page_url: str,
    script_data: dict[str, Any],
    locator_context: dict[str, Any],
    dom_context: dict[str, Any],
    retry_feedback: dict[str, Any] | None = None,
) -> str:
    concise_dom_context = {
        "helper": _normalize_text((dom_context or {}).get("helper")) or _normalize_text(helper),
        "label": _normalize_text((dom_context or {}).get("label")) or _normalize_text(label),
        "candidates": _select_relevant_candidates(helper, locator_context, dom_context),
    }
    request_value = _select_request_value(helper, value, script_data or {})
    error_text = _truncate(last_error, 1200)

    prompt = (
        "You repair Playwright locators for Oracle enterprise web apps. Return JSON only.\n"
        "Preserve the recorded target semantics.\n"
        "Prefer the recorded target context when it is more precise than nearby DOM candidates.\n"
        "Do not invent ids, labels, text, roles, or attributes that are not present.\n"
        "Prefer stable selectors based on id, aria-label, labelledby_text, label-hint, name, role, or data-oj-field.\n"
        "For Oracle controls, prefer the control host when the inner node is intercepted or stale.\n"
        "Return exactly this schema:\n"
        '{"strategies":[{"kind":"css"|"xpath"|"role"|"label"|"placeholder"|"text","selector":string|null,"role":string|null,"name":string|null,"text":string|null,"exact":boolean|null,"reason":string|null}]}\n'
        "Rules:\n"
        "- Return at most 3 strategies, best first.\n"
        "- Every strategy must target the requested control, not just a nearby element.\n"
        "- Do not repeat the same locator idea with tiny formatting changes.\n"
        f"- Helper: {_normalize_text(helper)}\n"
        f"- Intended action: {_action_kind_for_helper(helper)}\n"
        f"- Target label: {_normalize_text(label)}\n"
        f"- Page title: {_normalize_text(page_title) or 'unknown'}\n"
        f"- Page URL: {_normalize_text(page_url) or 'unknown'}\n"
        f"- Last execution error: {error_text or 'unknown'}\n"
        "Requested action value JSON:\n"
        f"{json.dumps(request_value, ensure_ascii=False)}\n"
        "Recorded script data JSON:\n"
        f"{json.dumps(script_data or {}, ensure_ascii=False)}\n"
        "Recorded target context JSON:\n"
        f"{json.dumps(locator_context or {}, ensure_ascii=False)}\n"
        "Relevant DOM candidates JSON:\n"
        f"{json.dumps(concise_dom_context, ensure_ascii=False)}"
    )

    if retry_feedback:
        prompt += (
            "\nRetry feedback JSON:\n"
            f"{json.dumps(retry_feedback, ensure_ascii=False)}\n"
            "The previous locator strategies failed. Return different strategies that address the retry feedback.\n"
            "Avoid repeating selectors or targets that already failed."
        )

    return prompt

