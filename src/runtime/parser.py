"""
AST-based parser for Playwright recordings.

Converts raw .py scripts (from Playwright codegen / Phantom) into a structured
action list that the executor can run with full Oracle ADF retry/fallback logic.

Unlike the regex-based rewriters in the old pipeline, the AST parser understands
Python code structure — so it catches EVERY locator pattern codegen can produce,
not just the ones a regex happens to match.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class ParseCoverageError(ValueError):
    """Raised when the AST parser encounters run() statements it cannot model safely."""


@dataclass
class LocatorStep:
    """One segment in a Playwright locator chain.

    Examples:
        get_by_role("textbox", name="Username")  →  method="get_by_role", args=["textbox"], kwargs={"name": "Username"}
        locator("a")                              →  method="locator", args=["a"], kwargs={}
        first                                     →  method="first", args=[], kwargs={}, is_property=True
        nth(0)                                    →  method="nth", args=[0], kwargs={}
    """

    method: str
    args: list[Any] = field(default_factory=list)
    kwargs: dict[str, Any] = field(default_factory=dict)
    is_property: bool = False


@dataclass
class Action:
    """A single parsed action from a Playwright recording."""

    type: str  # goto, fill, click, press, select_option, check, uncheck,
    # set_input_files, wait, reload, close_page, close_context,
    # close_browser, setup_browser, setup_context, setup_page, unknown
    line: int
    raw: str  # original source line(s)

    # -- Page navigation --
    url: str | None = None  # for goto
    goto_kwargs: dict[str, Any] = field(default_factory=dict)

    # -- Element locator --
    page_var: str = "page"
    page_source_var: str | None = None  # for setup_page assignments
    locator_steps: list[LocatorStep] = field(default_factory=list)

    # -- Action arguments --
    value: str | None = None  # for fill
    key: str | None = None  # for press
    option_value: str | None = None  # for select_option positional
    option_kwargs: dict[str, Any] = field(default_factory=dict)  # for select_option kwargs
    action_args: list[Any] = field(default_factory=list)  # raw positional args to the action
    action_kwargs: dict[str, Any] = field(default_factory=dict)  # raw keyword args to the action

    # -- Wait --
    wait_state: str | None = None  # for wait_for_load_state
    wait_ms: int | None = None  # for wait_for_timeout

    # -- Derived locator metadata (populated by _enrich) --
    locator_method: str | None = None  # primary locator method (get_by_role, get_by_text, etc.)
    role: str | None = None  # ARIA role if get_by_role
    name: str | None = None  # name= kwarg from get_by_role, or label/text string
    exact: bool | None = None  # exact= kwarg
    selector: str | None = None  # raw CSS selector if locator()

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": self.type,
            "line": self.line,
        }
        if self.url is not None:
            d["url"] = self.url
        if self.goto_kwargs:
            d["goto_kwargs"] = self.goto_kwargs
        if self.page_var != "page":
            d["page_var"] = self.page_var
        if self.page_source_var is not None:
            d["page_source_var"] = self.page_source_var
        if self.locator_steps:
            d["locator_steps"] = [
                {
                    "method": s.method,
                    **({"args": s.args} if s.args else {}),
                    **({"kwargs": s.kwargs} if s.kwargs else {}),
                    **({"is_property": True} if s.is_property else {}),
                }
                for s in self.locator_steps
            ]
        if self.value is not None:
            d["value"] = self.value
        if self.key is not None:
            d["key"] = self.key
        if self.option_value is not None:
            d["option_value"] = self.option_value
        if self.option_kwargs:
            d["option_kwargs"] = self.option_kwargs
        if self.wait_state is not None:
            d["wait_state"] = self.wait_state
        if self.wait_ms is not None:
            d["wait_ms"] = self.wait_ms
        if self.locator_method:
            d["locator_method"] = self.locator_method
        if self.role:
            d["role"] = self.role
        if self.name is not None:
            d["name"] = self.name
        if self.exact is not None:
            d["exact"] = self.exact
        if self.selector:
            d["selector"] = self.selector
        d["raw"] = self.raw
        return d


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _const_value(node: ast.expr) -> Any:
    """Extract a constant value from an AST node, or fall back to ast.unparse."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_const_value(el) for el in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_const_value(el) for el in node.elts)
    if isinstance(node, ast.Dict):
        return {
            _const_value(k) if k is not None else None: _const_value(v)
            for k, v in zip(node.keys, node.values)
        }
    if isinstance(node, (ast.Name, ast.Attribute)):
        return ast.unparse(node)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _const_value(node.operand)
        if isinstance(inner, (int, float)):
            return -inner
    # Fall back to source representation
    try:
        return ast.unparse(node)
    except Exception:
        return "<expr>"


