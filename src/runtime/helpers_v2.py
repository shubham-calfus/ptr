from __future__ import annotations

import atexit
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from playwright.sync_api import Locator, Page
from src.runtime.experience import (
    append_episode as _experience_append_episode,
    retrieve_recovery_candidates as _experience_retrieve_recovery_candidates,
)

__all__ = [
    "_ptr_launch_chromium",
    "_ptr_register_page",
    "_ptr_set_script_data",
    "_ptr_wait_ms",
    "_ptr_wait_after_interaction",
    "_ptr_capture_failure",
    "_ptr_write_diagnostics",
    "_ptr_tracked_action",
    "_ptr_goto_page",
    "_ptr_raw_click",
    "_ptr_raw_fill",
    "_ptr_raw_press",
    "_ptr_login_submit_and_redirect",
    "_ptr_fill_textbox",
    "_ptr_submit_textbox_enter",
    "_ptr_click_textbox",
    "_ptr_click_combobox",
    "_ptr_click_button_target",
    "_ptr_click_numeric_button_target",
    "_ptr_click_text_target",
    "_ptr_click_listbox_option",
    "_ptr_select_combobox_option",
    "_ptr_select_search_trigger_option",
    "_ptr_select_adf_menu_panel_option",
    "_ptr_pick_date_via_icon",
    "_ptr_click_navigation_button",
    "_ptr_wait_for_post_login_redirect",
]

_PTR_LAST_PAGE: Page | None = None
_PTR_STEP_INDEX = 0
_PTR_STEP_ARTIFACTS: list[dict[str, Any]] = []
_PTR_ACTION_LOG: list[dict[str, Any]] = []
_PTR_SCRIPT_DATA: dict[str, Any] = {}
_PTR_CURRENT_STRATEGY = {
    "helper": "",
    "strategy": "direct",
    "label": "",
    "attempts": [],
    "ai_interactions": [],
    "experience_interactions": [],
    "script_data": {},
    "recovery": None,
}

_PTR_DIAGNOSTICS_PATH = os.getenv("PTR_DIAGNOSTICS_PATH", "")
_PTR_FAILURE_SCREENSHOT_PATH = os.getenv("PTR_FAILURE_SCREENSHOT_PATH", "")
_PTR_STEP_ARTIFACTS_DIR = os.getenv("PTR_STEP_ARTIFACTS_DIR", "")
_PTR_EXPERIENCE_STORE_PATH = os.getenv("PTR_EXPERIENCE_STORE_PATH", "")
_PTR_RUNNER_VERSION = str(os.getenv("PTR_RUNNER_VERSION", "ptr-v2")).strip() or "ptr-v2"
_PTR_SUPPRESS_PATCH_CAPTURE = 0

_PTR_POPUP_SCOPE_SELECTORS = [
    '[role="dialog"]:visible',
    '[aria-modal="true"]:visible',
    '[role="menu"]:visible',
    '[role="listbox"]:visible',
    ".oj-dialog:visible",
    ".oj-popup:visible",
]


def _ptr_env_flag(name: str, default: str = "true") -> bool:
    return str(os.getenv(name, default)).strip().lower() not in ("false", "0", "no", "off")


def _ptr_wait_ms(env_name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(env_name, str(default))))
    except Exception:
        return default


def _ptr_int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _ptr_clone_json_value(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)


def _ptr_set_script_data(payload: dict[str, Any] | None = None) -> None:
    global _PTR_SCRIPT_DATA
    if isinstance(payload, dict) and payload:
        _PTR_SCRIPT_DATA = _ptr_clone_json_value(payload) or {}
    else:
        _PTR_SCRIPT_DATA = {}


def _ptr_current_script_data() -> dict[str, Any]:
    current = _PTR_CURRENT_STRATEGY.get("script_data") or _PTR_SCRIPT_DATA
    return _ptr_clone_json_value(current or {}) or {}


def _ptr_reset_strategy_tracking(helper: str, label: str = "") -> None:
    _PTR_CURRENT_STRATEGY["helper"] = helper
    _PTR_CURRENT_STRATEGY["strategy"] = "direct"
    _PTR_CURRENT_STRATEGY["label"] = label
    _PTR_CURRENT_STRATEGY["attempts"] = []
    _PTR_CURRENT_STRATEGY["ai_interactions"] = []
    _PTR_CURRENT_STRATEGY["experience_interactions"] = []
    _PTR_CURRENT_STRATEGY["script_data"] = _ptr_clone_json_value(_PTR_SCRIPT_DATA or {}) or {}
    _PTR_CURRENT_STRATEGY["recovery"] = None


def _ptr_record_strategy_attempt(strategy: str) -> None:
    normalized = str(strategy or "").strip()
    if not normalized:
        return
    attempts = _PTR_CURRENT_STRATEGY.setdefault("attempts", [])
    attempts.append(normalized)
    _PTR_CURRENT_STRATEGY["strategy"] = normalized


def _ptr_record_ai_interaction(entry: dict[str, Any]) -> None:
    if not isinstance(entry, dict) or not entry:
        return
    interactions = _PTR_CURRENT_STRATEGY.setdefault("ai_interactions", [])
    if not isinstance(interactions, list):
        interactions = []
        _PTR_CURRENT_STRATEGY["ai_interactions"] = interactions
    interactions.append(_ptr_clone_json_value(entry))


def _ptr_update_last_ai_interaction(patch: dict[str, Any]) -> None:
    interactions = _PTR_CURRENT_STRATEGY.setdefault("ai_interactions", [])
    if not interactions or not isinstance(interactions[-1], dict):
        return
    current = _ptr_clone_json_value(interactions[-1]) or {}
    if isinstance(current, dict):
        current.update(_ptr_clone_json_value(patch))
        interactions[-1] = current


def _ptr_finalize_last_ai_interaction(
    *,
    repair_outcome: str,
    strategy_name: str = "",
    error: Any = None,
    postcondition_kind: str = "",
) -> None:
    outcome = str(repair_outcome or "").strip()
    if not outcome:
        return

    patch: dict[str, Any] = {"repair_outcome": outcome}
    normalized_strategy = str(strategy_name or "").strip()
    if normalized_strategy:
        patch["last_locator_strategy"] = normalized_strategy
        if outcome == "validated":
            patch["validated_locator_strategy"] = normalized_strategy
    normalized_postcondition = str(postcondition_kind or "").strip()
    if normalized_postcondition:
        patch["postcondition_kind"] = normalized_postcondition
        patch["postcondition_passed"] = outcome == "validated"
    error_text = str(error or "").strip()
    if error_text:
        patch["repair_error"] = error_text
    _ptr_update_last_ai_interaction(patch)


def _ptr_record_experience_interaction(entry: dict[str, Any]) -> None:
    if not isinstance(entry, dict) or not entry:
        return
    interactions = _PTR_CURRENT_STRATEGY.setdefault("experience_interactions", [])
    if not isinstance(interactions, list):
        interactions = []
        _PTR_CURRENT_STRATEGY["experience_interactions"] = interactions
    interactions.append(_ptr_clone_json_value(entry))


def _ptr_update_last_experience_interaction(patch: dict[str, Any]) -> None:
    interactions = _PTR_CURRENT_STRATEGY.setdefault("experience_interactions", [])
    if not interactions or not isinstance(interactions[-1], dict):
        return
    current = _ptr_clone_json_value(interactions[-1]) or {}
    if isinstance(current, dict):
        current.update(_ptr_clone_json_value(patch))
        interactions[-1] = current


def _ptr_set_recovery_record(source: str, kind: str, handler_name: str, details: dict[str, Any] | None = None) -> None:
    _PTR_CURRENT_STRATEGY["recovery"] = {
        "source": str(source or "").strip(),
        "kind": str(kind or "").strip(),
        "handler_name": str(handler_name or "").strip(),
        "details": _ptr_clone_json_value(details or {}),
    }


def _ptr_strategy_snapshot() -> tuple[list[str], list[str], str]:
    attempts = [
        str(item).strip()
        for item in (_PTR_CURRENT_STRATEGY.get("attempts") or [])
        if str(item).strip()
    ]
    unique_attempts: list[str] = []
    seen: set[str] = set()
    for strategy in attempts:
        if strategy in seen:
            continue
        seen.add(strategy)
        unique_attempts.append(strategy)
    final_strategy = str(_PTR_CURRENT_STRATEGY.get("strategy") or "").strip() or "direct"
    return attempts, unique_attempts, final_strategy


def _ptr_launch_chromium(playwright, headless: bool = False):
    desired_headless = _ptr_env_flag("PTR_HEADLESS", "false")
    window_width = max(960, _ptr_int_env("PTR_WINDOW_WIDTH", 1440))
    window_height = max(700, _ptr_int_env("PTR_WINDOW_HEIGHT", 900))
    launch_kwargs: dict[str, Any] = {
        "channel": "chromium",
        "headless": desired_headless if not headless else headless,
        "args": [f"--window-size={window_width},{window_height}"],
    }
    return playwright.chromium.launch(**launch_kwargs)


def _ptr_register_page(page: Page) -> Page:
    global _PTR_LAST_PAGE
    _PTR_LAST_PAGE = page
    return page


def _ptr_locator_element_handle(locator: Locator, timeout_ms: int | None = None):
    timeout = max(50, int(timeout_ms or _ptr_int_env("PTR_LOCATOR_SNAPSHOT_TIMEOUT_MS", 250)))
    try:
        return locator.element_handle(timeout=timeout)
    except Exception:
        return None


def _ptr_safe_locator_eval(locator: Locator, expression: str, arg: Any | None = None) -> Any:
    try:
        handle = _ptr_locator_element_handle(locator)
        if handle is None:
            return None
        if arg is None:
            return handle.evaluate(expression)
        return handle.evaluate(expression, arg)
    except Exception:
        return None


def _ptr_safe_page_eval(page: Page | None, expression: str) -> Any:
    if page is None:
        return None
    try:
        return page.evaluate(expression)
    except Exception:
        return None


def _ptr_locator_value(locator: Locator) -> str:
    try:
        handle = _ptr_locator_element_handle(locator)
        if handle is None:
            return ""
        value = handle.evaluate(
            r"""(node) => {
                if (!node) return "";
                if ("value" in node) return String(node.value || "");
                return "";
            }"""
        )
        return str(value or "").strip()
    except Exception:
        return ""


def _ptr_locator_text(locator: Locator) -> str:
    try:
        handle = _ptr_locator_element_handle(locator)
        if handle is None:
            return ""
        value = handle.evaluate(
            r"""(node) => String(node?.innerText || node?.textContent || "").replace(/\s+/g, " ").trim()"""
        )
        return str(value or "").strip()
    except Exception:
        return ""


