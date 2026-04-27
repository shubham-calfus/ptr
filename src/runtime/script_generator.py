"""
Script generator for Playwright recordings.

Takes an optimized action list and generates a Python script where supported
actions are routed through the appropriate _ptr_* helper function. If a parsed
action still falls outside resilient helper coverage, the generator raises a
coverage error instead of emitting a silent raw fallback.

The generated script is executed by subprocess.run() just like the old pipeline.
The _ptr_* runtime helpers are imported by tools.py from the dedicated
runtime module, and the AST generator emits the full execution wrapper directly.
Preparation no longer embeds a giant helper blob into each generated script.
"""

from __future__ import annotations

from typing import Any

from src.runtime.parser import Action, LocatorStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class CoverageError(ValueError):
    """Raised when the AST generator encounters actions outside helper coverage."""

_LOGIN_TEXTBOX_LABELS = frozenset({
    "username", "user name", "user id", "userid",
    "password", "email", "email address",
})

_NAV_BUTTON_LABELS = frozenset({
    "Continue", "Submit", "Next", "Review", "Back", "Go back",
})


def _normalize_label(name: str | None) -> str:
    return " ".join(str(name or "").lower().split())


def _is_login_field(name: str | None) -> bool:
    return _normalize_label(name) in _LOGIN_TEXTBOX_LABELS


def _escape(value: Any) -> str:
    """Escape a value for embedding in generated Python source."""
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        # Use repr for safe escaping
        return repr(value)
    if isinstance(value, dict):
        items = ", ".join(f"{_escape(k)}: {_escape(v)}" for k, v in value.items())
        return f"{{{items}}}"
    if isinstance(value, (list, tuple)):
        items = ", ".join(_escape(v) for v in value)
        return f"[{items}]"
    return repr(value)


def _build_locator_expr(page_var: str, steps: list[LocatorStep]) -> str:
    """Build a Playwright locator expression from steps.

    e.g.: page.get_by_role("textbox", name="Username")
    """
    parts = [page_var]
    for step in steps:
        if step.is_property:
            parts.append(f".{step.method}")
        else:
            args_parts = [_escape(a) for a in step.args]
            kwargs_parts = [f"{k}={_escape(v)}" for k, v in step.kwargs.items()]
            all_parts = args_parts + kwargs_parts
            parts.append(f".{step.method}({', '.join(all_parts)})")
    return "".join(parts)


def _serialize_locator_steps(steps: list[LocatorStep]) -> list[dict[str, Any]]:
    return [
        {
            "method": step.method,
            **({"args": step.args} if step.args else {}),
            **({"kwargs": step.kwargs} if step.kwargs else {}),
            **({"is_property": True} if step.is_property else {}),
        }
        for step in steps
    ]


def _build_script_data(
    action: Action,
    tracked_action: str,
    helper_name: str,
    page_var: str,
    *,
    primary_locator_expr: str | None = None,
    secondary_locator_expr: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tracked_action": tracked_action,
        "helper_name": helper_name,
        "line": action.line,
        "raw": " ".join(str(action.raw or "").split()),
        "page_var": page_var,
        "parsed_action": action.to_dict(),
    }
    if primary_locator_expr is not None:
        payload["primary_locator_expr"] = primary_locator_expr
    if secondary_locator_expr is not None:
        payload["secondary_locator_expr"] = secondary_locator_expr
    if action.locator_steps:
        payload["primary_locator_steps"] = _serialize_locator_steps(action.locator_steps)
    if extra:
        payload.update(extra)
    return payload


def _tracked_action_lines(
    action: Action,
    tracked_action: str,
    label: str,
    helper_name: str,
    call_args: list[str],
    page_var: str,
    *,
    primary_locator_expr: str | None = None,
    secondary_locator_expr: str | None = None,
    extra: dict[str, Any] | None = None,
) -> list[str]:
    script_data = _build_script_data(
        action,
        tracked_action,
        helper_name,
        page_var,
        primary_locator_expr=primary_locator_expr,
        secondary_locator_expr=secondary_locator_expr,
        extra=extra,
    )
    return [
        f"    _ptr_set_script_data({_escape(script_data)})",
        f"    _ptr_tracked_action({_escape(tracked_action)}, {_escape(label)}, {helper_name}, {', '.join(call_args)})",
    ]