def _extract_call_args(node: ast.Call) -> tuple[list[Any], dict[str, Any]]:
    """Extract positional and keyword arguments from a Call node."""
    args = [_const_value(a) for a in node.args]
    kwargs = {kw.arg: _const_value(kw.value) for kw in node.keywords if kw.arg is not None}
    return args, kwargs


@dataclass
class _ChainSegment:
    """One segment in an unwound method chain."""
    kind: str  # "name", "call", "attr"
    name: str = ""  # variable name (for "name") or attribute/method name (for "attr"/"call")
    args: list[Any] = field(default_factory=list)
    kwargs: dict[str, Any] = field(default_factory=dict)


def _unwind_chain(node: ast.expr) -> list[_ChainSegment]:
    """Unwind a method chain into a flat list of segments.

    Given: page.get_by_role("textbox", name="X").first.fill("value")
    Returns: [
        _ChainSegment(kind="name", name="page"),
        _ChainSegment(kind="call", name="get_by_role", args=["textbox"], kwargs={"name": "X"}),
        _ChainSegment(kind="attr", name="first"),
        _ChainSegment(kind="call", name="fill", args=["value"], kwargs={}),
    ]
    """
    segments: list[_ChainSegment] = []
    _unwind_chain_recursive(node, segments)
    return segments


def _unwind_chain_recursive(node: ast.expr, segments: list[_ChainSegment]) -> None:
    if isinstance(node, ast.Name):
        segments.append(_ChainSegment(kind="name", name=node.id))

    elif isinstance(node, ast.Attribute):
        _unwind_chain_recursive(node.value, segments)
        segments.append(_ChainSegment(kind="attr", name=node.attr))

    elif isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute):
            _unwind_chain_recursive(node.func.value, segments)
            args, kwargs = _extract_call_args(node)
            segments.append(_ChainSegment(kind="call", name=node.func.attr, args=args, kwargs=kwargs))
        elif isinstance(node.func, ast.Name):
            args, kwargs = _extract_call_args(node)
            segments.append(_ChainSegment(kind="call", name=node.func.id, args=args, kwargs=kwargs))
        else:
            # Complex expression we can't unwind further
            _unwind_chain_recursive(node.func, segments)
            args, kwargs = _extract_call_args(node)
            segments.append(_ChainSegment(kind="call", name="<call>", args=args, kwargs=kwargs))

    elif isinstance(node, ast.Subscript):
        _unwind_chain_recursive(node.value, segments)
        segments.append(_ChainSegment(kind="attr", name=f"[{ast.unparse(node.slice)}]"))


# ---------------------------------------------------------------------------
# Action classification
# ---------------------------------------------------------------------------

_LOCATOR_METHODS = frozenset({
    "get_by_role",
    "get_by_text",
    "get_by_label",
    "get_by_title",
    "get_by_placeholder",
    "get_by_alt_text",
    "get_by_test_id",
    "locator",
    "frame_locator",
})

_LOCATOR_MODIFIERS = frozenset({
    "first",
    "last",
    "nth",
    "filter",
    "locator",
    "get_by_role",
    "get_by_text",
    "get_by_label",
    "get_by_title",
    "get_by_placeholder",
})

_ACTION_METHODS = frozenset({
    "click",
    "dblclick",
    "fill",
    "press",
    "press_sequentially",
    "type",
    "check",
    "uncheck",
    "select_option",
    "set_input_files",
    "hover",
    "focus",
    "scroll_into_view_if_needed",
    "input_value",
    "inner_text",
    "inner_html",
    "text_content",
    "is_visible",
    "is_enabled",
    "is_checked",
    "wait_for",
    "screenshot",
    "evaluate",
    "dispatch_event",
    "set_checked",
})

