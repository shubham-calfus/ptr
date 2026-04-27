"""
Action optimizer for Playwright recordings.

Takes the raw action list from the parser and detects compound patterns that
should be executed as a single atomic operation with full Oracle ADF handling.

Compound patterns detected:
  1. combobox click + option click       → select_combobox
  2. textbox fill + Enter press          → fill_and_submit
  3. search icon click + result click    → search_and_select
  4. ADF menu trigger + option click     → adf_menu_select
  5. date icon click + day click         → date_pick
  6. search field fill + result click    → search_and_select (inline)
  7. textbox click + fill on same        → redundant click removed
  8. fill + Tab press on same            → redundant Tab removed
  9. password Enter + goto               → login_and_redirect

Also classifies single actions:
  - button "Continue"/"Submit"/"Next"    → navigation_button
  - button with numeric label            → numeric_button (date picker day)
"""

from __future__ import annotations

from typing import Any

from src.runtime.parser import Action


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NAV_BUTTON_LABELS = frozenset({
    "Continue", "Submit", "Next", "Review", "Back", "Go back",
})

_LOGIN_TEXTBOX_LABELS = frozenset({
    "username", "user name", "user id", "userid",
    "password", "email", "email address",
})

_SEARCH_KEYWORDS = frozenset({
    "search", "find", "look up", "person", "people", "candidate",
    "employee", "worker", "manager", "recruiter", "add as", "select",
})

_MENU_TRIGGER_LINK_KEYWORDS = frozenset({
    "action", "actions", "more", "menu", "options", "tasks",
})

_SKIPPABLE_RELATIONSHIP_BUTTONS = frozenset({"continue"})

_CLOSE_ACTION_TYPES = frozenset({
    "close_page",
    "close_context",
    "close_browser",
})