def _format_action_preview(action: Action) -> str:
    preview = " ".join(str(action.raw or "").split())
    if len(preview) > 160:
        preview = preview[:157] + "..."
    return preview


def _unsupported_action_reason(action: Action) -> str | None:
    if action.type not in _GENERATORS:
        return f'Unhandled action type "{action.type}".'

    if action.type == "click":
        if not action.locator_steps:
            return "Click action is missing locator steps."
        role = action.role
        method = action.locator_method
        label = action.name or ""

        if role in ("textbox", "spinbutton"):
            return None
        if role == "combobox":
            return None
        if role == "button":
            return None
        if method in ("get_by_text", "get_by_title"):
            return None
        if role in ("link", "option", "cell", "gridcell", "tab", "menuitem"):
            return None
        if method in ("get_by_label", "get_by_placeholder", "get_by_alt_text", "get_by_test_id"):
            return None
        if role == "listbox" and len(action.locator_steps) > 1:
            return None
        return (
            "Click target does not map to a resilient helper "
            f"(locator_method={method!r}, role={role!r})."
        )

    if action.type in {"select_option", "check", "uncheck", "set_input_files", "hover", "dblclick"}:
        return (
            f'Action "{action.type}" still relies on a raw Playwright call. '
            "Add helper coverage before replaying this recording via AST."
        )

    if action.type == "fill" and not action.locator_steps:
        return "Fill action is missing locator steps."

    if action.type == "press" and not action.locator_steps:
        return "Press action is missing locator steps."

    return None


def _raise_coverage_error(action: Action, reason: str) -> None:
    raise CoverageError(
        "AST generator found an unsupported or unsafe action:\n"
        f"- line {action.line}: {reason}\n"
        f"  source: {_format_action_preview(action)}"
    )


# ---------------------------------------------------------------------------
# Action code generators
# ---------------------------------------------------------------------------

def _gen_setup_browser(action: Action, page_var: str) -> list[str]:
    # Generate headless=False here — the runtime launcher decides the actual
    # headless mode based on PTR_HEADLESS (default: false). This preserves the
    # same external behavior while keeping browser policy inside the runtime.
    return [f"    browser = _ptr_launch_chromium(playwright, headless=False)"]


def _gen_setup_context(action: Action, page_var: str) -> list[str]:
    kwargs = action.action_kwargs or {}
    # Pass viewport through if present
    if kwargs:
        kwargs_str = ", ".join(f"{k}={_escape(v)}" for k, v in kwargs.items())
        return [f"    context = browser.new_context({kwargs_str})"]
    return ["    context = browser.new_context()"]


def _gen_setup_page(action: Action, page_var: str) -> list[str]:
    page_source_var = action.page_source_var or "context"
    return [f"    {page_var} = _ptr_register_page({page_source_var}.new_page())"]


def _gen_goto(action: Action, page_var: str) -> list[str]:
    kwargs_parts = []
    for k, v in (action.goto_kwargs or {}).items():
        kwargs_parts.append(f"{k}={_escape(v)}")
    label = action.url or "Navigate"
    call_args = [page_var, _escape(action.url)]
    call_args.extend(kwargs_parts)
    return _tracked_action_lines(
        action,
        "goto",
        label,
        "_ptr_goto_page",
        call_args,
        page_var,
        extra={"goto_kwargs": action.goto_kwargs},
    )


