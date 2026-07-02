"""Auto-split from helpers_v2.py. `facade` is the helpers_v2 facade: the single
shared namespace, so monkeypatching helpers_v2.X and shared _ACT_* state
behave exactly as in the original module. Call shared helpers via `facade.`."""

from __future__ import annotations

import re
from typing import Any

from playwright.sync_api import Locator, Page

try:
    from .. import helpers_v2 as facade
except ImportError:  # pragma: no cover
    from src.runtime import helpers_v2 as facade

__all__ = [
    "_act_oracle_notification_badge_signature",
    "_act_try_expand_oracle_quick_actions",
    "_act_try_oracle_quick_action_exact_match",
    "_act_try_oracle_home_search",
    "_act_try_oracle_notification_badge",
    "_act_try_oracle_recorded_button_context",
    "_act_try_oracle_guided_action_card",
    "_act_try_open_oracle_select_single_with_keyboard",
]


def _act_oracle_notification_badge_signature(value: Any) -> str:
    normalized = facade._act_normalize_text(value)
    match = re.fullmatch("notifications\\s*\\(\\d+\\s+unread\\)", normalized)
    if not match:
        return ""
    return "notifications (unread)"


def _act_try_expand_oracle_quick_actions(page: Page, label: str) -> bool:
    try:
        target = page.get_by_text(label, exact=True)
        if facade._act_locator_is_actionable(target, timeout_ms=500):
            return False
    except Exception:
        pass
    candidates = (
        page.get_by_label("Show more quick actions"),
        page.get_by_text("Show More", exact=True),
    )
    for candidate in candidates:
        try:
            if not facade._act_locator_is_actionable(candidate.first, timeout_ms=1200):
                continue
            facade._act_record_strategy_attempt("oracle_quick_actions_expand")
            candidate.first.click(timeout=facade._act_wait_ms("ACT_ACTION_TIMEOUT_MS", 3000))
            page.wait_for_timeout(facade._act_wait_ms("ACT_QUICK_ACTIONS_EXPAND_WAIT_MS", 600))
            return True
        except Exception:
            continue
    return False


def _act_try_oracle_quick_action_exact_match(
    page: Page, label: str, error: Any, postcondition, *, allow_after_expand: bool = False
) -> str:
    error_text = str(error or "")
    lowered_error = error_text.lower()
    if not allow_after_expand:
        if "strict mode violation" not in lowered_error:
            return ""
        if 'get_by_role("link"' not in error_text and "get_by_role('link'" not in error_text:
            return ""
    label_text = str(label or "").strip()
    if not label_text:
        return ""
    exact_text = re.compile(f"^{re.escape(label_text)}$")
    candidates = [
        (
            "oracle_quick_action_exact_link",
            page.locator("a[type='quickaction']").filter(has_text=exact_text),
        ),
        (
            "oracle_quick_action_exact_class",
            page.locator("a.flat-quickactions-item-link").filter(has_text=exact_text),
        ),
        ("oracle_quick_action_exact_role", page.get_by_role("link", name=label_text, exact=True)),
        ("oracle_quick_action_exact_text", page.get_by_text(label_text, exact=True)),
    ]
    for strategy_name, candidate in candidates:
        try:
            resolved = candidate.first if hasattr(candidate, "first") else candidate
            if not facade._act_locator_is_actionable(resolved, timeout_ms=1200):
                continue
            before = facade._act_observe(page, resolved)
            facade._act_record_strategy_attempt(strategy_name)
            facade._act_strict_click(resolved)
            page.wait_for_timeout(facade._act_wait_ms("ACT_POST_CLICK_WAIT_MS", 250))
            after = facade._act_observe(page, resolved)
            if postcondition(before, after):
                return strategy_name
        except Exception:
            continue
    return ""