def _normalize(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def _is_login_field(name: str | None) -> bool:
    if not name:
        return False
    return _normalize(name) in _LOGIN_TEXTBOX_LABELS


def _is_search_like_label(name: str | None) -> bool:
    if not name:
        return False
    normalized = _normalize(name)
    return any(kw in normalized for kw in _SEARCH_KEYWORDS)


def _is_menu_like_link(action: Action | None) -> bool:
    if not (
        action
        and action.type == "click"
        and action.locator_method == "get_by_role"
        and action.role == "link"
        and action.name
    ):
        return False
    normalized = _normalize(action.name)
    return any(keyword in normalized for keyword in _MENU_TRIGGER_LINK_KEYWORDS)


def _is_navigation_button_click(action: Action | None) -> bool:
    return bool(
        action
        and action.type == "click"
        and action.role == "button"
        and action.name in _NAV_BUTTON_LABELS
    )


def _is_reporting_relationship_combobox(action: Action | None) -> bool:
    return bool(
        action
        and action.type == "click"
        and action.role == "combobox"
        and _normalize(action.name) == "reporting relationship"
    )


def _is_close_action(action: Action | None) -> bool:
    return bool(action and action.type in _CLOSE_ACTION_TYPES)


def _same_locator(a: Action, b: Action) -> bool:
    """Check if two actions target the same element."""
    if a.page_var != b.page_var:
        return False
    if a.locator_method != b.locator_method:
        return False
    if a.role != b.role:
        return False
    if a.name != b.name:
        return False
    if a.selector != b.selector:
        return False
    return True


def _is_oracle_select_locator(action: Action) -> bool:
    """Check if a locator targets an Oracle single-select widget."""
    if not action.selector:
        return False
    lower = action.selector.lower()
    return any(token in lower for token in (
        "singleselect", "oj-select-single", "searchselect", "select-single",
    ))


def _values_match(fill_value: str | None, option_name: str | None) -> bool:
    """Check if a fill value and option text are related (for search+select)."""
    if not fill_value or not option_name:
        return False
    nf = _normalize(fill_value)
    no = _normalize(option_name)
    if not nf or not no:
        return False
    return nf == no or nf in no or no in nf


def _same_search_field(open_action: Action | None, fill_action: Action | None) -> bool:
    if not (
        open_action
        and fill_action
        and open_action.page_var == fill_action.page_var
        and open_action.type == "click"
        and open_action.role == "combobox"
        and fill_action.type == "fill"
        and fill_action.role in ("textbox", "combobox")
    ):
        return False
    if _normalize(open_action.name) != _normalize(fill_action.name):
        return False
    return _is_search_like_label(fill_action.name or open_action.name)


# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------

def optimize(actions: list[Action]) -> list[Action]:
    """Detect compound patterns and merge/annotate actions.

    Returns a new list of Actions. Compound actions get a new type
    (e.g., "select_combobox") and carry the data from both constituent actions.
    """
    result: list[Action] = []
    i = 0
    n = len(actions)

    while i < n:
        action = actions[i]
        next_action = actions[i + 1] if i + 1 < n else None

        # --- Pattern: redundant textbox click used only for focus ---
        if (
            action.type == "click"
            and action.role in ("textbox", "spinbutton")
            and not _is_login_field(action.name)
        ):
            if next_action is None or _is_close_action(next_action):
                i += 1
                continue
            if _is_navigation_button_click(next_action):
                i += 1
                continue
            if next_action.type in ("fill", "press") and _same_locator(action, next_action):
                i += 1
                continue

        # --- Pattern: fill + Tab press on same (redundant Tab) ---
        if (
            action.type == "fill"
            and action.role in ("textbox", "spinbutton")
            and not _is_login_field(action.name)
            and i + 1 < n
            and actions[i + 1].type == "press"
            and actions[i + 1].key == "Tab"
            and _same_locator(action, actions[i + 1])
            and i + 2 < n
            and not _same_locator(action, actions[i + 2])
        ):
            result.append(action)  # keep the fill
            i += 2  # skip fill + Tab, Tab is dropped
            continue

        # --- Pattern: textbox fill + Enter press → fill_and_submit ---
        if (
            action.type == "fill"
            and action.role in ("textbox", "spinbutton")
            and not _is_login_field(action.name)
            and i + 1 < n
            and actions[i + 1].type == "press"
            and actions[i + 1].key == "Enter"
            and _same_locator(action, actions[i + 1])
        ):
            merged = Action(
                type="fill_and_submit",
                line=action.line,
                raw=f"{action.raw}\n{actions[i + 1].raw}",
                page_var=action.page_var,
                locator_steps=action.locator_steps,
                value=action.value,
                key="Enter",
                locator_method=action.locator_method,
                role=action.role,
                name=action.name,
                exact=action.exact,
            )
            result.append(merged)
            i += 2
            continue

        # --- Pattern: password Enter + goto → login_and_redirect ---
        if (
            action.type == "press"
            and action.key == "Enter"
            and action.name
            and _normalize(action.name) == "password"
            and i + 1 < n
            and actions[i + 1].type == "goto"
        ):
            merged = Action(
                type="login_and_redirect",
                line=action.line,
                raw=f"{action.raw}\n{actions[i + 1].raw}",
                page_var=action.page_var,
                locator_steps=action.locator_steps,
                key="Enter",
                url=actions[i + 1].url,
                locator_method=action.locator_method,
                role=action.role,
                name=action.name,
            )
            result.append(merged)
            i += 2
            continue

        # --- Pattern: combobox click + option/cell/gridcell click → select_combobox ---
        if (
            action.type == "click"
            and action.role == "combobox"
            and i + 1 < n
            and actions[i + 1].type == "click"
            and actions[i + 1].role in ("option", "cell", "gridcell")
        ):
            option_action = actions[i + 1]
            merged = Action(
                type="select_combobox",
                line=action.line,
                raw=f"{action.raw}\n{option_action.raw}",
                page_var=action.page_var,
                locator_steps=action.locator_steps,
                value=option_action.name,  # the option text
                locator_method=action.locator_method,
                role=action.role,
                name=action.name,  # combobox label
                exact=action.exact,
                action_kwargs={
                    "option_role": option_action.role,
                    "option_name": option_action.name,
                    "option_exact": option_action.exact,
                    "option_locator_steps": [
                        {
                            "method": s.method,
                            **({"args": s.args} if s.args else {}),
                            **({"kwargs": s.kwargs} if s.kwargs else {}),
                        }
                        for s in option_action.locator_steps
                    ],
                },
            )
            result.append(merged)
            i += 2
            continue

        # --- Pattern: Oracle single-select locator + option click → select_combobox ---
        if (
            action.type == "click"
            and _is_oracle_select_locator(action)
            and i + 1 < n
            and actions[i + 1].type == "click"
            and (
                actions[i + 1].role in ("option", "cell", "gridcell")
                or actions[i + 1].locator_method == "get_by_text"
            )
        ):
            option_action = actions[i + 1]
            merged = Action(
                type="select_combobox",
                line=action.line,
                raw=f"{action.raw}\n{option_action.raw}",
                page_var=action.page_var,
                locator_steps=action.locator_steps,
                value=option_action.name,
                locator_method=action.locator_method,
                name="",  # Oracle single-select has no label from locator
                selector=action.selector,
                action_kwargs={
                    "option_role": option_action.role,
                    "option_name": option_action.name,
                    "option_exact": option_action.exact,
                },
            )
            result.append(merged)
            i += 2
            continue

        # --- Pattern: combobox click + fill search field + result click → search_and_select ---
        if (
            action.type == "click"
            and action.role == "combobox"
            and i + 2 < n
            and _same_search_field(action, actions[i + 1])
            and actions[i + 2].type == "click"
            and actions[i + 2].locator_method in ("get_by_text", "get_by_role")
            and actions[i + 2].role in (None, "option", "cell", "gridcell")
            and (
                _values_match(actions[i + 1].value, actions[i + 2].name)
                or _is_search_like_label(actions[i + 1].name)
            )
        ):
            fill_action = actions[i + 1]
            option_action = actions[i + 2]
            merged = Action(
                type="search_and_select",
                line=action.line,
                raw=f"{action.raw}\n{fill_action.raw}\n{option_action.raw}",
                page_var=fill_action.page_var,
                locator_steps=fill_action.locator_steps,
                name=fill_action.name,
                value=option_action.name,
                locator_method=fill_action.locator_method,
                role=fill_action.role,
                exact=fill_action.exact,
                action_kwargs={
                    "trigger_kind": "fill",
                    "fill_value": fill_action.value,
                    "option_kind": option_action.locator_method.replace("get_by_", "")
                        if option_action.locator_method else "text",
                    "option_role": option_action.role,
                    "option_name": option_action.name,
                    "option_exact": option_action.exact,
                },
            )
            result.append(merged)
            i += 3
            continue

        # --- Pattern: search icon (get_by_title "Search: X") + result click → search_and_select ---
        if (
            action.type == "click"
            and action.locator_method == "get_by_title"
            and action.name
            and action.name.lower().startswith("search:")
            and "select date" not in (action.name or "").lower()
            and i + 1 < n
            and actions[i + 1].type == "click"
            and actions[i + 1].locator_method in ("get_by_text", "get_by_role")
        ):
            option_action = actions[i + 1]
            merged = Action(
                type="search_and_select",
                line=action.line,
                raw=f"{action.raw}\n{option_action.raw}",
                page_var=action.page_var,
                locator_steps=action.locator_steps,
                name=action.name,  # "Search: Person"
                value=option_action.name,  # the option text
                locator_method=action.locator_method,
                action_kwargs={
                    "trigger_kind": "title",
                    "option_kind": option_action.locator_method.replace("get_by_", "")
                        if option_action.locator_method else "text",
                    "option_role": option_action.role,
                    "option_name": option_action.name,
                    "option_exact": option_action.exact,
                },
            )
            result.append(merged)
            i += 2
            continue

        # --- Pattern: fill textbox + click text/option result → search_and_select (inline) ---
        if (
            action.type == "fill"
            and action.role in ("textbox", "combobox")
            and i + 1 < n
            and actions[i + 1].type == "click"
            and actions[i + 1].locator_method in ("get_by_text", "get_by_role")
            and actions[i + 1].role in (None, "option", "cell", "gridcell")
            and (
                _values_match(action.value, actions[i + 1].name)
                or _is_search_like_label(action.name)
            )
        ):
            option_action = actions[i + 1]
            merged = Action(
                type="search_and_select",
                line=action.line,
                raw=f"{action.raw}\n{option_action.raw}",
                page_var=action.page_var,
                locator_steps=action.locator_steps,
                name=action.name,  # field label
                value=option_action.name,  # selected option text
                locator_method=action.locator_method,
                role=action.role,
                action_kwargs={
                    "trigger_kind": "fill",
                    "fill_value": action.value,
                    "option_kind": option_action.locator_method.replace("get_by_", "")
                        if option_action.locator_method else "text",
                    "option_role": option_action.role,
                    "option_name": option_action.name,
                    "option_exact": option_action.exact,
                },
            )
            result.append(merged)
            i += 2
            continue

        # --- Pattern: ADF menu trigger (get_by_title / menu-like link) + text option ---
        if (
            action.type == "click"
            and action.locator_method in ("get_by_title", "get_by_role")
            and action.name
            and not (action.name or "").lower().startswith("search:")
            and "select date" not in (action.name or "").lower()
            and (
                action.locator_method == "get_by_title"
                or _is_menu_like_link(action)
            )
            and i + 1 < n
            and actions[i + 1].type == "click"
            and actions[i + 1].locator_method == "get_by_text"
        ):
            option_action = actions[i + 1]
            trigger_kind = "title" if action.locator_method == "get_by_title" else "link"
            merged = Action(
                type="adf_menu_select",
                line=action.line,
                raw=f"{action.raw}\n{option_action.raw}",
                page_var=action.page_var,
                locator_steps=action.locator_steps,
                name=action.name,  # trigger label
                value=option_action.name,  # option text
                locator_method=action.locator_method,
                action_kwargs={
                    "trigger_kind": trigger_kind,
                    "option_name": option_action.name,
                    "option_exact": option_action.exact,
                },
            )
            result.append(merged)
            i += 2
            continue

        # --- Pattern: date icon (get_by_title "Select Date...") + day button ---
        if (
            action.type == "click"
            and action.locator_method == "get_by_title"
            and action.name
            and "select date" in (action.name or "").lower()
            and i + 1 < n
            and actions[i + 1].type == "click"
            and actions[i + 1].role in ("button", "cell", "gridcell")
            and actions[i + 1].name
            and actions[i + 1].name.strip().isdigit()
        ):
            day_action = actions[i + 1]
            merged = Action(
                type="date_pick",
                line=action.line,
                raw=f"{action.raw}\n{day_action.raw}",
                page_var=action.page_var,
                locator_steps=action.locator_steps,
                name=action.name,  # "Select Date: Start Date"
                value=day_action.name,  # "15" (day number)
                locator_method=action.locator_method,
                action_kwargs={
                    "day_role": day_action.role,
                    "day_label": day_action.name,
                },
            )
            result.append(merged)
            i += 2
            continue

        # --- Pattern: stray Continue before Reporting Relationship combobox ---
        if (
            action.type == "click"
            and action.role == "button"
            and _normalize(action.name) in _SKIPPABLE_RELATIONSHIP_BUTTONS
            and _is_reporting_relationship_combobox(next_action)
        ):
            i += 1
            continue

        # --- Single action classification ---

        # Navigation buttons
        if (
            action.type == "click"
            and action.role == "button"
            and action.name in _NAV_BUTTON_LABELS
        ):
            action = Action(
                type="navigation_button",
                line=action.line,
                raw=action.raw,
                page_var=action.page_var,
                locator_steps=action.locator_steps,
                name=action.name,
                locator_method=action.locator_method,
                role=action.role,
                exact=action.exact,
                action_args=action.action_args,
                action_kwargs=action.action_kwargs,
            )

        # Numeric button (date picker day)
        elif (
            action.type == "click"
            and action.role == "button"
            and action.name
            and action.name.strip().isdigit()
        ):
            action = Action(
                type="numeric_button",
                line=action.line,
                raw=action.raw,
                page_var=action.page_var,
                locator_steps=action.locator_steps,
                name=action.name,
                locator_method=action.locator_method,
                role=action.role,
                exact=action.exact,
            )

        result.append(action)
        i += 1

    return result


def optimize_to_dicts(actions: list[Action]) -> list[dict[str, Any]]:
    """Optimize actions and return as plain dicts."""
    return [a.to_dict() for a in optimize(actions)]
