"""Auto-split from helpers_v2.py. `facade` is the helpers_v2 facade: the single
shared namespace, so monkeypatching helpers_v2.X and shared _ACT_* state
behave exactly as in the original module. Call shared helpers via `facade.`."""

from __future__ import annotations

import base64
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from playwright.sync_api import Locator, Page

try:
    from .. import helpers_v2 as facade
except ImportError:  # pragma: no cover
    from src.runtime import helpers_v2 as facade

__all__ = [
    "_act_rank_ai_dom_candidates",
    "_act_collect_ai_dom_candidates",
    "_act_capture_failure_context",
    "_act_store_experience_episode",
    "_act_request_experience_recovery",
    "_act_locator_from_repair_strategy",
    "_act_openai_base_url",
    "_act_ai_self_repair_enabled",
    "_act_ai_self_repair_model",
    "_act_extract_ai_output_text",
    "_act_parse_ai_json_response",
    "_act_build_ai_self_repair_prompt",
    "_act_capture_ai_context_screenshot",
    "_act_capture_ai_extract_context_screenshot",
    "_act_request_ai_self_repair",
    "_act_write_ai_extracted_outputs",
    "_act_normalize_ai_extract_text",
    "_act_compact_ai_extract_snapshot",
    "_act_build_ai_extract_prompt",
    "_act_ai_extract",
    "_act_ai_text_matches_label",
    "_act_ai_locator_matches_label",
    "_act_experience_repair_locators",
    "_act_ai_repair_locators",
    "_act_execute_ai_repair_rounds",
]


def _act_rank_ai_dom_candidates(
    helper: str, label: str, candidates: list[dict[str, Any]], max_candidates: int
) -> list[dict[str, Any]]:
    normalized_label = facade._act_normalize_text(label)
    normalized_helper = facade._act_normalize_text(helper)
    label_tokens = [token for token in re.findall("[a-z0-9]+", normalized_label) if len(token) > 1]

    def _score_text(value: Any, weight: int) -> int:
        return weight if facade._act_ai_text_matches_label(value, label) else 0

    def _score_candidate(candidate: dict[str, Any], index: int) -> tuple[int, int]:
        score = 0
        score += _score_text(candidate.get("text"), 60)
        score += _score_text(candidate.get("aria_label"), 50)
        score += _score_text(candidate.get("labelledby_text"), 50)
        score += _score_text(candidate.get("oracle_host_text"), 45)
        score += _score_text(candidate.get("oracle_host_data_oj_field"), 35)
        score += _score_text(candidate.get("title"), 45)
        score += _score_text(candidate.get("label_hint"), 40)
        score += _score_text(candidate.get("name"), 25)
        score += _score_text(candidate.get("id"), 15)
        score += _score_text(candidate.get("placeholder"), 10)
        score += _score_text(candidate.get("html"), 5)
        combined = " ".join(
            facade._act_normalize_text(candidate.get(key))
            for key in (
                "text",
                "aria_label",
                "labelledby_text",
                "oracle_host_text",
                "oracle_host_data_oj_field",
                "title",
                "label_hint",
                "placeholder",
                "name",
                "id",
                "html",
            )
        ).strip()
        if label_tokens and combined:
            matched_tokens = sum(1 for token in label_tokens if token in combined)
            if matched_tokens:
                score += matched_tokens * 8
                if matched_tokens == len(label_tokens):
                    score += 18
        tag = facade._act_normalize_text(candidate.get("tag"))
        role = facade._act_normalize_text(candidate.get("role"))
        html = facade._act_normalize_text(candidate.get("html"))
        text_length = len(facade._act_normalize_text(candidate.get("text")))
        if "menu" in normalized_helper:
            if role == "menuitem":
                score += 45
            if role in {"button", "link", "option"}:
                score += 18
            if tag in {"a", "button"}:
                score += 12
            if tag in {"select", "input", "textarea"}:
                score -= 40
            if "oj-popup" in html or 'role="menu"' in html or "role='menu'" in html:
                score += 15
            if text_length > 180:
                score -= 20
        elif "button" in normalized_helper:
            if tag in {"oj-action-card", "oj-switch"}:
                score += 40
            if "oj-action-card" in html or "oj-switch" in html:
                score += 30
            if role == "switch":
                score += 25
            if tag == "button":
                score += 5
            if tag in {"select", "textarea"}:
                score -= 25
            if text_length > 220:
                score -= 15
        elif "date" in normalized_helper:
            if tag in {"oj-input-date", "oj-c-input-date"}:
                score += 35
            if "select date" in html:
                score += 25
        elif (
            "combobox" in normalized_helper
            or "search" in normalized_helper
            or "select_option" in normalized_helper
            or (normalized_helper == "select_option")
        ):
            if tag == "select":
                score += 55
            if tag in {"oj-select-single", "oj-c-select-single"}:
                score += 45
            if candidate.get("oracle_host_tag") in {"oj-select-single", "oj-c-select-single"}:
                score += 35
            if role == "combobox":
                score += 25
            if candidate.get("data_oj_field"):
                score += 15
            if "oj-searchselect" in html:
                score += 20
        return (score, -index)

    ranked = [
        candidate
        for _, candidate in sorted(
            [
                (_score_candidate(candidate, idx), candidate)
                for idx, candidate in enumerate(candidates)
            ],
            key=lambda item: item[0],
            reverse=True,
        )
        if candidate
    ]
    if normalized_label:
        strong_matches = [
            candidate for candidate in ranked if _score_candidate(candidate, 0)[0] > 0
        ]
        if strong_matches:
            ranked = strong_matches + [
                candidate for candidate in ranked if candidate not in strong_matches
            ]
    return ranked[:max_candidates]