def _act_try_oracle_home_search(page: Page, label: str, postcondition) -> bool:
    observation = facade._act_observe(page)
    page_signature = facade._act_page_signature(page, observation)
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
            if facade._act_locator_is_actionable(resolved, timeout_ms=1200):
                search_box = resolved
                break
        except Exception:
            continue
    if search_box is None:
        return False
    try:
        facade._act_record_strategy_attempt("oracle_home_search")
        facade._act_strict_click(search_box)
        facade._act_strict_fill(
            search_box, label, timeout_ms=facade._act_wait_ms("ACT_TEXT_ENTRY_TIMEOUT_MS", 3000)
        )
        page.wait_for_timeout(facade._act_wait_ms("ACT_ORACLE_HOME_SEARCH_WAIT_MS", 750))
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
            if not facade._act_locator_is_actionable(resolved, timeout_ms=1200):
                continue
            before = facade._act_observe(page, resolved)
            facade._act_record_strategy_attempt(strategy_name)
            facade._act_strict_click(resolved)
            page.wait_for_timeout(facade._act_wait_ms("ACT_POST_CLICK_WAIT_MS", 250))
            after = facade._act_observe(page, resolved)
            if postcondition(before, after):
                return True
        except Exception:
            continue
    try:
        before_enter = facade._act_observe(page, search_box)
        facade._act_record_strategy_attempt("oracle_home_search_enter")
        search_box.press("Enter")
        page.wait_for_timeout(facade._act_wait_ms("ACT_POST_CLICK_WAIT_MS", 250))
        after_enter = facade._act_observe(page, search_box)
        if postcondition(before_enter, after_enter):
            return True
    except Exception:
        return False
    return False


def _act_try_oracle_notification_badge(page: Page, label: str, postcondition) -> str:
    if not facade._act_oracle_notification_badge_signature(label):
        return ""
    badge_pattern = re.compile("^Notifications\\s*\\(\\d+\\s+unread\\)$", re.IGNORECASE)
    candidates = [
        ("oracle_notification_badge_role", page.get_by_role("link", name=badge_pattern)),
        ("oracle_notification_badge_text", page.get_by_text(badge_pattern)),
    ]
    for strategy_name, candidate in candidates:
        try:
            resolved = candidate.first if hasattr(candidate, "first") else candidate
            if not facade._act_locator_is_actionable(resolved, timeout_ms=1200):
                continue
            before = facade._act_observe(page, resolved)
            facade._act_record_strategy_attempt(strategy_name)
            facade._act_strict_click(resolved)
            page.wait_for_timeout(facade._act_wait_ms("ACT_POST_CLICK_WAIT_MS", 250))
            after = facade._act_observe(page, resolved)
            if postcondition(before, after):
                return strategy_name
        except Exception:
            continue
    return ""


def _act_try_oracle_recorded_button_context(
    page: Page, locator: Locator, label: str, error: Any, postcondition
) -> str:
    error_text = str(error or "")
    lowered_error = error_text.lower()
    if "strict mode violation" not in lowered_error:
        return ""
    if 'get_by_role("button"' not in error_text and "get_by_role('button'" not in error_text:
        return ""
    recorded_context = facade._act_capture_locator_context(locator)
    if not isinstance(recorded_context, dict):
        return ""
    title_text = str(recorded_context.get("title") or "").strip()
    id_text = str(recorded_context.get("id") or "").strip()
    class_name = str(recorded_context.get("class_name") or "").strip()
    if not title_text and (not id_text):
        return ""
    page_title = ""
    try:
        page_title = str(page.title() or "").strip()
    except Exception:
        page_title = ""
    if not (
        "oracle" in facade._act_normalize_text(page_title)
        or "homebutton" in facade._act_normalize_text(class_name)
    ):
        return ""
    candidates: list[tuple[str, Locator]] = []
    if title_text:
        escaped_title = title_text.replace("\\", "\\\\").replace('"', '\\"')
        candidates.append(
            ("oracle_recorded_button_title", page.locator(f'button[title="{escaped_title}"]'))
        )
    if id_text:
        escaped_id = id_text.replace("\\", "\\\\").replace('"', '\\"')
        candidates.append(("oracle_recorded_button_id", page.locator(f'button[id="{escaped_id}"]')))
    for strategy_name, candidate in candidates:
        try:
            resolved = candidate.first if hasattr(candidate, "first") else candidate
            if not facade._act_locator_is_actionable(resolved, timeout_ms=1200):
                continue
            before = facade._act_observe(page, resolved)
            facade._act_record_strategy_attempt(strategy_name)
            facade._act_strict_click(resolved)
            page.wait_for_timeout(facade._act_wait_ms("ACT_POST_CLICK_WAIT_MS", 250))
            after = facade._act_observe(page, resolved)
            if postcondition(before, after):
                return strategy_name
        except Exception:
            continue
    return ""