def _gen_fill(action: Action, page_var: str) -> list[str]:
    """Generate fill call — routes through _ptr_fill_textbox for non-login fields."""
    if not action.locator_steps:
        _raise_coverage_error(action, "Fill action is missing locator steps.")

    locator_expr = _build_locator_expr(page_var, action.locator_steps)
    label = action.name or ""

    # Login fields stay raw (Username, Password, Email)
    if _is_login_field(label):
        return _tracked_action_lines(
            action,
            "fill_textbox",
            label,
            "_ptr_raw_fill",
            [locator_expr, page_var, _escape(label), _escape(action.value)],
            page_var,
            primary_locator_expr=locator_expr,
            extra={"value": action.value},
        )

    # Everything else goes through the tracked helper with full retry/fallback
    return _tracked_action_lines(
        action,
        "fill_textbox",
        label,
        "_ptr_fill_textbox",
        [locator_expr, page_var, _escape(label), _escape(action.value)],
        page_var,
        primary_locator_expr=locator_expr,
        extra={"value": action.value},
    )


def _gen_press(action: Action, page_var: str) -> list[str]:
    """Generate press call."""
    if not action.locator_steps:
        _raise_coverage_error(action, "Press action is missing locator steps.")

    locator_expr = _build_locator_expr(page_var, action.locator_steps)
    label = action.name or action.key or "Press key"
    return _tracked_action_lines(
        action,
        "press_key",
        label,
        "_ptr_raw_press",
        [locator_expr, page_var, _escape(label), _escape(action.key)],
        page_var,
        primary_locator_expr=locator_expr,
        extra={"key": action.key},
    )


def _gen_click(action: Action, page_var: str) -> list[str]:
    """Generate click call — routes through the appropriate helper based on element type."""
    if not action.locator_steps:
        _raise_coverage_error(action, "Click action is missing locator steps.")

    locator_expr = _build_locator_expr(page_var, action.locator_steps)
    role = action.role
    label = action.name or ""
    method = action.locator_method

    # Textbox/spinbutton click → _ptr_click_textbox
    if role in ("textbox", "spinbutton"):
        if _is_login_field(label):
            return _tracked_action_lines(
                action,
                "click_textbox",
                label,
                "_ptr_raw_click",
                [locator_expr, page_var, _escape(label)],
                page_var,
                primary_locator_expr=locator_expr,
            )
        return _tracked_action_lines(
            action,
            "click_textbox",
            label,
            "_ptr_click_textbox",
            [locator_expr, page_var, _escape(label)],
            page_var,
            primary_locator_expr=locator_expr,
        )

    # Combobox click (standalone, not part of select_combobox) → _ptr_click_combobox
    if role == "combobox":
        return _tracked_action_lines(
            action,
            "click_combobox",
            label,
            "_ptr_click_combobox",
            [locator_expr, page_var, _escape(label)],
            page_var,
            primary_locator_expr=locator_expr,
        )

    # Button click → _ptr_click_button_target
    if role == "button":
        if label and label.strip().isdigit():
            return _tracked_action_lines(
                action,
                "click_numeric_button",
                label,
                "_ptr_click_numeric_button_target",
                [locator_expr, page_var, _escape(label)],
                page_var,
                primary_locator_expr=locator_expr,
            )
        return _tracked_action_lines(
            action,
            "click_button",
            label,
            "_ptr_click_button_target",
            [locator_expr, page_var, _escape(label)],
            page_var,
            primary_locator_expr=locator_expr,
        )

    # get_by_text click → _ptr_click_text_target
    if method == "get_by_text":
        return _tracked_action_lines(
            action,
            "click_text",
            label,
            "_ptr_click_text_target",
            [locator_expr, page_var, _escape(label)],
            page_var,
            primary_locator_expr=locator_expr,
        )

    # get_by_title click → title/text helper with title attribute fallback
    if method == "get_by_title":
        return _tracked_action_lines(
            action,
            "click_title",
            label,
            "_ptr_click_text_target",
            [locator_expr, page_var, _escape(label)],
            page_var,
            primary_locator_expr=locator_expr,
        )

    # Link, option, cell, gridcell, tab, menuitem → _ptr_click_text_target
    if role in ("link", "option", "cell", "gridcell", "tab", "menuitem"):
        return _tracked_action_lines(
            action,
            f"click_{role}",
            label,
            "_ptr_click_text_target",
            [locator_expr, page_var, _escape(label)],
            page_var,
            primary_locator_expr=locator_expr,
        )

    # get_by_label, get_by_placeholder, get_by_test_id, locator() → generic click with fallback
    if method in ("get_by_label", "get_by_placeholder", "get_by_alt_text", "get_by_test_id"):
        return _tracked_action_lines(
            action,
            f"click_{method}",
            label,
            "_ptr_click_text_target",
            [locator_expr, page_var, _escape(label)],
            page_var,
            primary_locator_expr=locator_expr,
        )

    # Listbox with locator("li") → _ptr_click_listbox_option
    if role == "listbox" and len(action.locator_steps) > 1:
        return _tracked_action_lines(
            action,
            "click_listbox",
            label,
            "_ptr_click_listbox_option",
            [locator_expr, page_var, _escape(label)],
            page_var,
            primary_locator_expr=locator_expr,
        )

    _raise_coverage_error(
        action,
        "Click target does not map to a resilient helper "
        f"(locator_method={method!r}, role={role!r}).",
    )