def _act_collect_ai_dom_candidates(current_page: Page, helper: str, label: str) -> dict[str, Any]:
    max_candidates = max(3, min(12, facade._act_int_env("ACT_AI_SELF_REPAIR_MAX_CANDIDATES", 8)))
    max_html_chars = max(
        240, min(2400, facade._act_int_env("ACT_AI_SELF_REPAIR_MAX_HTML_CHARS", 900))
    )
    max_text_chars = max(
        120, min(600, facade._act_int_env("ACT_AI_SELF_REPAIR_MAX_TEXT_CHARS", 220))
    )
    raw_candidate_cap = max(max_candidates * 6, 60)
    try:
        context = current_page.evaluate(
            '(payload) => {\n                const helper = String(payload?.helper || "").trim();\n                const label = String(payload?.label || "").trim();\n                const maxCandidates = Number(payload?.maxCandidates || 8);\n                const rawCandidateCap = Number(payload?.rawCandidateCap || 60);\n                const maxHtmlChars = Number(payload?.maxHtmlChars || 900);\n                const maxTextChars = Number(payload?.maxTextChars || 220);\n                const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();\n                const truncate = (value, limit) => {\n                    const text = normalize(value);\n                    if (!text || text.length <= limit) return text;\n                    return text.slice(0, Math.max(0, limit - 3)) + "...";\n                };\n                const isVisible = (candidate) => {\n                    if (!candidate) return false;\n                    const style = window.getComputedStyle(candidate);\n                    if (!style) return false;\n                    if (style.display === "none" || style.visibility === "hidden") return false;\n                    if (candidate.getAttribute?.("aria-hidden") === "true") return false;\n                    const rect = candidate.getBoundingClientRect();\n                    return rect.width > 0 && rect.height > 0;\n                };\n                const helperSelectors = [];\n                if (helper.includes("button")) {\n                    helperSelectors.push(\n                        "oj-action-card",\n                        ".oj-actioncard",\n                        "oj-switch",\n                        "[role=\'switch\']"\n                    );\n                }\n                if (helper.includes("date")) {\n                    helperSelectors.push(\n                        "oj-input-date",\n                        "oj-c-input-date",\n                        "[title=\'Select Date.\']",\n                        "[aria-label=\'Select Date.\']"\n                    );\n                }\n                const generalSelectors = [\n                    "select",\n                    "input",\n                    "textarea",\n                    "button",\n                    "a",\n                    "[role=\'textbox\']",\n                    "[role=\'spinbutton\']",\n                    "[role=\'combobox\']",\n                    "[role=\'button\']",\n                    "[role=\'switch\']",\n                    "[role=\'checkbox\']",\n                    "[role=\'tab\']",\n                    "[role=\'link\']",\n                    "[role=\'menuitem\']",\n                    "[role=\'option\']",\n                    "[role=\'cell\']",\n                    "[role=\'gridcell\']",\n                    "oj-select-single",\n                    "oj-c-select-single",\n                    "oj-input-text",\n                    "oj-c-input-text",\n                    "oj-input-number",\n                    "oj-c-input-number",\n                    "oj-input-date",\n                    "oj-c-input-date",\n                    "oj-text-area",\n                    "oj-c-text-area",\n                ];\n                const menuSelectors = [\n                    "[role=\'menu\']",\n                    "[role=\'menuitem\']",\n                    ".oj-popup",\n                    ".oj-popup [role=\'menuitem\']",\n                    "a",\n                    "button",\n                    "[role=\'button\']",\n                    "[role=\'link\']",\n                    "[role=\'option\']",\n                ];\n                const buttonSelectors = [\n                    "oj-action-card",\n                    ".oj-actioncard",\n                    "oj-switch",\n                    "[role=\'switch\']",\n                    "button",\n                    "a",\n                    "[role=\'button\']",\n                    "[role=\'link\']",\n                    "[role=\'tab\']",\n                    "[role=\'checkbox\']",\n                    "[role=\'menuitem\']",\n                ];\n                let selectors = [...helperSelectors, ...generalSelectors];\n                if (helper.includes("menu")) {\n                    selectors = [...helperSelectors, ...menuSelectors];\n                } else if (helper.includes("button")) {\n                    selectors = [...helperSelectors, ...buttonSelectors];\n                }\n                const seen = new Set();\n                const results = [];\n                const labelledByText = (candidate) => {\n                    const ids = normalize(candidate?.getAttribute?.("aria-labelledby"));\n                    if (!ids) return "";\n                    const values = [];\n                    for (const id of ids.split(/\\s+/)) {\n                        const node = document.getElementById(id);\n                        const text = truncate(node?.innerText || node?.textContent, maxTextChars);\n                        if (text) values.push(text);\n                    }\n                    return normalize(values.join(" "));\n                };\n                const pushCandidate = (candidate) => {\n                    if (!candidate || seen.has(candidate) || !isVisible(candidate)) return;\n                    seen.add(candidate);\n                    const role = normalize(candidate.getAttribute?.("role"));\n                    const text = truncate(candidate.innerText || candidate.textContent, maxTextChars);\n                    const oracleHost = candidate.closest?.("oj-select-single, oj-c-select-single");\n                    const entry = {\n                        tag: String(candidate.tagName || "").toLowerCase(),\n                        role,\n                        id: normalize(candidate.id),\n                        name: normalize(candidate.getAttribute?.("name")),\n                        aria_label: normalize(candidate.getAttribute?.("aria-label")),\n                        aria_labelledby: normalize(candidate.getAttribute?.("aria-labelledby")),\n                        aria_controls: normalize(candidate.getAttribute?.("aria-controls")),\n                        labelledby_text: truncate(labelledByText(candidate), maxTextChars),\n                        label_hint: normalize(candidate.getAttribute?.("label-hint")),\n                        placeholder: normalize(candidate.getAttribute?.("placeholder")),\n                        title: normalize(candidate.getAttribute?.("title")),\n                        data_oj_field: normalize(candidate.getAttribute?.("data-oj-field")),\n                        oracle_host_tag: normalize(oracleHost?.tagName).toLowerCase(),\n                        oracle_host_id: normalize(oracleHost?.id),\n                        oracle_host_text: truncate(oracleHost?.innerText || oracleHost?.textContent, maxTextChars),\n                        oracle_host_data_oj_field: normalize(oracleHost?.getAttribute?.("data-oj-field")),\n                        text,\n                        html: normalize(candidate.outerHTML).slice(0, maxHtmlChars),\n                    };\n                    if (\n                        !entry.text\n                        && !entry.aria_label\n                        && !entry.title\n                        && !entry.id\n                        && !entry.label_hint\n                        && !entry.labelledby_text\n                        && !entry.oracle_host_text\n                    ) {\n                        return;\n                    }\n                    results.push(entry);\n                };\n                for (const selector of selectors) {\n                    for (const candidate of document.querySelectorAll(selector)) {\n                        if (results.length >= rawCandidateCap) break;\n                        pushCandidate(candidate);\n                    }\n                    if (results.length >= rawCandidateCap) break;\n                }\n                return { helper, label, candidates: results.slice(0, rawCandidateCap) };\n            }',
            {
                "helper": helper,
                "label": label,
                "maxCandidates": max_candidates,
                "rawCandidateCap": raw_candidate_cap,
                "maxHtmlChars": max_html_chars,
                "maxTextChars": max_text_chars,
            },
        )
        if not isinstance(context, dict):
            return {"helper": helper, "label": label, "candidates": []}
        ranked = facade._act_rank_ai_dom_candidates(
            helper, label, list(context.get("candidates") or []), max_candidates
        )
        context["candidates"] = ranked
        return context
    except Exception:
        return {"helper": helper, "label": label, "candidates": []}


