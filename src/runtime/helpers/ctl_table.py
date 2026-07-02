"""Auto-split from helpers_v2.py. `facade` is the helpers_v2 facade: the single
shared namespace, so monkeypatching helpers_v2.X and shared _ACT_* state
behave exactly as in the original module. Call shared helpers via `facade.`."""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Locator, Page

try:
    from .. import helpers_v2 as facade
except ImportError:  # pragma: no cover
    from src.runtime import helpers_v2 as facade

__all__ = [
    "_act_table_field_click_postcondition",
    "_act_table_row_postcondition",
    "_act_recorded_locator_is_table_scoped",
    "_act_oracle_table_editor_table_id",
    "_act_active_oracle_table_editor",
    "_act_active_oracle_table_editor_locator",
    "_act_oracle_table_fill_postcondition",
    "_act_try_oracle_table_active_editor_fill",
    "_act_click_table_field",
    "_act_click_table_row",
]


def _act_table_field_click_postcondition(before: dict[str, Any], after: dict[str, Any]) -> bool:
    if facade._act_generic_click_postcondition(before, after):
        return True
    return facade._act_active_element_matches_target(after)


def _act_table_row_postcondition(before: dict[str, Any], after: dict[str, Any]) -> bool:
    before_meta = before.get("target_meta") if isinstance(before.get("target_meta"), dict) else {}
    after_meta = after.get("target_meta") if isinstance(after.get("target_meta"), dict) else {}
    before_selected = facade._act_normalize_text(before_meta.get("aria_selected"))
    after_selected = facade._act_normalize_text(after_meta.get("aria_selected"))
    if after_selected == "true" and before_selected != after_selected:
        return True
    before_classes = facade._act_normalize_text(before_meta.get("class_name"))
    after_classes = facade._act_normalize_text(after_meta.get("class_name"))
    if before_classes != after_classes and "selected" in after_classes:
        return True
    return facade._act_generic_click_postcondition(before, after)


def _act_recorded_locator_is_table_scoped(script_data: dict[str, Any] | None = None) -> bool:
    return any(
        role in {"row", "table", "cell", "gridcell"}
        for role in facade._act_recorded_locator_roles(script_data)
    )


