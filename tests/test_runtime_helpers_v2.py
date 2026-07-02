import importlib
import json
import os
import sys
from itertools import chain, repeat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.runtime import helpers_v2
from src.tools.tools import (
    _ensure_runner_pythonpath,
    _inject_runtime_helpers,
    _prepare_script_via_ast,
)


def _full_recording(body: str) -> str:
    return f"""
from playwright.sync_api import Playwright, sync_playwright


def run(playwright: Playwright) -> None:
{body}


with sync_playwright() as playwright:
    run(playwright)
"""


def test_prepare_script_via_ast_imports_runtime_helpers_v2() -> None:
    script = _full_recording(
        """    browser = playwright.chromium.launch(headless=False)
    page = browser.new_page()
    page.get_by_role("link", name="Home", exact=True).click()
    browser.close()"""
    )

    prepared = _prepare_script_via_ast(script)

    assert "from src.runtime.helpers_v2 import *" in prepared
    assert "def _act_wait_for_initial_page_settle" not in prepared


def test_act_get_multi_line_rows_reads_execution_payload(monkeypatch) -> None:
    monkeypatch.setenv(
        "ACT_EXECUTION_PARAMETERS_JSON",
        json.dumps(
            {
                "username": "svc.user",
                "multi_line": [
                    {"description": "Line 1", "quantity": "-1", "unit_price": "10"},
                    {"description": "Line 2", "quantity": "-2", "unit_price": "20"},
                ],
            }
        ),
    )

    assert helpers_v2._act_get_multi_line_rows() == [
        {"description": "Line 1", "quantity": "-1", "unit_price": "10"},
        {"description": "Line 2", "quantity": "-2", "unit_price": "20"},
    ]
    assert helpers_v2._ACT_MULTI_LINE_SHEET_SUMMARY == {
        "sheet_name": "multi_line",
        "loaded_row_count": 2,
        "raw_row_count": 2,
        "skipped_row_count": 0,
        "columns": ["description", "quantity", "unit_price"],
    }


def test_set_script_data_merges_multi_line_context() -> None:
    previous_context = helpers_v2._act_clone_json_value(helpers_v2._ACT_MULTI_LINE_CONTEXT)
    previous_script_data = helpers_v2._act_clone_json_value(helpers_v2._ACT_SCRIPT_DATA)
    try:
        helpers_v2._ACT_MULTI_LINE_CONTEXT.clear()
        helpers_v2._ACT_MULTI_LINE_CONTEXT.update(
            {
                "scope": "row",
                "row_index": 3,
                "total_rows": 7,
                "row_values": {"description": "Line 3", "quantity": "-3"},
            }
        )

        helpers_v2._act_set_script_data({"tracked_action": "fill_textbox"})

        assert helpers_v2._ACT_SCRIPT_DATA == {
            "tracked_action": "fill_textbox",
            "multi_line_context": {
                "scope": "row",
                "row_index": 3,
                "total_rows": 7,
                "row_values": {"description": "Line 3", "quantity": "-3"},
            },
        }
    finally:
        helpers_v2._ACT_MULTI_LINE_CONTEXT.clear()
        if isinstance(previous_context, dict):
            helpers_v2._ACT_MULTI_LINE_CONTEXT.update(previous_context)
        helpers_v2._act_set_script_data(previous_script_data if isinstance(previous_script_data, dict) else {})


def test_importing_helpers_v2_does_not_eagerly_import_html_report_generator(monkeypatch) -> None:
    monkeypatch.delitem(sys.modules, "src.runtime.helpers_v2", raising=False)
    monkeypatch.delitem(sys.modules, "src.utils", raising=False)
    monkeypatch.delitem(sys.modules, "src.utils.html_report_generator", raising=False)

    importlib.import_module("src.runtime.helpers_v2")

    assert "src.utils.html_report_generator" not in sys.modules


def test_legacy_inject_runtime_helpers_shims_to_v2_import() -> None:
    script = "from __future__ import annotations\n\nprint('hello')\n"

    instrumented = _inject_runtime_helpers(script)

    assert "from src.runtime.helpers_v2 import *" in instrumented
    assert "def _act_wait_for_initial_page_settle" not in instrumented


def test_ensure_runner_pythonpath_prepends_project_root_once() -> None:
    project_root = Path("/tmp/project").resolve()
    env = _ensure_runner_pythonpath({"PYTHONPATH": f"/tmp/a{os.pathsep}/tmp/b"}, project_root=project_root)

    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(project_root)
    assert env["PYTHONPATH"].split(os.pathsep).count(str(project_root)) == 1


def test_ensure_runner_pythonpath_sets_path_when_missing() -> None:
    project_root = Path("/tmp/project").resolve()
    env = _ensure_runner_pythonpath({}, project_root=project_root)

    assert env["PYTHONPATH"] == str(project_root)


class _FakeLocator:
    def __init__(self, *, value: str = "", text: str = "", actionable: bool = False) -> None:
        self._value = value
        self._text = text
        self._actionable = actionable

    def input_value(self) -> str:
        return self._value

    def inner_text(self) -> str:
        return self._text

    def text_content(self) -> str:
        return self._text

    def wait_for(self, *, state: str, timeout: int) -> None:
        if not self._actionable:
            raise RuntimeError("not actionable")

    def scroll_into_view_if_needed(self, *, timeout: int) -> None:
        return None


class _TimeoutRecordingLocator:
    def __init__(self) -> None:
        self.events: list[tuple[str, int]] = []

    def wait_for(self, *, state: str, timeout: int) -> None:
        self.events.append(("wait_for", timeout))

    def scroll_into_view_if_needed(self, *, timeout: int) -> None:
        self.events.append(("scroll", timeout))

    def click(self, *, timeout: int) -> None:
        self.events.append(("click", timeout))

    def dblclick(self, *, timeout: int) -> None:
        self.events.append(("dblclick", timeout))

    def fill(self, value: str, *, timeout: int) -> None:
        self.events.append(("fill", timeout))


class _CheckboxLocator:
    def __init__(self, *, checked: bool = False, check_raises: bool = False) -> None:
        self.checked = checked
        self.check_raises = check_raises
        self.events: list[tuple[str, int | None]] = []

    def wait_for(self, *, state: str, timeout: int) -> None:
        return None

    def scroll_into_view_if_needed(self, *, timeout: int) -> None:
        return None

    def is_checked(self) -> bool:
        return self.checked

    def check(self, *, timeout: int) -> None:
        self.events.append(("check", timeout))
        if self.check_raises:
            raise RuntimeError("raw check not supported")
        self.checked = True

    def uncheck(self, *, timeout: int) -> None:
        self.events.append(("uncheck", timeout))
        if self.check_raises:
            raise RuntimeError("raw uncheck not supported")
        self.checked = False

    def click(self, timeout: int | None = None) -> None:
        self.events.append(("click", timeout))
        self.checked = not self.checked


class _FakeHandle:
    def evaluate(self, expression: str, arg=None):
        if "const readValue" in expression:
            return "fast-value"
        return "fast text"


class _FastSnapshotLocator:
    def __init__(self) -> None:
        self.timeout = None

    def element_handle(self, timeout: int):
        self.timeout = timeout
        return _FakeHandle()

    def input_value(self) -> str:
        raise AssertionError("raw input_value should not be used")

    def inner_text(self) -> str:
        raise AssertionError("raw inner_text should not be used")

    def text_content(self) -> str:
        raise AssertionError("raw text_content should not be used")


class _NestedValueHandle:
    def evaluate(self, expression: str, arg=None):
        if "aria-valuenow" in expression and "querySelector" in expression:
            return "1"
        return ""


class _NestedValueLocator:
    def element_handle(self, timeout: int):
        return _NestedValueHandle()


class _NamedLocator:
    def __init__(self, name: str) -> None:
        self.name = name
        self.filled: list[str] = []
        self.pressed: list[str] = []

    def press(self, key: str) -> None:
        self.pressed.append(key)


class _OracleHomePage:
    def __init__(self) -> None:
        self.url = "https://eqjz.ds-fa.oraclepdemos.com/fscmUI/faces/FuseWelcome"
        self.search = _NamedLocator("search")
        self.result = _NamedLocator("result")
        self.waits: list[int] = []

    def get_by_role(self, role: str, name: str | None = None, exact: bool | None = None):
        if role == "combobox" and name == "Search:":
            return self.search
        if role == "link" and name == "Promote and Change Position":
            return self.result
        return _NamedLocator(f"{role}:{name}")

    def get_by_placeholder(self, text: str, exact: bool | None = None):
        return self.search

    def get_by_text(self, text: str, exact: bool | None = None):
        return self.result if text == "Promote and Change Position" else _NamedLocator(f"text:{text}")

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)


class _FakeChromium:
    def __init__(self) -> None:
        self.launch_kwargs = None
        self.connect_calls: list[dict[str, object]] = []

    def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return kwargs

    def connect_over_cdp(self, url: str, **kwargs):
        payload = {"url": url, "kwargs": kwargs}
        self.connect_calls.append(payload)
        return payload


class _FakePlaywright:
    def __init__(self) -> None:
        self.chromium = _FakeChromium()


class _FakeSteelSessions:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.retrieved: list[str] = []
        self.released: list[str] = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id="steel-session-created")

    def retrieve(self, session_id: str):
        self.retrieved.append(session_id)
        return SimpleNamespace(id=session_id)

    def release(self, session_id: str) -> None:
        self.released.append(session_id)


class _FakeSteelClient:
    instances: list["_FakeSteelClient"] = []

    def __init__(self, steel_api_key: str | None = None) -> None:
        self.steel_api_key = steel_api_key
        self.sessions = _FakeSteelSessions()
        type(self).instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.instances.clear()


class _DateLocator:
    def __init__(self, name: str) -> None:
        self.name = name

    @property
    def first(self):
        return self


class _WarningDialogPage:
    def __init__(self) -> None:
        self.url = "https://example.com/fscmUI/faces/ap/invoice"
        self.waits: list[int] = []
        self.locator_calls: list[str] = []

    def locator(self, selector: str):
        self.locator_calls.append(selector)
        return _DateLocator(f"locator:{selector}")

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)


class _FilteredLocatorCollection:
    def __init__(self, locator) -> None:
        self._locator = locator
        self.has_text = None

    def filter(self, *, has_text=None):
        self.has_text = has_text
        return self

    @property
    def first(self):
        return self._locator


class _OracleQuickActionPage:
    def __init__(self) -> None:
        self.quick_action = _DateLocator("quick_action")
        self.role_exact = _DateLocator("role_exact")
        self.text_exact = _DateLocator("text_exact")
        self.waits: list[int] = []

    def locator(self, selector: str):
        if selector in {"a[type='quickaction']", "a.flat-quickactions-item-link"}:
            return _FilteredLocatorCollection(self.quick_action)
        return _FilteredLocatorCollection(_DateLocator(f"locator:{selector}"))

    def get_by_role(self, role: str, name: str | None = None, exact: bool | None = None):
        if role == "link" and name == "Promote and Change Position" and exact is True:
            return self.role_exact
        return _NamedLocator(f"{role}:{name}:{exact}")

    def get_by_text(self, text: str, exact: bool | None = None):
        if text == "Promote and Change Position" and exact is True:
            return self.text_exact
        return _NamedLocator(f"text:{text}:{exact}")

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)


class _OracleNotificationBadgePage:
    def __init__(self) -> None:
        self.notification_role = _DateLocator("notification_role")
        self.notification_text = _DateLocator("notification_text")
        self.waits: list[int] = []

    def get_by_role(self, role: str, name=None, exact: bool | None = None):
        pattern = getattr(name, "pattern", "")
        if role == "link" and "notifications" in str(pattern).lower() and "unread" in str(pattern).lower():
            return self.notification_role
        return _NamedLocator(f"{role}:{name}:{exact}")

    def get_by_text(self, text, exact: bool | None = None):
        pattern = getattr(text, "pattern", "")
        if "notifications" in str(pattern).lower() and "unread" in str(pattern).lower():
            return self.notification_text
        return _NamedLocator(f"text:{text}:{exact}")

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)


class _RecordedButtonContextPage:
    def __init__(self) -> None:
        self.title_button = _DateLocator("title_button")
        self.id_button = _DateLocator("id_button")
        self.waits: list[int] = []

    def title(self) -> str:
        return "Notifications - Oracle Fusion Cloud Applications"

    def locator(self, selector: str):
        if selector == 'button[title="Approve Job Requisition Medical Office Administrator - 1269 Requires Approval"]':
            return _FilteredLocatorCollection(self.title_button)
        if selector == 'button[id="_FOpt1:_FOr1:0:_FONSr2:0:MAnt2:0:up1:UPsp1:r1:0:lv4:2:cb2"]':
            return _FilteredLocatorCollection(self.id_button)
        return _FilteredLocatorCollection(_DateLocator(f"locator:{selector}"))

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)


class _KeyboardEntryLocator:
    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def wait_for(self, *, state: str, timeout: int) -> None:
        self.events.append(("wait_for", state, timeout))

    def scroll_into_view_if_needed(self, *, timeout: int) -> None:
        self.events.append(("scroll", timeout))

    def click(self, *, timeout: int) -> None:
        self.events.append(("click", timeout))

    def press(self, key: str, *, timeout: int | None = None) -> None:
        self.events.append(("press", key, timeout))

    def press_sequentially(self, text: str, *, delay: int | None = None, timeout: int | None = None) -> None:
        self.events.append(("press_sequentially", text, delay, timeout))

    def type(self, text: str, *, delay: int | None = None, timeout: int | None = None) -> None:
        self.events.append(("type", text, delay, timeout))

    def fill(self, value: str, *, timeout: int | None = None) -> None:
        self.events.append(("fill", value, timeout))


class _OracleTableEditorLocator(_KeyboardEntryLocator):
    def __init__(self) -> None:
        super().__init__()
        self.current_value = ""

    def press(self, key: str, *, timeout: int | None = None) -> None:
        self.events.append(("press", key, timeout))
        if key == "ControlOrMeta+A":
            return
        if key == "Backspace":
            self.current_value = ""

    def press_sequentially(self, text: str, *, delay: int | None = None, timeout: int | None = None) -> None:
        self.events.append(("press_sequentially", text, delay, timeout))
        self.current_value = text

    def type(self, text: str, *, delay: int | None = None, timeout: int | None = None) -> None:
        self.events.append(("type", text, delay, timeout))
        self.current_value = text

    def fill(self, value: str, *, timeout: int | None = None) -> None:
        self.events.append(("fill", value, timeout))
        self.current_value = value


class _OracleKeyboardEntryLocator(_KeyboardEntryLocator):
    def __init__(self) -> None:
        super().__init__()
        self.expanded = False
        self.focused = False

    def click(self, *, timeout: int) -> None:
        self.events.append(("click", timeout))
        raise RuntimeError("oj-label intercepts pointer events")

    def focus(self, timeout: int | None = None) -> None:
        self.focused = True
        self.events.append(("focus", timeout))

    def press(self, key: str, *, timeout: int | None = None) -> None:
        self.events.append(("press", key, timeout))
        if key == "ArrowDown":
            self.expanded = True


class _OracleKeyboardComboboxLocator:
    def __init__(self) -> None:
        self.expanded = False
        self.focused = False
        self.pressed: list[tuple[str, int | None]] = []

    def focus(self, timeout: int | None = None) -> None:
        self.focused = True

    def press(self, key: str, *, timeout: int | None = None) -> None:
        self.pressed.append((key, timeout))
        if key == "ArrowDown":
            self.expanded = True


class _OracleKeyboardSelectLocator:
    def __init__(self) -> None:
        self.focused = False
        self.events: list[tuple[str, object | None]] = []

    def focus(self, timeout: int | None = None) -> None:
        self.focused = True
        self.events.append(("focus", timeout))

    def press(self, key: str, *, timeout: int | None = None) -> None:
        self.events.append(("press", key))


class _DatePage:
    def __init__(self, attr_locator: _DateLocator, label_locator: _DateLocator | None = None) -> None:
        self.attr_locator = attr_locator
        self.label_locator = label_locator or attr_locator
        self.waits: list[int] = []

    def locator(self, selector: str):
        return self.attr_locator

    def get_by_label(self, text: str):
        return self.label_locator

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)


class _ActionCardSwitchLocator:
    def __init__(self) -> None:
        self.aria_checked = "false"

    @property
    def first(self):
        return self


class _ActionCardLocator:
    def __init__(self, name: str) -> None:
        self.name = name
        self.switch = _ActionCardSwitchLocator()

    @property
    def first(self):
        return self

    def filter(self, **kwargs):
        return self

    def locator(self, selector: str):
        if selector == "[role='switch']":
            return self.switch
        raise AssertionError(f"unexpected selector: {selector}")


class _ActionCardPage:
    def __init__(self, card: _ActionCardLocator) -> None:
        self.url = "https://example.com/fscmUI/redwood/employment-change/update/assignment"
        self.card = card
        self.waits: list[int] = []

    def locator(self, selector: str):
        return self.card

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)


class _EvaluatePage:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.payloads: list[dict[str, object]] = []

    def evaluate(self, script: str, payload: dict[str, object]):
        self.payloads.append(payload)
        return dict(self.response)


class _PromptPage:
    def __init__(self) -> None:
        self.url = "https://example.com/fscmUI/redwood/employment-change/update/manager"

    def title(self) -> str:
        return "Change Manager - Oracle Fusion Cloud Applications"


class _AIScreenshotPage(_PromptPage):
    def __init__(self) -> None:
        super().__init__()
        self.screenshot_calls: list[dict[str, object]] = []
        self.waits: list[int] = []

    def screenshot(self, **kwargs):
        self.screenshot_calls.append(dict(kwargs))
        return b"fake-jpeg-bytes"

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)


class _NavigationPage:
    def __init__(self) -> None:
        self.url = "https://example.com/fscmUI/redwood/employment-change/update/assignment"
        self.waits: list[int] = []

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)


class _SnapshotPage(_NavigationPage):
    def title(self) -> str:
        return "Create Job Requisition - Oracle Fusion Cloud Applications"

    def evaluate(self, script: str):
        if "oj-table-scroller table.oj-table-element" in script:
            return [
                {
                    "table_index": 0,
                    "id": "requisition-dynamic-table_table",
                    "aria_labelledby": "requisition-dynamic-table_table",
                    "headers": ["Requisition Title", "Requisition Number", "Requisition Status"],
                    "rows": [["Analyst", "1003", "Approval - Pending"]],
                }
            ]
        return "Requisition REQ-10025 created successfully"


class _OptionPage:
    def __init__(self, locator: _FakeLocator) -> None:
        self.locator_ref = locator
        self.url = "https://example.com/fscmUI/redwood/demo"
        self.waits: list[int] = []

    def get_by_role(self, role: str, name: str | None = None, exact: bool | None = None):
        return self.locator_ref

    def get_by_text(self, text: str, exact: bool | None = None):
        return self.locator_ref

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)


class _SearchOptionLocator:
    def __init__(self, name: str) -> None:
        self.name = name

    @property
    def first(self):
        return self

    def wait_for(self, *, state: str, timeout: int) -> None:
        return None

    def scroll_into_view_if_needed(self, *, timeout: int) -> None:
        return None


class _AmbiguousSearchOptionLocator(_SearchOptionLocator):
    @property
    def first(self):
        return _SearchOptionLocator(f"{self.name}:first")


class _SearchOptionPage:
    def __init__(self) -> None:
        self.url = "https://example.com/fscmUI/redwood/demo"
        self.waits: list[int] = []
        self.role_calls: list[tuple[str, str | None, bool | None]] = []
        self.text_calls: list[tuple[str, bool | None]] = []
        self.locator_calls: list[str] = []
        self.scoped_role_calls: list[tuple[str, str, str | None, bool | None]] = []
        self.scoped_text_calls: list[tuple[str, str, bool | None]] = []

    def get_by_role(self, role: str, name: str | None = None, exact: bool | None = None):
        self.role_calls.append((role, name, exact))
        return _SearchOptionLocator(f"{role}:{name}")

    def get_by_text(self, text: str, exact: bool | None = None):
        self.text_calls.append((text, exact))
        return _SearchOptionLocator(f"text:{text}:{exact}")

    def locator(self, selector: str):
        self.locator_calls.append(selector)
        return _ScopedSearchOptionScope(self, selector)

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)


class _ScopedSearchOptionScope:
    def __init__(self, page: _SearchOptionPage, selector: str) -> None:
        self.page = page
        self.selector = selector

    def get_by_role(self, role: str, name: str | None = None, exact: bool | None = None):
        self.page.scoped_role_calls.append((self.selector, role, name, exact))
        return _SearchOptionLocator(f"scope:{self.selector}:{role}:{name}:{exact}")

    def get_by_text(self, text: str, exact: bool | None = None):
        self.page.scoped_text_calls.append((self.selector, text, exact))
        return _SearchOptionLocator(f"scope:{self.selector}:text:{text}:{exact}")


class _OracleLovDirectEntryInputLocator(_SearchOptionLocator):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.presses: list[tuple[str, int | None]] = []

    def press(self, key: str, *, timeout: int | None = None) -> None:
        self.presses.append((key, timeout))


class _OracleLovDirectEntryPage(_SearchOptionPage):
    def __init__(self, input_locator: _OracleLovDirectEntryInputLocator) -> None:
        super().__init__()
        self.input_locator = input_locator
        self.locator_calls: list[str] = []

    def locator(self, selector: str):
        self.locator_calls.append(selector)
        return self.input_locator


class _CompletionPage(_SearchOptionPage):
    def __init__(self, *, title_text: str) -> None:
        super().__init__()
        self.url = "https://example.com/fscmUI/ar/edit-transaction"
        self.title_text = title_text
        self.current_step = "Edit Transaction"

    def title(self) -> str:
        return self.title_text


def test_tracked_action_failure_records_normalized_runtime_action_name(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(helpers_v2, "_act_capture_failure_screenshot", lambda: None)
    monkeypatch.setattr(helpers_v2, "_act_finalize_action_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_capture_step", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: captured.update(kwargs))

    def _act_select_combobox_option():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        helpers_v2._act_tracked_action("select_combobox", "Salary Basis", _act_select_combobox_option)

    assert captured["action_type"] == "select_combobox_option"


def test_tracked_action_waits_after_success(monkeypatch) -> None:
    page = _NavigationPage()
    captured_steps: list[str] = []
    finalized: dict[str, object] = {}

    monkeypatch.setattr(helpers_v2, "_ACT_LAST_PAGE", page)
    monkeypatch.setattr(helpers_v2, "_act_capture_step", lambda action_type: captured_steps.append(action_type))
    monkeypatch.setattr(
        helpers_v2,
        "_act_finalize_action_log",
        lambda *args, **kwargs: finalized.update({"args": args, "kwargs": kwargs}),
    )

    helpers_v2._act_tracked_action("click_button", "Continue", lambda current_page: "ok", page)

    assert page.waits == [0]
    assert captured_steps == ["click_button"]
    assert finalized["kwargs"]["page"] is page


def test_tracked_action_records_multi_line_context_in_debug_trace(monkeypatch) -> None:
    page = _NavigationPage()

    monkeypatch.setattr(helpers_v2, "_ACT_LAST_PAGE", page)
    monkeypatch.setattr(helpers_v2, "_ACT_ACTION_LOG", [])
    monkeypatch.setattr(helpers_v2, "_act_capture_step", lambda action_type: None)
    monkeypatch.setattr(helpers_v2, "_act_capture_failure_screenshot", lambda: None)
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: None)

    previous_context = helpers_v2._act_clone_json_value(helpers_v2._ACT_MULTI_LINE_CONTEXT)
    previous_script_data = helpers_v2._act_clone_json_value(helpers_v2._ACT_SCRIPT_DATA)
    try:
        helpers_v2._ACT_MULTI_LINE_CONTEXT.clear()
        helpers_v2._ACT_MULTI_LINE_CONTEXT.update(
            {
                "scope": "row",
                "row_index": 2,
                "total_rows": 5,
                "row_values": {"description": "Ambulance Fee", "quantity": "2"},
            }
        )
        helpers_v2._act_set_script_data({"tracked_action": "fill_textbox"})

        helpers_v2._act_tracked_action("click_button", "Save", lambda current_page: "ok", page)

        entry = helpers_v2._ACT_ACTION_LOG[0]
        assert entry["script_data"]["multi_line_context"]["row_index"] == 2
        assert entry["debug"]["multi_line_context"]["row_index"] == 2
        assert entry["debug"]["multi_line_context"]["row_values"]["description"] == "Ambulance Fee"
    finally:
        helpers_v2._ACT_MULTI_LINE_CONTEXT.clear()
        if isinstance(previous_context, dict):
            helpers_v2._ACT_MULTI_LINE_CONTEXT.update(previous_context)
        helpers_v2._act_set_script_data(previous_script_data if isinstance(previous_script_data, dict) else {})