def _gen_select_option(action: Action, page_var: str) -> list[str]:
    _raise_coverage_error(
        action,
        'Action "select_option" still relies on a raw Playwright call. '
        "Add helper coverage before replaying this recording via AST.",
    )


def _gen_check(action: Action, page_var: str) -> list[str]:
    _raise_coverage_error(
        action,
        'Action "check" still relies on a raw Playwright call. '
        "Add helper coverage before replaying this recording via AST.",
    )


def _gen_uncheck(action: Action, page_var: str) -> list[str]:
    _raise_coverage_error(
        action,
        'Action "uncheck" still relies on a raw Playwright call. '
        "Add helper coverage before replaying this recording via AST.",
    )


def _gen_set_input_files(action: Action, page_var: str) -> list[str]:
    _raise_coverage_error(
        action,
        'Action "set_input_files" still relies on a raw Playwright call. '
        "Add helper coverage before replaying this recording via AST.",
    )


def _gen_hover(action: Action, page_var: str) -> list[str]:
    _raise_coverage_error(
        action,
        'Action "hover" still relies on a raw Playwright call. '
        "Add helper coverage before replaying this recording via AST.",
    )


def _gen_dblclick(action: Action, page_var: str) -> list[str]:
    _raise_coverage_error(
        action,
        'Action "dblclick" still relies on a raw Playwright call. '
        "Add helper coverage before replaying this recording via AST.",
    )


# --- Compound action generators ---

def _gen_fill_and_submit(action: Action, page_var: str) -> list[str]:
    """fill + Enter → _ptr_fill_textbox then _ptr_submit_textbox_enter."""
    locator_expr = _build_locator_expr(page_var, action.locator_steps)
    label = action.name or ""
    fill_lines = _tracked_action_lines(
        action,
        "fill_textbox",
        label,
        "_ptr_fill_textbox",
        [locator_expr, page_var, _escape(label), _escape(action.value)],
        page_var,
        primary_locator_expr=locator_expr,
        extra={"value": action.value},
    )
    submit_lines = _tracked_action_lines(
        action,
        "submit_textbox_enter",
        label,
        "_ptr_submit_textbox_enter",
        [locator_expr, page_var, _escape(label)],
        page_var,
        primary_locator_expr=locator_expr,
        extra={"submit_key": "Enter"},
    )
    return fill_lines + submit_lines