def _act_capture_failure_context(
    current_page: Page | None, helper: str, label: str, error: Any = None
) -> dict[str, Any]:
    try:
        page_url = str(current_page.url or "").strip() if current_page is not None else ""
    except Exception:
        page_url = ""
    try:
        page_title = str(current_page.title() or "").strip() if current_page is not None else ""
    except Exception:
        page_title = ""
    ready_state = facade._act_safe_page_eval(current_page, "() => document.readyState") or ""
    dom_context = (
        facade._act_collect_ai_dom_candidates(current_page, helper, label)
        if current_page is not None
        else {"helper": helper, "label": label, "candidates": []}
    )
    candidates = dom_context.get("candidates") or []
    return {
        "helper": helper,
        "label": label,
        "error": str(error or ""),
        "script_data": facade._act_current_script_data(),
        "page_url": page_url,
        "page_title": page_title,
        "ready_state": str(ready_state or ""),
        "busy_indicator_count": facade._act_busy_indicator_count(current_page),
        "active_element": facade._act_active_element(current_page),
        "dom_context": dom_context,
        "dom_candidate_count": len(candidates) if isinstance(candidates, list) else 0,
    }


def _act_store_experience_episode(
    *,
    action_type: str,
    label: str,
    page: Page | None,
    locator: Locator | None,
    error: Any = None,
    status: str,
    postcondition_kind: str,
    postcondition_passed: bool,
) -> None:
    if not facade._act_experience_enabled():
        return
    recovery = facade._ACT_CURRENT_STRATEGY.get("recovery")
    if status == "success" and (not recovery):
        return
    observation = facade._act_observe(page, locator)
    episode = {
        "episode_id": str(uuid.uuid4()),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runner_version": facade._ACT_RUNNER_VERSION,
        "app_family": "oracle",
        "ui_family": "oracle",
        "action_type": action_type,
        "target_label": label,
        "target_label_normalized": facade._act_normalize_text(label),
        "control_family": facade._act_control_family(action_type),
        "page_signature": facade._act_page_signature(page, observation),
        "failure_signature": facade._act_failure_signature(page, locator, error),
        "recovery": facade._act_clone_json_value(
            recovery or {"source": "strict", "kind": "direct", "handler_name": "strict"}
        ),
        "postcondition": {
            "kind": str(postcondition_kind or "").strip(),
            "passed": bool(postcondition_passed),
        },
        "outcome": {
            "status": str(status or "").strip(),
            "confidence": "high" if status == "success" and postcondition_passed else "low",
        },
    }
    try:
        facade._experience_append_episode(facade._ACT_EXPERIENCE_STORE_PATH, episode)
    except Exception:
        return


def _act_request_experience_recovery(
    current_page: Page, helper: str, label: str, last_error: Any, locator: Locator | None = None
) -> list[dict[str, Any]]:
    interaction: dict[str, Any] = {
        "feature": "experience_recovery",
        "helper": helper,
        "label": label,
        "store_path": facade._ACT_EXPERIENCE_STORE_PATH,
    }
    if not facade._act_experience_enabled():
        interaction["status"] = "disabled"
        interaction["error"] = "Experience recovery is disabled or store path is missing."
        facade._act_record_experience_interaction(interaction)
        return []
    page_signature = facade._act_page_signature(current_page)
    failure_signature = facade._act_failure_signature(current_page, locator, last_error)
    interaction["page_signature"] = facade._act_clone_json_value(page_signature)
    interaction["failure_signature"] = facade._act_clone_json_value(failure_signature)
    interaction["status"] = "requested"
    facade._act_record_experience_interaction(interaction)
    facade._act_record_strategy_attempt("experience_lookup")
    try:
        matches = facade._experience_retrieve_recovery_candidates(
            facade._ACT_EXPERIENCE_STORE_PATH,
            action_type=helper,
            target_label=label,
            control_family=facade._act_control_family(helper),
            page_signature=page_signature,
            failure_signature=failure_signature,
        )
    except Exception as exc:
        facade._act_update_last_experience_interaction(
            {"status": "request_error", "error_type": type(exc).__name__, "error": str(exc)}
        )
        return []
    facade._act_update_last_experience_interaction(
        {
            "status": "success" if matches else "miss",
            "candidate_count": len(matches),
            "candidate_kinds": [
                str((item.get("recovery") or {}).get("kind") or "").strip() for item in matches
            ],
            "candidate_scores": [int(item.get("retrieval_score") or 0) for item in matches],
        }
    )
    return matches