def _act_try_oracle_guided_action_card(page: Page, label: str, postcondition) -> bool:
    observation = facade._act_observe(page)
    page_signature = facade._act_page_signature(page, observation)
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
        before = facade._act_observe(page, card)
        switch_locator = None
        before_switch_state = ""
        try:
            switch_locator = card.locator("[role='switch']").first
            before_switch_state = str(
                (facade._act_extract_locator_metadata(switch_locator) or {}).get("aria_checked")
                or ""
            ).strip()
        except Exception:
            switch_locator = None
        click_target = None
        click_strategy = strategy_name
        if facade._act_locator_is_actionable(card, timeout_ms=1200):
            click_target = card
        elif switch_locator is not None and facade._act_locator_is_actionable(
            switch_locator, timeout_ms=1200
        ):
            click_target = switch_locator
            click_strategy = f"{strategy_name}_switch"
        if click_target is None:
            continue
        facade._act_record_strategy_attempt(click_strategy)
        facade._act_strict_click(
            click_target, timeout_ms=facade._act_wait_ms("ACT_ACTION_CARD_CLICK_TIMEOUT_MS", 4000)
        )
        page.wait_for_timeout(facade._act_wait_ms("ACT_ACTION_CARD_POST_CLICK_WAIT_MS", 1500))
        after = facade._act_observe(page, card)
        after_switch_state = before_switch_state
        if switch_locator is not None:
            try:
                after_switch_state = str(
                    (facade._act_extract_locator_metadata(switch_locator) or {}).get("aria_checked")
                    or ""
                ).strip()
            except Exception:
                after_switch_state = before_switch_state
        if postcondition(before, after):
            return True
        if before_switch_state != after_switch_state:
            return True
        if after_switch_state == "true":
            return True
    return False


def _act_try_open_oracle_select_single_with_keyboard(
    page: Page, locator: Locator, error: Any
) -> str:
    error_text = str(error or "").lower()
    if "intercepts pointer events" not in error_text:
        return ""
    metadata = facade._act_extract_locator_metadata(locator)
    class_name = str(metadata.get("class_name") or "").strip()
    oracle_info = facade._act_safe_locator_eval(
        locator,
        '(node) => {\n            const host = node?.closest?.("oj-select-single, oj-c-select-single");\n            return {\n                has_oracle_host: Boolean(host),\n            };\n        }',
    )
    has_oracle_host = bool((oracle_info or {}).get("has_oracle_host"))
    if not has_oracle_host and "oj-searchselect-input" not in class_name:
        return ""
    timeout = facade._act_wait_ms("ACT_ACTION_TIMEOUT_MS", 3000)
    focus_wait_ms = facade._act_wait_ms("ACT_COMBOBOX_FOCUS_WAIT_MS", 100)
    open_strategies = [
        ("oracle_select_single_arrowdown", "ArrowDown"),
        ("oracle_select_single_enter", "Enter"),
    ]
    for strategy_name, key_name in open_strategies:
        try:
            before = facade._act_observe(page, locator)
            facade._act_record_strategy_attempt(strategy_name)
            try:
                locator.focus(timeout=timeout)
            except TypeError:
                locator.focus()
            except Exception:
                pass
            page.wait_for_timeout(focus_wait_ms)
            locator.press(key_name, timeout=timeout)
            page.wait_for_timeout(facade._act_wait_ms("ACT_COMBOBOX_OPEN_WAIT_MS", 350))
            after = facade._act_observe(page, locator)
            if facade._act_combobox_open_postcondition(before, after):
                return strategy_name
        except Exception:
            continue
    return ""