def _act_oracle_table_editor_table_id(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        for pattern in facade._ACT_ORACLE_TABLE_EDITOR_ID_PATTERNS:
            match = pattern.search(text)
            if match:
                return str(match.group("table_id") or "").strip()
    return ""


def _act_active_oracle_table_editor(page: Page | None) -> dict[str, str]:
    result = facade._act_safe_page_eval(
        page,
        '() => {\n            const node = document.activeElement;\n            const text = (value) => String(value || "").replace(/\\s+/g, " ").trim();\n            const table = node?.closest?.(".oj-table-scroller, table.oj-table-element, [role=\'grid\'], [role=\'table\'], [id*=\':_ATp:ta\']");\n            if (!node) return {};\n            const row = node?.closest?.("[role=\'row\'], tr, .oj-table-body-row");\n            return {\n                tag: String(node?.tagName || "").toLowerCase(),\n                role: text(node?.getAttribute?.("role")),\n                id: text(node?.id),\n                name: text(node?.getAttribute?.("name")),\n                aria_label: text(node?.getAttribute?.("aria-label")),\n                title: text(node?.getAttribute?.("title")),\n                value: text(("value" in node ? node.value : "") || node?.getAttribute?.("value")),\n                row_text: text(row?.innerText || row?.textContent).slice(0, 400),\n                table_id: text(table?.id),\n            };\n        }',
    )
    active_info = result if isinstance(result, dict) else {}
    if not active_info:
        return {}
    inferred_table_id = facade._act_oracle_table_editor_table_id(
        active_info.get("id"), active_info.get("name")
    )
    if inferred_table_id and (not str(active_info.get("table_id") or "").strip()):
        active_info["table_id"] = inferred_table_id
    if not facade._act_normalize_text(active_info.get("row_text")) and (
        not facade._act_normalize_text(active_info.get("table_id"))
    ):
        return {}
    return active_info


def _act_active_oracle_table_editor_locator(
    page: Page | None,
) -> tuple[dict[str, str], Locator | None]:
    active_info = facade._act_active_oracle_table_editor(page)
    if page is None or not active_info:
        return (active_info, None)
    tag = facade._act_normalize_text(active_info.get("tag"))
    role = facade._act_normalize_text(active_info.get("role"))
    if tag not in {"input", "textarea"} and role not in {"textbox", "spinbutton"}:
        return (active_info, None)
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
    return (active_info, locator)


def _act_oracle_table_fill_postcondition(
    locator: Locator, active_locator: Locator | None, value: str
) -> bool:
    candidates = [active_locator, locator]
    for candidate in candidates:
        if candidate is None:
            continue
        observed = facade._act_locator_value(candidate) or facade._act_locator_text(candidate)
        if facade._act_value_matches(value, observed):
            return True
    return False


def _act_try_oracle_table_active_editor_fill(
    current_page: Page, locator: Locator, label: str, value: str
) -> dict[str, Any] | None:
    if not facade._act_recorded_locator_is_table_scoped():
        return None
    active_info, active_locator = facade._act_active_oracle_table_editor_locator(current_page)
    if active_locator is None:
        return None
    if facade._act_oracle_table_fill_postcondition(locator, active_locator, value):
        strategy_name = "oracle_table_active_editor_reflects_value"
        facade._act_record_strategy_attempt(strategy_name)
        return {
            "strategy_name": strategy_name,
            "active_element_id": str(active_info.get("id") or "").strip(),
            "table_id": str(active_info.get("table_id") or "").strip(),
            "row_text": str(active_info.get("row_text") or "").strip(),
            "used_keyboard_entry": False,
        }
    strategy_name = "oracle_table_active_editor_fill"
    facade._act_record_strategy_attempt(strategy_name)
    facade._act_fill_locator_via_keyboard(active_locator, value)
    facade._act_wait_for_field_processing(
        current_page, env_name="ACT_TEXTBOX_CHANGE_PROCESSING_WAIT_MS", default_ms=500
    )
    if facade._act_oracle_table_fill_postcondition(locator, active_locator, value):
        return {
            "strategy_name": strategy_name,
            "active_element_id": str(active_info.get("id") or "").strip(),
            "table_id": str(active_info.get("table_id") or "").strip(),
            "row_text": str(active_info.get("row_text") or "").strip(),
            "used_keyboard_entry": True,
        }
    raise RuntimeError(f'Oracle table editor for "{label}" did not reflect the requested value.')


def _act_click_table_field(locator: Locator, current_page: Page, label: str) -> None:
    facade._act_register_page(current_page)
    before = facade._act_observe(current_page, locator)
    debug_trace = facade._act_update_debug_detail(
        "click_table_field",
        {
            "label": label,
            "status": "strict_attempt",
            "before": facade._act_debug_observation_summary(before),
        },
    )
    facade._act_strict_click(locator)
    current_page.wait_for_timeout(facade._act_wait_ms("ACT_POST_CLICK_WAIT_MS", 250))
    after = facade._act_observe(current_page, locator)
    if facade._act_table_field_click_postcondition(before, after):
        debug_trace["after"] = facade._act_debug_observation_summary(after)
        debug_trace["resolved_by"] = "strict"
        debug_trace["status"] = "success"
        facade._act_set_debug_detail("click_table_field", debug_trace)
        return
    debug_trace["after"] = facade._act_debug_observation_summary(after)
    debug_trace["status"] = "failed"
    debug_trace["error"] = f'Table field "{label}" did not change focus or control state.'
    facade._act_set_debug_detail("click_table_field", debug_trace)
    raise RuntimeError(f'Table field "{label}" did not change focus or control state.')


def _act_click_table_row(locator: Locator, current_page: Page, label: str) -> None:
    facade._act_register_page(current_page)
    before = facade._act_observe(current_page, locator)
    debug_trace = facade._act_update_debug_detail(
        "click_table_row",
        {
            "label": label,
            "status": "strict_attempt",
            "before": facade._act_debug_observation_summary(before),
        },
    )
    facade._act_strict_click(locator)
    current_page.wait_for_timeout(facade._act_wait_ms("ACT_POST_CLICK_WAIT_MS", 250))
    after = facade._act_observe(current_page, locator)
    if facade._act_table_row_postcondition(before, after):
        debug_trace["after"] = facade._act_debug_observation_summary(after)
        debug_trace["resolved_by"] = "strict"
        debug_trace["status"] = "success"
        facade._act_set_debug_detail("click_table_row", debug_trace)
        return
    debug_trace["after"] = facade._act_debug_observation_summary(after)
    debug_trace["status"] = "failed"
    debug_trace["error"] = f'Table row "{label}" did not change row selection state.'
    facade._act_set_debug_detail("click_table_row", debug_trace)
    raise RuntimeError(f'Table row "{label}" did not change row selection state.')