def test_tracked_action_does_not_wait_after_failure(monkeypatch) -> None:
    page = _NavigationPage()

    monkeypatch.setattr(helpers_v2, "_ACT_LAST_PAGE", page)
    monkeypatch.setattr(helpers_v2, "_act_capture_step", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_capture_failure_screenshot", lambda: None)
    monkeypatch.setattr(helpers_v2, "_act_finalize_action_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: None)

    with pytest.raises(RuntimeError):
        helpers_v2._act_tracked_action("click_button", "Continue", lambda current_page: (_ for _ in ()).throw(RuntimeError("boom")), page)

    assert page.waits == []


def test_tracked_action_failure_captures_step_before_failure_screenshot(monkeypatch) -> None:
    page = _NavigationPage()
    events: list[str] = []

    monkeypatch.setattr(helpers_v2, "_ACT_LAST_PAGE", page)
    monkeypatch.setattr(helpers_v2, "_act_capture_step", lambda action_type: events.append(f"step:{action_type}"))
    monkeypatch.setattr(helpers_v2, "_act_capture_failure_screenshot", lambda: events.append("failure_screenshot"))
    monkeypatch.setattr(helpers_v2, "_act_finalize_action_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: None)

    with pytest.raises(RuntimeError):
        helpers_v2._act_tracked_action(
            "click_button",
            "Continue",
            lambda current_page: (_ for _ in ()).throw(RuntimeError("boom")),
            page,
        )

    assert events == ["step:click_button", "failure_screenshot"]


def test_tracked_action_uses_universal_ai_repair_for_failed_checkbox_helper(monkeypatch) -> None:
    page = _NavigationPage()
    original_locator = object()
    ai_locator = object()
    captured_steps: list[str] = []
    failure_events: list[str] = []
    finalized: dict[str, object] = {}
    stored_episodes: list[dict[str, object]] = []

    def _act_check_target(locator, current_page, step_label):
        if locator is original_locator:
            raise RuntimeError("boom")
        assert locator is ai_locator
        assert current_page is page
        assert step_label == "Items"
        return "ai-ok"

    monkeypatch.setattr(helpers_v2, "_ACT_LAST_PAGE", page)
    monkeypatch.setattr(helpers_v2, "_act_resolve_page", lambda args: page)
    monkeypatch.setattr(helpers_v2, "_act_resolve_primary_locator", lambda args: original_locator)
    monkeypatch.setattr(helpers_v2, "_act_capture_step", lambda action_type: captured_steps.append(action_type))
    monkeypatch.setattr(helpers_v2, "_act_capture_failure_screenshot", lambda: failure_events.append("failure"))
    monkeypatch.setattr(
        helpers_v2,
        "_act_finalize_action_log",
        lambda *args, **kwargs: finalized.update({"args": args, "kwargs": kwargs}),
    )
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: stored_episodes.append(kwargs))
    monkeypatch.setattr(helpers_v2, "_act_set_recovery_record", lambda *args, **kwargs: None)

    def _fake_execute_ai_repair_rounds(**kwargs):
        assert kwargs["helper"] == "check_target"
        assert kwargs["label"] == "Items"
        assert kwargs["postcondition_kind"] == "checkbox_state_changed"
        assert kwargs["execute_locator"]("ai_css_1", ai_locator, {"selector": "#ai"}) is True
        return (("ai_css_1", ai_locator, {"selector": "#ai"}), kwargs["last_error"])

    monkeypatch.setattr(helpers_v2, "_act_execute_ai_repair_rounds", _fake_execute_ai_repair_rounds)

    result = helpers_v2._act_tracked_action("check", "Items", _act_check_target, original_locator, page, "Items")

    assert result == "ai-ok"
    assert page.waits == [0]
    assert captured_steps == ["check"]
    assert failure_events == []
    assert finalized["args"][2] == "success"
    assert stored_episodes and stored_episodes[-1]["status"] == "success"
    assert stored_episodes[-1]["action_type"] == "check_target"


def test_tracked_action_does_not_run_universal_ai_when_helper_already_used_ai(monkeypatch) -> None:
    page = _NavigationPage()
    original_locator = object()
    events: list[str] = []

    def _act_check_target(locator, current_page, step_label):
        helpers_v2._ACT_CURRENT_STRATEGY["ai_interactions"] = [{"status": "requested"}]
        raise RuntimeError("boom")

    monkeypatch.setattr(helpers_v2, "_ACT_LAST_PAGE", page)
    monkeypatch.setattr(helpers_v2, "_act_resolve_page", lambda args: page)
    monkeypatch.setattr(helpers_v2, "_act_resolve_primary_locator", lambda args: original_locator)
    monkeypatch.setattr(helpers_v2, "_act_capture_step", lambda action_type: events.append(f"step:{action_type}"))
    monkeypatch.setattr(helpers_v2, "_act_capture_failure_screenshot", lambda: events.append("failure"))
    monkeypatch.setattr(helpers_v2, "_act_finalize_action_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_execute_ai_repair_rounds",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("universal AI should not run")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        helpers_v2._act_tracked_action("check", "Items", _act_check_target, original_locator, page, "Items")

    assert page.waits == []
    assert events == ["step:check", "failure"]


def test_tracked_raw_action_records_raw_inline_strategy(monkeypatch) -> None:
    page = _NavigationPage()
    captured_steps: list[str] = []

    monkeypatch.setattr(helpers_v2, "_ACT_LAST_PAGE", page)
    monkeypatch.setattr(helpers_v2, "_ACT_ACTION_LOG", [])
    monkeypatch.setattr(helpers_v2, "_act_capture_step", lambda action_type: captured_steps.append(action_type))
    monkeypatch.setattr(helpers_v2, "_act_capture_failure_screenshot", lambda: None)
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: None)
    helpers_v2._act_set_script_data({"raw": "calls.append('ran')"})

    local_scope = {"calls": []}

    helpers_v2._act_tracked_raw_action(
        "click",
        ".xen",
        "calls.append('ran')",
        {},
        local_scope,
        page=page,
    )

    assert local_scope["calls"] == ["ran"]
    assert captured_steps == ["click"]
    assert helpers_v2._ACT_ACTION_LOG[0]["strategy"] == "raw_inline"
    assert helpers_v2._ACT_ACTION_LOG[0]["label"] == ".xen"


def test_tracked_raw_action_exec_scope_includes_re_module(monkeypatch) -> None:
    page = _NavigationPage()

    monkeypatch.setattr(helpers_v2, "_ACT_LAST_PAGE", page)
    monkeypatch.setattr(helpers_v2, "_ACT_ACTION_LOG", [])
    monkeypatch.setattr(helpers_v2, "_act_capture_step", lambda action_type: None)
    monkeypatch.setattr(helpers_v2, "_act_capture_failure_screenshot", lambda: None)
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: None)
    helpers_v2._act_set_script_data({"raw": "calls.append(bool(re.compile('^Sales Territory').match('Sales TerritoryAutocompletes on TAB')))"})

    local_scope = {"calls": []}

    helpers_v2._act_tracked_raw_action(
        "click",
        "tr",
        "calls.append(bool(re.compile('^Sales Territory').match('Sales TerritoryAutocompletes on TAB')))",
        {},
        local_scope,
        page=page,
    )

    assert local_scope["calls"] == [True]
    assert helpers_v2._ACT_ACTION_LOG[0]["strategy"] == "raw_inline"


def test_wait_after_interaction_captures_page_snapshot_with_default_zero_delay(monkeypatch) -> None:
    page = _SnapshotPage()

    monkeypatch.setattr(helpers_v2, "_ACT_LAST_PAGE_SNAPSHOT", {})

    helpers_v2._act_wait_after_interaction(page)

    assert page.waits == [0]
    assert helpers_v2._ACT_LAST_PAGE_SNAPSHOT["page_url"] == page.url
    assert helpers_v2._ACT_LAST_PAGE_SNAPSHOT["page_title"] == "Create Job Requisition - Oracle Fusion Cloud Applications"
    assert helpers_v2._ACT_LAST_PAGE_SNAPSHOT["page_text"] == "Requisition REQ-10025 created successfully"
    assert helpers_v2._ACT_LAST_PAGE_SNAPSHOT["oracle_tables"][0]["rows"][0][1] == "1003"


def test_wait_after_interaction_honors_env_override(monkeypatch) -> None:
    page = _SnapshotPage()

    monkeypatch.setattr(helpers_v2, "_ACT_LAST_PAGE_SNAPSHOT", {})
    monkeypatch.setenv("ACT_AFTER_ACTION_WAIT_MS", "2000")

    helpers_v2._act_wait_after_interaction(page)

    assert page.waits == [2_000]


def test_wait_after_interaction_honors_zero_env_override(monkeypatch) -> None:
    page = _SnapshotPage()

    monkeypatch.setattr(helpers_v2, "_ACT_LAST_PAGE_SNAPSHOT", {})
    monkeypatch.setenv("ACT_AFTER_ACTION_WAIT_MS", "0")

    helpers_v2._act_wait_after_interaction(page)

    assert page.waits == [0]


def test_wait_after_interaction_falls_back_to_default_on_bad_override(monkeypatch) -> None:
    page = _SnapshotPage()

    monkeypatch.setattr(helpers_v2, "_ACT_LAST_PAGE_SNAPSHOT", {})
    monkeypatch.setenv("ACT_AFTER_ACTION_WAIT_MS", "not-a-number")

    helpers_v2._act_wait_after_interaction(page)

    assert page.waits == [helpers_v2._ACT_HARDCODED_AFTER_ACTION_WAIT_MS]


def test_write_diagnostics_persists_oracle_tables(tmp_path, monkeypatch) -> None:
    diagnostics_path = tmp_path / "diagnostics.json"
    monkeypatch.setattr(helpers_v2, "_ACT_DIAGNOSTICS_PATH", str(diagnostics_path))
    monkeypatch.setattr(helpers_v2, "_ACT_LAST_PAGE", None)
    monkeypatch.setattr(
        helpers_v2,
        "_ACT_LAST_PAGE_SNAPSHOT",
        {
            "page_url": "https://example.com/requisitions",
            "page_title": "Job Requisitions",
            "page_text": "Analyst 1006 Approval - Pending",
            "oracle_tables": [
                {
                    "headers": ["Requisition Title", "Requisition Number"],
                    "rows": [["Analyst", "1006"]],
                }
            ],
            "page_semantics": {
                "label_values": [{"label": "Requisition Number", "value": "1006"}],
                "text_candidates": [],
                "dialogs": [],
            },
        },
    )
    monkeypatch.setattr(helpers_v2, "_ACT_FAILURE_SCREENSHOT_PATH", None)
    monkeypatch.setattr(helpers_v2, "_ACT_STEP_ARTIFACTS", [])
    monkeypatch.setattr(helpers_v2, "_ACT_ACTION_LOG", [])
    monkeypatch.setattr(
        helpers_v2,
        "_ACT_CURRENT_STRATEGY",
        {
            "helper": "fill_textbox",
            "strategy": "oracle_spinbutton_keyboard_fill",
            "label": "Picked Quantity",
            "attempts": ["direct", "oracle_spinbutton_keyboard_fill"],
            "ai_interactions": [],
            "experience_interactions": [],
            "script_data": {"tracked_action": "fill_textbox"},
            "recovery": {"handler_name": "oracle_spinbutton_fill"},
            "debug": {"fill_textbox": {"status": "success"}},
        },
    )
    monkeypatch.setenv("ACT_DEBUG_TRACE", "true")
    monkeypatch.setenv("ACT_CAPTURE_STEPS", "true")
    monkeypatch.setenv("ACT_TEXT_ENTRY_TIMEOUT_MS", "4500")

    helpers_v2._act_write_diagnostics()

    payload = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    assert payload["oracle_tables"][0]["rows"][0][1] == "1006"
    assert payload["page_semantics"]["label_values"][0]["value"] == "1006"
    assert payload["runtime_debug"]["settings"]["debug_trace"] is True
    assert payload["runtime_debug"]["settings"]["text_entry_timeout_ms"] == 4500
    assert payload["runtime_debug"]["snapshot_summary"]["oracle_table_count"] == 1
    assert payload["runtime_debug"]["last_strategy_state"]["strategy"] == "oracle_spinbutton_keyboard_fill"


def test_capture_live_snapshot_before_close_persists_latest_live_page(tmp_path, monkeypatch) -> None:
    diagnostics_path = tmp_path / "diagnostics.json"
    page = _SnapshotPage()

    monkeypatch.setattr(helpers_v2, "_ACT_DIAGNOSTICS_PATH", str(diagnostics_path))
    monkeypatch.setattr(helpers_v2, "_ACT_LAST_PAGE", page)
    monkeypatch.setattr(helpers_v2, "_ACT_LAST_PAGE_SNAPSHOT", {})
    monkeypatch.setattr(helpers_v2, "_ACT_FAILURE_SCREENSHOT_PATH", None)
    monkeypatch.setattr(helpers_v2, "_ACT_STEP_ARTIFACTS", [])
    monkeypatch.setattr(helpers_v2, "_ACT_ACTION_LOG", [])
    monkeypatch.setenv("ACT_FLOW_CONTEXT_PRE_CLOSE_WAIT_MS", "0")

    helpers_v2._act_capture_live_snapshot_before_close(page)

    payload = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    assert payload["page_url"] == page.url
    assert payload["oracle_tables"][0]["rows"][0][1] == "1003"


def test_capture_step_uses_override_png_bytes(tmp_path, monkeypatch) -> None:
    page = _AIScreenshotPage()
    monkeypatch.setattr(helpers_v2, "_ACT_LAST_PAGE", page)
    monkeypatch.setattr(helpers_v2, "_ACT_STEP_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(helpers_v2, "_ACT_STEP_INDEX", 0)
    monkeypatch.setattr(helpers_v2, "_ACT_STEP_ARTIFACTS", [])
    monkeypatch.setattr(helpers_v2, "_ACT_NEXT_STEP_SCREENSHOT_OVERRIDE_PNG", b"override-png-bytes")

    helpers_v2._act_capture_step("ai_extract")

    assert page.screenshot_calls == []
    assert helpers_v2._ACT_NEXT_STEP_SCREENSHOT_OVERRIDE_PNG is None
    assert len(helpers_v2._ACT_STEP_ARTIFACTS) == 1
    artifact_path = Path(helpers_v2._ACT_STEP_ARTIFACTS[0]["local_path"])
    assert artifact_path.read_bytes() == b"override-png-bytes"


def test_option_selection_postcondition_accepts_trigger_value_match(monkeypatch) -> None:
    trigger = _FakeLocator(value="ES Annual Salary Basis")
    option = _FakeLocator(actionable=True)
    monkeypatch.setattr(helpers_v2, "_act_locator_value", lambda locator: "ES Annual Salary Basis")
    monkeypatch.setattr(helpers_v2, "_act_locator_text", lambda locator: "")

    assert helpers_v2._act_option_selection_postcondition(
        {"dialog_count": 1},
        {"dialog_count": 1},
        trigger,
        option,
        "ES Annual Salary Basis",
    )


def test_oracle_menu_option_semantic_postcondition_waits_for_invoice_validation(monkeypatch) -> None:
    page = _NavigationPage()
    states = iter([True, False])
    times = iter([0.0, 0.0])

    monkeypatch.setattr(
        helpers_v2,
        "_act_oracle_invoice_shows_not_validated",
        lambda current_page: next(states, False),
    )
    monkeypatch.setattr(helpers_v2.time, "time", lambda: next(times, 1.0))
    monkeypatch.setenv("ACT_INVOICE_VALIDATE_POSTCONDITION_TIMEOUT_MS", "500")
    monkeypatch.setenv("ACT_MENU_OPTION_SEMANTIC_POLL_MS", "100")

    assert helpers_v2._act_oracle_menu_option_semantic_postcondition(page, "Invoice Actions", "Validate") is True
    assert page.waits == [100]


def test_oracle_invoice_shows_not_validated_accepts_never_validated_copy(monkeypatch) -> None:
    monkeypatch.setattr(
        helpers_v2,
        "_act_page_visible_text",
        lambda page: "invoice status never validated accounting required",
    )

    assert helpers_v2._act_oracle_invoice_shows_not_validated(_NavigationPage()) is True


def test_oracle_menu_option_semantic_postcondition_waits_for_accounting_surface(monkeypatch) -> None:
    page = _NavigationPage()
    states = iter([False, True])
    times = iter([0.0, 0.0])

    monkeypatch.setattr(
        helpers_v2,
        "_act_oracle_invoice_accounting_ready",
        lambda current_page: next(states, True),
    )
    monkeypatch.setattr(helpers_v2.time, "time", lambda: next(times, 1.0))
    monkeypatch.setenv("ACT_INVOICE_ACCOUNTING_POSTCONDITION_TIMEOUT_MS", "500")
    monkeypatch.setenv("ACT_MENU_OPTION_SEMANTIC_POLL_MS", "100")

    assert helpers_v2._act_oracle_menu_option_semantic_postcondition(page, "Invoice Actions", "Account in Final") is True
    assert page.waits == [100]


def test_wait_for_oracle_menu_trigger_option_visibility_polls_until_invoice_actions_option_is_visible(monkeypatch) -> None:
    page = _NavigationPage()
    option = _SearchOptionLocator("raw-option")
    option_candidates = [
        ("raw_option", option),
        ("role_menuitem", _SearchOptionLocator("menuitem:Validate")),
        ("role_option", _SearchOptionLocator("option:Validate")),
        ("text_option", _SearchOptionLocator("text:Validate:True")),
    ]
    times = iter([0.0, 0.0])

    monkeypatch.setattr(
        helpers_v2,
        "_act_locator_is_actionable",
        lambda locator, timeout_ms=None: bool(page.waits) and getattr(locator, "name", "") == "menuitem:Validate",
    )
    monkeypatch.setattr(helpers_v2.time, "time", lambda: next(times, 1.0))
    monkeypatch.setenv("ACT_MENU_TRIGGER_OPTION_TIMEOUT_MS", "500")
    monkeypatch.setenv("ACT_MENU_TRIGGER_OPTION_POLL_MS", "100")

    assert helpers_v2._act_wait_for_oracle_menu_trigger_option_visibility(
        page,
        option_candidates,
        "Invoice Actions",
    ) is True
    assert page.waits == [100]


def test_wait_for_oracle_menu_trigger_option_visibility_accepts_menuitem_when_raw_validate_text_is_ambiguous(monkeypatch) -> None:
    page = _SearchOptionPage()
    option = _SearchOptionLocator("raw-option")
    option_candidates = helpers_v2._act_menu_panel_option_candidates(page, option, "Validate")

    monkeypatch.setattr(
        helpers_v2,
        "_act_locator_is_actionable",
        lambda locator, timeout_ms=None: getattr(locator, "name", "") == "menuitem:Validate",
    )

    assert helpers_v2._act_wait_for_oracle_menu_trigger_option_visibility(
        page,
        option_candidates,
        "Invoice Actions",
    ) is True
    assert ("menuitem", "Validate", True) in page.role_calls
    assert ("Validate", True) in page.text_calls


def test_value_matches_requires_non_empty_observed_value() -> None:
    assert helpers_v2._act_value_matches("Project Manager", "") is False


def test_value_matches_requires_empty_observed_value_for_empty_expected() -> None:
    assert helpers_v2._act_value_matches("", "1") is False
    assert helpers_v2._act_value_matches("", "") is True


def test_check_target_marks_checkbox_checked_via_raw_check(monkeypatch) -> None:
    locator = _CheckboxLocator(checked=False)
    page = _NavigationPage()
    waits: list[str] = []

    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_field_processing",
        lambda *args, **kwargs: waits.append("done"),
    )

    helpers_v2._act_check_target(locator, page, "Create a job application on")

    assert locator.checked is True
    assert ("check", 3000) in locator.events
    assert waits == ["done"]


def test_check_target_falls_back_to_click_when_raw_check_is_unsupported(monkeypatch) -> None:
    locator = _CheckboxLocator(checked=False, check_raises=True)
    page = _NavigationPage()
    waits: list[str] = []

    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_field_processing",
        lambda *args, **kwargs: waits.append("done"),
    )

    helpers_v2._act_check_target(locator, page, "Create a job application on")

    assert locator.checked is True
    assert ("check", 3000) in locator.events
    assert ("click", 3000) in locator.events
    assert waits == ["done", "done"]


def test_checkbox_semantic_postcondition_accepts_matching_active_checkbox_after_rerender() -> None:
    before = {
        "target_meta": {
            "tag": "input",
            "id": "ui-id-427|cb",
            "name": "ui-id-426",
        }
    }
    after = {
        "target_meta": {},
        "active_element": {
            "tag": "input",
            "id": "ui-id-427|cb",
            "name": "ui-id-426",
            "checked": "true",
        },
    }

    assert helpers_v2._act_checkbox_semantic_postcondition(before, after, True) is True
    assert helpers_v2._act_checkbox_semantic_postcondition(before, after, False) is False


def test_set_checkbox_state_accepts_matching_active_checkbox_when_locator_rerenders(monkeypatch) -> None:
    locator = _CheckboxLocator(checked=False)
    page = _NavigationPage()
    waits: list[str] = []
    click_attempts: list[int] = []
    observe_calls = iter(
        [
            {
                "target_meta": {
                    "tag": "input",
                    "id": "ui-id-427|cb",
                    "name": "ui-id-426",
                },
                "active_element": {},
            },
            {
                "target_meta": {},
                "active_element": {
                    "tag": "input",
                    "id": "ui-id-427|cb",
                    "name": "ui-id-426",
                    "checked": "true",
                },
            },
        ]
    )

    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_field_processing",
        lambda *args, **kwargs: waits.append("done"),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_checkbox_matches",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_observe",
        lambda *args, **kwargs: next(observe_calls),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_strict_click",
        lambda _locator, timeout_ms=None: click_attempts.append(int(timeout_ms or 0)),
    )

    helpers_v2._act_set_checkbox_state(locator, page, "Items", True)

    assert ("check", 3000) in locator.events
    assert click_attempts == []
    assert waits == ["done"]


def test_combobox_open_postcondition_accepts_aria_expanded_transition() -> None:
    assert helpers_v2._act_combobox_open_postcondition(
        {"dialog_count": 0, "target_meta": {"aria_expanded": "false"}},
        {"dialog_count": 0, "target_meta": {"aria_expanded": "true"}},
    )


def test_select_combobox_option_waits_for_processing_after_success(monkeypatch) -> None:
    trigger = _FakeLocator(value="ES Annual Salary Basis")
    option = _FakeLocator(actionable=True)
    page = _OptionPage(option)
    waited: list[str] = []
    observations = iter(
        [
            {"dialog_count": 1, "body_marker": "before"},
            {"dialog_count": 0, "body_marker": "after"},
        ]
    )

    monkeypatch.setattr(helpers_v2, "_act_click_combobox", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_strict_click", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: waited.append("done"))
    monkeypatch.setattr(helpers_v2, "_act_combobox_trigger_reflects_option", lambda *args, **kwargs: True)

    helpers_v2._act_select_combobox_option(trigger, option, page, "Salary Basis", "ES Annual Salary Basis")

    assert waited == ["done"]


def test_select_combobox_option_retries_when_value_does_not_stick(monkeypatch) -> None:
    trigger = _FakeLocator(value="")
    option = _FakeLocator(actionable=True)
    page = _OptionPage(option)
    observations = iter(
        [
            {"dialog_count": 1, "body_marker": "before-1"},
            {"dialog_count": 0, "body_marker": "after-1"},
            {"dialog_count": 1, "body_marker": "before-2"},
            {"dialog_count": 0, "body_marker": "after-2"},
        ]
    )
    open_calls: list[str] = []
    processing_waits: list[str] = []
    click_count = 0

    def strict_click(*args, **kwargs):
        nonlocal click_count
        click_count += 1
        if click_count == 2:
            trigger._value = "Project Manager"

    monkeypatch.setattr(helpers_v2, "_act_click_combobox", lambda *args, **kwargs: open_calls.append("open"))
    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_strict_click", strict_click)
    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_field_processing",
        lambda *args, **kwargs: processing_waits.append("done"),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_combobox_trigger_reflects_option",
        lambda *args, **kwargs: click_count >= 2,
    )
    monkeypatch.setenv("ACT_COMBOBOX_VALUE_RETRY_COUNT", "1")

    helpers_v2._act_select_combobox_option(trigger, option, page, "Reporting Relationship", "Project Manager")

    assert click_count == 2
    assert open_calls == ["open", "open"]
    assert processing_waits == ["done", "done"]


def test_select_option_postcondition_accepts_matching_selected_value() -> None:
    assert helpers_v2._act_select_option_postcondition(
        {
            "value": "",
            "selected_values": [],
            "selected_labels": [],
            "selected_indexes": [],
        },
        {
            "value": "3",
            "selected_values": ["3"],
            "selected_labels": ["Credit Memo"],
            "selected_indexes": [2],
        },
        ["3"],
        {},
    )


def test_select_option_postcondition_fails_when_adf_commit_markers_reflect_old_value() -> None:
    # Transaction Class selectOneChoice: Playwright forced "Credit memo" into the
    # native <select>, but ADF never committed it -> title/_afov still "Invoice"/"0"
    # and the dependent header stays "Create Transaction: Invoice".
    before_state = {
        "value": "0",
        "selected_values": ["0"],
        "selected_labels": ["Invoice"],
        "selected_indexes": [0],
        "title": "Invoice",
        "afov": "0",
        "is_adf": True,
    }
    after_state = {
        "value": "1",
        "selected_values": ["1"],
        "selected_labels": ["Credit memo"],
        "selected_indexes": [1],
        "title": "Invoice",
        "afov": "0",
        "is_adf": True,
    }

    assert (
        helpers_v2._act_select_option_postcondition(before_state, after_state, ["1"], {})
        is False
    )


def test_select_option_postcondition_accepts_adf_commit_when_title_reflects_selection() -> None:
    before_state = {
        "value": "0",
        "selected_values": ["0"],
        "selected_labels": ["Invoice"],
        "selected_indexes": [0],
        "title": "Invoice",
        "afov": "0",
        "is_adf": True,
    }
    after_state = {
        "value": "1",
        "selected_values": ["1"],
        "selected_labels": ["Credit memo"],
        "selected_indexes": [1],
        "title": "Credit memo",
        "afov": "1",
        "is_adf": True,
    }

    assert helpers_v2._act_select_option_postcondition(before_state, after_state, ["1"], {})


def test_adf_select_commit_gate_ignores_plain_html_select_without_markers() -> None:
    # Plain HTML <select> (not ADF): no is_adf flag, so the existing DOM-state
    # postcondition stands and the change is accepted.
    after_state = {
        "value": "1",
        "selected_values": ["1"],
        "selected_labels": ["Credit memo"],
        "selected_indexes": [1],
    }

    assert helpers_v2._act_adf_select_commit_contradicted(after_state) is False
    assert helpers_v2._act_select_option_postcondition(
        {"value": "0", "selected_values": ["0"], "selected_labels": ["Invoice"], "selected_indexes": [0]},
        after_state,
        ["1"],
        {},
    )


def test_select_option_target_waits_for_processing_after_success(monkeypatch) -> None:
    locator = object()
    page = _NavigationPage()
    waited: list[str] = []
    states = iter(
        [
            {
                "value": "",
                "selected_values": [],
                "selected_labels": [],
                "selected_indexes": [],
            },
            {
                "value": "3",
                "selected_values": ["3"],
                "selected_labels": ["Credit Memo"],
                "selected_indexes": [2],
            },
        ]
    )

    monkeypatch.setattr(helpers_v2, "_act_apply_select_option", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_select_option_state", lambda *args, **kwargs: next(states))
    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_field_processing",
        lambda *args, **kwargs: waited.append("done"),
    )
    monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", lambda *args, **kwargs: [])
    monkeypatch.setattr(helpers_v2, "_act_ai_repair_locators", lambda *args, **kwargs: [])

    helpers_v2._act_select_option_target(locator, page, "Type", ["3"], {})

    assert waited == ["done"]


def test_wait_for_select_target_enabled_classifies_enabled_disabled_absent(monkeypatch) -> None:
    """The bounded enable-wait returns enabled (visible+enabled), disabled (visible but stays
    disabled past the budget), or absent (can't introspect -> let the normal path run)."""
    monkeypatch.setattr(helpers_v2, "_act_wait_ms", lambda name, default: default)

    monkeypatch.setattr(
        helpers_v2, "_act_safe_locator_eval", lambda loc, expr: {"visible": True, "disabled": False}
    )
    assert (
        helpers_v2._act_wait_for_select_target_enabled(
            object(), _NavigationPage(), env_name="X", default_ms=1000
        )
        == "enabled"
    )

    monkeypatch.setattr(
        helpers_v2, "_act_safe_locator_eval", lambda loc, expr: {"visible": True, "disabled": True}
    )
    page = _NavigationPage()
    assert (
        helpers_v2._act_wait_for_select_target_enabled(
            object(), page, env_name="X", default_ms=500
        )
        == "disabled"
    )
    assert page.waits  # it polled within the bounded window rather than returning instantly

    monkeypatch.setattr(helpers_v2, "_act_safe_locator_eval", lambda loc, expr: None)
    assert (
        helpers_v2._act_wait_for_select_target_enabled(
            object(), _NavigationPage(), env_name="X", default_ms=1000
        )
        == "absent"
    )


def test_select_option_target_fast_fails_on_disabled_without_ai(monkeypatch) -> None:
    """A disabled dependent select (e.g. Requisitioning BU off Procurement BU) fails fast with a
    real reason -- it must not call select_option, experience, or AI self-repair."""
    calls = {"apply": 0, "experience": 0}
    monkeypatch.setattr(helpers_v2, "_act_select_option_state", lambda *a, **k: {})
    monkeypatch.setattr(helpers_v2, "_act_resolve_select_target", lambda *a, **k: {})
    monkeypatch.setattr(
        helpers_v2, "_act_wait_for_select_target_enabled", lambda *a, **k: "disabled"
    )

    def _apply(*_a, **_k):
        calls["apply"] += 1

    monkeypatch.setattr(helpers_v2, "_act_apply_select_option", _apply)

    def _experience(*_a, **_k):
        calls["experience"] += 1
        return []

    monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", _experience)

    def _no_ai(**_k):
        raise AssertionError("AI self-repair must not run on a disabled target")

    monkeypatch.setattr(helpers_v2, "_act_execute_ai_repair_rounds", _no_ai)

    with pytest.raises(RuntimeError) as excinfo:
        helpers_v2._act_select_option_target(
            object(), _NavigationPage(), "Requisitioning BU", ["105 Tacoma BU"], {}
        )
    assert "disabled" in str(excinfo.value).lower()
    assert calls == {"apply": 0, "experience": 0}


def test_select_option_target_skips_disabled_when_value_already_set(monkeypatch) -> None:
    """An auto-derived DISABLED field that already holds the requested value is a no-op success --
    the recording does not have to drop the step, and no select/AI is attempted."""
    recovery: dict = {}
    monkeypatch.setattr(
        helpers_v2,
        "_act_select_option_state",
        lambda *a, **k: {
            "value": "105 Tacoma BU",
            "selected_values": ["105 Tacoma BU"],
            "selected_labels": ["105 Tacoma BU"],
            "selected_indexes": [0],
        },
    )
    monkeypatch.setattr(helpers_v2, "_act_resolve_select_target", lambda *a, **k: {})
    monkeypatch.setattr(
        helpers_v2, "_act_wait_for_select_target_enabled", lambda *a, **k: "disabled"
    )

    def _no_apply(*_a, **_k):
        raise AssertionError("must not select a disabled field whose value already matches")

    monkeypatch.setattr(helpers_v2, "_act_apply_select_option", _no_apply)
    monkeypatch.setattr(
        helpers_v2, "_act_set_recovery_record", lambda *a, **k: recovery.update({"args": a})
    )
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **k: None)

    # Must NOT raise -- it skips as a no-op success.
    helpers_v2._act_select_option_target(
        object(), _NavigationPage(), "Requisitioning BU", ["105 Tacoma BU"], {}
    )
    assert "disabled_target_value_already_set" in recovery.get("args", ())


def test_select_option_target_blurs_after_commit_to_fire_dependent_cascade(monkeypatch) -> None:
    """After a select commits, the runner blurs the field so ADF fires its dependent-enabling PPR
    (a programmatic select_option commits the value but never blurs, leaving a downstream LOV like
    Supplier disabled). Controlled by ACT_SELECT_BLUR_COMMIT."""
    locator = object()
    blurred: list = []
    committed = {
        "value": "3",
        "selected_values": ["3"],
        "selected_labels": ["X"],
        "selected_indexes": [2],
    }
    monkeypatch.setattr(helpers_v2, "_act_apply_select_option", lambda *a, **k: None)
    monkeypatch.setattr(helpers_v2, "_act_select_option_state", lambda *a, **k: committed)
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *a, **k: None)
    monkeypatch.setattr(
        helpers_v2, "_act_wait_for_select_target_enabled", lambda *a, **k: "enabled"
    )
    monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", lambda *a, **k: [])
    monkeypatch.setattr(
        helpers_v2, "_act_commit_select_blur", lambda loc: bool(blurred.append(loc)) or True
    )

    monkeypatch.setenv("ACT_SELECT_BLUR_COMMIT", "true")
    helpers_v2._act_select_option_target(locator, _NavigationPage(), "Type", ["3"], {})
    assert blurred == [locator]

    blurred.clear()
    monkeypatch.setenv("ACT_SELECT_BLUR_COMMIT", "false")
    helpers_v2._act_select_option_target(locator, _NavigationPage(), "Type", ["3"], {})
    assert blurred == []


def test_try_commit_oracle_adf_select_uses_keyboard_commit(monkeypatch) -> None:
    locator = _OracleKeyboardSelectLocator()
    page = _NavigationPage()
    waited: list[str] = []
    states = iter(
        [
            {
                "value": "1",
                "selected_values": ["1"],
                "selected_labels": ["Credit memo"],
                "selected_indexes": [1],
                "title": "Invoice",
                "afov": "0",
                "is_adf": True,
            },
            {
                "value": "1",
                "selected_values": ["1"],
                "selected_labels": ["Credit memo"],
                "selected_indexes": [1],
                "title": "Credit memo",
                "afov": "1",
                "is_adf": True,
            },
        ]
    )

    monkeypatch.setattr(helpers_v2, "_act_select_option_state", lambda *args, **kwargs: next(states))
    monkeypatch.setattr(
        helpers_v2,
        "_act_resolve_select_target",
        lambda *args, **kwargs: {"index": 1, "value": "1", "label": "Credit memo"},
    )
    monkeypatch.setattr(helpers_v2, "_act_reset_select_to_index", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_field_processing",
        lambda *args, **kwargs: waited.append("done"),
    )

    details = helpers_v2._act_try_commit_oracle_adf_select(
        locator,
        page,
        {
            "value": "0",
            "selected_values": ["0"],
            "selected_labels": ["Invoice"],
            "selected_indexes": [0],
            "title": "Invoice",
            "afov": "0",
            "is_adf": True,
        },
        ["1"],
        {},
    )

    assert details == {
        "strategy_name": "oracle_adf_select_keyboard_commit",
        "target_index": 1,
        "target_value": "1",
        "target_label": "Credit memo",
    }
    assert locator.focused is True
    assert locator.events == [("focus", 3000), ("press", "ArrowDown"), ("press", "Tab")]
    assert waited == ["done"]
    assert page.waits == [250]