def _gen_select_combobox(action: Action, page_var: str) -> list[str]:
    """combobox click + option click → _ptr_select_combobox_option."""
    trigger_expr = _build_locator_expr(page_var, action.locator_steps)
    label = action.name or ""
    option_name = (action.action_kwargs or {}).get("option_name", action.value or "")
    option_role = (action.action_kwargs or {}).get("option_role", "option")
    option_exact = (action.action_kwargs or {}).get("option_exact")

    # Build option locator
    option_steps = (action.action_kwargs or {}).get("option_locator_steps")
    if option_steps:
        # Rebuild from stored steps
        steps = []
        for s in option_steps:
            steps.append(LocatorStep(
                method=s["method"],
                args=s.get("args", []),
                kwargs=s.get("kwargs", {}),
            ))
        option_expr = _build_locator_expr(page_var, steps)
    else:
        exact_kwarg = ""
        if option_exact is not None:
            exact_kwarg = f", exact={_escape(option_exact)}"
        if option_role and option_role != "text":
            option_expr = f'{page_var}.get_by_role({_escape(option_role)}, name={_escape(option_name)}{exact_kwarg})'
        else:
            option_expr = f'{page_var}.get_by_text({_escape(option_name)}{exact_kwarg})'

    return _tracked_action_lines(
        action,
        "select_combobox",
        label,
        "_ptr_select_combobox_option",
        [trigger_expr, option_expr, page_var, _escape(label), _escape(option_name)],
        page_var,
        primary_locator_expr=trigger_expr,
        secondary_locator_expr=option_expr,
        extra={
            "option_name": option_name,
            "option_role": option_role,
            "option_exact": option_exact,
        },
    )


def _gen_search_and_select(action: Action, page_var: str) -> list[str]:
    """Search trigger + result click → _ptr_select_search_trigger_option."""
    trigger_expr = _build_locator_expr(page_var, action.locator_steps)
    title = action.name or ""
    option_name = (action.action_kwargs or {}).get("option_name", action.value or "")
    option_kind = (action.action_kwargs or {}).get("option_kind", "text")
    option_role = (action.action_kwargs or {}).get("option_role")
    option_exact = (action.action_kwargs or {}).get("option_exact")
    fill_value = (action.action_kwargs or {}).get("fill_value")

    exact_kwarg = ""
    if option_exact is not None:
        exact_kwarg = f", exact={_escape(option_exact)}"

    if option_role and option_role in ("option", "cell", "gridcell"):
        option_expr = f'{page_var}.get_by_role({_escape(option_role)}, name={_escape(option_name)}{exact_kwarg})'
    else:
        option_expr = f'{page_var}.get_by_text({_escape(option_name)}{exact_kwarg})'

    helper_args = [
        trigger_expr,
        option_expr,
        page_var,
        _escape(title),
        _escape(option_name),
        f"option_kind={_escape(option_kind)}",
    ]
    if fill_value is not None:
        helper_args.append(f"fill_value={_escape(fill_value)}")

    return _tracked_action_lines(
        action,
        "search_and_select",
        title,
        "_ptr_select_search_trigger_option",
        helper_args,
        page_var,
        primary_locator_expr=trigger_expr,
        secondary_locator_expr=option_expr,
        extra={
            "option_name": option_name,
            "option_kind": option_kind,
            "option_role": option_role,
            "option_exact": option_exact,
            "fill_value": fill_value,
        },
    )


def _gen_adf_menu_select(action: Action, page_var: str) -> list[str]:
    """ADF menu trigger + option → _ptr_select_adf_menu_panel_option."""
    trigger_expr = _build_locator_expr(page_var, action.locator_steps)
    trigger_label = action.name or ""
    option_name = (action.action_kwargs or {}).get("option_name", action.value or "")
    trigger_kind = (action.action_kwargs or {}).get("trigger_kind", "title")

    option_expr = f'{page_var}.get_by_text({_escape(option_name)})'

    return _tracked_action_lines(
        action,
        "adf_menu_select",
        trigger_label,
        "_ptr_select_adf_menu_panel_option",
        [trigger_expr, option_expr, page_var, _escape(trigger_label), _escape(option_name), f"trigger_kind={_escape(trigger_kind)}"],
        page_var,
        primary_locator_expr=trigger_expr,
        secondary_locator_expr=option_expr,
        extra={
            "option_name": option_name,
            "trigger_kind": trigger_kind,
        },
    )