def _act_locator_from_repair_strategy(
    current_page: Page, strategy: dict[str, Any], prefix: str, idx: int
) -> tuple[str, Locator | None]:
    kind = str(strategy.get("kind") or "").strip().lower()
    selector = str(strategy.get("selector") or "").strip()
    role = str(strategy.get("role") or "").strip()
    name = str(strategy.get("name") or "").strip()
    text = str(strategy.get("text") or "").strip()
    exact = bool(strategy.get("exact")) if isinstance(strategy.get("exact"), bool) else False
    locator: Locator | None = None
    strategy_name = ""
    try:
        if kind == "css" and selector:
            locator = current_page.locator(selector).first
            strategy_name = f"{prefix}_css_{idx}"
        elif kind == "xpath" and selector:
            locator = current_page.locator(
                selector if selector.startswith("xpath=") else f"xpath={selector}"
            ).first
            strategy_name = f"{prefix}_xpath_{idx}"
        elif kind == "role" and role:
            locator = current_page.get_by_role(role, name=name, exact=exact).first
            strategy_name = f"{prefix}_role_{idx}"
        elif kind == "label" and text:
            locator = current_page.get_by_label(text, exact=exact).first
            strategy_name = f"{prefix}_label_{idx}"
        elif kind == "placeholder" and text:
            locator = current_page.get_by_placeholder(text, exact=exact).first
            strategy_name = f"{prefix}_placeholder_{idx}"
        elif kind == "text" and text:
            locator = current_page.get_by_text(text, exact=exact).first
            strategy_name = f"{prefix}_text_{idx}"
    except Exception:
        locator = None
    return (strategy_name, locator)