def test_try_commit_oracle_adf_select_infers_previous_committed_index_from_adf_markers(monkeypatch) -> None:
    locator = _OracleKeyboardSelectLocator()
    page = _NavigationPage()
    waited: list[str] = []
    states = iter(
        [
            {
                "value": "2",
                "selected_values": ["2"],
                "selected_labels": ["Receipts"],
                "selected_indexes": [2],
                "title": "Inventory",
                "afov": "0",
                "is_adf": True,
            },
            {
                "value": "2",
                "selected_values": ["2"],
                "selected_labels": ["Receipts"],
                "selected_indexes": [2],
                "title": "Receipts",
                "afov": "2",
                "is_adf": True,
            },
        ]
    )

    monkeypatch.setattr(helpers_v2, "_act_select_option_state", lambda *args, **kwargs: next(states))
    monkeypatch.setattr(
        helpers_v2,
        "_act_resolve_select_target",
        lambda *args, **kwargs: {"index": 2, "value": "2", "label": "Receipts"},
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_resolve_select_option_by_committed_marker",
        lambda *args, **kwargs: {"index": 0, "value": "0", "label": "Inventory"},
    )
    monkeypatch.setattr(helpers_v2, "_act_reset_select_to_index", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_field_processing",
        lambda *args, **kwargs: waited.append("done"),
    )

    details = helpers_v2._act_try_commit_oracle_adf_select(
        locator,
        page,
        {},
        ["2"],
        {},
    )

    assert details == {
        "strategy_name": "oracle_adf_select_keyboard_commit",
        "target_index": 2,
        "target_value": "2",
        "target_label": "Receipts",
        "committed_index_source": "adf_marker",
        "committed_value": "0",
        "committed_label": "Inventory",
    }
    assert locator.focused is True
    assert locator.events == [("focus", 3000), ("press", "ArrowDown"), ("press", "ArrowDown"), ("press", "Tab")]
    assert waited == ["done"]
    assert page.waits == [250]


def test_try_commit_oracle_adf_select_uses_contradicted_state_as_target_when_initial_target_missing(monkeypatch) -> None:
    locator = _OracleKeyboardSelectLocator()
    page = _NavigationPage()
    waited: list[str] = []
    states = iter(
        [
            {
                "value": "2",
                "selected_values": ["2"],
                "selected_labels": ["Receipts"],
                "selected_indexes": [2],
                "title": "Inventory",
                "afov": "0",
                "is_adf": True,
            },
            {
                "value": "2",
                "selected_values": ["2"],
                "selected_labels": ["Receipts"],
                "selected_indexes": [2],
                "title": "Receipts",
                "afov": "2",
                "is_adf": True,
            },
        ]
    )

    monkeypatch.setattr(helpers_v2, "_act_select_option_state", lambda *args, **kwargs: next(states))
    monkeypatch.setattr(helpers_v2, "_act_resolve_select_target", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        helpers_v2,
        "_act_resolve_select_option_by_committed_marker",
        lambda *args, **kwargs: {"index": 0, "value": "0", "label": "Inventory"},
    )
    monkeypatch.setattr(helpers_v2, "_act_reset_select_to_index", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_field_processing",
        lambda *args, **kwargs: waited.append("done"),
    )

    details = helpers_v2._act_try_commit_oracle_adf_select(
        locator,
        page,
        {},
        ["2"],
        {},
    )

    assert details == {
        "strategy_name": "oracle_adf_select_keyboard_commit",
        "target_index": 2,
        "target_value": "2",
        "target_label": "Receipts",
        "committed_index_source": "adf_marker",
        "committed_value": "0",
        "committed_label": "Inventory",
    }
    assert locator.focused is True
    assert locator.events == [("focus", 3000), ("press", "ArrowDown"), ("press", "ArrowDown"), ("press", "Tab")]
    assert waited == ["done"]
    assert page.waits == [250]


def test_try_oracle_adf_component_commit_returns_component_details(monkeypatch) -> None:
    locator = object()

    monkeypatch.setattr(
        helpers_v2,
        "_act_safe_locator_eval",
        lambda *args, **kwargs: {
            "ok": True,
            "base_id": "_FOpt1:_FOr1:0:_FONSr2:0:_FOTRaT:0:soc1",
            "content_id": "_FOpt1:_FOr1:0:_FONSr2:0:_FOTRaT:0:soc1::content",
            "used": ["focus", "selectedIndex", "component.setValue", "component.processUpdates"],
        },
    )

    details = helpers_v2._act_try_oracle_adf_component_commit(
        locator,
        {"index": 2, "value": "2", "label": "Receipts"},
    )

    assert details == {
        "strategy_name": "oracle_adf_select_component_commit",
        "component_id": "_FOpt1:_FOr1:0:_FONSr2:0:_FOTRaT:0:soc1",
        "content_id": "_FOpt1:_FOr1:0:_FONSr2:0:_FOTRaT:0:soc1::content",
        "used": ["focus", "selectedIndex", "component.setValue", "component.processUpdates"],
        "target_index": 2,
        "target_value": "2",
        "target_label": "Receipts",
    }


def test_try_commit_oracle_adf_select_falls_back_to_component_commit(monkeypatch) -> None:
    locator = _OracleKeyboardSelectLocator()
    page = _NavigationPage()
    waited: list[str] = []
    states = iter(
        [
            {
                "value": "2",
                "selected_values": ["2"],
                "selected_labels": ["Receipts"],
                "selected_indexes": [2],
                "title": "Inventory",
                "afov": "0",
                "is_adf": True,
            },
            {
                "value": "2",
                "selected_values": ["2"],
                "selected_labels": ["Receipts"],
                "selected_indexes": [2],
                "title": "Inventory",
                "afov": "0",
                "is_adf": True,
            },
            {
                "value": "2",
                "selected_values": ["2"],
                "selected_labels": ["Receipts"],
                "selected_indexes": [2],
                "title": "Receipts",
                "afov": "2",
                "is_adf": True,
            },
        ]
    )

    monkeypatch.setattr(helpers_v2, "_act_select_option_state", lambda *args, **kwargs: next(states))
    monkeypatch.setattr(helpers_v2, "_act_resolve_select_target", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        helpers_v2,
        "_act_resolve_select_option_by_committed_marker",
        lambda *args, **kwargs: {"index": 0, "value": "0", "label": "Inventory"},
    )
    monkeypatch.setattr(helpers_v2, "_act_reset_select_to_index", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_field_processing",
        lambda *args, **kwargs: waited.append("done"),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_try_oracle_adf_component_commit",
        lambda *args, **kwargs: {
            "strategy_name": "oracle_adf_select_component_commit",
            "component_id": "_FOpt1:_FOr1:0:_FONSr2:0:_FOTRaT:0:soc1",
            "content_id": "_FOpt1:_FOr1:0:_FONSr2:0:_FOTRaT:0:soc1::content",
            "used": ["focus", "selectedIndex", "component.setValue", "component.processUpdates"],
            "target_index": 2,
            "target_value": "2",
            "target_label": "Receipts",
        },
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_oracle_adf_commit_events",
        lambda *args, **kwargs: pytest.fail("plain event commit should not run after component commit succeeds"),
    )

    details = helpers_v2._act_try_commit_oracle_adf_select(
        locator,
        page,
        {},
        ["2"],
        {},
    )

    assert details == {
        "strategy_name": "oracle_adf_select_component_commit",
        "component_id": "_FOpt1:_FOr1:0:_FONSr2:0:_FOTRaT:0:soc1",
        "content_id": "_FOpt1:_FOr1:0:_FONSr2:0:_FOTRaT:0:soc1::content",
        "used": ["focus", "selectedIndex", "component.setValue", "component.processUpdates"],
        "target_index": 2,
        "target_value": "2",
        "target_label": "Receipts",
        "committed_index_source": "adf_marker",
        "committed_value": "0",
        "committed_label": "Inventory",
    }
    assert locator.focused is True
    assert locator.events == [("focus", 3000), ("press", "ArrowDown"), ("press", "ArrowDown"), ("press", "Tab")]
    assert waited == ["done", "done"]
    assert page.waits == [250, 250]


def test_select_option_target_uses_oracle_adf_commit_before_ai(monkeypatch) -> None:
    locator = object()
    page = _NavigationPage()
    recovery: dict[str, object] = {}
    stored: dict[str, object] = {}
    states = iter(
        [
            {
                "value": "0",
                "selected_values": ["0"],
                "selected_labels": ["Invoice"],
                "selected_indexes": [0],
                "title": "Invoice",
                "afov": "0",
                "is_adf": True,
            },
            {
                "value": "1",
                "selected_values": ["1"],
                "selected_labels": ["Credit memo"],
                "selected_indexes": [1],
                "title": "Invoice",
                "afov": "0",
                "is_adf": True,
            },
        ]
    )

    monkeypatch.setattr(helpers_v2, "_act_apply_select_option", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_select_option_state", lambda *args, **kwargs: next(states))
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_try_commit_oracle_adf_select",
        lambda *args, **kwargs: {
            "strategy_name": "oracle_adf_select_keyboard_commit",
            "target_index": 1,
            "target_value": "1",
            "target_label": "Credit memo",
        },
    )
    monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", lambda *args, **kwargs: pytest.fail("experience should not run"))
    monkeypatch.setattr(helpers_v2, "_act_ai_repair_locators", lambda *args, **kwargs: pytest.fail("ai should not run"))
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: stored.update(kwargs))
    monkeypatch.setattr(
        helpers_v2,
        "_act_set_recovery_record",
        lambda source, kind, handler_name, details=None: recovery.update(
            {"source": source, "kind": kind, "handler_name": handler_name, "details": details or {}}
        ),
    )

    helpers_v2._act_select_option_target(locator, page, "Transaction Class", ["1"], {})

    assert recovery == {
        "source": "oracle_handler",
        "kind": "oracle_adf_select_commit",
        "handler_name": "oracle_adf_select_commit",
        "details": {
            "strategy_name": "oracle_adf_select_keyboard_commit",
            "target_index": 1,
            "target_value": "1",
            "target_label": "Credit memo",
        },
    }
    assert stored["status"] == "success"
    assert stored["postcondition_kind"] == "option_selected"


def test_select_option_target_does_not_false_pass_on_semantic_match_when_adf_markers_contradict(monkeypatch) -> None:
    locator = object()
    page = _NavigationPage()
    recovery: dict[str, object] = {}
    stored: dict[str, object] = {}
    states = iter(
        [
            {},
            {
                "value": "2",
                "selected_values": ["2"],
                "selected_labels": ["Receipts"],
                "selected_indexes": [2],
                "text": "Inventory Counts Receipts",
                "title": "Inventory",
                "afov": "0",
                "is_adf": True,
            },
        ]
    )

    monkeypatch.setattr(helpers_v2, "_act_apply_select_option", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_select_option_state", lambda *args, **kwargs: next(states))
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_resolve_select_target",
        lambda *args, **kwargs: {"index": 2, "value": "2", "label": "Receipts"},
    )
    monkeypatch.setattr(helpers_v2, "_act_oracle_label_value_matches", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        helpers_v2,
        "_act_try_commit_oracle_adf_select",
        lambda *args, **kwargs: {
            "strategy_name": "oracle_adf_select_keyboard_commit",
            "target_index": 2,
            "target_value": "2",
            "target_label": "Receipts",
            "committed_index_source": "adf_marker",
            "committed_value": "0",
            "committed_label": "Inventory",
        },
    )
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_searchselect_select_option_recovery", lambda *args, **kwargs: pytest.fail("searchselect recovery should not run"))
    monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", lambda *args, **kwargs: pytest.fail("experience should not run"))
    monkeypatch.setattr(helpers_v2, "_act_ai_repair_locators", lambda *args, **kwargs: pytest.fail("ai should not run"))
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: stored.update(kwargs))
    monkeypatch.setattr(
        helpers_v2,
        "_act_set_recovery_record",
        lambda source, kind, handler_name, details=None: recovery.update(
            {"source": source, "kind": kind, "handler_name": handler_name, "details": details or {}}
        ),
    )

    helpers_v2._act_select_option_target(locator, page, "Show Tasks", ["2"], {})

    assert recovery == {
        "source": "oracle_handler",
        "kind": "oracle_adf_select_commit",
        "handler_name": "oracle_adf_select_commit",
        "details": {
            "strategy_name": "oracle_adf_select_keyboard_commit",
            "target_index": 2,
            "target_value": "2",
            "target_label": "Receipts",
            "committed_index_source": "adf_marker",
            "committed_value": "0",
            "committed_label": "Inventory",
        },
    }
    assert stored["status"] == "success"
    assert stored["postcondition_kind"] == "option_selected"


def test_select_option_target_accepts_oracle_semantic_label_value_when_field_already_selected(monkeypatch) -> None:
    locator = object()
    page = _NavigationPage()
    recovery: dict[str, object] = {}
    stored: dict[str, object] = {}
    states = iter(
        [
            {
                "value": "",
                "selected_values": [],
                "selected_labels": [],
                "selected_indexes": [],
            },
            {
                "value": "",
                "selected_values": [],
                "selected_labels": [],
                "selected_indexes": [],
            },
        ]
    )

    monkeypatch.setattr(helpers_v2, "_act_apply_select_option", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_select_option_state", lambda *args, **kwargs: next(states))
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_resolve_select_target",
        lambda *args, **kwargs: {"index": 3, "value": "3", "label": "Test Solutions"},
    )
    monkeypatch.setattr(helpers_v2, "_act_oracle_label_value_matches", lambda *args, **kwargs: True)
    monkeypatch.setattr(helpers_v2, "_act_try_commit_oracle_adf_select", lambda *args, **kwargs: pytest.fail("adf commit should not run"))
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_searchselect_select_option_recovery", lambda *args, **kwargs: pytest.fail("searchselect recovery should not run"))
    monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", lambda *args, **kwargs: pytest.fail("experience should not run"))
    monkeypatch.setattr(helpers_v2, "_act_ai_repair_locators", lambda *args, **kwargs: pytest.fail("ai should not run"))
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: stored.update(kwargs))
    monkeypatch.setattr(
        helpers_v2,
        "_act_set_recovery_record",
        lambda source, kind, handler_name, details=None: recovery.update(
            {"source": source, "kind": kind, "handler_name": handler_name, "details": details or {}}
        ),
    )

    helpers_v2._act_select_option_target(locator, page, "Business Unit", ["3"], {})

    assert recovery == {
        "source": "oracle_handler",
        "kind": "oracle_label_value_already_selected",
        "handler_name": "oracle_label_value_already_selected",
        "details": {
            "target_value": "3",
            "target_label": "Test Solutions",
        },
    }
    assert stored["status"] == "success"
    assert stored["postcondition_kind"] == "option_selected"


def test_resolve_select_target_maps_oracle_one_based_value_to_zero_based_option(monkeypatch) -> None:
    monkeypatch.setattr(
        helpers_v2,
        "_act_safe_locator_eval",
        lambda *args, **kwargs: {
            "is_adf": True,
            "options": [
                {"index": 0, "value": "0", "label": "Connectors"},
                {"index": 1, "value": "1", "label": "Corporate"},
                {"index": 2, "value": "2", "label": "Test Solutions"},
            ],
        },
    )

    assert helpers_v2._act_resolve_select_target(object(), ["3"], {}) == {
        "index": 2,
        "value": "2",
        "label": "Test Solutions",
    }


def test_select_option_target_reuses_precomputed_target_when_control_goes_stale(monkeypatch) -> None:
    locator = object()
    page = _NavigationPage()
    recovery: dict[str, object] = {}
    stored: dict[str, object] = {}
    states = iter(
        [
            {
                "value": "0",
                "selected_values": ["0"],
                "selected_labels": [],
                "selected_indexes": [0],
                "text": "Connectors Corporate Test Solutions",
                "title": "",
                "afov": "0",
                "is_adf": True,
            },
            {},
        ]
    )
    resolve_calls = {"count": 0}

    def _resolve_once(*args, **kwargs):
        resolve_calls["count"] += 1
        if resolve_calls["count"] > 1:
            pytest.fail("target should be reused after the control goes stale")
        return {"index": 2, "value": "2", "label": "Test Solutions"}

    monkeypatch.setattr(helpers_v2, "_act_apply_select_option", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_select_option_state", lambda *args, **kwargs: next(states))
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_resolve_select_target", _resolve_once)
    monkeypatch.setattr(helpers_v2, "_act_oracle_label_value_matches", lambda *args, **kwargs: True)
    monkeypatch.setattr(helpers_v2, "_act_try_commit_oracle_adf_select", lambda *args, **kwargs: pytest.fail("adf commit should not run"))
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_searchselect_select_option_recovery", lambda *args, **kwargs: pytest.fail("searchselect recovery should not run"))
    monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", lambda *args, **kwargs: pytest.fail("experience should not run"))
    monkeypatch.setattr(helpers_v2, "_act_ai_repair_locators", lambda *args, **kwargs: pytest.fail("ai should not run"))
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: stored.update(kwargs))
    monkeypatch.setattr(
        helpers_v2,
        "_act_set_recovery_record",
        lambda source, kind, handler_name, details=None: recovery.update(
            {"source": source, "kind": kind, "handler_name": handler_name, "details": details or {}}
        ),
    )

    helpers_v2._act_select_option_target(locator, page, "Business Unit", ["3"], {})

    assert resolve_calls["count"] == 1
    assert recovery == {
        "source": "oracle_handler",
        "kind": "oracle_label_value_already_selected",
        "handler_name": "oracle_label_value_already_selected",
        "details": {
            "target_value": "2",
            "target_label": "Test Solutions",
        },
    }
    assert stored["status"] == "success"
    assert stored["postcondition_kind"] == "option_selected"


def test_try_oracle_searchselect_select_option_recovery_uses_resolved_target_label(monkeypatch) -> None:
    locator = object()
    page = _SearchOptionPage()
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        helpers_v2,
        "_act_extract_locator_metadata",
        lambda current: {"tag": "select", "role": "", "oracle_host_tag": ""},
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_resolve_select_target",
        lambda *args, **kwargs: {"index": 3, "value": "3", "label": "Vision Operations"},
    )
    monkeypatch.setattr(helpers_v2, "_act_ai_locator_matches_label", lambda locator, label: True)
    monkeypatch.setattr(
        helpers_v2,
        "_act_select_search_trigger_option",
        lambda trigger, option, current_page, title, option_name, **kwargs: captured.update(
            {
                "trigger": getattr(trigger, "name", ""),
                "option": getattr(option, "name", ""),
                "title": title,
                "option_name": option_name,
                "kwargs": kwargs,
            }
        ),
    )

    details = helpers_v2._act_try_oracle_searchselect_select_option_recovery(
        locator,
        page,
        "Business Unit",
        ["3"],
        {},
    )

    assert details == {
        "strategy_name": "oracle_searchselect_role_combobox",
        "target_index": 3,
        "target_value": "3",
        "target_label": "Vision Operations",
        "option_name": "Vision Operations",
        "fill_value": "Vision Operations",
    }
    assert captured["trigger"] == "combobox:Business Unit"
    assert captured["option"] == "text:Vision Operations:True"
    assert captured["title"] == "Business Unit"
    assert captured["option_name"] == "Vision Operations"
    assert captured["kwargs"] == {
        "option_exact": True,
        "fill_value": "Vision Operations",
        "allow_repair": False,
    }


def test_select_option_target_uses_oracle_searchselect_recovery_before_experience_ai(monkeypatch) -> None:
    locator = object()
    page = _NavigationPage()
    recovery: dict[str, object] = {}
    stored: dict[str, object] = {}
    states = iter(
        [
            {
                "value": "1",
                "selected_values": ["1"],
                "selected_labels": ["Operations"],
                "selected_indexes": [1],
            },
            {
                "value": "1",
                "selected_values": ["1"],
                "selected_labels": ["Operations"],
                "selected_indexes": [1],
            },
        ]
    )

    monkeypatch.setattr(helpers_v2, "_act_apply_select_option", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_select_option_state", lambda *args, **kwargs: next(states))
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_try_commit_oracle_adf_select", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_try_oracle_searchselect_select_option_recovery",
        lambda *args, **kwargs: {
            "strategy_name": "oracle_searchselect_role_combobox",
            "target_index": 3,
            "target_value": "3",
            "target_label": "Vision Operations",
            "option_name": "Vision Operations",
            "fill_value": "Vision Operations",
        },
    )
    monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", lambda *args, **kwargs: pytest.fail("experience should not run"))
    monkeypatch.setattr(helpers_v2, "_act_ai_repair_locators", lambda *args, **kwargs: pytest.fail("ai should not run"))
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: stored.update(kwargs))
    monkeypatch.setattr(
        helpers_v2,
        "_act_set_recovery_record",
        lambda source, kind, handler_name, details=None: recovery.update(
            {"source": source, "kind": kind, "handler_name": handler_name, "details": details or {}}
        ),
    )

    helpers_v2._act_select_option_target(locator, page, "Business Unit", ["3"], {})

    assert recovery == {
        "source": "oracle_handler",
        "kind": "oracle_searchselect_select_option",
        "handler_name": "oracle_searchselect_select_option",
        "details": {
            "strategy_name": "oracle_searchselect_role_combobox",
            "target_index": 3,
            "target_value": "3",
            "target_label": "Vision Operations",
            "option_name": "Vision Operations",
            "fill_value": "Vision Operations",
        },
    }
    assert stored["status"] == "success"
    assert stored["postcondition_kind"] == "option_selected"


def test_fill_textbox_waits_for_processing_before_validating(monkeypatch) -> None:
    locator = object()
    page = _NavigationPage()
    waited: list[str] = []

    monkeypatch.setattr(helpers_v2, "_act_strict_fill", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_field_processing",
        lambda *args, **kwargs: waited.append("done"),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_locator_value",
        lambda current_locator: "-10.00" if waited else "",
    )
    monkeypatch.setattr(helpers_v2, "_act_locator_text", lambda *args, **kwargs: "")

    helpers_v2._act_fill_textbox(locator, page, "Amount", "-10")

    assert waited == ["done"]


def test_fill_textbox_uses_oracle_table_active_editor_when_row_scoped_locator_does_not_reflect_value(monkeypatch) -> None:
    locator = object()
    table_editor = _OracleTableEditorLocator()
    page = _NavigationPage()
    waited: list[str] = []
    captured: dict[str, object] = {}
    previous_script_data = helpers_v2._act_clone_json_value(helpers_v2._ACT_SCRIPT_DATA)
    previous_strategy = helpers_v2._act_clone_json_value(helpers_v2._ACT_CURRENT_STRATEGY)
    try:
        helpers_v2._act_set_script_data(
            {
                "tracked_action": "fill_textbox",
                "parsed_action": {
                    "type": "fill",
                    "locator_steps": [
                        {"method": "get_by_role", "args": ["row"], "kwargs": {"name": "1 Item Type Amount"}},
                        {"method": "get_by_label", "args": ["Amount"]},
                    ],
                },
                "primary_locator_steps": [
                    {"method": "get_by_role", "args": ["row"], "kwargs": {"name": "1 Item Type Amount"}},
                    {"method": "get_by_label", "args": ["Amount"]},
                ],
            }
        )
        helpers_v2._act_reset_strategy_tracking("fill_textbox", "Amount")

        monkeypatch.setattr(helpers_v2, "_act_strict_fill", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            helpers_v2,
            "_act_wait_for_field_processing",
            lambda *args, **kwargs: waited.append("done"),
        )
        monkeypatch.setattr(
            helpers_v2,
            "_act_active_oracle_table_editor_locator",
            lambda current_page: (
                {
                    "id": "oracle-table-editor",
                    "table_id": "invoice-lines-table",
                    "row_text": "1 Item Type Amount",
                },
                table_editor,
            ),
        )
        monkeypatch.setattr(
            helpers_v2,
            "_act_locator_value",
            lambda current_locator: (
                table_editor.current_value if current_locator is table_editor else ""
            ),
        )
        monkeypatch.setattr(helpers_v2, "_act_locator_text", lambda *args, **kwargs: "")
        monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", lambda *args, **kwargs: [])
        monkeypatch.setattr(helpers_v2, "_act_ai_repair_locators", lambda *args, **kwargs: [])
        monkeypatch.setattr(
            helpers_v2,
            "_act_store_experience_episode",
            lambda **kwargs: captured.update(kwargs),
        )

        helpers_v2._act_fill_textbox(locator, page, "Amount", "-10")

        assert ("press", "ControlOrMeta+A", 3000) in table_editor.events
        assert ("press", "Backspace", 3000) in table_editor.events
        assert ("press_sequentially", "-10", 40, 3000) in table_editor.events
        assert waited == ["done", "done"]
        assert helpers_v2._ACT_CURRENT_STRATEGY["recovery"]["handler_name"] == "oracle_table_active_editor_fill"
        debug_trace = helpers_v2._ACT_CURRENT_STRATEGY["debug"]["fill_textbox"]
        assert debug_trace["oracle_table_active_editor_fill"]["status"] == "validated"
        assert debug_trace["resolved_by"] == "oracle_table_active_editor_fill"
        assert captured["status"] == "success"
        assert captured["postcondition_passed"] is True
    finally:
        helpers_v2._act_set_script_data(previous_script_data)
        helpers_v2._ACT_CURRENT_STRATEGY.clear()
        helpers_v2._ACT_CURRENT_STRATEGY.update(previous_strategy)


def test_locator_value_reads_nested_spinbutton_value_from_oracle_host() -> None:
    assert helpers_v2._act_locator_value(_NestedValueLocator()) == "1"


def test_fill_textbox_uses_oracle_spinbutton_keyboard_fill_when_direct_fill_does_not_reflect_value(monkeypatch) -> None:
    locator = object()
    spinbutton = _OracleTableEditorLocator()
    page = _NavigationPage()
    waited: list[str] = []
    captured: dict[str, object] = {}
    previous_script_data = helpers_v2._act_clone_json_value(helpers_v2._ACT_SCRIPT_DATA)
    previous_strategy = helpers_v2._act_clone_json_value(helpers_v2._ACT_CURRENT_STRATEGY)
    try:
        helpers_v2._act_set_script_data(
            {
                "tracked_action": "fill_textbox",
                "parsed_action": {
                    "type": "fill",
                    "locator_steps": [
                        {"method": "get_by_role", "args": ["spinbutton"], "kwargs": {"name": "Picked Quantity"}},
                    ],
                },
                "primary_locator_steps": [
                    {"method": "get_by_role", "args": ["spinbutton"], "kwargs": {"name": "Picked Quantity"}},
                ],
            }
        )
        helpers_v2._act_reset_strategy_tracking("fill_textbox", "Picked Quantity")

        monkeypatch.setattr(helpers_v2, "_act_strict_fill", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            helpers_v2,
            "_act_wait_for_field_processing",
            lambda *args, **kwargs: waited.append("done"),
        )
        monkeypatch.setattr(
            helpers_v2,
            "_act_active_spinbutton_locator",
            lambda current_page: (
                {
                    "id": "ui-id-544|input",
                    "role": "spinbutton",
                },
                spinbutton,
            ),
        )
        monkeypatch.setattr(
            helpers_v2,
            "_act_locator_value",
            lambda current_locator: (
                spinbutton.current_value if current_locator is spinbutton else ""
            ),
        )
        monkeypatch.setattr(helpers_v2, "_act_locator_text", lambda *args, **kwargs: "")
        monkeypatch.setattr(helpers_v2, "_act_oracle_label_value_matches", lambda *args, **kwargs: False)
        monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", lambda *args, **kwargs: [])
        monkeypatch.setattr(helpers_v2, "_act_ai_repair_locators", lambda *args, **kwargs: [])
        monkeypatch.setattr(
            helpers_v2,
            "_act_store_experience_episode",
            lambda **kwargs: captured.update(kwargs),
        )

        helpers_v2._act_fill_textbox(locator, page, "Picked Quantity", "1")

        assert ("press", "ControlOrMeta+A", 3000) in spinbutton.events
        assert ("press", "Backspace", 3000) in spinbutton.events
        assert ("press_sequentially", "1", 40, 3000) in spinbutton.events
        assert ("press", "Tab", 3000) in spinbutton.events
        assert waited == ["done", "done"]
        assert helpers_v2._ACT_CURRENT_STRATEGY["recovery"]["handler_name"] == "oracle_spinbutton_fill"
        debug_trace = helpers_v2._ACT_CURRENT_STRATEGY["debug"]["fill_textbox"]
        assert debug_trace["oracle_spinbutton_fill"]["status"] == "validated"
        assert debug_trace["resolved_by"] == "oracle_spinbutton_fill"
        assert captured["status"] == "success"
        assert captured["postcondition_passed"] is True
    finally:
        helpers_v2._act_set_script_data(previous_script_data)
        helpers_v2._ACT_CURRENT_STRATEGY.clear()
        helpers_v2._ACT_CURRENT_STRATEGY.update(previous_strategy)


def test_fill_textbox_does_not_treat_preexisting_spinbutton_value_as_fill_success(monkeypatch) -> None:
    locator = object()
    spinbutton = _OracleTableEditorLocator()
    spinbutton.current_value = "1"
    page = _NavigationPage()
    waited: list[str] = []
    captured: dict[str, object] = {}
    previous_script_data = helpers_v2._act_clone_json_value(helpers_v2._ACT_SCRIPT_DATA)
    previous_strategy = helpers_v2._act_clone_json_value(helpers_v2._ACT_CURRENT_STRATEGY)
    try:
        helpers_v2._act_set_script_data(
            {
                "tracked_action": "fill_textbox",
                "parsed_action": {
                    "type": "fill",
                    "locator_steps": [
                        {"method": "get_by_role", "args": ["spinbutton"], "kwargs": {"name": "Picked Quantity"}},
                    ],
                },
                "primary_locator_steps": [
                    {"method": "get_by_role", "args": ["spinbutton"], "kwargs": {"name": "Picked Quantity"}},
                ],
            }
        )
        helpers_v2._act_reset_strategy_tracking("fill_textbox", "Picked Quantity")

        monkeypatch.setattr(helpers_v2, "_act_strict_fill", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            helpers_v2,
            "_act_wait_for_field_processing",
            lambda *args, **kwargs: waited.append("done"),
        )
        monkeypatch.setattr(
            helpers_v2,
            "_act_active_spinbutton_locator",
            lambda current_page: (
                {
                    "id": "ui-id-544|input",
                    "role": "spinbutton",
                },
                spinbutton,
            ),
        )
        monkeypatch.setattr(
            helpers_v2,
            "_act_locator_value",
            lambda current_locator: (
                spinbutton.current_value if current_locator is spinbutton else ""
            ),
        )
        monkeypatch.setattr(helpers_v2, "_act_locator_text", lambda *args, **kwargs: "")
        monkeypatch.setattr(helpers_v2, "_act_oracle_label_value_matches", lambda *args, **kwargs: False)
        monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", lambda *args, **kwargs: [])
        monkeypatch.setattr(helpers_v2, "_act_ai_repair_locators", lambda *args, **kwargs: [])
        monkeypatch.setattr(
            helpers_v2,
            "_act_store_experience_episode",
            lambda **kwargs: captured.update(kwargs),
        )

        helpers_v2._act_fill_textbox(locator, page, "Picked Quantity", "1")

        assert ("press", "ControlOrMeta+A", 3000) in spinbutton.events
        assert ("press", "Backspace", 3000) in spinbutton.events
        assert ("press_sequentially", "1", 40, 3000) in spinbutton.events
        assert ("press", "Tab", 3000) in spinbutton.events
        assert waited == ["done", "done"]
        assert helpers_v2._ACT_CURRENT_STRATEGY["strategy"] == "oracle_spinbutton_keyboard_fill"
        assert helpers_v2._ACT_CURRENT_STRATEGY["recovery"]["handler_name"] == "oracle_spinbutton_fill"
        debug_trace = helpers_v2._ACT_CURRENT_STRATEGY["debug"]["fill_textbox"]
        assert debug_trace["oracle_spinbutton_fill"]["status"] == "validated"
        assert debug_trace["resolved_by"] == "oracle_spinbutton_fill"
        assert captured["status"] == "success"
        assert captured["postcondition_passed"] is True
    finally:
        helpers_v2._act_set_script_data(previous_script_data)
        helpers_v2._ACT_CURRENT_STRATEGY.clear()
        helpers_v2._ACT_CURRENT_STRATEGY.update(previous_strategy)