_PAGE_METHODS = frozenset({
    "goto",
    "reload",
    "go_back",
    "go_forward",
    "close",
    "wait_for_load_state",
    "wait_for_timeout",
    "wait_for_url",
    "wait_for_event",
    "screenshot",
    "evaluate",
    "keyboard",
    "mouse",
})

_SETUP_VARS = frozenset({"playwright", "browser", "context"})


def _is_ignorable_statement(stmt: ast.stmt) -> bool:
    """Return True for statements that are safe to ignore in run()."""
    if isinstance(stmt, ast.Pass):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
        return isinstance(stmt.value.value, str)
    return False


def _format_statement_preview(raw_line: str) -> str:
    preview = " ".join(str(raw_line or "").split())
    if len(preview) > 160:
        return preview[:157] + "..."
    return preview


def _classify_setup_assignment(stmt: ast.Assign, raw_line: str) -> Action | None:
    """Classify supported setup assignments like browser/context/page creation."""
    if len(stmt.targets) != 1:
        return None

    target = stmt.targets[0]
    if not isinstance(target, ast.Name):
        return None

    if not isinstance(stmt.value, ast.Call):
        return None

    target_name = target.id
    chain = _unwind_chain(stmt.value)

    if target_name == "browser":
        return Action(
            type="setup_browser",
            line=stmt.lineno,
            raw=raw_line,
            action_args=[_const_value(a) for a in stmt.value.args],
            action_kwargs={
                kw.arg: _const_value(kw.value)
                for kw in stmt.value.keywords
                if kw.arg is not None
            },
        )

    if target_name == "context":
        return Action(
            type="setup_context",
            line=stmt.lineno,
            raw=raw_line,
            action_args=[_const_value(a) for a in stmt.value.args],
            action_kwargs={
                kw.arg: _const_value(kw.value)
                for kw in stmt.value.keywords
                if kw.arg is not None
            },
        )

    if not chain or chain[-1].kind != "call" or chain[-1].name != "new_page":
        return None

    page_source_var = "context"
    if chain[0].kind == "name":
        page_source_var = chain[0].name

    return Action(
        type="setup_page",
        line=stmt.lineno,
        raw=raw_line,
        page_var=target_name,
        page_source_var=page_source_var,
    )