def _ptr_normalize_text(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _ptr_rank_ai_dom_candidates(helper: str, label: str, candidates: list[dict[str, Any]], max_candidates: int) -> list[dict[str, Any]]:
    normalized_label = _ptr_normalize_text(label)
    label_tokens = [token for token in re.findall(r"[a-z0-9]+", normalized_label) if len(token) > 1]

    def _score_text(value: Any, weight: int) -> int:
        return weight if _ptr_ai_text_matches_label(value, label) else 0

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
            _ptr_normalize_text(candidate.get(key))
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

        tag = _ptr_normalize_text(candidate.get("tag"))
        role = _ptr_normalize_text(candidate.get("role"))
        html = _ptr_normalize_text(candidate.get("html"))
        if "button" in helper:
            if tag in {"oj-action-card", "oj-switch"}:
                score += 40
            if "oj-action-card" in html or "oj-switch" in html:
                score += 30
            if role == "switch":
                score += 25
            if tag == "button":
                score += 5
        elif "date" in helper:
            if tag in {"oj-input-date", "oj-c-input-date"}:
                score += 35
            if "select date" in html:
                score += 25
        elif "combobox" in helper or "search" in helper:
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

        return score, -index

    ranked = [
        candidate
        for _, candidate in sorted(
            [(_score_candidate(candidate, idx), candidate) for idx, candidate in enumerate(candidates)],
            key=lambda item: item[0],
            reverse=True,
        )
        if candidate
    ]
    if normalized_label:
        strong_matches = [candidate for candidate in ranked if _score_candidate(candidate, 0)[0] > 0]
        if strong_matches:
            ranked = strong_matches + [candidate for candidate in ranked if candidate not in strong_matches]
    return ranked[:max_candidates]


def _ptr_extract_locator_metadata(locator: Locator) -> dict[str, str]:
    metadata = _ptr_safe_locator_eval(
        locator,
        r"""(node) => {
            const text = (value) => String(value || "").replace(/\s+/g, " ").trim();
            const labelledByText = () => {
                const ids = text(node?.getAttribute?.("aria-labelledby"));
                if (!ids) return "";
                const values = [];
                for (const id of ids.split(/\s+/)) {
                    const candidate = document.getElementById(id);
                    const candidateText = text(candidate?.innerText || candidate?.textContent);
                    if (candidateText) values.push(candidateText);
                }
                return text(values.join(" "));
            };
            const oracleHost = node?.closest?.("oj-select-single, oj-c-select-single");
            return {
                tag: String(node?.tagName || "").toLowerCase(),
                role: text(node?.getAttribute?.("role")),
                id: text(node?.id),
                name: text(node?.getAttribute?.("name")),
                aria_label: text(node?.getAttribute?.("aria-label")),
                aria_labelledby: text(node?.getAttribute?.("aria-labelledby")),
                aria_controls: text(node?.getAttribute?.("aria-controls")),
                title: text(node?.getAttribute?.("title")),
                placeholder: text(node?.getAttribute?.("placeholder")),
                label_hint: text(node?.getAttribute?.("label-hint")),
                data_oj_field: text(node?.getAttribute?.("data-oj-field")),
                labelledby_text: labelledByText(),
                text: text(node?.innerText || node?.textContent),
                class_name: text(node?.className),
                oracle_host_tag: text(oracleHost?.tagName).toLowerCase(),
                oracle_host_id: text(oracleHost?.id),
                oracle_host_text: text(oracleHost?.innerText || oracleHost?.textContent),
                oracle_host_data_oj_field: text(oracleHost?.getAttribute?.("data-oj-field")),
                aria_expanded: text(node?.getAttribute?.("aria-expanded")),
                aria_disabled: text(node?.getAttribute?.("aria-disabled")),
                disabled: node?.disabled ? "true" : (node?.hasAttribute?.("disabled") ? "true" : ""),
                aria_selected: text(node?.getAttribute?.("aria-selected")),
                aria_checked: text(node?.getAttribute?.("aria-checked")),
            };
        }""",
    )
    return metadata if isinstance(metadata, dict) else {}


def _ptr_capture_locator_context(locator: Locator | None) -> dict[str, Any]:
    if locator is None:
        return {}
    context = _ptr_safe_locator_eval(
        locator,
        r"""(node) => {
            const text = (value) => String(value || "").replace(/\s+/g, " ").trim();
            const labelledByText = () => {
                const ids = text(node?.getAttribute?.("aria-labelledby"));
                if (!ids) return "";
                const values = [];
                for (const id of ids.split(/\s+/)) {
                    const candidate = document.getElementById(id);
                    const candidateText = text(candidate?.innerText || candidate?.textContent);
                    if (candidateText) values.push(candidateText);
                }
                return text(values.join(" "));
            };
            const oracleHost = node?.closest?.("oj-select-single, oj-c-select-single");
            return {
                tag: text(node?.tagName).toLowerCase(),
                role: text(node?.getAttribute?.("role")),
                id: text(node?.id),
                aria_label: text(node?.getAttribute?.("aria-label")),
                aria_labelledby: text(node?.getAttribute?.("aria-labelledby")),
                labelledby_text: labelledByText(),
                aria_controls: text(node?.getAttribute?.("aria-controls")),
                placeholder: text(node?.getAttribute?.("placeholder")),
                title: text(node?.getAttribute?.("title")),
                class_name: text(node?.className),
                data_oj_field: text(node?.getAttribute?.("data-oj-field")),
                text: text(node?.innerText || node?.textContent),
                html: text(node?.outerHTML).slice(0, 1200),
                oracle_host: oracleHost ? {
                    tag: text(oracleHost?.tagName).toLowerCase(),
                    id: text(oracleHost?.id),
                    text: text(oracleHost?.innerText || oracleHost?.textContent),
                    data_oj_field: text(oracleHost?.getAttribute?.("data-oj-field")),
                    labelled_by: text(oracleHost?.getAttribute?.("labelled-by")),
                    html: text(oracleHost?.outerHTML).slice(0, 1200),
                } : {},
            };
        }""",
    )
    return context if isinstance(context, dict) else {}


def _ptr_locator_is_actionable(locator: Locator, timeout_ms: int | None = None) -> bool:
    timeout = timeout_ms or _ptr_wait_ms("PTR_ACTION_TIMEOUT_MS", 3000)
    try:
        locator.wait_for(state="visible", timeout=timeout)
        try:
            locator.scroll_into_view_if_needed(timeout=min(timeout, 1000))
        except Exception:
            pass
        return True
    except Exception:
        return False


def _ptr_strict_click(locator: Locator, timeout_ms: int | None = None) -> None:
    timeout = timeout_ms or _ptr_wait_ms("PTR_ACTION_TIMEOUT_MS", 3000)
    locator.wait_for(state="visible", timeout=timeout)
    try:
        locator.scroll_into_view_if_needed(timeout=min(timeout, 1000))
    except Exception:
        pass
    locator.click(timeout=timeout)


def _ptr_strict_fill(locator: Locator, value: str, timeout_ms: int | None = None) -> None:
    timeout = timeout_ms or _ptr_wait_ms("PTR_ACTION_TIMEOUT_MS", 3000)
    locator.wait_for(state="visible", timeout=timeout)
    try:
        locator.scroll_into_view_if_needed(timeout=min(timeout, 1000))
    except Exception:
        pass
    locator.fill(value, timeout=timeout)


def _ptr_enter_search_value(
    locator: Locator,
    value: str,
    timeout_ms: int | None = None,
    *,
    current_page: Page | None = None,
    label: str = "",
) -> None:
    timeout = timeout_ms or _ptr_wait_ms("PTR_TEXT_ENTRY_TIMEOUT_MS", 3000)
    locator.wait_for(state="visible", timeout=timeout)
    try:
        locator.scroll_into_view_if_needed(timeout=min(timeout, 1000))
    except Exception:
        pass
    try:
        locator.click(timeout=timeout)
    except Exception as click_exc:
        page = current_page or _PTR_LAST_PAGE
        oracle_strategy_name = ""
        if page is not None:
            oracle_strategy_name = _ptr_try_open_oracle_select_single_with_keyboard(page, locator, click_exc)
        if not oracle_strategy_name:
            raise
        _ptr_set_recovery_record(
            "oracle_handler",
            "oracle_select_single_keyboard_open",
            "oracle_select_single_keyboard_open",
            {
                "trigger_label": label,
                "strategy_name": oracle_strategy_name,
            },
        )

    try:
        locator.press("ControlOrMeta+A", timeout=timeout)
        locator.press("Backspace", timeout=timeout)
    except Exception:
        try:
            locator.fill("", timeout=timeout)
        except Exception:
            pass

    text = str(value or "")
    if not text:
        return

    key_delay = _ptr_wait_ms("PTR_SEARCH_KEY_DELAY_MS", 75)
    try:
        locator.press_sequentially(text, delay=key_delay, timeout=timeout)
    except Exception:
        try:
            locator.type(text, delay=key_delay, timeout=timeout)
        except Exception:
            locator.fill(text, timeout=timeout)


def _ptr_guided_flow_state(page: Page | None) -> dict[str, Any]:
    result = _ptr_safe_page_eval(
        page,
        r"""() => {
            const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
            const isVisible = (node) => {
                if (!node) return false;
                const style = window.getComputedStyle(node);
                if (!style) return false;
                if (style.display === "none" || style.visibility === "hidden") return false;
                if (node.getAttribute?.("aria-hidden") === "true") return false;
                const rect = node.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const textOf = (node) => normalize(node?.innerText || node?.textContent || "");
            const firstVisibleText = (selectors) => {
                for (const selector of selectors) {
                    for (const node of document.querySelectorAll(selector)) {
                        if (!isVisible(node)) continue;
                        const text = textOf(node);
                        if (text) return text;
                    }
                }
                return "";
            };
            const dedupe = (items) => {
                const seen = new Set();
                const values = [];
                for (const item of items) {
                    const normalized = normalize(item);
                    if (!normalized || seen.has(normalized)) continue;
                    seen.add(normalized);
                    values.push(normalized);
                }
                return values;
            };

            const selectedStep = firstVisibleText([
                '[role="tab"][aria-selected="true"]',
                '[role="tab"].oj-selected',
                '.oj-navigationlist-item.oj-selected',
                '[aria-current="step"]',
                '.oj-sp-guided-process-right-panel-navigation-list-step.oj-selected',
            ]);

            let progressCounter = "";
            const counterPattern = /^\d+\s*\|\s*\d+$/;
            for (const node of document.querySelectorAll("body *")) {
                if (!isVisible(node)) continue;
                const text = textOf(node);
                if (counterPattern.test(text)) {
                    progressCounter = text;
                    break;
                }
            }

            const primaryHeading = firstVisibleText([
                "main h1",
                "main h2",
                "main h3",
                '[role="main"] h1',
                '[role="main"] h2',
                '[role="main"] h3',
                '[role="heading"][aria-level="1"]',
                '[role="heading"][aria-level="2"]',
                '[role="heading"][aria-level="3"]',
                'h1',
                'h2',
                'h3',
                '.oj-typography-heading-lg',
                '.oj-typography-heading-md',
                '.oj-typography-heading-sm',
            ]);

            const footerActions = [];
            for (const node of document.querySelectorAll("button, [role='button'], a[title], a[aria-label]")) {
                if (!isVisible(node)) continue;
                const rect = node.getBoundingClientRect();
                if (rect.top < window.innerHeight * 0.55) continue;
                const label = normalize(
                    node.getAttribute?.("aria-label") ||
                    node.getAttribute?.("title") ||
                    node.innerText ||
                    node.textContent ||
                    ""
                );
                if (label) footerActions.push(label);
            }

            return {
                selected_step: selectedStep,
                progress_counter: progressCounter,
                primary_heading: primaryHeading,
                footer_actions: dedupe(footerActions).slice(0, 8),
            };
        }""",
    )
    return result if isinstance(result, dict) else {}


def _ptr_current_guided_step(page: Page | None) -> str:
    guided_flow = _ptr_guided_flow_state(page)
    return str((guided_flow or {}).get("selected_step") or "").strip()


def _ptr_dialog_count(page: Page | None) -> int:
    result = _ptr_safe_page_eval(
        page,
        r"""() => {
            const selectors = [
                '[role="dialog"]',
                '[aria-modal="true"]',
                '[role="menu"]',
                '[role="listbox"]',
                '.oj-dialog',
                '.oj-popup',
            ];
            const isVisible = (node) => {
                if (!node) return false;
                const style = window.getComputedStyle(node);
                if (!style) return false;
                if (style.display === "none" || style.visibility === "hidden") return false;
                const rect = node.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const seen = new Set();
            let count = 0;
            for (const selector of selectors) {
                for (const node of document.querySelectorAll(selector)) {
                    if (!isVisible(node)) continue;
                    if (seen.has(node)) continue;
                    seen.add(node);
                    count += 1;
                }
            }
            return count;
        }""",
    )
    try:
        return int(result or 0)
    except Exception:
        return 0


def _ptr_active_element(page: Page | None) -> dict[str, str]:
    result = _ptr_safe_page_eval(
        page,
        r"""() => {
            const node = document.activeElement;
            const text = (value) => String(value || "").replace(/\s+/g, " ").trim();
            return {
                tag: String(node?.tagName || "").toLowerCase(),
                role: text(node?.getAttribute?.("role")),
                id: text(node?.id),
                name: text(node?.getAttribute?.("name")),
                aria_label: text(node?.getAttribute?.("aria-label")),
                title: text(node?.getAttribute?.("title")),
                text: text(node?.innerText || node?.textContent),
            };
        }""",
    )
    return result if isinstance(result, dict) else {}


def _ptr_body_marker(page: Page | None) -> str:
    result = _ptr_safe_page_eval(
        page,
        r"""() => String(document.body?.innerText || "").replace(/\s+/g, " ").trim().slice(0, 800)""",
    )
    return str(result or "").strip()


def _ptr_observe(page: Page | None, locator: Locator | None = None) -> dict[str, Any]:
    try:
        url = str(page.url or "").strip() if page is not None else ""
    except Exception:
        url = ""
    try:
        title = str(page.title() or "").strip() if page is not None else ""
    except Exception:
        title = ""
    observation = {
        "url": url,
        "title": title,
        "guided_step": _ptr_current_guided_step(page),
        "guided_flow": _ptr_guided_flow_state(page),
        "dialog_count": _ptr_dialog_count(page),
        "active_element": _ptr_active_element(page),
        "body_marker": _ptr_body_marker(page),
        "target_value": "",
        "target_text": "",
        "target_visible": False,
        "target_meta": {},
    }
    if locator is not None:
        try:
            observation["target_visible"] = bool(locator.is_visible())
        except Exception:
            observation["target_visible"] = False
        observation["target_value"] = _ptr_locator_value(locator)
        observation["target_text"] = _ptr_locator_text(locator)
        observation["target_meta"] = _ptr_extract_locator_metadata(locator)
    return observation


def _ptr_generic_click_postcondition(before: dict[str, Any], after: dict[str, Any]) -> bool:
    keys = ("url", "title", "guided_step", "guided_flow", "dialog_count", "body_marker", "active_element")
    if any(before.get(key) != after.get(key) for key in keys):
        return True
    if before.get("target_visible") != after.get("target_visible"):
        return True
    if before.get("target_text") != after.get("target_text"):
        return True
    if before.get("target_value") != after.get("target_value"):
        return True
    if before.get("target_meta") != after.get("target_meta"):
        return True
    return False


def _ptr_combobox_open_postcondition(before: dict[str, Any], after: dict[str, Any]) -> bool:
    if int(after.get("dialog_count") or 0) > int(before.get("dialog_count") or 0):
        return True
    before_meta = before.get("target_meta") if isinstance(before.get("target_meta"), dict) else {}
    after_meta = after.get("target_meta") if isinstance(after.get("target_meta"), dict) else {}
    before_expanded = _ptr_normalize_text(before_meta.get("aria_expanded"))
    after_expanded = _ptr_normalize_text(after_meta.get("aria_expanded"))
    if after_expanded == "true" and before_expanded != after_expanded:
        return True
    return _ptr_generic_click_postcondition(before, after)


def _ptr_value_matches(expected: str, observed: str) -> bool:
    normalized_expected = _ptr_normalize_text(expected)
    normalized_observed = _ptr_normalize_text(observed)
    if not normalized_expected:
        return bool(normalized_observed)
    if normalized_expected == normalized_observed:
        return True
    return normalized_expected in normalized_observed or normalized_observed in normalized_expected


def _ptr_normalize_runtime_action_name(name: Any) -> str:
    normalized = str(name or "").strip().strip("_")
    if normalized.startswith("ptr_"):
        normalized = normalized[4:]
    return normalized or "unknown"


def _ptr_option_selection_postcondition(
    before: dict[str, Any],
    after: dict[str, Any],
    trigger: Locator,
    option_locator: Locator,
    option_name: str,
) -> bool:
    observed = _ptr_locator_value(trigger) or _ptr_locator_text(trigger)
    if _ptr_value_matches(option_name, observed):
        return True
    if int(after.get("dialog_count") or 0) < int(before.get("dialog_count") or 0):
        return True
    if _ptr_generic_click_postcondition(before, after):
        return True
    if int(before.get("dialog_count") or 0) > 0 and not _ptr_locator_is_actionable(option_locator, timeout_ms=250):
        return True
    return False


def _ptr_guided_flow_advanced(before: dict[str, Any], after: dict[str, Any]) -> bool:
    before_state = before if isinstance(before, dict) else {}
    after_state = after if isinstance(after, dict) else {}

    before_selected = _ptr_normalize_text(before_state.get("selected_step"))
    after_selected = _ptr_normalize_text(after_state.get("selected_step"))
    if before_selected and after_selected and before_selected != after_selected:
        return True

    before_counter = _ptr_normalize_text(before_state.get("progress_counter"))
    after_counter = _ptr_normalize_text(after_state.get("progress_counter"))
    if before_counter and after_counter and before_counter != after_counter:
        return True

    before_heading = _ptr_normalize_text(before_state.get("primary_heading"))
    after_heading = _ptr_normalize_text(after_state.get("primary_heading"))
    if before_heading and after_heading and before_heading != after_heading:
        return True

    before_footer = {_ptr_normalize_text(item) for item in (before_state.get("footer_actions") or []) if _ptr_normalize_text(item)}
    after_footer = {_ptr_normalize_text(item) for item in (after_state.get("footer_actions") or []) if _ptr_normalize_text(item)}
    if before_footer != after_footer:
        if "continue" in before_footer and "continue" not in after_footer:
            return True
        if "submit" not in before_footer and "submit" in after_footer:
            return True

    return False


def _ptr_busy_indicator_count(page: Page | None) -> int:
    result = _ptr_safe_page_eval(
        page,
        r"""() => {
            const selectors = [
                '[aria-busy="true"]',
                '[role="progressbar"]',
                '.oj-progress-circle',
                '.oj-progress-bar',
                '.oj-progress-spinner',
            ];
            const isVisible = (node) => {
                if (!node) return false;
                const style = window.getComputedStyle(node);
                if (!style) return false;
                if (style.display === "none" || style.visibility === "hidden") return false;
                const rect = node.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            let count = 0;
            for (const selector of selectors) {
                for (const node of document.querySelectorAll(selector)) {
                    if (isVisible(node)) count += 1;
                }
            }
            return count;
        }""",
    )
    try:
        return int(result or 0)
    except Exception:
        return 0


def _ptr_wait_for_observation_stability(
    page: Page | None,
    timeout_ms: int | None = None,
    quiet_ms: int | None = None,
) -> dict[str, Any]:
    if page is None:
        return {}
    total_timeout = int(timeout_ms or _ptr_wait_ms("PTR_POST_ACTION_STABILIZE_TIMEOUT_MS", 2500))
    stable_window = int(quiet_ms or _ptr_wait_ms("PTR_POST_ACTION_STABILIZE_QUIET_MS", 600))
    if total_timeout <= 0 or stable_window <= 0:
        return _ptr_observe(page)
    deadline = time.time() + (total_timeout / 1000.0)
    last_observation = _ptr_observe(page)
    stable_since: float | None = None
    poll_ms = max(100, min(200, stable_window // 3 or 100))
    while time.time() < deadline:
        current_observation = _ptr_observe(page)
        if _ptr_busy_indicator_count(page) > 0 or current_observation != last_observation:
            last_observation = current_observation
            stable_since = None
        else:
            now = time.time()
            if stable_since is None:
                stable_since = now
            elif (now - stable_since) * 1000.0 >= stable_window:
                return current_observation
        page.wait_for_timeout(poll_ms)
    return last_observation


def _ptr_wait_for_field_processing(page: Page | None, *, env_name: str, default_ms: int = 5000) -> None:
    if page is None:
        return
    wait_ms = _ptr_wait_ms(env_name, default_ms)
    if wait_ms > 0:
        page.wait_for_timeout(wait_ms)
    _ptr_wait_for_observation_stability(page)


def _ptr_experience_enabled() -> bool:
    if not _PTR_EXPERIENCE_STORE_PATH:
        return False
    return _ptr_env_flag("PTR_EXPERIENCE_ENABLED", "true")


def _ptr_control_family(helper: str) -> str:
    normalized = str(helper or "").strip().lower()
    if "combobox" in normalized:
        return "combobox"
    if "menu" in normalized:
        return "menu_panel"
    if "textbox" in normalized or "fill" in normalized:
        return "textbox"
    if "navigation" in normalized:
        return "navigation_button"
    if "button" in normalized:
        return "button"
    if "date" in normalized:
        return "date_picker"
    if "listbox" in normalized:
        return "listbox"
    if "search" in normalized:
        return "search_trigger"
    return "text_target"


def _ptr_oracle_surface_type(page: Page | None, observation: dict[str, Any] | None = None) -> str:
    try:
        url = str(page.url or "").strip() if page is not None else ""
    except Exception:
        url = ""
    title = str((observation or {}).get("title") or "").strip()
    guided_step = str((observation or {}).get("guided_step") or "").strip()
    dialog_count = int((observation or {}).get("dialog_count") or 0)
    if guided_step:
        return "guided_process"
    if dialog_count > 0:
        return "adf_popup"
    if "fusewelcome" in url.lower():
        return "redwood_home"
    if "/faces/" in url.lower():
        return "adf_form"
    if title:
        return "oracle_page"
    return "unknown"


def _ptr_page_signature(page: Page | None, observation: dict[str, Any] | None = None) -> dict[str, Any]:
    current_observation = observation or _ptr_observe(page)
    try:
        parsed = urlparse(str(page.url or "").strip()) if page is not None else urlparse("")
    except Exception:
        parsed = urlparse("")
    return {
        "host": str(parsed.netloc or "").strip().lower(),
        "path_hint": str(parsed.path or "").strip(),
        "title": str(current_observation.get("title") or "").strip(),
        "guided_step": str(current_observation.get("guided_step") or "").strip(),
        "surface_type": _ptr_oracle_surface_type(page, current_observation),
    }


def _ptr_failure_signature(current_page: Page | None, locator: Locator | None, error: Any) -> dict[str, Any]:
    ready_state = _ptr_safe_page_eval(current_page, "() => document.readyState") or ""
    error_type = type(error).__name__ if error is not None else ""
    error_hint = str(error or "").strip()
    target_ready = False
    if locator is not None:
        target_ready = _ptr_locator_is_actionable(locator, timeout_ms=500)
    return {
        "error_type": str(error_type).strip(),
        "error_hint": error_hint[:200],
        "ready_state": str(ready_state or "").strip(),
        "busy_indicator_count": _ptr_busy_indicator_count(current_page),
        "target_ready": bool(target_ready),
        "popup_open": _ptr_dialog_count(current_page) > 0,
    }


def _ptr_collect_ai_dom_candidates(current_page: Page, helper: str, label: str) -> dict[str, Any]:
    max_candidates = max(3, min(12, _ptr_int_env("PTR_AI_SELF_REPAIR_MAX_CANDIDATES", 8)))
    max_html_chars = max(240, min(2400, _ptr_int_env("PTR_AI_SELF_REPAIR_MAX_HTML_CHARS", 900)))
    raw_candidate_cap = max(max_candidates * 6, 60)
    try:
        context = current_page.evaluate(
            r"""(payload) => {
                const helper = String(payload?.helper || "").trim();
                const label = String(payload?.label || "").trim();
                const maxCandidates = Number(payload?.maxCandidates || 8);
                const rawCandidateCap = Number(payload?.rawCandidateCap || 60);
                const maxHtmlChars = Number(payload?.maxHtmlChars || 900);
                const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
                const isVisible = (candidate) => {
                    if (!candidate) return false;
                    const style = window.getComputedStyle(candidate);
                    if (!style) return false;
                    if (style.display === "none" || style.visibility === "hidden") return false;
                    if (candidate.getAttribute?.("aria-hidden") === "true") return false;
                    const rect = candidate.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                const helperSelectors = [];
                if (helper.includes("button")) {
                    helperSelectors.push(
                        "oj-action-card",
                        ".oj-actioncard",
                        "oj-switch",
                        "[role='switch']"
                    );
                }
                if (helper.includes("date")) {
                    helperSelectors.push(
                        "oj-input-date",
                        "oj-c-input-date",
                        "[title='Select Date.']",
                        "[aria-label='Select Date.']"
                    );
                }
                const selectors = [...helperSelectors, ...[
                    "input",
                    "textarea",
                    "button",
                    "a",
                    "[role='textbox']",
                    "[role='spinbutton']",
                    "[role='combobox']",
                    "[role='button']",
                    "[role='switch']",
                    "[role='checkbox']",
                    "[role='tab']",
                    "[role='link']",
                    "[role='menuitem']",
                    "[role='option']",
                    "[role='cell']",
                    "[role='gridcell']",
                    "oj-select-single",
                    "oj-c-select-single",
                    "oj-input-text",
                    "oj-c-input-text",
                    "oj-input-number",
                    "oj-c-input-number",
                    "oj-input-date",
                    "oj-c-input-date",
                    "oj-text-area",
                    "oj-c-text-area",
                ]];
                const seen = new Set();
                const results = [];
                const labelledByText = (candidate) => {
                    const ids = normalize(candidate?.getAttribute?.("aria-labelledby"));
                    if (!ids) return "";
                    const values = [];
                    for (const id of ids.split(/\s+/)) {
                        const node = document.getElementById(id);
                        const text = normalize(node?.innerText || node?.textContent);
                        if (text) values.push(text);
                    }
                    return normalize(values.join(" "));
                };
                const pushCandidate = (candidate) => {
                    if (!candidate || seen.has(candidate) || !isVisible(candidate)) return;
                    seen.add(candidate);
                    const role = normalize(candidate.getAttribute?.("role"));
                    const text = normalize(candidate.innerText || candidate.textContent);
                    const oracleHost = candidate.closest?.("oj-select-single, oj-c-select-single");
                    const entry = {
                        tag: String(candidate.tagName || "").toLowerCase(),
                        role,
                        id: normalize(candidate.id),
                        name: normalize(candidate.getAttribute?.("name")),
                        aria_label: normalize(candidate.getAttribute?.("aria-label")),
                        aria_labelledby: normalize(candidate.getAttribute?.("aria-labelledby")),
                        aria_controls: normalize(candidate.getAttribute?.("aria-controls")),
                        labelledby_text: labelledByText(candidate),
                        label_hint: normalize(candidate.getAttribute?.("label-hint")),
                        placeholder: normalize(candidate.getAttribute?.("placeholder")),
                        title: normalize(candidate.getAttribute?.("title")),
                        data_oj_field: normalize(candidate.getAttribute?.("data-oj-field")),
                        oracle_host_tag: normalize(oracleHost?.tagName).toLowerCase(),
                        oracle_host_id: normalize(oracleHost?.id),
                        oracle_host_text: normalize(oracleHost?.innerText || oracleHost?.textContent),
                        oracle_host_data_oj_field: normalize(oracleHost?.getAttribute?.("data-oj-field")),
                        text,
                        html: normalize(candidate.outerHTML).slice(0, maxHtmlChars),
                    };
                    if (
                        !entry.text
                        && !entry.aria_label
                        && !entry.title
                        && !entry.id
                        && !entry.label_hint
                        && !entry.labelledby_text
                        && !entry.oracle_host_text
                    ) {
                        return;
                    }
                    results.push(entry);
                };
                for (const selector of selectors) {
                    for (const candidate of document.querySelectorAll(selector)) {
                        if (results.length >= rawCandidateCap) break;
                        pushCandidate(candidate);
                    }
                    if (results.length >= rawCandidateCap) break;
                }
                return { helper, label, candidates: results.slice(0, rawCandidateCap) };
            }""",
            {
                "helper": helper,
                "label": label,
                "maxCandidates": max_candidates,
                "rawCandidateCap": raw_candidate_cap,
                "maxHtmlChars": max_html_chars,
            },
        )
        if not isinstance(context, dict):
            return {"helper": helper, "label": label, "candidates": []}
        ranked = _ptr_rank_ai_dom_candidates(helper, label, list(context.get("candidates") or []), max_candidates)
        context["candidates"] = ranked
        return context
    except Exception:
        return {"helper": helper, "label": label, "candidates": []}


def _ptr_capture_failure_context(current_page: Page | None, helper: str, label: str, error: Any = None) -> dict[str, Any]:
    try:
        page_url = str(current_page.url or "").strip() if current_page is not None else ""
    except Exception:
        page_url = ""
    try:
        page_title = str(current_page.title() or "").strip() if current_page is not None else ""
    except Exception:
        page_title = ""
    ready_state = _ptr_safe_page_eval(current_page, "() => document.readyState") or ""
    dom_context = (
        _ptr_collect_ai_dom_candidates(current_page, helper, label)
        if current_page is not None
        else {"helper": helper, "label": label, "candidates": []}
    )
    candidates = dom_context.get("candidates") or []
    return {
        "helper": helper,
        "label": label,
        "error": str(error or ""),
        "script_data": _ptr_current_script_data(),
        "page_url": page_url,
        "page_title": page_title,
        "ready_state": str(ready_state or ""),
        "busy_indicator_count": _ptr_busy_indicator_count(current_page),
        "active_element": _ptr_active_element(current_page),
        "dom_context": dom_context,
        "dom_candidate_count": len(candidates) if isinstance(candidates, list) else 0,
    }


def _ptr_store_experience_episode(
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
    if not _ptr_experience_enabled():
        return
    recovery = _PTR_CURRENT_STRATEGY.get("recovery")
    if status == "success" and not recovery:
        return

    observation = _ptr_observe(page, locator)
    episode = {
        "episode_id": str(uuid.uuid4()),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runner_version": _PTR_RUNNER_VERSION,
        "app_family": "oracle",
        "ui_family": "oracle",
        "action_type": action_type,
        "target_label": label,
        "target_label_normalized": _ptr_normalize_text(label),
        "control_family": _ptr_control_family(action_type),
        "page_signature": _ptr_page_signature(page, observation),
        "failure_signature": _ptr_failure_signature(page, locator, error),
        "recovery": _ptr_clone_json_value(recovery or {"source": "strict", "kind": "direct", "handler_name": "strict"}),
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
        _experience_append_episode(_PTR_EXPERIENCE_STORE_PATH, episode)
    except Exception:
        return


def _ptr_request_experience_recovery(
    current_page: Page,
    helper: str,
    label: str,
    last_error: Any,
    locator: Locator | None = None,
) -> list[dict[str, Any]]:
    interaction: dict[str, Any] = {
        "feature": "experience_recovery",
        "helper": helper,
        "label": label,
        "store_path": _PTR_EXPERIENCE_STORE_PATH,
    }
    if not _ptr_experience_enabled():
        interaction["status"] = "disabled"
        interaction["error"] = "Experience recovery is disabled or store path is missing."
        _ptr_record_experience_interaction(interaction)
        return []

    page_signature = _ptr_page_signature(current_page)
    failure_signature = _ptr_failure_signature(current_page, locator, last_error)
    interaction["page_signature"] = _ptr_clone_json_value(page_signature)
    interaction["failure_signature"] = _ptr_clone_json_value(failure_signature)
    interaction["status"] = "requested"
    _ptr_record_experience_interaction(interaction)
    _ptr_record_strategy_attempt("experience_lookup")

    try:
        matches = _experience_retrieve_recovery_candidates(
            _PTR_EXPERIENCE_STORE_PATH,
            action_type=helper,
            target_label=label,
            control_family=_ptr_control_family(helper),
            page_signature=page_signature,
            failure_signature=failure_signature,
        )
    except Exception as exc:
        _ptr_update_last_experience_interaction(
            {
                "status": "request_error",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        return []

    _ptr_update_last_experience_interaction(
        {
            "status": "success" if matches else "miss",
            "candidate_count": len(matches),
            "candidate_kinds": [str(((item.get("recovery") or {}).get("kind") or "")).strip() for item in matches],
            "candidate_scores": [int(item.get("retrieval_score") or 0) for item in matches],
        }
    )
    return matches


def _ptr_locator_from_repair_strategy(current_page: Page, strategy: dict[str, Any], prefix: str, idx: int) -> tuple[str, Locator | None]:
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
            locator = current_page.locator(selector if selector.startswith("xpath=") else f"xpath={selector}").first
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
    return strategy_name, locator


def _ptr_capture_failure_screenshot() -> None:
    if not _PTR_FAILURE_SCREENSHOT_PATH or _PTR_LAST_PAGE is None:
        return
    try:
        Path(_PTR_FAILURE_SCREENSHOT_PATH).parent.mkdir(parents=True, exist_ok=True)
        _PTR_LAST_PAGE.screenshot(path=_PTR_FAILURE_SCREENSHOT_PATH, full_page=True)
    except Exception:
        return


def _ptr_capture_failure(error: Any = None) -> None:
    _ptr_capture_failure_screenshot()


def _ptr_capture_step(action: str) -> None:
    global _PTR_STEP_INDEX
    if not _PTR_STEP_ARTIFACTS_DIR or _PTR_LAST_PAGE is None:
        return
    try:
        Path(_PTR_STEP_ARTIFACTS_DIR).mkdir(parents=True, exist_ok=True)
        _PTR_STEP_INDEX += 1
        filename = f"step_{_PTR_STEP_INDEX:03d}_{re.sub(r'[^A-Za-z0-9._-]+', '_', str(action or 'step')).strip('._') or 'step'}.png"
        path = Path(_PTR_STEP_ARTIFACTS_DIR) / filename
        _PTR_LAST_PAGE.screenshot(path=str(path), full_page=_ptr_env_flag("PTR_STEP_SCREENSHOT_FULL_PAGE", "false"))
        _PTR_STEP_ARTIFACTS.append({"index": _PTR_STEP_INDEX, "action": str(action or "step"), "local_path": str(path)})
    except Exception:
        return


def _ptr_write_diagnostics() -> None:
    if not _PTR_DIAGNOSTICS_PATH:
        return
    page_url = ""
    page_title = ""
    try:
        if _PTR_LAST_PAGE is not None:
            page_url = str(_PTR_LAST_PAGE.url or "").strip()
            page_title = str(_PTR_LAST_PAGE.title() or "").strip()
    except Exception:
        pass
    payload = {
        "page_url": page_url,
        "page_title": page_title,
        "failure_screenshot_path": _PTR_FAILURE_SCREENSHOT_PATH or None,
        "step_artifacts": _PTR_STEP_ARTIFACTS,
        "action_log": _PTR_ACTION_LOG,
    }
    try:
        Path(_PTR_DIAGNOSTICS_PATH).write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        return


atexit.register(_ptr_write_diagnostics)


def _ptr_patch_page_methods() -> None:
    if getattr(Page, "_ptr_v2_patched", False):
        return

    original_goto = Page.goto
    original_reload = Page.reload

    def _wrapped_goto(self, *args, **kwargs):
        global _PTR_SUPPRESS_PATCH_CAPTURE
        _ptr_register_page(self)
        try:
            result = original_goto(self, *args, **kwargs)
            if _PTR_SUPPRESS_PATCH_CAPTURE <= 0:
                _ptr_capture_step("goto")
            return result
        except Exception:
            if _PTR_SUPPRESS_PATCH_CAPTURE <= 0:
                _ptr_capture_failure_screenshot()
            raise

    def _wrapped_reload(self, *args, **kwargs):
        global _PTR_SUPPRESS_PATCH_CAPTURE
        _ptr_register_page(self)
        try:
            result = original_reload(self, *args, **kwargs)
            if _PTR_SUPPRESS_PATCH_CAPTURE <= 0:
                _ptr_capture_step("reload")
            return result
        except Exception:
            if _PTR_SUPPRESS_PATCH_CAPTURE <= 0:
                _ptr_capture_failure_screenshot()
            raise

    Page.goto = _wrapped_goto
    Page.reload = _wrapped_reload
    setattr(Page, "_ptr_v2_patched", True)


_ptr_patch_page_methods()


def _ptr_openai_base_url() -> str:
    return str(os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")


def _ptr_ai_self_repair_enabled() -> bool:
    if not _ptr_env_flag("PTR_AI_SELF_REPAIR_ENABLED", "false"):
        return False
    return bool(str(os.getenv("OPENAI_API_KEY", "")).strip())


def _ptr_ai_self_repair_model() -> str:
    return (
        str(
            os.getenv(
                "PTR_AI_SELF_REPAIR_MODEL",
                os.getenv("OPENAI_FAILURE_SUMMARY_MODEL", "gpt-4.1-mini"),
            )
        ).strip()
        or "gpt-4.1-mini"
    )


def _ptr_extract_ai_output_text(payload: dict[str, Any]) -> str:
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


def _ptr_parse_ai_json_response(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("AI response was not a JSON object.")
    return parsed


def _ptr_build_ai_self_repair_prompt(
    current_page: Page,
    helper: str,
    label: str,
    last_error: Any,
    value: str | None = None,
    locator: Locator | None = None,
    dom_context: dict[str, Any] | None = None,
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
        dom_context = _ptr_collect_ai_dom_candidates(current_page, helper, label)
    dom_json = json.dumps(dom_context, ensure_ascii=False)
    script_data_json = json.dumps(_ptr_current_script_data(), ensure_ascii=False)
    locator_context_json = json.dumps(_ptr_capture_locator_context(locator), ensure_ascii=False)
    action_kind = "fill" if helper == "fill_textbox" else "click"
    value_line = f"\n- Value to enter: {value}" if value is not None else ""
    error_text = str(last_error or "").strip()
    if len(error_text) > 2000:
        error_text = error_text[:2000] + "..."
    return (
        "You repair Playwright locators for enterprise web apps. Return JSON only.\n"
        "Use the recorded script data to preserve the original target semantics.\n"
        "Strict execution failed. Use only the provided DOM candidates.\n"
        "Do not invent attributes or text that are not present.\n"
        "Prefer stable selectors based on id, aria-label, label-hint, name, role, or data-oj-field.\n"
        "If the failure mentions pointer interception or overlay issues, prefer the enclosing Oracle host control that matches the recorded target over an intercepted inner input.\n"
        "Return exactly this schema:\n"
        '{"strategies":[{"kind":"css"|"xpath"|"role"|"label"|"placeholder"|"text","selector":string|null,"role":string|null,"name":string|null,"text":string|null,"exact":boolean|null,"reason":string|null}]}\n'
        "Rules:\n"
        "- Return at most 3 strategies, best first.\n"
        "- CSS/XPath selectors must be valid Playwright locator selectors.\n"
        "- For role, set role and name.\n"
        "- For label/placeholder/text, set text.\n"
        f"- Helper: {helper}\n"
        f"- Intended action: {action_kind}\n"
        f"- Target label: {label}\n"
        f"- Page title: {page_title or 'unknown'}\n"
        f"- Page URL: {page_url or 'unknown'}\n"
        f"- Last error: {error_text}"
        f"{value_line}\n"
        "Recorded script data JSON:\n"
        f"{script_data_json}\n"
        "Recorded target context JSON:\n"
        f"{locator_context_json}\n"
        "DOM candidates JSON:\n"
        f"{dom_json}"
    )


def _ptr_request_ai_self_repair(
    current_page: Page,
    helper: str,
    label: str,
    last_error: Any,
    value: str | None = None,
    locator: Locator | None = None,
) -> list[dict[str, Any]]:
    system_prompt = "You are a senior Playwright locator repair assistant. Return concise JSON only."
    endpoint = f"{_ptr_openai_base_url()}/responses"
    model = _ptr_ai_self_repair_model()
    interaction: dict[str, Any] = {
        "feature": "self_repair",
        "helper": helper,
        "label": label,
        "model": model,
        "endpoint": endpoint,
        "system_prompt": system_prompt,
    }
    if value is not None:
        interaction["value"] = str(value)
    script_data = _ptr_current_script_data()
    if script_data:
        interaction["script_data"] = script_data
    locator_context = _ptr_capture_locator_context(locator)
    if locator_context:
        interaction["recorded_target_context"] = locator_context

    if not _ptr_ai_self_repair_enabled():
        interaction["status"] = "disabled"
        interaction["error"] = "AI self-repair is disabled or OPENAI_API_KEY is missing."
        _ptr_record_ai_interaction(interaction)
        return []

    dom_context = _ptr_collect_ai_dom_candidates(current_page, helper, label)
    prompt = _ptr_build_ai_self_repair_prompt(
        current_page,
        helper,
        label,
        last_error,
        value=value,
        locator=locator,
        dom_context=dom_context,
    )
    interaction["user_prompt"] = prompt
    interaction["dom_candidate_count"] = len(dom_context.get("candidates") or [])
    interaction["max_output_tokens"] = 400

    if not interaction["dom_candidate_count"]:
        interaction["status"] = "skipped_no_candidates"
        interaction["error"] = "No DOM candidates were collected for AI self-repair."
        _ptr_record_ai_interaction(interaction)
        return []

    interaction["status"] = "requested"
    _ptr_record_ai_interaction(interaction)
    _ptr_record_strategy_attempt("ai_self_repair_lookup")

    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
            {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
        ],
        "text": {"format": {"type": "json_object"}},
        "max_output_tokens": 400,
    }

    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {str(os.getenv('OPENAI_API_KEY', '')).strip()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    response_body = ""
    try:
        timeout_s = max(5.0, _ptr_wait_ms("PTR_AI_SELF_REPAIR_TIMEOUT_MS", 15000) / 1000.0)
        with urlopen(request, timeout=timeout_s) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(response_body)
            _ptr_update_last_ai_interaction(
                {
                    "http_status": int(getattr(response, "status", 0) or 0),
                    "api_response_body": response_body,
                }
            )
    except HTTPError as exc:
        error_body = ""
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            error_body = ""
        _ptr_update_last_ai_interaction(
            {
                "status": "request_error",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "http_status": int(getattr(exc, "code", 0) or 0),
                "error_response_body": error_body,
            }
        )
        return []
    except URLError as exc:
        _ptr_update_last_ai_interaction(
            {
                "status": "request_error",
                "error_type": type(exc).__name__,
                "error": str(getattr(exc, "reason", exc)),
            }
        )
        return []
    except Exception as exc:
        _ptr_update_last_ai_interaction(
            {
                "status": "request_error",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "api_response_body": response_body,
            }
        )
        return []

    response_text = _ptr_extract_ai_output_text(parsed)
    _ptr_update_last_ai_interaction({"response_text": response_text})
    try:
        parsed_response = _ptr_parse_ai_json_response(response_text)
    except Exception as exc:
        _ptr_update_last_ai_interaction({"status": "parse_error", "error_type": type(exc).__name__, "error": str(exc)})
        return []

    strategies = parsed_response.get("strategies")
    if not isinstance(strategies, list):
        _ptr_update_last_ai_interaction(
            {
                "status": "invalid_response",
                "error": "AI response JSON did not contain a strategies list.",
                "parsed_response": parsed_response,
            }
        )
        return []

    normalized = [item for item in strategies[:3] if isinstance(item, dict)]
    _ptr_update_last_ai_interaction(
        {
            "status": "success" if normalized else "empty",
            "parsed_response": parsed_response,
            "response_strategy_count": len(normalized),
            "response_strategies": normalized,
        }
    )
    return normalized


def _ptr_ai_text_matches_label(value: Any, label: str) -> bool:
    normalized_label = _ptr_normalize_text(label)
    normalized_value = _ptr_normalize_text(value)
    if not normalized_label:
        return True
    if not normalized_value:
        return False
    if normalized_value == normalized_label or normalized_label in normalized_value:
        return True
    label_tokens = [token for token in re.findall(r"[a-z0-9]+", normalized_label) if len(token) > 1]
    if not label_tokens:
        return normalized_label in normalized_value
    if len(label_tokens) == 1:
        return label_tokens[0] in normalized_value
    return all(token in normalized_value for token in label_tokens)


def _ptr_ai_locator_matches_label(locator: Locator, label: str) -> bool:
    if not str(label or "").strip():
        return True
    metadata = _ptr_extract_locator_metadata(locator)
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
        if _ptr_ai_text_matches_label(metadata.get(key), label):
            return True
    return False


def _ptr_experience_repair_locators(current_page: Page, helper: str, label: str, last_error: Any, locator: Locator | None = None) -> list[tuple[str, Locator, dict[str, Any]]]:
    locators: list[tuple[str, Locator, dict[str, Any]]] = []
    for idx, episode in enumerate(_ptr_request_experience_recovery(current_page, helper, label, last_error, locator=locator), start=1):
        recovery = episode.get("recovery") or {}
        if str(recovery.get("kind") or "").strip() != "ai_locator_repair":
            continue
        strategy = (recovery.get("details") or {}).get("locator_strategy") or {}
        if not isinstance(strategy, dict) or not strategy:
            continue
        strategy_name, candidate = _ptr_locator_from_repair_strategy(current_page, strategy, "experience", idx)
        if candidate is None or not strategy_name:
            continue
        if not _ptr_ai_locator_matches_label(candidate, label):
            continue
        locators.append((strategy_name, candidate, episode))
    return locators


def _ptr_ai_repair_locators(
    current_page: Page,
    helper: str,
    label: str,
    last_error: Any,
    value: str | None = None,
    locator: Locator | None = None,
) -> list[tuple[str, Locator, dict[str, Any]]]:
    locators: list[tuple[str, Locator, dict[str, Any]]] = []
    rejected_names: list[str] = []
    rejected_reasons: list[str] = []
    for idx, strategy in enumerate(
        _ptr_request_ai_self_repair(current_page, helper, label, last_error, value=value, locator=locator),
        start=1,
    ):
        declared_label = str(strategy.get("name") or strategy.get("text") or "").strip()
        strategy_name, locator = _ptr_locator_from_repair_strategy(current_page, strategy, "ai", idx)

        if locator is None or not strategy_name:
            continue
        if declared_label and not _ptr_ai_text_matches_label(declared_label, label):
            rejected_names.append(strategy_name)
            rejected_reasons.append(f"{strategy_name}: response target does not match requested label")
            continue
        if not _ptr_ai_locator_matches_label(locator, label):
            rejected_names.append(strategy_name)
            rejected_reasons.append(f"{strategy_name}: resolved element does not match requested label")
            continue
        locators.append((strategy_name, locator, strategy))

    if _PTR_CURRENT_STRATEGY.get("ai_interactions"):
        patch: dict[str, Any] = {
            "locator_candidate_count": len(locators),
            "locator_strategies": [name for name, _, _ in locators],
            "rejected_locator_strategies": rejected_names,
            "rejected_locator_reasons": rejected_reasons,
        }
        if not locators:
            try:
                last_interaction = ((_PTR_CURRENT_STRATEGY.get("ai_interactions") or [])[-1] or {})
            except Exception:
                last_interaction = {}
            response_strategy_count = int((last_interaction.get("response_strategy_count") or 0) if isinstance(last_interaction, dict) else 0)
            if response_strategy_count > 0:
                patch["repair_outcome"] = "no_usable_locator"
        _ptr_update_last_ai_interaction(patch)
    return locators


def _ptr_try_expand_oracle_quick_actions(page: Page, label: str) -> bool:
    try:
        target = page.get_by_text(label, exact=True)
        if _ptr_locator_is_actionable(target, timeout_ms=500):
            return False
    except Exception:
        pass
    candidates = (
        page.get_by_label("Show more quick actions"),
        page.get_by_text("Show More", exact=True),
    )
    for candidate in candidates:
        try:
            if not _ptr_locator_is_actionable(candidate.first, timeout_ms=1200):
                continue
            _ptr_record_strategy_attempt("oracle_quick_actions_expand")
            candidate.first.click(timeout=_ptr_wait_ms("PTR_ACTION_TIMEOUT_MS", 3000))
            page.wait_for_timeout(_ptr_wait_ms("PTR_QUICK_ACTIONS_EXPAND_WAIT_MS", 600))
            return True
        except Exception:
            continue
    return False


def _ptr_try_oracle_home_search(page: Page, label: str, postcondition) -> bool:
    observation = _ptr_observe(page)
    page_signature = _ptr_page_signature(page, observation)
    if "fusewelcome" not in str(page_signature.get("path_hint") or "").lower():
        return False

    search_candidates = (
        page.get_by_role("combobox", name="Search:"),
        page.get_by_placeholder("Search for people and actions"),
    )

    search_box = None
    for candidate in search_candidates:
        try:
            resolved = candidate.first if hasattr(candidate, "first") else candidate
            if _ptr_locator_is_actionable(resolved, timeout_ms=1200):
                search_box = resolved
                break
        except Exception:
            continue
    if search_box is None:
        return False

    try:
        _ptr_record_strategy_attempt("oracle_home_search")
        _ptr_strict_click(search_box)
        _ptr_strict_fill(search_box, label, timeout_ms=_ptr_wait_ms("PTR_TEXT_ENTRY_TIMEOUT_MS", 3000))
        page.wait_for_timeout(_ptr_wait_ms("PTR_ORACLE_HOME_SEARCH_WAIT_MS", 750))
    except Exception:
        return False

    option_candidates = [
        ("oracle_home_search_link", page.get_by_role("link", name=label, exact=True)),
        ("oracle_home_search_option", page.get_by_role("option", name=label, exact=True)),
        ("oracle_home_search_menuitem", page.get_by_role("menuitem", name=label, exact=True)),
        ("oracle_home_search_cell", page.get_by_role("cell", name=label, exact=True)),
        ("oracle_home_search_text", page.get_by_text(label, exact=True)),
    ]

    for strategy_name, candidate in option_candidates:
        try:
            resolved = candidate.first if hasattr(candidate, "first") else candidate
            if not _ptr_locator_is_actionable(resolved, timeout_ms=1200):
                continue
            before = _ptr_observe(page, resolved)
            _ptr_record_strategy_attempt(strategy_name)
            _ptr_strict_click(resolved)
            page.wait_for_timeout(_ptr_wait_ms("PTR_POST_CLICK_WAIT_MS", 250))
            after = _ptr_observe(page, resolved)
            if postcondition(before, after):
                return True
        except Exception:
            continue

    try:
        before_enter = _ptr_observe(page, search_box)
        _ptr_record_strategy_attempt("oracle_home_search_enter")
        search_box.press("Enter")
        page.wait_for_timeout(_ptr_wait_ms("PTR_POST_CLICK_WAIT_MS", 250))
        after_enter = _ptr_observe(page, search_box)
        if postcondition(before_enter, after_enter):
            return True
    except Exception:
        return False
    return False


def _ptr_try_oracle_guided_action_card(page: Page, label: str, postcondition) -> bool:
    observation = _ptr_observe(page)
    page_signature = _ptr_page_signature(page, observation)
    if str(page_signature.get("surface_type") or "").strip() != "guided_process":
        return False

    label_text = str(label or "").strip()
    if not label_text:
        return False

    candidates = [
        ("oracle_action_card", page.locator("oj-action-card").filter(has_text=label_text)),
        ("oracle_action_card_class", page.locator(".oj-actioncard").filter(has_text=label_text)),
    ]

    for strategy_name, candidate in candidates:
        try:
            card = candidate.first if hasattr(candidate, "first") else candidate
        except Exception:
            card = candidate

        before = _ptr_observe(page, card)
        switch_locator = None
        before_switch_state = ""
        try:
            switch_locator = card.locator("[role='switch']").first
            before_switch_state = str((_ptr_extract_locator_metadata(switch_locator) or {}).get("aria_checked") or "").strip()
        except Exception:
            switch_locator = None

        click_target = None
        click_strategy = strategy_name
        if _ptr_locator_is_actionable(card, timeout_ms=1200):
            click_target = card
        elif switch_locator is not None and _ptr_locator_is_actionable(switch_locator, timeout_ms=1200):
            click_target = switch_locator
            click_strategy = f"{strategy_name}_switch"
        if click_target is None:
            continue

        _ptr_record_strategy_attempt(click_strategy)
        _ptr_strict_click(click_target, timeout_ms=_ptr_wait_ms("PTR_ACTION_CARD_CLICK_TIMEOUT_MS", 4000))
        page.wait_for_timeout(_ptr_wait_ms("PTR_ACTION_CARD_POST_CLICK_WAIT_MS", 1500))

        after = _ptr_observe(page, card)
        after_switch_state = before_switch_state
        if switch_locator is not None:
            try:
                after_switch_state = str((_ptr_extract_locator_metadata(switch_locator) or {}).get("aria_checked") or "").strip()
            except Exception:
                after_switch_state = before_switch_state

        if postcondition(before, after):
            return True
        if before_switch_state != after_switch_state:
            return True
        if after_switch_state == "true":
            return True
    return False


def _ptr_try_open_oracle_select_single_with_keyboard(page: Page, locator: Locator, error: Any) -> str:
    error_text = str(error or "").lower()
    if "intercepts pointer events" not in error_text:
        return ""

    metadata = _ptr_extract_locator_metadata(locator)
    class_name = str(metadata.get("class_name") or "").strip()
    oracle_info = _ptr_safe_locator_eval(
        locator,
        r"""(node) => {
            const host = node?.closest?.("oj-select-single, oj-c-select-single");
            return {
                has_oracle_host: Boolean(host),
            };
        }""",
    )
    has_oracle_host = bool((oracle_info or {}).get("has_oracle_host"))
    if not has_oracle_host and "oj-searchselect-input" not in class_name:
        return ""

    timeout = _ptr_wait_ms("PTR_ACTION_TIMEOUT_MS", 3000)
    focus_wait_ms = _ptr_wait_ms("PTR_COMBOBOX_FOCUS_WAIT_MS", 100)
    open_strategies = [
        ("oracle_select_single_arrowdown", "ArrowDown"),
        ("oracle_select_single_enter", "Enter"),
    ]

    for strategy_name, key_name in open_strategies:
        try:
            before = _ptr_observe(page, locator)
            _ptr_record_strategy_attempt(strategy_name)
            try:
                locator.focus(timeout=timeout)
            except TypeError:
                locator.focus()
            except Exception:
                pass
            page.wait_for_timeout(focus_wait_ms)
            locator.press(key_name, timeout=timeout)
            page.wait_for_timeout(_ptr_wait_ms("PTR_COMBOBOX_OPEN_WAIT_MS", 350))
            after = _ptr_observe(page, locator)
            if _ptr_combobox_open_postcondition(before, after):
                return strategy_name
        except Exception:
            continue
    return ""


def _ptr_collect_validation_messages(page: Page) -> list[str]:
    result = _ptr_safe_page_eval(
        page,
        r"""() => {
            const selectors = [
                '[role="alert"]',
                '.oj-messagebanner-item',
                '.oj-message-error',
                '.oj-form-control-inline-message',
                '.oj-invalid-text',
            ];
            const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
            const cleanMessage = (value) => normalize(value).replace(/\s+Close$/i, "").trim();
            const isVisible = (node) => {
                if (!node) return false;
                const style = window.getComputedStyle(node);
                if (!style) return false;
                if (style.display === "none" || style.visibility === "hidden") return false;
                const rect = node.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const getLabelText = (node) => {
                if (!node) return "";
                const directAttrs = ["aria-label", "title", "placeholder", "name"];
                for (const attr of directAttrs) {
                    const value = normalize(node.getAttribute(attr));
                    if (value) return value;
                }
                const labelledBy = normalize(node.getAttribute("aria-labelledby"));
                if (labelledBy) {
                    for (const id of labelledBy.split(/\s+/)) {
                        const candidate = document.getElementById(id);
                        const text = normalize(candidate && (candidate.innerText || candidate.textContent));
                        if (text) return text;
                    }
                }
                if (node.id) {
                    const label = document.querySelector(`label[for="${node.id.replace(/"/g, '\\"')}"]`);
                    const labelText = normalize(label && (label.innerText || label.textContent));
                    if (labelText) return labelText;
                }
                const hintedParent = node.closest("[label-hint]");
                const hintedLabel = normalize(hintedParent && hintedParent.getAttribute("label-hint"));
                if (hintedLabel) return hintedLabel;
                const fieldParent = node.closest("[data-oj-field]");
                const fieldLabel = normalize(fieldParent && fieldParent.getAttribute("data-oj-field"));
                if (fieldLabel) return fieldLabel;
                return normalize(node.id) || "Required field";
            };
            const values = [];
            for (const selector of selectors) {
                for (const node of document.querySelectorAll(selector)) {
                    if (!isVisible(node)) continue;
                    const text = cleanMessage(node.innerText || node.textContent || "");
                    if (text) values.push(text);
                }
            }
            const seen = new Set(values);
            const requiredSelectors = [
                "input[aria-required='true']",
                "textarea[aria-required='true']",
                "select[aria-required='true']",
                "[role='combobox'][aria-required='true']",
                "[required]",
            ];
            for (const selector of requiredSelectors) {
                for (const node of document.querySelectorAll(selector)) {
                    if (!isVisible(node)) continue;
                    const value = normalize(
                        node.value ??
                        node.getAttribute("value") ??
                        node.textContent ??
                        ""
                    );
                    const invalid = normalize(node.getAttribute("aria-invalid")).toLowerCase() === "true";
                    if (!invalid && value) continue;
                    const label = getLabelText(node);
                    const message = cleanMessage(label ? `${label}: Select a value.` : "Required field: Select a value.");
                    if (message && !seen.has(message)) {
                        values.push(message);
                        seen.add(message);
                    }
                }
            }
            return values.filter(Boolean).slice(0, 8);
        }""",
    )
    return [str(item).strip() for item in (result or []) if str(item).strip()]


def _ptr_resolve_page(args: tuple[Any, ...]) -> Page | None:
    for arg in args:
        if isinstance(arg, Page):
            return arg
    return _PTR_LAST_PAGE


def _ptr_resolve_primary_locator(args: tuple[Any, ...]) -> Locator | None:
    for arg in args:
        if isinstance(arg, Locator):
            return arg
    return None


def _ptr_finalize_action_log(action_type: str, label: str, status: str, duration_ms: int, *, error: Any = None, page: Page | None = None) -> None:
    attempts, unique_attempts, strategy = _ptr_strategy_snapshot()
    entry: dict[str, Any] = {
        "action": action_type,
        "label": label,
        "status": status,
        "duration_ms": duration_ms,
        "strategy": strategy,
        "step": len(_PTR_ACTION_LOG) + 1,
        "fallback_attempt_count": len(attempts),
        "fallback_strategy_count": len(unique_attempts),
        "fallback_strategies": attempts,
        "fallback_strategies_unique": unique_attempts,
        "ai_interactions": _ptr_clone_json_value(_PTR_CURRENT_STRATEGY.get("ai_interactions") or []),
        "experience_interactions": _ptr_clone_json_value(_PTR_CURRENT_STRATEGY.get("experience_interactions") or []),
    }
    script_data = _ptr_current_script_data()
    if script_data:
        entry["script_data"] = script_data
    recovery = _PTR_CURRENT_STRATEGY.get("recovery")
    if isinstance(recovery, dict) and recovery:
        entry["recovery"] = _ptr_clone_json_value(recovery)
    if error is not None:
        entry["error"] = str(error)
        entry["failure_context"] = _ptr_capture_failure_context(page, action_type, label, error)
    _PTR_ACTION_LOG.append(entry)


def _ptr_goto_page(current_page: Page, url: str, **goto_kwargs) -> Any:
    global _PTR_SUPPRESS_PATCH_CAPTURE
    _ptr_register_page(current_page)
    _PTR_SUPPRESS_PATCH_CAPTURE += 1
    try:
        return current_page.goto(url, **goto_kwargs)
    finally:
        _PTR_SUPPRESS_PATCH_CAPTURE = max(0, _PTR_SUPPRESS_PATCH_CAPTURE - 1)


def _ptr_raw_click(locator: Locator, current_page: Page, label: str) -> None:
    _ptr_register_page(current_page)
    locator.click()


def _ptr_raw_fill(locator: Locator, current_page: Page, label: str, value: str) -> None:
    _ptr_register_page(current_page)
    locator.fill(value)


def _ptr_raw_press(locator: Locator, current_page: Page, label: str, key: str) -> None:
    _ptr_register_page(current_page)
    locator.press(key)


def _ptr_login_submit_and_redirect(locator: Locator, current_page: Page, label: str, expected_url: str) -> None:
    _ptr_register_page(current_page)
    locator.press("Enter")
    _ptr_wait_for_post_login_redirect(current_page, expected_url)


def _ptr_wait_after_interaction(page: Page | None) -> None:
    wait_ms = _ptr_wait_ms("PTR_AFTER_ACTION_WAIT_MS", 10000)
    if wait_ms <= 0:
        return
    current_page = page or _PTR_LAST_PAGE
    if current_page is None:
        return
    try:
        current_page.wait_for_timeout(wait_ms)
    except Exception:
        return


def _ptr_tracked_action(action_type: str, label: str, fn, *args, **kwargs):
    page = _ptr_resolve_page(args)
    primary_locator = _ptr_resolve_primary_locator(args)
    if page is not None:
        _ptr_register_page(page)
    _ptr_reset_strategy_tracking(action_type, label)
    _ptr_record_strategy_attempt("direct")
    start = time.time()
    try:
        result = fn(*args, **kwargs)
        current_page = _PTR_LAST_PAGE or page
        _ptr_wait_after_interaction(current_page)
        _ptr_capture_step(action_type)
        _ptr_finalize_action_log(
            action_type,
            label,
            "success",
            int((time.time() - start) * 1000),
            page=current_page,
        )
        return result
    except Exception as exc:
        _ptr_capture_failure_screenshot()
        _ptr_store_experience_episode(
            action_type=_ptr_normalize_runtime_action_name(getattr(fn, "__name__", action_type)),
            label=label,
            page=page,
            locator=primary_locator,
            error=exc,
            status="failed",
            postcondition_kind="none",
            postcondition_passed=False,
        )
        _ptr_finalize_action_log(
            action_type,
            label,
            "failed",
            int((time.time() - start) * 1000),
            error=exc,
            page=page,
        )
        raise


def _ptr_click_with_candidates(page: Page, label: str, locator: Locator, helper: str, postcondition):
    before = _ptr_observe(page, locator)
    try:
        _ptr_strict_click(locator)
        after = _ptr_observe(page, locator)
        if postcondition(before, after):
            return
        raise RuntimeError(f'Action "{label}" completed but no postcondition changed.')
    except Exception as direct_exc:
        last_error: Exception = direct_exc
        if _ptr_try_expand_oracle_quick_actions(page, label):
            try:
                _ptr_strict_click(locator)
                after = _ptr_observe(page, locator)
                if postcondition(before, after):
                    _ptr_set_recovery_record(
                        "oracle_handler",
                        "quick_action_expand",
                        "oracle_quick_actions_expand",
                        {"trigger": "Show more quick actions"},
                    )
                    _ptr_store_experience_episode(
                        action_type=helper,
                        label=label,
                        page=page,
                        locator=locator,
                        error=direct_exc,
                        status="success",
                        postcondition_kind="action_effect",
                        postcondition_passed=True,
                    )
                    return
                last_error = RuntimeError(f'Action "{label}" still had no postcondition after expanding quick actions.')
            except Exception as exc:
                last_error = exc

        if _ptr_try_oracle_home_search(page, label, postcondition):
            _ptr_set_recovery_record(
                "oracle_handler",
                "home_search",
                "oracle_home_search",
                {"search_label": label},
            )
            _ptr_store_experience_episode(
                action_type=helper,
                label=label,
                page=page,
                locator=locator,
                error=last_error,
                status="success",
                postcondition_kind="action_effect",
                postcondition_passed=True,
            )
            return

        if helper in {"click_button_target", "click_numeric_button_target"} and _ptr_try_oracle_guided_action_card(page, label, postcondition):
            _ptr_set_recovery_record(
                "oracle_handler",
                "guided_action_card",
                "oracle_guided_action_card",
                {"label": label},
            )
            _ptr_store_experience_episode(
                action_type=helper,
                label=label,
                page=page,
                locator=locator,
                error=last_error,
                status="success",
                postcondition_kind="action_card_selected",
                postcondition_passed=True,
            )
            return

        for strategy_name, experience_locator, episode in _ptr_experience_repair_locators(page, helper, label, last_error, locator=locator):
            try:
                _ptr_record_strategy_attempt(strategy_name)
                before_experience = _ptr_observe(page, experience_locator)
                _ptr_strict_click(experience_locator)
                after_experience = _ptr_observe(page, experience_locator)
                if postcondition(before_experience, after_experience):
                    _ptr_set_recovery_record(
                        "experience_reuse",
                        str(((episode.get("recovery") or {}).get("kind") or "")).strip() or "experience_reuse",
                        "experience_reuse",
                        {
                            "episode_id": str(episode.get("episode_id") or "").strip(),
                            "retrieval_score": int(episode.get("retrieval_score") or 0),
                            "locator_strategy": _ptr_clone_json_value(((episode.get("recovery") or {}).get("details") or {}).get("locator_strategy") or {}),
                        },
                    )
                    _ptr_store_experience_episode(
                        action_type=helper,
                        label=label,
                        page=page,
                        locator=experience_locator,
                        error=last_error,
                        status="success",
                        postcondition_kind="action_effect",
                        postcondition_passed=True,
                    )
                    return
                last_error = RuntimeError(f'Experience strategy "{strategy_name}" did not satisfy postcondition for "{label}".')
            except Exception as exc:
                last_error = exc

        ai_candidates = _ptr_ai_repair_locators(page, helper, label, last_error, locator=locator)
        last_ai_strategy_name = ""
        for strategy_name, ai_locator, ai_strategy in ai_candidates:
            last_ai_strategy_name = strategy_name
            try:
                _ptr_record_strategy_attempt(strategy_name)
                before_ai = _ptr_observe(page, ai_locator)
                _ptr_strict_click(ai_locator)
                after_ai = _ptr_observe(page, ai_locator)
                if postcondition(before_ai, after_ai):
                    _ptr_set_recovery_record(
                        "ai_validated",
                        "ai_locator_repair",
                        "ai_locator_repair",
                        {
                            "strategy_name": strategy_name,
                            "locator_strategy": _ptr_clone_json_value(ai_strategy),
                        },
                    )
                    _ptr_store_experience_episode(
                        action_type=helper,
                        label=label,
                        page=page,
                        locator=ai_locator,
                        error=last_error,
                        status="success",
                        postcondition_kind="action_effect",
                        postcondition_passed=True,
                    )
                    _ptr_finalize_last_ai_interaction(
                        repair_outcome="validated",
                        strategy_name=strategy_name,
                        postcondition_kind="action_effect",
                    )
                    return
                last_error = RuntimeError(f'AI strategy "{strategy_name}" did not satisfy postcondition for "{label}".')
            except Exception as exc:
                last_error = exc
        if ai_candidates:
            _ptr_finalize_last_ai_interaction(
                repair_outcome="execution_failed",
                strategy_name=last_ai_strategy_name,
                error=last_error,
                postcondition_kind="action_effect",
            )

        raise RuntimeError(f'Unable to click target "{label}" after strict execution, Oracle recovery, and AI self-repair.') from last_error


def _ptr_fill_textbox(locator: Locator, current_page: Page, label: str, value: str) -> None:
    _ptr_register_page(current_page)
    try:
        _ptr_strict_fill(locator, value)
        observed = _ptr_locator_value(locator) or _ptr_locator_text(locator)
        if _ptr_value_matches(value, observed):
            return
        raise RuntimeError(f'Textbox "{label}" did not reflect the requested value.')
    except Exception as direct_exc:
        last_error: Exception = direct_exc
        for strategy_name, experience_locator, episode in _ptr_experience_repair_locators(current_page, "fill_textbox", label, direct_exc, locator=locator):
            try:
                _ptr_record_strategy_attempt(strategy_name)
                _ptr_strict_fill(experience_locator, value)
                observed = _ptr_locator_value(experience_locator) or _ptr_locator_text(experience_locator)
                if _ptr_value_matches(value, observed):
                    _ptr_set_recovery_record(
                        "experience_reuse",
                        str(((episode.get("recovery") or {}).get("kind") or "")).strip() or "experience_reuse",
                        "experience_reuse",
                        {
                            "episode_id": str(episode.get("episode_id") or "").strip(),
                            "retrieval_score": int(episode.get("retrieval_score") or 0),
                            "locator_strategy": _ptr_clone_json_value(((episode.get("recovery") or {}).get("details") or {}).get("locator_strategy") or {}),
                        },
                    )
                    _ptr_store_experience_episode(
                        action_type="fill_textbox",
                        label=label,
                        page=current_page,
                        locator=experience_locator,
                        error=direct_exc,
                        status="success",
                        postcondition_kind="field_value_changed",
                        postcondition_passed=True,
                    )
                    return
                last_error = RuntimeError(f'Experience strategy "{strategy_name}" did not satisfy fill postcondition for "{label}".')
            except Exception as exc:
                last_error = exc
        ai_candidates = _ptr_ai_repair_locators(current_page, "fill_textbox", label, direct_exc, value=value, locator=locator)
        last_ai_strategy_name = ""
        for strategy_name, ai_locator, ai_strategy in ai_candidates:
            last_ai_strategy_name = strategy_name
            try:
                _ptr_record_strategy_attempt(strategy_name)
                _ptr_strict_fill(ai_locator, value)
                observed = _ptr_locator_value(ai_locator) or _ptr_locator_text(ai_locator)
                if _ptr_value_matches(value, observed):
                    _ptr_set_recovery_record(
                        "ai_validated",
                        "ai_locator_repair",
                        "ai_locator_repair",
                        {
                            "strategy_name": strategy_name,
                            "locator_strategy": _ptr_clone_json_value(ai_strategy),
                        },
                    )
                    _ptr_store_experience_episode(
                        action_type="fill_textbox",
                        label=label,
                        page=current_page,
                        locator=ai_locator,
                        error=direct_exc,
                        status="success",
                        postcondition_kind="field_value_changed",
                        postcondition_passed=True,
                    )
                    _ptr_finalize_last_ai_interaction(
                        repair_outcome="validated",
                        strategy_name=strategy_name,
                        postcondition_kind="field_value_changed",
                    )
                    return
                last_error = RuntimeError(f'AI strategy "{strategy_name}" did not satisfy fill postcondition for "{label}".')
            except Exception as exc:
                last_error = exc
        if ai_candidates:
            _ptr_finalize_last_ai_interaction(
                repair_outcome="execution_failed",
                strategy_name=last_ai_strategy_name,
                error=last_error,
                postcondition_kind="field_value_changed",
            )
        raise RuntimeError(f'Unable to fill textbox "{label}" using strict execution and AI self-repair.') from last_error


def _ptr_submit_textbox_enter(locator: Locator, current_page: Page, label: str) -> None:
    _ptr_register_page(current_page)
    before = _ptr_observe(current_page, locator)
    locator.press("Enter")
    current_page.wait_for_timeout(_ptr_wait_ms("PTR_POST_ENTER_WAIT_MS", 400))
    after = _ptr_observe(current_page, locator)
    if _ptr_generic_click_postcondition(before, after):
        return


def _ptr_click_textbox(locator: Locator, current_page: Page, label: str) -> None:
    _ptr_register_page(current_page)
    before = _ptr_observe(current_page, locator)
    _ptr_strict_click(locator)
    after = _ptr_observe(current_page, locator)
    if _ptr_generic_click_postcondition(before, after):
        return
    raise RuntimeError(f'Textbox "{label}" was clicked but focus/state did not change.')


def _ptr_click_combobox(locator: Locator, current_page: Page, label: str) -> None:
    _ptr_register_page(current_page)
    before = _ptr_observe(current_page, locator)
    try:
        _ptr_strict_click(locator)
        current_page.wait_for_timeout(_ptr_wait_ms("PTR_COMBOBOX_OPEN_WAIT_MS", 350))
        after = _ptr_observe(current_page, locator)
        if _ptr_combobox_open_postcondition(before, after):
            return
        raise RuntimeError(f'Combobox "{label}" did not open.')
    except Exception as direct_exc:
        last_error: Exception = direct_exc
        oracle_strategy_name = _ptr_try_open_oracle_select_single_with_keyboard(current_page, locator, direct_exc)
        if oracle_strategy_name:
            _ptr_set_recovery_record(
                "oracle_handler",
                "oracle_select_single_keyboard_open",
                "oracle_select_single_keyboard_open",
                {
                    "trigger_label": label,
                    "strategy_name": oracle_strategy_name,
                },
            )
            _ptr_store_experience_episode(
                action_type="click_combobox",
                label=label,
                page=current_page,
                locator=locator,
                error=direct_exc,
                status="success",
                postcondition_kind="dialog_opened",
                postcondition_passed=True,
            )
            return
        for strategy_name, experience_locator, episode in _ptr_experience_repair_locators(current_page, "click_combobox", label, direct_exc, locator=locator):
            try:
                _ptr_record_strategy_attempt(strategy_name)
                before_experience = _ptr_observe(current_page, experience_locator)
                _ptr_strict_click(experience_locator)
                current_page.wait_for_timeout(_ptr_wait_ms("PTR_COMBOBOX_OPEN_WAIT_MS", 350))
                after_experience = _ptr_observe(current_page, experience_locator)
                if _ptr_combobox_open_postcondition(before_experience, after_experience):
                    _ptr_set_recovery_record(
                        "experience_reuse",
                        str(((episode.get("recovery") or {}).get("kind") or "")).strip() or "experience_reuse",
                        "experience_reuse",
                        {
                            "episode_id": str(episode.get("episode_id") or "").strip(),
                            "retrieval_score": int(episode.get("retrieval_score") or 0),
                            "locator_strategy": _ptr_clone_json_value(((episode.get("recovery") or {}).get("details") or {}).get("locator_strategy") or {}),
                        },
                    )
                    _ptr_store_experience_episode(
                        action_type="click_combobox",
                        label=label,
                        page=current_page,
                        locator=experience_locator,
                        error=direct_exc,
                        status="success",
                        postcondition_kind="dialog_opened",
                        postcondition_passed=True,
                    )
                    return
                last_error = RuntimeError(f'Experience strategy "{strategy_name}" did not open combobox "{label}".')
            except Exception as exc:
                last_error = exc
        ai_candidates = _ptr_ai_repair_locators(current_page, "click_combobox", label, direct_exc, locator=locator)
        last_ai_strategy_name = ""
        for strategy_name, ai_locator, ai_strategy in ai_candidates:
            last_ai_strategy_name = strategy_name
            try:
                _ptr_record_strategy_attempt(strategy_name)
                before_ai = _ptr_observe(current_page, ai_locator)
                _ptr_strict_click(ai_locator)
                current_page.wait_for_timeout(_ptr_wait_ms("PTR_COMBOBOX_OPEN_WAIT_MS", 350))
                after_ai = _ptr_observe(current_page, ai_locator)
                if _ptr_combobox_open_postcondition(before_ai, after_ai):
                    _ptr_set_recovery_record(
                        "ai_validated",
                        "ai_locator_repair",
                        "ai_locator_repair",
                        {
                            "strategy_name": strategy_name,
                            "locator_strategy": _ptr_clone_json_value(ai_strategy),
                        },
                    )
                    _ptr_store_experience_episode(
                        action_type="click_combobox",
                        label=label,
                        page=current_page,
                        locator=ai_locator,
                        error=direct_exc,
                        status="success",
                        postcondition_kind="dialog_opened",
                        postcondition_passed=True,
                    )
                    _ptr_finalize_last_ai_interaction(
                        repair_outcome="validated",
                        strategy_name=strategy_name,
                        postcondition_kind="dialog_opened",
                    )
                    return
                last_error = RuntimeError(f'AI strategy "{strategy_name}" did not open combobox "{label}".')
            except Exception as exc:
                last_error = exc
        if ai_candidates:
            _ptr_finalize_last_ai_interaction(
                repair_outcome="execution_failed",
                strategy_name=last_ai_strategy_name,
                error=last_error,
                postcondition_kind="dialog_opened",
            )
        raise RuntimeError(f'Unable to open combobox "{label}".') from last_error


def _ptr_click_button_target(locator: Locator, current_page: Page, label: str) -> None:
    _ptr_register_page(current_page)
    _ptr_click_with_candidates(
        current_page,
        label,
        locator,
        "click_button_target",
        _ptr_generic_click_postcondition,
    )


def _ptr_click_numeric_button_target(locator: Locator, current_page: Page, label: str) -> None:
    _ptr_register_page(current_page)
    _ptr_click_with_candidates(
        current_page,
        label,
        locator,
        "click_numeric_button_target",
        _ptr_generic_click_postcondition,
    )


def _ptr_click_text_target(locator: Locator, current_page: Page, label: str) -> None:
    _ptr_register_page(current_page)
    _ptr_click_with_candidates(
        current_page,
        label,
        locator,
        "click_text_target",
        _ptr_generic_click_postcondition,
    )


def _ptr_click_listbox_option(locator: Locator, current_page: Page, label: str) -> None:
    _ptr_click_text_target(locator, current_page, label)


def _ptr_select_combobox_option(trigger: Locator, option: Locator, current_page: Page, label: str, option_name: str) -> None:
    _ptr_register_page(current_page)
    _ptr_click_combobox(trigger, current_page, label)
    last_error: Exception | None = None
    option_target = str(option_name or "").strip()
    option_candidates = [
        ("raw_option", option),
        ("role_option", current_page.get_by_role("option", name=option_name)),
        ("role_cell", current_page.get_by_role("cell", name=option_name)),
        ("role_gridcell", current_page.get_by_role("gridcell", name=option_name)),
        ("text_option", current_page.get_by_text(option_name, exact=True)),
    ]
    for strategy_name, candidate in option_candidates:
        try:
            _ptr_record_strategy_attempt(strategy_name)
            resolved = candidate.first if hasattr(candidate, "first") else candidate
            before = _ptr_observe(current_page, resolved)
            _ptr_strict_click(resolved)
            current_page.wait_for_timeout(_ptr_wait_ms("PTR_COMBOBOX_SELECT_WAIT_MS", 400))
            after = _ptr_observe(current_page, resolved)
            if _ptr_option_selection_postcondition(before, after, trigger, resolved, option_name):
                _ptr_wait_for_field_processing(
                    current_page,
                    env_name="PTR_DROPDOWN_CHANGE_PROCESSING_WAIT_MS",
                    default_ms=5000,
                )
                return
            last_error = RuntimeError(f'Combobox "{label}" did not reflect option "{option_name}".')
        except Exception as exc:
            last_error = exc
    if last_error is None:
        last_error = RuntimeError(f'Combobox "{label}" did not reflect option "{option_name}".')

    for strategy_name, experience_locator, episode in _ptr_experience_repair_locators(
        current_page,
        "select_combobox_option",
        option_target,
        last_error,
        locator=option,
    ):
        try:
            _ptr_record_strategy_attempt(strategy_name)
            before_experience = _ptr_observe(current_page, experience_locator)
            _ptr_strict_click(experience_locator)
            current_page.wait_for_timeout(_ptr_wait_ms("PTR_COMBOBOX_SELECT_WAIT_MS", 400))
            after_experience = _ptr_observe(current_page, experience_locator)
            if _ptr_option_selection_postcondition(before_experience, after_experience, trigger, experience_locator, option_name):
                _ptr_wait_for_field_processing(
                    current_page,
                    env_name="PTR_DROPDOWN_CHANGE_PROCESSING_WAIT_MS",
                    default_ms=5000,
                )
                _ptr_set_recovery_record(
                    "experience_reuse",
                    str(((episode.get("recovery") or {}).get("kind") or "")).strip() or "experience_reuse",
                    "experience_reuse",
                    {
                        "trigger_label": label,
                        "option_name": option_name,
                        "episode_id": str(episode.get("episode_id") or "").strip(),
                        "retrieval_score": int(episode.get("retrieval_score") or 0),
                        "locator_strategy": _ptr_clone_json_value(((episode.get("recovery") or {}).get("details") or {}).get("locator_strategy") or {}),
                    },
                )
                _ptr_store_experience_episode(
                    action_type="select_combobox_option",
                    label=option_target,
                    page=current_page,
                    locator=experience_locator,
                    error=last_error,
                    status="success",
                    postcondition_kind="option_selected",
                    postcondition_passed=True,
                )
                return
            last_error = RuntimeError(f'Experience strategy "{strategy_name}" did not select option "{option_name}" for "{label}".')
        except Exception as exc:
            last_error = exc

    ai_candidates = _ptr_ai_repair_locators(
        current_page,
        "select_combobox_option",
        option_target,
        last_error,
        locator=option,
    )
    last_ai_strategy_name = ""
    for strategy_name, ai_locator, ai_strategy in ai_candidates:
        last_ai_strategy_name = strategy_name
        try:
            _ptr_record_strategy_attempt(strategy_name)
            before_ai = _ptr_observe(current_page, ai_locator)
            _ptr_strict_click(ai_locator)
            current_page.wait_for_timeout(_ptr_wait_ms("PTR_COMBOBOX_SELECT_WAIT_MS", 400))
            after_ai = _ptr_observe(current_page, ai_locator)
            if _ptr_option_selection_postcondition(before_ai, after_ai, trigger, ai_locator, option_name):
                _ptr_wait_for_field_processing(
                    current_page,
                    env_name="PTR_DROPDOWN_CHANGE_PROCESSING_WAIT_MS",
                    default_ms=5000,
                )
                _ptr_set_recovery_record(
                    "ai_validated",
                    "ai_locator_repair",
                    "ai_locator_repair",
                    {
                        "trigger_label": label,
                        "option_name": option_name,
                        "strategy_name": strategy_name,
                        "locator_strategy": _ptr_clone_json_value(ai_strategy),
                    },
                )
                _ptr_store_experience_episode(
                    action_type="select_combobox_option",
                    label=option_target,
                    page=current_page,
                    locator=ai_locator,
                    error=last_error,
                    status="success",
                    postcondition_kind="option_selected",
                    postcondition_passed=True,
                )
                _ptr_finalize_last_ai_interaction(
                    repair_outcome="validated",
                    strategy_name=strategy_name,
                    postcondition_kind="option_selected",
                )
                return
            last_error = RuntimeError(f'AI strategy "{strategy_name}" did not select option "{option_name}" for "{label}".')
        except Exception as exc:
            last_error = exc
    if ai_candidates:
        _ptr_finalize_last_ai_interaction(
            repair_outcome="execution_failed",
            strategy_name=last_ai_strategy_name,
            error=last_error,
            postcondition_kind="option_selected",
        )
    raise RuntimeError(f'Unable to select combobox option "{option_name}" for "{label}".') from last_error


def _ptr_select_search_trigger_option(
    trigger: Locator,
    option: Locator,
    current_page: Page,
    title: str,
    option_name: str,
    *,
    option_kind: str = "text",
    fill_value: str | None = None,
) -> None:
    _ptr_register_page(current_page)
    if fill_value is not None:
        _ptr_enter_search_value(
            trigger,
            fill_value,
            timeout_ms=_ptr_wait_ms("PTR_TEXT_ENTRY_TIMEOUT_MS", 3000),
            current_page=current_page,
            label=title,
        )
    else:
        _ptr_strict_click(trigger)
    current_page.wait_for_timeout(_ptr_wait_ms("PTR_SEARCH_RESULTS_WAIT_MS", 750))
    last_error: Exception | None = None
    option_target = str(option_name or "").strip()
    raw_option_timeout_ms = _ptr_wait_ms("PTR_SEARCH_RESULT_TIMEOUT_MS", 6000)
    option_candidates = [
        ("raw_option", option),
        ("role_option", current_page.get_by_role("option", name=option_name)),
        ("role_cell", current_page.get_by_role("cell", name=option_name)),
        ("role_gridcell", current_page.get_by_role("gridcell", name=option_name)),
        ("text_option", current_page.get_by_text(option_name, exact=True)),
    ]
    for strategy_name, candidate in option_candidates:
        try:
            resolved = candidate.first if hasattr(candidate, "first") else candidate
            _ptr_record_strategy_attempt(strategy_name)
            before = _ptr_observe(current_page, resolved)
            timeout_ms = raw_option_timeout_ms if strategy_name == "raw_option" else None
            _ptr_strict_click(resolved, timeout_ms=timeout_ms)
            current_page.wait_for_timeout(_ptr_wait_ms("PTR_POST_CLICK_WAIT_MS", 250))
            after = _ptr_observe(current_page, resolved)
            if _ptr_option_selection_postcondition(before, after, trigger, resolved, option_name):
                _ptr_wait_for_field_processing(
                    current_page,
                    env_name="PTR_DROPDOWN_CHANGE_PROCESSING_WAIT_MS",
                    default_ms=5000,
                )
                return
            last_error = RuntimeError(f'Search trigger "{title}" did not apply option "{option_name}".')
        except Exception as exc:
            last_error = exc
    if last_error is None:
        last_error = RuntimeError(f'Search trigger "{title}" did not apply option "{option_name}".')

    for strategy_name, experience_locator, episode in _ptr_experience_repair_locators(
        current_page,
        "select_search_trigger_option",
        option_target,
        last_error,
        locator=option,
    ):
        try:
            _ptr_record_strategy_attempt(strategy_name)
            before_experience = _ptr_observe(current_page, experience_locator)
            _ptr_strict_click(experience_locator)
            current_page.wait_for_timeout(_ptr_wait_ms("PTR_POST_CLICK_WAIT_MS", 250))
            after_experience = _ptr_observe(current_page, experience_locator)
            if _ptr_option_selection_postcondition(before_experience, after_experience, trigger, experience_locator, option_name):
                _ptr_wait_for_field_processing(
                    current_page,
                    env_name="PTR_DROPDOWN_CHANGE_PROCESSING_WAIT_MS",
                    default_ms=5000,
                )
                _ptr_set_recovery_record(
                    "experience_reuse",
                    str(((episode.get("recovery") or {}).get("kind") or "")).strip() or "experience_reuse",
                    "experience_reuse",
                    {
                        "trigger_label": title,
                        "option_name": option_name,
                        "episode_id": str(episode.get("episode_id") or "").strip(),
                        "retrieval_score": int(episode.get("retrieval_score") or 0),
                        "locator_strategy": _ptr_clone_json_value(((episode.get("recovery") or {}).get("details") or {}).get("locator_strategy") or {}),
                    },
                )
                _ptr_store_experience_episode(
                    action_type="select_search_trigger_option",
                    label=option_target,
                    page=current_page,
                    locator=experience_locator,
                    error=last_error,
                    status="success",
                    postcondition_kind="option_selected",
                    postcondition_passed=True,
                )
                return
            last_error = RuntimeError(f'Experience strategy "{strategy_name}" did not apply search option "{option_name}".')
        except Exception as exc:
            last_error = exc

    ai_candidates = _ptr_ai_repair_locators(
        current_page,
        "select_search_trigger_option",
        option_target,
        last_error,
        locator=option,
    )
    last_ai_strategy_name = ""
    for strategy_name, ai_locator, ai_strategy in ai_candidates:
        last_ai_strategy_name = strategy_name
        try:
            _ptr_record_strategy_attempt(strategy_name)
            before_ai = _ptr_observe(current_page, ai_locator)
            _ptr_strict_click(ai_locator)
            current_page.wait_for_timeout(_ptr_wait_ms("PTR_POST_CLICK_WAIT_MS", 250))
            after_ai = _ptr_observe(current_page, ai_locator)
            if _ptr_option_selection_postcondition(before_ai, after_ai, trigger, ai_locator, option_name):
                _ptr_wait_for_field_processing(
                    current_page,
                    env_name="PTR_DROPDOWN_CHANGE_PROCESSING_WAIT_MS",
                    default_ms=5000,
                )
                _ptr_set_recovery_record(
                    "ai_validated",
                    "ai_locator_repair",
                    "ai_locator_repair",
                    {
                        "trigger_label": title,
                        "option_name": option_name,
                        "strategy_name": strategy_name,
                        "locator_strategy": _ptr_clone_json_value(ai_strategy),
                    },
                )
                _ptr_store_experience_episode(
                    action_type="select_search_trigger_option",
                    label=option_target,
                    page=current_page,
                    locator=ai_locator,
                    error=last_error,
                    status="success",
                    postcondition_kind="option_selected",
                    postcondition_passed=True,
                )
                _ptr_finalize_last_ai_interaction(
                    repair_outcome="validated",
                    strategy_name=strategy_name,
                    postcondition_kind="option_selected",
                )
                return
            last_error = RuntimeError(f'AI strategy "{strategy_name}" did not apply search option "{option_name}".')
        except Exception as exc:
            last_error = exc
    if ai_candidates:
        _ptr_finalize_last_ai_interaction(
            repair_outcome="execution_failed",
            strategy_name=last_ai_strategy_name,
            error=last_error,
            postcondition_kind="option_selected",
        )
    raise RuntimeError(f'Unable to apply search option "{option_name}" for "{title}".') from last_error


def _ptr_select_adf_menu_panel_option(
    trigger: Locator,
    option: Locator,
    current_page: Page,
    trigger_label: str,
    option_name: str,
    *,
    trigger_kind: str = "title",
) -> None:
    _ptr_register_page(current_page)
    _ptr_strict_click(trigger)
    current_page.wait_for_timeout(_ptr_wait_ms("PTR_MENU_OPEN_WAIT_MS", 350))
    last_error: Exception | None = None
    option_target = str(option_name or "").strip()
    option_candidates = [
        ("raw_option", option),
        ("role_menuitem", current_page.get_by_role("menuitem", name=option_name)),
        ("role_option", current_page.get_by_role("option", name=option_name)),
        ("text_option", current_page.get_by_text(option_name, exact=True)),
    ]
    for strategy_name, candidate in option_candidates:
        try:
            resolved = candidate.first if hasattr(candidate, "first") else candidate
            _ptr_record_strategy_attempt(strategy_name)
            before = _ptr_observe(current_page, resolved)
            _ptr_strict_click(resolved)
            current_page.wait_for_timeout(_ptr_wait_ms("PTR_POST_CLICK_WAIT_MS", 250))
            after = _ptr_observe(current_page, resolved)
            if _ptr_option_selection_postcondition(before, after, trigger, resolved, option_name):
                _ptr_wait_for_field_processing(
                    current_page,
                    env_name="PTR_DROPDOWN_CHANGE_PROCESSING_WAIT_MS",
                    default_ms=5000,
                )
                return
            last_error = RuntimeError(f'Menu panel "{trigger_label}" did not apply option "{option_name}".')
        except Exception as exc:
            last_error = exc
    if last_error is None:
        last_error = RuntimeError(f'Menu panel "{trigger_label}" did not apply option "{option_name}".')

    for strategy_name, experience_locator, episode in _ptr_experience_repair_locators(
        current_page,
        "select_adf_menu_panel_option",
        option_target,
        last_error,
        locator=option,
    ):
        try:
            _ptr_record_strategy_attempt(strategy_name)
            before_experience = _ptr_observe(current_page, experience_locator)
            _ptr_strict_click(experience_locator)
            current_page.wait_for_timeout(_ptr_wait_ms("PTR_POST_CLICK_WAIT_MS", 250))
            after_experience = _ptr_observe(current_page, experience_locator)
            if _ptr_option_selection_postcondition(before_experience, after_experience, trigger, experience_locator, option_name):
                _ptr_wait_for_field_processing(
                    current_page,
                    env_name="PTR_DROPDOWN_CHANGE_PROCESSING_WAIT_MS",
                    default_ms=5000,
                )
                _ptr_set_recovery_record(
                    "experience_reuse",
                    str(((episode.get("recovery") or {}).get("kind") or "")).strip() or "experience_reuse",
                    "experience_reuse",
                    {
                        "trigger_label": trigger_label,
                        "option_name": option_name,
                        "episode_id": str(episode.get("episode_id") or "").strip(),
                        "retrieval_score": int(episode.get("retrieval_score") or 0),
                        "locator_strategy": _ptr_clone_json_value(((episode.get("recovery") or {}).get("details") or {}).get("locator_strategy") or {}),
                    },
                )
                _ptr_store_experience_episode(
                    action_type="select_adf_menu_panel_option",
                    label=option_target,
                    page=current_page,
                    locator=experience_locator,
                    error=last_error,
                    status="success",
                    postcondition_kind="option_selected",
                    postcondition_passed=True,
                )
                return
            last_error = RuntimeError(f'Experience strategy "{strategy_name}" did not apply menu option "{option_name}".')
        except Exception as exc:
            last_error = exc

    ai_candidates = _ptr_ai_repair_locators(
        current_page,
        "select_adf_menu_panel_option",
        option_target,
        last_error,
        locator=option,
    )
    last_ai_strategy_name = ""
    for strategy_name, ai_locator, ai_strategy in ai_candidates:
        last_ai_strategy_name = strategy_name
        try:
            _ptr_record_strategy_attempt(strategy_name)
            before_ai = _ptr_observe(current_page, ai_locator)
            _ptr_strict_click(ai_locator)
            current_page.wait_for_timeout(_ptr_wait_ms("PTR_POST_CLICK_WAIT_MS", 250))
            after_ai = _ptr_observe(current_page, ai_locator)
            if _ptr_option_selection_postcondition(before_ai, after_ai, trigger, ai_locator, option_name):
                _ptr_wait_for_field_processing(
                    current_page,
                    env_name="PTR_DROPDOWN_CHANGE_PROCESSING_WAIT_MS",
                    default_ms=5000,
                )
                _ptr_set_recovery_record(
                    "ai_validated",
                    "ai_locator_repair",
                    "ai_locator_repair",
                    {
                        "trigger_label": trigger_label,
                        "option_name": option_name,
                        "strategy_name": strategy_name,
                        "locator_strategy": _ptr_clone_json_value(ai_strategy),
                    },
                )
                _ptr_store_experience_episode(
                    action_type="select_adf_menu_panel_option",
                    label=option_target,
                    page=current_page,
                    locator=ai_locator,
                    error=last_error,
                    status="success",
                    postcondition_kind="option_selected",
                    postcondition_passed=True,
                )
                _ptr_finalize_last_ai_interaction(
                    repair_outcome="validated",
                    strategy_name=strategy_name,
                    postcondition_kind="option_selected",
                )
                return
            last_error = RuntimeError(f'AI strategy "{strategy_name}" did not apply menu option "{option_name}".')
        except Exception as exc:
                last_error = exc
    if ai_candidates:
        _ptr_finalize_last_ai_interaction(
            repair_outcome="execution_failed",
            strategy_name=last_ai_strategy_name,
            error=last_error,
            postcondition_kind="option_selected",
        )
    raise RuntimeError(f'Unable to apply menu option "{option_name}" for "{trigger_label}".') from last_error


def _ptr_wait_for_date_icon(icon: Locator, current_page: Page, title: str) -> Locator:
    title_text = str(title or "").strip()
    timeout_ms = _ptr_wait_ms("PTR_DATE_ICON_READY_TIMEOUT_MS", 8000)
    poll_ms = _ptr_wait_ms("PTR_DATE_ICON_POLL_MS", 250)
    deadline = time.time() + (timeout_ms / 1000.0)
    ready_state = ""
    busy_indicators = 0
    candidates: list[tuple[str, Locator]] = []

    if title_text:
        escaped_title = title_text.replace("\\", "\\\\").replace('"', '\\"')
        candidates.extend(
            [
                ("date_attr_match", current_page.locator(f'[title="{escaped_title}"], [aria-label="{escaped_title}"]').first),
                ("date_label_match", current_page.get_by_label(title_text)),
            ]
        )

    while time.time() < deadline:
        ready_state = str(_ptr_safe_page_eval(current_page, "() => document.readyState") or "").strip()
        busy_indicators = _ptr_busy_indicator_count(current_page)
        if _ptr_locator_is_actionable(icon, timeout_ms=500):
            return icon
        for strategy_name, candidate in candidates:
            try:
                resolved = candidate.first if hasattr(candidate, "first") else candidate
            except Exception:
                resolved = candidate
            if _ptr_locator_is_actionable(resolved, timeout_ms=400):
                _ptr_record_strategy_attempt(strategy_name)
                return resolved
        current_page.wait_for_timeout(max(100, poll_ms))

    raise RuntimeError(
        f'Date control "{title_text or "date picker"}" did not become ready within {timeout_ms}ms. '
        f"ready_state={ready_state or 'unknown'}; busy_indicators={busy_indicators}."
    )


def _ptr_pick_date_via_icon(icon: Locator, day: Locator, current_page: Page, title: str, day_label: str) -> None:
    _ptr_register_page(current_page)
    icon_target = _ptr_wait_for_date_icon(icon, current_page, title)
    _ptr_strict_click(icon_target, timeout_ms=_ptr_wait_ms("PTR_DATE_ICON_CLICK_TIMEOUT_MS", 4000))
    current_page.wait_for_timeout(_ptr_wait_ms("PTR_DATE_PICKER_WAIT_MS", 300))
    if not _ptr_locator_is_actionable(day, timeout_ms=_ptr_wait_ms("PTR_DATE_DAY_READY_TIMEOUT_MS", 5000)):
        raise RuntimeError(
            f'Date option "{day_label}" did not become ready after opening "{title}" within '
            f'{_ptr_wait_ms("PTR_DATE_DAY_READY_TIMEOUT_MS", 5000)}ms.'
        )
    before = _ptr_observe(current_page, day)
    _ptr_record_strategy_attempt("day_select")
    _ptr_strict_click(day)
    deadline = time.time() + (_ptr_wait_ms("PTR_DATE_POST_SELECT_WAIT_MS", 6000) / 1000.0)
    while time.time() < deadline:
        after = _ptr_observe(current_page, day)
        if int(after.get("dialog_count") or 0) < int(before.get("dialog_count") or 0):
            _ptr_wait_for_field_processing(
                current_page,
                env_name="PTR_DATE_CHANGE_PROCESSING_WAIT_MS",
                default_ms=5000,
            )
            return
        if _ptr_generic_click_postcondition(before, after):
            _ptr_wait_for_field_processing(
                current_page,
                env_name="PTR_DATE_CHANGE_PROCESSING_WAIT_MS",
                default_ms=5000,
            )
            return
        if int(before.get("dialog_count") or 0) > 0 and not _ptr_locator_is_actionable(day, timeout_ms=250):
            _ptr_wait_for_field_processing(
                current_page,
                env_name="PTR_DATE_CHANGE_PROCESSING_WAIT_MS",
                default_ms=5000,
            )
            return
        current_page.wait_for_timeout(200)
    raise RuntimeError(
        f'Date option "{day_label}" did not apply within '
        f'{_ptr_wait_ms("PTR_DATE_POST_SELECT_WAIT_MS", 6000)}ms after opening "{title}".'
    )


def _ptr_click_navigation_button(locator: Locator, current_page: Page, label: str) -> None:
    _ptr_register_page(current_page)
    before = _ptr_observe(current_page, locator)
    before_step = before.get("guided_step") or ""
    guided_process = str(_ptr_page_signature(current_page, before).get("surface_type") or "").strip() == "guided_process"
    normalized_label = _ptr_normalize_text(label)

    def _is_disabled(observation: dict[str, Any]) -> bool:
        meta = observation.get("target_meta") if isinstance(observation, dict) else {}
        meta = meta if isinstance(meta, dict) else {}
        disabled = _ptr_normalize_text(meta.get("disabled"))
        if disabled == "true":
            return True
        aria_disabled = _ptr_normalize_text(meta.get("aria_disabled"))
        return aria_disabled == "true"

    before_disabled = _is_disabled(before)
    _ptr_strict_click(locator, timeout_ms=_ptr_wait_ms("PTR_NAV_BUTTON_CLICK_TIMEOUT_MS", 4000))
    deadline = time.time() + (_ptr_wait_ms("PTR_NAV_BUTTON_POSTCONDITION_TIMEOUT_MS", 15000) / 1000.0)
    validation_grace_ms = _ptr_wait_ms("PTR_NAV_BUTTON_VALIDATION_GRACE_MS", 1200)
    validation_seen_at: float | None = None
    last_validation_messages: list[str] = []
    while time.time() < deadline:
        after = _ptr_observe(current_page, locator)
        after_step = after.get("guided_step") or ""
        if before_step and after_step and before_step != after_step:
            return
        if guided_process:
            if _ptr_guided_flow_advanced(before.get("guided_flow") or {}, after.get("guided_flow") or {}):
                return
            if before.get("url") != after.get("url"):
                return
            if before.get("title") != after.get("title"):
                return
        elif _ptr_generic_click_postcondition(before, after):
            return
        validation_messages = _ptr_collect_validation_messages(current_page)
        if validation_messages:
            if validation_messages != last_validation_messages:
                last_validation_messages = list(validation_messages)
                validation_seen_at = time.time()
            grace_elapsed = validation_seen_at is not None and ((time.time() - validation_seen_at) * 1000.0 >= validation_grace_ms)
            submit_disabled_after_click = (
                guided_process
                and normalized_label == "submit"
                and not before_disabled
                and _is_disabled(after)
            )
            if submit_disabled_after_click:
                current_page.wait_for_timeout(250)
                continue
            if not grace_elapsed or _ptr_busy_indicator_count(current_page) > 0:
                current_page.wait_for_timeout(250)
                continue
            prefix = f'Navigation button "{label}" did not advance'
            if before_step:
                prefix += f' from step "{before_step}"'
            raise RuntimeError(f"{prefix}. " + "; ".join(validation_messages))
        validation_seen_at = None
        last_validation_messages = []
        current_page.wait_for_timeout(250)
    suffix = f' from step "{before_step}"' if before_step else ""
    validation_messages = _ptr_collect_validation_messages(current_page)
    if validation_messages:
        raise RuntimeError(
            f'Navigation button "{label}" did not advance{suffix}. ' + "; ".join(validation_messages)
        )
    raise RuntimeError(
        f'Navigation button "{label}" did not advance{suffix} within '
        f'{_ptr_wait_ms("PTR_NAV_BUTTON_POSTCONDITION_TIMEOUT_MS", 15000)}ms. '
        "The click completed, but the guided-flow state, page title, and URL did not change, "
        "and no explicit validation message became visible."
    )


def _ptr_wait_for_post_login_redirect(current_page: Page, expected_url: str) -> None:
    _ptr_register_page(current_page)
    timeout_ms = _ptr_wait_ms("PTR_LOGIN_REDIRECT_WAIT_MS", 15000)
    deadline = time.time() + timeout_ms / 1000.0
    normalized_expected = str(expected_url or "").strip()
    while time.time() < deadline:
        try:
            current_url = str(current_page.url or "").strip()
        except Exception:
            current_url = ""
        if normalized_expected and normalized_expected in current_url:
            return
        if current_url and "signin" not in current_url.lower() and "login" not in current_url.lower():
            return
        current_page.wait_for_timeout(250)
    raise RuntimeError("Post-login redirect did not settle within the configured timeout.")