def test_active_oracle_table_editor_infers_adf_table_id_without_table_ancestor(monkeypatch) -> None:
    monkeypatch.setattr(
        helpers_v2,
        "_act_safe_page_eval",
        lambda page, expression: {
            "tag": "input",
            "role": "",
            "id": "pt1:ap1:at2:_ATp:ta2:0:i26::content",
            "name": "pt1:ap1:at2:_ATp:ta2:0:i26",
            "row_text": "1 Item Type Amount",
            "table_id": "",
        },
    )

    active_info = helpers_v2._act_active_oracle_table_editor(object())

    assert active_info["table_id"] == "pt1:ap1:at2:_ATp:ta2"
    assert active_info["row_text"] == "1 Item Type Amount"


def test_combobox_trigger_reflects_option_reads_descendant_input_value(monkeypatch) -> None:
    trigger = object()

    monkeypatch.setattr(helpers_v2, "_act_locator_value", lambda locator: "")
    monkeypatch.setattr(helpers_v2, "_act_locator_text", lambda locator: "Business Unit")
    monkeypatch.setattr(
        helpers_v2,
        "_act_safe_locator_eval",
        lambda locator, expression, arg=None: ["Business Unit", "Test Solutions"],
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_extract_locator_metadata",
        lambda locator: {"text": "Business Unit", "oracle_host_text": "Business Unit", "title": ""},
    )

    assert helpers_v2._act_combobox_trigger_reflects_option(trigger, "Test Solutions") is True


def test_enter_search_value_uses_keyboard_events_for_oracle_autosuggest() -> None:
    locator = _KeyboardEntryLocator()

    helpers_v2._act_enter_search_value(locator, "Fu")

    assert ("press", "ControlOrMeta+A", 3000) in locator.events
    assert ("press", "Backspace", 3000) in locator.events
    assert ("press_sequentially", "Fu", 75, 3000) in locator.events
    assert ("fill", "Fu", 3000) not in locator.events


def test_enter_search_value_uses_oracle_keyboard_open_when_label_intercepts_pointer_events(monkeypatch) -> None:
    locator = _OracleKeyboardEntryLocator()
    page = _NavigationPage()

    helpers_v2._act_reset_strategy_tracking("search_and_select", "Hiring Manager")

    def observe(current_page, current_locator=None):
        expanded = bool(getattr(current_locator, "expanded", False))
        return {
            "url": page.url,
            "title": "Create Job Requisition - Oracle Fusion Cloud Applications",
            "guided_step": "Hiring team",
            "guided_flow": {},
            "dialog_count": 1 if expanded else 0,
            "active_element": {"id": "expanded" if expanded else "collapsed"},
            "body_marker": "body",
            "target_value": "",
            "target_text": "",
            "target_visible": True,
            "target_meta": {"aria_expanded": "true" if expanded else "false"},
        }

    monkeypatch.setattr(helpers_v2, "_act_observe", observe)
    monkeypatch.setattr(
        helpers_v2,
        "_act_extract_locator_metadata",
        lambda *args, **kwargs: {"class_name": "oj-searchselect-input", "role": "combobox"},
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_safe_locator_eval",
        lambda *args, **kwargs: {"has_oracle_host": True},
    )

    helpers_v2._act_enter_search_value(locator, "Curtis Feitty", current_page=page, label="Hiring Manager")

    assert locator.focused is True
    assert ("press", "ArrowDown", 3000) in locator.events
    assert ("press", "ControlOrMeta+A", 3000) in locator.events
    assert ("press", "Backspace", 3000) in locator.events
    assert ("press_sequentially", "Curtis Feitty", 75, 3000) in locator.events
    assert helpers_v2._ACT_CURRENT_STRATEGY["recovery"] == {
        "source": "oracle_handler",
        "kind": "oracle_select_single_keyboard_open",
        "handler_name": "oracle_select_single_keyboard_open",
        "details": {
            "trigger_label": "Hiring Manager",
            "strategy_name": "oracle_select_single_arrowdown",
        },
    }


def test_build_ai_self_repair_prompt_includes_script_data_and_recorded_target_context(monkeypatch) -> None:
    page = _PromptPage()
    helpers_v2._act_set_script_data(
        {
            "tracked_action": "click_combobox",
            "raw": "page.get_by_role('combobox', name='Why are you changing the').click()",
            "primary_locator_expr": "page.get_by_role('combobox', name='Why are you changing the')",
        }
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_capture_locator_context",
        lambda *args, **kwargs: {
            "id": "whenAndWhyForm_fl_employmentWhenAndWhy.ActionReasonId|input",
            "oracle_host": {
                "tag": "oj-select-single",
                "id": "whenAndWhyForm_fl_employmentWhenAndWhy.ActionReasonId",
            },
        },
    )

    prompt = helpers_v2._act_build_ai_self_repair_prompt(
        page,
        "click_combobox",
        "Why are you changing the",
        RuntimeError("Locator.click failed because label subtree intercepts pointer events"),
        locator=_DateLocator("recorded"),
        dom_context={
            "helper": "click_combobox",
            "label": "Why are you changing the",
            "candidates": [
                {
                    "tag": "oj-select-single",
                    "id": "whenAndWhyForm_fl_employmentWhenAndWhy.ActionReasonId",
                    "text": "Why are you changing the manager?",
                }
            ],
        },
    )

    assert "Recorded script data JSON" in prompt
    assert "primary_locator_expr" in prompt
    assert "Recorded target context JSON" in prompt
    assert "oracle_host" in prompt
    assert "intercepts pointer events" in prompt
    helpers_v2._act_set_script_data({})


def test_build_ai_self_repair_prompt_marks_select_actions_and_includes_retry_feedback(monkeypatch) -> None:
    page = _PromptPage()
    helpers_v2._act_set_script_data(
        {
            "tracked_action": "select_option",
            "raw": 'page.get_by_label("Transaction Class").select_option("1")',
            "primary_locator_expr": 'page.get_by_label("Transaction Class")',
            "option_args": ["1"],
            "option_kwargs": {},
            "parsed_action": {
                "type": "select_option",
                "option_value": "1",
            },
        }
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_capture_locator_context",
        lambda *args, **kwargs: {
            "tag": "select",
            "id": "transactionClass::content",
            "aria_label": "Transaction Class",
            "title": "Invoice",
            "html": '<select id="transactionClass::content" title="Invoice" _afov="0"><option value="0">Invoice</option><option value="1">Credit Memo</option></select>',
        },
    )

    prompt = helpers_v2._act_build_ai_self_repair_prompt(
        page,
        "select_option_target",
        "Transaction Class",
        RuntimeError('Select "Transaction Class" did not reflect the requested option selection {"args":["1"],"kwargs":{}}.'),
        value='{"args":["1"],"kwargs":{}}',
        locator=_DateLocator("recorded"),
        dom_context={
            "helper": "select_option_target",
            "label": "Transaction Class",
            "candidates": [
                {
                    "tag": "input",
                    "id": "transactionSource::content",
                    "aria_label": "Transaction Source",
                    "text": "",
                    "html": '<input id="transactionSource::content" aria-label="Transaction Source">',
                }
            ],
        },
        retry_feedback={
            "round": 1,
            "execution_error": 'AI strategy "ai_css_1" did not satisfy select_option for "Transaction Class".',
            "attempted_locator_strategies": ["ai_css_1"],
        },
    )

    assert "Intended action: select_option" in prompt
    assert "Requested action value JSON" in prompt
    assert '"parsed_option_value": "1"' in prompt
    assert '"origin": "recorded_target"' in prompt
    assert "Retry feedback JSON" in prompt
    helpers_v2._act_set_script_data({})


def test_execute_ai_repair_rounds_retries_with_failure_feedback(monkeypatch) -> None:
    page = _PromptPage()
    retry_payloads: list[dict[str, object] | None] = []

    def _fake_ai_repair_locators(current_page, helper, label, last_error, value=None, locator=None, retry_feedback=None):
        retry_payloads.append(retry_feedback)
        round_index = len(retry_payloads)
        return [(f"ai_round_{round_index}", _DateLocator(f"candidate-{round_index}"), {"kind": "css", "selector": f"#candidate-{round_index}"})]

    monkeypatch.setattr(helpers_v2, "_act_ai_repair_locators", _fake_ai_repair_locators)
    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_finalize_last_ai_interaction", lambda **kwargs: None)

    attempts: list[str] = []

    result, last_error = helpers_v2._act_execute_ai_repair_rounds(
        current_page=page,
        helper="select_option_target",
        label="Transaction Class",
        last_error=RuntimeError("initial failure"),
        locator=_DateLocator("recorded"),
        value='{"args":["1"],"kwargs":{}}',
        postcondition_kind="option_selected",
        failure_message=lambda strategy_name: f'AI strategy "{strategy_name}" did not satisfy select_option for "Transaction Class".',
        execute_locator=lambda strategy_name, ai_locator, ai_strategy: attempts.append(strategy_name) or strategy_name.endswith("_2"),
    )

    assert result is not None
    assert result[0] == "ai_round_2"
    assert attempts == ["ai_round_1", "ai_round_2"]
    assert retry_payloads[0] is None
    assert retry_payloads[1] is not None
    assert retry_payloads[1]["round"] == 1
    assert retry_payloads[1]["attempted_locator_strategies"] == ["ai_round_1"]
    assert 'did not satisfy select_option for "Transaction Class"' in str(retry_payloads[1]["execution_error"])
    assert isinstance(last_error, Exception)


def test_request_ai_self_repair_attaches_full_page_screenshot_and_records_it(monkeypatch) -> None:
    page = _AIScreenshotPage()
    captured_request: dict[str, object] = {}

    class _FakeAIResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return b'{"output_text":"{\\"strategies\\": []}"}'

    def _fake_urlopen(request, timeout=0):
        captured_request["payload"] = json.loads(request.data.decode("utf-8"))
        captured_request["timeout"] = timeout
        return _FakeAIResponse()

    monkeypatch.setenv("ACT_AI_SELF_REPAIR_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-demo")
    monkeypatch.setattr(
        helpers_v2,
        "_act_collect_ai_dom_candidates",
        lambda *args, **kwargs: {
            "helper": "click_button_target",
            "label": "Approve",
            "candidates": [{"tag": "button", "id": "approve-1269", "text": "Approve"}],
        },
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_capture_locator_context",
        lambda *args, **kwargs: {"id": "approve-1269", "title": "Approve Job Requisition 1269"},
    )
    monkeypatch.setattr(helpers_v2, "urlopen", _fake_urlopen)

    helpers_v2._act_reset_strategy_tracking("click_button_target", "Approve")
    strategies = helpers_v2._act_request_ai_self_repair(
        page,
        "click_button_target",
        "Approve",
        RuntimeError('Locator.wait_for: Error: strict mode violation: get_by_role("button", name="Approve") resolved to 9 elements'),
        locator=_DateLocator("recorded"),
    )

    assert strategies == []
    user_content = captured_request["payload"]["input"][1]["content"]
    assert user_content[0]["type"] == "input_text"
    assert "full-page screenshot is attached" in user_content[0]["text"]
    assert user_content[1]["type"] == "input_image"
    assert user_content[1]["image_url"].startswith("data:image/jpeg;base64,")
    assert page.screenshot_calls == [{"full_page": True, "type": "jpeg", "quality": 45, "scale": "css"}]
    interaction = helpers_v2._ACT_CURRENT_STRATEGY["ai_interactions"][-1]
    assert interaction["page_screenshot"]["status"] == "captured"
    assert interaction["page_screenshot"]["image_url"].startswith("data:image/jpeg;base64,")


def test_ai_locator_matches_label_accepts_labelledby_text(monkeypatch) -> None:
    monkeypatch.setattr(
        helpers_v2,
        "_act_extract_locator_metadata",
        lambda *args, **kwargs: {"labelledby_text": "Why are you changing the manager?"},
    )

    assert helpers_v2._act_ai_locator_matches_label(_DateLocator("recorded"), "Why are you changing the")


def test_ai_locator_matches_label_accepts_oracle_notification_badge_count_changes(monkeypatch) -> None:
    monkeypatch.setattr(
        helpers_v2,
        "_act_extract_locator_metadata",
        lambda *args, **kwargs: {"title": "Notifications (9 unread)"},
    )

    assert helpers_v2._act_ai_locator_matches_label(_DateLocator("recorded"), "Notifications (10 unread)")


def test_select_search_trigger_option_enters_search_value_before_selecting(monkeypatch) -> None:
    trigger = _DateLocator("search")
    option = _DateLocator("result")
    page = _OptionPage(option)
    clicks: list[str] = []
    entered: list[tuple[str, str]] = []
    waited: list[str] = []
    observations = iter(
        [
            {"dialog_count": 1, "body_marker": "before"},
            {"dialog_count": 0, "body_marker": "after"},
        ]
    )

    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_strict_click", lambda locator, timeout_ms=None: clicks.append(locator.name))
    monkeypatch.setattr(
        helpers_v2,
        "_act_enter_search_value",
        lambda locator, value, timeout_ms=None, current_page=None, label="": entered.append((locator.name, value)),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_oracle_searchselect_query",
        lambda *args, **kwargs: ({"open": True, "no_matches": False, "filter_value": "Fu"}, True),
    )
    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_act_option_selection_postcondition", lambda *args, **kwargs: True)
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: waited.append("done"))

    helpers_v2._act_select_search_trigger_option(
        trigger,
        option,
        page,
        "Search for people to add as",
        "Wan Fu",
        fill_value="Fu",
    )

    assert clicks == ["result"]
    assert entered == [("search", "Fu")]
    assert waited == ["done"]


def test_select_search_trigger_option_preserves_non_exact_text_matching(monkeypatch) -> None:
    trigger = _SearchOptionLocator("search")
    option = _SearchOptionLocator("raw-option")
    page = _SearchOptionPage()
    clicked: list[str] = []

    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *args, **kwargs: None)

    def _fake_click(locator, timeout_ms=None):
        clicked.append(locator.name)
        if locator.name != "text:Supremo Candidate Selection:None":
            raise RuntimeError("candidate miss")

    monkeypatch.setattr(helpers_v2, "_act_strict_click", _fake_click)
    monkeypatch.setattr(helpers_v2, "_act_enter_search_value", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_oracle_searchselect_query",
        lambda *args, **kwargs: ({"open": True, "no_matches": False, "filter_value": "su"}, True),
    )
    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: {"dialog_count": 1, "body_marker": "state"})
    monkeypatch.setattr(helpers_v2, "_act_option_selection_postcondition", lambda *args, **kwargs: True)
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: None)

    helpers_v2._act_select_search_trigger_option(
        trigger,
        option,
        page,
        "Candidate Selection Process",
        "Supremo Candidate Selection",
        fill_value="su",
    )

    assert page.text_calls == [("Supremo Candidate Selection", None)]
    assert clicked[-1] == "text:Supremo Candidate Selection:None"


def test_select_search_trigger_option_preserves_exact_text_matching_when_requested(monkeypatch) -> None:
    trigger = _SearchOptionLocator("search")
    option = _SearchOptionLocator("raw-option")
    page = _SearchOptionPage()
    clicked: list[str] = []

    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *args, **kwargs: None)

    def _fake_click(locator, timeout_ms=None):
        clicked.append(locator.name)
        if locator.name != "text:Wan Fu:True":
            raise RuntimeError("candidate miss")

    monkeypatch.setattr(helpers_v2, "_act_strict_click", _fake_click)
    monkeypatch.setattr(helpers_v2, "_act_enter_search_value", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_oracle_searchselect_query",
        lambda *args, **kwargs: ({"open": True, "no_matches": False, "filter_value": "Fu"}, True),
    )
    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: {"dialog_count": 1, "body_marker": "state"})
    monkeypatch.setattr(helpers_v2, "_act_option_selection_postcondition", lambda *args, **kwargs: True)
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: None)

    helpers_v2._act_select_search_trigger_option(
        trigger,
        option,
        page,
        "Search for people to add as",
        "Wan Fu",
        option_exact=True,
        fill_value="Fu",
    )

    assert page.text_calls == [("Wan Fu", True)]
    assert clicked[-1] == "text:Wan Fu:True"


def test_select_search_trigger_option_fails_fast_when_title_search_trigger_opens_no_surface(monkeypatch) -> None:
    trigger = _SearchOptionLocator("search")
    option = _SearchOptionLocator("raw-option")
    page = _SearchOptionPage()
    clicked: list[str] = []

    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_strict_click", lambda locator, timeout_ms=None: clicked.append(locator.name))
    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_search_option_surface",
        lambda *args, **kwargs: {"option_visible": False, "popup_open": False},
    )

    with pytest.raises(
        RuntimeError,
        match='Search trigger "Search: Receipt Method" did not open a visible search surface',
    ):
        helpers_v2._act_select_search_trigger_option(
            trigger,
            option,
            page,
            "Search: Receipt Method",
            "Checking Account Receipt.",
        )

    assert clicked == ["search"]


def test_select_search_trigger_option_uses_oracle_lov_direct_entry_when_surface_never_opens(monkeypatch) -> None:
    trigger = _SearchOptionLocator("search")
    option = _SearchOptionLocator("raw-option")
    input_locator = _OracleLovDirectEntryInputLocator("receipt-method-input")
    page = _OracleLovDirectEntryPage(input_locator)
    entered: list[tuple[str, str, str]] = []
    clicked: list[str] = []

    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_strict_click", lambda locator, timeout_ms=None: clicked.append(locator.name))
    monkeypatch.setattr(
        helpers_v2,
        "_act_extract_locator_metadata",
        lambda locator: {
            "id": "pt1:_FOr1:1:_FONSr2:0:MAnt2:1:pt1:RCF1:0:ap1:receiptMethodId::lovIconId",
            "tag": "a",
            "role": "",
        }
        if locator is trigger
        else {
            "id": "pt1:_FOr1:1:_FONSr2:0:MAnt2:1:pt1:RCF1:0:ap1:receiptMethodId::content",
            "tag": "input",
            "role": "combobox",
            "disabled": "",
            "aria_disabled": "",
        },
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_search_option_surface",
        lambda *args, **kwargs: {"option_visible": False, "popup_open": False},
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_enter_search_value",
        lambda locator, value, timeout_ms=None, current_page=None, label="": entered.append((locator.name, value, label)),
    )
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_oracle_label_value_matches",
        lambda current_page, label, expected_value: label == "Receipt Method" and expected_value == "Checking Account Receipt.",
    )
    monkeypatch.setattr(helpers_v2, "_act_locator_value", lambda locator: "")
    monkeypatch.setattr(helpers_v2, "_act_locator_text", lambda locator: "")
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_set_recovery_record", lambda *args, **kwargs: None)

    helpers_v2._act_select_search_trigger_option(
        trigger,
        option,
        page,
        "Search: Receipt Method",
        "Checking Account Receipt.",
    )

    assert clicked == ["search"]
    assert page.locator_calls == ['[id="pt1:_FOr1:1:_FONSr2:0:MAnt2:1:pt1:RCF1:0:ap1:receiptMethodId::content"]']
    assert entered == [("receipt-method-input", "Checking Account Receipt.", "Receipt Method")]
    assert input_locator.presses == [("Tab", 3000)]


def test_select_search_trigger_option_bounds_non_raw_candidate_timeout(monkeypatch) -> None:
    trigger = _SearchOptionLocator("search")
    option = _SearchOptionLocator("raw-option")
    page = _SearchOptionPage()
    clicked: list[tuple[str, int | None]] = []

    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_search_option_surface",
        lambda *args, **kwargs: {"option_visible": False, "popup_open": True},
    )

    def _fake_click(locator, timeout_ms=None):
        clicked.append((locator.name, timeout_ms))
        if locator.name == "raw-option":
            raise RuntimeError("raw candidate miss")

    monkeypatch.setattr(helpers_v2, "_act_strict_click", _fake_click)
    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: {"dialog_count": 1, "body_marker": "state"})
    monkeypatch.setattr(helpers_v2, "_act_option_selection_postcondition", lambda *args, **kwargs: True)
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: None)

    helpers_v2._act_select_search_trigger_option(
        trigger,
        option,
        page,
        "Search: Receipt Method",
        "Checking Account Receipt.",
    )

    assert clicked[1:] == [
        ("raw-option", 6000),
        ("option:Checking Account Receipt.", 1500),
    ]


def test_wait_for_oracle_searchselect_query_polls_until_requested_value_reflects(monkeypatch) -> None:
    page = _SearchOptionPage()
    states = iter(
        [
            {
                "open": True,
                "no_matches": True,
                "live_text": "No matches found",
                "filter_value": "Supremo Candidate Selection Process",
            },
            {
                "open": True,
                "no_matches": False,
                "filter_value": "Can",
            },
        ]
    )

    monkeypatch.setattr(helpers_v2, "_act_oracle_searchselect_state", lambda current_page: next(states))

    state, reflected = helpers_v2._act_wait_for_oracle_searchselect_query(
        page,
        "Can",
        timeout_ms=200,
    )

    assert reflected is True
    assert state["filter_value"] == "Can"
    assert page.waits == [100]


def test_strict_click_honors_explicit_zero_timeout(monkeypatch) -> None:
    locator = _TimeoutRecordingLocator()
    monkeypatch.setenv("ACT_ACTION_TIMEOUT_MS", "3000")

    helpers_v2._act_strict_click(locator, timeout_ms=0)

    assert locator.events == [("wait_for", 0), ("scroll", 0), ("click", 0)]


def test_wait_for_oracle_searchselect_query_honors_explicit_zero_timeout(monkeypatch) -> None:
    page = _SearchOptionPage()
    monkeypatch.setattr(
        helpers_v2,
        "_act_oracle_searchselect_state",
        lambda current_page: {"open": True, "no_matches": True, "filter_value": "Supremo Candidate Selection Process"},
    )

    state, reflected = helpers_v2._act_wait_for_oracle_searchselect_query(
        page,
        "Can",
        timeout_ms=0,
    )

    assert reflected is False
    assert state["filter_value"] == "Supremo Candidate Selection Process"
    assert page.waits == []


def test_select_search_trigger_option_retries_when_oracle_query_does_not_reflect_requested_value(
    monkeypatch,
) -> None:
    trigger = _SearchOptionLocator("search")
    option = _SearchOptionLocator("raw-option")
    page = _SearchOptionPage()
    entered: list[tuple[str, str]] = []
    clicked: list[str] = []

    wait_states = iter(
        [
            (
                {
                    "open": True,
                    "no_matches": True,
                    "filter_value": "Supremo Candidate Selection Process",
                },
                False,
            ),
            (
                {
                    "open": True,
                    "no_matches": False,
                    "filter_value": "Can",
                },
                True,
            ),
        ]
    )

    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_enter_search_value",
        lambda locator, value, timeout_ms=None, current_page=None, label="": entered.append((locator.name, value)),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_oracle_searchselect_query",
        lambda *args, **kwargs: next(wait_states),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_oracle_searchselect_state",
        lambda current_page: {"open": True, "no_matches": False, "filter_value": "Can"},
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_strict_click",
        lambda locator, timeout_ms=None: clicked.append(locator.name),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_observe",
        lambda *args, **kwargs: {"dialog_count": 1, "body_marker": "state"},
    )
    monkeypatch.setattr(helpers_v2, "_act_option_selection_postcondition", lambda *args, **kwargs: True)
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: None)

    helpers_v2._act_select_search_trigger_option(
        trigger,
        option,
        page,
        "Candidate Selection Process",
        "Candidate Selection Process - DE - DE_CSP",
        fill_value="Can",
    )

    assert entered == [("search", "Can"), ("search", "Can")]
    assert clicked == ["raw-option"]


def test_select_search_trigger_option_fails_clearly_when_oracle_query_never_reflects_requested_value(
    monkeypatch,
) -> None:
    trigger = _SearchOptionLocator("search")
    option = _SearchOptionLocator("raw-option")
    page = _SearchOptionPage()
    entered: list[tuple[str, str]] = []

    monkeypatch.setattr(
        helpers_v2,
        "_act_enter_search_value",
        lambda locator, value, timeout_ms=None, current_page=None, label="": entered.append((locator.name, value)),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_oracle_searchselect_query",
        lambda *args, **kwargs: (
            {
                "open": True,
                "no_matches": True,
                "filter_value": "Supremo Candidate Selection Process",
            },
            False,
        ),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_oracle_searchselect_state",
        lambda current_page: {
            "open": True,
            "no_matches": True,
            "filter_value": "Supremo Candidate Selection Process",
        },
    )
    monkeypatch.setattr(helpers_v2, "_act_locator_visible", lambda *args, **kwargs: False)

    with pytest.raises(
        RuntimeError,
        match='Oracle search-select "Candidate Selection Process" did not reflect requested query "Can"',
    ):
        helpers_v2._act_select_search_trigger_option(
            trigger,
            option,
            page,
            "Candidate Selection Process",
            "Candidate Selection Process - DE - DE_CSP",
            fill_value="Can",
        )

    assert entered == [("search", "Can"), ("search", "Can")]


def test_select_search_trigger_option_proceeds_when_oracle_query_is_unknown_but_exact_option_is_visible(
    monkeypatch,
) -> None:
    trigger = _SearchOptionLocator("search")
    option = _SearchOptionLocator("raw-option")
    page = _SearchOptionPage()
    entered: list[tuple[str, str]] = []
    clicked: list[str] = []

    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_enter_search_value",
        lambda locator, value, timeout_ms=None, current_page=None, label="": entered.append((locator.name, value)),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_oracle_searchselect_query",
        lambda *args, **kwargs: (
            {
                "open": True,
                "no_matches": False,
                "filter_value": "",
            },
            False,
        ),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_oracle_searchselect_state",
        lambda current_page: {
            "open": True,
            "no_matches": False,
            "filter_value": "",
        },
    )
    monkeypatch.setattr(helpers_v2, "_act_locator_visible", lambda locator, timeout_ms=None: locator.name == "raw-option")
    monkeypatch.setattr(
        helpers_v2,
        "_act_strict_click",
        lambda locator, timeout_ms=None: clicked.append(locator.name),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_observe",
        lambda *args, **kwargs: {"dialog_count": 1, "body_marker": "state"},
    )
    monkeypatch.setattr(helpers_v2, "_act_option_selection_postcondition", lambda *args, **kwargs: True)
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: None)

    helpers_v2._act_select_search_trigger_option(
        trigger,
        option,
        page,
        "Business Unit",
        "US1 Business Unit",
        option_exact=True,
        fill_value="US1 Business Unit",
    )

    assert entered == [("search", "US1 Business Unit"), ("search", "US1 Business Unit")]
    assert clicked == ["raw-option"]


def test_select_search_trigger_option_proceeds_when_oracle_no_matches_state_is_stale_but_exact_option_is_visible(
    monkeypatch,
) -> None:
    trigger = _SearchOptionLocator("search")
    option = _SearchOptionLocator("raw-option")
    page = _SearchOptionPage()
    clicked: list[str] = []

    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_enter_search_value", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_oracle_searchselect_query",
        lambda *args, **kwargs: (
            {
                "open": True,
                "no_matches": True,
                "live_text": "No matches found",
                "filter_value": "US1 Business Unit",
            },
            True,
        ),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_oracle_searchselect_state",
        lambda current_page: {
            "open": True,
            "no_matches": True,
            "live_text": "No matches found",
            "filter_value": "US1 Business Unit",
        },
    )
    monkeypatch.setattr(helpers_v2, "_act_locator_visible", lambda locator, timeout_ms=None: locator.name == "raw-option")
    monkeypatch.setattr(
        helpers_v2,
        "_act_strict_click",
        lambda locator, timeout_ms=None: clicked.append(locator.name),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_observe",
        lambda *args, **kwargs: {"dialog_count": 1, "body_marker": "state"},
    )
    monkeypatch.setattr(helpers_v2, "_act_option_selection_postcondition", lambda *args, **kwargs: True)
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: None)

    helpers_v2._act_select_search_trigger_option(
        trigger,
        option,
        page,
        "Business Unit",
        "US1 Business Unit",
        option_exact=True,
        fill_value="US1 Business Unit",
    )

    assert clicked == ["raw-option"]


def test_select_adf_menu_panel_option_fails_clearly_after_invoice_validate_semantic_failure(monkeypatch) -> None:
    trigger = _SearchOptionLocator("trigger")
    option = _SearchOptionLocator("raw-option")
    page = _SearchOptionPage()
    clicked: list[str] = []

    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: {"dialog_count": 1, "body_marker": "state"})
    monkeypatch.setattr(helpers_v2, "_act_strict_click", lambda locator, timeout_ms=None: clicked.append(locator.name))
    monkeypatch.setattr(
        helpers_v2,
        "_act_oracle_menu_option_semantic_postcondition",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_experience_repair_locators",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("experience should not run")),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_ai_repair_locators",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ai should not run")),
    )

    with pytest.raises(RuntimeError, match='Invoice Actions "Validate" did not clear the "Not validated" status.'):
        helpers_v2._act_select_adf_menu_panel_option(
            trigger,
            option,
            page,
            "Invoice Actions",
            "Validate",
            trigger_kind="link",
        )

    assert clicked == ["trigger", "raw-option"]
    assert page.waits == [350, 250]