def _act_openai_base_url() -> str:
    return str(os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")


def _act_ai_self_repair_enabled() -> bool:
    if not facade._act_env_flag("ACT_AI_SELF_REPAIR_ENABLED", "false"):
        return False
    return bool(facade.get_runner_env_value("OPENAI_API_KEY"))


def _act_ai_self_repair_model() -> str:
    return (
        str(
            os.getenv(
                "ACT_AI_SELF_REPAIR_MODEL",
                os.getenv("OPENAI_FAILURE_SUMMARY_MODEL", "gpt-5.4-mini"),
            )
        ).strip()
        or "gpt-5.4-mini"
    )


def _act_extract_ai_output_text(payload: dict[str, Any]) -> str:
    direct = str(payload.get("output_text") or "").strip()
    if direct:
        return direct
    parts: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            text = str(content.get("text") or "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _act_parse_ai_json_response(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub("^```(?:json)?\\s*", "", cleaned)
        cleaned = re.sub("\\s*```$", "", cleaned)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("AI response was not a JSON object.")
    return parsed


def _act_build_ai_self_repair_prompt(
    current_page: Page,
    helper: str,
    label: str,
    last_error: Any,
    value: str | None = None,
    locator: Locator | None = None,
    dom_context: dict[str, Any] | None = None,
    retry_feedback: dict[str, Any] | None = None,
) -> str:
    try:
        page_url = str(current_page.url or "").strip()
    except Exception:
        page_url = ""
    try:
        page_title = str(current_page.title() or "").strip()
    except Exception:
        page_title = ""
    if dom_context is None:
        dom_context = facade._act_collect_ai_dom_candidates(current_page, helper, label)
    return facade.build_ai_repair_prompt(
        helper=helper,
        label=label,
        last_error=last_error,
        value=value,
        page_title=page_title,
        page_url=page_url,
        script_data=facade._act_current_script_data(),
        locator_context=facade._act_capture_locator_context(locator),
        dom_context=dom_context,
        retry_feedback=retry_feedback,
    )


def _act_capture_ai_context_screenshot(current_page: Page | None) -> dict[str, Any]:
    if current_page is None:
        return {}
    image_format = facade._act_normalize_text(
        os.getenv("ACT_AI_SELF_REPAIR_SCREENSHOT_FORMAT", "jpeg")
    )
    if image_format not in {"jpeg", "png"}:
        image_format = "jpeg"
    media_type = "image/jpeg" if image_format == "jpeg" else "image/png"
    screenshot_kwargs: dict[str, Any] = {"full_page": True}
    if image_format == "jpeg":
        screenshot_kwargs["type"] = "jpeg"
        screenshot_kwargs["quality"] = max(
            20, min(95, facade._act_int_env("ACT_AI_SELF_REPAIR_SCREENSHOT_QUALITY", 45))
        )
    else:
        screenshot_kwargs["type"] = "png"
    screenshot_scale = facade._act_normalize_text(
        os.getenv("ACT_AI_SELF_REPAIR_SCREENSHOT_SCALE", "css")
    )
    if screenshot_scale in {"css", "device"}:
        screenshot_kwargs["scale"] = screenshot_scale
    try:
        screenshot_bytes = current_page.screenshot(**screenshot_kwargs)
        if not isinstance(screenshot_bytes, bytes) or not screenshot_bytes:
            return {}
        return {
            "status": "captured",
            "image_url": f"data:{media_type};base64,{base64.b64encode(screenshot_bytes).decode('ascii')}",
            "media_type": media_type,
            "format": image_format,
            "full_page": True,
            "scale": screenshot_kwargs.get("scale") or "",
            "quality": screenshot_kwargs.get("quality"),
        }
    except Exception as exc:
        return {
            "status": "capture_error",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "media_type": media_type,
            "format": image_format,
            "full_page": True,
            "scale": screenshot_kwargs.get("scale") or "",
            "quality": screenshot_kwargs.get("quality"),
        }


def _act_capture_ai_extract_context_screenshot(current_page: Page | None) -> dict[str, Any]:
    if current_page is None:
        return {}
    wait_ms = facade._act_wait_ms("ACT_AI_EXTRACT_PRE_CAPTURE_WAIT_MS", 1200)
    try:
        current_page.wait_for_timeout(wait_ms)
    except Exception:
        pass
    try:
        facade._act_capture_page_snapshot(current_page)
    except Exception:
        pass
    screenshot_kwargs: dict[str, Any] = {
        "full_page": facade._act_env_flag("ACT_AI_EXTRACT_SCREENSHOT_FULL_PAGE", "false"),
        "type": "png",
    }
    screenshot_scale = facade._act_normalize_text(
        os.getenv(
            "ACT_AI_EXTRACT_SCREENSHOT_SCALE",
            os.getenv("ACT_AI_SELF_REPAIR_SCREENSHOT_SCALE", "css"),
        )
    )
    if screenshot_scale in {"css", "device"}:
        screenshot_kwargs["scale"] = screenshot_scale
    try:
        screenshot_bytes = current_page.screenshot(**screenshot_kwargs)
        if not isinstance(screenshot_bytes, bytes) or not screenshot_bytes:
            return {
                "status": "empty",
                "media_type": "image/png",
                "format": "png",
                "full_page": bool(screenshot_kwargs.get("full_page")),
                "scale": screenshot_kwargs.get("scale") or "",
                "pre_capture_wait_ms": wait_ms,
            }
        facade._ACT_NEXT_STEP_SCREENSHOT_OVERRIDE_PNG = screenshot_bytes
        return {
            "status": "captured",
            "image_url": f"data:image/png;base64,{base64.b64encode(screenshot_bytes).decode('ascii')}",
            "media_type": "image/png",
            "format": "png",
            "full_page": bool(screenshot_kwargs.get("full_page")),
            "scale": screenshot_kwargs.get("scale") or "",
            "pre_capture_wait_ms": wait_ms,
        }
    except Exception as exc:
        facade._ACT_NEXT_STEP_SCREENSHOT_OVERRIDE_PNG = None
        return {
            "status": "capture_error",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "media_type": "image/png",
            "format": "png",
            "full_page": bool(screenshot_kwargs.get("full_page")),
            "scale": screenshot_kwargs.get("scale") or "",
            "pre_capture_wait_ms": wait_ms,
        }


def _act_request_ai_self_repair(
    current_page: Page,
    helper: str,
    label: str,
    last_error: Any,
    value: str | None = None,
    locator: Locator | None = None,
    retry_feedback: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    endpoint = f"{facade._act_openai_base_url()}/responses"
    model = facade._act_ai_self_repair_model()
    interaction: dict[str, Any] = {
        "feature": "self_repair",
        "helper": helper,
        "label": label,
        "model": model,
        "endpoint": endpoint,
        "system_prompt": facade.AI_REPAIR_SYSTEM_PROMPT,
    }
    if value is not None:
        interaction["value"] = str(value)
    script_data = facade._act_current_script_data()
    if script_data:
        interaction["script_data"] = facade._act_clone_json_value(script_data)
        interaction["recorded_script_data"] = facade._act_clone_json_value(script_data)
    locator_context = facade._act_capture_locator_context(locator)
    if locator_context:
        interaction["recorded_target_context"] = facade._act_clone_json_value(locator_context)
    if retry_feedback:
        interaction["retry_feedback"] = facade._act_clone_json_value(retry_feedback)
    if last_error not in (None, ""):
        interaction["last_error"] = str(last_error)
    if not facade._act_ai_self_repair_enabled():
        interaction["status"] = "disabled"
        interaction["error"] = "AI self-repair is disabled or OPENAI_API_KEY is missing."
        facade._act_record_ai_interaction(interaction)
        return []
    screenshot_context = facade._act_capture_ai_context_screenshot(current_page)
    if screenshot_context:
        interaction["page_screenshot"] = facade._act_clone_json_value(screenshot_context)
    dom_context = facade._act_collect_ai_dom_candidates(current_page, helper, label)
    prompt = facade._act_build_ai_self_repair_prompt(
        current_page,
        helper,
        label,
        last_error,
        value=value,
        locator=locator,
        dom_context=dom_context,
        retry_feedback=retry_feedback,
    )
    if str((screenshot_context or {}).get("status") or "").strip() == "captured":
        prompt += "\nA full-page screenshot is attached in this request as additional context.\nUse the screenshot together with the recorded target context and DOM candidates."
    interaction["user_prompt"] = prompt
    interaction["dom_candidates"] = facade._act_clone_json_value(dom_context)
    interaction["dom_candidate_count"] = len(dom_context.get("candidates") or [])
    interaction["max_output_tokens"] = 400
    if not interaction["dom_candidate_count"]:
        interaction["status"] = "skipped_no_candidates"
        interaction["error"] = "No DOM candidates were collected for AI self-repair."
        facade._act_record_ai_interaction(interaction)
        return []
    interaction["status"] = "requested"
    facade._act_record_ai_interaction(interaction)
    facade._act_record_strategy_attempt("ai_self_repair_lookup")
    user_content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    if str((screenshot_context or {}).get("status") or "").strip() == "captured":
        image_url = str((screenshot_context or {}).get("image_url") or "").strip()
        if image_url:
            user_content.append({"type": "input_image", "image_url": image_url})
    payload = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": facade.AI_REPAIR_SYSTEM_PROMPT}],
            },
            {"role": "user", "content": user_content},
        ],
        "text": {"format": {"type": "json_object"}},
        "max_output_tokens": 400,
    }
    request = facade.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {facade.get_runner_env_value('OPENAI_API_KEY')}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    response_body = ""
    try:
        timeout_s = max(5.0, facade._act_wait_ms("ACT_AI_SELF_REPAIR_TIMEOUT_MS", 15000) / 1000.0)
        with facade.urlopen(request, timeout=timeout_s) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(response_body)
            facade._act_update_last_ai_interaction(
                {
                    "http_status": int(getattr(response, "status", 0) or 0),
                    "api_response_body": response_body,
                }
            )
    except facade.HTTPError as exc:
        error_body = ""
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            error_body = ""
        facade._act_update_last_ai_interaction(
            {
                "status": "request_error",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "http_status": int(getattr(exc, "code", 0) or 0),
                "error_response_body": error_body,
            }
        )
        return []
    except facade.URLError as exc:
        facade._act_update_last_ai_interaction(
            {
                "status": "request_error",
                "error_type": type(exc).__name__,
                "error": str(getattr(exc, "reason", exc)),
            }
        )
        return []
    except Exception as exc:
        facade._act_update_last_ai_interaction(
            {
                "status": "request_error",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "api_response_body": response_body,
            }
        )
        return []
    response_text = facade._act_extract_ai_output_text(parsed)
    facade._act_update_last_ai_interaction({"response_text": response_text})
    try:
        parsed_response = facade._act_parse_ai_json_response(response_text)
    except Exception as exc:
        facade._act_update_last_ai_interaction(
            {"status": "parse_error", "error_type": type(exc).__name__, "error": str(exc)}
        )
        return []
    strategies = parsed_response.get("strategies")
    if not isinstance(strategies, list):
        facade._act_update_last_ai_interaction(
            {
                "status": "invalid_response",
                "error": "AI response JSON did not contain a strategies list.",
                "parsed_response": parsed_response,
            }
        )
        return []
    normalized = [item for item in strategies[:3] if isinstance(item, dict)]
    facade._act_update_last_ai_interaction(
        {
            "status": "success" if normalized else "empty",
            "parsed_response": parsed_response,
            "response_strategy_count": len(normalized),
            "response_strategies": normalized,
        }
    )
    return normalized


