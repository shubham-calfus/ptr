from __future__ import annotations

import atexit
import base64
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from playwright.sync_api import Browser, BrowserContext, Locator, Page

try:
    from .experience import (
        append_episode as _experience_append_episode,
        retrieve_recovery_candidates as _experience_retrieve_recovery_candidates,
    )
    from ..utils.ai_repair_prompt import (
        AI_REPAIR_SYSTEM_PROMPT,
        build_ai_repair_prompt,
    )
    from ..utils.runtime_env import get_runner_env_value, load_runner_local_env
except ImportError:  # pragma: no cover - published/runtime fallback
    from src.runtime.experience import (
        append_episode as _experience_append_episode,
        retrieve_recovery_candidates as _experience_retrieve_recovery_candidates,
    )
    from src.utils.ai_repair_prompt import (
        AI_REPAIR_SYSTEM_PROMPT,
        build_ai_repair_prompt,
    )
    from src.utils.runtime_env import get_runner_env_value, load_runner_local_env

__all__ = [
    "_ptr_launch_chromium",
    "_ptr_register_page",
    "_ptr_set_script_data",
    "_ptr_wait_ms",
    "_ptr_wait_after_interaction",
    "_ptr_capture_failure",
    "_ptr_write_diagnostics",
    "_ptr_tracked_action",
    "_ptr_tracked_raw_action",
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
    "_ptr_check_target",
    "_ptr_uncheck_target",
    "_ptr_click_numeric_button_target",
    "_ptr_click_text_target",
    "_ptr_dblclick_text_target",
    "_ptr_click_table_field",
    "_ptr_click_table_row",
    "_ptr_click_listbox_option",
    "_ptr_select_option_target",
    "_ptr_select_combobox_option",
    "_ptr_select_search_trigger_option",
    "_ptr_select_adf_menu_panel_option",
    "_ptr_pick_date_via_icon",
    "_ptr_click_navigation_button",
    "_ptr_wait_for_post_login_redirect",
    "_ptr_ai_extract",
    "_ptr_resolve",
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
    "debug": {},
}

_PTR_DIAGNOSTICS_PATH = os.getenv("PTR_DIAGNOSTICS_PATH", "")
_PTR_FAILURE_SCREENSHOT_PATH = os.getenv("PTR_FAILURE_SCREENSHOT_PATH", "")
_PTR_STEP_ARTIFACTS_DIR = os.getenv("PTR_STEP_ARTIFACTS_DIR", "")
_PTR_EXPERIENCE_STORE_PATH = os.getenv("PTR_EXPERIENCE_STORE_PATH", "")
_PTR_RUNNER_VERSION = str(os.getenv("PTR_RUNNER_VERSION", "ptr-v2")).strip() or "ptr-v2"
_PTR_SUPPRESS_PATCH_CAPTURE = 0
_PTR_LAST_PAGE_SNAPSHOT: dict[str, Any] = {}
# Default settle wait applied after every tracked action. Overridable per run via the
# PTR_AFTER_ACTION_WAIT_MS env var (set by the tool from the recording payload); this
# literal is only the fallback when the caller does not provide a value.
_PTR_HARDCODED_AFTER_ACTION_WAIT_MS = 10_000
_PTR_STEEL_BROWSER_SESSION_IDS: dict[int, str] = {}
_PTR_STEEL_RELEASE_SESSION_IDS: set[str] = set()
_PTR_NEXT_STEP_SCREENSHOT_OVERRIDE_PNG: bytes | None = None
load_runner_local_env()

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


def _ptr_resolve_wait_override_ms(value: Any, env_name: str, default: int) -> int:
    if value is None:
        return _ptr_wait_ms(env_name, default)
    try:
        return max(0, int(value))
    except Exception:
        return _ptr_wait_ms(env_name, default)


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
        cloned = _ptr_clone_json_value(payload) or {}
        _PTR_SCRIPT_DATA = cloned
        _PTR_CURRENT_STRATEGY["script_data"] = _ptr_clone_json_value(cloned) or {}
    else:
        _PTR_SCRIPT_DATA = {}
        _PTR_CURRENT_STRATEGY["script_data"] = {}


def _ptr_current_script_data() -> dict[str, Any]:
    current = _PTR_CURRENT_STRATEGY.get("script_data") or _PTR_SCRIPT_DATA
    return _ptr_clone_json_value(current or {}) or {}


def _ptr_debug_enabled() -> bool:
    return _ptr_env_flag("PTR_DEBUG_TRACE", "false")