def test_select_adf_menu_panel_option_fails_clearly_when_invoice_actions_does_not_expose_requested_option(monkeypatch) -> None:
    trigger = _SearchOptionLocator("trigger")
    option = _SearchOptionLocator("raw-option")
    page = _SearchOptionPage()
    clicked: list[str] = []

    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_strict_click", lambda locator, timeout_ms=None: clicked.append(locator.name))
    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_oracle_menu_trigger_option_visibility",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_experience_repair_locators",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("experience should not run")),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_ai_repair_locators",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ai should not run")),
    )

    with pytest.raises(RuntimeError, match='Invoice Actions did not expose menu option "Validate".'):
        helpers_v2._act_select_adf_menu_panel_option(
            trigger,
            option,
            page,
            "Invoice Actions",
            "Validate",
            trigger_kind="link",
        )

    assert clicked == ["trigger"]
    assert page.waits == [350]


def test_select_adf_menu_panel_option_uses_strict_raw_validate_before_menuitem_fallback(monkeypatch) -> None:
    trigger = _SearchOptionLocator("trigger")
    option = _AmbiguousSearchOptionLocator("raw-option")
    page = _SearchOptionPage()
    clicked: list[str] = []

    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_oracle_menu_trigger_option_visibility",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_observe",
        lambda *args, **kwargs: {"dialog_count": 1, "body_marker": "state"},
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_option_selection_postcondition",
        lambda before, after, trigger_locator, option_locator, option_name, **kwargs: getattr(option_locator, "name", "") == "menuitem:Validate",
    )
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_experience_repair_locators",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("experience should not run")),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_ai_repair_locators",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ai should not run")),
    )

    def _strict_click(locator, timeout_ms=None):
        clicked.append(locator.name)
        if locator.name == "raw-option":
            raise RuntimeError("strict mode violation")

    monkeypatch.setattr(helpers_v2, "_act_strict_click", _strict_click)

    helpers_v2._act_select_adf_menu_panel_option(
        trigger,
        option,
        page,
        "Invoice Actions",
        "Validate",
        trigger_kind="link",
    )

    assert clicked == ["trigger", "raw-option", "menuitem:Validate"]
    assert "raw-option:first" not in clicked
    assert page.waits == [350, 250]


def test_select_adf_menu_panel_option_skips_hidden_raw_text_candidate_before_click_timeout(monkeypatch) -> None:
    trigger = _SearchOptionLocator("trigger")
    option = _SearchOptionLocator("raw-option")
    page = _SearchOptionPage()
    clicked: list[str] = []

    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_oracle_menu_trigger_option_visibility",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_locator_is_actionable",
        lambda locator, timeout_ms=None: getattr(locator, "name", "") == "menuitem:Edit Additional Information",
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_observe",
        lambda *args, **kwargs: {"dialog_count": 1, "body_marker": "state"},
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_option_selection_postcondition",
        lambda before, after, trigger_locator, option_locator, option_name, **kwargs: getattr(option_locator, "name", "") == "menuitem:Edit Additional Information",
    )
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_experience_repair_locators",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("experience should not run")),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_ai_repair_locators",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ai should not run")),
    )

    def _strict_click(locator, timeout_ms=None):
        clicked.append(locator.name)
        if locator.name == "raw-option":
            raise AssertionError("hidden raw text candidate should not be clicked")

    monkeypatch.setattr(helpers_v2, "_act_strict_click", _strict_click)

    helpers_v2._act_select_adf_menu_panel_option(
        trigger,
        option,
        page,
        "ActionsValidateReprice",
        "Edit Additional Information",
        trigger_kind="text",
    )

    assert clicked == ["trigger", "menuitem:Edit Additional Information"]
    assert page.waits == [350, 250]


def test_requires_semantic_validation_covers_transaction_completion_split_buttons() -> None:
    assert helpers_v2._act_oracle_menu_option_requires_semantic_validation(
        "Complete and Create Another",
        "Complete and Review",
    )


def test_oracle_transaction_completion_advanced_detects_navigation_and_step_change(monkeypatch) -> None:
    baseline = {
        "url": "https://example.com/fscmUI/ar/edit-transaction",
        "title": "Edit Transaction: Invoice 58005",
        "guided_step": "Edit Transaction",
    }
    stalled = _CompletionPage(title_text="Edit Transaction: Invoice 58005")
    reviewed = _CompletionPage(title_text="Review Transaction: Invoice 58005")
    reviewed.url = "https://example.com/fscmUI/ar/review-transaction"
    same_title_new_step = _CompletionPage(title_text="Edit Transaction: Invoice 58005")
    same_title_new_step.current_step = "Review Transaction"

    monkeypatch.setattr(helpers_v2, "_act_current_guided_step", lambda page: page.current_step)

    assert helpers_v2._act_oracle_transaction_completion_advanced(stalled, baseline) is False
    assert helpers_v2._act_oracle_transaction_completion_advanced(reviewed, baseline) is True
    assert helpers_v2._act_oracle_transaction_completion_advanced(same_title_new_step, baseline) is True


def test_oracle_transaction_completion_advanced_detects_review_state_from_oracle_page_content(monkeypatch) -> None:
    baseline = {
        "url": "https://example.com/fscmUI/ar/edit-transaction",
        "title": "Create Transaction - Billing - Oracle Fusion Cloud Applications",
        "guided_step": "",
        "guided_flow": {"primary_heading": "Edit Transaction: Invoice 58007"},
        "body_marker": "Edit Transaction: Invoice 58007 Status Incomplete Payment Terms IMMEDIATE",
    }
    page = _CompletionPage(title_text="Create Transaction - Billing - Oracle Fusion Cloud Applications")
    state = {
        "primary_heading": "Review Transaction: Invoice 7005359",
        "body_marker": "Review Transaction: Invoice 7005359 Status Complete Payment Terms IMMEDIATE",
    }

    monkeypatch.setattr(helpers_v2, "_act_current_guided_step", lambda current_page: "")
    monkeypatch.setattr(helpers_v2, "_act_guided_flow_state", lambda current_page: {"primary_heading": state["primary_heading"]})
    monkeypatch.setattr(helpers_v2, "_act_body_marker", lambda current_page: state["body_marker"])

    assert helpers_v2._act_oracle_transaction_completion_advanced(page, baseline) is True


def test_oracle_transaction_completion_advanced_records_debug_trace_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("ACT_DEBUG_TRACE", "true")
    helpers_v2._ACT_ACTION_LOG.clear()
    helpers_v2._act_reset_strategy_tracking("_act_select_adf_menu_panel_option", "Complete and Create Another")

    baseline = {
        "url": "https://example.com/fscmUI/ar/edit-transaction",
        "title": "Create Transaction - Billing - Oracle Fusion Cloud Applications",
        "guided_step": "",
        "guided_flow": {"primary_heading": "Edit Transaction: Invoice 58007"},
        "body_marker": "Edit Transaction: Invoice 58007 Status Incomplete Payment Terms IMMEDIATE",
    }
    page = _CompletionPage(title_text="Create Transaction - Billing - Oracle Fusion Cloud Applications")
    state = {
        "primary_heading": "Review Transaction: Invoice 7005359",
        "body_marker": "Review Transaction: Invoice 7005359 Status Complete Payment Terms IMMEDIATE",
    }

    monkeypatch.setattr(helpers_v2, "_act_current_guided_step", lambda current_page: "")
    monkeypatch.setattr(helpers_v2, "_act_guided_flow_state", lambda current_page: {"primary_heading": state["primary_heading"]})
    monkeypatch.setattr(helpers_v2, "_act_body_marker", lambda current_page: state["body_marker"])

    assert helpers_v2._act_oracle_transaction_completion_advanced(page, baseline) is True

    helpers_v2._act_finalize_action_log(
        "adf_menu_select",
        "Complete and Create Another",
        "success",
        250,
        page=page,
    )

    debug_payload = helpers_v2._ACT_ACTION_LOG[-1]["debug"]["oracle_completion_check"]
    assert debug_payload["matched_signal"] == "heading_changed_to_review"
    assert debug_payload["postcondition_passed"] is True


def test_set_debug_detail_records_select_option_trace_without_global_debug(monkeypatch) -> None:
    monkeypatch.delenv("ACT_DEBUG_TRACE", raising=False)
    helpers_v2._act_reset_strategy_tracking("select_option_target", "Business Unit")

    helpers_v2._act_set_debug_detail("select_option_target", {"status": "direct_failed"})

    assert helpers_v2._ACT_CURRENT_STRATEGY["debug"]["select_option_target"] == {
        "status": "direct_failed"
    }


def test_set_debug_detail_records_click_trace_without_global_debug(monkeypatch) -> None:
    monkeypatch.delenv("ACT_DEBUG_TRACE", raising=False)
    helpers_v2._act_reset_strategy_tracking("click_text_target", "Edit Additional Information")

    helpers_v2._act_set_debug_detail("click_with_candidates", {"status": "direct_failed"})

    assert helpers_v2._ACT_CURRENT_STRATEGY["debug"]["click_with_candidates"] == {
        "status": "direct_failed"
    }


def test_rank_ai_dom_candidates_prioritizes_select_for_select_option() -> None:
    ranked = helpers_v2._act_rank_ai_dom_candidates(
        "select_option_target",
        "Business Unit",
        [
            {
                "tag": "input",
                "role": "combobox",
                "text": "",
                "aria_label": "",
                "labelledby_text": "",
                "oracle_host_text": "",
                "oracle_host_data_oj_field": "",
                "title": "",
                "label_hint": "",
                "placeholder": "",
                "name": "partyName",
                "id": "partyNameId",
                "html": '<input role="combobox" id="partyNameId" value="Intel">',
                "data_oj_field": "",
                "oracle_host_tag": "",
            },
            {
                "tag": "select",
                "role": "",
                "text": "Test Solutions Vision Operations",
                "aria_label": "Business Unit",
                "labelledby_text": "Business Unit",
                "oracle_host_text": "",
                "oracle_host_data_oj_field": "",
                "title": "",
                "label_hint": "",
                "placeholder": "",
                "name": "businessUnit",
                "id": "businessUnitId",
                "html": '<select id="businessUnitId" aria-label="Business Unit"><option selected>Test Solutions</option></select>',
                "data_oj_field": "",
                "oracle_host_tag": "",
            },
        ],
        2,
    )

    assert ranked[0]["tag"] == "select"
    assert ranked[0]["id"] == "businessUnitId"


def test_rank_ai_dom_candidates_demotes_large_unrelated_select_for_menu_helper() -> None:
    ranked = helpers_v2._act_rank_ai_dom_candidates(
        "adf_menu_select",
        "Invoice Actions",
        [
            {
                "tag": "select",
                "role": "",
                "text": "USD - US Dollar EUR - Euro GBP - Pound Sterling JPY - Yen " * 8,
                "aria_label": "",
                "labelledby_text": "",
                "oracle_host_text": "",
                "oracle_host_data_oj_field": "",
                "title": "USD - US Dollar",
                "label_hint": "",
                "placeholder": "",
                "name": "",
                "id": "currencySelect",
                "html": "<select id='currencySelect'></select>",
                "data_oj_field": "",
                "oracle_host_tag": "",
            },
            {
                "tag": "a",
                "role": "menuitem",
                "text": "Invoice Actions",
                "aria_label": "Invoice Actions",
                "labelledby_text": "",
                "oracle_host_text": "",
                "oracle_host_data_oj_field": "",
                "title": "",
                "label_hint": "",
                "placeholder": "",
                "name": "",
                "id": "invoiceActionsTrigger",
                "html": "<a role='menuitem' aria-label='Invoice Actions'>Invoice Actions</a>",
                "data_oj_field": "",
                "oracle_host_tag": "",
            },
        ],
        2,
    )

    assert ranked[0]["id"] == "invoiceActionsTrigger"


def test_select_adf_menu_panel_option_fails_when_completion_action_does_not_advance_flow(monkeypatch) -> None:
    monkeypatch.setenv("ACT_TRANSACTION_COMPLETE_POSTCONDITION_TIMEOUT_MS", "0")
    trigger = _SearchOptionLocator("trigger")
    option = _SearchOptionLocator("raw-option")
    page = _CompletionPage(title_text="Edit Transaction: Invoice 58005")
    clicked: list[str] = []
    observations = iter(
        [
            {
                "url": page.url,
                "title": "Edit Transaction: Invoice 58005",
                "guided_step": "Edit Transaction",
                "dialog_count": 1,
            }
        ]
    )

    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: dict(next(observations, {
        "url": page.url,
        "title": "Edit Transaction: Invoice 58005",
        "guided_step": "Edit Transaction",
        "dialog_count": 1,
    })))
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_locator_is_actionable", lambda locator, timeout_ms=None: getattr(locator, "name", "") in {
        "raw-option",
        "menuitem:Complete and Review",
    })
    monkeypatch.setattr(
        helpers_v2,
        "_act_experience_repair_locators",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("experience should not run")),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_ai_repair_locators",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ai should not run")),
    )

    def _strict_click(locator, timeout_ms=None):
        clicked.append(locator.name)

    monkeypatch.setattr(helpers_v2, "_act_strict_click", _strict_click)

    with pytest.raises(RuntimeError, match="did not complete the transaction"):
        helpers_v2._act_select_adf_menu_panel_option(
            trigger,
            option,
            page,
            "Complete and Create Another",
            "Complete and Review",
            trigger_kind="title",
        )

    assert clicked == ["trigger", "raw-option", "menuitem:Complete and Review"]


def test_select_adf_menu_panel_option_reasserts_when_first_click_only_saves(monkeypatch) -> None:
    # AR_Credit_Memo flake: the first "Complete and Review" activation on a fresh draft lands
    # only the SAVE phase (create -> "edit transaction: <n>", still incomplete, idle), so the
    # completion postcondition fails. From that saved-pending state one more "Complete and
    # Review" must reach Review -- deterministically, so the step passes on any pod.
    monkeypatch.setenv("ACT_TRANSACTION_COMPLETE_POSTCONDITION_TIMEOUT_MS", "0")
    monkeypatch.setenv("ACT_ORACLE_PPR_SETTLE_MS", "0")
    monkeypatch.setenv("ACT_COMPLETION_REASSERT_MAX", "2")
    trigger = _SearchOptionLocator("trigger")
    option = _SearchOptionLocator("raw-option")
    page = _CompletionPage(
        title_text="Create Transaction - Billing - Oracle Fusion Cloud Applications"
    )
    clicked: list[str] = []
    state = {
        "primary_heading": "Create Transaction: Credit Memo",
        "body_marker": "Create Transaction: Credit Memo Status Incomplete",
    }
    option_clicks = {"count": 0}
    candidate = _SearchOptionLocator("complete-and-review")

    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *a, **k: None)
    monkeypatch.setattr(
        helpers_v2, "_act_wait_for_oracle_menu_trigger_option_visibility", lambda *a, **k: True
    )
    monkeypatch.setattr(
        helpers_v2, "_act_menu_panel_option_candidates", lambda *a, **k: [("text", candidate)]
    )
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *a, **k: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_locator_is_actionable",
        lambda locator, timeout_ms=None: getattr(locator, "name", "") == "complete-and-review",
    )
    monkeypatch.setattr(helpers_v2, "_act_busy_indicator_count", lambda current_page: 0)
    monkeypatch.setattr(helpers_v2, "_act_collect_validation_messages", lambda current_page: [])
    monkeypatch.setattr(
        helpers_v2,
        "_act_experience_repair_locators",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("experience should not run")),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_ai_repair_locators",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ai should not run")),
    )

    def _observe(*args, **kwargs):
        return {
            "url": page.url,
            "title": page.title_text,
            "guided_step": "",
            "guided_flow": {"primary_heading": state["primary_heading"]},
            "dialog_count": 1,
            "body_marker": state["body_marker"],
            "active_element": {
                "tag": "button",
                "id": "_FOd1::msgDlg::cancel",
                "text": "OK",
            },
        }

    def _strict_click(locator, timeout_ms=None):
        name = getattr(locator, "name", "")
        clicked.append(name)
        if name == "complete-and-review":
            option_clicks["count"] += 1
            if option_clicks["count"] == 1:
                # first activation: only the SAVE phase lands (create -> edit, still incomplete)
                state["primary_heading"] = "Edit Transaction: Credit Memo 149769"
                state["body_marker"] = "Edit Transaction: Credit Memo 149769 Status Incomplete"
            else:
                # re-assert: the Complete+Review phase now renders the Review page
                state["body_marker"] = "Review Transaction: Credit Memo 149769 Status Complete"

    monkeypatch.setattr(helpers_v2, "_act_current_guided_step", lambda current_page: "")
    monkeypatch.setattr(
        helpers_v2,
        "_act_guided_flow_state",
        lambda current_page: {"primary_heading": state["primary_heading"]},
    )
    monkeypatch.setattr(helpers_v2, "_act_body_marker", lambda current_page: state["body_marker"])
    monkeypatch.setattr(helpers_v2, "_act_observe", _observe)
    monkeypatch.setattr(helpers_v2, "_act_strict_click", _strict_click)

    helpers_v2._act_select_adf_menu_panel_option(
        trigger,
        option,
        page,
        "Complete and Create Another",
        "Complete and Review",
        trigger_kind="title",
    )

    # try A (loop) opened the menu + clicked the option, which only saved; the re-assert
    # re-opened the split button and re-clicked "Complete and Review" to reach Review.
    assert clicked == ["trigger", "complete-and-review", "trigger", "complete-and-review"]
    assert option_clicks["count"] == 2


def test_select_adf_menu_panel_option_reassert_stops_on_validation_block(monkeypatch) -> None:
    # When the saved transaction cannot complete because a real validation message blocks it,
    # the re-assert must NOT re-click (no loop, no false pass) and the step must fail honestly --
    # the same outcome on any pod.
    monkeypatch.setenv("ACT_TRANSACTION_COMPLETE_POSTCONDITION_TIMEOUT_MS", "0")
    monkeypatch.setenv("ACT_ORACLE_PPR_SETTLE_MS", "0")
    monkeypatch.setenv("ACT_COMPLETION_REASSERT_MAX", "2")
    trigger = _SearchOptionLocator("trigger")
    option = _SearchOptionLocator("raw-option")
    page = _CompletionPage(
        title_text="Create Transaction - Billing - Oracle Fusion Cloud Applications"
    )
    clicked: list[str] = []
    # already saved-but-incomplete from the start, and it stays that way (cannot complete)
    state = {
        "primary_heading": "Edit Transaction: Credit Memo 149769",
        "body_marker": "Edit Transaction: Credit Memo 149769 Status Incomplete",
    }

    candidate = _SearchOptionLocator("complete-and-review")

    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *a, **k: None)
    monkeypatch.setattr(
        helpers_v2, "_act_wait_for_oracle_menu_trigger_option_visibility", lambda *a, **k: True
    )
    monkeypatch.setattr(
        helpers_v2, "_act_menu_panel_option_candidates", lambda *a, **k: [("text", candidate)]
    )
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *a, **k: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_locator_is_actionable",
        lambda locator, timeout_ms=None: getattr(locator, "name", "") == "complete-and-review",
    )
    monkeypatch.setattr(helpers_v2, "_act_busy_indicator_count", lambda current_page: 0)
    monkeypatch.setattr(
        helpers_v2,
        "_act_oracle_transaction_saved_pending_completion",
        lambda current_page: True,
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_collect_validation_messages",
        lambda current_page: ["Transaction Date: Enter a value."],
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_experience_repair_locators",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("experience should not run")),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_ai_repair_locators",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ai should not run")),
    )

    def _observe(*args, **kwargs):
        return {
            "url": page.url,
            "title": page.title_text,
            "guided_step": "",
            "guided_flow": {"primary_heading": state["primary_heading"]},
            "dialog_count": 1,
            "body_marker": state["body_marker"],
            "active_element": {
                "tag": "button",
                "id": "_FOd1::msgDlg::cancel",
                "text": "OK",
            },
        }

    monkeypatch.setattr(helpers_v2, "_act_current_guided_step", lambda current_page: "")
    monkeypatch.setattr(
        helpers_v2,
        "_act_guided_flow_state",
        lambda current_page: {"primary_heading": state["primary_heading"]},
    )
    monkeypatch.setattr(helpers_v2, "_act_body_marker", lambda current_page: state["body_marker"])
    monkeypatch.setattr(helpers_v2, "_act_observe", _observe)
    monkeypatch.setattr(
        helpers_v2, "_act_strict_click", lambda locator, timeout_ms=None: clicked.append(
            getattr(locator, "name", "")
        )
    )
    helpers_v2._act_reset_strategy_tracking(
        "_act_select_adf_menu_panel_option", "Complete and Create Another"
    )

    with pytest.raises(RuntimeError, match="did not complete the transaction"):
        helpers_v2._act_select_adf_menu_panel_option(
            trigger,
            option,
            page,
            "Complete and Create Another",
            "Complete and Review",
            trigger_kind="title",
        )

    # try A opened + clicked once; the re-assert bailed on the validation block without re-clicking.
    assert clicked == ["trigger", "complete-and-review"]
    debug_trace = helpers_v2._ACT_CURRENT_STRATEGY["debug"]["select_adf_menu_panel_option"]
    attempt = debug_trace["completion_reassert_attempts"][-1]
    assert attempt["status"] == "blocked_by_validation"
    assert attempt["probe_observation"]["dialog_count"] == 1
    assert attempt["probe_observation"]["active_element"]["id"] == "_FOd1::msgDlg::cancel"
    assert attempt["messages"] == ["Transaction Date: Enter a value."]


def test_select_adf_menu_panel_option_completion_continues_when_menu_visibility_probe_misses_direct_submit(monkeypatch) -> None:
    monkeypatch.setenv("ACT_TRANSACTION_COMPLETE_POSTCONDITION_TIMEOUT_MS", "0")
    trigger = _SearchOptionLocator("trigger")
    option = _SearchOptionLocator("raw-option")
    page = _CompletionPage(title_text="Create Receipt - Accounts Receivable - Oracle Fusion Cloud Applications")
    clicked: list[str] = []
    state = {
        "primary_heading": "Create Receipt",
        "body_marker": "Create Receipt Status Incomplete",
    }

    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_wait_for_oracle_menu_trigger_option_visibility",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_oracle_visible_popup_option_candidates",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_locator_is_actionable",
        lambda locator, timeout_ms=None: getattr(locator, "name", "") == "raw-option",
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_experience_repair_locators",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("experience should not run")),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_ai_repair_locators",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ai should not run")),
    )

    def _observe(*args, **kwargs):
        return {
            "url": page.url,
            "title": page.title_text,
            "guided_step": "",
            "guided_flow": {"primary_heading": state["primary_heading"]},
            "dialog_count": 1,
            "body_marker": state["body_marker"],
        }

    def _strict_click(locator, timeout_ms=None):
        clicked.append(locator.name)
        if locator.name == "raw-option":
            state["primary_heading"] = "Review Receipt"
            state["body_marker"] = "Review Receipt Status Complete"

    monkeypatch.setattr(helpers_v2, "_act_current_guided_step", lambda current_page: "")
    monkeypatch.setattr(helpers_v2, "_act_guided_flow_state", lambda current_page: {"primary_heading": state["primary_heading"]})
    monkeypatch.setattr(helpers_v2, "_act_body_marker", lambda current_page: state["body_marker"])
    monkeypatch.setattr(helpers_v2, "_act_observe", _observe)
    monkeypatch.setattr(helpers_v2, "_act_strict_click", _strict_click)

    helpers_v2._act_select_adf_menu_panel_option(
        trigger,
        option,
        page,
        "Submit and Create Another",
        "Submit",
        trigger_kind="title",
    )

    assert clicked == ["trigger", "raw-option"]
    assert page.waits == [350, 250]


def test_select_adf_menu_panel_option_completion_uses_visible_popup_submit_when_page_wide_candidates_miss(monkeypatch) -> None:
    monkeypatch.setenv("ACT_TRANSACTION_COMPLETE_POSTCONDITION_TIMEOUT_MS", "0")
    trigger = _SearchOptionLocator("trigger")
    option = _SearchOptionLocator("raw-option")
    page = _CompletionPage(title_text="Create Receipt - Accounts Receivable - Oracle Fusion Cloud Applications")
    clicked: list[str] = []
    state = {
        "primary_heading": "Create Receipt",
        "body_marker": "Create Receipt Status Incomplete",
    }

    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_locator_is_actionable",
        lambda locator, timeout_ms=None: getattr(locator, "name", "") == "scope:[role='menu']:visible:menuitem:Submit:True",
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_experience_repair_locators",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("experience should not run")),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_ai_repair_locators",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ai should not run")),
    )

    def _observe(*args, **kwargs):
        return {
            "url": page.url,
            "title": page.title_text,
            "guided_step": "",
            "guided_flow": {"primary_heading": state["primary_heading"]},
            "dialog_count": 1,
            "body_marker": state["body_marker"],
        }

    def _strict_click(locator, timeout_ms=None):
        clicked.append(locator.name)
        if locator.name == "scope:[role='menu']:visible:menuitem:Submit:True":
            state["primary_heading"] = "Review Receipt"
            state["body_marker"] = "Review Receipt Status Complete"

    monkeypatch.setattr(helpers_v2, "_act_current_guided_step", lambda current_page: "")
    monkeypatch.setattr(helpers_v2, "_act_guided_flow_state", lambda current_page: {"primary_heading": state["primary_heading"]})
    monkeypatch.setattr(helpers_v2, "_act_body_marker", lambda current_page: state["body_marker"])
    monkeypatch.setattr(helpers_v2, "_act_observe", _observe)
    monkeypatch.setattr(helpers_v2, "_act_strict_click", _strict_click)

    helpers_v2._act_select_adf_menu_panel_option(
        trigger,
        option,
        page,
        "Submit and Create Another",
        "Submit",
        trigger_kind="title",
    )

    assert clicked == ["trigger", "scope:[role='menu']:visible:menuitem:Submit:True"]
    assert ("menuitem", "Submit", True) in page.role_calls
    assert ("[role='menu']:visible", "menuitem", "Submit", True) in page.scoped_role_calls


def test_select_adf_menu_panel_option_completion_uses_menuitem_row_when_text_cell_advances_nothing(monkeypatch) -> None:
    monkeypatch.setenv("ACT_TRANSACTION_COMPLETE_POSTCONDITION_TIMEOUT_MS", "0")
    trigger = _SearchOptionLocator("trigger")
    option = _SearchOptionLocator("raw-option")
    page = _CompletionPage(title_text="Edit Transaction: Invoice 58005")
    clicked: list[str] = []
    state = {"title": "Edit Transaction: Invoice 58005"}

    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_locator_is_actionable", lambda locator, timeout_ms=None: getattr(locator, "name", "") in {
        "raw-option",
        "menuitem:Complete and Review",
    })
    monkeypatch.setattr(
        helpers_v2,
        "_act_experience_repair_locators",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("experience should not run")),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_ai_repair_locators",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ai should not run")),
    )

    def _observe(*args, **kwargs):
        return {
            "url": page.url,
            "title": state["title"],
            "guided_step": "Edit Transaction",
            "dialog_count": 1,
        }

    def _strict_click(locator, timeout_ms=None):
        clicked.append(locator.name)
        if locator.name == "menuitem:Complete and Review":
            state["title"] = "Review Transaction: Invoice 58005"
            page.title_text = state["title"]

    monkeypatch.setattr(helpers_v2, "_act_observe", _observe)
    monkeypatch.setattr(helpers_v2, "_act_strict_click", _strict_click)

    helpers_v2._act_select_adf_menu_panel_option(
        trigger,
        option,
        page,
        "Complete and Create Another",
        "Complete and Review",
        trigger_kind="title",
    )

    assert clicked == ["trigger", "raw-option", "menuitem:Complete and Review"]


def test_select_adf_menu_panel_option_completion_accepts_review_page_when_browser_title_stays_stable(monkeypatch) -> None:
    monkeypatch.setenv("ACT_TRANSACTION_COMPLETE_POSTCONDITION_TIMEOUT_MS", "0")
    trigger = _SearchOptionLocator("trigger")
    option = _SearchOptionLocator("raw-option")
    page = _CompletionPage(title_text="Create Transaction - Billing - Oracle Fusion Cloud Applications")
    clicked: list[str] = []
    state = {
        "primary_heading": "Edit Transaction: Invoice 58007",
        "body_marker": "Edit Transaction: Invoice 58007 Status Incomplete Payment Terms IMMEDIATE",
    }

    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_locator_is_actionable", lambda locator, timeout_ms=None: getattr(locator, "name", "") == "raw-option")
    monkeypatch.setattr(
        helpers_v2,
        "_act_experience_repair_locators",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("experience should not run")),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_ai_repair_locators",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ai should not run")),
    )

    def _observe(*args, **kwargs):
        return {
            "url": page.url,
            "title": page.title_text,
            "guided_step": "",
            "guided_flow": {"primary_heading": state["primary_heading"]},
            "dialog_count": 1,
            "body_marker": state["body_marker"],
        }

    def _strict_click(locator, timeout_ms=None):
        clicked.append(locator.name)
        if locator.name == "raw-option":
            state["primary_heading"] = "Review Transaction: Invoice 7005359"
            state["body_marker"] = "Review Transaction: Invoice 7005359 Status Complete Payment Terms IMMEDIATE"

    monkeypatch.setattr(helpers_v2, "_act_current_guided_step", lambda current_page: "")
    monkeypatch.setattr(helpers_v2, "_act_guided_flow_state", lambda current_page: {"primary_heading": state["primary_heading"]})
    monkeypatch.setattr(helpers_v2, "_act_body_marker", lambda current_page: state["body_marker"])
    monkeypatch.setattr(helpers_v2, "_act_observe", _observe)
    monkeypatch.setattr(helpers_v2, "_act_strict_click", _strict_click)

    helpers_v2._act_select_adf_menu_panel_option(
        trigger,
        option,
        page,
        "Complete and Create Another",
        "Complete and Review",
        trigger_kind="title",
    )

    assert clicked == ["trigger", "raw-option"]


def test_select_search_trigger_option_fails_clearly_on_oracle_no_matches(monkeypatch) -> None:
    trigger = _SearchOptionLocator("search")
    option = _SearchOptionLocator("raw-option")
    page = _SearchOptionPage()
    experience_calls: list[str] = []
    ai_calls: list[str] = []

    helpers_v2._act_reset_strategy_tracking("select_search_trigger_option", "Candidate Selection Process")
    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_enter_search_value", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helpers_v2,
        "_act_oracle_searchselect_state",
        lambda current_page: {
            "open": True,
            "no_matches": True,
            "live_text": "No matches found",
            "filter_value": "su",
        },
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_experience_repair_locators",
        lambda *args, **kwargs: experience_calls.append("called") or [],
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_ai_repair_locators",
        lambda *args, **kwargs: ai_calls.append("called") or [],
    )
    monkeypatch.setattr(helpers_v2, "_act_locator_visible", lambda *args, **kwargs: False)

    with pytest.raises(
        RuntimeError,
        match='Oracle search-select "Candidate Selection Process" returned no matches for query "su"',
    ):
        helpers_v2._act_select_search_trigger_option(
            trigger,
            option,
            page,
            "Candidate Selection Process",
            "Supremo Candidate Selection",
            fill_value="su",
        )

    assert experience_calls == []
    assert ai_calls == []
    debug_trace = helpers_v2._ACT_CURRENT_STRATEGY["debug"]["select_search_trigger_option"]
    assert debug_trace["status"] == "failed"
    assert debug_trace["oracle_search_state"]["no_matches"] is True
    assert "no matches" in debug_trace["final_error"].lower()


