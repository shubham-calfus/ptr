from __future__ import annotations

import ast
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def normalize_param_name(name: str) -> str:
    """Convert labels like 'Receipt Number' into stable parameter keys."""
    collapsed: list[str] = []
    last_was_separator = False

    for char in str(name or "").lower():
        if char.isalnum():
            collapsed.append(char)
            last_was_separator = False
            continue
        if collapsed and not last_was_separator:
            collapsed.append("_")
            last_was_separator = True

    normalized = "".join(collapsed).strip("_")
    aliases = {
        "starturl": "url",
        "start_url": "url",
    }
    return aliases.get(normalized, normalized)


def is_placeholder_token(value: str) -> bool:
    trimmed = str(value or "").strip()
    if len(trimmed) < 4 or not trimmed.startswith("{{") or not trimmed.endswith("}}"):
        return False
    inner = trimmed[2:-2]
    return bool(inner) and all(char.isalnum() or char == "_" for char in inner)


def find_placeholder_names(script_text: str) -> list[str]:
    names: list[str] = []
    start = 0
    while True:
        open_brace = script_text.find("{{", start)
        if open_brace < 0:
            break
        close_brace = script_text.find("}}", open_brace + 2)
        if close_brace < 0:
            break
        candidate = script_text[open_brace + 2 : close_brace]
        if candidate and all(char.isalnum() or char == "_" for char in candidate):
            names.append(candidate)
        start = close_brace + 2
    return names


def substitute_parameters(script_text: str, parameters: dict[str, object]) -> str:
    """
    Replace {{variable}} placeholders in the script with runtime values.

    Placeholder replacement stays string-based on purpose: it avoids regex-based
    source rewriting while still preserving the original recording text.
    """
    for key, value in parameters.items():
        placeholder = f"{{{{{key}}}}}"
        if placeholder in script_text:
            script_text = script_text.replace(placeholder, str(value))

    unresolved = sorted(set(find_placeholder_names(script_text)))
    if unresolved:
        logger.warning(
            "Script has unresolved parameter placeholders: %s",
            ", ".join(unresolved),
        )
    return script_text


@dataclass(frozen=True)
class _Replacement:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class _ChainSegment:
    kind: str
    name: str
    node: ast.AST


def _find_run_function(tree: ast.Module) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"run", "main"}:
            return node
    return None


def _build_line_starts(script_text: str) -> list[int]:
    starts: list[int] = []
    offset = 0
    for line in script_text.splitlines(keepends=True):
        starts.append(offset)
        offset += len(line)
    if not starts:
        starts.append(0)
    return starts


def _absolute_offset(line_starts: list[int], lineno: int, col_offset: int) -> int:
    return line_starts[max(0, lineno - 1)] + col_offset


def _quote_wrapped(value: str, quote: str) -> str:
    escaped = value.replace("\\", "\\\\")
    if quote == '"':
        escaped = escaped.replace('"', '\\"')
    else:
        escaped = escaped.replace("'", "\\'")
    return f"{quote}{escaped}{quote}"


def _string_node(node: ast.AST | None) -> ast.Constant | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node
    return None


def _call_string_arg(call: ast.Call, index: int = 0) -> ast.Constant | None:
    if index >= len(call.args):
        return None
    return _string_node(call.args[index])


def _call_string_kwarg(call: ast.Call, key: str) -> ast.Constant | None:
    for keyword in call.keywords:
        if keyword.arg == key:
            return _string_node(keyword.value)
    return None


def _string_value(node: ast.Constant | None) -> str | None:
    if node is None:
        return None
    value = node.value
    if isinstance(value, str):
        return value
    return None


def _unwind_chain(node: ast.AST) -> list[_ChainSegment]:
    segments: list[_ChainSegment] = []

    def _walk(current: ast.AST) -> None:
        if isinstance(current, ast.Name):
            segments.append(_ChainSegment(kind="name", name=current.id, node=current))
            return
        if isinstance(current, ast.Attribute):
            _walk(current.value)
            segments.append(_ChainSegment(kind="attr", name=current.attr, node=current))
            return
        if isinstance(current, ast.Call):
            if isinstance(current.func, ast.Attribute):
                _walk(current.func.value)
                segments.append(_ChainSegment(kind="call", name=current.func.attr, node=current))
                return
            if isinstance(current.func, ast.Name):
                segments.append(_ChainSegment(kind="call", name=current.func.id, node=current))
                return

    _walk(node)
    return segments


def _first_locator_label(chain: list[_ChainSegment]) -> str | None:
    for segment in chain:
        if segment.kind != "call" or not isinstance(segment.node, ast.Call):
            continue
        if segment.name == "get_by_role":
            return _string_value(_call_string_kwarg(segment.node, "name"))
        if segment.name == "get_by_label":
            return _string_value(_call_string_arg(segment.node, 0))
    return None


