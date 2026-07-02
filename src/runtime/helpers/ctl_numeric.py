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
    "_act_recorded_locator_is_spinbutton",
    "_act_active_spinbutton_locator",
    "_act_oracle_spinbutton_fill_postcondition",
    "_act_try_oracle_spinbutton_fill",
    "_act_click_numeric_button_target",
]


def _act_recorded_locator_is_spinbutton(script_data: dict[str, Any] | None = None) -> bool:
    return "spinbutton" in facade._act_recorded_locator_roles(script_data)


def _act_active_spinbutton_locator(page: Page | None) -> tuple[dict[str, str], Locator | None]:
    active_info = facade._act_safe_page_eval(
        page,
        '() => {\n            const node = document.activeElement;\n            const text = (value) => String(value || "").replace(/\\s+/g, " ").trim();\n            if (!node) return {};\n            return {\n                tag: String(node?.tagName || "").toLowerCase(),\n                role: text(node?.getAttribute?.("role")),\n                id: text(node?.id),\n                name: text(node?.getAttribute?.("name")),\n                aria_label: text(node?.getAttribute?.("aria-label")),\n                title: text(node?.getAttribute?.("title")),\n                value: text(("value" in node ? node.value : "") || node?.getAttribute?.("value")),\n                aria_valuenow: text(node?.getAttribute?.("aria-valuenow")),\n                aria_valuetext: text(node?.getAttribute?.("aria-valuetext")),\n            };\n        }',
    )
    active_info = active_info if isinstance(active_info, dict) else {}
    if page is None or not active_info:
        return (active_info, None)
    role = facade._act_normalize_text(active_info.get("role"))
    if role != "spinbutton":
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


def _act_oracle_spinbutton_fill_postcondition(
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
        observed = facade._act_locator_value(candidate) or facade._act_locator_text(candidate)
        if facade._act_value_matches(value, observed):
            return True
    return facade._act_oracle_label_value_matches(current_page, label, value)


def _act_try_oracle_spinbutton_fill(
    current_page: Page, locator: Locator, label: str, value: str
) -> dict[str, Any] | None:
    if not facade._act_recorded_locator_is_spinbutton():
        return None
    active_info, active_locator = facade._act_active_spinbutton_locator(current_page)
    target_locator = active_locator or locator
    strategy_name = "oracle_spinbutton_keyboard_fill"
    facade._act_record_strategy_attempt(strategy_name)
    facade._act_fill_locator_via_keyboard(target_locator, value)
    try:
        target_locator.press("Tab", timeout=facade._act_wait_ms("ACT_TEXT_ENTRY_TIMEOUT_MS", 3000))
    except Exception:
        pass
    facade._act_wait_for_field_processing(
        current_page, env_name="ACT_TEXTBOX_CHANGE_PROCESSING_WAIT_MS", default_ms=500
    )
    if facade._act_oracle_spinbutton_fill_postcondition(
        current_page, locator, active_locator, label, value
    ):
        return {
            "strategy_name": strategy_name,
            "active_element_id": str(active_info.get("id") or "").strip(),
            "used_keyboard_entry": True,
        }
    raise RuntimeError(f'Oracle spinbutton "{label}" did not reflect the requested value.')


def _act_click_numeric_button_target(locator: Locator, current_page: Page, label: str) -> None:
    facade._act_register_page(current_page)
    facade._act_click_with_candidates(
        current_page,
        label,
        locator,
        "click_numeric_button_target",
        facade._act_generic_click_postcondition,
    )