def test_click_combobox_uses_oracle_keyboard_open_when_label_intercepts_pointer_events(monkeypatch) -> None:
    page = _NavigationPage()
    locator = _OracleKeyboardComboboxLocator()
    stored: dict[str, object] = {}

    helpers_v2._act_reset_strategy_tracking("click_combobox", "Why are you changing the")

    def observe(current_page, current_locator=None):
        expanded = bool(getattr(current_locator, "expanded", False))
        return {
            "url": page.url,
            "title": "Change Manager - Oracle Fusion Cloud Applications",
            "guided_step": "When and why",
            "guided_flow": {},
            "dialog_count": 0,
            "active_element": {"id": "expanded" if expanded else "collapsed"},
            "body_marker": "body",
            "target_value": "",
            "target_text": "",
            "target_visible": True,
            "target_meta": {"aria_expanded": "true" if expanded else "false"},
        }

    monkeypatch.setattr(
        helpers_v2,
        "_act_strict_click",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("label subtree intercepts pointer events")
        ),
    )
    monkeypatch.setattr(helpers_v2, "_act_observe", observe)
    monkeypatch.setattr(
        helpers_v2,
        "_act_extract_locator_metadata",
        lambda *args, **kwargs: {"class_name": "oj-searchselect-input", "role": "combobox"},
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_safe_locator_eval",
        lambda *args, **kwargs: {"has_oracle_host": True},
    )
    monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", lambda *args, **kwargs: pytest.fail("experience recovery should not run"))
    monkeypatch.setattr(helpers_v2, "_act_ai_repair_locators", lambda *args, **kwargs: pytest.fail("ai repair should not run"))
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: stored.update(kwargs))

    helpers_v2._act_click_combobox(locator, page, "Why are you changing the")

    assert locator.focused is True
    assert locator.pressed == [("ArrowDown", 3000)]
    assert helpers_v2._ACT_CURRENT_STRATEGY["recovery"] == {
        "source": "oracle_handler",
        "kind": "oracle_select_single_keyboard_open",
        "handler_name": "oracle_select_single_keyboard_open",
        "details": {
            "trigger_label": "Why are you changing the",
            "strategy_name": "oracle_select_single_arrowdown",
        },
    }
    debug_trace = helpers_v2._ACT_CURRENT_STRATEGY["debug"]["click_combobox"]
    assert debug_trace["oracle_select_single_keyboard_open"]["status"] == "validated"
    assert debug_trace["resolved_by"] == "oracle_select_single_keyboard_open"
    assert stored["status"] == "success"
    assert stored["postcondition_kind"] == "dialog_opened"


def test_click_combobox_marks_ai_interaction_failed_when_locator_does_not_validate(monkeypatch) -> None:
    page = _NavigationPage()
    recorded = _DateLocator("recorded")
    repaired = _DateLocator("repaired")
    observations = iter(
        [
            {"dialog_count": 0, "body_marker": "same"},
            {"dialog_count": 0, "body_marker": "same"},
            {"dialog_count": 0, "body_marker": "same"},
            {"dialog_count": 0, "body_marker": "same"},
            {"dialog_count": 0, "body_marker": "same"},
            {"dialog_count": 0, "body_marker": "same"},
        ]
    )

    helpers_v2._act_reset_strategy_tracking("click_combobox", "Search for people to add as")
    helpers_v2._ACT_CURRENT_STRATEGY["ai_interactions"] = [
        {
            "feature": "self_repair",
            "helper": "click_combobox",
            "label": "Search for people to add as",
            "status": "success",
            "response_strategy_count": 1,
        }
    ]

    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_act_strict_click", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_act_combobox_open_postcondition", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        helpers_v2,
        "_act_ai_repair_locators",
        lambda *args, **kwargs: [("ai_css_1", repaired, {"kind": "css", "selector": "#search"})],
    )

    with pytest.raises(RuntimeError, match='Unable to open combobox "Search for people to add as"'):
        helpers_v2._act_click_combobox(recorded, page, "Search for people to add as")

    interaction = helpers_v2._ACT_CURRENT_STRATEGY["ai_interactions"][-1]
    assert interaction["repair_outcome"] == "execution_failed"
    assert interaction["last_locator_strategy"] == "ai_css_1"
    assert interaction["postcondition_kind"] == "dialog_opened"
    assert interaction["postcondition_passed"] is False
    assert 'did not open combobox "Search for people to add as"' in interaction["repair_error"]


def test_click_with_candidates_records_debug_trace_on_failure(monkeypatch) -> None:
    page = _NavigationPage()
    locator = _DateLocator("recorded")

    helpers_v2._act_reset_strategy_tracking("click_text_target", "Edit Additional Information")

    monkeypatch.setattr(
        helpers_v2,
        "_act_observe",
        lambda *args, **kwargs: {
            "url": page.url,
            "title": "Work Area",
            "guided_step": "",
            "guided_flow": {},
            "dialog_count": 0,
            "active_element": {},
            "body_marker": "body",
            "target_value": "",
            "target_text": "Edit Additional Information",
            "target_visible": True,
            "target_meta": {"role": "link"},
        },
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_strict_click",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("strict click failed")),
    )
    monkeypatch.setattr(helpers_v2, "_act_try_expand_oracle_quick_actions", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_quick_action_exact_match", lambda *args, **kwargs: "")
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_home_search", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_guided_action_card", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        helpers_v2,
        "_act_execute_ai_repair_rounds",
        lambda **kwargs: (None, RuntimeError('AI strategy "text_option" did not satisfy postcondition')),
    )

    with pytest.raises(RuntimeError, match='Unable to click target "Edit Additional Information"'):
        helpers_v2._act_click_with_candidates(
            page,
            "Edit Additional Information",
            locator,
            "click_text_target",
            helpers_v2._act_generic_click_postcondition,
        )

    debug_trace = helpers_v2._ACT_CURRENT_STRATEGY["debug"]["click_with_candidates"]
    assert debug_trace["status"] == "failed"
    assert debug_trace["direct_attempt"]["status"] == "failed"
    assert debug_trace["experience_attempts"] == [{"status": "no_candidates"}]
    assert debug_trace["ai_repair"]["status"] == "failed"


def test_button_click_postcondition_rejects_focus_on_self_while_dialog_open() -> None:
    """A drawer-commit button (e.g. Oracle "Update") that only moves focus onto itself and wiggles
    body text while the drawer stays open did NOT commit -- the postcondition must be False."""
    before = {
        "url": "u", "title": "t", "guided_step": "", "guided_flow": {}, "dialog_count": 1,
        "active_element": {"tag": "input", "id": "ui-id-509|input"},
        "body_marker": "Quantity Required", "target_value": "", "target_text": "Update",
        "target_visible": True, "target_meta": {"tag": "button", "aria_label": "Update"},
    }
    after = dict(before)
    after["active_element"] = {"tag": "button", "aria_label": "Update"}
    after["body_marker"] = "Quantity Press Key"
    assert helpers_v2._act_button_click_postcondition(before, after) is False


def test_button_click_postcondition_accepts_drawer_close_and_in_page_body_change() -> None:
    before = {
        "url": "u", "title": "t", "guided_step": "", "guided_flow": {}, "dialog_count": 1,
        "active_element": {"tag": "button", "aria_label": "Update"},
        "body_marker": "b", "target_value": "", "target_text": "Update",
        "target_visible": True, "target_meta": {"tag": "button", "aria_label": "Update"},
    }
    # Drawer closed -> dialog_count change is a real effect.
    closed = dict(before)
    closed["dialog_count"] = 0
    assert helpers_v2._act_button_click_postcondition(before, closed) is True
    # In-page button (no dialog gating the view) whose body content changed is still a real effect.
    in_page = dict(before)
    in_page["dialog_count"] = 0
    after_in_page = dict(in_page)
    after_in_page["body_marker"] = "new content"
    assert helpers_v2._act_button_click_postcondition(in_page, after_in_page) is True


def test_adf_lov_query_results_populated_gates_on_lov_button_and_rows(monkeypatch) -> None:
    helpers_v2._act_reset_strategy_tracking("click_button_target", "Search")
    monkeypatch.setattr(
        helpers_v2, "_act_safe_locator_eval", lambda *a, **k: {"is_lov_query": True, "row_count": 5}
    )
    assert helpers_v2._act_adf_lov_query_results_populated(_NavigationPage(), object()) is True
    monkeypatch.setattr(
        helpers_v2, "_act_safe_locator_eval", lambda *a, **k: {"is_lov_query": True, "row_count": 0}
    )
    assert helpers_v2._act_adf_lov_query_results_populated(_NavigationPage(), object()) is False
    # A non-LOV button must never be loosened by this postcondition.
    monkeypatch.setattr(
        helpers_v2, "_act_safe_locator_eval", lambda *a, **k: {"is_lov_query": False}
    )
    assert helpers_v2._act_adf_lov_query_results_populated(_NavigationPage(), object()) is False


def test_click_button_target_accepts_adf_lov_search_when_results_populate(monkeypatch) -> None:
    """An ADF LOV 'Search' button populates a results table inside an af:popup that no generic
    signal sees (popup is not a dialog; rows are past the truncated body_marker). The click must be
    validated by the populated results grid -- not sent into recovery/AI. Regression for the AR
    Credit Memo Transaction Type search that visibly returned rows yet burned ~3min and failed."""
    page = _NavigationPage()
    helpers_v2._act_reset_strategy_tracking("click_button_target", "Search")
    obs = {
        "url": "u", "title": "t", "guided_step": "", "guided_flow": {}, "dialog_count": 0,
        "active_element": {"tag": "input", "id": "name"},
        "body_marker": "Create Transaction Credit Memo main header text",
        "target_value": "", "target_text": "Search", "target_visible": True,
        "target_meta": {"tag": "button", "id": "x::_afrLovInternalQueryId::search"},
    }
    # before == after -> the generic button postcondition sees no change (the real failure mode).
    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *a, **k: obs)
    monkeypatch.setattr(helpers_v2, "_act_strict_click", lambda *a, **k: None)
    monkeypatch.setattr(
        helpers_v2, "_act_adf_lov_query_results_populated", lambda page, locator: True
    )
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **k: None)

    def _no_recovery(*_a, **_k):
        raise AssertionError("recovery must not run; populated LOV results validate the click")

    monkeypatch.setattr(helpers_v2, "_act_try_expand_oracle_quick_actions", _no_recovery)
    monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", _no_recovery)

    def _no_ai(**_k):
        raise AssertionError("AI must not run; populated LOV results validate the click")

    monkeypatch.setattr(helpers_v2, "_act_execute_ai_repair_rounds", _no_ai)

    helpers_v2._act_click_button_target(object(), page, "Search")

    trace = helpers_v2._ACT_CURRENT_STRATEGY["debug"]["click_with_candidates"]
    assert trace["status"] == "success"
    assert trace["resolved_by"] == "strict"


def test_button_click_postcondition_accepts_own_state_toggle() -> None:
    """A split/menu button that toggles aria-expanded did something even with no navigation."""
    before = {
        "url": "u", "title": "t", "guided_step": "", "guided_flow": {}, "dialog_count": 0,
        "active_element": {"tag": "button", "aria_label": "Actions"},
        "body_marker": "b", "target_value": "", "target_text": "Actions", "target_visible": True,
        "target_meta": {"tag": "button", "aria_label": "Actions", "aria_expanded": "false"},
    }
    after = dict(before)
    after["target_meta"] = {"tag": "button", "aria_label": "Actions", "aria_expanded": "true"}
    assert helpers_v2._act_button_click_postcondition(before, after) is True


def test_button_no_commit_failure_only_for_actuated_drawer_button() -> None:
    obs = {
        "url": "u", "title": "t", "guided_step": "", "guided_flow": {}, "dialog_count": 1,
        "active_element": {"tag": "button", "aria_label": "Update"},
        "body_marker": "b", "target_value": "", "target_text": "Update",
        "target_visible": True, "target_meta": {"tag": "button", "aria_label": "Update"},
    }
    # Drawer-commit that did not close -> descriptor with the real reason.
    failure = helpers_v2._act_button_no_commit_failure(
        "click_button_target", None, "Update", obs, obs
    )
    assert failure is not None
    assert "never committed" in failure["reason"]
    # Not a plain button helper -> None (text targets keep generic behavior).
    assert (
        helpers_v2._act_button_no_commit_failure("click_text_target", None, "Update", obs, obs)
        is None
    )
    # No dialog open -> None (recovery may still be useful for a non-drawer button).
    no_dialog = dict(obs)
    no_dialog["dialog_count"] = 0
    assert (
        helpers_v2._act_button_no_commit_failure(
            "click_button_target", None, "Update", no_dialog, no_dialog
        )
        is None
    )
    # Focus did not land on the target (click may have hit an overlay) -> None.
    other_focus = dict(obs)
    other_focus["active_element"] = {"tag": "input", "id": "x"}
    assert (
        helpers_v2._act_button_no_commit_failure(
            "click_button_target", None, "Update", obs, other_focus
        )
        is None
    )


def test_click_button_target_fast_fails_when_drawer_does_not_close_without_ai(monkeypatch) -> None:
    """A button click that lands on its target but leaves the drawer open (Oracle "Update" not
    committing) fails fast with the real reason -- it must NOT grind Oracle recovery, experience,
    or AI self-repair on a control that already clicked. Regression guard for the RTV cascade where
    a falsely-passed Update masked the real failure at the following step."""
    page = _NavigationPage()
    helpers_v2._act_reset_strategy_tracking("click_button_target", "Update")

    observation = {
        "url": "u", "title": "Oracle", "guided_step": "", "guided_flow": {}, "dialog_count": 1,
        "active_element": {"tag": "button", "aria_label": "Update"},
        "body_marker": "Return line", "target_value": "", "target_text": "Update",
        "target_visible": True, "target_meta": {"tag": "button", "aria_label": "Update"},
    }
    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *a, **k: observation)
    monkeypatch.setattr(helpers_v2, "_act_strict_click", lambda *a, **k: None)
    # The async-effect settle finds no change here (the drawer genuinely never closes), so collapse
    # it to an identity to keep this test about the no-commit verdict, not the wait.
    monkeypatch.setattr(
        helpers_v2,
        "_act_settle_click_postcondition",
        lambda page, locator, postcondition, before, after: after,
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_collect_validation_messages",
        lambda *a, **k: ["Subinventory: Select a value."],
    )

    def _no_recovery(*_a, **_k):
        raise AssertionError("recovery cascade must not run for a button that already clicked")

    monkeypatch.setattr(helpers_v2, "_act_try_expand_oracle_quick_actions", _no_recovery)
    monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", _no_recovery)

    def _no_ai(**_k):
        raise AssertionError("AI self-repair must not run for a button that already clicked")

    monkeypatch.setattr(helpers_v2, "_act_execute_ai_repair_rounds", _no_ai)

    with pytest.raises(RuntimeError) as excinfo:
        helpers_v2._act_click_button_target(object(), page, "Update")

    message = str(excinfo.value)
    assert "never committed" in message
    assert "Subinventory: Select a value." in message
    trace = helpers_v2._ACT_CURRENT_STRATEGY["debug"]["click_with_candidates"]
    assert trace["status"] == "failed"
    assert trace["direct_attempt"]["status"] == "no_commit"
    assert trace["direct_attempt"]["validation_messages"] == ["Subinventory: Select a value."]


def test_retry_strict_click_after_oracle_ppr_skips_when_page_is_idle(monkeypatch) -> None:
    """When the page is NOT busy, a strict-click failure is a real binding problem, not PPR
    timing -- the bounded retry must skip instantly (no extra wait, no re-click) so recovery
    runs immediately."""
    monkeypatch.setattr(helpers_v2, "_act_busy_indicator_count", lambda *a, **k: 0)

    def _no_click(*_a, **_k):
        raise AssertionError("must not re-click the locator when the page is idle")

    monkeypatch.setattr(helpers_v2, "_act_strict_click", _no_click)
    before = {"body_marker": "x", "dialog_count": 0}
    result = helpers_v2._act_retry_strict_click_after_oracle_ppr(
        _NavigationPage(), "View all actions", object(), lambda b, a: True, before
    )
    assert result is None


def test_retry_strict_click_after_oracle_ppr_returns_none_when_locator_never_actionable(
    monkeypatch,
) -> None:
    # Busy at the gate, then cleared, so the settle loop exits promptly.
    busy_vals = iter([1, 0, 0])
    monkeypatch.setattr(
        helpers_v2, "_act_busy_indicator_count", lambda *a, **k: next(busy_vals, 0)
    )
    monkeypatch.setattr(helpers_v2, "_act_locator_is_actionable", lambda *a, **k: False)

    def _no_click(*_a, **_k):
        raise AssertionError("must not re-click when the locator never becomes actionable")

    monkeypatch.setattr(helpers_v2, "_act_strict_click", _no_click)
    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *a, **k: {"body_marker": "y"})
    result = helpers_v2._act_retry_strict_click_after_oracle_ppr(
        _NavigationPage(), "View all actions", object(), lambda b, a: True, {"body_marker": "x"}
    )
    assert result is None


def test_click_text_target_retries_exact_locator_after_oracle_ppr_settles(monkeypatch) -> None:
    """A click whose strict locator times out only because Oracle is still rendering (PPR) gets
    the EXACT recorded locator re-tried once the page settles -- BEFORE any alternative-locator
    recovery -- so we don't mis-target on a half-rendered page. Regression guard for the 'View all
    actions' springboard link that loads late on a cold run."""
    page = _NavigationPage()
    helpers_v2._act_reset_strategy_tracking("click_text_target", "View all actions")

    calls = {"strict": 0}
    before_obs = {
        "url": "u", "title": "t", "guided_step": "", "guided_flow": {}, "dialog_count": 0,
        "active_element": {}, "body_marker": "loading", "target_value": "", "target_text": "",
        "target_visible": False, "target_meta": {},
    }
    after_obs = dict(before_obs)
    after_obs["body_marker"] = "View all actions panel open"

    seq = {"n": 0}

    def _observe(*_a, **_k):
        seq["n"] += 1
        return before_obs if seq["n"] == 1 else after_obs

    monkeypatch.setattr(helpers_v2, "_act_observe", _observe)

    def _strict(*_a, **_k):
        calls["strict"] += 1
        if calls["strict"] == 1:
            raise RuntimeError("Locator.wait_for: Timeout 30000ms exceeded.")

    monkeypatch.setattr(helpers_v2, "_act_strict_click", _strict)
    # busy at the gate, then cleared -> emulates PPR finishing.
    busy_vals = iter([2, 0, 0, 0])
    monkeypatch.setattr(
        helpers_v2, "_act_busy_indicator_count", lambda *a, **k: next(busy_vals, 0)
    )
    monkeypatch.setattr(helpers_v2, "_act_locator_is_actionable", lambda *a, **k: True)
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **k: None)

    def _no_recovery(*_a, **_k):
        raise AssertionError("alternative-locator recovery must not run; PPR retry succeeds first")

    monkeypatch.setattr(helpers_v2, "_act_try_expand_oracle_quick_actions", _no_recovery)
    monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", _no_recovery)

    def _no_ai(**_k):
        raise AssertionError("AI self-repair must not run; PPR retry succeeds first")

    monkeypatch.setattr(helpers_v2, "_act_execute_ai_repair_rounds", _no_ai)

    helpers_v2._act_click_with_candidates(
        page,
        "View all actions",
        object(),
        "click_text_target",
        helpers_v2._act_generic_click_postcondition,
    )

    trace = helpers_v2._ACT_CURRENT_STRATEGY["debug"]["click_with_candidates"]
    assert trace["status"] == "success"
    assert trace["resolved_by"] == "oracle_ppr_settle_retry"
    assert calls["strict"] == 2


def test_click_with_candidates_waits_for_async_navigation_before_no_commit(monkeypatch) -> None:
    """A navigation button (e.g. 'Create Return') whose effect (URL change) lands a beat AFTER the
    click must NOT be declared a no-commit failure. The bounded postcondition settle gives the
    async effect time to manifest. Regression guard for the Create Return false fast-fail, where a
    pre-existing ambient popup (dialog_count=1) + focus-on-self looked like a stuck drawer."""
    page = _NavigationPage()
    helpers_v2._act_reset_strategy_tracking("click_button_target", "Create Return")

    base = {
        "url": "u0", "title": "t", "guided_step": "", "guided_flow": {}, "dialog_count": 1,
        "active_element": {"tag": "button", "aria_label": "Create Return"},
        "body_marker": "Returns", "target_value": "", "target_text": "Create Return",
        "target_visible": True, "target_meta": {"tag": "button", "aria_label": "Create Return"},
    }
    seq = {"n": 0}

    def _observe(*_a, **_k):
        seq["n"] += 1
        if seq["n"] <= 2:  # before + immediate after: navigation has not landed yet
            return dict(base)
        navigated = dict(base)
        navigated["url"] = "u1-create-return-page"
        return navigated

    monkeypatch.setattr(helpers_v2, "_act_observe", _observe)
    monkeypatch.setattr(helpers_v2, "_act_strict_click", lambda *a, **k: None)
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **k: None)

    def _no_no_commit(*_a, **_k):
        raise AssertionError("no-commit must not fire while an async navigation is still settling")

    monkeypatch.setattr(helpers_v2, "_act_button_no_commit_failure", _no_no_commit)

    helpers_v2._act_click_with_candidates(
        page,
        "Create Return",
        object(),
        "click_button_target",
        helpers_v2._act_button_click_postcondition,
    )

    trace = helpers_v2._ACT_CURRENT_STRATEGY["debug"]["click_with_candidates"]
    assert trace["status"] == "success"
    assert trace["resolved_by"] == "strict"


def test_control_family_recognizes_menu_panel_helpers() -> None:
    assert helpers_v2._act_control_family("select_adf_menu_panel_option") == "menu_panel"


def test_locator_value_and_text_use_fast_element_handle() -> None:
    locator = _FastSnapshotLocator()

    assert helpers_v2._act_locator_value(locator) == "fast-value"
    assert helpers_v2._act_locator_text(locator) == "fast text"
    assert locator.timeout is not None


def test_launch_chromium_defaults_to_headed_when_env_missing(monkeypatch) -> None:
    monkeypatch.delenv("ACT_BROWSER_PROVIDER", raising=False)
    monkeypatch.delenv("ACT_IS_LOCAL_ENV", raising=False)
    monkeypatch.delenv("STEEL_API_KEY", raising=False)
    monkeypatch.delenv("ACT_HEADLESS", raising=False)
    playwright = _FakePlaywright()

    result = helpers_v2._act_launch_chromium(playwright)

    assert result["headless"] is False


def test_launch_chromium_defaults_to_steel_when_api_key_is_present(monkeypatch) -> None:
    monkeypatch.delenv("ACT_BROWSER_PROVIDER", raising=False)
    monkeypatch.delenv("ACT_IS_LOCAL_ENV", raising=False)
    monkeypatch.setenv("STEEL_API_KEY", "steel-key")
    monkeypatch.delenv("ACT_HEADLESS", raising=False)
    _FakeSteelClient.reset()
    helpers_v2._ACT_STEEL_BROWSER_SESSION_IDS.clear()
    helpers_v2._ACT_STEEL_RELEASE_SESSION_IDS.clear()
    monkeypatch.setitem(sys.modules, "steel", SimpleNamespace(Steel=_FakeSteelClient))
    playwright = _FakePlaywright()

    result = helpers_v2._act_launch_chromium(playwright)

    assert playwright.chromium.launch_kwargs is None
    assert playwright.chromium.connect_calls == [result]
    assert result["url"].startswith("wss://connect.steel.dev?")
    assert "apiKey=steel-key" in result["url"]
    assert "sessionId=steel-session-created" in result["url"]
    assert _FakeSteelClient.instances[0].sessions.created == [{"api_timeout": 900000, "headless": False}]


def test_launch_chromium_uses_local_when_act_is_local_env(monkeypatch) -> None:
    monkeypatch.delenv("ACT_BROWSER_PROVIDER", raising=False)
    monkeypatch.setenv("ACT_IS_LOCAL_ENV", "true")
    monkeypatch.setenv("STEEL_API_KEY", "steel-key")
    monkeypatch.delenv("ACT_HEADLESS", raising=False)
    playwright = _FakePlaywright()

    result = helpers_v2._act_launch_chromium(playwright)

    assert result["headless"] is False
    assert playwright.chromium.launch_kwargs is not None
    assert playwright.chromium.connect_calls == []


def test_launch_chromium_explicit_steel_overrides_local_env(monkeypatch) -> None:
    monkeypatch.setenv("ACT_BROWSER_PROVIDER", "steel")
    monkeypatch.setenv("ACT_IS_LOCAL_ENV", "true")
    monkeypatch.setenv("STEEL_API_KEY", "steel-key")
    monkeypatch.delenv("ACT_HEADLESS", raising=False)
    _FakeSteelClient.reset()
    helpers_v2._ACT_STEEL_BROWSER_SESSION_IDS.clear()
    helpers_v2._ACT_STEEL_RELEASE_SESSION_IDS.clear()
    monkeypatch.setitem(sys.modules, "steel", SimpleNamespace(Steel=_FakeSteelClient))
    playwright = _FakePlaywright()

    result = helpers_v2._act_launch_chromium(playwright)

    assert playwright.chromium.launch_kwargs is None
    assert result["url"].startswith("wss://connect.steel.dev?")
    assert "apiKey=steel-key" in result["url"]


def test_runtime_exports_legacy_failure_hooks_for_generated_wrapper() -> None:
    assert "_act_capture_failure" in helpers_v2.__all__
    assert "_act_write_diagnostics" in helpers_v2.__all__
    assert "_act_wait_after_interaction" in helpers_v2.__all__
    assert "_act_tracked_raw_action" in helpers_v2.__all__


def test_try_oracle_home_search_uses_search_box_before_ai(monkeypatch) -> None:
    page = _OracleHomePage()
    clicked: list[str] = []

    monkeypatch.setattr(helpers_v2, "_act_page_signature", lambda *args, **kwargs: {"path_hint": "/fscmUI/faces/FuseWelcome"})
    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: {"dialog_count": 0})
    monkeypatch.setattr(helpers_v2, "_act_locator_is_actionable", lambda locator, timeout_ms=None: locator in {page.search, page.result})
    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda strategy: clicked.append(strategy))

    def _strict_click(locator, timeout_ms=None):
        clicked.append(locator.name)

    def _strict_fill(locator, value, timeout_ms=None):
        locator.filled.append(value)

    monkeypatch.setattr(helpers_v2, "_act_strict_click", _strict_click)
    monkeypatch.setattr(helpers_v2, "_act_strict_fill", _strict_fill)

    succeeded = helpers_v2._act_try_oracle_home_search(
        page,
        "Promote and Change Position",
        lambda before, after: "result" in clicked,
    )

    assert succeeded is True
    assert page.search.filled == ["Promote and Change Position"]
    assert "oracle_home_search" in clicked
    assert "result" in clicked


def test_wait_for_date_icon_allows_redwood_page_to_finish_rendering(monkeypatch) -> None:
    icon = _DateLocator("icon")
    fallback = _DateLocator("fallback")
    page = _DatePage(fallback)
    attempts = {"count": 0}

    monkeypatch.setattr(helpers_v2, "_act_safe_page_eval", lambda *args, **kwargs: "complete")
    monkeypatch.setattr(helpers_v2, "_act_busy_indicator_count", lambda *args, **kwargs: 0)

    def _is_actionable(locator, timeout_ms=None):
        if locator is icon:
            attempts["count"] += 1
            return attempts["count"] >= 3
        return False

    monkeypatch.setattr(helpers_v2, "_act_locator_is_actionable", _is_actionable)

    resolved = helpers_v2._act_wait_for_date_icon(icon, page, "Select Date.")

    assert resolved is icon
    assert page.waits


def test_pick_date_uses_date_icon_fallback_and_waits_for_postcondition(monkeypatch) -> None:
    icon = _DateLocator("icon")
    fallback = _DateLocator("fallback")
    day = _DateLocator("day")
    page = _DatePage(fallback)
    clicks: list[str] = []
    strategies: list[str] = []
    settled: list[bool] = []
    observations = iter(
        [
            {"dialog_count": 1, "body_marker": "before"},
            {"dialog_count": 0, "body_marker": "after"},
        ]
    )

    monkeypatch.setattr(helpers_v2, "_act_safe_page_eval", lambda *args, **kwargs: "complete")
    monkeypatch.setattr(helpers_v2, "_act_busy_indicator_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        helpers_v2,
        "_act_locator_is_actionable",
        lambda locator, timeout_ms=None: locator in {fallback, day},
    )
    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda strategy: strategies.append(strategy))
    monkeypatch.setattr(helpers_v2, "_act_strict_click", lambda locator, timeout_ms=None: clicks.append(locator.name))
    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_act_wait_for_field_processing", lambda *args, **kwargs: settled.append(True) or None)

    helpers_v2._act_pick_date_via_icon(icon, day, page, "Select Date.", "28")

    assert clicks == ["fallback", "day"]
    assert "date_attr_match" in strategies
    assert "day_select" in strategies
    assert settled == [True]


def test_try_oracle_guided_action_card_clicks_card_and_detects_switch_change(monkeypatch) -> None:
    card = _ActionCardLocator("managers-card")
    page = _ActionCardPage(card)
    strategies: list[str] = []

    monkeypatch.setattr(helpers_v2, "_act_page_signature", lambda *args, **kwargs: {"surface_type": "guided_process"})
    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: {"dialog_count": 0, "body_marker": "same"})
    monkeypatch.setattr(helpers_v2, "_act_locator_is_actionable", lambda locator, timeout_ms=None: locator in {card, card.switch})
    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda strategy: strategies.append(strategy))
    monkeypatch.setattr(
        helpers_v2,
        "_act_extract_locator_metadata",
        lambda locator: {"aria_checked": locator.aria_checked} if locator is card.switch else {},
    )

    def _strict_click(locator, timeout_ms=None):
        if locator is card:
            card.switch.aria_checked = "true"

    monkeypatch.setattr(helpers_v2, "_act_strict_click", _strict_click)

    succeeded = helpers_v2._act_try_oracle_guided_action_card(
        page,
        "Managers Add or remove",
        lambda before, after: False,
    )

    assert succeeded is True
    assert "oracle_action_card" in strategies


