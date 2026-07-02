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
    "_act_checkbox_state",
    "_act_checkbox_observed_state",
    "_act_checkbox_matches",
    "_act_checkbox_semantic_postcondition",
    "_act_set_checkbox_state",
    "_act_check_target",
    "_act_uncheck_target",
]


def _act_checkbox_state(locator: Locator) -> str:
    metadata = facade._act_extract_locator_metadata(locator)
    if isinstance(metadata, dict):
        normalized_metadata_state = facade._act_checkbox_observed_state(metadata)
        if normalized_metadata_state in {"true", "false", "mixed"}:
            return normalized_metadata_state
    try:
        return "true" if bool(locator.is_checked()) else "false"
    except Exception:
        pass
    checked = facade._act_safe_locator_eval(
        locator,
        '(node) => {\n            if (!node) return "";\n            const ariaChecked = String(node.getAttribute?.("aria-checked") || "").trim().toLowerCase();\n            if (ariaChecked === "true" || ariaChecked === "false" || ariaChecked === "mixed") return ariaChecked;\n            if (typeof node.checked === "boolean") return node.checked ? "true" : "false";\n            if (node.hasAttribute?.("checked")) return "true";\n            return "";\n        }',
    )
    normalized_checked = facade._act_normalize_text(checked)
    if normalized_checked in {"true", "false", "mixed"}:
        return normalized_checked
    return ""


def _act_checkbox_observed_state(metadata: dict[str, Any] | None) -> str:
    metadata = metadata if isinstance(metadata, dict) else {}
    normalized_aria_checked = facade._act_normalize_text(metadata.get("aria_checked"))
    if normalized_aria_checked in {"true", "false", "mixed"}:
        return normalized_aria_checked
    normalized_checked = facade._act_normalize_text(metadata.get("checked"))
    if normalized_checked in {"true", "false"}:
        return normalized_checked
    return ""


def _act_checkbox_matches(locator: Locator, desired_checked: bool) -> bool:
    desired_state = "true" if desired_checked else "false"
    return facade._act_checkbox_state(locator) == desired_state


def _act_checkbox_semantic_postcondition(
    before: dict[str, Any], after: dict[str, Any], desired_checked: bool
) -> bool:
    desired_state = "true" if desired_checked else "false"
    before_target = before.get("target_meta") if isinstance(before.get("target_meta"), dict) else {}
    after_target = after.get("target_meta") if isinstance(after.get("target_meta"), dict) else {}
    if facade._act_checkbox_observed_state(after_target) == desired_state:
        return True
    active = after.get("active_element") if isinstance(after.get("active_element"), dict) else {}
    if facade._act_checkbox_observed_state(active) != desired_state:
        return False
    if before_target and facade._act_active_element_matches_target(
        {"active_element": active, "target_meta": before_target}
    ):
        return True
    if after_target and facade._act_active_element_matches_target(
        {"active_element": active, "target_meta": after_target}
    ):
        return True
    return False


def _act_set_checkbox_state(
    locator: Locator, current_page: Page, label: str, desired_checked: bool
) -> None:
    facade._act_register_page(current_page)
    desired_state = "true" if desired_checked else "false"
    if facade._act_checkbox_matches(locator, desired_checked):
        return
    timeout_ms = facade._act_wait_ms("ACT_ACTION_TIMEOUT_MS", 3000)
    last_error: Exception | None = None
    before_observation = facade._act_observe(current_page, locator)
    try:
        locator.wait_for(state="visible", timeout=timeout_ms)
        try:
            locator.scroll_into_view_if_needed(timeout=min(timeout_ms, 1000))
        except Exception:
            pass
        facade._act_record_strategy_attempt("raw_set_checked")
        if desired_checked:
            locator.check(timeout=timeout_ms)
        else:
            locator.uncheck(timeout=timeout_ms)
    except Exception as exc:
        last_error = exc
    facade._act_wait_for_field_processing(
        current_page, env_name="ACT_CHECKBOX_CHANGE_PROCESSING_WAIT_MS", default_ms=750
    )
    if facade._act_checkbox_matches(locator, desired_checked):
        return
    after_raw_check = facade._act_observe(current_page, locator)
    if facade._act_checkbox_semantic_postcondition(
        before_observation, after_raw_check, desired_checked
    ):
        return
    try:
        facade._act_record_strategy_attempt("checkbox_click_fallback")
        facade._act_strict_click(locator, timeout_ms=timeout_ms)
    except Exception as exc:
        last_error = exc
    facade._act_wait_for_field_processing(
        current_page, env_name="ACT_CHECKBOX_CHANGE_PROCESSING_WAIT_MS", default_ms=750
    )
    if facade._act_checkbox_matches(locator, desired_checked):
        return
    after_click_fallback = facade._act_observe(current_page, locator)
    if facade._act_checkbox_semantic_postcondition(
        before_observation, after_click_fallback, desired_checked
    ):
        return
    if last_error is not None:
        raise RuntimeError(
            f'Checkbox "{label}" did not become {desired_state} after raw set_checked and click fallback.'
        ) from last_error
    raise RuntimeError(f'Checkbox "{label}" did not become {desired_state}.')


def _act_check_target(locator: Locator, current_page: Page, label: str) -> None:
    facade._act_set_checkbox_state(locator, current_page, label, True)


def _act_uncheck_target(locator: Locator, current_page: Page, label: str) -> None:
    facade._act_set_checkbox_state(locator, current_page, label, False)
