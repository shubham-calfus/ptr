"""Auto-split from helpers_v2.py. `facade` is the helpers_v2 facade: the single
shared namespace, so monkeypatching helpers_v2.X and shared _ACT_* state
behave exactly as in the original module. Call shared helpers via `facade.`."""

from __future__ import annotations

import time
from typing import Any

from playwright.sync_api import Locator, Page

try:
    from .. import helpers_v2 as facade
except ImportError:  # pragma: no cover
    from src.runtime import helpers_v2 as facade

__all__ = [
    "_act_click_button_target",
    "_act_click_text_target",
    "_act_dblclick_text_target",
    "_act_click_listbox_option",
    "_act_click_navigation_button",
]


def _act_click_button_target(locator: Locator, current_page: Page, label: str) -> None:
    facade._act_register_page(current_page)

    def _postcondition(before: dict[str, Any], after: dict[str, Any]) -> bool:
        if facade._act_button_click_postcondition(before, after):
            return True
        # An ADF LOV "Search" button populates a results table inside an af:popup that none of the
        # generic signals can see (the popup is not a dialog and its rows are past body_marker). A
        # populated results grid is the real postcondition for that one button; self-gated by id so
        # ordinary buttons are unaffected.
        return facade._act_adf_lov_query_results_populated(current_page, locator)

    facade._act_click_with_candidates(
        current_page, label, locator, "click_button_target", _postcondition
    )


def _act_click_text_target(locator: Locator, current_page: Page, label: str) -> None:
    facade._act_register_page(current_page)
    facade._act_click_with_candidates(
        current_page, label, locator, "click_text_target", facade._act_generic_click_postcondition
    )


def _act_dblclick_text_target(locator: Locator, current_page: Page, label: str) -> None:
    facade._act_register_page(current_page)
    before = facade._act_observe(current_page, locator)
    facade._act_strict_dblclick(locator)
    current_page.wait_for_timeout(facade._act_wait_ms("ACT_POST_CLICK_WAIT_MS", 250))
    after = facade._act_observe(current_page, locator)
    if facade._act_generic_click_postcondition(before, after):
        return
    raise RuntimeError(f'Double-click target "{label}" did not change page or control state.')


def _act_click_listbox_option(locator: Locator, current_page: Page, label: str) -> None:
    facade._act_click_text_target(locator, current_page, label)


def _act_click_navigation_button(locator: Locator, current_page: Page, label: str) -> None:
    facade._act_register_page(current_page)
    before = facade._act_observe(current_page, locator)
    before_step = before.get("guided_step") or ""
    guided_process = (
        str(facade._act_page_signature(current_page, before).get("surface_type") or "").strip()
        == "guided_process"
    )
    normalized_label = facade._act_normalize_text(label)

    def _is_disabled(observation: dict[str, Any]) -> bool:
        meta = observation.get("target_meta") if isinstance(observation, dict) else {}
        meta = meta if isinstance(meta, dict) else {}
        disabled = facade._act_normalize_text(meta.get("disabled"))
        if disabled == "true":
            return True
        aria_disabled = facade._act_normalize_text(meta.get("aria_disabled"))
        return aria_disabled == "true"

    before_disabled = _is_disabled(before)
    facade._act_strict_click(
        locator, timeout_ms=facade._act_wait_ms("ACT_NAV_BUTTON_CLICK_TIMEOUT_MS", 4000)
    )
    deadline = (
        time.time() + facade._act_wait_ms("ACT_NAV_BUTTON_POSTCONDITION_TIMEOUT_MS", 15000) / 1000.0
    )
    validation_grace_ms = facade._act_wait_ms("ACT_NAV_BUTTON_VALIDATION_GRACE_MS", 1200)
    validation_seen_at: float | None = None
    last_validation_messages: list[str] = []
    while time.time() < deadline:
        after = facade._act_observe(current_page, locator)
        after_step = after.get("guided_step") or ""
        if before_step and after_step and (before_step != after_step):
            return
        if guided_process:
            if facade._act_guided_flow_advanced(
                before.get("guided_flow") or {}, after.get("guided_flow") or {}
            ):
                return
            if before.get("url") != after.get("url"):
                return
            if before.get("title") != after.get("title"):
                return
        validation_messages = facade._act_collect_validation_messages(current_page)
        if validation_messages:
            if validation_messages != last_validation_messages:
                last_validation_messages = list(validation_messages)
                validation_seen_at = time.time()
            grace_elapsed = (
                validation_seen_at is not None
                and (time.time() - validation_seen_at) * 1000.0 >= validation_grace_ms
            )
            submit_disabled_after_click = (
                guided_process
                and normalized_label == "submit"
                and (not before_disabled)
                and _is_disabled(after)
            )
            if submit_disabled_after_click:
                current_page.wait_for_timeout(250)
                continue
            if not grace_elapsed or facade._act_busy_indicator_count(current_page) > 0:
                current_page.wait_for_timeout(250)
                continue
            prefix = f'Navigation button "{label}" did not advance'
            if before_step:
                prefix += f' from step "{before_step}"'
            raise RuntimeError(f"{prefix}. " + "; ".join(validation_messages))
        validation_seen_at = None
        last_validation_messages = []
        if not guided_process and facade._act_generic_click_postcondition(before, after):
            return
        current_page.wait_for_timeout(250)
    suffix = f' from step "{before_step}"' if before_step else ""
    validation_messages = facade._act_collect_validation_messages(current_page)
    if validation_messages:
        raise RuntimeError(
            f'Navigation button "{label}" did not advance{suffix}. '
            + "; ".join(validation_messages)
        )
    raise RuntimeError(
        f'''Navigation button "{label}" did not advance{suffix} within {facade._act_wait_ms("ACT_NAV_BUTTON_POSTCONDITION_TIMEOUT_MS", 15000)}ms. The click completed, but the guided-flow state, page title, and URL did not change, and no explicit validation message became visible.'''
    )