def test_click_with_candidates_uses_oracle_guided_action_card_before_ai(monkeypatch) -> None:
    page = object()
    locator = _DateLocator("primary")
    recovery: dict[str, object] = {}

    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: {"dialog_count": 0})
    monkeypatch.setattr(helpers_v2, "_act_strict_click", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("direct failed")))
    monkeypatch.setattr(helpers_v2, "_act_try_expand_oracle_quick_actions", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_home_search", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_guided_action_card", lambda *args, **kwargs: True)
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: recovery.setdefault("experience", kwargs))
    monkeypatch.setattr(helpers_v2, "_act_set_recovery_record", lambda source, kind, handler_name, details=None: recovery.update({"source": source, "kind": kind, "handler_name": handler_name, "details": details or {}}))
    monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("experience should not run")))
    monkeypatch.setattr(helpers_v2, "_act_ai_repair_locators", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ai should not run")))

    helpers_v2._act_click_with_candidates(page, "Managers Add or remove", locator, "click_button_target", lambda before, after: False)

    assert recovery["handler_name"] == "oracle_guided_action_card"
    assert recovery["kind"] == "guided_action_card"


def test_click_with_candidates_uses_oracle_quick_action_exact_match_on_strict_link_ambiguity(monkeypatch) -> None:
    page = _OracleQuickActionPage()
    locator = _DateLocator("primary")
    clicked: list[str] = []
    recovery: dict[str, object] = {}

    def _strict_click(target, timeout_ms=None):
        if target is locator:
            raise RuntimeError(
                'Locator.wait_for: Error: strict mode violation: get_by_role("link", name="Promote and Change Position") resolved to 2 elements'
            )
        clicked.append(target.name)

    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: {"clicked": tuple(clicked)})
    monkeypatch.setattr(helpers_v2, "_act_strict_click", _strict_click)
    monkeypatch.setattr(
        helpers_v2,
        "_act_locator_is_actionable",
        lambda target, timeout_ms=None: target in {page.quick_action, page.role_exact, page.text_exact},
    )
    monkeypatch.setattr(helpers_v2, "_act_try_expand_oracle_quick_actions", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_home_search", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_guided_action_card", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: recovery.setdefault("experience", kwargs))
    monkeypatch.setattr(
        helpers_v2,
        "_act_set_recovery_record",
        lambda source, kind, handler_name, details=None: recovery.update(
            {"source": source, "kind": kind, "handler_name": handler_name, "details": details or {}}
        ),
    )
    monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("experience should not run")))
    monkeypatch.setattr(helpers_v2, "_act_ai_repair_locators", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ai should not run")))

    helpers_v2._act_click_with_candidates(
        page,
        "Promote and Change Position",
        locator,
        "click_text_target",
        lambda before, after: before.get("clicked") != after.get("clicked"),
    )

    assert clicked == ["quick_action"]
    assert recovery["handler_name"] == "oracle_quick_action_exact_match"
    assert recovery["kind"] == "quick_action_exact_match"
    assert recovery["details"] == {"label": "Promote and Change Position", "strategy_name": "oracle_quick_action_exact_link"}


def test_click_with_candidates_uses_oracle_quick_action_exact_match_after_expand_for_button_locator(monkeypatch) -> None:
    page = _OracleQuickActionPage()
    locator = _DateLocator("primary")
    clicked: list[str] = []
    recovery: dict[str, object] = {}

    def _strict_click(target, timeout_ms=None):
        if target is locator:
            raise RuntimeError(
                'Locator.wait_for: Timeout 3000ms exceeded.\n'
                'Call log:\n'
                '  - waiting for get_by_role("button", name="View Accounting") to be visible'
            )
        clicked.append(target.name)

    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: {"clicked": tuple(clicked)})
    monkeypatch.setattr(helpers_v2, "_act_strict_click", _strict_click)
    monkeypatch.setattr(
        helpers_v2,
        "_act_locator_is_actionable",
        lambda target, timeout_ms=None: target is page.quick_action,
    )
    monkeypatch.setattr(helpers_v2, "_act_try_expand_oracle_quick_actions", lambda *args, **kwargs: True)
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_home_search", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_guided_action_card", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: recovery.setdefault("experience", kwargs))
    monkeypatch.setattr(
        helpers_v2,
        "_act_set_recovery_record",
        lambda source, kind, handler_name, details=None: recovery.update(
            {"source": source, "kind": kind, "handler_name": handler_name, "details": details or {}}
        ),
    )
    monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("experience should not run")))
    monkeypatch.setattr(helpers_v2, "_act_ai_repair_locators", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ai should not run")))

    helpers_v2._act_click_with_candidates(
        page,
        "View Accounting",
        locator,
        "click_button_target",
        lambda before, after: before.get("clicked") != after.get("clicked"),
    )

    assert clicked == ["quick_action"]
    assert recovery["handler_name"] == "oracle_quick_action_exact_match"
    assert recovery["kind"] == "quick_action_exact_match"
    assert recovery["details"] == {"label": "View Accounting", "strategy_name": "oracle_quick_action_exact_link"}


def test_click_with_candidates_uses_oracle_notification_badge_before_ai(monkeypatch) -> None:
    page = _OracleNotificationBadgePage()
    locator = _DateLocator("primary")
    clicked: list[str] = []
    recovery: dict[str, object] = {}

    def _strict_click(target, timeout_ms=None):
        if target is locator:
            raise RuntimeError(
                'Locator.wait_for: Timeout 30000ms exceeded.\n'
                'Call log:\n'
                '  - waiting for get_by_role("link", name="Notifications (10 unread)") to be visible'
            )
        clicked.append(target.name)

    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: {"clicked": tuple(clicked)})
    monkeypatch.setattr(helpers_v2, "_act_strict_click", _strict_click)
    monkeypatch.setattr(
        helpers_v2,
        "_act_locator_is_actionable",
        lambda target, timeout_ms=None: target is page.notification_role,
    )
    monkeypatch.setattr(helpers_v2, "_act_try_expand_oracle_quick_actions", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_quick_action_exact_match", lambda *args, **kwargs: "")
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_home_search", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("home search should not run")))
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_guided_action_card", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("guided action card should not run")))
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: recovery.setdefault("experience", kwargs))
    monkeypatch.setattr(
        helpers_v2,
        "_act_set_recovery_record",
        lambda source, kind, handler_name, details=None: recovery.update(
            {"source": source, "kind": kind, "handler_name": handler_name, "details": details or {}}
        ),
    )
    monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("experience should not run")))
    monkeypatch.setattr(helpers_v2, "_act_ai_repair_locators", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ai should not run")))

    helpers_v2._act_click_with_candidates(
        page,
        "Notifications (10 unread)",
        locator,
        "click_text_target",
        lambda before, after: before.get("clicked") != after.get("clicked"),
    )

    assert clicked == ["notification_role"]
    assert recovery["handler_name"] == "oracle_notification_badge"
    assert recovery["kind"] == "notification_badge"
    assert recovery["details"] == {"label": "Notifications (10 unread)", "strategy_name": "oracle_notification_badge_role"}


def test_click_with_candidates_uses_oracle_recorded_button_context_before_ai(monkeypatch) -> None:
    page = _RecordedButtonContextPage()
    locator = _DateLocator("primary")
    clicked: list[str] = []
    recovery: dict[str, object] = {}

    def _strict_click(target, timeout_ms=None):
        if target is locator:
            raise RuntimeError(
                'Locator.wait_for: Error: strict mode violation: get_by_role("button", name="Approve") resolved to 9 elements'
            )
        clicked.append(target.name)

    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: {"clicked": tuple(clicked)})
    monkeypatch.setattr(helpers_v2, "_act_strict_click", _strict_click)
    monkeypatch.setattr(
        helpers_v2,
        "_act_capture_locator_context",
        lambda *args, **kwargs: {
            "id": "_FOpt1:_FOr1:0:_FONSr2:0:MAnt2:0:up1:UPsp1:r1:0:lv4:2:cb2",
            "title": "Approve Job Requisition Medical Office Administrator - 1269 Requires Approval",
            "class_name": "homebutton-primary x7j p_AFTextOnly",
        },
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_locator_is_actionable",
        lambda target, timeout_ms=None: target is page.title_button,
    )
    monkeypatch.setattr(helpers_v2, "_act_try_expand_oracle_quick_actions", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_quick_action_exact_match", lambda *args, **kwargs: "")
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_notification_badge", lambda *args, **kwargs: "")
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_home_search", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("home search should not run")))
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_guided_action_card", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("guided action card should not run")))
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: recovery.setdefault("experience", kwargs))
    monkeypatch.setattr(
        helpers_v2,
        "_act_set_recovery_record",
        lambda source, kind, handler_name, details=None: recovery.update(
            {"source": source, "kind": kind, "handler_name": handler_name, "details": details or {}}
        ),
    )
    monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("experience should not run")))
    monkeypatch.setattr(helpers_v2, "_act_ai_repair_locators", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ai should not run")))

    helpers_v2._act_click_with_candidates(
        page,
        "Approve",
        locator,
        "click_button_target",
        lambda before, after: before.get("clicked") != after.get("clicked"),
    )

    assert clicked == ["title_button"]
    assert recovery["handler_name"] == "oracle_recorded_button_context"
    assert recovery["kind"] == "recorded_button_context"
    assert recovery["details"] == {"label": "Approve", "strategy_name": "oracle_recorded_button_title"}


def test_click_with_candidates_skips_optional_oracle_warning_ok_when_dialog_is_absent(monkeypatch) -> None:
    page = SimpleNamespace(url="https://example.com/fscmUI/faces/ap/invoice")
    locator = _DateLocator("ok")
    recovery: dict[str, object] = {}
    stored: dict[str, object] = {}

    monkeypatch.setattr(
        helpers_v2,
        "_act_observe",
        lambda *args, **kwargs: {"dialog_count": 0, "title": "Create Invoice - Oracle Fusion Cloud Applications"},
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_strict_click",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError(
                'Locator.wait_for: Timeout 3000ms exceeded.\n'
                'Call log:\n'
                '  - waiting for get_by_role("button", name="OK") to be visible'
            )
        ),
    )
    monkeypatch.setattr(helpers_v2, "_act_locator_is_actionable", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_try_expand_oracle_quick_actions", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_home_search", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_guided_action_card", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        helpers_v2,
        "_act_oracle_warning_dialog_state",
        lambda *args, **kwargs: {
            "dialog_count": 0,
            "warning_visible": False,
            "warning_title": "",
            "warning_text": "",
            "ok_button_visible": False,
            "any_ok_button_visible": False,
        },
    )
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: stored.update(kwargs))
    monkeypatch.setattr(
        helpers_v2,
        "_act_set_recovery_record",
        lambda source, kind, handler_name, details=None: recovery.update(
            {"source": source, "kind": kind, "handler_name": handler_name, "details": details or {}}
        ),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_experience_repair_locators",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("experience should not run")),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_ai_repair_locators",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ai should not run")),
    )

    helpers_v2._act_click_with_candidates(
        page,
        "OK",
        locator,
        "click_button_target",
        lambda before, after: False,
    )

    assert recovery["handler_name"] == "oracle_optional_warning_ok_absent"
    assert recovery["kind"] == "optional_warning_ok_absent"
    assert recovery["details"] == {
        "label": "OK",
        "surface_type": "adf_form",
        "dialog_count": 0,
        "reason": "optional_warning_dialog_not_present",
    }
    assert stored["status"] == "success"
    assert stored["postcondition_kind"] == "dialog_absent"


def test_click_with_candidates_keeps_failing_ok_when_warning_dialog_is_visible(monkeypatch) -> None:
    page = _OracleHomePage()
    locator = _DateLocator("ok")
    stored: dict[str, object] = {}

    monkeypatch.setattr(
        helpers_v2,
        "_act_observe",
        lambda *args, **kwargs: {"dialog_count": 1, "title": "Create Invoice - Oracle Fusion Cloud Applications"},
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_strict_click",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError(
                'Locator.wait_for: Timeout 3000ms exceeded.\n'
                'Call log:\n'
                '  - waiting for get_by_role("button", name="OK") to be visible'
            )
        ),
    )
    monkeypatch.setattr(helpers_v2, "_act_locator_is_actionable", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_try_expand_oracle_quick_actions", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_home_search", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_guided_action_card", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        helpers_v2,
        "_act_oracle_warning_dialog_state",
        lambda *args, **kwargs: {
            "dialog_count": 1,
            "warning_visible": True,
            "warning_title": "Warning",
            "warning_text": "Verify that this is not a duplicate.",
            "ok_button_visible": True,
            "any_ok_button_visible": True,
        },
    )
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: stored.update(kwargs))
    monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", lambda *args, **kwargs: [])
    monkeypatch.setattr(helpers_v2, "_act_ai_repair_locators", lambda *args, **kwargs: [])

    with pytest.raises(RuntimeError, match='Unable to click target "OK"'):
        helpers_v2._act_click_with_candidates(
            page,
            "OK",
            locator,
            "click_button_target",
            lambda before, after: False,
        )

    assert stored == {}


def test_click_with_candidates_dismisses_visible_oracle_warning_dialog_before_ai(monkeypatch) -> None:
    page = _WarningDialogPage()
    locator = _DateLocator("apply")
    recovery: dict[str, object] = {}
    stored: dict[str, object] = {}
    clicked: list[str] = []
    warning_states = iter(
        [
            {
                "dialog_count": 1,
                "warning_visible": True,
                "warning_title": "Warning",
                "warning_text": "The payment terms for this invoice differ from the payment terms on the purchase order.",
                "ok_button_visible": True,
                "any_ok_button_visible": True,
                "ok_button_id": "warning-ok-id",
                "close_button_id": "",
            },
            {
                "dialog_count": 0,
                "warning_visible": False,
                "warning_title": "",
                "warning_text": "",
                "ok_button_visible": False,
                "any_ok_button_visible": False,
                "ok_button_id": "",
                "close_button_id": "",
            },
        ]
    )

    monkeypatch.setattr(
        helpers_v2,
        "_act_observe",
        lambda *args, **kwargs: {
            "dialog_count": 0,
            "title": "Create Invoice - Oracle Fusion Cloud Applications",
            "active_element": {"id": "warning-ok-id"},
        },
    )

    def _strict_click(target, timeout_ms=None):
        clicked.append(target.name)
        if target.name == "apply":
            raise RuntimeError(
                "Locator.click: Timeout 30000ms exceeded.\n"
                "Call log:\n"
                "  - <div class=\"AFModalGlassPane\"></div> intercepts pointer events"
            )

    monkeypatch.setattr(helpers_v2, "_act_strict_click", _strict_click)
    monkeypatch.setattr(
        helpers_v2,
        "_act_locator_is_actionable",
        lambda locator, timeout_ms=None: getattr(locator, "name", "") == 'locator:[id="warning-ok-id"]',
    )
    monkeypatch.setattr(helpers_v2, "_act_try_expand_oracle_quick_actions", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_home_search", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_guided_action_card", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_notification_badge", lambda *args, **kwargs: "")
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_quick_action_exact_match", lambda *args, **kwargs: "")
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_recorded_button_context", lambda *args, **kwargs: "")
    monkeypatch.setattr(helpers_v2, "_act_oracle_warning_dialog_state", lambda *args, **kwargs: next(warning_states))
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: stored.update(kwargs))
    monkeypatch.setattr(
        helpers_v2,
        "_act_set_recovery_record",
        lambda source, kind, handler_name, details=None: recovery.update(
            {"source": source, "kind": kind, "handler_name": handler_name, "details": details or {}}
        ),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_experience_repair_locators",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("experience should not run")),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_ai_repair_locators",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ai should not run")),
    )

    helpers_v2._act_click_with_candidates(
        page,
        "Apply",
        locator,
        "click_button_target",
        lambda before, after: False,
    )

    assert clicked == ["apply", 'locator:[id="warning-ok-id"]']
    assert recovery["handler_name"] == "oracle_warning_dialog_dismiss"
    assert recovery["kind"] == "warning_dialog_dismiss"
    assert recovery["details"]["strategy_name"] == "oracle_warning_dialog_ok"
    assert stored["status"] == "success"
    assert stored["postcondition_kind"] == "warning_dialog_dismissed"


def test_click_with_candidates_does_not_skip_non_ok_button_when_dialog_is_absent(monkeypatch) -> None:
    page = _OracleHomePage()
    locator = _DateLocator("save")
    stored: dict[str, object] = {}

    monkeypatch.setattr(
        helpers_v2,
        "_act_observe",
        lambda *args, **kwargs: {"dialog_count": 0, "title": "Create Invoice - Oracle Fusion Cloud Applications"},
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_strict_click",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError(
                'Locator.wait_for: Timeout 3000ms exceeded.\n'
                'Call log:\n'
                '  - waiting for get_by_role("button", name="Save") to be visible'
            )
        ),
    )
    monkeypatch.setattr(helpers_v2, "_act_locator_is_actionable", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_try_expand_oracle_quick_actions", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_home_search", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_guided_action_card", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        helpers_v2,
        "_act_oracle_warning_dialog_state",
        lambda *args, **kwargs: {
            "dialog_count": 0,
            "warning_visible": False,
            "warning_title": "",
            "warning_text": "",
            "ok_button_visible": False,
            "any_ok_button_visible": False,
        },
    )
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **kwargs: stored.update(kwargs))
    monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", lambda *args, **kwargs: [])
    monkeypatch.setattr(helpers_v2, "_act_ai_repair_locators", lambda *args, **kwargs: [])

    with pytest.raises(RuntimeError, match='Unable to click target "Save"'):
        helpers_v2._act_click_with_candidates(
            page,
            "Save",
            locator,
            "click_button_target",
            lambda before, after: False,
        )

    assert stored == {}


def test_click_table_row_requires_selection_postcondition(monkeypatch) -> None:
    page = _OracleQuickActionPage()
    locator = _DateLocator("row")
    observed_states = iter(
        [
            {"target_meta": {"aria_selected": "false", "class_name": "oj-table-body-row"}, "body_marker": "same"},
            {"target_meta": {"aria_selected": "true", "class_name": "oj-table-body-row oj-selected"}, "body_marker": "same"},
        ]
    )
    clicked: list[tuple[object, int | None]] = []

    monkeypatch.setattr(helpers_v2, "_act_register_page", lambda current_page: current_page)
    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: next(observed_states))
    monkeypatch.setattr(
        helpers_v2,
        "_act_strict_click",
        lambda target, timeout_ms=None: clicked.append((target, timeout_ms)),
    )

    helpers_v2._act_click_table_row(locator, page, "Academic")

    assert clicked == [(locator, None)]
    assert page.waits == [250]


def test_click_table_field_accepts_already_focused_target(monkeypatch) -> None:
    page = _OracleQuickActionPage()
    locator = _DateLocator("field")
    observed_states = iter(
        [
            {
                "active_element": {"id": "desc-input", "name": "description", "tag": "input"},
                "target_meta": {"id": "desc-input", "name": "description", "tag": "input"},
                "body_marker": "same",
            },
            {
                "active_element": {"id": "desc-input", "name": "description", "tag": "input"},
                "target_meta": {"id": "desc-input", "name": "description", "tag": "input"},
                "body_marker": "same",
            },
        ]
    )
    clicked: list[tuple[object, int | None]] = []

    monkeypatch.setattr(helpers_v2, "_act_register_page", lambda current_page: current_page)
    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: next(observed_states))
    monkeypatch.setattr(
        helpers_v2,
        "_act_strict_click",
        lambda target, timeout_ms=None: clicked.append((target, timeout_ms)),
    )
    helpers_v2._act_reset_strategy_tracking("click_table_field", "Description")

    helpers_v2._act_click_table_field(locator, page, "Description")

    assert clicked == [(locator, None)]
    assert page.waits == [250]
    debug_trace = helpers_v2._ACT_CURRENT_STRATEGY["debug"]["click_table_field"]
    assert debug_trace["status"] == "success"
    assert debug_trace["resolved_by"] == "strict"
    assert debug_trace["after"]["active_element"]["id"] == "desc-input"


def test_click_table_field_still_fails_when_target_never_becomes_active(monkeypatch) -> None:
    page = _OracleQuickActionPage()
    locator = _DateLocator("field")
    observed_states = iter(
        [
            {
                "active_element": {"id": "other-input", "name": "other", "tag": "input"},
                "target_meta": {"id": "desc-input", "name": "description", "tag": "input"},
                "body_marker": "same",
            },
            {
                "active_element": {"id": "other-input", "name": "other", "tag": "input"},
                "target_meta": {"id": "desc-input", "name": "description", "tag": "input"},
                "body_marker": "same",
            },
        ]
    )

    monkeypatch.setattr(helpers_v2, "_act_register_page", lambda current_page: current_page)
    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: next(observed_states))
    monkeypatch.setattr(helpers_v2, "_act_strict_click", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match='Table field "Description" did not change focus or control state.'):
        helpers_v2._act_click_table_field(locator, page, "Description")


def test_collect_ai_dom_candidates_ranks_label_relevant_action_card_first() -> None:
    page = _EvaluatePage(
        {
            "helper": "click_button_target",
            "label": "Managers Add or remove",
            "candidates": [
                {
                    "tag": "button",
                    "role": "",
                    "id": "",
                    "name": "",
                    "aria_label": "Cancel",
                    "label_hint": "",
                    "placeholder": "",
                    "title": "",
                    "data_oj_field": "",
                    "text": "Cancel",
                    "html": "<button aria-label='Cancel'>Cancel</button>",
                },
                {
                    "tag": "oj-action-card",
                    "role": "button",
                    "id": "Step-0",
                    "name": "",
                    "aria_label": "",
                    "label_hint": "",
                    "placeholder": "",
                    "title": "",
                    "data_oj_field": "",
                    "text": "Managers Add or remove managers, and change manager relationship for a worker.",
                    "html": "<oj-action-card id='Step-0'>Managers Add or remove managers, and change manager relationship for a worker.<oj-switch><div role='switch' aria-label='Managers'></div></oj-switch></oj-action-card>",
                },
                {
                    "tag": "a",
                    "role": "",
                    "id": "ojSpSimpleUIShellGlobalHeader_GHLogoa1",
                    "name": "",
                    "aria_label": "Home",
                    "label_hint": "",
                    "placeholder": "",
                    "title": "Home",
                    "data_oj_field": "",
                    "text": "",
                    "html": "<a aria-label='Home' title='Home'></a>",
                },
            ],
        }
    )

    context = helpers_v2._act_collect_ai_dom_candidates(page, "click_button_target", "Managers Add or remove")

    candidates = context["candidates"]
    assert candidates[0]["tag"] == "oj-action-card"
    assert candidates[0]["id"] == "Step-0"
    assert page.payloads


def test_navigation_button_on_guided_process_requires_real_step_progress(monkeypatch) -> None:
    page = _NavigationPage()
    locator = _DateLocator("continue")
    after_observation = {
        "url": page.url,
        "title": "Change Assignment - Oracle Fusion Cloud Applications",
        "guided_step": "Assignment",
        "dialog_count": 0,
        "active_element": {"id": "after"},
        "body_marker": "after body",
        "target_value": "",
        "target_text": "Continue",
        "target_visible": True,
        "target_meta": {},
    }
    observations = chain(
        [
            {
                "url": page.url,
                "title": "Change Assignment - Oracle Fusion Cloud Applications",
                "guided_step": "Assignment",
                "dialog_count": 0,
                "active_element": {"id": "before"},
                "body_marker": "before body",
                "target_value": "",
                "target_text": "Continue",
                "target_visible": True,
                "target_meta": {},
            }
        ],
        repeat(after_observation),
    )

    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_act_page_signature", lambda *args, **kwargs: {"surface_type": "guided_process"})
    monkeypatch.setattr(helpers_v2, "_act_collect_validation_messages", lambda *args, **kwargs: [])
    monkeypatch.setattr(helpers_v2, "_act_strict_click", lambda *args, **kwargs: None)
    monkeypatch.setenv("ACT_NAV_BUTTON_POSTCONDITION_TIMEOUT_MS", "1")

    with pytest.raises(RuntimeError, match='did not advance from step "Assignment"'):
        helpers_v2._act_click_navigation_button(locator, page, "Continue")


def test_navigation_button_on_guided_process_succeeds_when_step_changes(monkeypatch) -> None:
    page = _NavigationPage()
    locator = _DateLocator("continue")
    observations = iter(
        [
            {
                "url": page.url,
                "title": "Change Assignment - Oracle Fusion Cloud Applications",
                "guided_step": "Assignment",
                "dialog_count": 0,
                "active_element": {"id": "before"},
                "body_marker": "before body",
                "target_value": "",
                "target_text": "Continue",
                "target_visible": True,
                "target_meta": {},
            },
            {
                "url": page.url,
                "title": "Change Assignment - Oracle Fusion Cloud Applications",
                "guided_step": "Managers",
                "dialog_count": 0,
                "active_element": {"id": "after"},
                "body_marker": "after body",
                "target_value": "",
                "target_text": "Continue",
                "target_visible": True,
                "target_meta": {},
            },
        ]
    )

    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_act_page_signature", lambda *args, **kwargs: {"surface_type": "guided_process"})
    monkeypatch.setattr(helpers_v2, "_act_collect_validation_messages", lambda *args, **kwargs: [])
    monkeypatch.setattr(helpers_v2, "_act_strict_click", lambda *args, **kwargs: None)

    helpers_v2._act_click_navigation_button(locator, page, "Continue")


def test_navigation_button_on_guided_process_succeeds_when_progress_counter_changes(monkeypatch) -> None:
    page = _NavigationPage()
    locator = _DateLocator("continue")
    observations = iter(
        [
            {
                "url": page.url,
                "title": "Change Assignment - Oracle Fusion Cloud Applications",
                "guided_step": "When and why",
                "guided_flow": {
                    "selected_step": "When and why",
                    "progress_counter": "2 | 12",
                    "primary_heading": "When and why",
                    "footer_actions": ["Cancel", "Continue", "Submit"],
                },
                "dialog_count": 0,
                "active_element": {"id": "before"},
                "body_marker": "before body",
                "target_value": "",
                "target_text": "Continue",
                "target_visible": True,
                "target_meta": {},
            },
            {
                "url": page.url,
                "title": "Change Assignment - Oracle Fusion Cloud Applications",
                "guided_step": "",
                "guided_flow": {
                    "selected_step": "",
                    "progress_counter": "3 | 11",
                    "primary_heading": "Assignment",
                    "footer_actions": ["Cancel", "Continue", "Submit"],
                },
                "dialog_count": 0,
                "active_element": {"id": "after"},
                "body_marker": "after body",
                "target_value": "",
                "target_text": "Continue",
                "target_visible": True,
                "target_meta": {},
            },
        ]
    )

    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_act_page_signature", lambda *args, **kwargs: {"surface_type": "guided_process"})
    monkeypatch.setattr(helpers_v2, "_act_collect_validation_messages", lambda *args, **kwargs: [])
    monkeypatch.setattr(helpers_v2, "_act_strict_click", lambda *args, **kwargs: None)

    helpers_v2._act_click_navigation_button(locator, page, "Continue")


def test_navigation_button_on_guided_process_succeeds_when_final_heading_changes(monkeypatch) -> None:
    page = _NavigationPage()
    locator = _DateLocator("continue")
    observations = iter(
        [
            {
                "url": page.url,
                "title": "Change Assignment - Oracle Fusion Cloud Applications",
                "guided_step": "Seniority dates",
                "guided_flow": {
                    "selected_step": "Seniority dates",
                    "progress_counter": "10 | 11",
                    "primary_heading": "Seniority dates",
                    "footer_actions": ["Cancel", "Continue", "Submit"],
                },
                "dialog_count": 0,
                "active_element": {"id": "before"},
                "body_marker": "before body",
                "target_value": "",
                "target_text": "Continue",
                "target_visible": True,
                "target_meta": {},
            },
            {
                "url": page.url,
                "title": "Change Assignment - Oracle Fusion Cloud Applications",
                "guided_step": "",
                "guided_flow": {
                    "selected_step": "",
                    "progress_counter": "11 | 11",
                    "primary_heading": "Need help? Contact us.",
                    "footer_actions": ["Cancel", "Submit"],
                },
                "dialog_count": 0,
                "active_element": {"id": "after"},
                "body_marker": "after body",
                "target_value": "",
                "target_text": "",
                "target_visible": False,
                "target_meta": {},
            },
        ]
    )

    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_act_page_signature", lambda *args, **kwargs: {"surface_type": "guided_process"})
    monkeypatch.setattr(
        helpers_v2,
        "_act_collect_validation_messages",
        lambda *args, **kwargs: ["Length of service is the difference between seniority date and the current application date"],
    )
    monkeypatch.setattr(helpers_v2, "_act_strict_click", lambda *args, **kwargs: None)

    helpers_v2._act_click_navigation_button(locator, page, "Continue")


def test_navigation_button_on_guided_process_surfaces_validation_after_grace(monkeypatch) -> None:
    page = _NavigationPage()
    locator = _DateLocator("continue")
    after_observation = {
        "url": page.url,
        "title": "Change Assignment - Oracle Fusion Cloud Applications",
        "guided_step": "Assignment",
        "guided_flow": {
            "selected_step": "Assignment",
            "progress_counter": "3 | 11",
            "primary_heading": "Assignment",
            "footer_actions": ["Cancel", "Continue", "Submit"],
        },
        "dialog_count": 0,
        "active_element": {"id": "after"},
        "body_marker": "after body",
        "target_value": "",
        "target_text": "Continue",
        "target_visible": True,
        "target_meta": {},
    }
    observations = chain(
        [
            {
                "url": page.url,
                "title": "Change Assignment - Oracle Fusion Cloud Applications",
                "guided_step": "Assignment",
                "guided_flow": {
                    "selected_step": "Assignment",
                    "progress_counter": "3 | 11",
                    "primary_heading": "Assignment",
                    "footer_actions": ["Cancel", "Continue", "Submit"],
                },
                "dialog_count": 0,
                "active_element": {"id": "before"},
                "body_marker": "before body",
                "target_value": "",
                "target_text": "Continue",
                "target_visible": True,
                "target_meta": {},
            }
        ],
        repeat(after_observation),
    )

    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_act_page_signature", lambda *args, **kwargs: {"surface_type": "guided_process"})
    monkeypatch.setattr(
        helpers_v2,
        "_act_collect_validation_messages",
        lambda *args, **kwargs: ["What's the way to change the assignment?: Select a value."],
    )
    monkeypatch.setattr(helpers_v2, "_act_strict_click", lambda *args, **kwargs: None)
    monkeypatch.setenv("ACT_NAV_BUTTON_POSTCONDITION_TIMEOUT_MS", "5")
    monkeypatch.setenv("ACT_NAV_BUTTON_VALIDATION_GRACE_MS", "0")

    with pytest.raises(RuntimeError, match="What's the way to change the assignment\\?: Select a value\\."):
        helpers_v2._act_click_navigation_button(locator, page, "Continue")