def _ptr_trim_debug_text(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _ptr_set_debug_detail(key: str, payload: Any) -> None:
    normalized_key = str(key or "").strip()
    if not normalized_key:
        return
    debug_payload = _PTR_CURRENT_STRATEGY.setdefault("debug", {})
    if not isinstance(debug_payload, dict):
        debug_payload = {}
        _PTR_CURRENT_STRATEGY["debug"] = debug_payload
    debug_payload[normalized_key] = _ptr_clone_json_value(payload)


def _ptr_debug_observation_summary(observation: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(observation, dict):
        return {}
    active_element = observation.get("active_element")
    active_element = active_element if isinstance(active_element, dict) else {}
    target_meta = observation.get("target_meta")
    target_meta = target_meta if isinstance(target_meta, dict) else {}
    guided_flow = observation.get("guided_flow")
    guided_flow = guided_flow if isinstance(guided_flow, dict) else {}
    return {
        "url": str(observation.get("url") or "").strip(),
        "title": str(observation.get("title") or "").strip(),
        "guided_step": str(observation.get("guided_step") or "").strip(),
        "guided_flow_primary_heading": _ptr_trim_debug_text(guided_flow.get("primary_heading"), 160),
        "dialog_count": int(observation.get("dialog_count") or 0),
        "body_marker": _ptr_trim_debug_text(observation.get("body_marker"), 160),
        "active_element": {
            "tag": str(active_element.get("tag") or "").strip(),
            "id": str(active_element.get("id") or "").strip(),
            "name": str(active_element.get("name") or "").strip(),
            "role": str(active_element.get("role") or "").strip(),
            "aria_label": _ptr_trim_debug_text(active_element.get("aria_label"), 120),
            "title": _ptr_trim_debug_text(active_element.get("title"), 120),
        },
        "target": {
            "visible": bool(observation.get("target_visible")),
            "value": _ptr_trim_debug_text(observation.get("target_value"), 120),
            "text": _ptr_trim_debug_text(observation.get("target_text"), 120),
            "tag": str(target_meta.get("tag") or "").strip(),
            "id": str(target_meta.get("id") or "").strip(),
            "name": str(target_meta.get("name") or "").strip(),
            "role": str(target_meta.get("role") or "").strip(),
            "aria_label": _ptr_trim_debug_text(target_meta.get("aria_label"), 120),
            "title": _ptr_trim_debug_text(target_meta.get("title"), 120),
            "class_name": _ptr_trim_debug_text(target_meta.get("class_name"), 120),
            "aria_expanded": str(target_meta.get("aria_expanded") or "").strip(),
            "aria_selected": str(target_meta.get("aria_selected") or "").strip(),
        },
    }


def _ptr_update_debug_detail(key: str, payload: dict[str, Any]) -> dict[str, Any]:
    current = _PTR_CURRENT_STRATEGY.get("debug") or {}
    existing = current.get(key) if isinstance(current, dict) else {}
    merged = _ptr_clone_json_value(existing) if isinstance(existing, dict) else {}
    if not isinstance(merged, dict):
        merged = {}
    merged.update(_ptr_clone_json_value(payload) or {})
    _ptr_set_debug_detail(key, merged)
    return merged


def _ptr_reset_strategy_tracking(helper: str, label: str = "") -> None:
    _PTR_CURRENT_STRATEGY["helper"] = helper
    _PTR_CURRENT_STRATEGY["strategy"] = "direct"
    _PTR_CURRENT_STRATEGY["label"] = label
    _PTR_CURRENT_STRATEGY["attempts"] = []
    _PTR_CURRENT_STRATEGY["ai_interactions"] = []
    _PTR_CURRENT_STRATEGY["experience_interactions"] = []
    _PTR_CURRENT_STRATEGY["script_data"] = _ptr_clone_json_value(_PTR_SCRIPT_DATA or {}) or {}
    _PTR_CURRENT_STRATEGY["recovery"] = None
    _PTR_CURRENT_STRATEGY["debug"] = {}


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


def _ptr_last_ai_interaction() -> dict[str, Any]:
    interactions = _PTR_CURRENT_STRATEGY.setdefault("ai_interactions", [])
    if not interactions or not isinstance(interactions[-1], dict):
        return {}
    return _ptr_clone_json_value(interactions[-1]) or {}


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


def _ptr_resolve_browser_provider() -> str:
    explicit_provider = str(os.getenv("PTR_BROWSER_PROVIDER", "")).strip().lower()
    if explicit_provider:
        if explicit_provider not in {"local", "steel"}:
            raise RuntimeError(f"Unsupported PTR_BROWSER_PROVIDER: {explicit_provider}")
        return explicit_provider

    if _ptr_env_flag("PTR_IS_LOCAL_ENV", "false"):
        return "local"

    return "steel" if str(os.getenv("STEEL_API_KEY", "")).strip() else "local"


def _ptr_release_steel_session(session_id: str) -> None:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id or normalized_session_id not in _PTR_STEEL_RELEASE_SESSION_IDS:
        return

    steel_api_key = str(os.getenv("STEEL_API_KEY", "")).strip()
    if not steel_api_key:
        return

    try:
        from steel import Steel

        Steel(steel_api_key=steel_api_key).sessions.release(normalized_session_id)
    except Exception:
        pass
    finally:
        _PTR_STEEL_RELEASE_SESSION_IDS.discard(normalized_session_id)


def _ptr_release_pending_steel_sessions() -> None:
    for pending_session_id in list(_PTR_STEEL_RELEASE_SESSION_IDS):
        _ptr_release_steel_session(pending_session_id)


def _ptr_launch_steel_browser(playwright, *, headless: bool) -> Any:
    steel_api_key = str(os.getenv("STEEL_API_KEY", "")).strip()
    if not steel_api_key:
        raise RuntimeError("PTR_BROWSER_PROVIDER=steel but STEEL_API_KEY is not configured.")

    from steel import Steel

    browser_type = getattr(playwright, "chromium")
    connect_over_cdp = getattr(browser_type, "connect_over_cdp", None)
    if connect_over_cdp is None:
        raise RuntimeError("Playwright chromium browser type does not support connect_over_cdp.")

    steel_session_id = str(os.getenv("STEEL_SESSION_ID", "")).strip()
    steel_connect_url = str(os.getenv("STEEL_CONNECT_URL", "wss://connect.steel.dev")).strip() or "wss://connect.steel.dev"
    steel_client = Steel(steel_api_key=steel_api_key)
    steel_connect_retries = max(0, _ptr_int_env("PTR_STEEL_CONNECT_RETRIES", 2))
    steel_session_timeout_ms = max(60_000, _ptr_int_env("PTR_STEEL_SESSION_TIMEOUT_MS", 900_000))

    last_error: Exception | None = None
    for attempt in range(steel_connect_retries + 1):
        created_session_id = ""
        should_release_session = False
        try:
            if steel_session_id:
                steel_session = steel_client.sessions.retrieve(steel_session_id)
            else:
                steel_session = steel_client.sessions.create(
                    api_timeout=steel_session_timeout_ms,
                    headless=bool(headless),
                )
                created_session_id = str(getattr(steel_session, "id", "")).strip()
                should_release_session = bool(created_session_id)
                if should_release_session:
                    _PTR_STEEL_RELEASE_SESSION_IDS.add(created_session_id)

            active_session_id = str(getattr(steel_session, "id", "")).strip()
            if not active_session_id:
                raise RuntimeError("Steel session creation did not return a session id.")

            connect_url = f"{steel_connect_url}?{urlencode({'apiKey': steel_api_key, 'sessionId': active_session_id})}"
            browser = connect_over_cdp(connect_url)
            if should_release_session:
                _PTR_STEEL_BROWSER_SESSION_IDS[id(browser)] = active_session_id
            return browser
        except Exception as exc:
            exc_text = str(exc)
            if (
                exc.__class__.__name__ == "AuthenticationError"
                or "Authentication failed" in exc_text
                or "Unauthorized" in exc_text
            ):
                raise RuntimeError(
                    "Steel authentication failed while creating the browser session. "
                    "Verify STEEL_API_KEY in the worker environment and restart both "
                    "the tool and agent workers after updating it."
                ) from exc

            if created_session_id:
                _ptr_release_steel_session(created_session_id)
            last_error = exc
            if attempt >= steel_connect_retries:
                break
            time.sleep(min(2**attempt, 5))

    if last_error is not None:
        raise last_error
    raise RuntimeError("Failed to connect to Steel browser.")


def _ptr_launch_chromium(playwright, headless: bool = False):
    browser_provider = _ptr_resolve_browser_provider()
    desired_headless = _ptr_env_flag("PTR_HEADLESS", "false")
    effective_headless = desired_headless if not headless else headless
    if browser_provider == "steel":
        return _ptr_launch_steel_browser(playwright, headless=effective_headless)

    window_width = max(960, _ptr_int_env("PTR_WINDOW_WIDTH", 1440))
    window_height = max(700, _ptr_int_env("PTR_WINDOW_HEIGHT", 900))
    launch_kwargs: dict[str, Any] = {
        "headless": effective_headless,
        "args": [f"--window-size={window_width},{window_height}"],
    }

    chromium_executable = ""
    if _ptr_env_flag("PTR_USE_SYSTEM_CHROMIUM", "true"):
        configured_path = str(
            os.getenv("PTR_CHROMIUM_EXECUTABLE_PATH")
            or os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
            or ""
        ).strip()
        candidate_paths = [configured_path] if configured_path else []
        candidate_paths.extend([
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ])
        for candidate in candidate_paths:
            if candidate and os.path.exists(candidate):
                chromium_executable = candidate
                break

    if chromium_executable:
        launch_kwargs["executable_path"] = chromium_executable
    else:
        launch_kwargs["channel"] = "chromium"

    return playwright.chromium.launch(**launch_kwargs)


def _ptr_register_page(page: Page) -> Page:
    global _PTR_LAST_PAGE
    _PTR_LAST_PAGE = page
    return page


def _ptr_capture_oracle_table_snapshots(page: Page | None) -> list[dict[str, Any]]:
    tables = _ptr_safe_page_eval(
        page,
        r"""() => {
            const text = (value) => String(value || "").replace(/\s+/g, " ").trim();
            const isVisible = (node) => {
                if (!node) return false;
                const style = window.getComputedStyle(node);
                if (!style || style.display === "none" || style.visibility === "hidden") return false;
                const rect = node.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };

            const seen = new Set();
            const candidates = Array.from(
                document.querySelectorAll(".oj-table-scroller table.oj-table-element, table.oj-table-element")
            );

            return candidates
                .filter((table) => {
                    if (!isVisible(table) || seen.has(table)) return false;
                    seen.add(table);
                    return true;
                })
                .slice(0, 3)
                .map((table, tableIndex) => {
                    const headerCells = Array.from(table.querySelectorAll("thead tr th"));
                    const columnIndexes = [];
                    const headers = [];
                    headerCells.forEach((cell, index) => {
                        const label = text(cell.getAttribute("abbr") || cell.innerText || cell.textContent);
                        if (!label) return;
                        columnIndexes.push(index);
                        headers.push(label);
                    });

                    const rows = Array.from(table.querySelectorAll("tbody tr"))
                        .slice(0, 5)
                        .map((row) => {
                            const cells = Array.from(row.children);
                            return columnIndexes.map((columnIndex) =>
                                text(cells[columnIndex]?.innerText || cells[columnIndex]?.textContent)
                            );
                        })
                        .filter((row) => row.some((value) => value));

                    return {
                        table_index: tableIndex,
                        id: text(table.id),
                        aria_labelledby: text(table.getAttribute("aria-labelledby")),
                        headers,
                        rows,
                    };
                })
                .filter((table) => table.headers.length > 0 && table.rows.length > 0);
        }""",
    )
    return tables if isinstance(tables, list) else []


def _ptr_flow_context_capture_enabled() -> bool:
    return _ptr_env_flag("PTR_FLOW_CONTEXT_CAPTURE_ENABLED", "true")


def _ptr_capture_semantic_snapshot(page: Page | None) -> dict[str, Any]:
    if page is None:
        return {
            "label_values": [],
            "text_candidates": [],
            "dialogs": [],
        }

    snapshot = _ptr_safe_page_eval(
        page,
        r"""() => {
            const normalize = (value, maxLen = 300) => {
                const text = String(value || "").replace(/\s+/g, " ").trim();
                return text.length > maxLen ? text.slice(0, maxLen).trim() : text;
            };
            const isVisible = (node) => {
                if (!node) return false;
                const style = window.getComputedStyle(node);
                if (!style || style.display === "none" || style.visibility === "hidden") return false;
                const rect = node.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const byIdsText = (ids) => {
                return normalize(
                    String(ids || "")
                        .split(/\s+/)
                        .map((id) => document.getElementById(id))
                        .filter(Boolean)
                        .map((node) => normalize(node.innerText || node.textContent))
                        .filter(Boolean)
                        .join(" ")
                );
            };
            const nearestLabelText = (node) => {
                if (!node) return "";
                const explicit = normalize(node.getAttribute?.("aria-label"));
                if (explicit) return explicit;
                const labelledBy = byIdsText(node.getAttribute?.("aria-labelledby"));
                if (labelledBy) return labelledBy;
                const host = node.closest?.("[data-oj-field], oj-select-single, oj-c-select-single, oj-input-text, oj-c-input-text, oj-input-number, oj-c-input-number, oj-input-date, oj-c-input-date, oj-text-area, oj-c-text-area");
                const hostLabelledBy = byIdsText(host?.getAttribute?.("labelled-by") || host?.getAttribute?.("aria-labelledby"));
                if (hostLabelledBy) return hostLabelledBy;
                const label = node.closest?.("label, oj-label") || host?.querySelector?.("label, oj-label");
                if (label) {
                    const labelText = normalize(label.innerText || label.textContent);
                    if (labelText) return labelText;
                }
                const previous = node.previousElementSibling;
                if (previous) {
                    const previousText = normalize(previous.innerText || previous.textContent, 120);
                    if (previousText && previousText.length <= 120) return previousText;
                }
                return "";
            };
            const controlValue = (node) => {
                if (!node) return "";
                const host = node.closest?.("[data-oj-field], oj-select-single, oj-c-select-single, oj-input-text, oj-c-input-text, oj-input-number, oj-c-input-number, oj-input-date, oj-c-input-date, oj-text-area, oj-c-text-area");
                const directValue = normalize(("value" in node ? node.value : "") || node.getAttribute?.("value"));
                if (directValue) return directValue;
                const ariaValueText = normalize(node.getAttribute?.("aria-valuetext"));
                if (ariaValueText) return ariaValueText;
                const ariaChecked = normalize(node.getAttribute?.("aria-checked"));
                if (ariaChecked) return ariaChecked;
                const readonlyValue = normalize(
                    host?.querySelector?.(".oj-text-field-readonly")?.innerText
                    || host?.querySelector?.(".oj-searchselect-filter-text-field")?.value
                    || host?.querySelector?.(".oj-searchselect-filter-text-field")?.innerText
                );
                if (readonlyValue) return readonlyValue;
                const text = normalize(node.innerText || node.textContent);
                if (text) return text;
                const title = normalize(node.getAttribute?.("title"));
                if (title) return title;
                return normalize(host?.innerText || host?.textContent);
            };

            const labelValues = [];
            const labelSeen = new Set();
            const controlSelector = [
                "input",
                "textarea",
                "select",
                "[role='textbox']",
                "[role='combobox']",
                "[role='spinbutton']",
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
                "[data-oj-field]"
            ].join(",");

            Array.from(document.querySelectorAll(controlSelector))
                .filter(isVisible)
                .slice(0, 180)
                .forEach((node) => {
                    const label = nearestLabelText(node);
                    const value = controlValue(node);
                    if (!label || !value) return;
                    const key = `${label}||${value}`;
                    if (labelSeen.has(key)) return;
                    labelSeen.add(key);
                    labelValues.push({
                        label,
                        value,
                        tag: normalize(node.tagName).toLowerCase(),
                        role: normalize(node.getAttribute?.("role")).toLowerCase(),
                        id: normalize(node.id),
                        title: normalize(node.getAttribute?.("title")),
                        aria_label: normalize(node.getAttribute?.("aria-label")),
                        data_oj_field: normalize(node.getAttribute?.("data-oj-field") || node.closest?.("[data-oj-field]")?.getAttribute?.("data-oj-field")),
                    });
                });

            const textCandidates = [];
            const textSeen = new Set();
            Array.from(document.querySelectorAll("[role='heading'],h1,h2,h3,a,button,[title],[aria-label],li,td,th"))
                .filter(isVisible)
                .slice(0, 160)
                .forEach((node) => {
                    const text = normalize(node.innerText || node.textContent);
                    const title = normalize(node.getAttribute?.("title"));
                    const ariaLabel = normalize(node.getAttribute?.("aria-label"));
                    const combined = text || title || ariaLabel;
                    if (!combined) return;
                    const key = `${normalize(node.tagName)}|${normalize(node.id)}|${combined}`;
                    if (textSeen.has(key)) return;
                    textSeen.add(key);
                    textCandidates.push({
                        text,
                        title,
                        aria_label: ariaLabel,
                        tag: normalize(node.tagName).toLowerCase(),
                        role: normalize(node.getAttribute?.("role")).toLowerCase(),
                        id: normalize(node.id),
                    });
                });

            const dialogs = [];
            Array.from(document.querySelectorAll("[role='dialog'], .oj-dialog, .oj-popup"))
                .filter(isVisible)
                .slice(0, 5)
                .forEach((node, index) => {
                    const titleNode = node.querySelector("[role='heading'], h1, h2, h3, .oj-dialog-title");
                    dialogs.push({
                        index,
                        title: normalize(titleNode?.innerText || titleNode?.textContent, 160),
                        text: normalize(node.innerText || node.textContent, 1200),
                    });
                });

            return {
                label_values: labelValues,
                text_candidates: textCandidates,
                dialogs,
            };
        }""",
    )

    if not isinstance(snapshot, dict):
        return {
            "label_values": [],
            "text_candidates": [],
            "dialogs": [],
        }
    return {
        "label_values": snapshot.get("label_values") if isinstance(snapshot.get("label_values"), list) else [],
        "text_candidates": snapshot.get("text_candidates") if isinstance(snapshot.get("text_candidates"), list) else [],
        "dialogs": snapshot.get("dialogs") if isinstance(snapshot.get("dialogs"), list) else [],
    }


def _ptr_semantic_snapshot_has_content(snapshot: dict[str, Any] | None) -> bool:
    if not isinstance(snapshot, dict):
        return False
    return any(bool(snapshot.get(key)) for key in ("label_values", "text_candidates", "dialogs"))


def _ptr_runtime_debug_settings() -> dict[str, Any]:
    return {
        "runner_version": _PTR_RUNNER_VERSION,
        "after_action_wait_ms": _ptr_wait_ms("PTR_AFTER_ACTION_WAIT_MS", _PTR_HARDCODED_AFTER_ACTION_WAIT_MS),
        "capture_steps": bool(_PTR_STEP_ARTIFACTS_DIR),
        "record_video": bool(str(os.getenv("PTR_VIDEO_DIR", "")).strip()),
        "step_screenshot_full_page": _ptr_env_flag("PTR_STEP_SCREENSHOT_FULL_PAGE", "false"),
        "page_text_snapshot_max_chars": _ptr_wait_ms("PTR_PAGE_TEXT_SNAPSHOT_MAX_CHARS", 12_000),
        "debug_trace": _ptr_env_flag("PTR_DEBUG_TRACE", "false"),
        "experience_enabled": _ptr_env_flag("PTR_EXPERIENCE_ENABLED", "true"),
        "ai_self_repair_enabled": _ptr_ai_self_repair_enabled(),
        "action_timeout_ms": _ptr_wait_ms("PTR_ACTION_TIMEOUT_MS", 3000),
        "text_entry_timeout_ms": _ptr_wait_ms("PTR_TEXT_ENTRY_TIMEOUT_MS", 3000),
        "text_click_timeout_ms": _ptr_wait_ms("PTR_TEXT_CLICK_TIMEOUT_MS", 3000),
        "textbox_change_processing_wait_ms": _ptr_wait_ms("PTR_TEXTBOX_CHANGE_PROCESSING_WAIT_MS", 500),
        "ai_extract_pre_capture_wait_ms": _ptr_wait_ms("PTR_AI_EXTRACT_PRE_CAPTURE_WAIT_MS", 1200),
    }


def _ptr_runtime_snapshot_summary(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    current = snapshot if isinstance(snapshot, dict) else {}
    oracle_tables = current.get("oracle_tables")
    oracle_tables = oracle_tables if isinstance(oracle_tables, list) else []
    page_semantics = current.get("page_semantics")
    page_semantics = page_semantics if isinstance(page_semantics, dict) else {}
    return {
        "captured_at_epoch_ms": int(time.time() * 1000),
        "page_text_length": len(str(current.get("page_text") or "")),
        "oracle_table_count": len(oracle_tables),
        "oracle_table_row_count": sum(
            len(table.get("rows") or [])
            for table in oracle_tables
            if isinstance(table, dict)
        ),
        "label_value_count": len(page_semantics.get("label_values") or []),
        "text_candidate_count": len(page_semantics.get("text_candidates") or []),
        "dialog_count": len(page_semantics.get("dialogs") or []),
        "step_artifact_count": len(_PTR_STEP_ARTIFACTS),
        "action_log_count": len(_PTR_ACTION_LOG),
    }


def _ptr_persist_diagnostics_snapshot(snapshot: dict[str, Any] | None = None) -> None:
    if not _PTR_DIAGNOSTICS_PATH:
        return
    current = snapshot if isinstance(snapshot, dict) else dict(_PTR_LAST_PAGE_SNAPSHOT)
    payload = {
        "page_url": str(current.get("page_url") or "").strip(),
        "page_title": str(current.get("page_title") or "").strip(),
        "page_text": str(current.get("page_text") or "").strip(),
        "oracle_tables": current.get("oracle_tables") or [],
        "page_semantics": current.get("page_semantics") or {},
        "failure_screenshot_path": _PTR_FAILURE_SCREENSHOT_PATH or None,
        "step_artifacts": _PTR_STEP_ARTIFACTS,
        "action_log": _PTR_ACTION_LOG,
        "runtime_debug": {
            "settings": _ptr_runtime_debug_settings(),
            "snapshot_summary": _ptr_runtime_snapshot_summary(current),
            "last_strategy_state": _ptr_clone_json_value(_PTR_CURRENT_STRATEGY),
        },
    }
    try:
        Path(_PTR_DIAGNOSTICS_PATH).write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        return


def _ptr_capture_live_snapshot_before_close(page: Page | None) -> None:
    current_page = page or _PTR_LAST_PAGE
    if current_page is None:
        return
    try:
        wait_ms = _ptr_wait_ms("PTR_FLOW_CONTEXT_PRE_CLOSE_WAIT_MS", 0)
        if wait_ms > 0:
            current_page.wait_for_timeout(wait_ms)
    except Exception:
        pass
    try:
        snapshot = _ptr_capture_page_snapshot(current_page)
        _ptr_persist_diagnostics_snapshot(snapshot)
    except Exception:
        return


def _ptr_context_pages(context: BrowserContext) -> list[Page]:
    try:
        pages = getattr(context, "pages", None)
        if callable(pages):
            items = pages()
        else:
            items = pages
        return list(items or [])
    except Exception:
        return []


def _ptr_browser_contexts(browser: Browser) -> list[BrowserContext]:
    try:
        contexts = getattr(browser, "contexts", None)
        if callable(contexts):
            items = contexts()
        else:
            items = contexts
        return list(items or [])
    except Exception:
        return []


def _ptr_order_pages_for_snapshot(pages: list[Page]) -> list[Page]:
    ordered: list[Page] = []
    last_page = _PTR_LAST_PAGE
    trailing_page: Page | None = None
    for page in pages:
        if page is last_page:
            trailing_page = page
            continue
        ordered.append(page)
    if trailing_page is not None:
        ordered.append(trailing_page)
    return ordered


def _ptr_capture_page_snapshot(page: Page | None) -> dict[str, Any]:
    global _PTR_LAST_PAGE_SNAPSHOT
    if page is None:
        return _PTR_LAST_PAGE_SNAPSHOT

    snapshot = dict(_PTR_LAST_PAGE_SNAPSHOT)
    try:
        page_url = str(page.url or "").strip()
        if page_url:
            snapshot["page_url"] = page_url
    except Exception:
        snapshot.setdefault("page_url", "")
    try:
        page_title = str(page.title() or "").strip()
        if page_title:
            snapshot["page_title"] = page_title
    except Exception:
        snapshot.setdefault("page_title", "")

    body_text = _ptr_safe_page_eval(
        page,
        r"""() => {
            const body = document?.body;
            if (!body) return "";
            return String(body.innerText || body.textContent || "").replace(/\s+/g, " ").trim();
        }""",
    )
    text = str(body_text or "").strip()
    max_chars = max(0, _ptr_int_env("PTR_PAGE_TEXT_SNAPSHOT_MAX_CHARS", 12000))
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip()
    if text:
        snapshot["page_text"] = text
    elif "page_text" not in snapshot:
        snapshot["page_text"] = ""

    tables = _ptr_capture_oracle_table_snapshots(page)
    if tables:
        snapshot["oracle_tables"] = tables
    elif "oracle_tables" not in snapshot:
        snapshot["oracle_tables"] = []

    semantics = _ptr_capture_semantic_snapshot(page)
    if _ptr_semantic_snapshot_has_content(semantics):
        snapshot["page_semantics"] = semantics
    elif "page_semantics" not in snapshot:
        snapshot["page_semantics"] = {
            "label_values": [],
            "text_candidates": [],
            "dialogs": [],
        }

    _PTR_LAST_PAGE_SNAPSHOT = snapshot
    _ptr_persist_diagnostics_snapshot(snapshot)
    return snapshot


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


def _ptr_safe_page_eval(page: Page | None, expression: str, arg: Any | None = None) -> Any:
    if page is None:
        return None
    try:
        if arg is None:
            return page.evaluate(expression)
        return page.evaluate(expression, arg)
    except Exception:
        return None


def _ptr_locator_visible(locator: Locator, timeout_ms: int | None = None) -> bool:
    timeout = max(50, int(timeout_ms or _ptr_int_env("PTR_LOCATOR_SNAPSHOT_TIMEOUT_MS", 250)))
    try:
        locator.wait_for(state="visible", timeout=timeout)
        return True
    except Exception:
        return False


def _ptr_locator_value(locator: Locator) -> str:
    try:
        handle = _ptr_locator_element_handle(locator)
        if handle is None:
            return ""
        value = handle.evaluate(
            r"""(node) => {
                if (!node) return "";
                const readValue = (candidate) => {
                    if (!candidate) return "";
                    if ("value" in candidate) {
                        const direct = String(candidate.value || "").trim();
                        if (direct) return direct;
                    }
                    const attrCandidates = [
                        candidate.getAttribute?.("value"),
                        candidate.getAttribute?.("aria-valuetext"),
                        candidate.getAttribute?.("aria-valuenow"),
                    ];
                    for (const raw of attrCandidates) {
                        const text = String(raw || "").trim();
                        if (text) return text;
                    }
                    return "";
                };
                const direct = readValue(node);
                if (direct) return direct;
                const nested = node.querySelector?.("[role='spinbutton'], input, textarea, select");
                return readValue(nested);
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


def _ptr_oracle_notification_badge_signature(value: Any) -> str:
    normalized = _ptr_normalize_text(value)
    match = re.fullmatch(r"notifications\s*\(\d+\s+unread\)", normalized)
    if not match:
        return ""
    return "notifications (unread)"


def _ptr_rank_ai_dom_candidates(helper: str, label: str, candidates: list[dict[str, Any]], max_candidates: int) -> list[dict[str, Any]]:
    normalized_label = _ptr_normalize_text(label)
    normalized_helper = _ptr_normalize_text(helper)
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
        text_length = len(_ptr_normalize_text(candidate.get("text")))
        if "menu" in normalized_helper:
            if role == "menuitem":
                score += 45
            if role in {"button", "link", "option"}:
                score += 18
            if tag in {"a", "button"}:
                score += 12
            if tag in {"select", "input", "textarea"}:
                score -= 40
            if "oj-popup" in html or "role=\"menu\"" in html or "role='menu'" in html:
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
            or normalized_helper == "select_option"
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
                checked: typeof node?.checked === "boolean" ? (node.checked ? "true" : "false") : "",
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
    timeout = _ptr_resolve_wait_override_ms(timeout_ms, "PTR_ACTION_TIMEOUT_MS", 3000)
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
    timeout = _ptr_resolve_wait_override_ms(timeout_ms, "PTR_ACTION_TIMEOUT_MS", 3000)
    locator.wait_for(state="visible", timeout=timeout)
    try:
        locator.scroll_into_view_if_needed(timeout=min(timeout, 1000))
    except Exception:
        pass
    locator.click(timeout=timeout)


def _ptr_strict_dblclick(locator: Locator, timeout_ms: int | None = None) -> None:
    timeout = _ptr_resolve_wait_override_ms(timeout_ms, "PTR_ACTION_TIMEOUT_MS", 3000)
    locator.wait_for(state="visible", timeout=timeout)
    try:
        locator.scroll_into_view_if_needed(timeout=min(timeout, 1000))
    except Exception:
        pass
    locator.dblclick(timeout=timeout)


def _ptr_strict_fill(locator: Locator, value: str, timeout_ms: int | None = None) -> None:
    timeout = _ptr_resolve_wait_override_ms(timeout_ms, "PTR_ACTION_TIMEOUT_MS", 3000)
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
    timeout = _ptr_resolve_wait_override_ms(timeout_ms, "PTR_TEXT_ENTRY_TIMEOUT_MS", 3000)
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
                aria_checked: text(node?.getAttribute?.("aria-checked")),
                checked: typeof node?.checked === "boolean" ? (node.checked ? "true" : "false") : "",
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


def _ptr_active_element_matches_target(observation: dict[str, Any]) -> bool:
    active = observation.get("active_element") if isinstance(observation, dict) else {}
    target = observation.get("target_meta") if isinstance(observation, dict) else {}
    active = active if isinstance(active, dict) else {}
    target = target if isinstance(target, dict) else {}
    if not active or not target:
        return False

    active_tag = _ptr_normalize_text(active.get("tag"))
    target_tag = _ptr_normalize_text(target.get("tag"))
    tags_compatible = not active_tag or not target_tag or active_tag == target_tag

    active_id = _ptr_normalize_text(active.get("id"))
    target_id = _ptr_normalize_text(target.get("id"))
    if active_id and target_id and active_id == target_id:
        return True

    if not tags_compatible:
        return False

    for active_key, target_key in (
        ("name", "name"),
        ("aria_label", "aria_label"),
        ("title", "title"),
    ):
        active_value = _ptr_normalize_text(active.get(active_key))
        target_value = _ptr_normalize_text(target.get(target_key))
        if active_value and target_value and active_value == target_value:
            return True

    return False


def _ptr_table_field_click_postcondition(before: dict[str, Any], after: dict[str, Any]) -> bool:
    if _ptr_generic_click_postcondition(before, after):
        return True
    return _ptr_active_element_matches_target(after)


def _ptr_table_row_postcondition(before: dict[str, Any], after: dict[str, Any]) -> bool:
    before_meta = before.get("target_meta") if isinstance(before.get("target_meta"), dict) else {}
    after_meta = after.get("target_meta") if isinstance(after.get("target_meta"), dict) else {}
    before_selected = _ptr_normalize_text(before_meta.get("aria_selected"))
    after_selected = _ptr_normalize_text(after_meta.get("aria_selected"))
    if after_selected == "true" and before_selected != after_selected:
        return True

    before_classes = _ptr_normalize_text(before_meta.get("class_name"))
    after_classes = _ptr_normalize_text(after_meta.get("class_name"))
    if before_classes != after_classes and "selected" in after_classes:
        return True

    return _ptr_generic_click_postcondition(before, after)


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
        return not normalized_observed
    if not normalized_observed:
        return False
    if normalized_expected == normalized_observed:
        return True
    return normalized_expected in normalized_observed or normalized_observed in normalized_expected


def _ptr_recorded_locator_roles(script_data: dict[str, Any] | None = None) -> list[str]:
    current = script_data if isinstance(script_data, dict) else _ptr_current_script_data()
    steps: list[dict[str, Any]] = []
    for key in ("primary_locator_steps", "secondary_locator_steps"):
        value = current.get(key)
        if isinstance(value, list):
            steps.extend(item for item in value if isinstance(item, dict))
    parsed_action = current.get("parsed_action")
    if isinstance(parsed_action, dict):
        locator_steps = parsed_action.get("locator_steps")
        if isinstance(locator_steps, list):
            steps.extend(item for item in locator_steps if isinstance(item, dict))

    roles: list[str] = []
    for step in steps:
        if _ptr_normalize_text(step.get("method")) != "get_by_role":
            continue
        args = step.get("args") or []
        role = _ptr_normalize_text(args[0] if args else "")
        if role and role not in roles:
            roles.append(role)
    return roles


def _ptr_recorded_locator_is_table_scoped(script_data: dict[str, Any] | None = None) -> bool:
    return any(role in {"row", "table", "cell", "gridcell"} for role in _ptr_recorded_locator_roles(script_data))


def _ptr_recorded_locator_is_spinbutton(script_data: dict[str, Any] | None = None) -> bool:
    return "spinbutton" in _ptr_recorded_locator_roles(script_data)


_PTR_ORACLE_TABLE_EDITOR_ID_PATTERNS = (
    re.compile(r"(?P<table_id>.*?:at\d+:_ATp:ta\d+):\d+:i\d+(?:::[A-Za-z0-9_-]+)?$", re.IGNORECASE),
    re.compile(r"(?P<table_id>.*?:_ATp:ta\d+):\d+:i\d+(?:::[A-Za-z0-9_-]+)?$", re.IGNORECASE),
)


def _ptr_oracle_table_editor_table_id(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        for pattern in _PTR_ORACLE_TABLE_EDITOR_ID_PATTERNS:
            match = pattern.search(text)
            if match:
                return str(match.group("table_id") or "").strip()
    return ""


def _ptr_active_oracle_table_editor(page: Page | None) -> dict[str, str]:
    result = _ptr_safe_page_eval(
        page,
        r"""() => {
            const node = document.activeElement;
            const text = (value) => String(value || "").replace(/\s+/g, " ").trim();
            const table = node?.closest?.(".oj-table-scroller, table.oj-table-element, [role='grid'], [role='table'], [id*=':_ATp:ta']");
            if (!node) return {};
            const row = node?.closest?.("[role='row'], tr, .oj-table-body-row");
            return {
                tag: String(node?.tagName || "").toLowerCase(),
                role: text(node?.getAttribute?.("role")),
                id: text(node?.id),
                name: text(node?.getAttribute?.("name")),
                aria_label: text(node?.getAttribute?.("aria-label")),
                title: text(node?.getAttribute?.("title")),
                value: text(("value" in node ? node.value : "") || node?.getAttribute?.("value")),
                row_text: text(row?.innerText || row?.textContent).slice(0, 400),
                table_id: text(table?.id),
            };
        }""",
    )
    active_info = result if isinstance(result, dict) else {}
    if not active_info:
        return {}

    inferred_table_id = _ptr_oracle_table_editor_table_id(
        active_info.get("id"),
        active_info.get("name"),
    )
    if inferred_table_id and not str(active_info.get("table_id") or "").strip():
        active_info["table_id"] = inferred_table_id

    if not _ptr_normalize_text(active_info.get("row_text")) and not _ptr_normalize_text(active_info.get("table_id")):
        return {}
    return active_info


def _ptr_active_spinbutton_locator(page: Page | None) -> tuple[dict[str, str], Locator | None]:
    active_info = _ptr_safe_page_eval(
        page,
        r"""() => {
            const node = document.activeElement;
            const text = (value) => String(value || "").replace(/\s+/g, " ").trim();
            if (!node) return {};
            return {
                tag: String(node?.tagName || "").toLowerCase(),
                role: text(node?.getAttribute?.("role")),
                id: text(node?.id),
                name: text(node?.getAttribute?.("name")),
                aria_label: text(node?.getAttribute?.("aria-label")),
                title: text(node?.getAttribute?.("title")),
                value: text(("value" in node ? node.value : "") || node?.getAttribute?.("value")),
                aria_valuenow: text(node?.getAttribute?.("aria-valuenow")),
                aria_valuetext: text(node?.getAttribute?.("aria-valuetext")),
            };
        }""",
    )
    active_info = active_info if isinstance(active_info, dict) else {}
    if page is None or not active_info:
        return active_info, None

    role = _ptr_normalize_text(active_info.get("role"))
    if role != "spinbutton":
        return active_info, None

    locator = None
    active_id = str(active_info.get("id") or "").strip()
    if active_id:
        escaped_id = active_id.replace("\\", "\\\\").replace('"', '\\"')
        try:
            locator = page.locator(f'[id="{escaped_id}"]').first
        except Exception:
            try:
                locator = page.locator(f'[id="{escaped_id}"]')
            except Exception:
                locator = None

    if locator is None:
        active_name = str(active_info.get("name") or "").strip()
        if active_name:
            escaped_name = active_name.replace("\\", "\\\\").replace('"', '\\"')
            try:
                locator = page.locator(f'[name="{escaped_name}"]').first
            except Exception:
                try:
                    locator = page.locator(f'[name="{escaped_name}"]')
                except Exception:
                    locator = None

    return active_info, locator


def _ptr_active_oracle_table_editor_locator(page: Page | None) -> tuple[dict[str, str], Locator | None]:
    active_info = _ptr_active_oracle_table_editor(page)
    if page is None or not active_info:
        return active_info, None

    tag = _ptr_normalize_text(active_info.get("tag"))
    role = _ptr_normalize_text(active_info.get("role"))
    if tag not in {"input", "textarea"} and role not in {"textbox", "spinbutton"}:
        return active_info, None

    locator = None
    active_id = str(active_info.get("id") or "").strip()
    if active_id:
        escaped_id = active_id.replace("\\", "\\\\").replace('"', '\\"')
        try:
            locator = page.locator(f'[id="{escaped_id}"]').first
        except Exception:
            try:
                locator = page.locator(f'[id="{escaped_id}"]')
            except Exception:
                locator = None
    if locator is None:
        active_name = str(active_info.get("name") or "").strip()
        if active_name:
            escaped_name = active_name.replace("\\", "\\\\").replace('"', '\\"')
            try:
                locator = page.locator(f'[name="{escaped_name}"]').first
            except Exception:
                try:
                    locator = page.locator(f'[name="{escaped_name}"]')
                except Exception:
                    locator = None
    return active_info, locator


def _ptr_fill_locator_via_keyboard(locator: Locator, value: str) -> None:
    timeout = _ptr_wait_ms("PTR_TEXT_ENTRY_TIMEOUT_MS", 3000)
    locator.wait_for(state="visible", timeout=timeout)
    try:
        locator.scroll_into_view_if_needed(timeout=min(timeout, 1000))
    except Exception:
        pass
    try:
        locator.click(timeout=min(timeout, 1000))
    except Exception:
        pass
    try:
        locator.press("ControlOrMeta+A", timeout=timeout)
    except Exception:
        pass
    try:
        locator.press("Backspace", timeout=timeout)
    except Exception:
        pass

    entry_delay = max(0, min(200, _ptr_int_env("PTR_TEXT_ENTRY_KEY_DELAY_MS", 40)))
    try:
        locator.press_sequentially(str(value), delay=entry_delay, timeout=timeout)
        return
    except Exception:
        pass
    try:
        locator.type(str(value), delay=entry_delay, timeout=timeout)
        return
    except Exception:
        pass
    locator.fill(str(value), timeout=timeout)


def _ptr_oracle_table_fill_postcondition(locator: Locator, active_locator: Locator | None, value: str) -> bool:
    candidates = [active_locator, locator]
    for candidate in candidates:
        if candidate is None:
            continue
        observed = _ptr_locator_value(candidate) or _ptr_locator_text(candidate)
        if _ptr_value_matches(value, observed):
            return True
    return False


def _ptr_oracle_spinbutton_fill_postcondition(
    current_page: Page | None,
    locator: Locator,
    active_locator: Locator | None,
    label: str,
    value: str,
) -> bool:
    candidates = [active_locator, locator]
    for candidate in candidates:
        if candidate is None:
            continue
        observed = _ptr_locator_value(candidate) or _ptr_locator_text(candidate)
        if _ptr_value_matches(value, observed):
            return True
    return _ptr_oracle_label_value_matches(current_page, label, value)


def _ptr_try_oracle_spinbutton_fill(
    current_page: Page,
    locator: Locator,
    label: str,
    value: str,
) -> dict[str, Any] | None:
    if not _ptr_recorded_locator_is_spinbutton():
        return None

    active_info, active_locator = _ptr_active_spinbutton_locator(current_page)
    target_locator = active_locator or locator

    strategy_name = "oracle_spinbutton_keyboard_fill"
    _ptr_record_strategy_attempt(strategy_name)
    _ptr_fill_locator_via_keyboard(target_locator, value)
    try:
        target_locator.press("Tab", timeout=_ptr_wait_ms("PTR_TEXT_ENTRY_TIMEOUT_MS", 3000))
    except Exception:
        pass
    _ptr_wait_for_field_processing(
        current_page,
        env_name="PTR_TEXTBOX_CHANGE_PROCESSING_WAIT_MS",
        default_ms=500,
    )
    if _ptr_oracle_spinbutton_fill_postcondition(current_page, locator, active_locator, label, value):
        return {
            "strategy_name": strategy_name,
            "active_element_id": str(active_info.get("id") or "").strip(),
            "used_keyboard_entry": True,
        }
    raise RuntimeError(f'Oracle spinbutton "{label}" did not reflect the requested value.')


def _ptr_try_oracle_table_active_editor_fill(
    current_page: Page,
    locator: Locator,
    label: str,
    value: str,
) -> dict[str, Any] | None:
    if not _ptr_recorded_locator_is_table_scoped():
        return None

    active_info, active_locator = _ptr_active_oracle_table_editor_locator(current_page)
    if active_locator is None:
        return None

    if _ptr_oracle_table_fill_postcondition(locator, active_locator, value):
        strategy_name = "oracle_table_active_editor_reflects_value"
        _ptr_record_strategy_attempt(strategy_name)
        return {
            "strategy_name": strategy_name,
            "active_element_id": str(active_info.get("id") or "").strip(),
            "table_id": str(active_info.get("table_id") or "").strip(),
            "row_text": str(active_info.get("row_text") or "").strip(),
            "used_keyboard_entry": False,
        }

    strategy_name = "oracle_table_active_editor_fill"
    _ptr_record_strategy_attempt(strategy_name)
    _ptr_fill_locator_via_keyboard(active_locator, value)
    _ptr_wait_for_field_processing(
        current_page,
        env_name="PTR_TEXTBOX_CHANGE_PROCESSING_WAIT_MS",
        default_ms=500,
    )
    if _ptr_oracle_table_fill_postcondition(locator, active_locator, value):
        return {
            "strategy_name": strategy_name,
            "active_element_id": str(active_info.get("id") or "").strip(),
            "table_id": str(active_info.get("table_id") or "").strip(),
            "row_text": str(active_info.get("row_text") or "").strip(),
            "used_keyboard_entry": True,
        }
    raise RuntimeError(f'Oracle table editor for "{label}" did not reflect the requested value.')


def _ptr_page_visible_text(page: Page | None) -> str:
    result = _ptr_safe_page_eval(
        page,
        r"""() => {
            const body = document?.body;
            if (!body) return "";
            return String(body.innerText || body.textContent || "").replace(/\s+/g, " ").trim();
        }""",
    )
    return _ptr_normalize_text(result)


def _ptr_oracle_invoice_shows_not_validated(page: Page | None) -> bool:
    visible_text = _ptr_page_visible_text(page)
    return "not validated" in visible_text or "never validated" in visible_text


_PTR_COMPLETION_SPLIT_TRIGGERS = frozenset(
    {
        "complete and create another",
        "submit and create another",
    }
)


def _ptr_oracle_menu_option_is_completion_action(trigger_label: str) -> bool:
    return _ptr_normalize_text(trigger_label) in _PTR_COMPLETION_SPLIT_TRIGGERS


def _ptr_oracle_menu_trigger_requires_option_visibility(trigger_label: str) -> bool:
    return (
        _ptr_normalize_text(trigger_label) == "invoice actions"
        or _ptr_oracle_menu_option_is_completion_action(trigger_label)
    )


def _ptr_menu_panel_option_candidates(
    current_page: Page,
    option: Locator,
    option_name: str,
) -> list[tuple[str, Locator]]:
    return [
        ("raw_option", option),
        ("role_menuitem", current_page.get_by_role("menuitem", name=option_name, exact=True)),
        ("role_option", current_page.get_by_role("option", name=option_name, exact=True)),
        ("text_option", current_page.get_by_text(option_name, exact=True)),
    ]


def _ptr_oracle_visible_popup_option_candidates(
    current_page: Page,
    option_name: str,
) -> list[tuple[str, Locator]]:
    page_locator = getattr(current_page, "locator", None)
    if not callable(page_locator):
        return []

    candidates: list[tuple[str, Locator]] = []
    popup_selectors = [
        "[role='menu']:visible",
        ".oj-popup:visible",
    ]
    for selector in popup_selectors:
        try:
            popup_scope = current_page.locator(selector)
        except Exception:
            continue
        if popup_scope is None:
            continue

        get_by_role = getattr(popup_scope, "get_by_role", None)
        if callable(get_by_role):
            for role_name in ("menuitem", "link", "button", "option"):
                try:
                    candidates.append(
                        (
                            f"oracle_popup_{role_name}_{len(candidates)}",
                            popup_scope.get_by_role(role_name, name=option_name, exact=True),
                        )
                    )
                except Exception:
                    pass

        get_by_text = getattr(popup_scope, "get_by_text", None)
        if callable(get_by_text):
            try:
                candidates.append(
                    (
                        f"oracle_popup_text_{len(candidates)}",
                        popup_scope.get_by_text(option_name, exact=True),
                    )
                )
            except Exception:
                pass
    return candidates


def _ptr_wait_for_oracle_menu_trigger_option_visibility(
    page: Page | None,
    option_candidates: Sequence[tuple[str, Locator]],
    trigger_label: str,
) -> bool:
    if not _ptr_oracle_menu_trigger_requires_option_visibility(trigger_label):
        return True
    if page is None:
        return False

    def _condition() -> bool:
        for _, candidate in option_candidates:
            if _ptr_locator_is_actionable(candidate, timeout_ms=250):
                return True
        return False

    if _condition():
        return True

    timeout_ms = max(0, _ptr_wait_ms("PTR_MENU_TRIGGER_OPTION_TIMEOUT_MS", 3000))
    if timeout_ms <= 0:
        return _condition()

    poll_ms = max(100, _ptr_wait_ms("PTR_MENU_TRIGGER_OPTION_POLL_MS", 250))
    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        page.wait_for_timeout(poll_ms)
        if _condition():
            return True
    return _condition()


def _ptr_oracle_invoice_accounting_ready(page: Page | None) -> bool:
    result = _ptr_safe_page_eval(
        page,
        r"""() => {
            const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
            const isVisible = (node) => {
                if (!node) return false;
                const style = window.getComputedStyle(node);
                if (!style) return false;
                if (style.display === "none" || style.visibility === "hidden") return false;
                const rect = node.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const hasExactInteractiveText = (selector, expected) => {
                for (const node of document.querySelectorAll(selector)) {
                    if (!isVisible(node)) continue;
                    if (normalize(node.innerText || node.textContent) === expected) return true;
                }
                return false;
            };
            return {
                has_view_accounting: hasExactInteractiveText('button, [role="button"], a, [role="link"]', "view accounting"),
                has_accounting_link: hasExactInteractiveText('a, [role="link"]', "accounting"),
            };
        }""",
    )
    if isinstance(result, dict):
        return bool(result.get("has_view_accounting") or result.get("has_accounting_link"))
    return False


def _ptr_wait_for_menu_option_semantic_condition(
    page: Page | None,
    predicate,
    *,
    timeout_env_name: str,
    default_timeout_ms: int,
) -> bool:
    if page is None:
        return False

    def _condition() -> bool:
        try:
            return bool(predicate())
        except Exception:
            return False

    if _condition():
        return True

    timeout_ms = max(0, _ptr_wait_ms(timeout_env_name, default_timeout_ms))
    if timeout_ms <= 0:
        return _condition()

    poll_ms = max(100, _ptr_wait_ms("PTR_MENU_OPTION_SEMANTIC_POLL_MS", 250))
    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        page.wait_for_timeout(poll_ms)
        if _condition():
            return True
    return _condition()


def _ptr_oracle_transaction_completion_advanced(
    page: Page | None,
    baseline: dict[str, Any] | None,
) -> bool:
    if page is None:
        return False
    baseline = baseline if isinstance(baseline, dict) else {}
    try:
        current_url = str(getattr(page, "url", "") or "").strip()
    except Exception:
        current_url = ""
    try:
        current_title = str(page.title() or "").strip()
    except Exception:
        current_title = ""

    baseline_url = str(baseline.get("url") or "").strip()
    baseline_title = str(baseline.get("title") or "").strip()

    baseline_step = str(baseline.get("guided_step") or "").strip()
    current_step = str(_ptr_current_guided_step(page) or "").strip()

    baseline_guided_flow = baseline.get("guided_flow") if isinstance(baseline.get("guided_flow"), dict) else {}
    baseline_heading = _ptr_normalize_text(baseline_guided_flow.get("primary_heading"))
    current_guided_flow = _ptr_guided_flow_state(page)
    current_heading = _ptr_normalize_text((current_guided_flow or {}).get("primary_heading"))

    baseline_body = _ptr_normalize_text(baseline.get("body_marker"))
    current_body = _ptr_normalize_text(_ptr_body_marker(page))
    signals = {
        "url_changed": bool(baseline_url and current_url and current_url != baseline_url),
        "title_changed": bool(baseline_title and current_title and current_title != baseline_title),
        "guided_step_changed": bool(baseline_step and current_step and current_step != baseline_step),
        "heading_changed_to_review": bool(
            baseline_heading
            and current_heading
            and current_heading != baseline_heading
            and (current_heading.startswith("review transaction") or current_heading.startswith("view transaction"))
        ),
        "body_contains_review_transaction": bool(
            baseline_body
            and current_body
            and current_body != baseline_body
            and "review transaction" in current_body
            and "review transaction" not in baseline_body
        ),
        "status_incomplete_to_complete": bool(
            baseline_body
            and current_body
            and current_body != baseline_body
            and "status complete" in current_body
            and "status incomplete" in baseline_body
        ),
    }
    matched_signal = next((name for name, matched in signals.items() if matched), "")
    _ptr_set_debug_detail(
        "oracle_completion_check",
        {
            "baseline": {
                "url": baseline_url,
                "title": baseline_title,
                "guided_step": baseline_step,
                "primary_heading": baseline_heading,
                "body_marker": _ptr_trim_debug_text(baseline_body),
            },
            "current": {
                "url": current_url,
                "title": current_title,
                "guided_step": current_step,
                "primary_heading": current_heading,
                "body_marker": _ptr_trim_debug_text(current_body),
            },
            "signals": signals,
            "matched_signal": matched_signal,
            "postcondition_passed": bool(matched_signal),
        },
    )
    return bool(matched_signal)


def _ptr_oracle_menu_option_requires_semantic_validation(trigger_label: str, option_name: str) -> bool:
    normalized_trigger = _ptr_normalize_text(trigger_label)
    normalized_option = _ptr_normalize_text(option_name)
    if _ptr_oracle_menu_option_is_completion_action(trigger_label):
        return True
    if normalized_trigger != "invoice actions":
        return False
    return normalized_option in {"validate", "account in final"}


def _ptr_oracle_menu_option_semantic_postcondition(
    page: Page | None,
    trigger_label: str,
    option_name: str,
    *,
    before: dict[str, Any] | None = None,
) -> bool | None:
    if not _ptr_oracle_menu_option_requires_semantic_validation(trigger_label, option_name):
        return None

    if _ptr_oracle_menu_option_is_completion_action(trigger_label):
        return _ptr_wait_for_menu_option_semantic_condition(
            page,
            lambda: _ptr_oracle_transaction_completion_advanced(page, before),
            timeout_env_name="PTR_TRANSACTION_COMPLETE_POSTCONDITION_TIMEOUT_MS",
            default_timeout_ms=8000,
        )

    normalized_option = _ptr_normalize_text(option_name)
    if normalized_option == "validate":
        return _ptr_wait_for_menu_option_semantic_condition(
            page,
            lambda: not _ptr_oracle_invoice_shows_not_validated(page),
            timeout_env_name="PTR_INVOICE_VALIDATE_POSTCONDITION_TIMEOUT_MS",
            default_timeout_ms=20000,
        )
    if normalized_option == "account in final":
        return _ptr_wait_for_menu_option_semantic_condition(
            page,
            lambda: _ptr_oracle_invoice_accounting_ready(page),
            timeout_env_name="PTR_INVOICE_ACCOUNTING_POSTCONDITION_TIMEOUT_MS",
            default_timeout_ms=10000,
        )
    return None


def _ptr_menu_option_failure_message(trigger_label: str, option_name: str) -> str:
    if _ptr_oracle_menu_option_is_completion_action(trigger_label):
        return (
            f'"{option_name}" did not complete the transaction: the flow did not advance '
            f"(no navigation / guided-step change), so it stays in the editable draft."
        )
    if _ptr_oracle_menu_option_requires_semantic_validation(trigger_label, option_name):
        normalized_option = _ptr_normalize_text(option_name)
        if normalized_option == "validate":
            return 'Invoice Actions "Validate" did not clear the "Not validated" status.'
        if normalized_option == "account in final":
            return 'Invoice Actions "Account in Final" did not expose the Accounting action.'
    return f'Menu panel "{trigger_label}" did not apply option "{option_name}".'


def _ptr_menu_trigger_failure_message(trigger_label: str, option_name: str) -> str:
    if _ptr_oracle_menu_trigger_requires_option_visibility(trigger_label):
        return f'{trigger_label} did not expose menu option "{option_name}".'
    return f'Menu panel "{trigger_label}" did not expose option "{option_name}".'


def _ptr_checkbox_state(locator: Locator) -> str:
    metadata = _ptr_extract_locator_metadata(locator)
    if isinstance(metadata, dict):
        normalized_metadata_state = _ptr_checkbox_observed_state(metadata)
        if normalized_metadata_state in {"true", "false", "mixed"}:
            return normalized_metadata_state
    try:
        return "true" if bool(locator.is_checked()) else "false"
    except Exception:
        pass
    checked = _ptr_safe_locator_eval(
        locator,
        r"""(node) => {
            if (!node) return "";
            const ariaChecked = String(node.getAttribute?.("aria-checked") || "").trim().toLowerCase();
            if (ariaChecked === "true" || ariaChecked === "false" || ariaChecked === "mixed") return ariaChecked;
            if (typeof node.checked === "boolean") return node.checked ? "true" : "false";
            if (node.hasAttribute?.("checked")) return "true";
            return "";
        }""",
    )
    normalized_checked = _ptr_normalize_text(checked)
    if normalized_checked in {"true", "false", "mixed"}:
        return normalized_checked
    return ""


def _ptr_checkbox_observed_state(metadata: dict[str, Any] | None) -> str:
    metadata = metadata if isinstance(metadata, dict) else {}
    normalized_aria_checked = _ptr_normalize_text(metadata.get("aria_checked"))
    if normalized_aria_checked in {"true", "false", "mixed"}:
        return normalized_aria_checked
    normalized_checked = _ptr_normalize_text(metadata.get("checked"))
    if normalized_checked in {"true", "false"}:
        return normalized_checked
    return ""


def _ptr_checkbox_matches(locator: Locator, desired_checked: bool) -> bool:
    desired_state = "true" if desired_checked else "false"
    return _ptr_checkbox_state(locator) == desired_state


def _ptr_checkbox_semantic_postcondition(
    before: dict[str, Any],
    after: dict[str, Any],
    desired_checked: bool,
) -> bool:
    desired_state = "true" if desired_checked else "false"
    before_target = before.get("target_meta") if isinstance(before.get("target_meta"), dict) else {}
    after_target = after.get("target_meta") if isinstance(after.get("target_meta"), dict) else {}
    if _ptr_checkbox_observed_state(after_target) == desired_state:
        return True

    active = after.get("active_element") if isinstance(after.get("active_element"), dict) else {}
    if _ptr_checkbox_observed_state(active) != desired_state:
        return False

    if before_target and _ptr_active_element_matches_target({"active_element": active, "target_meta": before_target}):
        return True
    if after_target and _ptr_active_element_matches_target({"active_element": active, "target_meta": after_target}):
        return True
    return False


def _ptr_set_checkbox_state(locator: Locator, current_page: Page, label: str, desired_checked: bool) -> None:
    _ptr_register_page(current_page)
    desired_state = "true" if desired_checked else "false"
    if _ptr_checkbox_matches(locator, desired_checked):
        return

    timeout_ms = _ptr_wait_ms("PTR_ACTION_TIMEOUT_MS", 3000)
    last_error: Exception | None = None
    before_observation = _ptr_observe(current_page, locator)

    try:
        locator.wait_for(state="visible", timeout=timeout_ms)
        try:
            locator.scroll_into_view_if_needed(timeout=min(timeout_ms, 1000))
        except Exception:
            pass
        _ptr_record_strategy_attempt("raw_set_checked")
        if desired_checked:
            locator.check(timeout=timeout_ms)
        else:
            locator.uncheck(timeout=timeout_ms)
    except Exception as exc:
        last_error = exc

    _ptr_wait_for_field_processing(
        current_page,
        env_name="PTR_CHECKBOX_CHANGE_PROCESSING_WAIT_MS",
        default_ms=750,
    )
    if _ptr_checkbox_matches(locator, desired_checked):
        return
    after_raw_check = _ptr_observe(current_page, locator)
    if _ptr_checkbox_semantic_postcondition(before_observation, after_raw_check, desired_checked):
        return

    try:
        _ptr_record_strategy_attempt("checkbox_click_fallback")
        _ptr_strict_click(locator, timeout_ms=timeout_ms)
    except Exception as exc:
        last_error = exc

    _ptr_wait_for_field_processing(
        current_page,
        env_name="PTR_CHECKBOX_CHANGE_PROCESSING_WAIT_MS",
        default_ms=750,
    )
    if _ptr_checkbox_matches(locator, desired_checked):
        return
    after_click_fallback = _ptr_observe(current_page, locator)
    if _ptr_checkbox_semantic_postcondition(before_observation, after_click_fallback, desired_checked):
        return

    if last_error is not None:
        raise RuntimeError(
            f'Checkbox "{label}" did not become {desired_state} after raw set_checked and click fallback.'
        ) from last_error
    raise RuntimeError(f'Checkbox "{label}" did not become {desired_state}.')


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
    *,
    page: Page | None = None,
    trigger_label: str = "",
) -> bool:
    semantic_result = _ptr_oracle_menu_option_semantic_postcondition(
        page,
        trigger_label,
        option_name,
        before=before,
    )
    if semantic_result is not None:
        return semantic_result
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


def _ptr_combobox_trigger_reflects_option(trigger: Locator, option_name: str) -> bool:
    observed_candidates: list[str] = []

    def _record_observed(value: Any) -> None:
        normalized = str(value or "").strip()
        if normalized:
            observed_candidates.append(normalized)

    _record_observed(_ptr_locator_value(trigger))
    _record_observed(_ptr_locator_text(trigger))

    descendant_values = _ptr_safe_locator_eval(
        trigger,
        r"""(node) => {
            const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
            const values = [];
            const seen = new Set();
            const record = (value) => {
                const normalized = normalize(value);
                if (!normalized || seen.has(normalized)) return;
                seen.add(normalized);
                values.push(normalized);
            };

            if (!node) return values;

            record("value" in node ? node.value : "");
            record(node.innerText || node.textContent);

            const descendants = node.querySelectorAll?.("input, textarea, select") || [];
            for (const descendant of descendants) {
                record("value" in descendant ? descendant.value : "");
                record(descendant.getAttribute?.("value"));
                record(descendant.innerText || descendant.textContent);
                if (descendant.tagName?.toLowerCase() === "select" && descendant.selectedOptions?.length) {
                    for (const selected of descendant.selectedOptions) {
                        record(selected.innerText || selected.textContent);
                    }
                }
            }
            return values;
        }""",
    )
    if isinstance(descendant_values, list):
        for value in descendant_values:
            _record_observed(value)

    for observed in observed_candidates:
        if _ptr_value_matches(option_name, observed):
            return True
    metadata = _ptr_extract_locator_metadata(trigger)
    if not isinstance(metadata, dict):
        return False
    for key in ("text", "oracle_host_text", "title"):
        if _ptr_value_matches(option_name, str(metadata.get(key) or "")):
            return True
    return False


def _ptr_try_apply_combobox_option_candidate(
    trigger: Locator,
    option_locator: Locator,
    current_page: Page,
    label: str,
    option_name: str,
) -> Exception | None:
    retry_count = max(0, _ptr_int_env("PTR_COMBOBOX_VALUE_RETRY_COUNT", 1))
    last_error: Exception | None = None
    for attempt in range(retry_count + 1):
        try:
            if attempt > 0:
                _ptr_click_combobox(trigger, current_page, label)
                current_page.wait_for_timeout(_ptr_wait_ms("PTR_COMBOBOX_RETRY_WAIT_MS", 250))
            before = _ptr_observe(current_page, option_locator)
            _ptr_strict_click(option_locator)
            current_page.wait_for_timeout(_ptr_wait_ms("PTR_COMBOBOX_SELECT_WAIT_MS", 400))
            after = _ptr_observe(current_page, option_locator)
            if not _ptr_option_selection_postcondition(before, after, trigger, option_locator, option_name):
                last_error = RuntimeError(f'Combobox "{label}" did not reflect option "{option_name}".')
                continue
            _ptr_wait_for_field_processing(
                current_page,
                env_name="PTR_DROPDOWN_CHANGE_PROCESSING_WAIT_MS",
                default_ms=5000,
            )
            if _ptr_combobox_trigger_reflects_option(trigger, option_name):
                return None
            last_error = RuntimeError(
                f'Combobox "{label}" did not reflect option "{option_name}" after selection.'
            )
        except Exception as exc:
            last_error = exc
    return last_error or RuntimeError(f'Combobox "{label}" did not reflect option "{option_name}".')


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
        if "submit" in before_footer and "submit" not in after_footer:
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
    total_timeout = _ptr_resolve_wait_override_ms(timeout_ms, "PTR_POST_ACTION_STABILIZE_TIMEOUT_MS", 2500)
    stable_window = _ptr_resolve_wait_override_ms(quiet_ms, "PTR_POST_ACTION_STABILIZE_QUIET_MS", 600)
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


def _ptr_oracle_warning_dialog_state(page: Page | None) -> dict[str, Any]:
    default_state = {
        "dialog_count": 0,
        "warning_visible": False,
        "warning_title": "",
        "warning_text": "",
        "ok_button_visible": False,
        "any_ok_button_visible": False,
        "ok_button_id": "",
        "close_button_id": "",
    }
    result = _ptr_safe_page_eval(
        page,
        r"""() => {
            const selectors = ['[role="dialog"]', '[aria-modal="true"]', '.oj-dialog', '.oj-popup'];
            const normalize = (value, limit = 1200) => String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);
            const buttonLabel = (button) => normalize(
                button?.innerText
                || button?.textContent
                || button?.getAttribute?.("aria-label")
                || button?.getAttribute?.("title")
                || button?.getAttribute?.("value"),
                80,
            ).toLowerCase();
            const isVisible = (node) => {
                if (!node) return false;
                const style = window.getComputedStyle(node);
                if (!style) return false;
                if (style.display === "none" || style.visibility === "hidden") return false;
                const rect = node.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const isWarningLike = (title, text) => {
                const lowerTitle = String(title || "").toLowerCase();
                const lowerText = String(text || "").toLowerCase();
                return lowerTitle.includes("warning")
                    || lowerText.startsWith("warning ")
                    || lowerText.includes(" warning ")
                    || lowerText.includes("duplicate")
                    || lowerText.includes("payment terms");
            };
            const dialogEntry = (node) => {
                const titleNode = node.querySelector("[role='heading'], h1, h2, h3, .oj-dialog-title");
                const title = normalize(titleNode?.innerText || titleNode?.textContent, 160);
                const text = normalize(node.innerText || node.textContent, 1200);
                const buttons = Array.from(
                    node.querySelectorAll("button, [role='button'], input[type='button'], input[type='submit']")
                ).filter(isVisible);
                const buttonTexts = buttons.map((button) => buttonLabel(button)).filter(Boolean);
                const okButton = buttons.find((button) => buttonLabel(button) === "ok") || null;
                const closeButton = buttons.find((button) => {
                    const label = buttonLabel(button);
                    return label === "close" || label === "x" || label === "×";
                }) || null;
                return {
                    title,
                    text,
                    has_ok_button: buttonTexts.includes("ok"),
                    warning_like: isWarningLike(title, text),
                    ok_button_id: normalize(okButton?.id, 160),
                    close_button_id: normalize(closeButton?.id, 160),
                };
            };
            const seen = new Set();
            const dialogs = [];
            for (const selector of selectors) {
                for (const node of document.querySelectorAll(selector)) {
                    if (!isVisible(node) || seen.has(node)) continue;
                    seen.add(node);
                    dialogs.push(dialogEntry(node));
                }
            }

            for (const button of Array.from(document.querySelectorAll("button, [role='button'], input[type='button'], input[type='submit']"))) {
                if (!isVisible(button)) continue;
                const label = buttonLabel(button);
                if (!["ok", "close", "x", "×"].includes(label)) continue;
                let current = button.parentElement;
                let depth = 0;
                while (current && depth < 8) {
                    if (isVisible(current) && !seen.has(current)) {
                        const entry = dialogEntry(current);
                        if (entry.warning_like) {
                            seen.add(current);
                            dialogs.push(entry);
                            break;
                        }
                    }
                    current = current.parentElement;
                    depth += 1;
                }
            }
            const warningDialog = dialogs.find((entry) => entry.warning_like) || null;
            return {
                dialog_count: dialogs.length,
                warning_visible: Boolean(warningDialog),
                warning_title: warningDialog ? warningDialog.title : "",
                warning_text: warningDialog ? warningDialog.text : "",
                ok_button_visible: Boolean(warningDialog && warningDialog.has_ok_button),
                any_ok_button_visible: dialogs.some((entry) => entry.has_ok_button),
                ok_button_id: warningDialog ? String(warningDialog.ok_button_id || "") : "",
                close_button_id: warningDialog ? String(warningDialog.close_button_id || "") : "",
            };
        }""",
    )
    if not isinstance(result, dict):
        return default_state
    return {
        "dialog_count": int(result.get("dialog_count") or 0),
        "warning_visible": bool(result.get("warning_visible")),
        "warning_title": str(result.get("warning_title") or "").strip(),
        "warning_text": str(result.get("warning_text") or "").strip(),
        "ok_button_visible": bool(result.get("ok_button_visible")),
        "any_ok_button_visible": bool(result.get("any_ok_button_visible")),
        "ok_button_id": str(result.get("ok_button_id") or "").strip(),
        "close_button_id": str(result.get("close_button_id") or "").strip(),
    }


def _ptr_error_looks_like_missing_button(error: Any) -> bool:
    error_text = _ptr_normalize_text(error)
    if not error_text:
        return False
    if any(
        snippet in error_text
        for snippet in (
            "strict mode violation",
            "pointer events",
            "intercepts",
            "another element",
        )
    ):
        return False
    return (
        ("waiting for" in error_text and "to be visible" in error_text)
        or "not actionable" in error_text
        or "resolved to 0 elements" in error_text
        or "timeout" in error_text
    )


def _ptr_try_skip_optional_oracle_warning_ok(
    page: Page | None,
    locator: Locator | None,
    label: str,
    error: Any,
    observation: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if _ptr_normalize_text(label) != "ok":
        return None
    current_observation = observation or _ptr_observe(page, locator)
    try:
        url = str(page.url or "").strip().lower() if page is not None else ""
    except Exception:
        url = ""
    title = str((current_observation or {}).get("title") or "").strip().lower()
    if not ("/faces/" in url or "fscmui" in url or "oracle" in title):
        return None
    if locator is not None and _ptr_locator_is_actionable(locator, timeout_ms=250):
        return None
    if not _ptr_error_looks_like_missing_button(error):
        return None
    warning_state = _ptr_oracle_warning_dialog_state(page)
    if int(warning_state.get("dialog_count") or 0) > 0:
        return None
    return {
        "label": label,
        "surface_type": _ptr_oracle_surface_type(page, current_observation),
        "dialog_count": 0,
        "reason": "optional_warning_dialog_not_present",
    }


def _ptr_try_dismiss_oracle_warning_dialog(
    page: Page | None,
    locator: Locator | None,
    label: str,
    error: Any,
    *,
    observation: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if page is None:
        return None

    current_observation = observation or _ptr_observe(page, locator)
    try:
        url = str(page.url or "").strip().lower()
    except Exception:
        url = ""
    title = str((current_observation or {}).get("title") or "").strip().lower()
    if not ("/faces/" in url or "fscmui" in url or "oracle" in title):
        return None

    error_text = _ptr_normalize_text(error)
    if not any(
        snippet in error_text
        for snippet in ("intercepts pointer events", "afmodalglasspane", "another element", "pointer events")
    ):
        return None

    warning_state = _ptr_oracle_warning_dialog_state(page)
    if not bool(warning_state.get("warning_visible")):
        return None

    candidate_specs: list[tuple[str, str]] = []
    ok_button_id = str(warning_state.get("ok_button_id") or "").strip()
    close_button_id = str(warning_state.get("close_button_id") or "").strip()
    if ok_button_id:
        candidate_specs.append(("oracle_warning_dialog_ok", ok_button_id))
    if close_button_id and close_button_id != ok_button_id:
        candidate_specs.append(("oracle_warning_dialog_close", close_button_id))

    active_element = current_observation.get("active_element") if isinstance(current_observation, dict) else {}
    active_element = active_element if isinstance(active_element, dict) else {}
    active_id = str(active_element.get("id") or "").strip()
    if active_id and active_id not in {ok_button_id, close_button_id}:
        candidate_specs.append(("oracle_warning_dialog_active_button", active_id))

    if not candidate_specs:
        return None

    page_locator = getattr(page, "locator", None)
    if not callable(page_locator):
        return None

    for strategy_name, button_id in candidate_specs:
        escaped_id = button_id.replace("\\", "\\\\").replace('"', '\\"')
        try:
            candidate = page.locator(f'[id="{escaped_id}"]')
            resolved = candidate.first if hasattr(candidate, "first") else candidate
            if not _ptr_locator_is_actionable(resolved, timeout_ms=1000):
                continue
            _ptr_record_strategy_attempt(strategy_name)
            _ptr_strict_click(resolved)
            page.wait_for_timeout(_ptr_wait_ms("PTR_POST_CLICK_WAIT_MS", 250))
            after_state = _ptr_oracle_warning_dialog_state(page)
            if not bool(after_state.get("warning_visible")):
                return {
                    "label": label,
                    "surface_type": _ptr_oracle_surface_type(page, current_observation),
                    "warning_title": str(warning_state.get("warning_title") or "").strip(),
                    "warning_text": _ptr_trim_debug_text(warning_state.get("warning_text"), 240),
                    "dialog_count_before": int(warning_state.get("dialog_count") or 0),
                    "dialog_count_after": int(after_state.get("dialog_count") or 0),
                    "strategy_name": strategy_name,
                    "button_id": button_id,
                }
        except Exception:
            continue
    return None


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
    max_text_chars = max(120, min(600, _ptr_int_env("PTR_AI_SELF_REPAIR_MAX_TEXT_CHARS", 220)))
    raw_candidate_cap = max(max_candidates * 6, 60)
    try:
        context = current_page.evaluate(
            r"""(payload) => {
                const helper = String(payload?.helper || "").trim();
                const label = String(payload?.label || "").trim();
                const maxCandidates = Number(payload?.maxCandidates || 8);
                const rawCandidateCap = Number(payload?.rawCandidateCap || 60);
                const maxHtmlChars = Number(payload?.maxHtmlChars || 900);
                const maxTextChars = Number(payload?.maxTextChars || 220);
                const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
                const truncate = (value, limit) => {
                    const text = normalize(value);
                    if (!text || text.length <= limit) return text;
                    return text.slice(0, Math.max(0, limit - 3)) + "...";
                };
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
                const generalSelectors = [
                    "select",
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
                ];
                const menuSelectors = [
                    "[role='menu']",
                    "[role='menuitem']",
                    ".oj-popup",
                    ".oj-popup [role='menuitem']",
                    "a",
                    "button",
                    "[role='button']",
                    "[role='link']",
                    "[role='option']",
                ];
                const buttonSelectors = [
                    "oj-action-card",
                    ".oj-actioncard",
                    "oj-switch",
                    "[role='switch']",
                    "button",
                    "a",
                    "[role='button']",
                    "[role='link']",
                    "[role='tab']",
                    "[role='checkbox']",
                    "[role='menuitem']",
                ];
                let selectors = [...helperSelectors, ...generalSelectors];
                if (helper.includes("menu")) {
                    selectors = [...helperSelectors, ...menuSelectors];
                } else if (helper.includes("button")) {
                    selectors = [...helperSelectors, ...buttonSelectors];
                }
                const seen = new Set();
                const results = [];
                const labelledByText = (candidate) => {
                    const ids = normalize(candidate?.getAttribute?.("aria-labelledby"));
                    if (!ids) return "";
                    const values = [];
                    for (const id of ids.split(/\s+/)) {
                        const node = document.getElementById(id);
                        const text = truncate(node?.innerText || node?.textContent, maxTextChars);
                        if (text) values.push(text);
                    }
                    return normalize(values.join(" "));
                };
                const pushCandidate = (candidate) => {
                    if (!candidate || seen.has(candidate) || !isVisible(candidate)) return;
                    seen.add(candidate);
                    const role = normalize(candidate.getAttribute?.("role"));
                    const text = truncate(candidate.innerText || candidate.textContent, maxTextChars);
                    const oracleHost = candidate.closest?.("oj-select-single, oj-c-select-single");
                    const entry = {
                        tag: String(candidate.tagName || "").toLowerCase(),
                        role,
                        id: normalize(candidate.id),
                        name: normalize(candidate.getAttribute?.("name")),
                        aria_label: normalize(candidate.getAttribute?.("aria-label")),
                        aria_labelledby: normalize(candidate.getAttribute?.("aria-labelledby")),
                        aria_controls: normalize(candidate.getAttribute?.("aria-controls")),
                        labelledby_text: truncate(labelledByText(candidate), maxTextChars),
                        label_hint: normalize(candidate.getAttribute?.("label-hint")),
                        placeholder: normalize(candidate.getAttribute?.("placeholder")),
                        title: normalize(candidate.getAttribute?.("title")),
                        data_oj_field: normalize(candidate.getAttribute?.("data-oj-field")),
                        oracle_host_tag: normalize(oracleHost?.tagName).toLowerCase(),
                        oracle_host_id: normalize(oracleHost?.id),
                        oracle_host_text: truncate(oracleHost?.innerText || oracleHost?.textContent, maxTextChars),
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
                "maxTextChars": max_text_chars,
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
    global _PTR_STEP_INDEX, _PTR_NEXT_STEP_SCREENSHOT_OVERRIDE_PNG
    if not _PTR_STEP_ARTIFACTS_DIR or _PTR_LAST_PAGE is None:
        return
    try:
        Path(_PTR_STEP_ARTIFACTS_DIR).mkdir(parents=True, exist_ok=True)
        _PTR_STEP_INDEX += 1
        filename = f"step_{_PTR_STEP_INDEX:03d}_{re.sub(r'[^A-Za-z0-9._-]+', '_', str(action or 'step')).strip('._') or 'step'}.png"
        path = Path(_PTR_STEP_ARTIFACTS_DIR) / filename
        override_png = _PTR_NEXT_STEP_SCREENSHOT_OVERRIDE_PNG
        _PTR_NEXT_STEP_SCREENSHOT_OVERRIDE_PNG = None
        if isinstance(override_png, bytes) and override_png:
            path.write_bytes(override_png)
        else:
            _PTR_LAST_PAGE.screenshot(path=str(path), full_page=_ptr_env_flag("PTR_STEP_SCREENSHOT_FULL_PAGE", "false"))
        _PTR_STEP_ARTIFACTS.append({"index": _PTR_STEP_INDEX, "action": str(action or "step"), "local_path": str(path)})
    except Exception:
        _PTR_NEXT_STEP_SCREENSHOT_OVERRIDE_PNG = None
        return


def _ptr_write_diagnostics() -> None:
    if not _PTR_DIAGNOSTICS_PATH:
        return
    try:
        _ptr_capture_live_snapshot_before_close(_PTR_LAST_PAGE)
    except Exception:
        pass
    _ptr_persist_diagnostics_snapshot()


atexit.register(_ptr_write_diagnostics)
atexit.register(_ptr_release_pending_steel_sessions)


def _ptr_patch_page_methods() -> None:
    if getattr(Page, "_ptr_v2_patched", False):
        return

    original_goto = Page.goto
    original_reload = Page.reload
    original_page_close = Page.close
    original_context_close = BrowserContext.close
    original_browser_close = Browser.close

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

    def _wrapped_page_close(self, *args, **kwargs):
        _ptr_register_page(self)
        _ptr_capture_live_snapshot_before_close(self)
        return original_page_close(self, *args, **kwargs)

    def _wrapped_context_close(self, *args, **kwargs):
        for page in _ptr_order_pages_for_snapshot(_ptr_context_pages(self)):
            _ptr_capture_live_snapshot_before_close(page)
        return original_context_close(self, *args, **kwargs)

    def _wrapped_browser_close(self, *args, **kwargs):
        pages: list[Page] = []
        for context in _ptr_browser_contexts(self):
            pages.extend(_ptr_context_pages(context))
        for page in _ptr_order_pages_for_snapshot(pages):
            _ptr_capture_live_snapshot_before_close(page)
        steel_session_id = _PTR_STEEL_BROWSER_SESSION_IDS.pop(id(self), "")
        try:
            return original_browser_close(self, *args, **kwargs)
        finally:
            _ptr_release_steel_session(steel_session_id)

    Page.goto = _wrapped_goto
    Page.reload = _wrapped_reload
    Page.close = _wrapped_page_close
    BrowserContext.close = _wrapped_context_close
    Browser.close = _wrapped_browser_close
    setattr(Page, "_ptr_v2_patched", True)


_ptr_patch_page_methods()


def _ptr_openai_base_url() -> str:
    return str(os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")


def _ptr_ai_self_repair_enabled() -> bool:
    if not _ptr_env_flag("PTR_AI_SELF_REPAIR_ENABLED", "false"):
        return False
    return bool(get_runner_env_value("OPENAI_API_KEY"))


def _ptr_ai_self_repair_model() -> str:
    return (
        str(
            os.getenv(
                "PTR_AI_SELF_REPAIR_MODEL",
                os.getenv("OPENAI_FAILURE_SUMMARY_MODEL", "gpt-5.4-mini"),
            )
        ).strip()
        or "gpt-5.4-mini"
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
        dom_context = _ptr_collect_ai_dom_candidates(current_page, helper, label)
    return build_ai_repair_prompt(
        helper=helper,
        label=label,
        last_error=last_error,
        value=value,
        page_title=page_title,
        page_url=page_url,
        script_data=_ptr_current_script_data(),
        locator_context=_ptr_capture_locator_context(locator),
        dom_context=dom_context,
        retry_feedback=retry_feedback,
    )


def _ptr_capture_ai_context_screenshot(current_page: Page | None) -> dict[str, Any]:
    if current_page is None:
        return {}

    image_format = _ptr_normalize_text(os.getenv("PTR_AI_SELF_REPAIR_SCREENSHOT_FORMAT", "jpeg"))
    if image_format not in {"jpeg", "png"}:
        image_format = "jpeg"
    media_type = "image/jpeg" if image_format == "jpeg" else "image/png"
    screenshot_kwargs: dict[str, Any] = {"full_page": True}
    if image_format == "jpeg":
        screenshot_kwargs["type"] = "jpeg"
        screenshot_kwargs["quality"] = max(20, min(95, _ptr_int_env("PTR_AI_SELF_REPAIR_SCREENSHOT_QUALITY", 45)))
    else:
        screenshot_kwargs["type"] = "png"

    screenshot_scale = _ptr_normalize_text(os.getenv("PTR_AI_SELF_REPAIR_SCREENSHOT_SCALE", "css"))
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


def _ptr_capture_ai_extract_context_screenshot(current_page: Page | None) -> dict[str, Any]:
    global _PTR_NEXT_STEP_SCREENSHOT_OVERRIDE_PNG
    if current_page is None:
        return {}

    wait_ms = _ptr_wait_ms("PTR_AI_EXTRACT_PRE_CAPTURE_WAIT_MS", 1200)
    try:
        current_page.wait_for_timeout(wait_ms)
    except Exception:
        pass

    try:
        _ptr_capture_page_snapshot(current_page)
    except Exception:
        pass

    screenshot_kwargs: dict[str, Any] = {
        "full_page": _ptr_env_flag("PTR_AI_EXTRACT_SCREENSHOT_FULL_PAGE", "false"),
        "type": "png",
    }
    screenshot_scale = _ptr_normalize_text(
        os.getenv(
            "PTR_AI_EXTRACT_SCREENSHOT_SCALE",
            os.getenv("PTR_AI_SELF_REPAIR_SCREENSHOT_SCALE", "css"),
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
        _PTR_NEXT_STEP_SCREENSHOT_OVERRIDE_PNG = screenshot_bytes
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
        _PTR_NEXT_STEP_SCREENSHOT_OVERRIDE_PNG = None
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


def _ptr_request_ai_self_repair(
    current_page: Page,
    helper: str,
    label: str,
    last_error: Any,
    value: str | None = None,
    locator: Locator | None = None,
    retry_feedback: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    endpoint = f"{_ptr_openai_base_url()}/responses"
    model = _ptr_ai_self_repair_model()
    interaction: dict[str, Any] = {
        "feature": "self_repair",
        "helper": helper,
        "label": label,
        "model": model,
        "endpoint": endpoint,
        "system_prompt": AI_REPAIR_SYSTEM_PROMPT,
    }
    if value is not None:
        interaction["value"] = str(value)
    script_data = _ptr_current_script_data()
    if script_data:
        interaction["script_data"] = _ptr_clone_json_value(script_data)
        interaction["recorded_script_data"] = _ptr_clone_json_value(script_data)
    locator_context = _ptr_capture_locator_context(locator)
    if locator_context:
        interaction["recorded_target_context"] = _ptr_clone_json_value(locator_context)
    if retry_feedback:
        interaction["retry_feedback"] = _ptr_clone_json_value(retry_feedback)
    if last_error not in (None, ""):
        interaction["last_error"] = str(last_error)

    if not _ptr_ai_self_repair_enabled():
        interaction["status"] = "disabled"
        interaction["error"] = "AI self-repair is disabled or OPENAI_API_KEY is missing."
        _ptr_record_ai_interaction(interaction)
        return []

    screenshot_context = _ptr_capture_ai_context_screenshot(current_page)
    if screenshot_context:
        interaction["page_screenshot"] = _ptr_clone_json_value(screenshot_context)

    dom_context = _ptr_collect_ai_dom_candidates(current_page, helper, label)
    prompt = _ptr_build_ai_self_repair_prompt(
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
        prompt += (
            "\nA full-page screenshot is attached in this request as additional context.\n"
            "Use the screenshot together with the recorded target context and DOM candidates."
        )
    interaction["user_prompt"] = prompt
    interaction["dom_candidates"] = _ptr_clone_json_value(dom_context)
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

    user_content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    if str((screenshot_context or {}).get("status") or "").strip() == "captured":
        image_url = str((screenshot_context or {}).get("image_url") or "").strip()
        if image_url:
            user_content.append({"type": "input_image", "image_url": image_url})

    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": AI_REPAIR_SYSTEM_PROMPT}]},
            {"role": "user", "content": user_content},
        ],
        "text": {"format": {"type": "json_object"}},
        "max_output_tokens": 400,
    }

    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {get_runner_env_value('OPENAI_API_KEY')}",
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


_PTR_AI_EXTRACTED: dict[str, str] = {}

_PTR_AI_EXTRACT_SYSTEM_PROMPT = (
    "You extract one specific value from provided web page evidence. "
    'Return JSON only in the form {"value": "<the exact value>"}. '
    "Return only the requested value with no labels, units, currency symbols, or "
    "extra words. Use structured page evidence and the screenshot together when both are present. "
    'If the value is not present in the provided evidence, return {"value": ""}.'
)
_PTR_AI_EXTRACT_PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")


def _ptr_resolve(value: Any) -> Any:
    """Substitute ``{{name}}`` placeholders with values captured earlier in this
    run by ai_extract(). Non-strings and unknown placeholders pass through
    unchanged (a stale placeholder then fails loudly at the locator, not here)."""
    if not isinstance(value, str) or "{{" not in value:
        return value

    def _sub(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key in _PTR_AI_EXTRACTED:
            return _PTR_AI_EXTRACTED[key]
        return match.group(0)

    return _PTR_AI_EXTRACT_PLACEHOLDER_RE.sub(_sub, value)


def _ptr_write_ai_extracted_outputs() -> None:
    """Surface ai_extract() values as flow-context outputs for downstream
    recordings (same ``PTR_SCRIPT_STEP_OUTPUT_PATH`` channel the runner reads)."""
    output_path = str(os.getenv("PTR_SCRIPT_STEP_OUTPUT_PATH", "") or "").strip()
    if not output_path or not _PTR_AI_EXTRACTED:
        return
    try:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"outputs": dict(_PTR_AI_EXTRACTED)}, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception:
        return


def _ptr_normalize_ai_extract_text(value: Any, limit: int) -> str:
    text = str(value or "").replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _ptr_compact_ai_extract_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}

    compact: dict[str, Any] = {}
    page_title = _ptr_normalize_ai_extract_text(snapshot.get("page_title"), 160)
    page_url = _ptr_normalize_ai_extract_text(snapshot.get("page_url"), 240)
    page_text = _ptr_normalize_ai_extract_text(snapshot.get("page_text"), 4000)
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
                _ptr_normalize_ai_extract_text(item, 120)
                for item in (table.get("headers") or [])[:12]
                if _ptr_normalize_ai_extract_text(item, 120)
            ]
            rows: list[list[str]] = []
            for row in (table.get("rows") or [])[:5]:
                if not isinstance(row, list):
                    continue
                compact_row = [
                    _ptr_normalize_ai_extract_text(cell, 160)
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
            label = _ptr_normalize_ai_extract_text(item.get("label"), 120)
            value = _ptr_normalize_ai_extract_text(item.get("value"), 180)
            if label and value:
                label_values.append({"label": label, "value": value})

        text_candidates: list[str] = []
        for item in (semantics.get("text_candidates") or [])[:30]:
            if not isinstance(item, dict):
                continue
            text = (
                _ptr_normalize_ai_extract_text(item.get("text"), 160)
                or _ptr_normalize_ai_extract_text(item.get("title"), 160)
                or _ptr_normalize_ai_extract_text(item.get("aria_label"), 160)
            )
            if text:
                text_candidates.append(text)

        dialogs: list[dict[str, str]] = []
        for item in (semantics.get("dialogs") or [])[:3]:
            if not isinstance(item, dict):
                continue
            title = _ptr_normalize_ai_extract_text(item.get("title"), 160)
            text = _ptr_normalize_ai_extract_text(item.get("text"), 600)
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


def _ptr_build_ai_extract_prompt(
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
        parts.append("A full-page screenshot is attached. Use it together with the structured page evidence.")
    return "\n".join(parts)


def _ptr_ai_extract(page: Page, name: str, prompt: str) -> str:
    """Capture the current page and ask the vision LLM for the value described by
    ``prompt``; store it under ``name`` for later ``{{name}}`` substitution and as
    a flow-context output. Raises if AI is unavailable or returns an empty value."""
    output_name = str(name or "").strip()
    instruction = str(prompt or "").strip()
    if not output_name:
        raise ValueError("ai_extract requires a non-empty value name.")
    if not instruction:
        raise ValueError(f"ai_extract('{output_name}', ...) requires a non-empty prompt.")

    endpoint = f"{_ptr_openai_base_url()}/responses"
    model = _ptr_ai_self_repair_model()
    interaction: dict[str, Any] = {
        "feature": "ai_extract",
        "label": output_name,
        "model": model,
        "endpoint": endpoint,
        "system_prompt": _PTR_AI_EXTRACT_SYSTEM_PROMPT,
        "user_prompt": instruction,
    }

    if not _ptr_ai_self_repair_enabled():
        interaction["status"] = "disabled"
        interaction["error"] = "ai_extract is unavailable: AI is disabled or OPENAI_API_KEY is missing."
        _ptr_record_ai_interaction(interaction)
        raise RuntimeError(
            f"ai_extract('{output_name}') failed: AI is disabled or OPENAI_API_KEY is missing."
        )

    current_page = page or _PTR_LAST_PAGE
    screenshot_context = _ptr_capture_ai_extract_context_screenshot(current_page) if current_page is not None else {}
    snapshot_context: dict[str, Any] = {}
    if current_page is not None:
        snapshot_context = _ptr_compact_ai_extract_snapshot(dict(_PTR_LAST_PAGE_SNAPSHOT))
        if not snapshot_context:
            try:
                snapshot_context = _ptr_compact_ai_extract_snapshot(_ptr_capture_page_snapshot(current_page))
            except Exception:
                snapshot_context = _ptr_compact_ai_extract_snapshot(dict(_PTR_LAST_PAGE_SNAPSHOT))
    else:
        snapshot_context = _ptr_compact_ai_extract_snapshot(dict(_PTR_LAST_PAGE_SNAPSHOT))
    if screenshot_context:
        interaction["page_screenshot"] = _ptr_clone_json_value(screenshot_context)
    if snapshot_context:
        interaction["page_snapshot"] = _ptr_clone_json_value(snapshot_context)

    has_screenshot = str((screenshot_context or {}).get("status") or "").strip() == "captured"
    user_content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": _ptr_build_ai_extract_prompt(
                instruction,
                snapshot_context,
                has_screenshot=has_screenshot,
            ),
        }
    ]
    if has_screenshot:
        image_url = str((screenshot_context or {}).get("image_url") or "").strip()
        if image_url:
            user_content.append({"type": "input_image", "image_url": image_url})

    interaction["status"] = "requested"
    _ptr_record_ai_interaction(interaction)
    _ptr_record_strategy_attempt("ai_extract")

    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": _PTR_AI_EXTRACT_SYSTEM_PROMPT}]},
            {"role": "user", "content": user_content},
        ],
        "text": {"format": {"type": "json_object"}},
        "max_output_tokens": 200,
    }
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {get_runner_env_value('OPENAI_API_KEY')}",
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
            {"status": "request_error", "error_type": type(exc).__name__, "error": str(exc), "error_response_body": error_body}
        )
        raise RuntimeError(f"ai_extract('{output_name}') failed: AI request error: {exc}") from exc
    except Exception as exc:
        _ptr_update_last_ai_interaction(
            {"status": "request_error", "error_type": type(exc).__name__, "error": str(exc), "api_response_body": response_body}
        )
        raise RuntimeError(f"ai_extract('{output_name}') failed: AI request error: {exc}") from exc

    response_text = _ptr_extract_ai_output_text(parsed)
    _ptr_update_last_ai_interaction({"response_text": response_text})

    value = ""
    try:
        parsed_response = _ptr_parse_ai_json_response(response_text)
        _ptr_update_last_ai_interaction({"parsed_response": parsed_response})
        if isinstance(parsed_response, dict) and parsed_response.get("value") is not None:
            value = str(parsed_response.get("value")).strip()
    except Exception:
        value = str(response_text or "").strip()

    if not value:
        _ptr_update_last_ai_interaction({"status": "empty", "extracted_value": ""})
        raise RuntimeError(
            f"ai_extract('{output_name}') returned an empty value for prompt: {instruction}"
        )

    _PTR_AI_EXTRACTED[output_name] = value
    _ptr_write_ai_extracted_outputs()
    _ptr_update_last_ai_interaction(
        {"status": "success", "extracted_name": output_name, "extracted_value": value}
    )
    return value


def _ptr_ai_text_matches_label(value: Any, label: str) -> bool:
    normalized_label = _ptr_normalize_text(label)
    normalized_value = _ptr_normalize_text(value)
    if not normalized_label:
        return True
    if not normalized_value:
        return False
    oracle_notification_signature = _ptr_oracle_notification_badge_signature(normalized_label)
    if oracle_notification_signature and oracle_notification_signature == _ptr_oracle_notification_badge_signature(normalized_value):
        return True
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
    retry_feedback: dict[str, Any] | None = None,
) -> list[tuple[str, Locator, dict[str, Any]]]:
    locators: list[tuple[str, Locator, dict[str, Any]]] = []
    rejected_names: list[str] = []
    rejected_reasons: list[str] = []
    for idx, strategy in enumerate(
        _ptr_request_ai_self_repair(
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


def _ptr_execute_ai_repair_rounds(
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
    max_rounds = max(1, min(2, _ptr_int_env("PTR_AI_SELF_REPAIR_MAX_ROUNDS", 2)))
    retry_feedback: dict[str, Any] | None = None
    latest_error: Exception = last_error if isinstance(last_error, Exception) else RuntimeError(str(last_error))
    used_ai = False
    last_ai_strategy_name = ""

    for round_index in range(1, max_rounds + 1):
        ai_candidates = _ptr_ai_repair_locators(
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
                _ptr_record_strategy_attempt(strategy_name)
                if execute_locator(strategy_name, ai_locator, ai_strategy):
                    _ptr_finalize_last_ai_interaction(
                        repair_outcome="validated",
                        strategy_name=strategy_name,
                        postcondition_kind=postcondition_kind,
                    )
                    return (strategy_name, ai_locator, ai_strategy), latest_error
                latest_error = RuntimeError(str(failure_message(strategy_name)))
            except Exception as exc:
                latest_error = exc

        if round_index >= max_rounds:
            break

        last_interaction = _ptr_last_ai_interaction()
        retry_feedback = {
            "round": round_index,
            "execution_error": str(latest_error),
            "attempted_locator_strategies": attempted_names,
            "rejected_locator_reasons": list(last_interaction.get("rejected_locator_reasons") or []),
            "previous_response_strategies": _ptr_clone_json_value(list(last_interaction.get("response_strategies") or [])),
            "previous_response_text": str(last_interaction.get("response_text") or "").strip(),
        }
        _ptr_update_last_ai_interaction(
            {
                "retry_requested": True,
                "retry_round": round_index + 1,
                "retry_feedback": _ptr_clone_json_value(retry_feedback),
            }
        )

    if used_ai:
        _ptr_finalize_last_ai_interaction(
            repair_outcome="execution_failed",
            strategy_name=last_ai_strategy_name,
            error=latest_error,
            postcondition_kind=postcondition_kind,
        )
    return None, latest_error


def _ptr_replace_primary_locator_arg(
    args: tuple[Any, ...],
    primary_locator: Locator | None,
    replacement_locator: Locator,
) -> tuple[Any, ...]:
    if primary_locator is None:
        return tuple(args)
    replaced_args = list(args)
    for index, arg in enumerate(replaced_args):
        if arg is primary_locator:
            replaced_args[index] = replacement_locator
            break
    return tuple(replaced_args)


def _ptr_universal_ai_postcondition_kind(helper_name: str) -> str:
    normalized_helper = _ptr_normalize_text(helper_name)
    if normalized_helper in {"check_target", "uncheck_target"}:
        return "checkbox_state_changed"
    if normalized_helper == "click_table_row":
        return "row_selected"
    if normalized_helper == "click_table_field":
        return "field_focused"
    if normalized_helper == "click_navigation_button":
        return "guided_flow_advanced"
    if normalized_helper == "submit_textbox_enter":
        return "enter_submitted"
    return "action_effect"


def _ptr_try_universal_ai_action_repair(
    *,
    action_type: str,
    label: str,
    fn,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    current_page: Page | None,
    primary_locator: Locator | None,
    last_error: Exception,
) -> tuple[bool, Any, Exception]:
    if current_page is None or primary_locator is None:
        return False, None, last_error
    if _PTR_CURRENT_STRATEGY.get("ai_interactions"):
        return False, None, last_error

    helper_name = _ptr_normalize_runtime_action_name(getattr(fn, "__name__", action_type))
    eligible_helpers = {
        "check_target",
        "uncheck_target",
        "click_textbox",
        "click_table_field",
        "click_table_row",
        "dblclick_text_target",
        "click_navigation_button",
        "submit_textbox_enter",
    }
    if helper_name not in eligible_helpers:
        return False, None, last_error

    debug_trace = _ptr_update_debug_detail(
        "universal_ai_repair",
        {
            "helper": helper_name,
            "label": label,
            "status": "requested",
            "trigger_error": _ptr_trim_debug_text(last_error, 320),
        },
    )
    postcondition_kind = _ptr_universal_ai_postcondition_kind(helper_name)
    execution_result: Any = None

    def _execute_ai_action_locator(strategy_name: str, ai_locator: Locator, ai_strategy: dict[str, Any]) -> bool:
        nonlocal execution_result
        retry_args = _ptr_replace_primary_locator_arg(args, primary_locator, ai_locator)
        execution_result = fn(*retry_args, **kwargs)
        return True

    ai_result, latest_error = _ptr_execute_ai_repair_rounds(
        current_page=current_page,
        helper=helper_name,
        label=label,
        last_error=last_error,
        locator=primary_locator,
        postcondition_kind=postcondition_kind,
        failure_message=lambda strategy_name: (
            f'AI strategy "{strategy_name}" did not repair "{label}" for helper "{helper_name}".'
        ),
        execute_locator=_execute_ai_action_locator,
    )
    if ai_result is None:
        debug_trace["status"] = "failed"
        debug_trace["error"] = _ptr_trim_debug_text(latest_error, 320)
        _ptr_set_debug_detail("universal_ai_repair", debug_trace)
        return False, None, latest_error

    strategy_name, ai_locator, ai_strategy = ai_result
    debug_trace["status"] = "validated"
    debug_trace["strategy_name"] = strategy_name
    debug_trace["locator_strategy"] = _ptr_clone_json_value(ai_strategy)
    _ptr_set_debug_detail("universal_ai_repair", debug_trace)
    _ptr_set_recovery_record(
        "ai_validated",
        "ai_locator_repair",
        "ai_locator_repair",
        {
            "helper": helper_name,
            "strategy_name": strategy_name,
            "locator_strategy": _ptr_clone_json_value(ai_strategy),
        },
    )
    _ptr_store_experience_episode(
        action_type=helper_name,
        label=label,
        page=current_page,
        locator=ai_locator,
        error=last_error,
        status="success",
        postcondition_kind=postcondition_kind,
        postcondition_passed=True,
    )
    return True, execution_result, latest_error


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


def _ptr_try_oracle_quick_action_exact_match(
    page: Page,
    label: str,
    error: Any,
    postcondition,
    *,
    allow_after_expand: bool = False,
) -> str:
    error_text = str(error or "")
    lowered_error = error_text.lower()
    if not allow_after_expand:
        if "strict mode violation" not in lowered_error:
            return ""
        if "get_by_role(\"link\"" not in error_text and "get_by_role('link'" not in error_text:
            return ""

    label_text = str(label or "").strip()
    if not label_text:
        return ""

    exact_text = re.compile(rf"^{re.escape(label_text)}$")
    candidates = [
        ("oracle_quick_action_exact_link", page.locator("a[type='quickaction']").filter(has_text=exact_text)),
        ("oracle_quick_action_exact_class", page.locator("a.flat-quickactions-item-link").filter(has_text=exact_text)),
        ("oracle_quick_action_exact_role", page.get_by_role("link", name=label_text, exact=True)),
        ("oracle_quick_action_exact_text", page.get_by_text(label_text, exact=True)),
    ]

    for strategy_name, candidate in candidates:
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
                return strategy_name
        except Exception:
            continue
    return ""


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


def _ptr_try_oracle_notification_badge(page: Page, label: str, postcondition) -> str:
    if not _ptr_oracle_notification_badge_signature(label):
        return ""

    badge_pattern = re.compile(r"^Notifications\s*\(\d+\s+unread\)$", re.IGNORECASE)
    candidates = [
        ("oracle_notification_badge_role", page.get_by_role("link", name=badge_pattern)),
        ("oracle_notification_badge_text", page.get_by_text(badge_pattern)),
    ]

    for strategy_name, candidate in candidates:
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
                return strategy_name
        except Exception:
            continue
    return ""


def _ptr_try_oracle_recorded_button_context(
    page: Page,
    locator: Locator,
    label: str,
    error: Any,
    postcondition,
) -> str:
    error_text = str(error or "")
    lowered_error = error_text.lower()
    if "strict mode violation" not in lowered_error:
        return ""
    if "get_by_role(\"button\"" not in error_text and "get_by_role('button'" not in error_text:
        return ""

    recorded_context = _ptr_capture_locator_context(locator)
    if not isinstance(recorded_context, dict):
        return ""

    title_text = str(recorded_context.get("title") or "").strip()
    id_text = str(recorded_context.get("id") or "").strip()
    class_name = str(recorded_context.get("class_name") or "").strip()
    if not title_text and not id_text:
        return ""
    page_title = ""
    try:
        page_title = str(page.title() or "").strip()
    except Exception:
        page_title = ""
    if not (
        "oracle" in _ptr_normalize_text(page_title)
        or "homebutton" in _ptr_normalize_text(class_name)
    ):
        return ""

    candidates: list[tuple[str, Locator]] = []
    if title_text:
        escaped_title = title_text.replace("\\", "\\\\").replace('"', '\\"')
        candidates.append(("oracle_recorded_button_title", page.locator(f'button[title="{escaped_title}"]')))
    if id_text:
        escaped_id = id_text.replace("\\", "\\\\").replace('"', '\\"')
        candidates.append(("oracle_recorded_button_id", page.locator(f'button[id="{escaped_id}"]')))

    for strategy_name, candidate in candidates:
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
                return strategy_name
        except Exception:
            continue
    return ""


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
    debug_payload = _PTR_CURRENT_STRATEGY.get("debug")
    if isinstance(debug_payload, dict) and debug_payload:
        entry["debug"] = _ptr_clone_json_value(debug_payload)
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
    current_page = page or _PTR_LAST_PAGE
    if current_page is None:
        return
    wait_ms = _ptr_wait_ms("PTR_AFTER_ACTION_WAIT_MS", _PTR_HARDCODED_AFTER_ACTION_WAIT_MS)
    try:
        current_page.wait_for_timeout(wait_ms)
    except Exception:
        pass
    _ptr_capture_page_snapshot(current_page)


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
        current_page = _PTR_LAST_PAGE or page
        repaired, repaired_result, final_error = _ptr_try_universal_ai_action_repair(
            action_type=action_type,
            label=label,
            fn=fn,
            args=args,
            kwargs=kwargs,
            current_page=current_page,
            primary_locator=primary_locator,
            last_error=exc,
        )
        if repaired:
            current_page = _PTR_LAST_PAGE or current_page
            _ptr_wait_after_interaction(current_page)
            _ptr_capture_step(action_type)
            _ptr_finalize_action_log(
                action_type,
                label,
                "success",
                int((time.time() - start) * 1000),
                page=current_page,
            )
            return repaired_result
        _ptr_capture_step(action_type)
        _ptr_capture_failure_screenshot()
        _ptr_store_experience_episode(
            action_type=_ptr_normalize_runtime_action_name(getattr(fn, "__name__", action_type)),
            label=label,
            page=current_page,
            locator=primary_locator,
            error=final_error,
            status="failed",
            postcondition_kind="none",
            postcondition_passed=False,
        )
        _ptr_finalize_action_log(
            action_type,
            label,
            "failed",
            int((time.time() - start) * 1000),
            error=final_error,
            page=current_page,
        )
        if final_error is not exc:
            raise final_error from exc
        raise


def _ptr_tracked_raw_action(
    action_type: str,
    label: str,
    raw_source: str,
    global_scope: dict[str, Any],
    local_scope: dict[str, Any],
    *,
    page: Page | None = None,
    locator: Locator | None = None,
):
    current_page = page or _PTR_LAST_PAGE
    if current_page is not None:
        _ptr_register_page(current_page)
    _ptr_reset_strategy_tracking(action_type, label)
    _ptr_record_strategy_attempt("raw_inline")
    start = time.time()
    exec_globals = dict(global_scope or {})
    exec_globals.setdefault("re", re)
    try:
        exec(str(raw_source or ""), exec_globals, local_scope)
        current_page = _PTR_LAST_PAGE or current_page
        _ptr_wait_after_interaction(current_page)
        _ptr_capture_step(action_type)
        _ptr_finalize_action_log(
            action_type,
            label,
            "success",
            int((time.time() - start) * 1000),
            page=current_page,
        )
    except Exception as exc:
        current_page = _PTR_LAST_PAGE or current_page
        _ptr_capture_step(action_type)
        _ptr_capture_failure_screenshot()
        _ptr_store_experience_episode(
            action_type=_ptr_normalize_runtime_action_name(action_type),
            label=label,
            page=current_page,
            locator=locator,
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
            page=current_page,
        )
        raise


def _ptr_click_with_candidates(page: Page, label: str, locator: Locator, helper: str, postcondition):
    requested_label = str(label or "").strip()
    resolved_label = _ptr_resolve(requested_label)
    if resolved_label is None:
        resolved_label = requested_label
    label = str(resolved_label).strip() or requested_label

    before = _ptr_observe(page, locator)
    debug_payload = {
        "helper": helper,
        "label": label,
        "status": "strict_attempt",
        "before": _ptr_debug_observation_summary(before),
        "experience_attempts": [],
    }
    if requested_label and requested_label != label:
        debug_payload["requested_label"] = requested_label
    debug_trace = _ptr_update_debug_detail("click_with_candidates", debug_payload)
    try:
        _ptr_strict_click(locator)
        after = _ptr_observe(page, locator)
        if postcondition(before, after):
            debug_trace["direct_attempt"] = {
                "status": "validated",
                "after": _ptr_debug_observation_summary(after),
            }
            debug_trace["resolved_by"] = "strict"
            debug_trace["status"] = "success"
            _ptr_set_debug_detail("click_with_candidates", debug_trace)
            return
        raise RuntimeError(f'Action "{label}" completed but no postcondition changed.')
    except Exception as direct_exc:
        last_error: Exception = direct_exc
        debug_trace["direct_attempt"] = {
            "status": "failed",
            "error": _ptr_trim_debug_text(direct_exc, 320),
        }
        quick_actions_expanded = False
        if _ptr_try_expand_oracle_quick_actions(page, label):
            quick_actions_expanded = True
            debug_trace["oracle_quick_actions_expand"] = {"attempted": True, "status": "retrying"}
            try:
                _ptr_strict_click(locator)
                after = _ptr_observe(page, locator)
                if postcondition(before, after):
                    debug_trace["oracle_quick_actions_expand"] = {
                        "attempted": True,
                        "status": "validated",
                        "after": _ptr_debug_observation_summary(after),
                    }
                    debug_trace["resolved_by"] = "oracle_quick_actions_expand"
                    debug_trace["status"] = "success"
                    _ptr_set_debug_detail("click_with_candidates", debug_trace)
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
                debug_trace["oracle_quick_actions_expand"] = {
                    "attempted": True,
                    "status": "postcondition_failed",
                    "error": _ptr_trim_debug_text(last_error, 320),
                }
            except Exception as exc:
                last_error = exc
                debug_trace["oracle_quick_actions_expand"] = {
                    "attempted": True,
                    "status": "failed",
                    "error": _ptr_trim_debug_text(exc, 320),
                }

        quick_action_strategy = _ptr_try_oracle_quick_action_exact_match(
            page,
            label,
            last_error,
            postcondition,
            allow_after_expand=quick_actions_expanded,
        )
        if quick_action_strategy:
            debug_trace["oracle_quick_action_exact_match"] = {
                "status": "validated",
                "strategy_name": quick_action_strategy,
            }
            debug_trace["resolved_by"] = "oracle_quick_action_exact_match"
            debug_trace["status"] = "success"
            _ptr_set_debug_detail("click_with_candidates", debug_trace)
            _ptr_set_recovery_record(
                "oracle_handler",
                "quick_action_exact_match",
                "oracle_quick_action_exact_match",
                {"label": label, "strategy_name": quick_action_strategy},
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
        debug_trace["oracle_quick_action_exact_match"] = {
            "status": "not_applied",
            "after_expand": quick_actions_expanded,
        }

        notification_badge_strategy = _ptr_try_oracle_notification_badge(page, label, postcondition)
        if notification_badge_strategy:
            debug_trace["oracle_notification_badge"] = {
                "status": "validated",
                "strategy_name": notification_badge_strategy,
            }
            debug_trace["resolved_by"] = "oracle_notification_badge"
            debug_trace["status"] = "success"
            _ptr_set_debug_detail("click_with_candidates", debug_trace)
            _ptr_set_recovery_record(
                "oracle_handler",
                "notification_badge",
                "oracle_notification_badge",
                {"label": label, "strategy_name": notification_badge_strategy},
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
        debug_trace["oracle_notification_badge"] = {"status": "not_applied"}

        if helper in {"click_button_target", "click_numeric_button_target"}:
            recorded_button_strategy = _ptr_try_oracle_recorded_button_context(
                page,
                locator,
                label,
                last_error,
                postcondition,
            )
            if recorded_button_strategy:
                debug_trace["oracle_recorded_button_context"] = {
                    "status": "validated",
                    "strategy_name": recorded_button_strategy,
                }
                debug_trace["resolved_by"] = "oracle_recorded_button_context"
                debug_trace["status"] = "success"
                _ptr_set_debug_detail("click_with_candidates", debug_trace)
                _ptr_set_recovery_record(
                    "oracle_handler",
                    "recorded_button_context",
                    "oracle_recorded_button_context",
                    {"label": label, "strategy_name": recorded_button_strategy},
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
            debug_trace["oracle_recorded_button_context"] = {"status": "not_applied"}

        if _ptr_try_oracle_home_search(page, label, postcondition):
            debug_trace["oracle_home_search"] = {
                "status": "validated",
                "search_label": label,
            }
            debug_trace["resolved_by"] = "oracle_home_search"
            debug_trace["status"] = "success"
            _ptr_set_debug_detail("click_with_candidates", debug_trace)
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
        debug_trace["oracle_home_search"] = {"status": "not_applied"}

        if helper in {"click_button_target", "click_numeric_button_target"} and _ptr_try_oracle_guided_action_card(page, label, postcondition):
            debug_trace["oracle_guided_action_card"] = {
                "status": "validated",
                "label": label,
            }
            debug_trace["resolved_by"] = "oracle_guided_action_card"
            debug_trace["status"] = "success"
            _ptr_set_debug_detail("click_with_candidates", debug_trace)
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
            debug_trace["oracle_guided_action_card"] = {"status": "not_applied"}

        if helper in {"click_button_target", "click_numeric_button_target"}:
            warning_dialog_recovery = _ptr_try_dismiss_oracle_warning_dialog(
                page,
                locator,
                label,
                last_error,
                observation=before,
            )
            if warning_dialog_recovery:
                debug_trace["oracle_warning_dialog_dismiss"] = {
                    "status": "validated",
                    "details": _ptr_clone_json_value(warning_dialog_recovery),
                }
                debug_trace["resolved_by"] = "oracle_warning_dialog_dismiss"
                debug_trace["status"] = "success"
                _ptr_set_debug_detail("click_with_candidates", debug_trace)
                _ptr_set_recovery_record(
                    "oracle_handler",
                    "warning_dialog_dismiss",
                    "oracle_warning_dialog_dismiss",
                    warning_dialog_recovery,
                )
                _ptr_store_experience_episode(
                    action_type=helper,
                    label=label,
                    page=page,
                    locator=locator,
                    error=last_error,
                    status="success",
                    postcondition_kind="warning_dialog_dismissed",
                    postcondition_passed=True,
                )
                return
            debug_trace["oracle_warning_dialog_dismiss"] = {"status": "not_applied"}

        if helper == "click_button_target":
            optional_warning_skip = _ptr_try_skip_optional_oracle_warning_ok(
                page,
                locator,
                label,
                last_error,
            )
            if optional_warning_skip:
                debug_trace["oracle_optional_warning_ok_absent"] = {
                    "status": "validated",
                    "details": _ptr_clone_json_value(optional_warning_skip),
                }
                debug_trace["resolved_by"] = "oracle_optional_warning_ok_absent"
                debug_trace["status"] = "success"
                _ptr_set_debug_detail("click_with_candidates", debug_trace)
                _ptr_set_recovery_record(
                    "oracle_handler",
                    "optional_warning_ok_absent",
                    "oracle_optional_warning_ok_absent",
                    optional_warning_skip,
                )
                _ptr_store_experience_episode(
                    action_type=helper,
                    label=label,
                    page=page,
                    locator=locator,
                    error=last_error,
                    status="success",
                    postcondition_kind="dialog_absent",
                    postcondition_passed=True,
                )
                return
            debug_trace["oracle_optional_warning_ok_absent"] = {"status": "not_applied"}

        for strategy_name, experience_locator, episode in _ptr_experience_repair_locators(page, helper, label, last_error, locator=locator):
            try:
                _ptr_record_strategy_attempt(strategy_name)
                before_experience = _ptr_observe(page, experience_locator)
                _ptr_strict_click(experience_locator)
                after_experience = _ptr_observe(page, experience_locator)
                if postcondition(before_experience, after_experience):
                    experience_attempts = debug_trace.setdefault("experience_attempts", [])
                    if isinstance(experience_attempts, list):
                        experience_attempts.append(
                            {
                                "strategy_name": strategy_name,
                                "status": "validated",
                                "episode_id": str(episode.get("episode_id") or "").strip(),
                                "retrieval_score": int(episode.get("retrieval_score") or 0),
                                "after": _ptr_debug_observation_summary(after_experience),
                            }
                        )
                    debug_trace["resolved_by"] = "experience_reuse"
                    debug_trace["status"] = "success"
                    _ptr_set_debug_detail("click_with_candidates", debug_trace)
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
                experience_attempts = debug_trace.setdefault("experience_attempts", [])
                if isinstance(experience_attempts, list):
                    experience_attempts.append(
                        {
                            "strategy_name": strategy_name,
                            "status": "postcondition_failed",
                            "episode_id": str(episode.get("episode_id") or "").strip(),
                            "retrieval_score": int(episode.get("retrieval_score") or 0),
                            "error": _ptr_trim_debug_text(last_error, 320),
                        }
                    )
            except Exception as exc:
                last_error = exc
                experience_attempts = debug_trace.setdefault("experience_attempts", [])
                if isinstance(experience_attempts, list):
                    experience_attempts.append(
                        {
                            "strategy_name": strategy_name,
                            "status": "failed",
                            "episode_id": str(episode.get("episode_id") or "").strip(),
                            "retrieval_score": int(episode.get("retrieval_score") or 0),
                            "error": _ptr_trim_debug_text(exc, 320),
                        }
                    )
        if not debug_trace.get("experience_attempts"):
            debug_trace["experience_attempts"] = [{"status": "no_candidates"}]

        def _execute_ai_click_locator(strategy_name: str, ai_locator: Locator, ai_strategy: dict[str, Any]) -> bool:
            before_ai = _ptr_observe(page, ai_locator)
            _ptr_strict_click(ai_locator)
            after_ai = _ptr_observe(page, ai_locator)
            return postcondition(before_ai, after_ai)

        ai_result, last_error = _ptr_execute_ai_repair_rounds(
            current_page=page,
            helper=helper,
            label=label,
            last_error=last_error,
            locator=locator,
            postcondition_kind="action_effect",
            failure_message=lambda strategy_name: f'AI strategy "{strategy_name}" did not satisfy postcondition for "{label}".',
            execute_locator=_execute_ai_click_locator,
        )
        if ai_result is not None:
            strategy_name, ai_locator, ai_strategy = ai_result
            debug_trace["ai_repair"] = {
                "status": "validated",
                "strategy_name": strategy_name,
                "locator_strategy": _ptr_clone_json_value(ai_strategy),
            }
            debug_trace["resolved_by"] = "ai_locator_repair"
            debug_trace["status"] = "success"
            _ptr_set_debug_detail("click_with_candidates", debug_trace)
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
            return
        debug_trace["ai_repair"] = {
            "status": "failed",
            "error": _ptr_trim_debug_text(last_error, 320),
        }
        debug_trace["status"] = "failed"
        debug_trace["final_error"] = _ptr_trim_debug_text(last_error, 320)
        _ptr_set_debug_detail("click_with_candidates", debug_trace)

        raise RuntimeError(f'Unable to click target "{label}" after strict execution, Oracle recovery, and AI self-repair.') from last_error


def _ptr_fill_textbox(locator: Locator, current_page: Page, label: str, value: str) -> None:
    _ptr_register_page(current_page)
    debug_trace = _ptr_update_debug_detail(
        "fill_textbox",
        {
            "label": label,
            "requested_value": _ptr_trim_debug_text(value, 120),
            "status": "strict_attempt",
            "experience_attempts": [],
        },
    )
    try:
        _ptr_strict_fill(locator, value)
        _ptr_wait_for_field_processing(
            current_page,
            env_name="PTR_TEXTBOX_CHANGE_PROCESSING_WAIT_MS",
            default_ms=500,
        )
        observed = _ptr_locator_value(locator) or _ptr_locator_text(locator)
        if _ptr_value_matches(value, observed):
            debug_trace["direct_attempt"] = {
                "status": "validated",
                "observed_value": _ptr_trim_debug_text(observed, 160),
            }
            debug_trace["resolved_by"] = "strict"
            debug_trace["status"] = "success"
            _ptr_set_debug_detail("fill_textbox", debug_trace)
            return
        raise RuntimeError(f'Textbox "{label}" did not reflect the requested value.')
    except Exception as direct_exc:
        last_error: Exception = direct_exc
        debug_trace["direct_attempt"] = {
            "status": "failed",
            "error": _ptr_trim_debug_text(direct_exc, 320),
        }
        try:
            oracle_recovery = _ptr_try_oracle_table_active_editor_fill(current_page, locator, label, value)
            if oracle_recovery:
                debug_trace["oracle_table_active_editor_fill"] = {
                    "status": "validated",
                    "details": _ptr_clone_json_value(oracle_recovery),
                }
                debug_trace["resolved_by"] = "oracle_table_active_editor_fill"
                debug_trace["status"] = "success"
                _ptr_set_debug_detail("fill_textbox", debug_trace)
                _ptr_set_recovery_record(
                    "oracle_handler",
                    "oracle_table_active_editor_fill",
                    "oracle_table_active_editor_fill",
                    oracle_recovery,
                )
                _ptr_store_experience_episode(
                    action_type="fill_textbox",
                    label=label,
                    page=current_page,
                    locator=locator,
                    error=direct_exc,
                    status="success",
                    postcondition_kind="field_value_changed",
                    postcondition_passed=True,
                )
                return
        except Exception as exc:
            last_error = exc
            debug_trace["oracle_table_active_editor_fill"] = {
                "status": "failed",
                "error": _ptr_trim_debug_text(exc, 320),
            }
        if "oracle_table_active_editor_fill" not in debug_trace:
            debug_trace["oracle_table_active_editor_fill"] = {"status": "not_applied"}
        try:
            oracle_spinbutton_recovery = _ptr_try_oracle_spinbutton_fill(current_page, locator, label, value)
            if oracle_spinbutton_recovery:
                debug_trace["oracle_spinbutton_fill"] = {
                    "status": "validated",
                    "details": _ptr_clone_json_value(oracle_spinbutton_recovery),
                }
                debug_trace["resolved_by"] = "oracle_spinbutton_fill"
                debug_trace["status"] = "success"
                _ptr_set_debug_detail("fill_textbox", debug_trace)
                _ptr_set_recovery_record(
                    "oracle_handler",
                    "oracle_spinbutton_fill",
                    "oracle_spinbutton_fill",
                    oracle_spinbutton_recovery,
                )
                _ptr_store_experience_episode(
                    action_type="fill_textbox",
                    label=label,
                    page=current_page,
                    locator=locator,
                    error=direct_exc,
                    status="success",
                    postcondition_kind="field_value_changed",
                    postcondition_passed=True,
                )
                return
        except Exception as exc:
            last_error = exc
            debug_trace["oracle_spinbutton_fill"] = {
                "status": "failed",
                "error": _ptr_trim_debug_text(exc, 320),
            }
        if "oracle_spinbutton_fill" not in debug_trace:
            debug_trace["oracle_spinbutton_fill"] = {"status": "not_applied"}
        for strategy_name, experience_locator, episode in _ptr_experience_repair_locators(current_page, "fill_textbox", label, direct_exc, locator=locator):
            try:
                _ptr_record_strategy_attempt(strategy_name)
                _ptr_strict_fill(experience_locator, value)
                _ptr_wait_for_field_processing(
                    current_page,
                    env_name="PTR_TEXTBOX_CHANGE_PROCESSING_WAIT_MS",
                    default_ms=500,
                )
                observed = _ptr_locator_value(experience_locator) or _ptr_locator_text(experience_locator)
                if _ptr_value_matches(value, observed):
                    experience_attempts = debug_trace.setdefault("experience_attempts", [])
                    if isinstance(experience_attempts, list):
                        experience_attempts.append(
                            {
                                "strategy_name": strategy_name,
                                "status": "validated",
                                "episode_id": str(episode.get("episode_id") or "").strip(),
                                "retrieval_score": int(episode.get("retrieval_score") or 0),
                                "observed_value": _ptr_trim_debug_text(observed, 160),
                            }
                        )
                    debug_trace["resolved_by"] = "experience_reuse"
                    debug_trace["status"] = "success"
                    _ptr_set_debug_detail("fill_textbox", debug_trace)
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
                experience_attempts = debug_trace.setdefault("experience_attempts", [])
                if isinstance(experience_attempts, list):
                    experience_attempts.append(
                        {
                            "strategy_name": strategy_name,
                            "status": "postcondition_failed",
                            "episode_id": str(episode.get("episode_id") or "").strip(),
                            "retrieval_score": int(episode.get("retrieval_score") or 0),
                            "observed_value": _ptr_trim_debug_text(observed, 160),
                            "error": _ptr_trim_debug_text(last_error, 320),
                        }
                    )
            except Exception as exc:
                last_error = exc
                experience_attempts = debug_trace.setdefault("experience_attempts", [])
                if isinstance(experience_attempts, list):
                    experience_attempts.append(
                        {
                            "strategy_name": strategy_name,
                            "status": "failed",
                            "episode_id": str(episode.get("episode_id") or "").strip(),
                            "retrieval_score": int(episode.get("retrieval_score") or 0),
                            "error": _ptr_trim_debug_text(exc, 320),
                        }
                    )
        if not debug_trace.get("experience_attempts"):
            debug_trace["experience_attempts"] = [{"status": "no_candidates"}]
        def _execute_ai_fill_locator(strategy_name: str, ai_locator: Locator, ai_strategy: dict[str, Any]) -> bool:
            _ptr_strict_fill(ai_locator, value)
            _ptr_wait_for_field_processing(
                current_page,
                env_name="PTR_TEXTBOX_CHANGE_PROCESSING_WAIT_MS",
                default_ms=500,
            )
            observed = _ptr_locator_value(ai_locator) or _ptr_locator_text(ai_locator)
            return _ptr_value_matches(value, observed)

        ai_result, last_error = _ptr_execute_ai_repair_rounds(
            current_page=current_page,
            helper="fill_textbox",
            label=label,
            last_error=last_error,
            value=value,
            locator=locator,
            postcondition_kind="field_value_changed",
            failure_message=lambda strategy_name: f'AI strategy "{strategy_name}" did not satisfy fill postcondition for "{label}".',
            execute_locator=_execute_ai_fill_locator,
        )
        if ai_result is not None:
            strategy_name, ai_locator, ai_strategy = ai_result
            debug_trace["ai_repair"] = {
                "status": "validated",
                "strategy_name": strategy_name,
                "locator_strategy": _ptr_clone_json_value(ai_strategy),
            }
            debug_trace["resolved_by"] = "ai_locator_repair"
            debug_trace["status"] = "success"
            _ptr_set_debug_detail("fill_textbox", debug_trace)
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
            return
        debug_trace["ai_repair"] = {
            "status": "failed",
            "error": _ptr_trim_debug_text(last_error, 320),
        }
        debug_trace["status"] = "failed"
        debug_trace["final_error"] = _ptr_trim_debug_text(last_error, 320)
        _ptr_set_debug_detail("fill_textbox", debug_trace)
        raise RuntimeError(f'Unable to fill textbox "{label}" using strict execution and AI self-repair.') from last_error


def _ptr_select_option_expectations(option_args: list[Any] | None, option_kwargs: dict[str, Any] | None) -> dict[str, list[Any]]:
    expectations: dict[str, list[Any]] = {
        "values": [],
        "labels": [],
        "indexes": [],
    }

    def _record(kind: str, value: Any) -> None:
        if isinstance(value, (list, tuple)):
            for item in value:
                _record(kind, item)
            return
        if isinstance(value, dict):
            for nested_kind in ("value", "label", "index"):
                if nested_kind in value:
                    _record(nested_kind, value.get(nested_kind))
            return
        if kind == "index":
            try:
                normalized_index = int(value)
            except Exception:
                return
            if normalized_index not in expectations["indexes"]:
                expectations["indexes"].append(normalized_index)
            return
        normalized_text = str(value or "").strip()
        if not normalized_text:
            return
        bucket = "labels" if kind == "label" else "values"
        if normalized_text not in expectations[bucket]:
            expectations[bucket].append(normalized_text)

    for arg in option_args or []:
        _record("value", arg)

    for kind in ("value", "label", "index"):
        if kind in (option_kwargs or {}):
            _record(kind, option_kwargs.get(kind))

    return expectations


def _ptr_select_option_state(locator: Locator) -> dict[str, Any]:
    state = _ptr_safe_locator_eval(
        locator,
        r"""(node) => {
            const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
            const dedupe = (items) => Array.from(new Set(items.map(normalize).filter(Boolean)));
            if (!node) {
                return {
                    value: "",
                    selected_values: [],
                    selected_labels: [],
                    selected_indexes: [],
                    text: "",
                };
            }
            const selectedOptions = Array.from(node.selectedOptions || []);
            const attr = (name) => normalize(node.getAttribute ? node.getAttribute(name) : "");
            const nodeId = String(node.id || "");
            return {
                value: normalize("value" in node ? node.value : ""),
                selected_values: dedupe(selectedOptions.map((option) => option?.value)),
                selected_labels: dedupe(selectedOptions.map((option) => option?.label || option?.innerText || option?.textContent)),
                selected_indexes: selectedOptions
                    .map((option) => Number(option?.index))
                    .filter((value) => Number.isInteger(value)),
                text: normalize(node.innerText || node.textContent),
                // ADF Faces selectOneChoice commit markers (see _ptr_adf_select_commit_contradicted).
                title: attr("title"),
                afov: attr("_afov"),
                is_adf: Boolean((node.hasAttribute && node.hasAttribute("_afov")) || nodeId.indexOf("::") !== -1),
            };
        }""",
    )
    return state if isinstance(state, dict) else {}


def _ptr_adf_select_commit_contradicted(state: dict[str, Any]) -> bool:
    """Return True when an ADF Faces ``<select>`` shows the requested option in
    the DOM but ADF's committed markers still reflect the prior value.

    Playwright's ``select_option`` mutates the native ``<select>`` value and
    ``selectedOptions`` directly. For an Oracle ADF ``selectOneChoice`` that is
    not enough: ADF only mirrors the committed value into the element's
    ``title`` attribute and its ``_afov`` ("original value") attribute when its
    own change handler / partial-page-refresh actually runs. When those markers
    contradict the DOM-selected option, Oracle never accepted the selection
    (e.g. the dependent "Create Transaction: Invoice" header never changes), so
    the action must not be reported as successful.

    Only ADF selects are gated (``is_adf``); plain HTML ``<select>`` elements are
    left to the existing DOM-state postcondition. A missing/blank marker is never
    treated as a contradiction, so a genuinely-committed select is never failed.
    """
    if not isinstance(state, dict) or not state.get("is_adf"):
        return False

    selected_labels = [
        label for label in (state.get("selected_labels") or []) if _ptr_normalize_text(label)
    ]
    selected_value = _ptr_normalize_text(state.get("value"))
    selected_indexes = {str(index).strip() for index in (state.get("selected_indexes") or [])}

    title = _ptr_normalize_text(state.get("title"))
    afov = _ptr_normalize_text(state.get("afov"))

    # No committed markers to evaluate -> cannot prove a missing commit.
    if not title and not afov:
        return False

    title_confirms = bool(title) and any(_ptr_value_matches(label, title) for label in selected_labels)
    afov_confirms = bool(afov) and (afov == selected_value or afov in selected_indexes)

    # A marker that agrees with the DOM selection means ADF committed the value.
    if title_confirms or afov_confirms:
        return False

    # Markers are present but still reflect the prior value -> not committed.
    return True


def _ptr_select_option_postcondition(
    before_state: dict[str, Any],
    after_state: dict[str, Any],
    option_args: list[Any] | None,
    option_kwargs: dict[str, Any] | None,
) -> bool:
    expectations = _ptr_select_option_expectations(option_args, option_kwargs)

    def _normalized_strings(values: list[Any]) -> list[str]:
        return [str(value or "").strip() for value in values if str(value or "").strip()]

    def _match_all(expected: list[str], observed: list[str]) -> bool:
        if not expected:
            return True
        if not observed:
            return False
        return all(
            any(_ptr_value_matches(expected_value, observed_value) for observed_value in observed)
            for expected_value in expected
        )

    observed_values = _normalized_strings([after_state.get("value")] + list(after_state.get("selected_values") or []))
    observed_labels = _normalized_strings(list(after_state.get("selected_labels") or []))
    observed_indexes: list[int] = []
    for value in list(after_state.get("selected_indexes") or []):
        try:
            observed_indexes.append(int(value))
        except Exception:
            continue

    matched_explicit_expectation = False
    if expectations["values"]:
        matched_explicit_expectation = True
        if not _match_all([str(item) for item in expectations["values"]], observed_values):
            return False
    if expectations["labels"]:
        matched_explicit_expectation = True
        if not _match_all([str(item) for item in expectations["labels"]], observed_labels):
            return False
    if expectations["indexes"]:
        matched_explicit_expectation = True
        if not all(int(item) in observed_indexes for item in expectations["indexes"]):
            return False

    if matched_explicit_expectation:
        base_pass = True
    else:
        base_pass = after_state != before_state and bool(observed_values or observed_labels or observed_indexes)

    if not base_pass:
        return False

    # Oracle ADF selectOneChoice commit gate: a forced native-<select> value is
    # not success unless ADF actually committed it (title/_afov agree with the
    # DOM selection). Otherwise the UI shows the new option while the model and
    # dependent header stay on the old value -> the action failed.
    if _ptr_adf_select_commit_contradicted(after_state):
        return False

    return True


def _ptr_apply_select_option(locator: Locator, option_args: list[Any] | None, option_kwargs: dict[str, Any] | None) -> None:
    timeout_ms = _ptr_wait_ms("PTR_ACTION_TIMEOUT_MS", 3000)
    locator.wait_for(state="visible", timeout=timeout_ms)
    try:
        locator.scroll_into_view_if_needed(timeout=min(timeout_ms, 1000))
    except Exception:
        pass

    call_kwargs = dict(option_kwargs or {})
    call_kwargs.setdefault("timeout", timeout_ms)
    locator.select_option(*(option_args or []), **call_kwargs)


def _ptr_primary_selected_index(state: dict[str, Any]) -> int | None:
    for value in list((state or {}).get("selected_indexes") or []):
        try:
            return int(value)
        except Exception:
            continue
    return None


def _ptr_resolve_select_target(locator: Locator, option_args: list[Any] | None, option_kwargs: dict[str, Any] | None) -> dict[str, Any]:
    expectations = _ptr_select_option_expectations(option_args, option_kwargs)
    snapshot = _ptr_safe_locator_eval(
        locator,
        r"""(node, payload) => {
            const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
            const options = Array.from(node?.options || []);
            const nodeId = String(node?.id || "");
            return {
                is_adf: Boolean((node?.hasAttribute && node.hasAttribute("_afov")) || nodeId.indexOf("::") !== -1),
                options: options.map((option) => ({
                    index: Number(option?.index),
                    value: normalize(option?.value),
                    label: normalize(option?.label || option?.innerText || option?.textContent),
                })),
            };
        }""",
        expectations,
    )
    if not isinstance(snapshot, dict):
        return {}

    raw_options = snapshot.get("options") or []
    options: list[dict[str, Any]] = []
    for raw_option in raw_options if isinstance(raw_options, list) else []:
        if not isinstance(raw_option, dict):
            continue
        try:
            option_index = int(raw_option.get("index"))
        except Exception:
            continue
        options.append(
            {
                "index": option_index,
                "value": str(raw_option.get("value") or "").strip(),
                "label": str(raw_option.get("label") or "").strip(),
            }
        )

    def _pick(option: dict[str, Any]) -> dict[str, Any]:
        return {
            "index": int(option.get("index")),
            "value": str(option.get("value") or "").strip(),
            "label": str(option.get("label") or "").strip(),
        }

    for expected_index in expectations["indexes"]:
        try:
            wanted_index = int(expected_index)
        except Exception:
            continue
        for option in options:
            if int(option.get("index")) == wanted_index:
                return _pick(option)

    for expected_value in expectations["values"]:
        normalized_expected = str(expected_value or "").strip()
        if not normalized_expected:
            continue
        for option in options:
            if _ptr_value_matches(normalized_expected, option.get("value")):
                return _pick(option)

    for expected_label in expectations["labels"]:
        normalized_expected = str(expected_label or "").strip()
        if not normalized_expected:
            continue
        for option in options:
            if _ptr_value_matches(normalized_expected, option.get("label")):
                return _pick(option)

    # Oracle ADF selectOneChoice often stores zero-based internal values while
    # older recordings persist the visible ordinal as "1", "2", "3". Reuse the
    # live option order only when the control clearly looks like ADF and the
    # option values are the canonical zero-based sequence.
    is_adf = bool(snapshot.get("is_adf"))
    if is_adf and len(expectations["values"]) == 1 and not expectations["labels"] and not expectations["indexes"]:
        try:
            ordinal = int(str(expectations["values"][0] or "").strip())
        except Exception:
            ordinal = None
        option_values = [str(option.get("value") or "").strip() for option in options]
        looks_zero_based = option_values == [str(index) for index in range(len(options))]
        if ordinal is not None and looks_zero_based and 1 <= ordinal <= len(options):
            return _pick(options[ordinal - 1])

    return {}


def _ptr_resolve_select_option_by_committed_marker(locator: Locator, state: dict[str, Any]) -> dict[str, Any]:
    snapshot = _ptr_safe_locator_eval(
        locator,
        r"""(node) => {
            const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
            const options = Array.from(node?.options || []);
            return {
                options: options.map((option) => ({
                    index: Number(option?.index),
                    value: normalize(option?.value),
                    label: normalize(option?.label || option?.innerText || option?.textContent),
                })),
            };
        }""",
    )
    if not isinstance(snapshot, dict):
        return {}

    raw_options = snapshot.get("options") or []
    options: list[dict[str, Any]] = []
    for raw_option in raw_options if isinstance(raw_options, list) else []:
        if not isinstance(raw_option, dict):
            continue
        try:
            option_index = int(raw_option.get("index"))
        except Exception:
            continue
        options.append(
            {
                "index": option_index,
                "value": str(raw_option.get("value") or "").strip(),
                "label": str(raw_option.get("label") or "").strip(),
            }
        )

    committed_afov = _ptr_normalize_text((state or {}).get("afov"))
    if committed_afov:
        for option in options:
            option_value = str(option.get("value") or "").strip()
            if option_value and _ptr_value_matches(committed_afov, option_value):
                return _ptr_clone_json_value(option)
            if committed_afov == str(option.get("index")):
                return _ptr_clone_json_value(option)

    committed_title = _ptr_normalize_text((state or {}).get("title"))
    if committed_title:
        for option in options:
            option_label = str(option.get("label") or "").strip()
            if option_label and _ptr_value_matches(committed_title, option_label):
                return _ptr_clone_json_value(option)

    return {}


def _ptr_oracle_adf_commit_events(locator: Locator) -> bool:
    result = _ptr_safe_locator_eval(
        locator,
        r"""(node) => {
            if (!node) return false;
            const fire = (name) => node.dispatchEvent(new Event(name, { bubbles: true, cancelable: true }));
            try {
                node.focus?.();
            } catch (error) {
                return false;
            }
            fire("input");
            fire("change");
            fire("blur");
            fire("focusout");
            return true;
        }""",
    )
    return bool(result)


def _ptr_try_oracle_adf_component_commit(locator: Locator, target: dict[str, Any]) -> dict[str, Any] | None:
    target_value = str(target.get("value") or "").strip()
    target_label = str(target.get("label") or "").strip()
    target_index = target.get("index")
    if isinstance(target_index, bool):
        target_index = int(target_index)
    elif not isinstance(target_index, int):
        try:
            target_index = int(target_index)
        except Exception:
            target_index = -1

    result = _ptr_safe_locator_eval(
        locator,
        r"""(node, payload) => {
            const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
            if (!node) return { ok: false, reason: "missing_node" };
            const contentId = String(node.id || "").trim();
            const baseId = contentId.endsWith("::content") ? contentId.slice(0, -"::content".length) : contentId;
            const win = node.ownerDocument?.defaultView || window;
            const adfPage = win?.AdfPage?.PAGE || null;
            const component = (
                adfPage?.findComponentByAbsoluteId?.(baseId)
                || adfPage?.findComponent?.(baseId)
                || null
            );
            if (!component) {
                return { ok: false, reason: "missing_component", base_id: baseId, content_id: contentId };
            }

            const targetValue = normalize(payload?.value);
            const targetIndex = Number(payload?.index);
            const used = [];

            try { node.focus?.(); used.push("focus"); } catch (error) {}
            try {
                if (Number.isInteger(targetIndex) && node.options && targetIndex >= 0 && targetIndex < node.options.length) {
                    node.selectedIndex = targetIndex;
                    used.push("selectedIndex");
                } else if (targetValue) {
                    node.value = targetValue;
                    used.push("value");
                }
            } catch (error) {}

            const fire = (name) => {
                try {
                    node.dispatchEvent(new Event(name, { bubbles: true, cancelable: true }));
                    used.push(`event:${name}`);
                } catch (error) {}
            };

            fire("input");
            fire("change");

            try {
                if (typeof component.setValue === "function" && targetValue) {
                    component.setValue(targetValue);
                    used.push("component.setValue");
                }
            } catch (error) {}

            try {
                if (typeof component.processUpdates === "function") {
                    component.processUpdates(node);
                    used.push("component.processUpdates");
                }
            } catch (error) {}

            try {
                if (typeof component._handleBlur === "function") {
                    component._handleBlur();
                    used.push("component._handleBlur");
                }
            } catch (error) {}

            try {
                if (win?.AdfCustomEvent?.queue) {
                    win.AdfCustomEvent.queue(component, "valueChange", { value: targetValue }, true);
                    used.push("AdfCustomEvent.queue");
                }
            } catch (error) {}

            fire("blur");
            fire("focusout");

            return {
                ok: true,
                base_id: baseId,
                content_id: contentId,
                used,
            };
        }""",
        {
            "value": target_value,
            "index": target_index,
        },
    )
    if not isinstance(result, dict) or not result.get("ok"):
        return None

    return {
        "strategy_name": "oracle_adf_select_component_commit",
        "component_id": str(result.get("base_id") or "").strip(),
        "content_id": str(result.get("content_id") or "").strip(),
        "used": _ptr_clone_json_value(result.get("used") or []),
        "target_index": target_index,
        "target_value": target_value,
        "target_label": target_label,
    }


def _ptr_reset_select_to_index(locator: Locator, index: int) -> bool:
    result = _ptr_safe_locator_eval(
        locator,
        r"""(node, payload) => {
            if (!node || !node.options) return false;
            const nextIndex = Number(payload?.index);
            if (!Number.isInteger(nextIndex) || nextIndex < 0 || nextIndex >= node.options.length) return false;
            node.selectedIndex = nextIndex;
            return true;
        }""",
        {"index": index},
    )
    return bool(result)


def _ptr_try_commit_oracle_adf_select(
    locator: Locator,
    current_page: Page,
    before_state: dict[str, Any],
    option_args: list[Any] | None,
    option_kwargs: dict[str, Any] | None,
) -> dict[str, Any] | None:
    contradicted_state = _ptr_select_option_state(locator)
    if not _ptr_adf_select_commit_contradicted(contradicted_state):
        return None

    timeout_ms = _ptr_wait_ms("PTR_ACTION_TIMEOUT_MS", 3000)
    wait_ms = _ptr_wait_ms("PTR_ADF_SELECT_COMMIT_WAIT_MS", 250)
    target = _ptr_resolve_select_target(locator, option_args, option_kwargs)
    if not target:
        def _compact_text(value: Any) -> str:
            return " ".join(str(value or "").split())

        contradicted_index = _ptr_primary_selected_index(contradicted_state)
        contradicted_value = _compact_text(contradicted_state.get("value"))
        selected_values = [
            _compact_text(value) for value in list(contradicted_state.get("selected_values") or [])
            if _compact_text(value)
        ]
        selected_labels = [
            _compact_text(value) for value in list(contradicted_state.get("selected_labels") or [])
            if _compact_text(value)
        ]
        fallback_value = contradicted_value or (selected_values[0] if selected_values else "")
        fallback_label = selected_labels[0] if selected_labels else ""
        if contradicted_index is not None or fallback_value or fallback_label:
            target = {
                "index": contradicted_index if contradicted_index is not None else -1,
                "value": fallback_value,
                "label": fallback_label,
            }
    before_index = _ptr_primary_selected_index(before_state)
    inferred_committed_option: dict[str, Any] = {}
    if before_index is None:
        inferred_committed_option = _ptr_resolve_select_option_by_committed_marker(locator, contradicted_state)
        inferred_index = inferred_committed_option.get("index")
        if isinstance(inferred_index, bool):
            before_index = int(inferred_index)
        elif isinstance(inferred_index, int):
            before_index = inferred_index
        else:
            try:
                before_index = int(inferred_index)
            except Exception:
                before_index = None
    target_index = target.get("index")
    if isinstance(target_index, bool):
        target_index = int(target_index)
    elif not isinstance(target_index, int):
        try:
            target_index = int(target_index)
        except Exception:
            target_index = None

    if before_index is not None and target_index is not None and before_index != target_index:
        try:
            locator.focus(timeout=timeout_ms)
        except Exception:
            pass
        if _ptr_reset_select_to_index(locator, before_index):
            direction = "ArrowDown" if target_index > before_index else "ArrowUp"
            for _ in range(abs(target_index - before_index)):
                locator.press(direction, timeout=timeout_ms)
            try:
                locator.press("Tab", timeout=timeout_ms)
            except Exception:
                pass
            current_page.wait_for_timeout(wait_ms)
            _ptr_wait_for_field_processing(
                current_page,
                env_name="PTR_DROPDOWN_CHANGE_PROCESSING_WAIT_MS",
                default_ms=5000,
            )
            keyboard_state = _ptr_select_option_state(locator)
            if _ptr_select_option_postcondition(before_state, keyboard_state, option_args, option_kwargs):
                details = {
                    "strategy_name": "oracle_adf_select_keyboard_commit",
                    "target_index": target_index,
                    "target_value": str(target.get("value") or "").strip(),
                    "target_label": str(target.get("label") or "").strip(),
                }
                if inferred_committed_option:
                    details["committed_index_source"] = "adf_marker"
                    details["committed_value"] = str(inferred_committed_option.get("value") or "").strip()
                    details["committed_label"] = str(inferred_committed_option.get("label") or "").strip()
                return details

    component_details = _ptr_try_oracle_adf_component_commit(locator, target)
    if component_details:
        current_page.wait_for_timeout(wait_ms)
        _ptr_wait_for_field_processing(
            current_page,
            env_name="PTR_DROPDOWN_CHANGE_PROCESSING_WAIT_MS",
            default_ms=5000,
        )
        component_state = _ptr_select_option_state(locator)
        if _ptr_select_option_postcondition(before_state, component_state, option_args, option_kwargs):
            if inferred_committed_option:
                component_details["committed_index_source"] = "adf_marker"
                component_details["committed_value"] = str(inferred_committed_option.get("value") or "").strip()
                component_details["committed_label"] = str(inferred_committed_option.get("label") or "").strip()
            return component_details

    if _ptr_oracle_adf_commit_events(locator):
        current_page.wait_for_timeout(wait_ms)
        _ptr_wait_for_field_processing(
            current_page,
            env_name="PTR_DROPDOWN_CHANGE_PROCESSING_WAIT_MS",
            default_ms=5000,
        )
        event_state = _ptr_select_option_state(locator)
        if _ptr_select_option_postcondition(before_state, event_state, option_args, option_kwargs):
            return {
                "strategy_name": "oracle_adf_select_event_commit",
                "target_index": target_index if target_index is not None else -1,
                "target_value": str(target.get("value") or "").strip(),
                "target_label": str(target.get("label") or "").strip(),
            }

    return None


def _ptr_oracle_label_value_matches(page: Page | None, label: str, expected_value: str) -> bool:
    normalized_label = _ptr_normalize_text(label)
    normalized_value = _ptr_normalize_text(expected_value)
    if not normalized_label or not normalized_value:
        return False

    result = _ptr_safe_page_eval(
        page,
        r"""(payload) => {
            const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
            const targetLabel = normalize(payload?.label);
            const targetValue = normalize(payload?.value);
            if (!targetLabel || !targetValue) return false;

            const isVisible = (node) => {
                if (!node) return false;
                const style = window.getComputedStyle(node);
                if (!style) return false;
                if (style.display === "none" || style.visibility === "hidden") return false;
                const rect = node.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const textOf = (node) => normalize(node?.innerText || node?.textContent || "");
            const matchesValue = (text) => {
                if (!text) return false;
                return text === targetValue || text.includes(targetValue) || targetValue.includes(text);
            };
            const containsLabel = (text) => {
                if (!text) return false;
                return text === targetLabel || text.includes(targetLabel) || targetLabel.includes(text);
            };
            const controlValues = (node) => {
                if (!node) return [];
                const values = [];
                const append = (value) => {
                    const normalized = normalize(value);
                    if (normalized) values.push(normalized);
                };
                append(node.value);
                append(node.getAttribute?.("value"));
                append(node.getAttribute?.("aria-valuetext"));
                append(node.getAttribute?.("aria-label"));
                const selectedOptions = Array.from(node.selectedOptions || []);
                for (const option of selectedOptions) {
                    append(option?.label || option?.innerText || option?.textContent);
                    append(option?.value);
                }
                append(node.innerText || node.textContent);
                return Array.from(new Set(values.filter(Boolean)));
            };
            const controlMatches = (node) => controlValues(node).some(matchesValue);
            const regionMatches = (text) => {
                if (!text) return false;
                if (!containsLabel(text)) return false;
                if (matchesValue(text)) return true;
                const withoutLabel = normalize(text.replace(payload?.label || "", " "));
                if (matchesValue(withoutLabel)) return true;
                return false;
            };
            const nearbyControlMatches = (node) => {
                if (!node) return false;
                const controls = [];
                const add = (candidate) => {
                    if (!candidate || controls.includes(candidate)) return;
                    controls.push(candidate);
                };
                const pushFrom = (root) => {
                    if (!root || !root.querySelectorAll) return;
                    for (const candidate of root.querySelectorAll("select, input, textarea, [role='combobox'], oj-select-single, oj-c-select-single")) {
                        add(candidate);
                    }
                };
                const forId = normalize(node.getAttribute?.("for"));
                if (forId) add(document.getElementById(forId));
                pushFrom(node);
                pushFrom(node.parentElement);
                pushFrom(node.closest("tr, .oj-formlayout-row, .oj-flex, .oj-sm-flex, .oj-form, .AFStretchWidth"));
                pushFrom(node.parentElement?.parentElement);
                return controls.some((candidate) => isVisible(candidate) && controlMatches(candidate));
            };
            const candidateSelectors = "label, oj-label, span, div, td, th";

            for (const node of document.querySelectorAll(candidateSelectors)) {
                if (!isVisible(node)) continue;
                const labelText = textOf(node);
                if (!labelText || !containsLabel(labelText)) continue;
                if (regionMatches(labelText)) return true;
                if (nearbyControlMatches(node)) return true;

                const siblingCandidates = [
                    node.nextElementSibling,
                    node.parentElement?.nextElementSibling,
                ].filter(Boolean);
                for (const candidate of siblingCandidates) {
                    const candidateText = textOf(candidate);
                    if (matchesValue(candidateText)) return true;
                    if (controlMatches(candidate)) return true;
                }

                const parent = node.parentElement;
                if (parent) {
                    if (regionMatches(textOf(parent))) return true;
                    const rowCandidates = Array.from(parent.children || [])
                        .filter((child) => child !== node)
                        .map((child) => textOf(child))
                        .filter(Boolean);
                    if (rowCandidates.some(matchesValue)) return true;
                }

                const ownRow = node.closest("tr, .oj-formlayout-row, .oj-flex, .oj-sm-flex, .oj-form, .AFStretchWidth");
                if (ownRow) {
                    if (regionMatches(textOf(ownRow))) return true;
                    if (nearbyControlMatches(ownRow)) return true;
                    const rowTextCandidates = Array.from(ownRow.querySelectorAll("span, div, td, th, a"))
                        .filter((candidate) => candidate !== node && isVisible(candidate))
                        .map((candidate) => textOf(candidate))
                        .filter(Boolean);
                    if (rowTextCandidates.some(matchesValue)) return true;
                }

                const grandParent = parent?.parentElement;
                if (grandParent && regionMatches(textOf(grandParent))) return true;
            }
            return false;
        }""",
        {"label": label, "value": expected_value},
    )
    return bool(result)


def _ptr_try_oracle_searchselect_select_option_recovery(
    locator: Locator,
    current_page: Page,
    label: str,
    option_args: list[Any] | None,
    option_kwargs: dict[str, Any] | None,
    resolved_target: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    metadata = _ptr_extract_locator_metadata(locator)
    target = _ptr_clone_json_value(resolved_target or {}) or _ptr_resolve_select_target(locator, option_args, option_kwargs)
    expectations = _ptr_select_option_expectations(option_args, option_kwargs)
    debug_trace: dict[str, Any] = {
        "label": label,
        "metadata": _ptr_clone_json_value(metadata),
        "target": _ptr_clone_json_value(target),
        "expectations": _ptr_clone_json_value(expectations),
        "trigger_attempts": [],
    }

    option_name = str(target.get("label") or "").strip()
    if not option_name:
        explicit_labels = [str(item or "").strip() for item in expectations.get("labels") or [] if str(item or "").strip()]
        explicit_values = [str(item or "").strip() for item in expectations.get("values") or [] if str(item or "").strip()]
        option_name = (explicit_labels[0] if explicit_labels else "") or (explicit_values[0] if explicit_values else "")
    if not option_name:
        debug_trace["status"] = "skipped_missing_option_name"
        _ptr_set_debug_detail("oracle_searchselect_recovery", debug_trace)
        return None

    fill_value = str(target.get("label") or "").strip() or option_name
    trigger_candidates: list[tuple[str, Locator]] = []
    direct_tag = str(metadata.get("tag") or "").strip().lower()
    direct_role = str(metadata.get("role") or "").strip().lower()
    oracle_host_tag = str(metadata.get("oracle_host_tag") or "").strip().lower()

    if (
        direct_role == "combobox"
        or direct_tag in {"oj-select-single", "oj-c-select-single"}
        or oracle_host_tag in {"oj-select-single", "oj-c-select-single"}
    ):
        trigger_candidates.append(("oracle_searchselect_direct", locator))

    get_by_role = getattr(current_page, "get_by_role", None)
    if callable(get_by_role):
        try:
            trigger_candidates.append(("oracle_searchselect_role_combobox", current_page.get_by_role("combobox", name=label, exact=True)))
        except Exception:
            pass

    seen_trigger_ids: set[int] = set()
    last_error: Exception | None = None
    for strategy_name, candidate in trigger_candidates:
        attempt: dict[str, Any] = {"strategy_name": strategy_name}
        try:
            resolved = candidate.first if hasattr(candidate, "first") else candidate
        except Exception:
            resolved = candidate
        if resolved is None:
            attempt["status"] = "empty_candidate"
            debug_trace["trigger_attempts"].append(attempt)
            continue
        resolved_id = id(resolved)
        if resolved_id in seen_trigger_ids:
            attempt["status"] = "duplicate_candidate"
            debug_trace["trigger_attempts"].append(attempt)
            continue
        seen_trigger_ids.add(resolved_id)
        label_match = _ptr_ai_locator_matches_label(resolved, label)
        attempt["label_match"] = label_match
        if not label_match:
            attempt["status"] = "label_mismatch"
            debug_trace["trigger_attempts"].append(attempt)
            continue
        try:
            option_locator = current_page.get_by_text(option_name, exact=True)
            _ptr_select_search_trigger_option(
                resolved,
                option_locator,
                current_page,
                label,
                option_name,
                option_exact=True,
                fill_value=fill_value,
                allow_repair=False,
            )
            attempt["status"] = "success"
            debug_trace["trigger_attempts"].append(attempt)
            debug_trace["status"] = "success"
            _ptr_set_debug_detail("oracle_searchselect_recovery", debug_trace)
            return {
                "strategy_name": strategy_name,
                "target_index": int(target.get("index")) if isinstance(target.get("index"), int) else -1,
                "target_value": str(target.get("value") or "").strip(),
                "target_label": str(target.get("label") or "").strip(),
                "option_name": option_name,
                "fill_value": fill_value,
            }
        except Exception as exc:
            last_error = exc
            attempt["status"] = "failed"
            attempt["error"] = _ptr_trim_debug_text(exc, 400)
            debug_trace["trigger_attempts"].append(attempt)

    debug_trace["status"] = "failed"
    if last_error is not None:
        debug_trace["last_error"] = _ptr_trim_debug_text(last_error, 400)
    _ptr_set_debug_detail("oracle_searchselect_recovery", debug_trace)
    return None


def _ptr_select_option_target(
    locator: Locator,
    current_page: Page,
    label: str,
    option_args: list[Any] | None,
    option_kwargs: dict[str, Any] | None,
) -> None:
    _ptr_register_page(current_page)
    target_description = json.dumps(
        _ptr_clone_json_value(
            {
                "args": option_args or [],
                "kwargs": option_kwargs or {},
            }
        ),
        ensure_ascii=False,
    )
    debug_trace: dict[str, Any] = {
        "label": label,
        "target_description": _ptr_clone_json_value({"args": option_args or [], "kwargs": option_kwargs or {}}),
    }
    _ptr_set_debug_detail("select_option_target", debug_trace)
    initial_target = _ptr_resolve_select_target(locator, option_args, option_kwargs)
    debug_trace["initial_target"] = _ptr_clone_json_value(initial_target)
    _ptr_set_debug_detail("select_option_target", debug_trace)

    before_state = _ptr_select_option_state(locator)
    debug_trace["before_state"] = _ptr_clone_json_value(before_state)
    _ptr_set_debug_detail("select_option_target", debug_trace)
    try:
        _ptr_apply_select_option(locator, option_args, option_kwargs)
        _ptr_wait_for_field_processing(
            current_page,
            env_name="PTR_DROPDOWN_CHANGE_PROCESSING_WAIT_MS",
            default_ms=5000,
        )
        after_state = _ptr_select_option_state(locator)
        direct_postcondition = _ptr_select_option_postcondition(before_state, after_state, option_args, option_kwargs)
        debug_trace["after_state"] = _ptr_clone_json_value(after_state)
        debug_trace["direct_postcondition_passed"] = direct_postcondition
        after_state_contradicted = _ptr_adf_select_commit_contradicted(after_state)
        debug_trace["adf_commit_contradicted"] = after_state_contradicted
        _ptr_set_debug_detail("select_option_target", debug_trace)
        if direct_postcondition:
            return
        oracle_recovery = None
        if after_state_contradicted:
            oracle_recovery = _ptr_try_commit_oracle_adf_select(
                locator,
                current_page,
                before_state,
                option_args,
                option_kwargs,
            )
            debug_trace["oracle_adf_commit_recovery"] = _ptr_clone_json_value(oracle_recovery)
            _ptr_set_debug_detail("select_option_target", debug_trace)
            if oracle_recovery:
                _ptr_set_recovery_record(
                    "oracle_handler",
                    "oracle_adf_select_commit",
                    "oracle_adf_select_commit",
                    oracle_recovery,
                )
                _ptr_store_experience_episode(
                    action_type="select_option_target",
                    label=label,
                    page=current_page,
                    locator=locator,
                    error=None,
                    status="success",
                    postcondition_kind="option_selected",
                    postcondition_passed=True,
                )
                return
        semantic_target = initial_target or _ptr_resolve_select_target(locator, option_args, option_kwargs)
        semantic_label = str(semantic_target.get("label") or "").strip()
        debug_trace["semantic_target"] = _ptr_clone_json_value(semantic_target)
        semantic_match = (
            not after_state_contradicted
            and bool(semantic_label)
            and _ptr_oracle_label_value_matches(current_page, label, semantic_label)
        )
        debug_trace["semantic_label_match"] = semantic_match
        _ptr_set_debug_detail("select_option_target", debug_trace)
        if semantic_match:
            _ptr_set_recovery_record(
                "oracle_handler",
                "oracle_label_value_already_selected",
                "oracle_label_value_already_selected",
                {
                    "target_value": str(semantic_target.get("value") or "").strip(),
                    "target_label": semantic_label,
                },
            )
            _ptr_store_experience_episode(
                action_type="select_option_target",
                label=label,
                page=current_page,
                locator=locator,
                error=None,
                status="success",
                postcondition_kind="option_selected",
                postcondition_passed=True,
            )
            return
        if "oracle_adf_commit_recovery" not in debug_trace:
            debug_trace["oracle_adf_commit_recovery"] = None
            _ptr_set_debug_detail("select_option_target", debug_trace)
        oracle_searchselect_recovery = _ptr_try_oracle_searchselect_select_option_recovery(
            locator,
            current_page,
            label,
            option_args,
            option_kwargs,
            resolved_target=initial_target,
        )
        debug_trace["oracle_searchselect_recovery"] = _ptr_clone_json_value(oracle_searchselect_recovery)
        _ptr_set_debug_detail("select_option_target", debug_trace)
        if oracle_searchselect_recovery:
            _ptr_set_recovery_record(
                "oracle_handler",
                "oracle_searchselect_select_option",
                "oracle_searchselect_select_option",
                oracle_searchselect_recovery,
            )
            _ptr_store_experience_episode(
                action_type="select_option_target",
                label=label,
                page=current_page,
                locator=locator,
                error=None,
                status="success",
                postcondition_kind="option_selected",
                postcondition_passed=True,
            )
            return
        debug_trace["status"] = "direct_failed"
        _ptr_set_debug_detail("select_option_target", debug_trace)
        raise RuntimeError(
            f'Select "{label}" did not reflect the requested option selection {target_description}.'
        )
    except Exception as direct_exc:
        debug_trace["direct_error"] = _ptr_trim_debug_text(direct_exc, 400)
        _ptr_set_debug_detail("select_option_target", debug_trace)
        last_error: Exception = direct_exc
        for strategy_name, experience_locator, episode in _ptr_experience_repair_locators(
            current_page,
            "select_option_target",
            label,
            direct_exc,
            locator=locator,
        ):
            try:
                _ptr_record_strategy_attempt(strategy_name)
                before_experience = _ptr_select_option_state(experience_locator)
                _ptr_apply_select_option(experience_locator, option_args, option_kwargs)
                _ptr_wait_for_field_processing(
                    current_page,
                    env_name="PTR_DROPDOWN_CHANGE_PROCESSING_WAIT_MS",
                    default_ms=5000,
                )
                after_experience = _ptr_select_option_state(experience_locator)
                if _ptr_select_option_postcondition(
                    before_experience,
                    after_experience,
                    option_args,
                    option_kwargs,
                ):
                    debug_trace["experience_reuse"] = {
                        "strategy_name": strategy_name,
                        "status": "success",
                    }
                    _ptr_set_debug_detail("select_option_target", debug_trace)
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
                        action_type="select_option_target",
                        label=label,
                        page=current_page,
                        locator=experience_locator,
                        error=direct_exc,
                        status="success",
                        postcondition_kind="option_selected",
                        postcondition_passed=True,
                    )
                    return
                if _ptr_try_commit_oracle_adf_select(
                    experience_locator,
                    current_page,
                    before_experience,
                    option_args,
                    option_kwargs,
                ):
                    debug_trace["experience_reuse"] = {
                        "strategy_name": strategy_name,
                        "status": "oracle_commit_success",
                    }
                    _ptr_set_debug_detail("select_option_target", debug_trace)
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
                        action_type="select_option_target",
                        label=label,
                        page=current_page,
                        locator=experience_locator,
                        error=direct_exc,
                        status="success",
                        postcondition_kind="option_selected",
                        postcondition_passed=True,
                    )
                    return
                last_error = RuntimeError(
                    f'Experience strategy "{strategy_name}" did not satisfy select_option for "{label}".'
                )
                debug_trace["experience_reuse"] = {
                    "strategy_name": strategy_name,
                    "status": "postcondition_failed",
                    "error": _ptr_trim_debug_text(last_error, 300),
                }
                _ptr_set_debug_detail("select_option_target", debug_trace)
            except Exception as exc:
                last_error = exc
                debug_trace["experience_reuse"] = {
                    "strategy_name": strategy_name,
                    "status": "failed",
                    "error": _ptr_trim_debug_text(exc, 300),
                }
                _ptr_set_debug_detail("select_option_target", debug_trace)

        def _execute_ai_select_locator(strategy_name: str, ai_locator: Locator, ai_strategy: dict[str, Any]) -> bool:
            before_ai = _ptr_select_option_state(ai_locator)
            _ptr_apply_select_option(ai_locator, option_args, option_kwargs)
            _ptr_wait_for_field_processing(
                current_page,
                env_name="PTR_DROPDOWN_CHANGE_PROCESSING_WAIT_MS",
                default_ms=5000,
            )
            after_ai = _ptr_select_option_state(ai_locator)
            if _ptr_select_option_postcondition(
                before_ai,
                after_ai,
                option_args,
                option_kwargs,
            ):
                return True
            return bool(
                _ptr_try_commit_oracle_adf_select(
                    ai_locator,
                    current_page,
                    before_ai,
                    option_args,
                    option_kwargs,
                )
            )

        ai_result, last_error = _ptr_execute_ai_repair_rounds(
            current_page=current_page,
            helper="select_option_target",
            label=label,
            last_error=last_error,
            value=target_description,
            locator=locator,
            postcondition_kind="option_selected",
            failure_message=lambda strategy_name: f'AI strategy "{strategy_name}" did not satisfy select_option for "{label}".',
            execute_locator=_execute_ai_select_locator,
        )
        if ai_result is not None:
            strategy_name, ai_locator, ai_strategy = ai_result
            debug_trace["ai_repair"] = {
                "strategy_name": strategy_name,
                "status": "success",
                "locator_strategy": _ptr_clone_json_value(ai_strategy),
            }
            _ptr_set_debug_detail("select_option_target", debug_trace)
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
                action_type="select_option_target",
                label=label,
                page=current_page,
                locator=ai_locator,
                error=direct_exc,
                status="success",
                postcondition_kind="option_selected",
                postcondition_passed=True,
            )
            return
        debug_trace["ai_repair"] = {
            "status": "failed",
            "error": _ptr_trim_debug_text(last_error, 300),
        }
        debug_trace["status"] = "failed"
        _ptr_set_debug_detail("select_option_target", debug_trace)
        raise RuntimeError(
            f'Unable to apply select_option for "{label}" with target {target_description}.'
        ) from last_error


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
    debug_trace = _ptr_update_debug_detail(
        "click_combobox",
        {
            "label": label,
            "status": "strict_attempt",
            "before": _ptr_debug_observation_summary(before),
            "experience_attempts": [],
        },
    )
    try:
        _ptr_strict_click(locator)
        current_page.wait_for_timeout(_ptr_wait_ms("PTR_COMBOBOX_OPEN_WAIT_MS", 350))
        after = _ptr_observe(current_page, locator)
        if _ptr_combobox_open_postcondition(before, after):
            debug_trace["direct_attempt"] = {
                "status": "validated",
                "after": _ptr_debug_observation_summary(after),
            }
            debug_trace["resolved_by"] = "strict"
            debug_trace["status"] = "success"
            _ptr_set_debug_detail("click_combobox", debug_trace)
            return
        raise RuntimeError(f'Combobox "{label}" did not open.')
    except Exception as direct_exc:
        last_error: Exception = direct_exc
        debug_trace["direct_attempt"] = {
            "status": "failed",
            "error": _ptr_trim_debug_text(direct_exc, 320),
        }
        oracle_strategy_name = _ptr_try_open_oracle_select_single_with_keyboard(current_page, locator, direct_exc)
        if oracle_strategy_name:
            debug_trace["oracle_select_single_keyboard_open"] = {
                "status": "validated",
                "strategy_name": oracle_strategy_name,
            }
            debug_trace["resolved_by"] = "oracle_select_single_keyboard_open"
            debug_trace["status"] = "success"
            _ptr_set_debug_detail("click_combobox", debug_trace)
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
        debug_trace["oracle_select_single_keyboard_open"] = {"status": "not_applied"}
        for strategy_name, experience_locator, episode in _ptr_experience_repair_locators(current_page, "click_combobox", label, direct_exc, locator=locator):
            try:
                _ptr_record_strategy_attempt(strategy_name)
                before_experience = _ptr_observe(current_page, experience_locator)
                _ptr_strict_click(experience_locator)
                current_page.wait_for_timeout(_ptr_wait_ms("PTR_COMBOBOX_OPEN_WAIT_MS", 350))
                after_experience = _ptr_observe(current_page, experience_locator)
                if _ptr_combobox_open_postcondition(before_experience, after_experience):
                    experience_attempts = debug_trace.setdefault("experience_attempts", [])
                    if isinstance(experience_attempts, list):
                        experience_attempts.append(
                            {
                                "strategy_name": strategy_name,
                                "status": "validated",
                                "episode_id": str(episode.get("episode_id") or "").strip(),
                                "retrieval_score": int(episode.get("retrieval_score") or 0),
                                "after": _ptr_debug_observation_summary(after_experience),
                            }
                        )
                    debug_trace["resolved_by"] = "experience_reuse"
                    debug_trace["status"] = "success"
                    _ptr_set_debug_detail("click_combobox", debug_trace)
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
                experience_attempts = debug_trace.setdefault("experience_attempts", [])
                if isinstance(experience_attempts, list):
                    experience_attempts.append(
                        {
                            "strategy_name": strategy_name,
                            "status": "postcondition_failed",
                            "episode_id": str(episode.get("episode_id") or "").strip(),
                            "retrieval_score": int(episode.get("retrieval_score") or 0),
                            "error": _ptr_trim_debug_text(last_error, 320),
                        }
                    )
            except Exception as exc:
                last_error = exc
                experience_attempts = debug_trace.setdefault("experience_attempts", [])
                if isinstance(experience_attempts, list):
                    experience_attempts.append(
                        {
                            "strategy_name": strategy_name,
                            "status": "failed",
                            "episode_id": str(episode.get("episode_id") or "").strip(),
                            "retrieval_score": int(episode.get("retrieval_score") or 0),
                            "error": _ptr_trim_debug_text(exc, 320),
                        }
                    )
        if not debug_trace.get("experience_attempts"):
            debug_trace["experience_attempts"] = [{"status": "no_candidates"}]
        def _execute_ai_combobox_locator(strategy_name: str, ai_locator: Locator, ai_strategy: dict[str, Any]) -> bool:
            before_ai = _ptr_observe(current_page, ai_locator)
            _ptr_strict_click(ai_locator)
            current_page.wait_for_timeout(_ptr_wait_ms("PTR_COMBOBOX_OPEN_WAIT_MS", 350))
            after_ai = _ptr_observe(current_page, ai_locator)
            return _ptr_combobox_open_postcondition(before_ai, after_ai)

        ai_result, last_error = _ptr_execute_ai_repair_rounds(
            current_page=current_page,
            helper="click_combobox",
            label=label,
            last_error=last_error,
            locator=locator,
            postcondition_kind="dialog_opened",
            failure_message=lambda strategy_name: f'AI strategy "{strategy_name}" did not open combobox "{label}".',
            execute_locator=_execute_ai_combobox_locator,
        )
        if ai_result is not None:
            strategy_name, ai_locator, ai_strategy = ai_result
            debug_trace["ai_repair"] = {
                "status": "validated",
                "strategy_name": strategy_name,
                "locator_strategy": _ptr_clone_json_value(ai_strategy),
            }
            debug_trace["resolved_by"] = "ai_locator_repair"
            debug_trace["status"] = "success"
            _ptr_set_debug_detail("click_combobox", debug_trace)
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
            return
        debug_trace["ai_repair"] = {
            "status": "failed",
            "error": _ptr_trim_debug_text(last_error, 320),
        }
        debug_trace["status"] = "failed"
        debug_trace["final_error"] = _ptr_trim_debug_text(last_error, 320)
        _ptr_set_debug_detail("click_combobox", debug_trace)
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


def _ptr_check_target(locator: Locator, current_page: Page, label: str) -> None:
    _ptr_set_checkbox_state(locator, current_page, label, True)


def _ptr_uncheck_target(locator: Locator, current_page: Page, label: str) -> None:
    _ptr_set_checkbox_state(locator, current_page, label, False)


def _ptr_click_numeric_button_target(locator: Locator, current_page: Page, label: str) -> None:
    _ptr_register_page(current_page)
    _ptr_click_with_candidates(
        current_page,
        label,
        locator,
        "click_numeric_button_target",
        _ptr_generic_click_postcondition,
    )


def _ptr_click_table_field(locator: Locator, current_page: Page, label: str) -> None:
    _ptr_register_page(current_page)
    before = _ptr_observe(current_page, locator)
    _ptr_strict_click(locator)
    current_page.wait_for_timeout(_ptr_wait_ms("PTR_POST_CLICK_WAIT_MS", 250))
    after = _ptr_observe(current_page, locator)
    if _ptr_table_field_click_postcondition(before, after):
        return
    raise RuntimeError(f'Table field "{label}" did not change focus or control state.')


def _ptr_click_text_target(locator: Locator, current_page: Page, label: str) -> None:
    _ptr_register_page(current_page)
    _ptr_click_with_candidates(
        current_page,
        label,
        locator,
        "click_text_target",
        _ptr_generic_click_postcondition,
    )


def _ptr_dblclick_text_target(locator: Locator, current_page: Page, label: str) -> None:
    _ptr_register_page(current_page)
    before = _ptr_observe(current_page, locator)
    _ptr_strict_dblclick(locator)
    current_page.wait_for_timeout(_ptr_wait_ms("PTR_POST_CLICK_WAIT_MS", 250))
    after = _ptr_observe(current_page, locator)
    if _ptr_generic_click_postcondition(before, after):
        return
    raise RuntimeError(f'Double-click target "{label}" did not change page or control state.')


def _ptr_click_listbox_option(locator: Locator, current_page: Page, label: str) -> None:
    _ptr_click_text_target(locator, current_page, label)


def _ptr_click_table_row(locator: Locator, current_page: Page, label: str) -> None:
    _ptr_register_page(current_page)
    before = _ptr_observe(current_page, locator)
    _ptr_strict_click(locator)
    current_page.wait_for_timeout(_ptr_wait_ms("PTR_POST_CLICK_WAIT_MS", 250))
    after = _ptr_observe(current_page, locator)
    if _ptr_table_row_postcondition(before, after):
        return
    raise RuntimeError(f'Table row "{label}" did not change row selection state.')


def _ptr_select_combobox_option(trigger: Locator, option: Locator, current_page: Page, label: str, option_name: str) -> None:
    _ptr_register_page(current_page)
    debug_trace = _ptr_update_debug_detail(
        "select_combobox_option",
        {
            "label": label,
            "option_name": option_name,
            "status": "open_trigger",
            "option_attempts": [],
            "experience_attempts": [],
        },
    )
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
            candidate_error = _ptr_try_apply_combobox_option_candidate(
                trigger,
                resolved,
                current_page,
                label,
                option_name,
            )
            if candidate_error is None:
                option_attempts = debug_trace.setdefault("option_attempts", [])
                if isinstance(option_attempts, list):
                    option_attempts.append({"strategy_name": strategy_name, "status": "validated"})
                debug_trace["resolved_by"] = strategy_name
                debug_trace["status"] = "success"
                _ptr_set_debug_detail("select_combobox_option", debug_trace)
                return
            last_error = candidate_error
            option_attempts = debug_trace.setdefault("option_attempts", [])
            if isinstance(option_attempts, list):
                option_attempts.append(
                    {
                        "strategy_name": strategy_name,
                        "status": "postcondition_failed",
                        "error": _ptr_trim_debug_text(candidate_error, 320),
                    }
                )
        except Exception as exc:
            last_error = exc
            option_attempts = debug_trace.setdefault("option_attempts", [])
            if isinstance(option_attempts, list):
                option_attempts.append(
                    {
                        "strategy_name": strategy_name,
                        "status": "failed",
                        "error": _ptr_trim_debug_text(exc, 320),
                    }
                )
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
            candidate_error = _ptr_try_apply_combobox_option_candidate(
                trigger,
                experience_locator,
                current_page,
                label,
                option_name,
            )
            if candidate_error is None:
                experience_attempts = debug_trace.setdefault("experience_attempts", [])
                if isinstance(experience_attempts, list):
                    experience_attempts.append(
                        {
                            "strategy_name": strategy_name,
                            "status": "validated",
                            "episode_id": str(episode.get("episode_id") or "").strip(),
                            "retrieval_score": int(episode.get("retrieval_score") or 0),
                        }
                    )
                debug_trace["resolved_by"] = "experience_reuse"
                debug_trace["status"] = "success"
                _ptr_set_debug_detail("select_combobox_option", debug_trace)
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
            last_error = candidate_error
            experience_attempts = debug_trace.setdefault("experience_attempts", [])
            if isinstance(experience_attempts, list):
                experience_attempts.append(
                    {
                        "strategy_name": strategy_name,
                        "status": "postcondition_failed",
                        "episode_id": str(episode.get("episode_id") or "").strip(),
                        "retrieval_score": int(episode.get("retrieval_score") or 0),
                        "error": _ptr_trim_debug_text(candidate_error, 320),
                    }
                )
        except Exception as exc:
            last_error = exc
            experience_attempts = debug_trace.setdefault("experience_attempts", [])
            if isinstance(experience_attempts, list):
                experience_attempts.append(
                    {
                        "strategy_name": strategy_name,
                        "status": "failed",
                        "episode_id": str(episode.get("episode_id") or "").strip(),
                        "retrieval_score": int(episode.get("retrieval_score") or 0),
                        "error": _ptr_trim_debug_text(exc, 320),
                    }
                )
    if not debug_trace.get("experience_attempts"):
        debug_trace["experience_attempts"] = [{"status": "no_candidates"}]

    ai_result, last_error = _ptr_execute_ai_repair_rounds(
        current_page=current_page,
        helper="select_combobox_option",
        label=option_target,
        last_error=last_error,
        locator=option,
        postcondition_kind="option_selected",
        failure_message=lambda strategy_name: f'AI strategy "{strategy_name}" did not apply combobox option "{option_name}".',
        execute_locator=lambda strategy_name, ai_locator, ai_strategy: (
            _ptr_try_apply_combobox_option_candidate(
                trigger,
                ai_locator,
                current_page,
                label,
                option_name,
            )
            is None
        ),
    )
    if ai_result is not None:
        strategy_name, ai_locator, ai_strategy = ai_result
        debug_trace["ai_repair"] = {
            "status": "validated",
            "strategy_name": strategy_name,
            "locator_strategy": _ptr_clone_json_value(ai_strategy),
        }
        debug_trace["resolved_by"] = "ai_locator_repair"
        debug_trace["status"] = "success"
        _ptr_set_debug_detail("select_combobox_option", debug_trace)
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
        return
    debug_trace["ai_repair"] = {
        "status": "failed",
        "error": _ptr_trim_debug_text(last_error, 320),
    }
    debug_trace["status"] = "failed"
    debug_trace["final_error"] = _ptr_trim_debug_text(last_error, 320)
    _ptr_set_debug_detail("select_combobox_option", debug_trace)
    raise RuntimeError(f'Unable to select combobox option "{option_name}" for "{label}".') from last_error


def _ptr_oracle_searchselect_state(page: Page | None) -> dict[str, Any]:
    state = _ptr_safe_page_eval(
        page,
        r"""() => {
            const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
            const isVisible = (node) => {
                if (!node) return false;
                const style = window.getComputedStyle(node);
                if (!style) return false;
                if (style.visibility === "hidden" || style.display === "none") return false;
                const rect = node.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const searchInputSelector = [
                "input[role='combobox']",
                "input.oj-inputtext-input",
                "input.oj-searchselect-input",
                "input.oj-searchselect-filter-text-field"
            ].join(", ");
            const inferHostIdFromFilterInput = (node) => {
                const id = normalize(node?.id);
                if (!id) return "";
                const match = id.match(/^oj-searchselect-filter-(.+?)(?:\|input)?$/);
                return normalize(match?.[1]);
            };

            const hosts = Array.from(document.querySelectorAll("oj-select-single, oj-c-select-single"))
                .filter((host) => {
                    if (!isVisible(host)) return false;
                    return (
                        host.classList?.contains("oj-listbox-dropdown-open")
                        || Boolean(host.querySelector?.(".oj-searchselect-filter"))
                        || Boolean(host.querySelector?.(".oj-searchselect-filter-text-field"))
                        || Boolean(host.querySelector?.("input[role='combobox'][aria-expanded='true']"))
                    );
                });

            const activeElement = document.activeElement;
            const activeFilterInput = activeElement?.matches?.(searchInputSelector) ? activeElement : null;
            const inferredHostId = inferHostIdFromFilterInput(activeFilterInput);

            let host = activeElement?.closest?.("oj-select-single, oj-c-select-single");
            if (!host && inferredHostId) {
                const inferredHost = document.getElementById(inferredHostId);
                if (inferredHost?.matches?.("oj-select-single, oj-c-select-single")) {
                    host = inferredHost;
                }
            }
            if (!host && hosts.length === 1) host = hosts[0];
            if (!host && !activeFilterInput) return {};
            if (host && !isVisible(host) && !activeFilterInput) return {};

            const liveRegion = host?.querySelector?.(".oj-listbox-liveregion");
            const filterInput = activeFilterInput || host?.querySelector?.(searchInputSelector);
            const liveText = normalize(liveRegion?.innerText || liveRegion?.textContent);
            const hostText = normalize(host?.innerText || host?.textContent);
            const filterValue = normalize(
                (filterInput && "value" in filterInput ? filterInput.value : "")
                || filterInput?.getAttribute?.("value")
            );

            return {
                host_id: normalize(host?.id || inferredHostId),
                open: true,
                no_matches: /no matches found/i.test(liveText || hostText),
                live_text: liveText,
                host_text: hostText,
                filter_value: filterValue,
            };
        }""",
    )
    return state if isinstance(state, dict) else {}


def _ptr_oracle_searchselect_query_matches(state: dict[str, Any], requested_query: str) -> bool:
    desired = _ptr_normalize_text(requested_query)
    if not desired:
        return True
    actual = _ptr_normalize_text((state or {}).get("filter_value"))
    return bool(actual) and actual == desired


def _ptr_wait_for_oracle_searchselect_query(
    page: Page | None,
    requested_query: str,
    *,
    timeout_ms: int | None = None,
) -> tuple[dict[str, Any], bool]:
    timeout = _ptr_resolve_wait_override_ms(timeout_ms, "PTR_SEARCH_QUERY_REFLECT_TIMEOUT_MS", 1500)
    deadline = time.time() + max(timeout, 0) / 1000.0
    last_state: dict[str, Any] = {}

    while True:
        last_state = _ptr_oracle_searchselect_state(page)
        if _ptr_oracle_searchselect_query_matches(last_state, requested_query):
            return last_state, True
        if time.time() >= deadline:
            return last_state, False
        if page is None:
            return last_state, False
        page.wait_for_timeout(min(100, max(1, int((deadline - time.time()) * 1000))))


def _ptr_try_oracle_lov_trigger_direct_entry(
    trigger: Locator,
    current_page: Page,
    title: str,
    option_name: str,
    *,
    fill_value: str | None = None,
) -> dict[str, Any] | None:
    normalized_title = str(title or "").strip()
    if not normalized_title.lower().startswith("search:"):
        return None

    field_label = normalized_title.split(":", 1)[1].strip()
    requested_value = str(fill_value or option_name or "").strip()
    if not field_label or not requested_value:
        return None

    metadata = _ptr_extract_locator_metadata(trigger)
    trigger_id = str(metadata.get("id") or "").strip()
    candidate_builders: list[tuple[str, Locator]] = []

    page_locator = getattr(current_page, "locator", None)
    if trigger_id.endswith("::lovIconId") and callable(page_locator):
        base_id = trigger_id[: -len("::lovIconId")]
        if base_id:
            candidate_builders.append(
                (
                    "oracle_lov_icon_content_id",
                    current_page.locator(f'[id="{base_id}::content"]'),
                )
            )

    get_by_role = getattr(current_page, "get_by_role", None)
    if callable(get_by_role):
        try:
            candidate_builders.append(
                ("oracle_lov_combobox_label", current_page.get_by_role("combobox", name=field_label, exact=True))
            )
        except Exception:
            pass
        try:
            candidate_builders.append(
                ("oracle_lov_textbox_label", current_page.get_by_role("textbox", name=field_label, exact=True))
            )
        except Exception:
            pass

    timeout_ms = _ptr_wait_ms("PTR_ACTION_TIMEOUT_MS", 3000)
    for strategy_name, candidate in candidate_builders:
        try:
            resolved = candidate.first if hasattr(candidate, "first") else candidate
            candidate_metadata = _ptr_extract_locator_metadata(resolved)
            candidate_tag = str(candidate_metadata.get("tag") or "").strip().lower()
            candidate_role = str(candidate_metadata.get("role") or "").strip().lower()
            if candidate_tag not in {"input", "textarea", "select"} and candidate_role not in {"combobox", "textbox"}:
                continue
            if str(candidate_metadata.get("disabled") or "").strip().lower() == "true":
                continue
            if str(candidate_metadata.get("aria_disabled") or "").strip().lower() == "true":
                continue
            if not _ptr_locator_visible(resolved, timeout_ms=300):
                continue

            _ptr_record_strategy_attempt(strategy_name)
            _ptr_enter_search_value(
                resolved,
                requested_value,
                timeout_ms=_ptr_wait_ms("PTR_TEXT_ENTRY_TIMEOUT_MS", 3000),
                current_page=current_page,
                label=field_label,
            )
            try:
                resolved.press("Tab", timeout=timeout_ms)
            except TypeError:
                resolved.press("Tab")
            current_page.wait_for_timeout(_ptr_wait_ms("PTR_LOV_DIRECT_ENTRY_COMMIT_WAIT_MS", 350))
            _ptr_wait_for_field_processing(
                current_page,
                env_name="PTR_DROPDOWN_CHANGE_PROCESSING_WAIT_MS",
                default_ms=5000,
            )
            observed = _ptr_locator_value(resolved) or _ptr_locator_text(resolved)
            if _ptr_value_matches(option_name, observed) or _ptr_oracle_label_value_matches(current_page, field_label, option_name):
                return {
                    "strategy_name": strategy_name,
                    "field_label": field_label,
                    "requested_value": requested_value,
                    "observed_value": observed,
                }
        except Exception:
            continue
    return None


def _ptr_wait_for_search_option_surface(
    page: Page | None,
    option: Locator,
    *,
    timeout_ms: int | None = None,
) -> dict[str, bool]:
    timeout = _ptr_resolve_wait_override_ms(timeout_ms, "PTR_SEARCH_SURFACE_TIMEOUT_MS", 1500)
    deadline = time.time() + max(timeout, 0) / 1000.0
    poll_ms = max(100, _ptr_wait_ms("PTR_SEARCH_SURFACE_POLL_MS", 150))

    while True:
        option_visible = _ptr_locator_visible(option, timeout_ms=min(250, max(timeout, 0)))
        popup_open = bool(
            _ptr_safe_page_eval(
                page,
                r"""(selectors) => {
                    const isVisible = (node) => {
                        if (!node) return false;
                        const style = window.getComputedStyle(node);
                        if (!style) return false;
                        if (style.display === "none" || style.visibility === "hidden") return false;
                        const rect = node.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    };

                    for (const selector of selectors || []) {
                        const nodes = Array.from(document.querySelectorAll(selector.replace(/:visible/g, "")));
                        if (nodes.some(isVisible)) return true;
                    }
                    return false;
                }""",
                list(_PTR_POPUP_SCOPE_SELECTORS),
            )
        )
        if option_visible or popup_open or time.time() >= deadline:
            return {
                "option_visible": option_visible,
                "popup_open": popup_open,
            }
        if page is None:
            return {
                "option_visible": option_visible,
                "popup_open": popup_open,
            }
        page.wait_for_timeout(min(poll_ms, max(1, int((deadline - time.time()) * 1000))))


def _ptr_select_search_trigger_option(
    trigger: Locator,
    option: Locator,
    current_page: Page,
    title: str,
    option_name: str,
    *,
    option_kind: str = "text",
    option_exact: bool | None = None,
    fill_value: str | None = None,
    allow_repair: bool = True,
) -> None:
    _ptr_register_page(current_page)
    debug_trace = _ptr_update_debug_detail(
        "select_search_trigger_option",
        {
            "title": title,
            "option_name": option_name,
            "fill_value": _ptr_trim_debug_text(fill_value, 120),
            "status": "open_or_search",
            "option_attempts": [],
            "experience_attempts": [],
        },
    )
    search_timeout_ms = _ptr_wait_ms("PTR_TEXT_ENTRY_TIMEOUT_MS", 3000)
    raw_option_timeout_ms = _ptr_wait_ms("PTR_SEARCH_RESULT_TIMEOUT_MS", 6000)
    option_candidate_timeout_ms = _ptr_wait_ms("PTR_SEARCH_OPTION_CANDIDATE_TIMEOUT_MS", 1500)
    option_already_visible = False
    if fill_value is not None:
        _ptr_enter_search_value(
            trigger,
            fill_value,
            timeout_ms=search_timeout_ms,
            current_page=current_page,
            label=title,
        )
    else:
        _ptr_strict_click(trigger)
    current_page.wait_for_timeout(_ptr_wait_ms("PTR_SEARCH_RESULTS_WAIT_MS", 750))
    oracle_search_state = _ptr_oracle_searchselect_state(current_page)
    if fill_value is not None:
        oracle_search_state, query_reflected = _ptr_wait_for_oracle_searchselect_query(
            current_page,
            fill_value,
            timeout_ms=_ptr_wait_ms("PTR_SEARCH_QUERY_REFLECT_TIMEOUT_MS", 1500),
        )
        if not query_reflected:
            _ptr_enter_search_value(
                trigger,
                fill_value,
                timeout_ms=search_timeout_ms,
                current_page=current_page,
                label=title,
            )
            current_page.wait_for_timeout(_ptr_wait_ms("PTR_SEARCH_RESULTS_WAIT_MS", 750))
            oracle_search_state, query_reflected = _ptr_wait_for_oracle_searchselect_query(
                current_page,
                fill_value,
                timeout_ms=_ptr_wait_ms("PTR_SEARCH_QUERY_REFLECT_TIMEOUT_MS", 1500),
            )
            if not query_reflected:
                option_already_visible = _ptr_locator_visible(option, timeout_ms=raw_option_timeout_ms)
            if not query_reflected and not option_already_visible:
                debug_trace["oracle_search_state"] = {
                    "open": bool(oracle_search_state.get("open")),
                    "no_matches": bool(oracle_search_state.get("no_matches")),
                    "filter_value": _ptr_trim_debug_text(oracle_search_state.get("filter_value"), 160),
                    "live_text": _ptr_trim_debug_text(oracle_search_state.get("live_text"), 160),
                    "query_reflected": False,
                    "option_already_visible": False,
                }
                debug_trace["status"] = "failed"
                visible_query = (
                    str(oracle_search_state.get("filter_value") or "").strip() or "unknown"
                )
                debug_trace["final_error"] = _ptr_trim_debug_text(
                    f'Oracle search-select "{title}" did not reflect requested query "{fill_value}". Visible query: "{visible_query}"',
                    320,
                )
                _ptr_set_debug_detail("select_search_trigger_option", debug_trace)
                raise RuntimeError(
                    f'Oracle search-select "{title}" did not reflect requested query "{fill_value}". '
                    f'Visible query: "{visible_query}"'
                )
        debug_trace["oracle_search_state"] = {
            "open": bool(oracle_search_state.get("open")),
            "no_matches": bool(oracle_search_state.get("no_matches")),
            "filter_value": _ptr_trim_debug_text(oracle_search_state.get("filter_value"), 160),
            "live_text": _ptr_trim_debug_text(oracle_search_state.get("live_text"), 160),
            "query_reflected": bool(query_reflected),
            "option_already_visible": bool(option_already_visible),
        }
    if bool(oracle_search_state.get("no_matches")) and not option_already_visible:
        option_already_visible = _ptr_locator_visible(option, timeout_ms=min(raw_option_timeout_ms, 750))
    if bool(oracle_search_state.get("no_matches")) and not option_already_visible:
        query_text = str(oracle_search_state.get("filter_value") or fill_value or option_name or "").strip()
        visible_state = str(oracle_search_state.get("live_text") or "No matches found").strip() or "No matches found"
        debug_trace["oracle_search_state"] = {
            "open": bool(oracle_search_state.get("open")),
            "no_matches": True,
            "filter_value": _ptr_trim_debug_text(query_text, 160),
            "live_text": _ptr_trim_debug_text(visible_state, 160),
            "query_reflected": debug_trace.get("oracle_search_state", {}).get("query_reflected"),
            "option_already_visible": bool(option_already_visible),
        }
        debug_trace["status"] = "failed"
        debug_trace["final_error"] = _ptr_trim_debug_text(
            f'Oracle search-select "{title}" returned no matches for query "{query_text}" while looking for option "{option_name}". Visible state: {visible_state}',
            320,
        )
        _ptr_set_debug_detail("select_search_trigger_option", debug_trace)
        raise RuntimeError(
            f'Oracle search-select "{title}" returned no matches for query "{query_text}" '
            f'while looking for option "{option_name}". Visible state: {visible_state}'
        )
    search_surface = {"option_visible": False, "popup_open": False}
    oracle_lov_direct_entry: dict[str, Any] | None = None
    if fill_value is None and not bool(oracle_search_state.get("open")):
        search_surface = _ptr_wait_for_search_option_surface(
            current_page,
            option,
            timeout_ms=_ptr_wait_ms("PTR_SEARCH_SURFACE_TIMEOUT_MS", 1500),
        )
        debug_trace["search_surface"] = dict(search_surface)
        if not search_surface.get("option_visible") and not search_surface.get("popup_open"):
            oracle_lov_direct_entry = _ptr_try_oracle_lov_trigger_direct_entry(
                trigger,
                current_page,
                title,
                option_name,
                fill_value=fill_value,
            )
            if oracle_lov_direct_entry:
                debug_trace["oracle_lov_direct_entry"] = {
                    "status": "validated",
                    "details": _ptr_clone_json_value(oracle_lov_direct_entry),
                }
                debug_trace["resolved_by"] = "oracle_lov_direct_entry"
                debug_trace["status"] = "success"
                _ptr_set_debug_detail("select_search_trigger_option", debug_trace)
                _ptr_set_recovery_record(
                    "oracle_handler",
                    "oracle_lov_direct_entry",
                    "oracle_lov_direct_entry",
                    {
                        "title": title,
                        "option_name": option_name,
                        **oracle_lov_direct_entry,
                    },
                )
                _ptr_store_experience_episode(
                    action_type="select_search_trigger_option",
                    label=str(option_name or "").strip(),
                    page=current_page,
                    locator=trigger,
                    error=None,
                    status="success",
                    postcondition_kind="option_selected",
                    postcondition_passed=True,
                )
                return
            debug_trace["oracle_lov_direct_entry"] = {"status": "not_applied"}
            debug_trace["status"] = "failed"
            debug_trace["final_error"] = _ptr_trim_debug_text(
                f'Search trigger "{title}" did not open a visible search surface for option "{option_name}".',
                320,
            )
            _ptr_set_debug_detail("select_search_trigger_option", debug_trace)
            raise RuntimeError(
                f'Search trigger "{title}" did not open a visible search surface for option "{option_name}".'
            )
    last_error: Exception | None = None
    option_target = str(option_name or "").strip()
    option_candidates = [
        ("raw_option", option),
        ("role_option", current_page.get_by_role("option", name=option_name)),
        ("role_cell", current_page.get_by_role("cell", name=option_name)),
        ("role_gridcell", current_page.get_by_role("gridcell", name=option_name)),
        (
            "text_option",
            current_page.get_by_text(option_name)
            if option_exact is None
            else current_page.get_by_text(option_name, exact=option_exact),
        ),
    ]
    for strategy_name, candidate in option_candidates:
        try:
            resolved = candidate.first if hasattr(candidate, "first") else candidate
            _ptr_record_strategy_attempt(strategy_name)
            before = _ptr_observe(current_page, resolved)
            timeout_ms = raw_option_timeout_ms if strategy_name == "raw_option" else option_candidate_timeout_ms
            _ptr_strict_click(resolved, timeout_ms=timeout_ms)
            current_page.wait_for_timeout(_ptr_wait_ms("PTR_POST_CLICK_WAIT_MS", 250))
            after = _ptr_observe(current_page, resolved)
            if _ptr_option_selection_postcondition(before, after, trigger, resolved, option_name):
                _ptr_wait_for_field_processing(
                    current_page,
                    env_name="PTR_DROPDOWN_CHANGE_PROCESSING_WAIT_MS",
                    default_ms=5000,
                )
                option_attempts = debug_trace.setdefault("option_attempts", [])
                if isinstance(option_attempts, list):
                    option_attempts.append(
                        {
                            "strategy_name": strategy_name,
                            "status": "validated",
                            "after": _ptr_debug_observation_summary(after),
                        }
                    )
                debug_trace["resolved_by"] = strategy_name
                debug_trace["status"] = "success"
                _ptr_set_debug_detail("select_search_trigger_option", debug_trace)
                return
            last_error = RuntimeError(f'Search trigger "{title}" did not apply option "{option_name}".')
            option_attempts = debug_trace.setdefault("option_attempts", [])
            if isinstance(option_attempts, list):
                option_attempts.append(
                    {
                        "strategy_name": strategy_name,
                        "status": "postcondition_failed",
                        "error": _ptr_trim_debug_text(last_error, 320),
                    }
                )
        except Exception as exc:
            last_error = exc
            option_attempts = debug_trace.setdefault("option_attempts", [])
            if isinstance(option_attempts, list):
                option_attempts.append(
                    {
                        "strategy_name": strategy_name,
                        "status": "failed",
                        "error": _ptr_trim_debug_text(exc, 320),
                    }
                )
    if last_error is None:
        last_error = RuntimeError(f'Search trigger "{title}" did not apply option "{option_name}".')

    if not allow_repair:
        debug_trace["status"] = "failed"
        debug_trace["final_error"] = _ptr_trim_debug_text(last_error, 320)
        _ptr_set_debug_detail("select_search_trigger_option", debug_trace)
        raise RuntimeError(f'Unable to apply search option "{option_name}" for "{title}".') from last_error

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
                experience_attempts = debug_trace.setdefault("experience_attempts", [])
                if isinstance(experience_attempts, list):
                    experience_attempts.append(
                        {
                            "strategy_name": strategy_name,
                            "status": "validated",
                            "episode_id": str(episode.get("episode_id") or "").strip(),
                            "retrieval_score": int(episode.get("retrieval_score") or 0),
                            "after": _ptr_debug_observation_summary(after_experience),
                        }
                    )
                debug_trace["resolved_by"] = "experience_reuse"
                debug_trace["status"] = "success"
                _ptr_set_debug_detail("select_search_trigger_option", debug_trace)
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
            experience_attempts = debug_trace.setdefault("experience_attempts", [])
            if isinstance(experience_attempts, list):
                experience_attempts.append(
                    {
                        "strategy_name": strategy_name,
                        "status": "postcondition_failed",
                        "episode_id": str(episode.get("episode_id") or "").strip(),
                        "retrieval_score": int(episode.get("retrieval_score") or 0),
                        "error": _ptr_trim_debug_text(last_error, 320),
                    }
                )
        except Exception as exc:
            last_error = exc
            experience_attempts = debug_trace.setdefault("experience_attempts", [])
            if isinstance(experience_attempts, list):
                experience_attempts.append(
                    {
                        "strategy_name": strategy_name,
                        "status": "failed",
                        "episode_id": str(episode.get("episode_id") or "").strip(),
                        "retrieval_score": int(episode.get("retrieval_score") or 0),
                        "error": _ptr_trim_debug_text(exc, 320),
                    }
                )
    if not debug_trace.get("experience_attempts"):
        debug_trace["experience_attempts"] = [{"status": "no_candidates"}]

    def _execute_ai_search_option_locator(strategy_name: str, ai_locator: Locator, ai_strategy: dict[str, Any]) -> bool:
        before_ai = _ptr_observe(current_page, ai_locator)
        _ptr_strict_click(ai_locator)
        current_page.wait_for_timeout(_ptr_wait_ms("PTR_POST_CLICK_WAIT_MS", 250))
        after_ai = _ptr_observe(current_page, ai_locator)
        if not _ptr_option_selection_postcondition(before_ai, after_ai, trigger, ai_locator, option_name):
            return False
        _ptr_wait_for_field_processing(
            current_page,
            env_name="PTR_DROPDOWN_CHANGE_PROCESSING_WAIT_MS",
            default_ms=5000,
        )
        return True

    ai_result, last_error = _ptr_execute_ai_repair_rounds(
        current_page=current_page,
        helper="select_search_trigger_option",
        label=option_target,
        last_error=last_error,
        locator=option,
        postcondition_kind="option_selected",
        failure_message=lambda strategy_name: f'AI strategy "{strategy_name}" did not apply search option "{option_name}".',
        execute_locator=_execute_ai_search_option_locator,
    )
    if ai_result is not None:
        strategy_name, ai_locator, ai_strategy = ai_result
        debug_trace["ai_repair"] = {
            "status": "validated",
            "strategy_name": strategy_name,
            "locator_strategy": _ptr_clone_json_value(ai_strategy),
        }
        debug_trace["resolved_by"] = "ai_locator_repair"
        debug_trace["status"] = "success"
        _ptr_set_debug_detail("select_search_trigger_option", debug_trace)
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
        return
    debug_trace["ai_repair"] = {
        "status": "failed",
        "error": _ptr_trim_debug_text(last_error, 320),
    }
    debug_trace["status"] = "failed"
    debug_trace["final_error"] = _ptr_trim_debug_text(last_error, 320)
    _ptr_set_debug_detail("select_search_trigger_option", debug_trace)
    raise RuntimeError(f'Unable to apply search option "{option_name}" for "{title}".') from last_error


def _ptr_reopen_menu_if_options_hidden(
    trigger: Locator,
    current_page: Page,
    candidate: Locator,
) -> None:
    probe_ms = _ptr_wait_ms("PTR_MENU_REOPEN_PROBE_MS", 300)
    if _ptr_locator_is_actionable(candidate, timeout_ms=probe_ms):
        return
    try:
        _ptr_strict_click(trigger)
        current_page.wait_for_timeout(_ptr_wait_ms("PTR_MENU_OPEN_WAIT_MS", 350))
    except Exception:
        pass


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
    debug_trace = _ptr_update_debug_detail(
        "select_adf_menu_panel_option",
        {
            "trigger_label": trigger_label,
            "option_name": option_name,
            "status": "open_menu",
            "option_attempts": [],
            "experience_attempts": [],
        },
    )
    _ptr_strict_click(trigger)
    current_page.wait_for_timeout(_ptr_wait_ms("PTR_MENU_OPEN_WAIT_MS", 350))
    is_completion = _ptr_oracle_menu_option_is_completion_action(trigger_label)
    option_candidates = _ptr_menu_panel_option_candidates(current_page, option, option_name)
    if is_completion:
        option_candidates = option_candidates[:2]
    menu_option_visible = _ptr_wait_for_oracle_menu_trigger_option_visibility(
        current_page,
        option_candidates,
        trigger_label,
    )
    if not menu_option_visible and not is_completion:
        debug_trace["status"] = "failed"
        debug_trace["final_error"] = _ptr_trim_debug_text(
            _ptr_menu_trigger_failure_message(trigger_label, option_name),
            320,
        )
        _ptr_set_debug_detail("select_adf_menu_panel_option", debug_trace)
        raise RuntimeError(_ptr_menu_trigger_failure_message(trigger_label, option_name))
    if not menu_option_visible and is_completion:
        debug_trace["menu_visibility_probe"] = "not_visible_continuing_with_completion_candidates"
        popup_candidates = _ptr_oracle_visible_popup_option_candidates(current_page, option_name)
        if popup_candidates:
            option_candidates = popup_candidates + option_candidates
    last_error: Exception | None = None
    semantic_failure_after_click = False
    option_target = str(option_name or "").strip()
    option_probe_ms = _ptr_wait_ms("PTR_MENU_OPTION_PROBE_MS", 1000)
    for index, (strategy_name, candidate) in enumerate(option_candidates):
        try:
            _ptr_record_strategy_attempt(strategy_name)
            if is_completion:
                _ptr_reopen_menu_if_options_hidden(trigger, current_page, candidate)
                if not _ptr_locator_is_actionable(
                    candidate,
                    timeout_ms=option_probe_ms,
                ):
                    option_attempts = debug_trace.setdefault("option_attempts", [])
                    if isinstance(option_attempts, list):
                        option_attempts.append(
                            {
                                "strategy_name": strategy_name,
                                "status": "not_actionable",
                            }
                        )
                    continue
            else:
                if index > 0:
                    _ptr_reopen_menu_if_options_hidden(trigger, current_page, candidate)
                if not _ptr_locator_is_actionable(candidate, timeout_ms=option_probe_ms):
                    option_attempts = debug_trace.setdefault("option_attempts", [])
                    if isinstance(option_attempts, list):
                        option_attempts.append(
                            {
                                "strategy_name": strategy_name,
                                "status": "not_actionable",
                            }
                        )
                    continue
            before = _ptr_observe(current_page, candidate)
            _ptr_strict_click(candidate)
            current_page.wait_for_timeout(_ptr_wait_ms("PTR_POST_CLICK_WAIT_MS", 250))
            after = _ptr_observe(current_page, candidate)
            if _ptr_option_selection_postcondition(
                before,
                after,
                trigger,
                candidate,
                option_name,
                page=current_page,
                trigger_label=trigger_label,
            ):
                _ptr_wait_for_field_processing(
                    current_page,
                    env_name="PTR_DROPDOWN_CHANGE_PROCESSING_WAIT_MS",
                    default_ms=5000,
                )
                option_attempts = debug_trace.setdefault("option_attempts", [])
                if isinstance(option_attempts, list):
                    option_attempts.append(
                        {
                            "strategy_name": strategy_name,
                            "status": "validated",
                            "after": _ptr_debug_observation_summary(after),
                        }
                    )
                debug_trace["resolved_by"] = strategy_name
                debug_trace["status"] = "success"
                _ptr_set_debug_detail("select_adf_menu_panel_option", debug_trace)
                return
            last_error = RuntimeError(_ptr_menu_option_failure_message(trigger_label, option_name))
            option_attempts = debug_trace.setdefault("option_attempts", [])
            if isinstance(option_attempts, list):
                option_attempts.append(
                    {
                        "strategy_name": strategy_name,
                        "status": "postcondition_failed",
                        "error": _ptr_trim_debug_text(last_error, 320),
                    }
                )
            if _ptr_oracle_menu_option_requires_semantic_validation(trigger_label, option_name) and not is_completion:
                semantic_failure_after_click = True
                break
        except Exception as exc:
            last_error = exc
            option_attempts = debug_trace.setdefault("option_attempts", [])
            if isinstance(option_attempts, list):
                option_attempts.append(
                    {
                        "strategy_name": strategy_name,
                        "status": "failed",
                        "error": _ptr_trim_debug_text(exc, 320),
                    }
                )
    if last_error is None:
        if is_completion:
            last_error = RuntimeError(_ptr_menu_trigger_failure_message(trigger_label, option_name))
        else:
            last_error = RuntimeError(_ptr_menu_option_failure_message(trigger_label, option_name))
    if semantic_failure_after_click:
        debug_trace["status"] = "failed"
        debug_trace["final_error"] = _ptr_trim_debug_text(last_error, 320)
        _ptr_set_debug_detail("select_adf_menu_panel_option", debug_trace)
        raise last_error
    if is_completion:
        debug_trace["status"] = "failed"
        debug_trace["final_error"] = _ptr_trim_debug_text(last_error, 320)
        _ptr_set_debug_detail("select_adf_menu_panel_option", debug_trace)
        raise last_error

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
            if _ptr_option_selection_postcondition(
                before_experience,
                after_experience,
                trigger,
                experience_locator,
                option_name,
                page=current_page,
                trigger_label=trigger_label,
            ):
                _ptr_wait_for_field_processing(
                    current_page,
                    env_name="PTR_DROPDOWN_CHANGE_PROCESSING_WAIT_MS",
                    default_ms=5000,
                )
                experience_attempts = debug_trace.setdefault("experience_attempts", [])
                if isinstance(experience_attempts, list):
                    experience_attempts.append(
                        {
                            "strategy_name": strategy_name,
                            "status": "validated",
                            "episode_id": str(episode.get("episode_id") or "").strip(),
                            "retrieval_score": int(episode.get("retrieval_score") or 0),
                            "after": _ptr_debug_observation_summary(after_experience),
                        }
                    )
                debug_trace["resolved_by"] = "experience_reuse"
                debug_trace["status"] = "success"
                _ptr_set_debug_detail("select_adf_menu_panel_option", debug_trace)
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
            last_error = RuntimeError(_ptr_menu_option_failure_message(trigger_label, option_name))
            experience_attempts = debug_trace.setdefault("experience_attempts", [])
            if isinstance(experience_attempts, list):
                experience_attempts.append(
                    {
                        "strategy_name": strategy_name,
                        "status": "postcondition_failed",
                        "episode_id": str(episode.get("episode_id") or "").strip(),
                        "retrieval_score": int(episode.get("retrieval_score") or 0),
                        "error": _ptr_trim_debug_text(last_error, 320),
                    }
                )
            if _ptr_oracle_menu_option_requires_semantic_validation(trigger_label, option_name):
                semantic_failure_after_click = True
                break
        except Exception as exc:
            last_error = exc
            experience_attempts = debug_trace.setdefault("experience_attempts", [])
            if isinstance(experience_attempts, list):
                experience_attempts.append(
                    {
                        "strategy_name": strategy_name,
                        "status": "failed",
                        "episode_id": str(episode.get("episode_id") or "").strip(),
                        "retrieval_score": int(episode.get("retrieval_score") or 0),
                        "error": _ptr_trim_debug_text(exc, 320),
                    }
                )
    if semantic_failure_after_click:
        debug_trace["status"] = "failed"
        debug_trace["final_error"] = _ptr_trim_debug_text(last_error, 320)
        _ptr_set_debug_detail("select_adf_menu_panel_option", debug_trace)
        raise last_error
    if not debug_trace.get("experience_attempts"):
        debug_trace["experience_attempts"] = [{"status": "no_candidates"}]

    def _execute_ai_menu_locator(strategy_name: str, ai_locator: Locator, ai_strategy: dict[str, Any]) -> bool:
        nonlocal semantic_failure_after_click
        before_ai = _ptr_observe(current_page, ai_locator)
        _ptr_strict_click(ai_locator)
        current_page.wait_for_timeout(_ptr_wait_ms("PTR_POST_CLICK_WAIT_MS", 250))
        after_ai = _ptr_observe(current_page, ai_locator)
        if _ptr_option_selection_postcondition(
            before_ai,
            after_ai,
            trigger,
            ai_locator,
            option_name,
            page=current_page,
            trigger_label=trigger_label,
        ):
            _ptr_wait_for_field_processing(
                current_page,
                env_name="PTR_DROPDOWN_CHANGE_PROCESSING_WAIT_MS",
                default_ms=5000,
            )
            return True
        if _ptr_oracle_menu_option_requires_semantic_validation(trigger_label, option_name):
            semantic_failure_after_click = True
        return False

    ai_result, last_error = _ptr_execute_ai_repair_rounds(
        current_page=current_page,
        helper="select_adf_menu_panel_option",
        label=option_target,
        last_error=last_error,
        locator=option,
        postcondition_kind="option_selected",
        failure_message=lambda strategy_name: _ptr_menu_option_failure_message(trigger_label, option_name),
        execute_locator=_execute_ai_menu_locator,
    )
    if semantic_failure_after_click:
        debug_trace["status"] = "failed"
        debug_trace["final_error"] = _ptr_trim_debug_text(last_error, 320)
        _ptr_set_debug_detail("select_adf_menu_panel_option", debug_trace)
        raise last_error
    if ai_result is not None:
        strategy_name, ai_locator, ai_strategy = ai_result
        debug_trace["ai_repair"] = {
            "status": "validated",
            "strategy_name": strategy_name,
            "locator_strategy": _ptr_clone_json_value(ai_strategy),
        }
        debug_trace["resolved_by"] = "ai_locator_repair"
        debug_trace["status"] = "success"
        _ptr_set_debug_detail("select_adf_menu_panel_option", debug_trace)
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
        return
    debug_trace["ai_repair"] = {
        "status": "failed",
        "error": _ptr_trim_debug_text(last_error, 320),
    }
    debug_trace["status"] = "failed"
    debug_trace["final_error"] = _ptr_trim_debug_text(last_error, 320)
    _ptr_set_debug_detail("select_adf_menu_panel_option", debug_trace)
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
        if not guided_process and _ptr_generic_click_postcondition(before, after):
            return
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