def _act_write_ai_extracted_outputs() -> None:
    """Surface ai_extract() values as flow-context outputs for downstream
    recordings (same ``ACT_SCRIPT_STEP_OUTPUT_PATH`` channel the runner reads)."""
    output_path = str(os.getenv("ACT_SCRIPT_STEP_OUTPUT_PATH", "") or "").strip()
    if not output_path or not facade._ACT_AI_EXTRACTED:
        return
    try:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"outputs": dict(facade._ACT_AI_EXTRACTED)}, ensure_ascii=False, default=str
            ),
            encoding="utf-8",
        )
    except Exception:
        return


def _act_normalize_ai_extract_text(value: Any, limit: int) -> str:
    text = str(value or "").replace("\xa0", " ")
    text = re.sub("\\s+", " ", text).strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _act_compact_ai_extract_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    compact: dict[str, Any] = {}
    page_title = facade._act_normalize_ai_extract_text(snapshot.get("page_title"), 160)
    page_url = facade._act_normalize_ai_extract_text(snapshot.get("page_url"), 240)
    page_text = facade._act_normalize_ai_extract_text(snapshot.get("page_text"), 4000)
    if page_title:
        compact["page_title"] = page_title
    if page_url:
        compact["page_url"] = page_url
    if page_text:
        compact["page_text"] = page_text
    raw_tables = snapshot.get("oracle_tables")
    if isinstance(raw_tables, list):
        tables: list[dict[str, Any]] = []
        for table in raw_tables[:2]:
            if not isinstance(table, dict):
                continue
            headers = [
                facade._act_normalize_ai_extract_text(item, 120)
                for item in (table.get("headers") or [])[:12]
                if facade._act_normalize_ai_extract_text(item, 120)
            ]
            rows: list[list[str]] = []
            for row in (table.get("rows") or [])[:5]:
                if not isinstance(row, list):
                    continue
                compact_row = [
                    facade._act_normalize_ai_extract_text(cell, 160)
                    for cell in row[: max(1, len(headers) or 12)]
                ]
                if any(compact_row):
                    rows.append(compact_row)
            if headers or rows:
                tables.append({"headers": headers, "rows": rows})
        if tables:
            compact["oracle_tables"] = tables
    semantics = snapshot.get("page_semantics")
    if isinstance(semantics, dict):
        label_values: list[dict[str, str]] = []
        for item in (semantics.get("label_values") or [])[:20]:
            if not isinstance(item, dict):
                continue
            label = facade._act_normalize_ai_extract_text(item.get("label"), 120)
            value = facade._act_normalize_ai_extract_text(item.get("value"), 180)
            if label and value:
                label_values.append({"label": label, "value": value})
        text_candidates: list[str] = []
        for item in (semantics.get("text_candidates") or [])[:30]:
            if not isinstance(item, dict):
                continue
            text = (
                facade._act_normalize_ai_extract_text(item.get("text"), 160)
                or facade._act_normalize_ai_extract_text(item.get("title"), 160)
                or facade._act_normalize_ai_extract_text(item.get("aria_label"), 160)
            )
            if text:
                text_candidates.append(text)
        dialogs: list[dict[str, str]] = []
        for item in (semantics.get("dialogs") or [])[:3]:
            if not isinstance(item, dict):
                continue
            title = facade._act_normalize_ai_extract_text(item.get("title"), 160)
            text = facade._act_normalize_ai_extract_text(item.get("text"), 600)
            if title or text:
                dialogs.append({"title": title, "text": text})
        if label_values or text_candidates or dialogs:
            compact["page_semantics"] = {}
            if label_values:
                compact["page_semantics"]["label_values"] = label_values
            if text_candidates:
                compact["page_semantics"]["text_candidates"] = text_candidates
            if dialogs:
                compact["page_semantics"]["dialogs"] = dialogs
    return compact


def _act_build_ai_extract_prompt(
    instruction: str,
    snapshot_context: dict[str, Any] | None = None,
    *,
    has_screenshot: bool = False,
) -> str:
    parts = [
        f"Requested extraction: {instruction}",
        'Return JSON only in the form {"value": "<the exact value>"}.',
        "Prefer explicit values from Oracle table rows, label-value pairs, dialogs, links, and visible page text.",
        "If the request mentions ordering such as first row or second row, preserve that ordering exactly.",
    ]
    if snapshot_context:
        parts.append("Structured page evidence:")
        parts.append(json.dumps(snapshot_context, ensure_ascii=False))
    if has_screenshot:
        parts.append(
            "A full-page screenshot is attached. Use it together with the structured page evidence."
        )
    return "\n".join(parts)


