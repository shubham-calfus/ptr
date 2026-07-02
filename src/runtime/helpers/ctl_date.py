"""Auto-split from helpers_v2.py. `facade` is the helpers_v2 facade: the single
shared namespace, so monkeypatching helpers_v2.X and shared _ACT_* state
behave exactly as in the original module. Call shared helpers via `facade.`."""

from __future__ import annotations

import time

from playwright.sync_api import Locator, Page

try:
    from .. import helpers_v2 as facade
except ImportError:  # pragma: no cover
    from src.runtime import helpers_v2 as facade

__all__ = [
    "_act_wait_for_date_icon",
    "_act_pick_date_via_icon",
]


def _act_wait_for_date_icon(icon: Locator, current_page: Page, title: str) -> Locator:
    title_text = str(title or "").strip()
    timeout_ms = facade._act_wait_ms("ACT_DATE_ICON_READY_TIMEOUT_MS", 8000)
    poll_ms = facade._act_wait_ms("ACT_DATE_ICON_POLL_MS", 250)
    deadline = time.time() + timeout_ms / 1000.0
    ready_state = ""
    busy_indicators = 0
    candidates: list[tuple[str, Locator]] = []
    if title_text:
        escaped_title = title_text.replace("\\", "\\\\").replace('"', '\\"')
        candidates.extend(
            [
                (
                    "date_attr_match",
                    current_page.locator(
                        f'[title="{escaped_title}"], [aria-label="{escaped_title}"]'
                    ).first,
                ),
                ("date_label_match", current_page.get_by_label(title_text)),
            ]
        )
    while time.time() < deadline:
        ready_state = str(
            facade._act_safe_page_eval(current_page, "() => document.readyState") or ""
        ).strip()
        busy_indicators = facade._act_busy_indicator_count(current_page)
        if facade._act_locator_is_actionable(icon, timeout_ms=500):
            return icon
        for strategy_name, candidate in candidates:
            try:
                resolved = candidate.first if hasattr(candidate, "first") else candidate
            except Exception:
                resolved = candidate
            if facade._act_locator_is_actionable(resolved, timeout_ms=400):
                facade._act_record_strategy_attempt(strategy_name)
                return resolved
        current_page.wait_for_timeout(max(100, poll_ms))
    raise RuntimeError(
        f'''Date control "{title_text or "date picker"}" did not become ready within {timeout_ms}ms. ready_state={ready_state or "unknown"}; busy_indicators={busy_indicators}.'''
    )


def _act_pick_date_via_icon(
    icon: Locator, day: Locator, current_page: Page, title: str, day_label: str
) -> None:
    facade._act_register_page(current_page)
    icon_target = facade._act_wait_for_date_icon(icon, current_page, title)
    facade._act_strict_click(
        icon_target, timeout_ms=facade._act_wait_ms("ACT_DATE_ICON_CLICK_TIMEOUT_MS", 4000)
    )
    current_page.wait_for_timeout(facade._act_wait_ms("ACT_DATE_PICKER_WAIT_MS", 300))
    if not facade._act_locator_is_actionable(
        day, timeout_ms=facade._act_wait_ms("ACT_DATE_DAY_READY_TIMEOUT_MS", 5000)
    ):
        raise RuntimeError(
            f'''Date option "{day_label}" did not become ready after opening "{title}" within {facade._act_wait_ms("ACT_DATE_DAY_READY_TIMEOUT_MS", 5000)}ms.'''
        )
    before = facade._act_observe(current_page, day)
    facade._act_record_strategy_attempt("day_select")
    facade._act_strict_click(day)
    deadline = time.time() + facade._act_wait_ms("ACT_DATE_POST_SELECT_WAIT_MS", 6000) / 1000.0
    while time.time() < deadline:
        after = facade._act_observe(current_page, day)
        if int(after.get("dialog_count") or 0) < int(before.get("dialog_count") or 0):
            facade._act_wait_for_field_processing(
                current_page, env_name="ACT_DATE_CHANGE_PROCESSING_WAIT_MS", default_ms=5000
            )
            return
        if facade._act_generic_click_postcondition(before, after):
            facade._act_wait_for_field_processing(
                current_page, env_name="ACT_DATE_CHANGE_PROCESSING_WAIT_MS", default_ms=5000
            )
            return
        if int(before.get("dialog_count") or 0) > 0 and (
            not facade._act_locator_is_actionable(day, timeout_ms=250)
        ):
            facade._act_wait_for_field_processing(
                current_page, env_name="ACT_DATE_CHANGE_PROCESSING_WAIT_MS", default_ms=5000
            )
            return
        current_page.wait_for_timeout(200)
    raise RuntimeError(
        f'''Date option "{day_label}" did not apply within {facade._act_wait_ms("ACT_DATE_POST_SELECT_WAIT_MS", 6000)}ms after opening "{title}".'''
    )