def _first_role_name(
    chain: list[_ChainSegment],
    *,
    roles: set[str],
) -> tuple[str | None, ast.Constant | None]:
    for segment in chain:
        if segment.kind != "call" or segment.name != "get_by_role" or not isinstance(segment.node, ast.Call):
            continue
        role_node = _call_string_arg(segment.node, 0)
        role = _string_value(role_node)
        if role in roles:
            name_node = _call_string_kwarg(segment.node, "name")
            return _string_value(name_node), name_node
    return None, None


def _first_call_string_arg(
    chain: list[_ChainSegment],
    *,
    method: str,
) -> tuple[str | None, ast.Constant | None]:
    for segment in chain:
        if segment.kind != "call" or segment.name != method or not isinstance(segment.node, ast.Call):
            continue
        value_node = _call_string_arg(segment.node, 0)
        return _string_value(value_node), value_node
    return None, None


def _node_replacement(
    script_text: str,
    line_starts: list[int],
    node: ast.Constant,
    replacement_value: str,
) -> _Replacement:
    start = _absolute_offset(line_starts, node.lineno, node.col_offset)
    end = _absolute_offset(line_starts, node.end_lineno or node.lineno, node.end_col_offset or node.col_offset)
    literal = script_text[start:end]
    quote = '"' if not literal or literal[0] not in {"'", '"'} else literal[0]
    return _Replacement(start=start, end=end, text=_quote_wrapped(replacement_value, quote))


def parameterise_script(script_text: str) -> tuple[str, dict[str, str]]:
    """
    Extract default parameters and replace them with placeholders without
    regex-based source rewriting.

    Supported patterns mirror the old implementation:
      0. page.goto("url")                            → url
      1. .fill("value") with field-name context      → text inputs
      2. .select_option(label=..) / ("value")        → <select> fields
      3. gridcell.click() after combobox click()     → LOV / dropdown picks
      4. get_by_text().click() after "Search: X"     → popup LOV picks
    """
    tree = ast.parse(script_text)
    run_func = _find_run_function(tree)
    statements = run_func.body if run_func is not None else tree.body

    line_starts = _build_line_starts(script_text)
    replacements: list[_Replacement] = []
    params: dict[str, str] = {}
    seen: set[str] = set()
    pending_gridcell_context: str | None = None
    last_search: str | None = None

    def _record_placeholder(param_name: str, value: str, node: ast.Constant) -> None:
        if not value or is_placeholder_token(value):
            return
        if param_name not in seen:
            params[param_name] = value
            seen.add(param_name)
        replacements.append(
            _node_replacement(script_text, line_starts, node, f"{{{{{param_name}}}}}")
        )

    for statement in statements:
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
            pending_gridcell_context = None
            continue

        chain = _unwind_chain(statement.value)
        if not chain or chain[-1].kind != "call" or not isinstance(chain[-1].node, ast.Call):
            pending_gridcell_context = None
            continue

        action_method = chain[-1].name
        action_call = chain[-1].node
        field_name = _first_locator_label(chain)
        title_text, _ = _first_call_string_arg(chain, method="get_by_title")

        if action_method == "goto":
            url_node = _call_string_arg(action_call, 0)
            url_value = _string_value(url_node)
            if url_node and url_value and not is_placeholder_token(url_value):
                _record_placeholder("url", url_value, url_node)
            pending_gridcell_context = None
            continue

        if action_method == "fill":
            fill_node = _call_string_arg(action_call, 0)
            fill_value = _string_value(fill_node)
            if field_name and fill_node and fill_value:
                _record_placeholder(normalize_param_name(field_name), fill_value, fill_node)
            pending_gridcell_context = None
            continue

        if action_method == "select_option":
            option_node = _call_string_kwarg(action_call, "label") or _call_string_arg(action_call, 0)
            option_value = _string_value(option_node)
            if field_name and option_node and option_value:
                _record_placeholder(normalize_param_name(field_name), option_value, option_node)
            pending_gridcell_context = None
            continue

        if action_method == "click":
            gridcell_name, gridcell_node = _first_role_name(chain, roles={"gridcell"})
            if pending_gridcell_context and gridcell_name and gridcell_node:
                _record_placeholder(
                    normalize_param_name(pending_gridcell_context),
                    gridcell_name,
                    gridcell_node,
                )
                pending_gridcell_context = None
                continue

            text_value, text_node = _first_call_string_arg(chain, method="get_by_text")
            if last_search and text_value and text_node:
                _record_placeholder(normalize_param_name(last_search), text_value, text_node)
                last_search = None

            if title_text and title_text.lower().startswith("search:"):
                last_search = title_text.split(":", 1)[1].strip()

            context_name, _ = _first_role_name(chain, roles={"combobox", "listbox", "textbox"})
            pending_gridcell_context = context_name
            continue

        pending_gridcell_context = None

    if not replacements:
        return script_text, params

    updated_script = script_text
    for replacement in sorted(replacements, key=lambda item: item.start, reverse=True):
        updated_script = (
            updated_script[: replacement.start]
            + replacement.text
            + updated_script[replacement.end :]
        )

    return updated_script, params