def _act_ai_extract(page: Page, name: str, prompt: str) -> str:
    """Capture the current page and ask the vision LLM for the value described by
    ``prompt``; store it under ``name`` for later ``{{name}}`` substitution and as
    a flow-context output. Raises if AI is unavailable or returns an empty value."""
    output_name = str(name or "").strip()
    instruction = str(prompt or "").strip()
    if not output_name:
        raise ValueError("ai_extract requires a non-empty value name.")
    if not instruction:
        raise ValueError(f"ai_extract('{output_name}', ...) requires a non-empty prompt.")
    endpoint = f"{facade._act_openai_base_url()}/responses"
    model = facade._act_ai_self_repair_model()
    interaction: dict[str, Any] = {
        "feature": "ai_extract",
        "label": output_name,
        "model": model,
        "endpoint": endpoint,
        "system_prompt": facade._ACT_AI_EXTRACT_SYSTEM_PROMPT,
        "user_prompt": instruction,
    }
    if not facade._act_ai_self_repair_enabled():
        interaction["status"] = "disabled"
        interaction["error"] = (
            "ai_extract is unavailable: AI is disabled or OPENAI_API_KEY is missing."
        )
        facade._act_record_ai_interaction(interaction)
        raise RuntimeError(
            f"ai_extract('{output_name}') failed: AI is disabled or OPENAI_API_KEY is missing."
        )
    current_page = page or facade._ACT_LAST_PAGE
    screenshot_context = (
        facade._act_capture_ai_extract_context_screenshot(current_page)
        if current_page is not None
        else {}
    )
    snapshot_context: dict[str, Any] = {}
    if current_page is not None:
        snapshot_context = facade._act_compact_ai_extract_snapshot(
            dict(facade._ACT_LAST_PAGE_SNAPSHOT)
        )
        if not snapshot_context:
            try:
                snapshot_context = facade._act_compact_ai_extract_snapshot(
                    facade._act_capture_page_snapshot(current_page)
                )
            except Exception:
                snapshot_context = facade._act_compact_ai_extract_snapshot(
                    dict(facade._ACT_LAST_PAGE_SNAPSHOT)
                )
    else:
        snapshot_context = facade._act_compact_ai_extract_snapshot(
            dict(facade._ACT_LAST_PAGE_SNAPSHOT)
        )
    if screenshot_context:
        interaction["page_screenshot"] = facade._act_clone_json_value(screenshot_context)
    if snapshot_context:
        interaction["page_snapshot"] = facade._act_clone_json_value(snapshot_context)
    has_screenshot = str((screenshot_context or {}).get("status") or "").strip() == "captured"
    user_content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": facade._act_build_ai_extract_prompt(
                instruction, snapshot_context, has_screenshot=has_screenshot
            ),
        }
    ]
    if has_screenshot:
        image_url = str((screenshot_context or {}).get("image_url") or "").strip()
        if image_url:
            user_content.append({"type": "input_image", "image_url": image_url})
    interaction["status"] = "requested"
    facade._act_record_ai_interaction(interaction)
    facade._act_record_strategy_attempt("ai_extract")
    payload = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": facade._ACT_AI_EXTRACT_SYSTEM_PROMPT}],
            },
            {"role": "user", "content": user_content},
        ],
        "text": {"format": {"type": "json_object"}},
        "max_output_tokens": 200,
    }
    request = facade.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {facade.get_runner_env_value('OPENAI_API_KEY')}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    response_body = ""
    try:
        timeout_s = max(5.0, facade._act_wait_ms("ACT_AI_SELF_REPAIR_TIMEOUT_MS", 15000) / 1000.0)
        with facade.urlopen(request, timeout=timeout_s) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(response_body)
            facade._act_update_last_ai_interaction(
                {
                    "http_status": int(getattr(response, "status", 0) or 0),
                    "api_response_body": response_body,
                }
            )
    except facade.HTTPError as exc:
        error_body = ""
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            error_body = ""
        facade._act_update_last_ai_interaction(
            {
                "status": "request_error",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "error_response_body": error_body,
            }
        )
        raise RuntimeError(f"ai_extract('{output_name}') failed: AI request error: {exc}") from exc
    except Exception as exc:
        facade._act_update_last_ai_interaction(
            {
                "status": "request_error",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "api_response_body": response_body,
            }
        )
        raise RuntimeError(f"ai_extract('{output_name}') failed: AI request error: {exc}") from exc
    response_text = facade._act_extract_ai_output_text(parsed)
    facade._act_update_last_ai_interaction({"response_text": response_text})
    value = ""
    try:
        parsed_response = facade._act_parse_ai_json_response(response_text)
        facade._act_update_last_ai_interaction({"parsed_response": parsed_response})
        if isinstance(parsed_response, dict) and parsed_response.get("value") is not None:
            value = str(parsed_response.get("value")).strip()
    except Exception:
        value = str(response_text or "").strip()
    if not value:
        facade._act_update_last_ai_interaction({"status": "empty", "extracted_value": ""})
        raise RuntimeError(
            f"ai_extract('{output_name}') returned an empty value for prompt: {instruction}"
        )
    facade._ACT_AI_EXTRACTED[output_name] = value
    facade._act_write_ai_extracted_outputs()
    facade._act_update_last_ai_interaction(
        {"status": "success", "extracted_name": output_name, "extracted_value": value}
    )
    return value


def _act_ai_text_matches_label(value: Any, label: str) -> bool:
    normalized_label = facade._act_normalize_text(label)
    normalized_value = facade._act_normalize_text(value)
    if not normalized_label:
        return True
    if not normalized_value:
        return False
    oracle_notification_signature = facade._act_oracle_notification_badge_signature(
        normalized_label
    )
    if (
        oracle_notification_signature
        and oracle_notification_signature
        == facade._act_oracle_notification_badge_signature(normalized_value)
    ):
        return True
    if normalized_value == normalized_label or normalized_label in normalized_value:
        return True
    label_tokens = [token for token in re.findall("[a-z0-9]+", normalized_label) if len(token) > 1]
    if not label_tokens:
        return normalized_label in normalized_value
    if len(label_tokens) == 1:
        return label_tokens[0] in normalized_value
    return all(token in normalized_value for token in label_tokens)


def _act_ai_locator_matches_label(locator: Locator, label: str) -> bool:
    if not str(label or "").strip():
        return True
    metadata = facade._act_extract_locator_metadata(locator)
    for key in (
        "aria_label",
        "labelledby_text",
        "oracle_host_text",
        "title",
        "name",
        "id",
        "label_hint",
        "data_oj_field",
        "oracle_host_data_oj_field",
        "text",
    ):
        if facade._act_ai_text_matches_label(metadata.get(key), label):
            return True
    return False