def _classify_action(
    segments: list[_ChainSegment],
    raw_line: str,
    line_no: int,
) -> Action | None:
    """Classify a chain of segments into an Action."""
    if not segments:
        return None

    # Find the base variable
    if segments[0].kind != "name":
        return None
    base_var = segments[0].name

    # --- Setup: browser = playwright.chromium.launch(...) ---
    # These are handled by looking at assignment targets in the caller.

    # --- Page-level methods (no locator) ---
    if len(segments) >= 2 and segments[-1].kind == "call":
        method = segments[-1].name
        args = segments[-1].args
        kwargs = segments[-1].kwargs

        # page.goto("url")
        if method == "goto" and base_var not in _SETUP_VARS:
            url = str(args[0]) if args else None
            goto_kwargs = dict(kwargs)
            return Action(
                type="goto",
                line=line_no,
                raw=raw_line,
                page_var=base_var,
                url=url,
                goto_kwargs=goto_kwargs,
            )

        # page.reload()
        if method == "reload" and base_var not in _SETUP_VARS:
            return Action(type="reload", line=line_no, raw=raw_line, page_var=base_var)

        # page.go_back()
        if method == "go_back" and base_var not in _SETUP_VARS:
            return Action(type="go_back", line=line_no, raw=raw_line, page_var=base_var)

        # page.go_forward()
        if method == "go_forward" and base_var not in _SETUP_VARS:
            return Action(type="go_forward", line=line_no, raw=raw_line, page_var=base_var)

        # page.close()
        if method == "close":
            if base_var == "context" or (len(segments) > 2 and segments[1].name == "context"):
                return Action(type="close_context", line=line_no, raw=raw_line)
            if base_var == "browser" or (len(segments) > 2 and segments[1].name == "browser"):
                return Action(type="close_browser", line=line_no, raw=raw_line)
            return Action(type="close_page", line=line_no, raw=raw_line, page_var=base_var)

        # page.wait_for_load_state("networkidle")
        if method == "wait_for_load_state" and base_var not in _SETUP_VARS:
            state = str(args[0]) if args else "load"
            return Action(
                type="wait",
                line=line_no,
                raw=raw_line,
                page_var=base_var,
                wait_state=state,
            )

        # page.wait_for_timeout(1000)
        if method == "wait_for_timeout" and base_var not in _SETUP_VARS:
            ms = int(args[0]) if args else 0
            return Action(
                type="wait",
                line=line_no,
                raw=raw_line,
                page_var=base_var,
                wait_ms=ms,
            )

    # --- Locator + Action chains ---
    # Find where the locator starts and the action is
    # Pattern: base_var . [locator_method(...).]* [modifier.]* action_method(...)
    if base_var in _SETUP_VARS:
        return None  # skip setup chains

    locator_start = None
    action_idx = None

    for i in range(1, len(segments)):
        seg = segments[i]
        name = seg.name

        if seg.kind == "call" and name in _LOCATOR_METHODS and locator_start is None:
            locator_start = i
        elif seg.kind == "call" and name in _ACTION_METHODS:
            action_idx = i
            break
        elif seg.kind == "call" and name in _LOCATOR_MODIFIERS:
            if locator_start is None:
                locator_start = i
        elif seg.kind == "attr" and name in _LOCATOR_MODIFIERS:
            if locator_start is None:
                locator_start = i

    if action_idx is None:
        # No recognized action — might be a page-level call or unknown
        if len(segments) >= 2 and segments[-1].kind == "call":
            method = segments[-1].name
            # context.close(), browser.close()
            if method == "close":
                if base_var == "context":
                    return Action(type="close_context", line=line_no, raw=raw_line)
                if base_var == "browser":
                    return Action(type="close_browser", line=line_no, raw=raw_line)
        return None

    # Build locator steps
    locator_steps: list[LocatorStep] = []
    if locator_start is not None:
        for i in range(locator_start, action_idx):
            seg = segments[i]
            if seg.kind == "call":
                locator_steps.append(LocatorStep(
                    method=seg.name,
                    args=seg.args,
                    kwargs=seg.kwargs,
                ))
            elif seg.kind == "attr":
                locator_steps.append(LocatorStep(
                    method=seg.name,
                    is_property=True,
                ))

    # Extract action info
    action_seg = segments[action_idx]
    action_method = action_seg.name
    action_args = action_seg.args
    action_kwargs = action_seg.kwargs

    action = Action(
        type=action_method,
        line=line_no,
        raw=raw_line,
        page_var=base_var,
        locator_steps=locator_steps,
        action_args=action_args,
        action_kwargs=action_kwargs,
    )

    # Populate typed fields based on action type
    if action_method == "fill":
        action.value = str(action_args[0]) if action_args else None
    elif action_method == "press":
        action.key = str(action_args[0]) if action_args else None
    elif action_method == "select_option":
        if action_args:
            action.option_value = str(action_args[0]) if isinstance(action_args[0], str) else None
        action.option_kwargs = action_kwargs

    # Enrich with locator metadata
    _enrich_locator_metadata(action)

    return action


