"""Auto-split from helpers_v2.py. `facade` is the helpers_v2 facade: the single
shared namespace, so monkeypatching helpers_v2.X and shared _ACT_* state
behave exactly as in the original module. Call shared helpers via `facade.`."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Locator, Page

try:
    from .. import helpers_v2 as facade
except ImportError:  # pragma: no cover
    from src.runtime import helpers_v2 as facade

__all__ = [
    "_act_env_flag",
    "_act_wait_ms",
    "_act_resolve_wait_override_ms",
    "_act_int_env",
    "_act_clone_json_value",
    "_act_set_script_data",
    "_act_current_script_data",
    "_act_debug_enabled",
    "_act_trim_debug_text",
    "_act_set_debug_detail",
    "_act_debug_observation_summary",
    "_act_control_type_label",
    "_act_update_debug_detail",
    "_act_reset_strategy_tracking",
    "_act_record_strategy_attempt",
    "_act_record_ai_interaction",
    "_act_update_last_ai_interaction",
    "_act_finalize_last_ai_interaction",
    "_act_last_ai_interaction",
    "_act_record_experience_interaction",
    "_act_update_last_experience_interaction",
    "_act_set_recovery_record",
    "_act_strategy_snapshot",
    "_act_resolve_browser_provider",
    "_act_release_steel_session",
    "_act_release_pending_steel_sessions",
    "_act_launch_steel_browser",
    "_act_launch_chromium",
    "_act_register_page",
    "_act_capture_oracle_table_snapshots",
    "_act_flow_context_capture_enabled",
    "_act_capture_semantic_snapshot",
    "_act_semantic_snapshot_has_content",
    "_act_runtime_debug_settings",
    "_act_runtime_snapshot_summary",
    "_act_persist_diagnostics_snapshot",
    "_act_capture_live_snapshot_before_close",
    "_act_context_pages",
    "_act_browser_contexts",
    "_act_order_pages_for_snapshot",
    "_act_capture_page_snapshot",
    "_act_locator_element_handle",
    "_act_safe_locator_eval",
    "_act_safe_page_eval",
    "_act_locator_visible",
    "_act_locator_value",
    "_act_locator_text",
    "_act_normalize_text",
    "_act_extract_locator_metadata",
    "_act_capture_locator_context",
    "_act_locator_is_actionable",
    "_act_strict_click",
    "_act_strict_dblclick",
    "_act_strict_fill",
    "_act_guided_flow_state",
    "_act_current_guided_step",
    "_act_dialog_count",
    "_act_active_element",
    "_act_body_marker",
    "_act_observe",
    "_act_generic_click_postcondition",
    "_act_button_click_postcondition",
    "_act_button_no_commit_failure",
    "_act_retry_strict_click_after_oracle_ppr",
    "_act_settle_click_postcondition",
    "_act_oracle_label_control_locator",
    "_act_active_element_matches_target",
    "_act_value_matches",
    "_act_recorded_locator_roles",
    "_act_page_visible_text",
    "_act_normalize_runtime_action_name",
    "_act_guided_flow_advanced",
    "_act_busy_indicator_count",
    "_act_wait_for_observation_stability",
    "_act_wait_for_field_processing",
    "_act_experience_enabled",
    "_act_control_family",
    "_act_oracle_surface_type",
    "_act_oracle_warning_dialog_state",
    "_act_error_looks_like_missing_button",
    "_act_try_skip_optional_oracle_warning_ok",
    "_act_try_dismiss_oracle_warning_dialog",
    "_act_page_signature",
    "_act_failure_signature",
    "_act_capture_failure_screenshot",
    "_act_capture_failure",
    "_act_capture_step",
    "_act_write_diagnostics",
    "_act_resolve",
    "_act_collect_validation_messages",
    "_act_resolve_page",
    "_act_resolve_primary_locator",
    "_act_finalize_action_log",
    "_act_goto_page",
    "_act_raw_click",
    "_act_raw_fill",
    "_act_raw_press",
    "_act_login_submit_and_redirect",
    "_act_wait_after_interaction",
    "_act_wait_for_post_login_redirect",
]


def _act_env_flag(name: str, default: str = "true") -> bool:
    return str(os.getenv(name, default)).strip().lower() not in ("false", "0", "no", "off")


def _act_wait_ms(env_name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(env_name, str(default))))
    except Exception:
        return default


def _act_resolve_wait_override_ms(value: Any, env_name: str, default: int) -> int:
    if value is None:
        return facade._act_wait_ms(env_name, default)
    try:
        return max(0, int(value))
    except Exception:
        return facade._act_wait_ms(env_name, default)


def _act_int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _act_clone_json_value(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)


def _act_set_script_data(payload: dict[str, Any] | None = None) -> None:
    cloned = facade._act_clone_json_value(payload) if isinstance(payload, dict) and payload else {}
    if not isinstance(cloned, dict):
        cloned = {}
    multi_line_context = facade._act_clone_json_value(
        getattr(facade, "_ACT_MULTI_LINE_CONTEXT", {}) or {}
    )
    if isinstance(multi_line_context, dict) and multi_line_context:
        cloned["multi_line_context"] = multi_line_context
    facade._ACT_SCRIPT_DATA = cloned
    facade._ACT_CURRENT_STRATEGY["script_data"] = facade._act_clone_json_value(cloned) or {}


def _act_current_script_data() -> dict[str, Any]:
    current = facade._ACT_CURRENT_STRATEGY.get("script_data") or facade._ACT_SCRIPT_DATA
    return facade._act_clone_json_value(current or {}) or {}


def _act_debug_enabled() -> bool:
    return facade._act_env_flag("ACT_DEBUG_TRACE", "false")


def _act_trim_debug_text(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _act_set_debug_detail(key: str, payload: Any) -> None:
    normalized_key = str(key or "").strip()
    if not normalized_key:
        return
    debug_payload = facade._ACT_CURRENT_STRATEGY.setdefault("debug", {})
    if not isinstance(debug_payload, dict):
        debug_payload = {}
        facade._ACT_CURRENT_STRATEGY["debug"] = debug_payload
    debug_payload[normalized_key] = facade._act_clone_json_value(payload)


def _act_control_type_label(tag: Any, oracle_host_tag: Any = "", role: Any = "") -> str:
    """Human-readable control type for the debug trace: which Oracle component family the
    resolved element belongs to. Pure labeling from already-observed fields with NO execution
    impact -- it only distinguishes Redwood Core Pack (oj-c-*) from legacy Oracle JET (oj-*)
    from classic ADF/HTML controls so a reader can tell at a glance what was acted on.
    """
    tag = str(tag or "").strip().lower()
    host = str(oracle_host_tag or "").strip().lower()
    role = str(role or "").strip().lower()

    def _family(name: str) -> str:
        if name.startswith("oj-c-"):
            return f"{name} (Redwood Core Pack)"
        if name.startswith("oj-"):
            return f"{name} (Oracle JET)"
        return ""

    # Prefer the Oracle host component -- the acted-on node is often a plain <input>
    # nested inside it (e.g. the combobox input inside oj-c-select-single).
    label = _family(host) or _family(tag)
    if label:
        return label
    if not tag:
        return ""
    return f"classic <{tag}>{f' [role={role}]' if role else ''}"


def _act_debug_observation_summary(observation: dict[str, Any] | None) -> dict[str, Any]:
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
        "guided_flow_primary_heading": facade._act_trim_debug_text(
            guided_flow.get("primary_heading"), 160
        ),
        "dialog_count": int(observation.get("dialog_count") or 0),
        "body_marker": facade._act_trim_debug_text(observation.get("body_marker"), 160),
        "active_element": {
            "tag": str(active_element.get("tag") or "").strip(),
            "control_type": facade._act_control_type_label(
                active_element.get("tag"),
                active_element.get("oracle_host_tag"),
                active_element.get("role"),
            ),
            "oracle_host_tag": str(active_element.get("oracle_host_tag") or "").strip(),
            "id": str(active_element.get("id") or "").strip(),
            "name": str(active_element.get("name") or "").strip(),
            "role": str(active_element.get("role") or "").strip(),
            "aria_label": facade._act_trim_debug_text(active_element.get("aria_label"), 120),
            "title": facade._act_trim_debug_text(active_element.get("title"), 120),
        },
        "target": {
            "visible": bool(observation.get("target_visible")),
            "value": facade._act_trim_debug_text(observation.get("target_value"), 120),
            "text": facade._act_trim_debug_text(observation.get("target_text"), 120),
            "tag": str(target_meta.get("tag") or "").strip(),
            "control_type": facade._act_control_type_label(
                target_meta.get("tag"),
                target_meta.get("oracle_host_tag"),
                target_meta.get("role"),
            ),
            "oracle_host_tag": str(target_meta.get("oracle_host_tag") or "").strip(),
            "id": str(target_meta.get("id") or "").strip(),
            "name": str(target_meta.get("name") or "").strip(),
            "role": str(target_meta.get("role") or "").strip(),
            "aria_label": facade._act_trim_debug_text(target_meta.get("aria_label"), 120),
            "title": facade._act_trim_debug_text(target_meta.get("title"), 120),
            "class_name": facade._act_trim_debug_text(target_meta.get("class_name"), 120),
            "aria_expanded": str(target_meta.get("aria_expanded") or "").strip(),
            "aria_selected": str(target_meta.get("aria_selected") or "").strip(),
        },
    }


def _act_update_debug_detail(key: str, payload: dict[str, Any]) -> dict[str, Any]:
    current = facade._ACT_CURRENT_STRATEGY.get("debug") or {}
    existing = current.get(key) if isinstance(current, dict) else {}
    merged = facade._act_clone_json_value(existing) if isinstance(existing, dict) else {}
    if not isinstance(merged, dict):
        merged = {}
    merged.update(facade._act_clone_json_value(payload) or {})
    facade._act_set_debug_detail(key, merged)
    return merged


def _act_reset_strategy_tracking(helper: str, label: str = "") -> None:
    facade._ACT_CURRENT_STRATEGY["helper"] = helper
    facade._ACT_CURRENT_STRATEGY["strategy"] = "direct"
    facade._ACT_CURRENT_STRATEGY["label"] = label
    facade._ACT_CURRENT_STRATEGY["attempts"] = []
    facade._ACT_CURRENT_STRATEGY["ai_interactions"] = []
    facade._ACT_CURRENT_STRATEGY["experience_interactions"] = []
    facade._ACT_CURRENT_STRATEGY["script_data"] = (
        facade._act_clone_json_value(facade._ACT_SCRIPT_DATA or {}) or {}
    )
    facade._ACT_CURRENT_STRATEGY["recovery"] = None
    facade._ACT_CURRENT_STRATEGY["debug"] = {}


def _act_record_strategy_attempt(strategy: str) -> None:
    normalized = str(strategy or "").strip()
    if not normalized:
        return
    attempts = facade._ACT_CURRENT_STRATEGY.setdefault("attempts", [])
    attempts.append(normalized)
    facade._ACT_CURRENT_STRATEGY["strategy"] = normalized


def _act_record_ai_interaction(entry: dict[str, Any]) -> None:
    if not isinstance(entry, dict) or not entry:
        return
    interactions = facade._ACT_CURRENT_STRATEGY.setdefault("ai_interactions", [])
    if not isinstance(interactions, list):
        interactions = []
        facade._ACT_CURRENT_STRATEGY["ai_interactions"] = interactions
    interactions.append(facade._act_clone_json_value(entry))


def _act_update_last_ai_interaction(patch: dict[str, Any]) -> None:
    interactions = facade._ACT_CURRENT_STRATEGY.setdefault("ai_interactions", [])
    if not interactions or not isinstance(interactions[-1], dict):
        return
    current = facade._act_clone_json_value(interactions[-1]) or {}
    if isinstance(current, dict):
        current.update(facade._act_clone_json_value(patch))
        interactions[-1] = current


def _act_finalize_last_ai_interaction(
    *, repair_outcome: str, strategy_name: str = "", error: Any = None, postcondition_kind: str = ""
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
    facade._act_update_last_ai_interaction(patch)


def _act_last_ai_interaction() -> dict[str, Any]:
    interactions = facade._ACT_CURRENT_STRATEGY.setdefault("ai_interactions", [])
    if not interactions or not isinstance(interactions[-1], dict):
        return {}
    return facade._act_clone_json_value(interactions[-1]) or {}


def _act_record_experience_interaction(entry: dict[str, Any]) -> None:
    if not isinstance(entry, dict) or not entry:
        return
    interactions = facade._ACT_CURRENT_STRATEGY.setdefault("experience_interactions", [])
    if not isinstance(interactions, list):
        interactions = []
        facade._ACT_CURRENT_STRATEGY["experience_interactions"] = interactions
    interactions.append(facade._act_clone_json_value(entry))


def _act_update_last_experience_interaction(patch: dict[str, Any]) -> None:
    interactions = facade._ACT_CURRENT_STRATEGY.setdefault("experience_interactions", [])
    if not interactions or not isinstance(interactions[-1], dict):
        return
    current = facade._act_clone_json_value(interactions[-1]) or {}
    if isinstance(current, dict):
        current.update(facade._act_clone_json_value(patch))
        interactions[-1] = current


def _act_set_recovery_record(
    source: str, kind: str, handler_name: str, details: dict[str, Any] | None = None
) -> None:
    facade._ACT_CURRENT_STRATEGY["recovery"] = {
        "source": str(source or "").strip(),
        "kind": str(kind or "").strip(),
        "handler_name": str(handler_name or "").strip(),
        "details": facade._act_clone_json_value(details or {}),
    }


def _act_strategy_snapshot() -> tuple[list[str], list[str], str]:
    attempts = [
        str(item).strip()
        for item in facade._ACT_CURRENT_STRATEGY.get("attempts") or []
        if str(item).strip()
    ]
    unique_attempts: list[str] = []
    seen: set[str] = set()
    for strategy in attempts:
        if strategy in seen:
            continue
        seen.add(strategy)
        unique_attempts.append(strategy)
    final_strategy = str(facade._ACT_CURRENT_STRATEGY.get("strategy") or "").strip() or "direct"
    return (attempts, unique_attempts, final_strategy)


def _act_resolve_browser_provider() -> str:
    explicit_provider = str(os.getenv("ACT_BROWSER_PROVIDER", "")).strip().lower()
    if explicit_provider:
        if explicit_provider not in {"local", "steel"}:
            raise RuntimeError(f"Unsupported ACT_BROWSER_PROVIDER: {explicit_provider}")
        return explicit_provider
    if facade._act_env_flag("ACT_IS_LOCAL_ENV", "false"):
        return "local"
    return "steel" if str(os.getenv("STEEL_API_KEY", "")).strip() else "local"


def _act_release_steel_session(session_id: str) -> None:
    normalized_session_id = str(session_id or "").strip()
    if (
        not normalized_session_id
        or normalized_session_id not in facade._ACT_STEEL_RELEASE_SESSION_IDS
    ):
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
        facade._ACT_STEEL_RELEASE_SESSION_IDS.discard(normalized_session_id)


def _act_release_pending_steel_sessions() -> None:
    for pending_session_id in list(facade._ACT_STEEL_RELEASE_SESSION_IDS):
        facade._act_release_steel_session(pending_session_id)


def _act_launch_steel_browser(playwright, *, headless: bool) -> Any:
    steel_api_key = str(os.getenv("STEEL_API_KEY", "")).strip()
    if not steel_api_key:
        raise RuntimeError("ACT_BROWSER_PROVIDER=steel but STEEL_API_KEY is not configured.")
    from steel import Steel

    browser_type = getattr(playwright, "chromium")
    connect_over_cdp = getattr(browser_type, "connect_over_cdp", None)
    if connect_over_cdp is None:
        raise RuntimeError("Playwright chromium browser type does not support connect_over_cdp.")
    steel_session_id = str(os.getenv("STEEL_SESSION_ID", "")).strip()
    steel_connect_url = (
        str(os.getenv("STEEL_CONNECT_URL", "wss://connect.steel.dev")).strip()
        or "wss://connect.steel.dev"
    )
    steel_client = Steel(steel_api_key=steel_api_key)
    steel_connect_retries = max(0, facade._act_int_env("ACT_STEEL_CONNECT_RETRIES", 2))
    steel_session_timeout_ms = max(
        60000, facade._act_int_env("ACT_STEEL_SESSION_TIMEOUT_MS", 900000)
    )
    last_error: Exception | None = None
    for attempt in range(steel_connect_retries + 1):
        created_session_id = ""
        should_release_session = False
        try:
            if steel_session_id:
                steel_session = steel_client.sessions.retrieve(steel_session_id)
            else:
                steel_session = steel_client.sessions.create(
                    api_timeout=steel_session_timeout_ms, headless=bool(headless)
                )
                created_session_id = str(getattr(steel_session, "id", "")).strip()
                should_release_session = bool(created_session_id)
                if should_release_session:
                    facade._ACT_STEEL_RELEASE_SESSION_IDS.add(created_session_id)
            active_session_id = str(getattr(steel_session, "id", "")).strip()
            if not active_session_id:
                raise RuntimeError("Steel session creation did not return a session id.")
            connect_url = f"{steel_connect_url}?{facade.urlencode({'apiKey': steel_api_key, 'sessionId': active_session_id})}"
            browser = connect_over_cdp(connect_url)
            if should_release_session:
                facade._ACT_STEEL_BROWSER_SESSION_IDS[id(browser)] = active_session_id
            return browser
        except Exception as exc:
            exc_text = str(exc)
            if (
                exc.__class__.__name__ == "AuthenticationError"
                or "Authentication failed" in exc_text
                or "Unauthorized" in exc_text
            ):
                raise RuntimeError(
                    "Steel authentication failed while creating the browser session. Verify STEEL_API_KEY in the worker environment and restart both the tool and agent workers after updating it."
                ) from exc
            if created_session_id:
                facade._act_release_steel_session(created_session_id)
            last_error = exc
            if attempt >= steel_connect_retries:
                break
            time.sleep(min(2**attempt, 5))
    if last_error is not None:
        raise last_error
    raise RuntimeError("Failed to connect to Steel browser.")


def _act_launch_chromium(playwright, headless: bool = False):
    browser_provider = facade._act_resolve_browser_provider()
    desired_headless = facade._act_env_flag("ACT_HEADLESS", "false")
    effective_headless = desired_headless if not headless else headless
    if browser_provider == "steel":
        return facade._act_launch_steel_browser(playwright, headless=effective_headless)
    window_width = max(960, facade._act_int_env("ACT_WINDOW_WIDTH", 1440))
    window_height = max(700, facade._act_int_env("ACT_WINDOW_HEIGHT", 900))
    launch_kwargs: dict[str, Any] = {
        "headless": effective_headless,
        "args": [f"--window-size={window_width},{window_height}"],
    }
    chromium_executable = ""
    if facade._act_env_flag("ACT_USE_SYSTEM_CHROMIUM", "true"):
        configured_path = str(
            os.getenv("ACT_CHROMIUM_EXECUTABLE_PATH")
            or os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
            or ""
        ).strip()
        candidate_paths = [configured_path] if configured_path else []
        candidate_paths.extend(["/usr/bin/chromium", "/usr/bin/chromium-browser"])
        for candidate in candidate_paths:
            if candidate and os.path.exists(candidate):
                chromium_executable = candidate
                break
    if chromium_executable:
        launch_kwargs["executable_path"] = chromium_executable
    else:
        launch_kwargs["channel"] = "chromium"
    return playwright.chromium.launch(**launch_kwargs)


def _act_register_page(page: Page) -> Page:
    facade._ACT_LAST_PAGE = page
    # Oracle work areas can take 1-2 min to navigate on the first page open; Playwright's
    # built-in navigation timeout is only 30s. Raise the per-page *navigation* default (goto,
    # reload, post-login redirect, popup navigation) without touching the tight action timeouts.
    # Applied once per page (guarded) to avoid redundant channel calls on every action.
    try:
        if not getattr(page, "_act_nav_timeout_applied", False):
            page.set_default_navigation_timeout(
                facade._act_wait_ms("ACT_NAVIGATION_TIMEOUT_MS", 120000)
            )
            try:
                page._act_nav_timeout_applied = True
            except Exception:
                pass
    except Exception:
        pass
    return page


def _act_capture_oracle_table_snapshots(page: Page | None) -> list[dict[str, Any]]:
    tables = facade._act_safe_page_eval(
        page,
        '() => {\n            const text = (value) => String(value || "").replace(/\\s+/g, " ").trim();\n            const isVisible = (node) => {\n                if (!node) return false;\n                const style = window.getComputedStyle(node);\n                if (!style || style.display === "none" || style.visibility === "hidden") return false;\n                const rect = node.getBoundingClientRect();\n                return rect.width > 0 && rect.height > 0;\n            };\n\n            const seen = new Set();\n            const candidates = Array.from(\n                document.querySelectorAll(".oj-table-scroller table.oj-table-element, table.oj-table-element")\n            );\n\n            return candidates\n                .filter((table) => {\n                    if (!isVisible(table) || seen.has(table)) return false;\n                    seen.add(table);\n                    return true;\n                })\n                .slice(0, 3)\n                .map((table, tableIndex) => {\n                    const headerCells = Array.from(table.querySelectorAll("thead tr th"));\n                    const columnIndexes = [];\n                    const headers = [];\n                    headerCells.forEach((cell, index) => {\n                        const label = text(cell.getAttribute("abbr") || cell.innerText || cell.textContent);\n                        if (!label) return;\n                        columnIndexes.push(index);\n                        headers.push(label);\n                    });\n\n                    const rows = Array.from(table.querySelectorAll("tbody tr"))\n                        .slice(0, 5)\n                        .map((row) => {\n                            const cells = Array.from(row.children);\n                            return columnIndexes.map((columnIndex) =>\n                                text(cells[columnIndex]?.innerText || cells[columnIndex]?.textContent)\n                            );\n                        })\n                        .filter((row) => row.some((value) => value));\n\n                    return {\n                        table_index: tableIndex,\n                        id: text(table.id),\n                        aria_labelledby: text(table.getAttribute("aria-labelledby")),\n                        headers,\n                        rows,\n                    };\n                })\n                .filter((table) => table.headers.length > 0 && table.rows.length > 0);\n        }',
    )
    return tables if isinstance(tables, list) else []


def _act_flow_context_capture_enabled() -> bool:
    return facade._act_env_flag("ACT_FLOW_CONTEXT_CAPTURE_ENABLED", "true")


def _act_capture_semantic_snapshot(page: Page | None) -> dict[str, Any]:
    if page is None:
        return {"label_values": [], "text_candidates": [], "dialogs": []}
    snapshot = facade._act_safe_page_eval(
        page,
        '() => {\n            const normalize = (value, maxLen = 300) => {\n                const text = String(value || "").replace(/\\s+/g, " ").trim();\n                return text.length > maxLen ? text.slice(0, maxLen).trim() : text;\n            };\n            const isVisible = (node) => {\n                if (!node) return false;\n                const style = window.getComputedStyle(node);\n                if (!style || style.display === "none" || style.visibility === "hidden") return false;\n                const rect = node.getBoundingClientRect();\n                return rect.width > 0 && rect.height > 0;\n            };\n            const byIdsText = (ids) => {\n                return normalize(\n                    String(ids || "")\n                        .split(/\\s+/)\n                        .map((id) => document.getElementById(id))\n                        .filter(Boolean)\n                        .map((node) => normalize(node.innerText || node.textContent))\n                        .filter(Boolean)\n                        .join(" ")\n                );\n            };\n            const nearestLabelText = (node) => {\n                if (!node) return "";\n                const explicit = normalize(node.getAttribute?.("aria-label"));\n                if (explicit) return explicit;\n                const labelledBy = byIdsText(node.getAttribute?.("aria-labelledby"));\n                if (labelledBy) return labelledBy;\n                const host = node.closest?.("[data-oj-field], oj-select-single, oj-c-select-single, oj-input-text, oj-c-input-text, oj-input-number, oj-c-input-number, oj-input-date, oj-c-input-date, oj-text-area, oj-c-text-area");\n                const hostLabelledBy = byIdsText(host?.getAttribute?.("labelled-by") || host?.getAttribute?.("aria-labelledby"));\n                if (hostLabelledBy) return hostLabelledBy;\n                const label = node.closest?.("label, oj-label") || host?.querySelector?.("label, oj-label");\n                if (label) {\n                    const labelText = normalize(label.innerText || label.textContent);\n                    if (labelText) return labelText;\n                }\n                const previous = node.previousElementSibling;\n                if (previous) {\n                    const previousText = normalize(previous.innerText || previous.textContent, 120);\n                    if (previousText && previousText.length <= 120) return previousText;\n                }\n                return "";\n            };\n            const controlValue = (node) => {\n                if (!node) return "";\n                const host = node.closest?.("[data-oj-field], oj-select-single, oj-c-select-single, oj-input-text, oj-c-input-text, oj-input-number, oj-c-input-number, oj-input-date, oj-c-input-date, oj-text-area, oj-c-text-area");\n                const directValue = normalize(("value" in node ? node.value : "") || node.getAttribute?.("value"));\n                if (directValue) return directValue;\n                const ariaValueText = normalize(node.getAttribute?.("aria-valuetext"));\n                if (ariaValueText) return ariaValueText;\n                const ariaChecked = normalize(node.getAttribute?.("aria-checked"));\n                if (ariaChecked) return ariaChecked;\n                const readonlyValue = normalize(\n                    host?.querySelector?.(".oj-text-field-readonly")?.innerText\n                    || host?.querySelector?.(".oj-searchselect-filter-text-field")?.value\n                    || host?.querySelector?.(".oj-searchselect-filter-text-field")?.innerText\n                );\n                if (readonlyValue) return readonlyValue;\n                const text = normalize(node.innerText || node.textContent);\n                if (text) return text;\n                const title = normalize(node.getAttribute?.("title"));\n                if (title) return title;\n                return normalize(host?.innerText || host?.textContent);\n            };\n\n            const labelValues = [];\n            const labelSeen = new Set();\n            const controlSelector = [\n                "input",\n                "textarea",\n                "select",\n                "[role=\'textbox\']",\n                "[role=\'combobox\']",\n                "[role=\'spinbutton\']",\n                "oj-select-single",\n                "oj-c-select-single",\n                "oj-input-text",\n                "oj-c-input-text",\n                "oj-input-number",\n                "oj-c-input-number",\n                "oj-input-date",\n                "oj-c-input-date",\n                "oj-text-area",\n                "oj-c-text-area",\n                "[data-oj-field]"\n            ].join(",");\n\n            Array.from(document.querySelectorAll(controlSelector))\n                .filter(isVisible)\n                .slice(0, 180)\n                .forEach((node) => {\n                    const label = nearestLabelText(node);\n                    const value = controlValue(node);\n                    if (!label || !value) return;\n                    const key = `${label}||${value}`;\n                    if (labelSeen.has(key)) return;\n                    labelSeen.add(key);\n                    labelValues.push({\n                        label,\n                        value,\n                        tag: normalize(node.tagName).toLowerCase(),\n                        role: normalize(node.getAttribute?.("role")).toLowerCase(),\n                        id: normalize(node.id),\n                        title: normalize(node.getAttribute?.("title")),\n                        aria_label: normalize(node.getAttribute?.("aria-label")),\n                        data_oj_field: normalize(node.getAttribute?.("data-oj-field") || node.closest?.("[data-oj-field]")?.getAttribute?.("data-oj-field")),\n                    });\n                });\n\n            const textCandidates = [];\n            const textSeen = new Set();\n            Array.from(document.querySelectorAll("[role=\'heading\'],h1,h2,h3,a,button,[title],[aria-label],li,td,th"))\n                .filter(isVisible)\n                .slice(0, 160)\n                .forEach((node) => {\n                    const text = normalize(node.innerText || node.textContent);\n                    const title = normalize(node.getAttribute?.("title"));\n                    const ariaLabel = normalize(node.getAttribute?.("aria-label"));\n                    const combined = text || title || ariaLabel;\n                    if (!combined) return;\n                    const key = `${normalize(node.tagName)}|${normalize(node.id)}|${combined}`;\n                    if (textSeen.has(key)) return;\n                    textSeen.add(key);\n                    textCandidates.push({\n                        text,\n                        title,\n                        aria_label: ariaLabel,\n                        tag: normalize(node.tagName).toLowerCase(),\n                        role: normalize(node.getAttribute?.("role")).toLowerCase(),\n                        id: normalize(node.id),\n                    });\n                });\n\n            const dialogs = [];\n            Array.from(document.querySelectorAll("[role=\'dialog\'], .oj-dialog, .oj-popup"))\n                .filter(isVisible)\n                .slice(0, 5)\n                .forEach((node, index) => {\n                    const titleNode = node.querySelector("[role=\'heading\'], h1, h2, h3, .oj-dialog-title");\n                    dialogs.push({\n                        index,\n                        title: normalize(titleNode?.innerText || titleNode?.textContent, 160),\n                        text: normalize(node.innerText || node.textContent, 1200),\n                    });\n                });\n\n            return {\n                label_values: labelValues,\n                text_candidates: textCandidates,\n                dialogs,\n            };\n        }',
    )
    if not isinstance(snapshot, dict):
        return {"label_values": [], "text_candidates": [], "dialogs": []}
    return {
        "label_values": snapshot.get("label_values")
        if isinstance(snapshot.get("label_values"), list)
        else [],
        "text_candidates": snapshot.get("text_candidates")
        if isinstance(snapshot.get("text_candidates"), list)
        else [],
        "dialogs": snapshot.get("dialogs") if isinstance(snapshot.get("dialogs"), list) else [],
    }


def _act_semantic_snapshot_has_content(snapshot: dict[str, Any] | None) -> bool:
    if not isinstance(snapshot, dict):
        return False
    return any(bool(snapshot.get(key)) for key in ("label_values", "text_candidates", "dialogs"))


def _act_runtime_debug_settings() -> dict[str, Any]:
    return {
        "runner_version": facade._ACT_RUNNER_VERSION,
        "after_action_wait_ms": facade._act_wait_ms(
            "ACT_AFTER_ACTION_WAIT_MS", facade._ACT_HARDCODED_AFTER_ACTION_WAIT_MS
        ),
        "capture_steps": bool(facade._ACT_STEP_ARTIFACTS_DIR),
        "record_video": bool(str(os.getenv("ACT_VIDEO_DIR", "")).strip()),
        "step_screenshot_full_page": facade._act_env_flag("ACT_STEP_SCREENSHOT_FULL_PAGE", "false"),
        "page_text_snapshot_max_chars": facade._act_wait_ms(
            "ACT_PAGE_TEXT_SNAPSHOT_MAX_CHARS", 12000
        ),
        "debug_trace": facade._act_env_flag("ACT_DEBUG_TRACE", "false"),
        "experience_enabled": facade._act_env_flag("ACT_EXPERIENCE_ENABLED", "true"),
        "ai_self_repair_enabled": facade._act_ai_self_repair_enabled(),
        "action_timeout_ms": facade._act_wait_ms("ACT_ACTION_TIMEOUT_MS", 3000),
        "text_entry_timeout_ms": facade._act_wait_ms("ACT_TEXT_ENTRY_TIMEOUT_MS", 3000),
        "text_click_timeout_ms": facade._act_wait_ms("ACT_TEXT_CLICK_TIMEOUT_MS", 3000),
        "textbox_change_processing_wait_ms": facade._act_wait_ms(
            "ACT_TEXTBOX_CHANGE_PROCESSING_WAIT_MS", 500
        ),
        "ai_extract_pre_capture_wait_ms": facade._act_wait_ms(
            "ACT_AI_EXTRACT_PRE_CAPTURE_WAIT_MS", 1200
        ),
    }


def _act_runtime_snapshot_summary(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
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
            len(table.get("rows") or []) for table in oracle_tables if isinstance(table, dict)
        ),
        "label_value_count": len(page_semantics.get("label_values") or []),
        "text_candidate_count": len(page_semantics.get("text_candidates") or []),
        "dialog_count": len(page_semantics.get("dialogs") or []),
        "step_artifact_count": len(facade._ACT_STEP_ARTIFACTS),
        "action_log_count": len(facade._ACT_ACTION_LOG),
    }


def _act_persist_diagnostics_snapshot(snapshot: dict[str, Any] | None = None) -> None:
    if not facade._ACT_DIAGNOSTICS_PATH:
        return
    current = snapshot if isinstance(snapshot, dict) else dict(facade._ACT_LAST_PAGE_SNAPSHOT)
    payload = {
        "page_url": str(current.get("page_url") or "").strip(),
        "page_title": str(current.get("page_title") or "").strip(),
        "page_text": str(current.get("page_text") or "").strip(),
        "oracle_tables": current.get("oracle_tables") or [],
        "page_semantics": current.get("page_semantics") or {},
        "failure_screenshot_path": facade._ACT_FAILURE_SCREENSHOT_PATH or None,
        "step_artifacts": facade._ACT_STEP_ARTIFACTS,
        "action_log": facade._ACT_ACTION_LOG,
        "runtime_debug": {
            "settings": facade._act_runtime_debug_settings(),
            "snapshot_summary": facade._act_runtime_snapshot_summary(current),
            "last_strategy_state": facade._act_clone_json_value(facade._ACT_CURRENT_STRATEGY),
        },
    }
    try:
        Path(facade._ACT_DIAGNOSTICS_PATH).write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        return


def _act_capture_live_snapshot_before_close(page: Page | None) -> None:
    current_page = page or facade._ACT_LAST_PAGE
    if current_page is None:
        return
    try:
        wait_ms = facade._act_wait_ms("ACT_FLOW_CONTEXT_PRE_CLOSE_WAIT_MS", 0)
        if wait_ms > 0:
            current_page.wait_for_timeout(wait_ms)
    except Exception:
        pass
    try:
        snapshot = facade._act_capture_page_snapshot(current_page)
        facade._act_persist_diagnostics_snapshot(snapshot)
    except Exception:
        return


def _act_context_pages(context: BrowserContext) -> list[Page]:
    try:
        pages = getattr(context, "pages", None)
        if callable(pages):
            items = pages()
        else:
            items = pages
        return list(items or [])
    except Exception:
        return []


def _act_browser_contexts(browser: Browser) -> list[BrowserContext]:
    try:
        contexts = getattr(browser, "contexts", None)
        if callable(contexts):
            items = contexts()
        else:
            items = contexts
        return list(items or [])
    except Exception:
        return []


def _act_order_pages_for_snapshot(pages: list[Page]) -> list[Page]:
    ordered: list[Page] = []
    last_page = facade._ACT_LAST_PAGE
    trailing_page: Page | None = None
    for page in pages:
        if page is last_page:
            trailing_page = page
            continue
        ordered.append(page)
    if trailing_page is not None:
        ordered.append(trailing_page)
    return ordered


def _act_capture_page_snapshot(page: Page | None) -> dict[str, Any]:
    if page is None:
        return facade._ACT_LAST_PAGE_SNAPSHOT
    snapshot = dict(facade._ACT_LAST_PAGE_SNAPSHOT)
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
    body_text = facade._act_safe_page_eval(
        page,
        '() => {\n            const body = document?.body;\n            if (!body) return "";\n            return String(body.innerText || body.textContent || "").replace(/\\s+/g, " ").trim();\n        }',
    )
    text = str(body_text or "").strip()
    max_chars = max(0, facade._act_int_env("ACT_PAGE_TEXT_SNAPSHOT_MAX_CHARS", 12000))
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip()
    if text:
        snapshot["page_text"] = text
    elif "page_text" not in snapshot:
        snapshot["page_text"] = ""
    tables = facade._act_capture_oracle_table_snapshots(page)
    if tables:
        snapshot["oracle_tables"] = tables
    elif "oracle_tables" not in snapshot:
        snapshot["oracle_tables"] = []
    semantics = facade._act_capture_semantic_snapshot(page)
    if facade._act_semantic_snapshot_has_content(semantics):
        snapshot["page_semantics"] = semantics
    elif "page_semantics" not in snapshot:
        snapshot["page_semantics"] = {"label_values": [], "text_candidates": [], "dialogs": []}
    facade._ACT_LAST_PAGE_SNAPSHOT = snapshot
    facade._act_persist_diagnostics_snapshot(snapshot)
    return snapshot


def _act_locator_element_handle(locator: Locator, timeout_ms: int | None = None):
    timeout = max(
        50, int(timeout_ms or facade._act_int_env("ACT_LOCATOR_SNAPSHOT_TIMEOUT_MS", 250))
    )
    try:
        return locator.element_handle(timeout=timeout)
    except Exception:
        return None


def _act_safe_locator_eval(locator: Locator, expression: str, arg: Any | None = None) -> Any:
    try:
        handle = facade._act_locator_element_handle(locator)
        if handle is None:
            return None
        if arg is None:
            return handle.evaluate(expression)
        return handle.evaluate(expression, arg)
    except Exception:
        return None


def _act_safe_page_eval(page: Page | None, expression: str, arg: Any | None = None) -> Any:
    if page is None:
        return None
    try:
        if arg is None:
            return page.evaluate(expression)
        return page.evaluate(expression, arg)
    except Exception:
        return None


def _act_locator_visible(locator: Locator, timeout_ms: int | None = None) -> bool:
    timeout = max(
        50, int(timeout_ms or facade._act_int_env("ACT_LOCATOR_SNAPSHOT_TIMEOUT_MS", 250))
    )
    try:
        locator.wait_for(state="visible", timeout=timeout)
        return True
    except Exception:
        return False


def _act_locator_value(locator: Locator) -> str:
    try:
        handle = facade._act_locator_element_handle(locator)
        if handle is None:
            return ""
        value = handle.evaluate(
            '(node) => {\n                if (!node) return "";\n                const readValue = (candidate) => {\n                    if (!candidate) return "";\n                    if ("value" in candidate) {\n                        const direct = String(candidate.value || "").trim();\n                        if (direct) return direct;\n                    }\n                    const attrCandidates = [\n                        candidate.getAttribute?.("value"),\n                        candidate.getAttribute?.("aria-valuetext"),\n                        candidate.getAttribute?.("aria-valuenow"),\n                    ];\n                    for (const raw of attrCandidates) {\n                        const text = String(raw || "").trim();\n                        if (text) return text;\n                    }\n                    return "";\n                };\n                const direct = readValue(node);\n                if (direct) return direct;\n                const nested = node.querySelector?.("[role=\'spinbutton\'], input, textarea, select");\n                return readValue(nested);\n            }'
        )
        return str(value or "").strip()
    except Exception:
        return ""


def _act_locator_text(locator: Locator) -> str:
    try:
        handle = facade._act_locator_element_handle(locator)
        if handle is None:
            return ""
        value = handle.evaluate(
            '(node) => String(node?.innerText || node?.textContent || "").replace(/\\s+/g, " ").trim()'
        )
        return str(value or "").strip()
    except Exception:
        return ""


def _act_normalize_text(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _act_extract_locator_metadata(locator: Locator) -> dict[str, str]:
    metadata = facade._act_safe_locator_eval(
        locator,
        '(node) => {\n            const text = (value) => String(value || "").replace(/\\s+/g, " ").trim();\n            const labelledByText = () => {\n                const ids = text(node?.getAttribute?.("aria-labelledby"));\n                if (!ids) return "";\n                const values = [];\n                for (const id of ids.split(/\\s+/)) {\n                    const candidate = document.getElementById(id);\n                    const candidateText = text(candidate?.innerText || candidate?.textContent);\n                    if (candidateText) values.push(candidateText);\n                }\n                return text(values.join(" "));\n            };\n            const oracleHost = node?.closest?.("oj-select-single, oj-c-select-single");\n            return {\n                tag: String(node?.tagName || "").toLowerCase(),\n                role: text(node?.getAttribute?.("role")),\n                id: text(node?.id),\n                name: text(node?.getAttribute?.("name")),\n                aria_label: text(node?.getAttribute?.("aria-label")),\n                aria_labelledby: text(node?.getAttribute?.("aria-labelledby")),\n                aria_controls: text(node?.getAttribute?.("aria-controls")),\n                title: text(node?.getAttribute?.("title")),\n                placeholder: text(node?.getAttribute?.("placeholder")),\n                label_hint: text(node?.getAttribute?.("label-hint")),\n                data_oj_field: text(node?.getAttribute?.("data-oj-field")),\n                labelledby_text: labelledByText(),\n                text: text(node?.innerText || node?.textContent),\n                class_name: text(node?.className),\n                oracle_host_tag: text(oracleHost?.tagName).toLowerCase(),\n                oracle_host_id: text(oracleHost?.id),\n                oracle_host_text: text(oracleHost?.innerText || oracleHost?.textContent),\n                oracle_host_data_oj_field: text(oracleHost?.getAttribute?.("data-oj-field")),\n                aria_expanded: text(node?.getAttribute?.("aria-expanded")),\n                aria_disabled: text(node?.getAttribute?.("aria-disabled")),\n                disabled: node?.disabled ? "true" : (node?.hasAttribute?.("disabled") ? "true" : ""),\n                aria_selected: text(node?.getAttribute?.("aria-selected")),\n                aria_checked: text(node?.getAttribute?.("aria-checked")),\n                checked: typeof node?.checked === "boolean" ? (node.checked ? "true" : "false") : "",\n            };\n        }',
    )
    return metadata if isinstance(metadata, dict) else {}


def _act_capture_locator_context(locator: Locator | None) -> dict[str, Any]:
    if locator is None:
        return {}
    context = facade._act_safe_locator_eval(
        locator,
        '(node) => {\n            const text = (value) => String(value || "").replace(/\\s+/g, " ").trim();\n            const labelledByText = () => {\n                const ids = text(node?.getAttribute?.("aria-labelledby"));\n                if (!ids) return "";\n                const values = [];\n                for (const id of ids.split(/\\s+/)) {\n                    const candidate = document.getElementById(id);\n                    const candidateText = text(candidate?.innerText || candidate?.textContent);\n                    if (candidateText) values.push(candidateText);\n                }\n                return text(values.join(" "));\n            };\n            const oracleHost = node?.closest?.("oj-select-single, oj-c-select-single");\n            return {\n                tag: text(node?.tagName).toLowerCase(),\n                role: text(node?.getAttribute?.("role")),\n                id: text(node?.id),\n                aria_label: text(node?.getAttribute?.("aria-label")),\n                aria_labelledby: text(node?.getAttribute?.("aria-labelledby")),\n                labelledby_text: labelledByText(),\n                aria_controls: text(node?.getAttribute?.("aria-controls")),\n                placeholder: text(node?.getAttribute?.("placeholder")),\n                title: text(node?.getAttribute?.("title")),\n                class_name: text(node?.className),\n                data_oj_field: text(node?.getAttribute?.("data-oj-field")),\n                text: text(node?.innerText || node?.textContent),\n                html: text(node?.outerHTML).slice(0, 1200),\n                oracle_host: oracleHost ? {\n                    tag: text(oracleHost?.tagName).toLowerCase(),\n                    id: text(oracleHost?.id),\n                    text: text(oracleHost?.innerText || oracleHost?.textContent),\n                    data_oj_field: text(oracleHost?.getAttribute?.("data-oj-field")),\n                    labelled_by: text(oracleHost?.getAttribute?.("labelled-by")),\n                    html: text(oracleHost?.outerHTML).slice(0, 1200),\n                } : {},\n            };\n        }',
    )
    return context if isinstance(context, dict) else {}


def _act_locator_is_actionable(locator: Locator, timeout_ms: int | None = None) -> bool:
    timeout = facade._act_resolve_wait_override_ms(timeout_ms, "ACT_ACTION_TIMEOUT_MS", 3000)
    try:
        locator.wait_for(state="visible", timeout=timeout)
        try:
            locator.scroll_into_view_if_needed(timeout=min(timeout, 1000))
        except Exception:
            pass
        return True
    except Exception:
        return False


def _act_strict_click(locator: Locator, timeout_ms: int | None = None) -> None:
    timeout = facade._act_resolve_wait_override_ms(timeout_ms, "ACT_ACTION_TIMEOUT_MS", 3000)
    locator.wait_for(state="visible", timeout=timeout)
    try:
        locator.scroll_into_view_if_needed(timeout=min(timeout, 1000))
    except Exception:
        pass
    locator.click(timeout=timeout)


def _act_strict_dblclick(locator: Locator, timeout_ms: int | None = None) -> None:
    timeout = facade._act_resolve_wait_override_ms(timeout_ms, "ACT_ACTION_TIMEOUT_MS", 3000)
    locator.wait_for(state="visible", timeout=timeout)
    try:
        locator.scroll_into_view_if_needed(timeout=min(timeout, 1000))
    except Exception:
        pass
    locator.dblclick(timeout=timeout)


def _act_strict_fill(locator: Locator, value: str, timeout_ms: int | None = None) -> None:
    timeout = facade._act_resolve_wait_override_ms(timeout_ms, "ACT_ACTION_TIMEOUT_MS", 3000)
    locator.wait_for(state="visible", timeout=timeout)
    try:
        locator.scroll_into_view_if_needed(timeout=min(timeout, 1000))
    except Exception:
        pass
    locator.fill(value, timeout=timeout)


def _act_guided_flow_state(page: Page | None) -> dict[str, Any]:
    result = facade._act_safe_page_eval(
        page,
        '() => {\n            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();\n            const isVisible = (node) => {\n                if (!node) return false;\n                const style = window.getComputedStyle(node);\n                if (!style) return false;\n                if (style.display === "none" || style.visibility === "hidden") return false;\n                if (node.getAttribute?.("aria-hidden") === "true") return false;\n                const rect = node.getBoundingClientRect();\n                return rect.width > 0 && rect.height > 0;\n            };\n            const textOf = (node) => normalize(node?.innerText || node?.textContent || "");\n            const firstVisibleText = (selectors) => {\n                for (const selector of selectors) {\n                    for (const node of document.querySelectorAll(selector)) {\n                        if (!isVisible(node)) continue;\n                        const text = textOf(node);\n                        if (text) return text;\n                    }\n                }\n                return "";\n            };\n            const dedupe = (items) => {\n                const seen = new Set();\n                const values = [];\n                for (const item of items) {\n                    const normalized = normalize(item);\n                    if (!normalized || seen.has(normalized)) continue;\n                    seen.add(normalized);\n                    values.push(normalized);\n                }\n                return values;\n            };\n\n            const selectedStep = firstVisibleText([\n                \'[role="tab"][aria-selected="true"]\',\n                \'[role="tab"].oj-selected\',\n                \'.oj-navigationlist-item.oj-selected\',\n                \'[aria-current="step"]\',\n                \'.oj-sp-guided-process-right-panel-navigation-list-step.oj-selected\',\n            ]);\n\n            let progressCounter = "";\n            const counterPattern = /^\\d+\\s*\\|\\s*\\d+$/;\n            for (const node of document.querySelectorAll("body *")) {\n                if (!isVisible(node)) continue;\n                const text = textOf(node);\n                if (counterPattern.test(text)) {\n                    progressCounter = text;\n                    break;\n                }\n            }\n\n            const primaryHeading = firstVisibleText([\n                "main h1",\n                "main h2",\n                "main h3",\n                \'[role="main"] h1\',\n                \'[role="main"] h2\',\n                \'[role="main"] h3\',\n                \'[role="heading"][aria-level="1"]\',\n                \'[role="heading"][aria-level="2"]\',\n                \'[role="heading"][aria-level="3"]\',\n                \'h1\',\n                \'h2\',\n                \'h3\',\n                \'.oj-typography-heading-lg\',\n                \'.oj-typography-heading-md\',\n                \'.oj-typography-heading-sm\',\n            ]);\n\n            const footerActions = [];\n            for (const node of document.querySelectorAll("button, [role=\'button\'], a[title], a[aria-label]")) {\n                if (!isVisible(node)) continue;\n                const rect = node.getBoundingClientRect();\n                if (rect.top < window.innerHeight * 0.55) continue;\n                const label = normalize(\n                    node.getAttribute?.("aria-label") ||\n                    node.getAttribute?.("title") ||\n                    node.innerText ||\n                    node.textContent ||\n                    ""\n                );\n                if (label) footerActions.push(label);\n            }\n\n            return {\n                selected_step: selectedStep,\n                progress_counter: progressCounter,\n                primary_heading: primaryHeading,\n                footer_actions: dedupe(footerActions).slice(0, 8),\n            };\n        }',
    )
    return result if isinstance(result, dict) else {}


def _act_current_guided_step(page: Page | None) -> str:
    guided_flow = facade._act_guided_flow_state(page)
    return str((guided_flow or {}).get("selected_step") or "").strip()


def _act_dialog_count(page: Page | None) -> int:
    result = facade._act_safe_page_eval(
        page,
        "() => {\n            const selectors = [\n                '[role=\"dialog\"]',\n                '[aria-modal=\"true\"]',\n                '[role=\"menu\"]',\n                '[role=\"listbox\"]',\n                '.oj-dialog',\n                '.oj-popup',\n            ];\n            const isVisible = (node) => {\n                if (!node) return false;\n                const style = window.getComputedStyle(node);\n                if (!style) return false;\n                if (style.display === \"none\" || style.visibility === \"hidden\") return false;\n                const rect = node.getBoundingClientRect();\n                return rect.width > 0 && rect.height > 0;\n            };\n            const seen = new Set();\n            let count = 0;\n            for (const selector of selectors) {\n                for (const node of document.querySelectorAll(selector)) {\n                    if (!isVisible(node)) continue;\n                    if (seen.has(node)) continue;\n                    seen.add(node);\n                    count += 1;\n                }\n            }\n            return count;\n        }",
    )
    try:
        return int(result or 0)
    except Exception:
        return 0


def _act_active_element(page: Page | None) -> dict[str, str]:
    result = facade._act_safe_page_eval(
        page,
        '() => {\n            const node = document.activeElement;\n            const text = (value) => String(value || "").replace(/\\s+/g, " ").trim();\n            return {\n                tag: String(node?.tagName || "").toLowerCase(),\n                role: text(node?.getAttribute?.("role")),\n                oracle_host_tag: text(node?.closest?.("oj-select-single,oj-c-select-single,oj-input-text,oj-c-input-text,oj-input-number,oj-c-input-number,oj-input-date,oj-c-input-date,oj-text-area,oj-c-text-area,oj-combobox-one,oj-c-combobox,oj-switch,oj-c-switch,oj-checkboxset,oj-c-checkbox,oj-action-card")?.tagName).toLowerCase(),\n                id: text(node?.id),\n                name: text(node?.getAttribute?.("name")),\n                aria_label: text(node?.getAttribute?.("aria-label")),\n                aria_checked: text(node?.getAttribute?.("aria-checked")),\n                checked: typeof node?.checked === "boolean" ? (node.checked ? "true" : "false") : "",\n                title: text(node?.getAttribute?.("title")),\n                text: text(node?.innerText || node?.textContent),\n            };\n        }',
    )
    return result if isinstance(result, dict) else {}


def _act_oracle_label_control_locator(page: Page | None, label: str) -> Locator | None:
    """Resolve a form control by its VISIBLE Oracle field label, role-agnostic.

    Oracle renders the same logical field with a different role/accessible-name depending on
    context (e.g. Business Unit is get_by_role('textbox', name='Business Unit') on an Invoice
    but an LOV combobox whose accessible name is the prompt hint on a Credit memo). When the
    recorded role+name locator misses, this resolves the field's control by anchoring on either:

      A) the field's visible LABEL element, scoped to its OWN field group (the label cell's
         sibling content cell / oj form-layout item / `for=` / aria-labelledby) -- never the
         whole table row, so a 2-column ADF PanelFormLayout doesn't fold two fields together; or
      B) the Oracle ADF LOV combobox prompt: af:inputComboboxListOfValues renders empty with the
         exact hint "Press DOWN arrow key and then Press ENTER key to make selection." regardless
         of the field's logical label, which is why a recorded name-based locator misses it.

    Both strategies return a control ONLY when exactly one visible control matches, so it never
    guesses between look-alikes (returns None on zero/ambiguous, so the caller falls through to
    experience/AI repair). The real open/value postcondition still gates success downstream.
    """
    if page is None:
        return None
    target = " ".join(str(label or "").split())
    if not target:
        return None
    probe = """
    (rawTarget) => {
        const norm = (v) => String(v || "").replace(/\\s+/g, " ").trim();
        const target = norm(rawTarget).toLowerCase();
        if (!target) return null;
        const labelText = (el) => norm(el && (el.innerText || el.textContent))
            .replace(/^\\*\\s*/, "").replace(/\\s*[:*]\\s*$/, "").trim().toLowerCase();
        const isVisible = (node) => {
            if (!node) return false;
            const s = window.getComputedStyle(node);
            if (!s || s.display === "none" || s.visibility === "hidden") return false;
            const r = node.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
        };
        const CTRL = "input,textarea,select,[role='textbox'],[role='combobox'],[role='spinbutton']";
        const HOST = "oj-select-single,oj-c-select-single,oj-input-text,oj-c-input-text,"
            + "oj-input-number,oj-c-input-number,oj-input-date,oj-c-input-date,"
            + "oj-text-area,oj-c-text-area,oj-combobox-one,oj-c-combobox";
        const controlId = (node) => {
            if (!node) return "";
            if (node.matches && node.matches(CTRL) && node.id) return node.id;
            const inner = node.querySelector && node.querySelector(CTRL);
            if (inner && inner.id) return inner.id;
            return node.id || "";
        };
        const found = new Set();
        const add = (node) => {
            if (!node || !isVisible(node)) return;
            const id = controlId(node);
            if (id) found.add(id);
        };

        // --- Strategy A: anchor on the visible field LABEL, scoped to its own field group ---
        const labelSel = "label, oj-label, legend, th, [class*='label' i], [data-oj-field]";
        const labelEls = Array.from(document.querySelectorAll(labelSel))
            .filter((el) => labelText(el) === target);
        const scanGroup = (root) => {
            if (!root) return;
            for (const c of root.querySelectorAll(CTRL)) add(c);
            if (found.size === 0) for (const c of root.querySelectorAll(HOST)) add(c);
        };
        for (const lab of labelEls) {
            const forId = lab.getAttribute && lab.getAttribute("for");
            if (forId) {
                const c = document.getElementById(forId);
                if (c) add((c.matches && c.matches(CTRL)) ? c
                    : (c.querySelector && c.querySelector(CTRL)) || (c.closest && c.closest(HOST))
                    || c);
            }
            if (lab.id) {
                for (const c of document.querySelectorAll('[aria-labelledby~="' + lab.id + '"]')) {
                    add(c.matches && c.matches(CTRL) ? c : (c.closest && c.closest(HOST)) || c);
                }
            }
            // classic ADF table layout: label cell -> the adjacent content cell in the same row
            const cell = lab.closest && lab.closest("td, th");
            if (cell) {
                let sib = cell.nextElementSibling;
                let hops = 0;
                while (sib && hops < 3 && found.size === 0) {
                    scanGroup(sib);
                    sib = sib.nextElementSibling;
                    hops += 1;
                }
            }
            // Redwood / oj form layout: label and control share one flex/field-layout item
            if (found.size === 0) {
                scanGroup(lab.closest && lab.closest(
                    ".oj-flex-item, .oj-formlayout, oj-form-layout > *, [data-oj-field]"
                ));
            }
        }
        const labelIds = Array.from(found);
        if (labelIds.length === 1) return { id: labelIds[0], via: "label" };

        // --- Strategy B: Oracle ADF LOV combobox by its standard empty prompt hint ---
        const HINT = "press down arrow key and then press enter key to make selection.";
        const attr = (el, name) => norm(el.getAttribute && el.getAttribute(name)).toLowerCase();
        const isHint = (el) => el && (attr(el, "title") === HINT
            || attr(el, "aria-label") === HINT || attr(el, "placeholder") === HINT);
        const lov = new Set();
        const lovById = new Map();
        for (const el of document.querySelectorAll("[role='combobox'], input, [role='textbox']")) {
            if (!isVisible(el) || !el.id) continue;
            let hit = isHint(el);
            if (!hit) {
                const cell = el.closest && el.closest("td, .af_inputComboboxListOfValues");
                if (cell && norm(cell.innerText || cell.textContent).toLowerCase() === HINT) {
                    hit = true;
                }
            }
            if (hit) { lov.add(el.id); lovById.set(el.id, el); }
        }
        const lovIds = Array.from(lov);
        if (lovIds.length === 1) return { id: lovIds[0], via: "lov_hint" };
        // Several empty LOV comboboxes: disambiguate by a nearby prompt/label matching the target.
        if (lovIds.length > 1) {
            const labelled = lovIds.filter((id) => {
                const grp = lovById.get(id).closest(
                    "td, .oj-flex-item, .oj-formlayout, tr, [data-oj-field]"
                );
                return grp && norm(grp.innerText || grp.textContent).toLowerCase().includes(target);
            });
            if (labelled.length === 1) return { id: labelled[0], via: "lov_hint_labelled" };
        }
        return null;
    }
    """
    result = facade._act_safe_page_eval(page, probe, target)
    if not isinstance(result, dict):
        return None
    control_id = str(result.get("id") or "").strip()
    if not control_id:
        return None
    escaped = control_id.replace("\\", "\\\\").replace('"', '\\"')
    return page.locator(f'[id="{escaped}"]')


def _act_body_marker(page: Page | None) -> str:
    result = facade._act_safe_page_eval(
        page,
        '() => String(document.body?.innerText || "").replace(/\\s+/g, " ").trim().slice(0, 800)',
    )
    return str(result or "").strip()


def _act_observe(page: Page | None, locator: Locator | None = None) -> dict[str, Any]:
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
        "guided_step": facade._act_current_guided_step(page),
        "guided_flow": facade._act_guided_flow_state(page),
        "dialog_count": facade._act_dialog_count(page),
        "active_element": facade._act_active_element(page),
        "body_marker": facade._act_body_marker(page),
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
        observation["target_value"] = facade._act_locator_value(locator)
        observation["target_text"] = facade._act_locator_text(locator)
        observation["target_meta"] = facade._act_extract_locator_metadata(locator)
    return observation


def _act_generic_click_postcondition(before: dict[str, Any], after: dict[str, Any]) -> bool:
    keys = (
        "url",
        "title",
        "guided_step",
        "guided_flow",
        "dialog_count",
        "body_marker",
        "active_element",
    )
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


def _act_active_element_matches_target(observation: dict[str, Any]) -> bool:
    active = observation.get("active_element") if isinstance(observation, dict) else {}
    target = observation.get("target_meta") if isinstance(observation, dict) else {}
    active = active if isinstance(active, dict) else {}
    target = target if isinstance(target, dict) else {}
    if not active or not target:
        return False
    active_tag = facade._act_normalize_text(active.get("tag"))
    target_tag = facade._act_normalize_text(target.get("tag"))
    tags_compatible = not active_tag or not target_tag or active_tag == target_tag
    active_id = facade._act_normalize_text(active.get("id"))
    target_id = facade._act_normalize_text(target.get("id"))
    if active_id and target_id and (active_id == target_id):
        return True
    if not tags_compatible:
        return False
    for active_key, target_key in (
        ("name", "name"),
        ("aria_label", "aria_label"),
        ("title", "title"),
    ):
        active_value = facade._act_normalize_text(active.get(active_key))
        target_value = facade._act_normalize_text(target.get(target_key))
        if active_value and target_value and (active_value == target_value):
            return True
    return False


def _act_observation_dialog_count(observation: dict[str, Any] | None) -> int:
    try:
        return int((observation or {}).get("dialog_count") or 0)
    except Exception:
        return 0


def _act_button_target_state_changed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    before_meta = before.get("target_meta") if isinstance(before, dict) else {}
    after_meta = after.get("target_meta") if isinstance(after, dict) else {}
    before_meta = before_meta if isinstance(before_meta, dict) else {}
    after_meta = after_meta if isinstance(after_meta, dict) else {}
    state_keys = (
        "aria_expanded",
        "aria_selected",
        "aria_checked",
        "checked",
        "disabled",
        "aria_disabled",
    )
    for key in state_keys:
        before_value = facade._act_normalize_text(before_meta.get(key))
        after_value = facade._act_normalize_text(after_meta.get(key))
        if before_value != after_value:
            return True
    return False


def _act_button_click_postcondition(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """Stricter postcondition for plain button clicks.

    A button's job is to advance the flow or change a control's state, so accept only a real
    effect: a navigation / dialog / guided-flow change, the button's own state or visibility
    changing, or its text/value changing. Two signals the generic postcondition trusts are
    tautological for a button and are deliberately discounted here:
      * focus landing on the button you just clicked (the expected side-effect of any click), and
      * a body-text wiggle while a dialog/drawer is still gating the view.
    This is what stops an Oracle drawer-commit button (e.g. "Update") from reporting success when
    the click landed but the drawer never closed.
    """
    for key in ("url", "title", "guided_step", "guided_flow", "dialog_count"):
        if before.get(key) != after.get(key):
            return True
    if before.get("target_visible") != after.get("target_visible"):
        return True
    if before.get("target_text") != after.get("target_text"):
        return True
    if before.get("target_value") != after.get("target_value"):
        return True
    if _act_button_target_state_changed(before, after):
        return True
    active_changed = before.get("active_element") != after.get("active_element")
    if active_changed and not facade._act_active_element_matches_target(after):
        return True
    body_changed = before.get("body_marker") != after.get("body_marker")
    if body_changed and _act_observation_dialog_count(after) == 0:
        return True
    return False


def _act_button_no_commit_failure(
    helper: str,
    page: Page | None,
    label: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any] | None:
    """Detect a button that actuated on its target but whose dialog/drawer never committed.

    Returns a failure descriptor (reason + any visible validation messages) when the strict click
    landed on the intended button (focus moved onto it) while a dialog/drawer was open before and
    stayed open after -- i.e. an Oracle "Update"/"OK"/"Save" inside a drawer that did not close.
    In that case the recovery cascade (which hunts for a *different* locator) is the wrong remedy,
    so the caller fails fast with the real reason instead of grinding Oracle handlers and AI
    self-repair on a control that already clicked. Returns None for any other helper/shape.
    """
    if helper != "click_button_target":
        return None
    if _act_observation_dialog_count(before) <= 0 or _act_observation_dialog_count(after) <= 0:
        return None
    if not facade._act_active_element_matches_target(after):
        return None
    messages = facade._act_collect_validation_messages(page) if page is not None else []
    reason = (
        f'Button "{label}" was clicked but the open dialog/drawer did not close and the flow did '
        f"not advance, so the action never committed."
    )
    if messages:
        reason += " Validation: " + "; ".join(messages)
    return {"reason": reason, "validation_messages": messages}


def _act_value_matches(expected: str, observed: str) -> bool:
    normalized_expected = facade._act_normalize_text(expected)
    normalized_observed = facade._act_normalize_text(observed)
    if not normalized_expected:
        return not normalized_observed
    if not normalized_observed:
        return False
    if normalized_expected == normalized_observed:
        return True
    return normalized_expected in normalized_observed or normalized_observed in normalized_expected


def _act_recorded_locator_roles(script_data: dict[str, Any] | None = None) -> list[str]:
    current = script_data if isinstance(script_data, dict) else facade._act_current_script_data()
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
        if facade._act_normalize_text(step.get("method")) != "get_by_role":
            continue
        args = step.get("args") or []
        role = facade._act_normalize_text(args[0] if args else "")
        if role and role not in roles:
            roles.append(role)
    return roles


def _act_page_visible_text(page: Page | None) -> str:
    result = facade._act_safe_page_eval(
        page,
        '() => {\n            const body = document?.body;\n            if (!body) return "";\n            return String(body.innerText || body.textContent || "").replace(/\\s+/g, " ").trim();\n        }',
    )
    return facade._act_normalize_text(result)


def _act_normalize_runtime_action_name(name: Any) -> str:
    normalized = str(name or "").strip().strip("_")
    if normalized.startswith("act_"):
        normalized = normalized[4:]
    return normalized or "unknown"


def _act_guided_flow_advanced(before: dict[str, Any], after: dict[str, Any]) -> bool:
    before_state = before if isinstance(before, dict) else {}
    after_state = after if isinstance(after, dict) else {}
    before_selected = facade._act_normalize_text(before_state.get("selected_step"))
    after_selected = facade._act_normalize_text(after_state.get("selected_step"))
    if before_selected and after_selected and (before_selected != after_selected):
        return True
    before_counter = facade._act_normalize_text(before_state.get("progress_counter"))
    after_counter = facade._act_normalize_text(after_state.get("progress_counter"))
    if before_counter and after_counter and (before_counter != after_counter):
        return True
    before_heading = facade._act_normalize_text(before_state.get("primary_heading"))
    after_heading = facade._act_normalize_text(after_state.get("primary_heading"))
    if before_heading and after_heading and (before_heading != after_heading):
        return True
    before_footer = {
        facade._act_normalize_text(item)
        for item in before_state.get("footer_actions") or []
        if facade._act_normalize_text(item)
    }
    after_footer = {
        facade._act_normalize_text(item)
        for item in after_state.get("footer_actions") or []
        if facade._act_normalize_text(item)
    }
    if before_footer != after_footer:
        if "continue" in before_footer and "continue" not in after_footer:
            return True
        if "submit" in before_footer and "submit" not in after_footer:
            return True
        if "submit" not in before_footer and "submit" in after_footer:
            return True
    return False


def _act_busy_indicator_count(page: Page | None) -> int:
    result = facade._act_safe_page_eval(
        page,
        "() => {\n            const selectors = [\n                '[aria-busy=\"true\"]',\n                '[role=\"progressbar\"]',\n                '.oj-progress-circle',\n                '.oj-progress-bar',\n                '.oj-progress-spinner',\n            ];\n            const isVisible = (node) => {\n                if (!node) return false;\n                const style = window.getComputedStyle(node);\n                if (!style) return false;\n                if (style.display === \"none\" || style.visibility === \"hidden\") return false;\n                const rect = node.getBoundingClientRect();\n                return rect.width > 0 && rect.height > 0;\n            };\n            let count = 0;\n            for (const selector of selectors) {\n                for (const node of document.querySelectorAll(selector)) {\n                    if (isVisible(node)) count += 1;\n                }\n            }\n            return count;\n        }",
    )
    try:
        return int(result or 0)
    except Exception:
        return 0


def _act_wait_for_observation_stability(
    page: Page | None, timeout_ms: int | None = None, quiet_ms: int | None = None
) -> dict[str, Any]:
    if page is None:
        return {}
    total_timeout = facade._act_resolve_wait_override_ms(
        timeout_ms, "ACT_POST_ACTION_STABILIZE_TIMEOUT_MS", 2500
    )
    stable_window = facade._act_resolve_wait_override_ms(
        quiet_ms, "ACT_POST_ACTION_STABILIZE_QUIET_MS", 600
    )
    if total_timeout <= 0 or stable_window <= 0:
        return facade._act_observe(page)
    deadline = time.time() + total_timeout / 1000.0
    last_observation = facade._act_observe(page)
    stable_since: float | None = None
    poll_ms = max(100, min(200, stable_window // 3 or 100))
    while time.time() < deadline:
        current_observation = facade._act_observe(page)
        if facade._act_busy_indicator_count(page) > 0 or current_observation != last_observation:
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


def _act_wait_for_field_processing(
    page: Page | None, *, env_name: str, default_ms: int = 5000
) -> None:
    if page is None:
        return
    wait_ms = facade._act_wait_ms(env_name, default_ms)
    if wait_ms > 0:
        page.wait_for_timeout(wait_ms)
    facade._act_wait_for_observation_stability(page)


def _act_retry_strict_click_after_oracle_ppr(
    page: Page | None,
    label: str,
    locator: Locator | None,
    postcondition,
    before: dict[str, Any],
) -> dict[str, Any] | None:
    """Bounded "minimal wait/delay" stage for the click path (the rung between strict and the
    Oracle recovery handlers).

    A strict click can time out only because Oracle is still rendering (PPR / _afrLoop navigation
    in flight), not because the recorded locator is wrong. Before the dispatcher falls to
    alternative-locator recovery -- which would mis-target on a half-rendered page -- give the
    EXACT recorded locator one bounded second chance once the page settles. Returns the post-click
    observation when the retry validates, else None (page already idle, no locator, or still no
    effect).

    It is gated on a visible Oracle busy indicator, which is precisely what distinguishes "still
    rendering, the element is coming" (extend + retry the same locator) from "page idle, this
    locator will never bind" (skip instantly -> recovery). So it is target-scoped, never blocks an
    already-idle page, and adds cost only on the failure path.
    """
    if page is None or locator is None:
        return None
    settle_ms = facade._act_wait_ms("ACT_ORACLE_PPR_SETTLE_MS", 10000)
    if settle_ms <= 0:
        return None
    if facade._act_busy_indicator_count(page) <= 0:
        return None
    deadline = time.time() + settle_ms / 1000.0
    poll_ms = facade._act_wait_ms("ACT_ORACLE_PPR_SETTLE_POLL_MS", 250)
    while time.time() < deadline and facade._act_busy_indicator_count(page) > 0:
        try:
            page.wait_for_timeout(poll_ms)
        except Exception:
            break
    remaining_ms = max(500, int((deadline - time.time()) * 1000))
    if not facade._act_locator_is_actionable(locator, timeout_ms=remaining_ms):
        return None
    try:
        facade._act_strict_click(locator, timeout_ms=remaining_ms)
    except Exception:
        return None
    after = facade._act_observe(page, locator)
    return after if postcondition(before, after) else None


def _act_settle_click_postcondition(
    page: Page | None,
    locator: Locator | None,
    postcondition,
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    """Give an async click effect a bounded chance to manifest before judging the postcondition.

    A click's real effect -- navigating to another page, opening or closing a dialog, an ADF PPR
    refresh -- is frequently asynchronous, so the observation captured immediately after the click
    can still show "nothing changed" even though the click worked. This polls the postcondition
    for a bounded window, re-observing, and returns as soon as it passes (else the last
    observation). It is what stops a navigation button (e.g. Oracle "Create Return") from being
    declared a no-commit / no-effect failure when its effect simply had not landed yet. Returns
    instantly when the postcondition already holds or the wait is disabled.
    """
    if page is None or postcondition(before, after):
        return after
    settle_ms = facade._act_wait_ms("ACT_CLICK_EFFECT_SETTLE_MS", 6000)
    if settle_ms <= 0:
        return after
    poll_ms = facade._act_wait_ms("ACT_CLICK_EFFECT_POLL_MS", 250)
    deadline = time.time() + settle_ms / 1000.0
    latest = after
    while time.time() < deadline:
        try:
            page.wait_for_timeout(poll_ms)
        except Exception:
            break
        latest = facade._act_observe(page, locator)
        if postcondition(before, latest):
            return latest
    return latest


def _act_experience_enabled() -> bool:
    if not facade._ACT_EXPERIENCE_STORE_PATH:
        return False
    return facade._act_env_flag("ACT_EXPERIENCE_ENABLED", "true")


def _act_control_family(helper: str) -> str:
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


def _act_oracle_surface_type(page: Page | None, observation: dict[str, Any] | None = None) -> str:
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


def _act_oracle_warning_dialog_state(page: Page | None) -> dict[str, Any]:
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
    result = facade._act_safe_page_eval(
        page,
        '() => {\n            const selectors = [\'[role="dialog"]\', \'[aria-modal="true"]\', \'.oj-dialog\', \'.oj-popup\'];\n            const normalize = (value, limit = 1200) => String(value || "").replace(/\\s+/g, " ").trim().slice(0, limit);\n            const buttonLabel = (button) => normalize(\n                button?.innerText\n                || button?.textContent\n                || button?.getAttribute?.("aria-label")\n                || button?.getAttribute?.("title")\n                || button?.getAttribute?.("value"),\n                80,\n            ).toLowerCase();\n            const isVisible = (node) => {\n                if (!node) return false;\n                const style = window.getComputedStyle(node);\n                if (!style) return false;\n                if (style.display === "none" || style.visibility === "hidden") return false;\n                const rect = node.getBoundingClientRect();\n                return rect.width > 0 && rect.height > 0;\n            };\n            const isWarningLike = (title, text) => {\n                const lowerTitle = String(title || "").toLowerCase();\n                const lowerText = String(text || "").toLowerCase();\n                return lowerTitle.includes("warning")\n                    || lowerText.startsWith("warning ")\n                    || lowerText.includes(" warning ")\n                    || lowerText.includes("duplicate")\n                    || lowerText.includes("payment terms");\n            };\n            const dialogEntry = (node) => {\n                const titleNode = node.querySelector("[role=\'heading\'], h1, h2, h3, .oj-dialog-title");\n                const title = normalize(titleNode?.innerText || titleNode?.textContent, 160);\n                const text = normalize(node.innerText || node.textContent, 1200);\n                const buttons = Array.from(\n                    node.querySelectorAll("button, [role=\'button\'], input[type=\'button\'], input[type=\'submit\']")\n                ).filter(isVisible);\n                const buttonTexts = buttons.map((button) => buttonLabel(button)).filter(Boolean);\n                const okButton = buttons.find((button) => buttonLabel(button) === "ok") || null;\n                const closeButton = buttons.find((button) => {\n                    const label = buttonLabel(button);\n                    return label === "close" || label === "x" || label === "×";\n                }) || null;\n                return {\n                    title,\n                    text,\n                    has_ok_button: buttonTexts.includes("ok"),\n                    warning_like: isWarningLike(title, text),\n                    ok_button_id: normalize(okButton?.id, 160),\n                    close_button_id: normalize(closeButton?.id, 160),\n                };\n            };\n            const seen = new Set();\n            const dialogs = [];\n            for (const selector of selectors) {\n                for (const node of document.querySelectorAll(selector)) {\n                    if (!isVisible(node) || seen.has(node)) continue;\n                    seen.add(node);\n                    dialogs.push(dialogEntry(node));\n                }\n            }\n\n            for (const button of Array.from(document.querySelectorAll("button, [role=\'button\'], input[type=\'button\'], input[type=\'submit\']"))) {\n                if (!isVisible(button)) continue;\n                const label = buttonLabel(button);\n                if (!["ok", "close", "x", "×"].includes(label)) continue;\n                let current = button.parentElement;\n                let depth = 0;\n                while (current && depth < 8) {\n                    if (isVisible(current) && !seen.has(current)) {\n                        const entry = dialogEntry(current);\n                        if (entry.warning_like) {\n                            seen.add(current);\n                            dialogs.push(entry);\n                            break;\n                        }\n                    }\n                    current = current.parentElement;\n                    depth += 1;\n                }\n            }\n            const warningDialog = dialogs.find((entry) => entry.warning_like) || null;\n            return {\n                dialog_count: dialogs.length,\n                warning_visible: Boolean(warningDialog),\n                warning_title: warningDialog ? warningDialog.title : "",\n                warning_text: warningDialog ? warningDialog.text : "",\n                ok_button_visible: Boolean(warningDialog && warningDialog.has_ok_button),\n                any_ok_button_visible: dialogs.some((entry) => entry.has_ok_button),\n                ok_button_id: warningDialog ? String(warningDialog.ok_button_id || "") : "",\n                close_button_id: warningDialog ? String(warningDialog.close_button_id || "") : "",\n            };\n        }',
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


def _act_error_looks_like_missing_button(error: Any) -> bool:
    error_text = facade._act_normalize_text(error)
    if not error_text:
        return False
    if any(
        snippet in error_text
        for snippet in ("strict mode violation", "pointer events", "intercepts", "another element")
    ):
        return False
    return (
        "waiting for" in error_text
        and "to be visible" in error_text
        or "not actionable" in error_text
        or "resolved to 0 elements" in error_text
        or ("timeout" in error_text)
    )


def _act_try_skip_optional_oracle_warning_ok(
    page: Page | None,
    locator: Locator | None,
    label: str,
    error: Any,
    observation: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if facade._act_normalize_text(label) != "ok":
        return None
    current_observation = observation or facade._act_observe(page, locator)
    try:
        url = str(page.url or "").strip().lower() if page is not None else ""
    except Exception:
        url = ""
    title = str((current_observation or {}).get("title") or "").strip().lower()
    if not ("/faces/" in url or "fscmui" in url or "oracle" in title):
        return None
    if locator is not None and facade._act_locator_is_actionable(locator, timeout_ms=250):
        return None
    if not facade._act_error_looks_like_missing_button(error):
        return None
    warning_state = facade._act_oracle_warning_dialog_state(page)
    if int(warning_state.get("dialog_count") or 0) > 0:
        return None
    return {
        "label": label,
        "surface_type": facade._act_oracle_surface_type(page, current_observation),
        "dialog_count": 0,
        "reason": "optional_warning_dialog_not_present",
    }


def _act_try_dismiss_oracle_warning_dialog(
    page: Page | None,
    locator: Locator | None,
    label: str,
    error: Any,
    *,
    observation: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if page is None:
        return None
    current_observation = observation or facade._act_observe(page, locator)
    try:
        url = str(page.url or "").strip().lower()
    except Exception:
        url = ""
    title = str((current_observation or {}).get("title") or "").strip().lower()
    if not ("/faces/" in url or "fscmui" in url or "oracle" in title):
        return None
    error_text = facade._act_normalize_text(error)
    if not any(
        snippet in error_text
        for snippet in (
            "intercepts pointer events",
            "afmodalglasspane",
            "another element",
            "pointer events",
        )
    ):
        return None
    warning_state = facade._act_oracle_warning_dialog_state(page)
    if not bool(warning_state.get("warning_visible")):
        return None
    candidate_specs: list[tuple[str, str]] = []
    ok_button_id = str(warning_state.get("ok_button_id") or "").strip()
    close_button_id = str(warning_state.get("close_button_id") or "").strip()
    if ok_button_id:
        candidate_specs.append(("oracle_warning_dialog_ok", ok_button_id))
    if close_button_id and close_button_id != ok_button_id:
        candidate_specs.append(("oracle_warning_dialog_close", close_button_id))
    active_element = (
        current_observation.get("active_element") if isinstance(current_observation, dict) else {}
    )
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
            if not facade._act_locator_is_actionable(resolved, timeout_ms=1000):
                continue
            facade._act_record_strategy_attempt(strategy_name)
            facade._act_strict_click(resolved)
            page.wait_for_timeout(facade._act_wait_ms("ACT_POST_CLICK_WAIT_MS", 250))
            after_state = facade._act_oracle_warning_dialog_state(page)
            if not bool(after_state.get("warning_visible")):
                return {
                    "label": label,
                    "surface_type": facade._act_oracle_surface_type(page, current_observation),
                    "warning_title": str(warning_state.get("warning_title") or "").strip(),
                    "warning_text": facade._act_trim_debug_text(
                        warning_state.get("warning_text"), 240
                    ),
                    "dialog_count_before": int(warning_state.get("dialog_count") or 0),
                    "dialog_count_after": int(after_state.get("dialog_count") or 0),
                    "strategy_name": strategy_name,
                    "button_id": button_id,
                }
        except Exception:
            continue
    return None


def _act_page_signature(
    page: Page | None, observation: dict[str, Any] | None = None
) -> dict[str, Any]:
    current_observation = observation or facade._act_observe(page)
    try:
        parsed = (
            facade.urlparse(str(page.url or "").strip())
            if page is not None
            else facade.urlparse("")
        )
    except Exception:
        parsed = facade.urlparse("")
    return {
        "host": str(parsed.netloc or "").strip().lower(),
        "path_hint": str(parsed.path or "").strip(),
        "title": str(current_observation.get("title") or "").strip(),
        "guided_step": str(current_observation.get("guided_step") or "").strip(),
        "surface_type": facade._act_oracle_surface_type(page, current_observation),
    }


def _act_failure_signature(
    current_page: Page | None, locator: Locator | None, error: Any
) -> dict[str, Any]:
    ready_state = facade._act_safe_page_eval(current_page, "() => document.readyState") or ""
    error_type = type(error).__name__ if error is not None else ""
    error_hint = str(error or "").strip()
    target_ready = False
    if locator is not None:
        target_ready = facade._act_locator_is_actionable(locator, timeout_ms=500)
    return {
        "error_type": str(error_type).strip(),
        "error_hint": error_hint[:200],
        "ready_state": str(ready_state or "").strip(),
        "busy_indicator_count": facade._act_busy_indicator_count(current_page),
        "target_ready": bool(target_ready),
        "popup_open": facade._act_dialog_count(current_page) > 0,
    }


def _act_capture_failure_screenshot() -> None:
    if not facade._ACT_FAILURE_SCREENSHOT_PATH or facade._ACT_LAST_PAGE is None:
        return
    try:
        Path(facade._ACT_FAILURE_SCREENSHOT_PATH).parent.mkdir(parents=True, exist_ok=True)
        facade._ACT_LAST_PAGE.screenshot(path=facade._ACT_FAILURE_SCREENSHOT_PATH, full_page=True)
    except Exception:
        return


def _act_capture_failure(error: Any = None) -> None:
    facade._act_capture_failure_screenshot()


def _act_capture_step(action: str) -> None:
    if not facade._ACT_STEP_ARTIFACTS_DIR or facade._ACT_LAST_PAGE is None:
        return
    try:
        Path(facade._ACT_STEP_ARTIFACTS_DIR).mkdir(parents=True, exist_ok=True)
        facade._ACT_STEP_INDEX += 1
        filename = f"step_{facade._ACT_STEP_INDEX:03d}_{re.sub('[^A-Za-z0-9._-]+', '_', str(action or 'step')).strip('._') or 'step'}.png"
        path = Path(facade._ACT_STEP_ARTIFACTS_DIR) / filename
        override_png = facade._ACT_NEXT_STEP_SCREENSHOT_OVERRIDE_PNG
        facade._ACT_NEXT_STEP_SCREENSHOT_OVERRIDE_PNG = None
        if isinstance(override_png, bytes) and override_png:
            path.write_bytes(override_png)
        else:
            facade._ACT_LAST_PAGE.screenshot(
                path=str(path),
                full_page=facade._act_env_flag("ACT_STEP_SCREENSHOT_FULL_PAGE", "false"),
            )
        facade._ACT_STEP_ARTIFACTS.append(
            {
                "index": facade._ACT_STEP_INDEX,
                "action": str(action or "step"),
                "local_path": str(path),
            }
        )
    except Exception:
        facade._ACT_NEXT_STEP_SCREENSHOT_OVERRIDE_PNG = None
        return


def _act_write_diagnostics() -> None:
    if not facade._ACT_DIAGNOSTICS_PATH:
        return
    try:
        facade._act_capture_live_snapshot_before_close(facade._ACT_LAST_PAGE)
    except Exception:
        pass
    facade._act_persist_diagnostics_snapshot()


def _act_resolve(value: Any) -> Any:
    """Substitute ``{{name}}`` placeholders with values captured earlier in this
    run by ai_extract(). Non-strings and unknown placeholders pass through
    unchanged (a stale placeholder then fails loudly at the locator, not here)."""
    if not isinstance(value, str) or "{{" not in value:
        return value

    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in facade._ACT_AI_EXTRACTED:
            return facade._ACT_AI_EXTRACTED[key]
        return match.group(0)

    return facade._ACT_AI_EXTRACT_PLACEHOLDER_RE.sub(_sub, value)


def _act_collect_validation_messages(page: Page) -> list[str]:
    result = facade._act_safe_page_eval(
        page,
        '() => {\n            const selectors = [\n                \'[role="alert"]\',\n                \'.oj-messagebanner-item\',\n                \'.oj-message-error\',\n                \'.oj-form-control-inline-message\',\n                \'.oj-invalid-text\',\n            ];\n            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();\n            const cleanMessage = (value) => normalize(value).replace(/\\s+Close$/i, "").trim();\n            const isVisible = (node) => {\n                if (!node) return false;\n                const style = window.getComputedStyle(node);\n                if (!style) return false;\n                if (style.display === "none" || style.visibility === "hidden") return false;\n                const rect = node.getBoundingClientRect();\n                return rect.width > 0 && rect.height > 0;\n            };\n            const getLabelText = (node) => {\n                if (!node) return "";\n                const directAttrs = ["aria-label", "title", "placeholder", "name"];\n                for (const attr of directAttrs) {\n                    const value = normalize(node.getAttribute(attr));\n                    if (value) return value;\n                }\n                const labelledBy = normalize(node.getAttribute("aria-labelledby"));\n                if (labelledBy) {\n                    for (const id of labelledBy.split(/\\s+/)) {\n                        const candidate = document.getElementById(id);\n                        const text = normalize(candidate && (candidate.innerText || candidate.textContent));\n                        if (text) return text;\n                    }\n                }\n                if (node.id) {\n                    const label = document.querySelector(`label[for="${node.id.replace(/"/g, \'\\\\"\')}"]`);\n                    const labelText = normalize(label && (label.innerText || label.textContent));\n                    if (labelText) return labelText;\n                }\n                const hintedParent = node.closest("[label-hint]");\n                const hintedLabel = normalize(hintedParent && hintedParent.getAttribute("label-hint"));\n                if (hintedLabel) return hintedLabel;\n                const fieldParent = node.closest("[data-oj-field]");\n                const fieldLabel = normalize(fieldParent && fieldParent.getAttribute("data-oj-field"));\n                if (fieldLabel) return fieldLabel;\n                return normalize(node.id) || "Required field";\n            };\n            const values = [];\n            for (const selector of selectors) {\n                for (const node of document.querySelectorAll(selector)) {\n                    if (!isVisible(node)) continue;\n                    const text = cleanMessage(node.innerText || node.textContent || "");\n                    if (text) values.push(text);\n                }\n            }\n            const seen = new Set(values);\n            const requiredSelectors = [\n                "input[aria-required=\'true\']",\n                "textarea[aria-required=\'true\']",\n                "select[aria-required=\'true\']",\n                "[role=\'combobox\'][aria-required=\'true\']",\n                "[required]",\n            ];\n            for (const selector of requiredSelectors) {\n                for (const node of document.querySelectorAll(selector)) {\n                    if (!isVisible(node)) continue;\n                    const value = normalize(\n                        node.value ??\n                        node.getAttribute("value") ??\n                        node.textContent ??\n                        ""\n                    );\n                    const invalid = normalize(node.getAttribute("aria-invalid")).toLowerCase() === "true";\n                    if (!invalid && value) continue;\n                    const label = getLabelText(node);\n                    const message = cleanMessage(label ? `${label}: Select a value.` : "Required field: Select a value.");\n                    if (message && !seen.has(message)) {\n                        values.push(message);\n                        seen.add(message);\n                    }\n                }\n            }\n            return values.filter(Boolean).slice(0, 8);\n        }',
    )
    return [str(item).strip() for item in result or [] if str(item).strip()]


def _act_resolve_page(args: tuple[Any, ...]) -> Page | None:
    for arg in args:
        if isinstance(arg, Page):
            return arg
    return facade._ACT_LAST_PAGE


def _act_resolve_primary_locator(args: tuple[Any, ...]) -> Locator | None:
    for arg in args:
        if isinstance(arg, Locator):
            return arg
    return None


def _act_finalize_action_log(
    action_type: str,
    label: str,
    status: str,
    duration_ms: int,
    *,
    error: Any = None,
    page: Page | None = None,
) -> None:
    attempts, unique_attempts, strategy = facade._act_strategy_snapshot()
    entry: dict[str, Any] = {
        "action": action_type,
        "label": label,
        "status": status,
        "duration_ms": duration_ms,
        "strategy": strategy,
        "step": len(facade._ACT_ACTION_LOG) + 1,
        "fallback_attempt_count": len(attempts),
        "fallback_strategy_count": len(unique_attempts),
        "fallback_strategies": attempts,
        "fallback_strategies_unique": unique_attempts,
        "ai_interactions": facade._act_clone_json_value(
            facade._ACT_CURRENT_STRATEGY.get("ai_interactions") or []
        ),
        "experience_interactions": facade._act_clone_json_value(
            facade._ACT_CURRENT_STRATEGY.get("experience_interactions") or []
        ),
    }
    script_data = facade._act_current_script_data()
    if script_data:
        entry["script_data"] = script_data
    recovery = facade._ACT_CURRENT_STRATEGY.get("recovery")
    if isinstance(recovery, dict) and recovery:
        entry["recovery"] = facade._act_clone_json_value(recovery)
    debug_payload = facade._ACT_CURRENT_STRATEGY.get("debug")
    if isinstance(debug_payload, dict) and debug_payload:
        entry["debug"] = facade._act_clone_json_value(debug_payload)
    if error is not None:
        entry["error"] = str(error)
        entry["failure_context"] = facade._act_capture_failure_context(
            page, action_type, label, error
        )
    facade._ACT_ACTION_LOG.append(entry)


def _act_goto_page(current_page: Page, url: str, **goto_kwargs) -> Any:
    facade._act_register_page(current_page)
    facade._ACT_SUPPRESS_PATCH_CAPTURE += 1
    try:
        return current_page.goto(url, **goto_kwargs)
    finally:
        facade._ACT_SUPPRESS_PATCH_CAPTURE = max(0, facade._ACT_SUPPRESS_PATCH_CAPTURE - 1)


def _act_raw_click(locator: Locator, current_page: Page, label: str) -> None:
    facade._act_register_page(current_page)
    locator.click()


def _act_raw_fill(locator: Locator, current_page: Page, label: str, value: str) -> None:
    facade._act_register_page(current_page)
    locator.fill(value)


def _act_raw_press(locator: Locator, current_page: Page, label: str, key: str) -> None:
    facade._act_register_page(current_page)
    locator.press(key)


def _act_login_submit_and_redirect(
    locator: Locator, current_page: Page, label: str, expected_url: str
) -> None:
    facade._act_register_page(current_page)
    locator.press("Enter")
    facade._act_wait_for_post_login_redirect(current_page, expected_url)


def _act_wait_after_interaction(page: Page | None) -> None:
    current_page = page or facade._ACT_LAST_PAGE
    if current_page is None:
        return
    wait_ms = facade._act_wait_ms(
        "ACT_AFTER_ACTION_WAIT_MS", facade._ACT_HARDCODED_AFTER_ACTION_WAIT_MS
    )
    try:
        current_page.wait_for_timeout(wait_ms)
    except Exception:
        pass
    facade._act_capture_page_snapshot(current_page)


def _act_wait_for_post_login_redirect(current_page: Page, expected_url: str) -> None:
    facade._act_register_page(current_page)
    timeout_ms = facade._act_wait_ms("ACT_LOGIN_REDIRECT_WAIT_MS", 15000)
    deadline = time.time() + timeout_ms / 1000.0
    normalized_expected = str(expected_url or "").strip()
    while time.time() < deadline:
        try:
            current_url = str(current_page.url or "").strip()
        except Exception:
            current_url = ""
        if normalized_expected and normalized_expected in current_url:
            return
        if (
            current_url
            and "signin" not in current_url.lower()
            and ("login" not in current_url.lower())
        ):
            return
        current_page.wait_for_timeout(250)
    raise RuntimeError("Post-login redirect did not settle within the configured timeout.")