def _act_experience_repair_locators(
    current_page: Page, helper: str, label: str, last_error: Any, locator: Locator | None = None
) -> list[tuple[str, Locator, dict[str, Any]]]:
    locators: list[tuple[str, Locator, dict[str, Any]]] = []
    for idx, episode in enumerate(
        facade._act_request_experience_recovery(
            current_page, helper, label, last_error, locator=locator
        ),
        start=1,
    ):
        recovery = episode.get("recovery") or {}
        if str(recovery.get("kind") or "").strip() != "ai_locator_repair":
            continue
        strategy = (recovery.get("details") or {}).get("locator_strategy") or {}
        if not isinstance(strategy, dict) or not strategy:
            continue
        strategy_name, candidate = facade._act_locator_from_repair_strategy(
            current_page, strategy, "experience", idx
        )
        if candidate is None or not strategy_name:
            continue
        if not facade._act_ai_locator_matches_label(candidate, label):
            continue
        locators.append((strategy_name, candidate, episode))
    return locators


def _act_ai_repair_locators(
    current_page: Page,
    helper: str,
    label: str,
    last_error: Any,
    value: str | None = None,
    locator: Locator | None = None,
    retry_feedback: dict[str, Any] | None = None,
) -> list[tuple[str, Locator, dict[str, Any]]]:
    locators: list[tuple[str, Locator, dict[str, Any]]] = []
    rejected_names: list[str] = []
    rejected_reasons: list[str] = []
    for idx, strategy in enumerate(
        facade._act_request_ai_self_repair(
            current_page,
            helper,
            label,
            last_error,
            value=value,
            locator=locator,
            retry_feedback=retry_feedback,
        ),
        start=1,
    ):
        declared_label = str(strategy.get("name") or strategy.get("text") or "").strip()
        strategy_name, locator = facade._act_locator_from_repair_strategy(
            current_page, strategy, "ai", idx
        )
        if locator is None or not strategy_name:
            continue
        if declared_label and (not facade._act_ai_text_matches_label(declared_label, label)):
            rejected_names.append(strategy_name)
            rejected_reasons.append(
                f"{strategy_name}: response target does not match requested label"
            )
            continue
        if not facade._act_ai_locator_matches_label(locator, label):
            rejected_names.append(strategy_name)
            rejected_reasons.append(
                f"{strategy_name}: resolved element does not match requested label"
            )
            continue
        locators.append((strategy_name, locator, strategy))
    if facade._ACT_CURRENT_STRATEGY.get("ai_interactions"):
        patch: dict[str, Any] = {
            "locator_candidate_count": len(locators),
            "locator_strategies": [name for name, _, _ in locators],
            "rejected_locator_strategies": rejected_names,
            "rejected_locator_reasons": rejected_reasons,
        }
        if not locators:
            try:
                last_interaction = (facade._ACT_CURRENT_STRATEGY.get("ai_interactions") or [])[
                    -1
                ] or {}
            except Exception:
                last_interaction = {}
            response_strategy_count = int(
                last_interaction.get("response_strategy_count") or 0
                if isinstance(last_interaction, dict)
                else 0
            )
            if response_strategy_count > 0:
                patch["repair_outcome"] = "no_usable_locator"
        facade._act_update_last_ai_interaction(patch)
    return locators


def _act_execute_ai_repair_rounds(
    *,
    current_page: Page,
    helper: str,
    label: str,
    last_error: Any,
    value: str | None = None,
    locator: Locator | None = None,
    postcondition_kind: str,
    failure_message,
    execute_locator,
) -> tuple[tuple[str, Locator, dict[str, Any]] | None, Exception]:
    max_rounds = max(1, min(2, facade._act_int_env("ACT_AI_SELF_REPAIR_MAX_ROUNDS", 2)))
    retry_feedback: dict[str, Any] | None = None
    latest_error: Exception = (
        last_error if isinstance(last_error, Exception) else RuntimeError(str(last_error))
    )
    used_ai = False
    last_ai_strategy_name = ""
    for round_index in range(1, max_rounds + 1):
        ai_candidates = facade._act_ai_repair_locators(
            current_page,
            helper,
            label,
            latest_error,
            value=value,
            locator=locator,
            retry_feedback=retry_feedback,
        )
        if not ai_candidates:
            break
        used_ai = True
        attempted_names: list[str] = []
        for strategy_name, ai_locator, ai_strategy in ai_candidates:
            attempted_names.append(strategy_name)
            last_ai_strategy_name = strategy_name
            try:
                facade._act_record_strategy_attempt(strategy_name)
                if execute_locator(strategy_name, ai_locator, ai_strategy):
                    facade._act_finalize_last_ai_interaction(
                        repair_outcome="validated",
                        strategy_name=strategy_name,
                        postcondition_kind=postcondition_kind,
                    )
                    return ((strategy_name, ai_locator, ai_strategy), latest_error)
                latest_error = RuntimeError(str(failure_message(strategy_name)))
            except Exception as exc:
                latest_error = exc
        if round_index >= max_rounds:
            break
        last_interaction = facade._act_last_ai_interaction()
        retry_feedback = {
            "round": round_index,
            "execution_error": str(latest_error),
            "attempted_locator_strategies": attempted_names,
            "rejected_locator_reasons": list(
                last_interaction.get("rejected_locator_reasons") or []
            ),
            "previous_response_strategies": facade._act_clone_json_value(
                list(last_interaction.get("response_strategies") or [])
            ),
            "previous_response_text": str(last_interaction.get("response_text") or "").strip(),
        }
        facade._act_update_last_ai_interaction(
            {
                "retry_requested": True,
                "retry_round": round_index + 1,
                "retry_feedback": facade._act_clone_json_value(retry_feedback),
            }
        )
    if used_ai:
        facade._act_finalize_last_ai_interaction(
            repair_outcome="execution_failed",
            strategy_name=last_ai_strategy_name,
            error=latest_error,
            postcondition_kind=postcondition_kind,
        )
    return (None, latest_error)