def _enrich_locator_metadata(action: Action) -> None:
    """Extract commonly-needed metadata from locator steps into top-level fields."""
    if not action.locator_steps:
        return

    primary = action.locator_steps[0]
    action.locator_method = primary.method

    if primary.method == "get_by_role":
        action.role = str(primary.args[0]) if primary.args else None
        action.name = primary.kwargs.get("name")
        if isinstance(action.name, str):
            action.name = action.name
        exact = primary.kwargs.get("exact")
        if exact is not None:
            action.exact = bool(exact) if not isinstance(exact, str) else exact == "True"

    elif primary.method == "get_by_text":
        action.name = str(primary.args[0]) if primary.args else None
        exact = primary.kwargs.get("exact")
        if exact is not None:
            action.exact = bool(exact) if not isinstance(exact, str) else exact == "True"

    elif primary.method == "get_by_label":
        action.name = str(primary.args[0]) if primary.args else None
        exact = primary.kwargs.get("exact")
        if exact is not None:
            action.exact = bool(exact) if not isinstance(exact, str) else exact == "True"

    elif primary.method == "get_by_title":
        action.name = str(primary.args[0]) if primary.args else None
        exact = primary.kwargs.get("exact")
        if exact is not None:
            action.exact = bool(exact) if not isinstance(exact, str) else exact == "True"

    elif primary.method == "get_by_placeholder":
        action.name = str(primary.args[0]) if primary.args else None
        exact = primary.kwargs.get("exact")
        if exact is not None:
            action.exact = bool(exact) if not isinstance(exact, str) else exact == "True"

    elif primary.method == "locator":
        action.selector = str(primary.args[0]) if primary.args else None

    elif primary.method == "get_by_alt_text":
        action.name = str(primary.args[0]) if primary.args else None

    elif primary.method == "get_by_test_id":
        action.name = str(primary.args[0]) if primary.args else None


# ---------------------------------------------------------------------------
# Script parsing
# ---------------------------------------------------------------------------

def _get_source_lines(source: str) -> list[str]:
    """Split source into lines (1-indexed via list offset)."""
    return [""] + source.splitlines()  # index 0 is unused; line 1 = index 1


def _get_raw_line(source_lines: list[str], node: ast.stmt) -> str:
    """Get the raw source for an AST statement, including multi-line."""
    start = getattr(node, "lineno", 0)
    end = getattr(node, "end_lineno", start) or start
    if start < 1 or start >= len(source_lines):
        return ""
    lines = source_lines[start: end + 1]
    return "\n".join(line for line in lines).strip()


def _find_run_function(tree: ast.Module) -> ast.FunctionDef | None:
    """Find the run(playwright) function in the AST."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in ("run", "main"):
                return node
    return None


def parse_script(source: str) -> list[Action]:
    """Parse a Playwright recording script into a list of Actions.

    This is the main entry point. It:
    1. Parses the Python source into an AST
    2. Finds the run() function
    3. Walks each statement in the function body
    4. Classifies each statement into an Action
    5. Returns the ordered action list

    Args:
        source: Python source code of the recording.

    Returns:
        List of Action objects in execution order.
    """
    tree = ast.parse(source)
    source_lines = _get_source_lines(source)

    run_func = _find_run_function(tree)
    if run_func is None:
        raise ValueError(
            "Could not find a run() or main() function in the recording script. "
            "The script must define a function named 'run' or 'main'."
        )

    actions: list[Action] = []
    unsupported_statements: list[tuple[int, str, str]] = []

    for stmt in run_func.body:
        raw_line = _get_raw_line(source_lines, stmt)

        # --- Assignment: browser = playwright.chromium.launch(...) etc. ---
        if isinstance(stmt, ast.Assign):
            setup_action = _classify_setup_assignment(stmt, raw_line)
            if setup_action is not None:
                actions.append(setup_action)
                continue

        # --- Expression statements: page.get_by_role(...).click() etc. ---
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            chain = _unwind_chain(stmt.value)
            action = _classify_action(chain, raw_line, stmt.lineno)
            if action is not None:
                actions.append(action)
                continue

        if _is_ignorable_statement(stmt):
            continue

        unsupported_statements.append(
            (stmt.lineno, type(stmt).__name__, _format_statement_preview(raw_line))
        )

    if unsupported_statements:
        details = "\n".join(
            f"- line {line_no} ({stmt_type}): {preview}"
            for line_no, stmt_type, preview in unsupported_statements
        )
        raise ParseCoverageError(
            "AST parser found run() statements it cannot safely normalize:\n"
            f"{details}"
        )

    return actions


# ---------------------------------------------------------------------------
# Convenience: parse to dict list (for JSON serialization / debugging)
# ---------------------------------------------------------------------------

def parse_script_to_dicts(source: str) -> list[dict[str, Any]]:
    """Parse a recording and return actions as plain dicts."""
    return [action.to_dict() for action in parse_script(source)]