def test_navigation_button_submit_waits_past_persistent_warning_when_button_disables(monkeypatch) -> None:
    page = _NavigationPage()
    locator = _DateLocator("submit")
    observations = iter(
        [
            {
                "url": page.url,
                "title": "Change Assignment - Oracle Fusion Cloud Applications",
                "guided_step": "Need help? Contact us.",
                "guided_flow": {
                    "selected_step": "Need help? Contact us.",
                    "progress_counter": "11 | 11",
                    "primary_heading": "Need help? Contact us.",
                    "footer_actions": ["Cancel", "Submit"],
                },
                "dialog_count": 0,
                "active_element": {"id": "before"},
                "body_marker": "before body",
                "target_value": "",
                "target_text": "Submit",
                "target_visible": True,
                "target_meta": {"disabled": "", "aria_disabled": ""},
            },
            {
                "url": page.url,
                "title": "Change Assignment - Oracle Fusion Cloud Applications",
                "guided_step": "Need help? Contact us.",
                "guided_flow": {
                    "selected_step": "Need help? Contact us.",
                    "progress_counter": "11 | 11",
                    "primary_heading": "Need help? Contact us.",
                    "footer_actions": ["Cancel", "Submit"],
                },
                "dialog_count": 0,
                "active_element": {"id": "processing"},
                "body_marker": "processing body",
                "target_value": "",
                "target_text": "Submit",
                "target_visible": True,
                "target_meta": {"disabled": "true", "aria_disabled": "true"},
            },
            {
                "url": "https://example.com/fscmUI/redwood/employment-change/confirmation",
                "title": "Confirmation - Oracle Fusion Cloud Applications",
                "guided_step": "",
                "guided_flow": {
                    "selected_step": "",
                    "progress_counter": "",
                    "primary_heading": "Confirmation",
                    "footer_actions": [],
                },
                "dialog_count": 0,
                "active_element": {"id": "after"},
                "body_marker": "after body",
                "target_value": "",
                "target_text": "",
                "target_visible": False,
                "target_meta": {"disabled": "true", "aria_disabled": "true"},
            },
        ]
    )

    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_act_page_signature", lambda *args, **kwargs: {"surface_type": "guided_process"})
    monkeypatch.setattr(
        helpers_v2,
        "_act_collect_validation_messages",
        lambda *args, **kwargs: ["Please try again later. If the issue persists, contact your help desk."],
    )
    monkeypatch.setattr(helpers_v2, "_act_strict_click", lambda *args, **kwargs: None)

    helpers_v2._act_click_navigation_button(locator, page, "Submit")

    assert page.waits == [250]


def test_navigation_button_submit_accepts_footer_transition_without_step_change(monkeypatch) -> None:
    page = _NavigationPage()
    locator = _DateLocator("submit")
    observations = iter(
        [
            {
                "url": page.url,
                "title": "Change Assignment - Oracle Fusion Cloud Applications",
                "guided_step": "Need help? Contact us.",
                "guided_flow": {
                    "selected_step": "Need help? Contact us.",
                    "progress_counter": "11 | 11",
                    "primary_heading": "Need help? Contact us.",
                    "footer_actions": ["Cancel", "Submit"],
                },
                "dialog_count": 0,
                "active_element": {"id": "before"},
                "body_marker": "before body",
                "target_value": "",
                "target_text": "Submit",
                "target_visible": True,
                "target_meta": {"disabled": "", "aria_disabled": ""},
            },
            {
                "url": page.url,
                "title": "Change Assignment - Oracle Fusion Cloud Applications",
                "guided_step": "Need help? Contact us.",
                "guided_flow": {
                    "selected_step": "Need help? Contact us.",
                    "progress_counter": "11 | 11",
                    "primary_heading": "Need help? Contact us.",
                    "footer_actions": ["Done"],
                },
                "dialog_count": 0,
                "active_element": {"id": "after"},
                "body_marker": "after body",
                "target_value": "",
                "target_text": "",
                "target_visible": False,
                "target_meta": {"disabled": "true", "aria_disabled": "true"},
            },
        ]
    )

    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_act_page_signature", lambda *args, **kwargs: {"surface_type": "guided_process"})
    monkeypatch.setattr(
        helpers_v2,
        "_act_collect_validation_messages",
        lambda *args, **kwargs: ["Please try again later. If the issue persists, contact your help desk."],
    )
    monkeypatch.setattr(helpers_v2, "_act_strict_click", lambda *args, **kwargs: None)

    helpers_v2._act_click_navigation_button(locator, page, "Submit")

    assert page.waits == []


def test_navigation_button_on_non_guided_page_surfaces_validation_before_generic_success(monkeypatch) -> None:
    page = _NavigationPage()
    locator = _DateLocator("submit")
    after_observation = {
        "url": page.url,
        "title": "Direct Reports - Person Management - Oracle Fusion Cloud Applications",
        "guided_step": "",
        "guided_flow": {},
        "dialog_count": 0,
        "active_element": {"id": "after"},
        "body_marker": "after body",
        "target_value": "",
        "target_text": "Submit",
        "target_visible": True,
        "target_meta": {"disabled": "", "aria_disabled": ""},
    }
    observations = chain(
        [
            {
                "url": page.url,
                "title": "Direct Reports - Person Management - Oracle Fusion Cloud Applications",
                "guided_step": "",
                "guided_flow": {},
                "dialog_count": 0,
                "active_element": {"id": "before"},
                "body_marker": "before body",
                "target_value": "",
                "target_text": "Submit",
                "target_visible": True,
                "target_meta": {"disabled": "", "aria_disabled": ""},
            }
        ],
        repeat(after_observation),
    )

    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_act_page_signature", lambda *args, **kwargs: {"surface_type": "adf_form"})
    monkeypatch.setattr(
        helpers_v2,
        "_act_collect_validation_messages",
        lambda *args, **kwargs: ["Error: A selection is required."],
    )
    monkeypatch.setattr(helpers_v2, "_act_busy_indicator_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(helpers_v2, "_act_strict_click", lambda *args, **kwargs: None)
    monkeypatch.setenv("ACT_NAV_BUTTON_POSTCONDITION_TIMEOUT_MS", "5")
    monkeypatch.setenv("ACT_NAV_BUTTON_VALIDATION_GRACE_MS", "0")

    with pytest.raises(RuntimeError, match="A selection is required"):
        helpers_v2._act_click_navigation_button(locator, page, "Submit")


def test_navigation_button_on_non_guided_page_succeeds_when_generic_effect_has_no_validation(monkeypatch) -> None:
    page = _NavigationPage()
    locator = _DateLocator("submit")
    observations = iter(
        [
            {
                "url": page.url,
                "title": "Direct Reports - Person Management - Oracle Fusion Cloud Applications",
                "guided_step": "",
                "guided_flow": {},
                "dialog_count": 0,
                "active_element": {"id": "before"},
                "body_marker": "before body",
                "target_value": "",
                "target_text": "Submit",
                "target_visible": True,
                "target_meta": {"disabled": "", "aria_disabled": ""},
            },
            {
                "url": page.url,
                "title": "Direct Reports - Person Management - Oracle Fusion Cloud Applications",
                "guided_step": "",
                "guided_flow": {},
                "dialog_count": 0,
                "active_element": {"id": "after"},
                "body_marker": "after body",
                "target_value": "",
                "target_text": "Submit",
                "target_visible": True,
                "target_meta": {"disabled": "", "aria_disabled": ""},
            },
        ]
    )

    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_act_page_signature", lambda *args, **kwargs: {"surface_type": "adf_form"})
    monkeypatch.setattr(helpers_v2, "_act_collect_validation_messages", lambda *args, **kwargs: [])
    monkeypatch.setattr(helpers_v2, "_act_strict_click", lambda *args, **kwargs: None)

    helpers_v2._act_click_navigation_button(locator, page, "Submit")


def test_act_resolve_substitutes_ai_extracted_values(monkeypatch) -> None:
    monkeypatch.setattr(helpers_v2, "_ACT_AI_EXTRACTED", {"order_number": "5724"})
    assert helpers_v2._act_resolve("{{order_number}}") == "5724"
    assert helpers_v2._act_resolve("PO {{order_number}} confirmed") == "PO 5724 confirmed"
    # Unknown placeholders are left intact (fail loudly at the locator, not here).
    assert helpers_v2._act_resolve("{{missing}}") == "{{missing}}"
    # Non-strings pass through unchanged.
    assert helpers_v2._act_resolve(1234) == 1234


def test_click_text_target_resolves_placeholder_label_for_recovery(monkeypatch) -> None:
    captured: dict[str, str] = {}
    monkeypatch.setattr(helpers_v2, "_ACT_AI_EXTRACTED", {"transaction_number": "15900"})
    monkeypatch.setattr(helpers_v2, "_act_register_page", lambda page: page)
    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        helpers_v2,
        "_act_strict_click",
        lambda locator: (_ for _ in ()).throw(RuntimeError("strict failed")),
    )

    def _capture_expand(page, label):
        captured["expand_label"] = label
        return False

    def _capture_experience(page, helper, label, last_error, locator=None):
        captured["experience_label"] = label
        return []

    def _capture_ai(*, current_page, helper, label, last_error, locator, postcondition_kind, failure_message, execute_locator):
        captured["ai_label"] = label
        return None, RuntimeError("no match")

    monkeypatch.setattr(helpers_v2, "_act_try_expand_oracle_quick_actions", _capture_expand)
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_quick_action_exact_match", lambda *args, **kwargs: "")
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_notification_badge", lambda *args, **kwargs: "")
    monkeypatch.setattr(helpers_v2, "_act_try_oracle_home_search", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_act_experience_repair_locators", _capture_experience)
    monkeypatch.setattr(helpers_v2, "_act_execute_ai_repair_rounds", _capture_ai)

    with pytest.raises(RuntimeError, match='15900'):
        helpers_v2._act_click_text_target(SimpleNamespace(), SimpleNamespace(), "{{transaction_number}}")

    assert captured["expand_label"] == "15900"
    assert captured["experience_label"] == "15900"
    assert captured["ai_label"] == "15900"


class _FakeAiResponse:
    status = 200

    def __init__(self, body: str) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body.encode("utf-8")

    def __enter__(self) -> "_FakeAiResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def test_act_ai_extract_stores_value_and_writes_flow_output(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(helpers_v2, "_ACT_AI_EXTRACTED", {})
    output_path = tmp_path / "script_step_output.json"
    monkeypatch.setenv("ACT_SCRIPT_STEP_OUTPUT_PATH", str(output_path))

    monkeypatch.setattr(helpers_v2, "_act_ai_self_repair_enabled", lambda: True)
    monkeypatch.setattr(
        helpers_v2,
        "get_runner_env_value",
        lambda key: "sk-test" if key == "OPENAI_API_KEY" else "",
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_capture_ai_extract_context_screenshot",
        lambda page: {"status": "captured", "image_url": "data:image/png;base64,AAAA"},
    )
    body = json.dumps({"output_text": json.dumps({"value": "5724"})})
    monkeypatch.setattr(helpers_v2, "urlopen", lambda *args, **kwargs: _FakeAiResponse(body))

    value = helpers_v2._act_ai_extract(SimpleNamespace(), "order_number", "extract order number only")

    assert value == "5724"
    assert helpers_v2._ACT_AI_EXTRACTED["order_number"] == "5724"
    assert json.loads(output_path.read_text(encoding="utf-8")) == {"outputs": {"order_number": "5724"}}


def test_act_capture_ai_extract_context_screenshot_waits_before_capture(monkeypatch) -> None:
    page = _AIScreenshotPage()
    monkeypatch.setenv("ACT_AI_EXTRACT_PRE_CAPTURE_WAIT_MS", "750")
    monkeypatch.setenv("ACT_AI_EXTRACT_SCREENSHOT_FULL_PAGE", "false")
    monkeypatch.setattr(helpers_v2, "_ACT_LAST_PAGE_SNAPSHOT", {})
    monkeypatch.setattr(helpers_v2, "_ACT_NEXT_STEP_SCREENSHOT_OVERRIDE_PNG", None)

    context = helpers_v2._act_capture_ai_extract_context_screenshot(page)

    assert page.waits == [750]
    assert page.screenshot_calls == [{"full_page": False, "type": "png", "scale": "css"}]
    assert context["status"] == "captured"
    assert context["format"] == "png"
    assert context["full_page"] is False
    assert context["pre_capture_wait_ms"] == 750
    assert helpers_v2._ACT_NEXT_STEP_SCREENSHOT_OVERRIDE_PNG == b"fake-jpeg-bytes"


def test_act_ai_extract_includes_structured_page_context_in_request(monkeypatch) -> None:
    monkeypatch.setattr(helpers_v2, "_ACT_AI_EXTRACTED", {})
    monkeypatch.setattr(helpers_v2, "_ACT_LAST_PAGE_SNAPSHOT", {})
    monkeypatch.setattr(helpers_v2, "_act_ai_self_repair_enabled", lambda: True)
    monkeypatch.setattr(
        helpers_v2,
        "get_runner_env_value",
        lambda key: "sk-test" if key == "OPENAI_API_KEY" else "",
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_capture_page_snapshot",
        lambda page: {
            "page_title": "Transactions - Billing - Oracle Fusion Cloud Applications",
            "page_url": "https://oracle.example.com/fscmUI/redwood/receivables/transactions",
            "page_text": "Transaction Number 1045 Complete Invoice Example Customer",
            "oracle_tables": [
                {
                    "headers": ["Transaction Number", "Status"],
                    "rows": [["1045", "Complete"], ["1044", "Draft"]],
                }
            ],
            "page_semantics": {
                "label_values": [{"label": "Transaction Number", "value": "1045"}],
                "text_candidates": [{"text": "Complete"}, {"text": "Invoice"}],
                "dialogs": [],
            },
        },
    )
    monkeypatch.setattr(
        helpers_v2,
        "_act_capture_ai_extract_context_screenshot",
        lambda page: {
            "status": "captured",
            "image_url": "data:image/png;base64,AAAA",
            "pre_capture_wait_ms": 1200,
            "format": "png",
            "media_type": "image/png",
            "full_page": False,
            "scale": "css",
        },
    )

    captured_request: dict[str, Any] = {}

    def _fake_urlopen(request, timeout=0):
        captured_request["payload"] = json.loads(request.data.decode("utf-8"))
        captured_request["timeout"] = timeout
        return _FakeAiResponse(json.dumps({"output_text": json.dumps({"value": "1045"})}))

    monkeypatch.setattr(helpers_v2, "urlopen", _fake_urlopen)

    value = helpers_v2._act_ai_extract(
        SimpleNamespace(),
        "transaction_number",
        "extract transaction number from the table, of first row",
    )

    assert value == "1045"
    user_content = captured_request["payload"]["input"][1]["content"]
    assert user_content[0]["type"] == "input_text"
    assert "Structured page evidence:" in user_content[0]["text"]
    assert '"oracle_tables"' in user_content[0]["text"]
    assert "Transactions - Billing - Oracle Fusion Cloud Applications" in user_content[0]["text"]
    assert "1045" in user_content[0]["text"]
    assert "first row" in user_content[0]["text"]
    assert user_content[1]["type"] == "input_image"
    assert user_content[1]["image_url"] == "data:image/png;base64,AAAA"


def test_act_ai_extract_raises_on_empty_value(monkeypatch) -> None:
    monkeypatch.setattr(helpers_v2, "_ACT_AI_EXTRACTED", {})
    monkeypatch.setattr(helpers_v2, "_act_ai_self_repair_enabled", lambda: True)
    monkeypatch.setattr(helpers_v2, "get_runner_env_value", lambda key: "sk-test")
    monkeypatch.setattr(helpers_v2, "_act_capture_ai_extract_context_screenshot", lambda page: {})
    body = json.dumps({"output_text": json.dumps({"value": ""})})
    monkeypatch.setattr(helpers_v2, "urlopen", lambda *args, **kwargs: _FakeAiResponse(body))

    with pytest.raises(RuntimeError, match="empty value"):
        helpers_v2._act_ai_extract(SimpleNamespace(), "order_number", "extract order number only")


def test_act_ai_extract_raises_when_ai_disabled(monkeypatch) -> None:
    monkeypatch.setattr(helpers_v2, "_ACT_AI_EXTRACTED", {})
    monkeypatch.setattr(helpers_v2, "_act_ai_self_repair_enabled", lambda: False)

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        helpers_v2._act_ai_extract(SimpleNamespace(), "order_number", "extract order number only")


class _NavTimeoutPage:
    """Minimal page stub that records set_default_navigation_timeout calls."""

    def __init__(self) -> None:
        self.navigation_timeouts: list[int] = []

    def set_default_navigation_timeout(self, timeout_ms: int) -> None:
        self.navigation_timeouts.append(timeout_ms)


def test_register_page_sets_default_navigation_timeout(monkeypatch) -> None:
    monkeypatch.delenv("ACT_NAVIGATION_TIMEOUT_MS", raising=False)
    page = _NavTimeoutPage()

    helpers_v2._act_register_page(page)

    # First page open / navigation gets the 2-minute default, not Playwright's 30s.
    assert page.navigation_timeouts == [120000]


def test_register_page_navigation_timeout_env_override(monkeypatch) -> None:
    monkeypatch.setenv("ACT_NAVIGATION_TIMEOUT_MS", "45000")
    page = _NavTimeoutPage()

    helpers_v2._act_register_page(page)

    assert page.navigation_timeouts == [45000]


def test_register_page_navigation_timeout_applied_once(monkeypatch) -> None:
    monkeypatch.delenv("ACT_NAVIGATION_TIMEOUT_MS", raising=False)
    page = _NavTimeoutPage()

    helpers_v2._act_register_page(page)
    helpers_v2._act_register_page(page)
    helpers_v2._act_register_page(page)

    # Guarded: no redundant channel calls on every subsequent action.
    assert page.navigation_timeouts == [120000]


def test_control_type_label_classifies_oracle_families() -> None:
    """Debug-trace control labels: Redwood Core Pack (oj-c-*) vs legacy JET (oj-*) vs classic."""
    f = helpers_v2._act_control_type_label
    assert f("input", "oj-c-select-single", "combobox") == "oj-c-select-single (Redwood Core Pack)"
    assert f("input", "oj-select-single", "combobox") == "oj-select-single (Oracle JET)"
    assert f("oj-c-input-date", "", "") == "oj-c-input-date (Redwood Core Pack)"
    assert f("oj-input-text", "", "") == "oj-input-text (Oracle JET)"
    assert f("select", "", "") == "classic <select>"
    assert f("input", "", "textbox") == "classic <input> [role=textbox]"
    assert f("", "", "") == ""


def test_debug_observation_summary_emits_control_type() -> None:
    """The debug observation surfaces a control_type for the active element and target,
    so the report's Debug Trace shows which Oracle component was acted on."""
    summary = helpers_v2._act_debug_observation_summary(
        {
            "active_element": {
                "tag": "input",
                "role": "combobox",
                "oracle_host_tag": "oj-c-select-single",
            },
            "target_meta": {"tag": "select", "role": "", "oracle_host_tag": ""},
        }
    )
    assert summary["active_element"]["control_type"] == "oj-c-select-single (Redwood Core Pack)"
    assert summary["active_element"]["oracle_host_tag"] == "oj-c-select-single"
    assert summary["target"]["control_type"] == "classic <select>"


def test_oracle_label_control_locator_resolves_single_match_or_bails(monkeypatch) -> None:
    """Label-anchored resolver returns a [id="..."] locator for a single resolved control,
    and None for no page / blank label / zero-or-ambiguous matches (it never guesses)."""

    class _Page:
        def __init__(self) -> None:
            self.selectors: list[str] = []

        def locator(self, selector: str):
            self.selectors.append(selector)
            return ("LOC", selector)

    page = _Page()
    resolved_id = "pt1:_FOr1:1::content"
    monkeypatch.setattr(
        helpers_v2,
        "_act_safe_page_eval",
        lambda p, expr, arg=None: {"id": resolved_id} if arg == "Business Unit" else None,
    )
    loc = helpers_v2._act_oracle_label_control_locator(page, "Business Unit")
    assert loc == ("LOC", '[id="pt1:_FOr1:1::content"]')
    assert page.selectors == ['[id="pt1:_FOr1:1::content"]']
    assert helpers_v2._act_oracle_label_control_locator(None, "Business Unit") is None
    assert helpers_v2._act_oracle_label_control_locator(page, "   ") is None
    # The LOV-combobox-by-hint strategy resolves to the same [id="..."] wrapper: the Python layer
    # only reads `id`, so it heals regardless of which probe strategy (label vs lov_hint) matched.
    lov_page = _Page()
    monkeypatch.setattr(
        helpers_v2,
        "_act_safe_page_eval",
        lambda p, expr, arg=None: {"id": "ap1:businessUnit::content", "via": "lov_hint"},
    )
    lov_loc = helpers_v2._act_oracle_label_control_locator(lov_page, "Business Unit")
    assert lov_loc == ("LOC", '[id="ap1:businessUnit::content"]')
    monkeypatch.setattr(helpers_v2, "_act_safe_page_eval", lambda p, expr, arg=None: None)
    assert helpers_v2._act_oracle_label_control_locator(_Page(), "Region") is None


def test_click_combobox_recovers_via_oracle_label_anchored_open(monkeypatch) -> None:
    """When the recorded trigger can't be clicked but the field resolves by its visible Oracle
    label, the combobox opens via the deterministic label-anchored handler (before AI). Covers
    the Credit-memo Business Unit case where role/name differ from the recorded locator."""

    class _Page:
        def wait_for_timeout(self, ms: int) -> None:
            pass

    class _Loc:
        def __init__(self, name: str) -> None:
            self.name = name

    recorded = _Loc("recorded")
    label_locator = _Loc("label")
    recovery: dict = {}

    stubs = {
        "_act_register_page": lambda *a, **k: None,
        "_act_observe": lambda *a, **k: {},
        "_act_debug_observation_summary": lambda *a, **k: {},
        "_act_update_debug_detail": lambda key, payload: payload,
        "_act_set_debug_detail": lambda *a, **k: None,
        "_act_trim_debug_text": lambda value, limit=0: str(value),
        "_act_wait_ms": lambda *a, **k: 0,
        "_act_record_strategy_attempt": lambda *a, **k: None,
        "_act_store_experience_episode": lambda **k: None,
        "_act_try_open_oracle_select_single_with_keyboard": lambda *a, **k: None,
        "_act_oracle_label_control_locator": lambda p, label: label_locator,
        "_act_combobox_open_postcondition": lambda before, after: True,
        "_act_set_recovery_record": lambda *a, **k: recovery.update({"args": a}),
    }
    for name, fn in stubs.items():
        monkeypatch.setattr(helpers_v2, name, fn)

    def _strict_click(loc, timeout_ms=None):
        if loc is recorded:
            raise RuntimeError("not visible")  # the recorded trigger never becomes visible

    monkeypatch.setattr(helpers_v2, "_act_strict_click", _strict_click)

    helpers_v2._act_click_combobox(recorded, _Page(), "Business Unit")  # must not raise
    assert "oracle_label_anchored_open" in recovery.get("args", ())


def test_select_combobox_option_substitutes_label_trigger_when_hidden(monkeypatch) -> None:
    """When the recorded trigger isn't actionable (the Credit-memo Business Unit case), the
    select flow substitutes the label-resolved control so BOTH the open and the value-equality
    postcondition run against a locator that actually resolves -- not the hidden recorded one."""

    class _Loc:
        def __init__(self, name: str) -> None:
            self.name = name

    recorded = _Loc("recorded")
    label_locator = _Loc("label")
    option = _Loc("option")
    opened_with: list = []

    monkeypatch.setattr(helpers_v2, "_act_register_page", lambda *a, **k: None)
    monkeypatch.setattr(helpers_v2, "_act_update_debug_detail", lambda key, payload: payload)
    monkeypatch.setattr(helpers_v2, "_act_set_debug_detail", lambda *a, **k: None)
    monkeypatch.setattr(helpers_v2, "_act_record_strategy_attempt", lambda *a, **k: None)
    monkeypatch.setattr(helpers_v2, "_act_trim_debug_text", lambda value, limit=0: str(value))
    # recorded trigger not actionable; everything else (label locator, options) is
    monkeypatch.setattr(
        helpers_v2, "_act_locator_is_actionable", lambda loc, timeout_ms=None: loc is not recorded
    )
    monkeypatch.setattr(
        helpers_v2, "_act_oracle_label_control_locator", lambda page, label: label_locator
    )
    monkeypatch.setattr(
        helpers_v2, "_act_click_combobox", lambda trig, page, label: opened_with.append(trig)
    )
    # first option candidate validates immediately
    monkeypatch.setattr(
        helpers_v2,
        "_act_try_apply_combobox_option_candidate",
        lambda trig, resolved, page, label, name: None,
    )

    class _Page:
        def get_by_role(self, *a, **k):
            return _Loc("byrole")

        def get_by_text(self, *a, **k):
            return _Loc("bytext")

    helpers_v2._act_select_combobox_option(
        recorded, option, _Page(), "Business Unit", "Test Solutions"
    )
    # the open must have used the label-resolved locator, not the hidden recorded trigger
    assert opened_with == [label_locator]


def test_click_combobox_fast_fails_on_disabled_without_ai(monkeypatch) -> None:
    """A disabled combobox (e.g. Supplier before its controlling field commits) fails fast --
    no strict click, no Oracle/experience/AI ladder, no 30s + minutes of recovery."""

    class _Page:
        def wait_for_timeout(self, ms: int) -> None:
            pass

    monkeypatch.setattr(helpers_v2, "_act_register_page", lambda *a, **k: None)
    monkeypatch.setattr(helpers_v2, "_act_observe", lambda *a, **k: {})
    monkeypatch.setattr(helpers_v2, "_act_debug_observation_summary", lambda *a, **k: {})
    monkeypatch.setattr(helpers_v2, "_act_update_debug_detail", lambda key, payload: payload)
    monkeypatch.setattr(helpers_v2, "_act_set_debug_detail", lambda *a, **k: None)
    monkeypatch.setattr(
        helpers_v2, "_act_wait_for_select_target_enabled", lambda *a, **k: "disabled"
    )

    def _no_click(*_a, **_k):
        raise AssertionError("must not strict-click a disabled combobox")

    monkeypatch.setattr(helpers_v2, "_act_strict_click", _no_click)

    def _no_ai(**_k):
        raise AssertionError("AI must not run on a disabled combobox")

    monkeypatch.setattr(helpers_v2, "_act_execute_ai_repair_rounds", _no_ai)

    with pytest.raises(RuntimeError) as excinfo:
        helpers_v2._act_click_combobox(object(), _Page(), "Supplier")
    assert "disabled" in str(excinfo.value).lower()


def test_select_combobox_option_skips_disabled_when_value_already_set(monkeypatch) -> None:
    """A disabled combobox already showing the requested option is a no-op success (auto-derived)
    -- no open attempt, the recording keeps the step."""
    recovery: dict = {}
    monkeypatch.setattr(helpers_v2, "_act_register_page", lambda *a, **k: None)
    monkeypatch.setattr(helpers_v2, "_act_update_debug_detail", lambda key, payload: payload)
    monkeypatch.setattr(helpers_v2, "_act_set_debug_detail", lambda *a, **k: None)
    monkeypatch.setattr(helpers_v2, "_act_locator_is_actionable", lambda *a, **k: True)
    monkeypatch.setattr(
        helpers_v2, "_act_wait_for_select_target_enabled", lambda *a, **k: "disabled"
    )
    monkeypatch.setattr(
        helpers_v2, "_act_combobox_trigger_reflects_option", lambda trig, name: True
    )

    def _no_open(*_a, **_k):
        raise AssertionError("must not open a disabled combobox that already matches")

    monkeypatch.setattr(helpers_v2, "_act_click_combobox", _no_open)
    monkeypatch.setattr(
        helpers_v2, "_act_set_recovery_record", lambda *a, **k: recovery.update({"args": a})
    )
    monkeypatch.setattr(helpers_v2, "_act_store_experience_episode", lambda **k: None)

    helpers_v2._act_select_combobox_option(
        object(), object(), _NavigationPage(), "Supplier", "New Test Runner"
    )
    assert "disabled_target_value_already_set" in recovery.get("args", ())


def test_select_combobox_option_fast_fails_on_disabled_value_mismatch(monkeypatch) -> None:
    """A disabled combobox whose value differs from the request fails fast (can't open/set it),
    without the open ladder or AI."""
    monkeypatch.setattr(helpers_v2, "_act_register_page", lambda *a, **k: None)
    monkeypatch.setattr(helpers_v2, "_act_update_debug_detail", lambda key, payload: payload)
    monkeypatch.setattr(helpers_v2, "_act_set_debug_detail", lambda *a, **k: None)
    monkeypatch.setattr(helpers_v2, "_act_locator_is_actionable", lambda *a, **k: True)
    monkeypatch.setattr(
        helpers_v2, "_act_wait_for_select_target_enabled", lambda *a, **k: "disabled"
    )
    monkeypatch.setattr(
        helpers_v2, "_act_combobox_trigger_reflects_option", lambda trig, name: False
    )

    def _no_open(*_a, **_k):
        raise AssertionError("must not open a disabled mismatched combobox")

    monkeypatch.setattr(helpers_v2, "_act_click_combobox", _no_open)

    with pytest.raises(RuntimeError) as excinfo:
        helpers_v2._act_select_combobox_option(
            object(), object(), _NavigationPage(), "Supplier", "New Test Runner"
        )
    assert "disabled" in str(excinfo.value).lower()