def _gen_date_pick(action: Action, page_var: str) -> list[str]:
    """Date icon + day click → _ptr_pick_date_via_icon."""
    icon_expr = _build_locator_expr(page_var, action.locator_steps)
    title = action.name or ""
    day_label = (action.action_kwargs or {}).get("day_label", action.value or "")
    day_role = (action.action_kwargs or {}).get("day_role", "button")

    day_expr = f'{page_var}.get_by_role({_escape(day_role)}, name={_escape(day_label)})'

    return _tracked_action_lines(
        action,
        "date_pick",
        title,
        "_ptr_pick_date_via_icon",
        [icon_expr, day_expr, page_var, _escape(title), _escape(day_label)],
        page_var,
        primary_locator_expr=icon_expr,
        secondary_locator_expr=day_expr,
        extra={
            "day_label": day_label,
            "day_role": day_role,
        },
    )


def _gen_navigation_button(action: Action, page_var: str) -> list[str]:
    """Continue/Submit/Next/etc → _ptr_click_navigation_button."""
    locator_expr = _build_locator_expr(page_var, action.locator_steps)
    label = action.name or ""
    return _tracked_action_lines(
        action,
        "navigation_button",
        label,
        "_ptr_click_navigation_button",
        [locator_expr, page_var, _escape(label)],
        page_var,
        primary_locator_expr=locator_expr,
    )


def _gen_numeric_button(action: Action, page_var: str) -> list[str]:
    """Numeric button (date picker day) → _ptr_click_numeric_button_target."""
    locator_expr = _build_locator_expr(page_var, action.locator_steps)
    label = action.name or ""
    return _tracked_action_lines(
        action,
        "click_numeric_button",
        label,
        "_ptr_click_numeric_button_target",
        [locator_expr, page_var, _escape(label)],
        page_var,
        primary_locator_expr=locator_expr,
    )


def _gen_login_and_redirect(action: Action, page_var: str) -> list[str]:
    """Password Enter + goto → press Enter then _ptr_wait_for_post_login_redirect."""
    locator_expr = _build_locator_expr(page_var, action.locator_steps)
    label = action.name or "Submit login"
    return _tracked_action_lines(
        action,
        "submit_login",
        label,
        "_ptr_login_submit_and_redirect",
        [locator_expr, page_var, _escape(label), _escape(action.url or "")],
        page_var,
        primary_locator_expr=locator_expr,
        extra={"redirect_url": action.url or ""},
    )


def _gen_wait(action: Action, page_var: str) -> list[str]:
    if action.wait_state:
        # Replace networkidle with a fallback wait (Oracle never settles)
        if action.wait_state == "networkidle":
            return [
                f'    {page_var}.wait_for_timeout(_ptr_wait_ms("PTR_NETWORK_IDLE_FALLBACK_WAIT_MS", 1000))'
            ]
        return [f"    {page_var}.wait_for_load_state({_escape(action.wait_state)})"]
    if action.wait_ms is not None:
        return [f"    {page_var}.wait_for_timeout({action.wait_ms})"]
    return []


def _gen_reload(action: Action, page_var: str) -> list[str]:
    return [f"    {page_var}.reload()"]


def _gen_go_back(action: Action, page_var: str) -> list[str]:
    return [f"    {page_var}.go_back()"]


def _gen_go_forward(action: Action, page_var: str) -> list[str]:
    return [f"    {page_var}.go_forward()"]


def _gen_close_page(action: Action, page_var: str) -> list[str]:
    return [f"    {page_var}.close()"]


def _gen_close_context(action: Action, page_var: str) -> list[str]:
    return ["    context.close()"]


def _gen_close_browser(action: Action, page_var: str) -> list[str]:
    return ["    browser.close()"]


# ---------------------------------------------------------------------------
# Generator dispatch
# ---------------------------------------------------------------------------

_GENERATORS: dict[str, Any] = {
    "setup_browser": _gen_setup_browser,
    "setup_context": _gen_setup_context,
    "setup_page": _gen_setup_page,
    "goto": _gen_goto,
    "fill": _gen_fill,
    "press": _gen_press,
    "click": _gen_click,
    "dblclick": _gen_dblclick,
    "hover": _gen_hover,
    "select_option": _gen_select_option,
    "check": _gen_check,
    "uncheck": _gen_uncheck,
    "set_input_files": _gen_set_input_files,
    "wait": _gen_wait,
    "reload": _gen_reload,
    "go_back": _gen_go_back,
    "go_forward": _gen_go_forward,
    "close_page": _gen_close_page,
    "close_context": _gen_close_context,
    "close_browser": _gen_close_browser,
    # Compound actions
    "fill_and_submit": _gen_fill_and_submit,
    "select_combobox": _gen_select_combobox,
    "search_and_select": _gen_search_and_select,
    "adf_menu_select": _gen_adf_menu_select,
    "date_pick": _gen_date_pick,
    "navigation_button": _gen_navigation_button,
    "numeric_button": _gen_numeric_button,
    "login_and_redirect": _gen_login_and_redirect,
}


def _gen_post_click_wait(action: Action, next_action: Action | None, page_var: str) -> list[str]:
    """Insert a short wait after clicks when the next action targets a different element."""
    if action.type not in ("click", "select_combobox", "search_and_select", "adf_menu_select", "date_pick"):
        return []
    if action.type == "navigation_button":
        return []  # nav button helper already handles waits
    if next_action is None:
        return []
    # If next action is on a different element, add a settle wait
    if next_action.locator_steps and action.locator_steps:
        if action.locator_steps != next_action.locator_steps:
            return [
                f'    {page_var}.wait_for_timeout(_ptr_wait_ms("PTR_POST_CLICK_WAIT_MS", 250))'
            ]
    return []


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_run_body(actions: list[Action]) -> str:
    """Generate the body of the run(playwright) function.

    This produces the Python code that goes inside:
        def run(playwright: Playwright) -> None:
            <generated code here>

    The output is indented with 4 spaces (function body level).
    """
    lines: list[str] = []
    page_var = "page"
    coverage_issues: list[str] = []

    for action in actions:
        reason = _unsupported_action_reason(action)
        if reason is None:
            continue
        coverage_issues.append(
            f"- line {action.line}: {reason}\n"
            f"  source: {_format_action_preview(action)}"
        )

    if coverage_issues:
        raise CoverageError(
            "AST generator found actions outside resilient helper coverage:\n"
            + "\n".join(coverage_issues)
        )

    for i, action in enumerate(actions):
        next_action = actions[i + 1] if i + 1 < len(actions) else None

        # Track the page variable
        if action.page_var and action.type not in (
            "setup_browser", "setup_context", "close_context", "close_browser",
        ):
            page_var = action.page_var

        generator = _GENERATORS.get(action.type)
        if generator is None:
            _raise_coverage_error(action, f'Unhandled action type "{action.type}".')

        action_lines = generator(action, page_var)
        lines.extend(action_lines)

        # Post-click waits
        wait_lines = _gen_post_click_wait(action, next_action, page_var)
        lines.extend(wait_lines)

    return "\n".join(lines)


def generate_full_script(actions: list[Action]) -> str:
    """Generate a complete, executable Playwright script from an action list.

    The output is a complete Python script with:
    - Imports
    - run() function with all actions
    - sync_playwright runner with error handling

    NOTE: The _ptr_* helper functions are NOT included here — tools.py injects
    a runtime module import after generation. This function emits the final
    run/playwright wrapper directly so preparation does not need to rewrite
    script source with embedded helper code.
    """
    body = generate_run_body(actions)

    return f"""from __future__ import annotations

from playwright.sync_api import Playwright, sync_playwright, expect


def run(playwright: Playwright) -> None:
{body}


with sync_playwright() as playwright:
    try:
        run(playwright)
    except Exception as exc:
        _ptr_capture_failure(exc)
        raise
    finally:
        _ptr_write_diagnostics()
"""
