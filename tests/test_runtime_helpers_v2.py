import json
import os
from itertools import chain, repeat
from pathlib import Path

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
    assert "def _ptr_wait_for_initial_page_settle" not in prepared


def test_legacy_inject_runtime_helpers_shims_to_v2_import() -> None:
    script = "from __future__ import annotations\n\nprint('hello')\n"

    instrumented = _inject_runtime_helpers(script)

    assert "from src.runtime.helpers_v2 import *" in instrumented
    assert "def _ptr_wait_for_initial_page_settle" not in instrumented


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
        if "node.value" in expression:
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

    def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return kwargs


class _FakePlaywright:
    def __init__(self) -> None:
        self.chromium = _FakeChromium()


class _DateLocator:
    def __init__(self, name: str) -> None:
        self.name = name

    @property
    def first(self):
        return self


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


class _SearchOptionPage:
    def __init__(self) -> None:
        self.url = "https://example.com/fscmUI/redwood/demo"
        self.waits: list[int] = []
        self.text_calls: list[tuple[str, bool | None]] = []

    def get_by_role(self, role: str, name: str | None = None, exact: bool | None = None):
        return _SearchOptionLocator(f"{role}:{name}")

    def get_by_text(self, text: str, exact: bool | None = None):
        self.text_calls.append((text, exact))
        return _SearchOptionLocator(f"text:{text}:{exact}")

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)


def test_tracked_action_failure_records_normalized_runtime_action_name(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(helpers_v2, "_ptr_capture_failure_screenshot", lambda: None)
    monkeypatch.setattr(helpers_v2, "_ptr_finalize_action_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_ptr_capture_step", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_ptr_store_experience_episode", lambda **kwargs: captured.update(kwargs))

    def _ptr_select_combobox_option():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        helpers_v2._ptr_tracked_action("select_combobox", "Salary Basis", _ptr_select_combobox_option)

    assert captured["action_type"] == "select_combobox_option"


def test_tracked_action_waits_after_success(monkeypatch) -> None:
    page = _NavigationPage()
    captured_steps: list[str] = []
    finalized: dict[str, object] = {}

    monkeypatch.setattr(helpers_v2, "_PTR_LAST_PAGE", page)
    monkeypatch.setattr(helpers_v2, "_ptr_capture_step", lambda action_type: captured_steps.append(action_type))
    monkeypatch.setattr(
        helpers_v2,
        "_ptr_finalize_action_log",
        lambda *args, **kwargs: finalized.update({"args": args, "kwargs": kwargs}),
    )

    helpers_v2._ptr_tracked_action("click_button", "Continue", lambda current_page: "ok", page)

    assert page.waits == [10_000]
    assert captured_steps == ["click_button"]
    assert finalized["kwargs"]["page"] is page


def test_tracked_action_does_not_wait_after_failure(monkeypatch) -> None:
    page = _NavigationPage()

    monkeypatch.setattr(helpers_v2, "_PTR_LAST_PAGE", page)
    monkeypatch.setattr(helpers_v2, "_ptr_capture_failure_screenshot", lambda: None)
    monkeypatch.setattr(helpers_v2, "_ptr_finalize_action_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_ptr_store_experience_episode", lambda **kwargs: None)

    with pytest.raises(RuntimeError):
        helpers_v2._ptr_tracked_action("click_button", "Continue", lambda current_page: (_ for _ in ()).throw(RuntimeError("boom")), page)

    assert page.waits == []


def test_wait_after_interaction_captures_page_snapshot_with_hardcoded_delay(monkeypatch) -> None:
    page = _SnapshotPage()

    monkeypatch.setattr(helpers_v2, "_PTR_LAST_PAGE_SNAPSHOT", {})

    helpers_v2._ptr_wait_after_interaction(page)

    assert page.waits == [10_000]
    assert helpers_v2._PTR_LAST_PAGE_SNAPSHOT["page_url"] == page.url
    assert helpers_v2._PTR_LAST_PAGE_SNAPSHOT["page_title"] == "Create Job Requisition - Oracle Fusion Cloud Applications"
    assert helpers_v2._PTR_LAST_PAGE_SNAPSHOT["page_text"] == "Requisition REQ-10025 created successfully"
    assert helpers_v2._PTR_LAST_PAGE_SNAPSHOT["oracle_tables"][0]["rows"][0][1] == "1003"


def test_write_diagnostics_persists_oracle_tables(tmp_path, monkeypatch) -> None:
    diagnostics_path = tmp_path / "diagnostics.json"
    monkeypatch.setattr(helpers_v2, "_PTR_DIAGNOSTICS_PATH", str(diagnostics_path))
    monkeypatch.setattr(helpers_v2, "_PTR_LAST_PAGE", None)
    monkeypatch.setattr(
        helpers_v2,
        "_PTR_LAST_PAGE_SNAPSHOT",
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
    monkeypatch.setattr(helpers_v2, "_PTR_FAILURE_SCREENSHOT_PATH", None)
    monkeypatch.setattr(helpers_v2, "_PTR_STEP_ARTIFACTS", [])
    monkeypatch.setattr(helpers_v2, "_PTR_ACTION_LOG", [])

    helpers_v2._ptr_write_diagnostics()

    payload = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    assert payload["oracle_tables"][0]["rows"][0][1] == "1006"
    assert payload["page_semantics"]["label_values"][0]["value"] == "1006"


def test_capture_live_snapshot_before_close_persists_latest_live_page(tmp_path, monkeypatch) -> None:
    diagnostics_path = tmp_path / "diagnostics.json"
    page = _SnapshotPage()

    monkeypatch.setattr(helpers_v2, "_PTR_DIAGNOSTICS_PATH", str(diagnostics_path))
    monkeypatch.setattr(helpers_v2, "_PTR_LAST_PAGE", page)
    monkeypatch.setattr(helpers_v2, "_PTR_LAST_PAGE_SNAPSHOT", {})
    monkeypatch.setattr(helpers_v2, "_PTR_FAILURE_SCREENSHOT_PATH", None)
    monkeypatch.setattr(helpers_v2, "_PTR_STEP_ARTIFACTS", [])
    monkeypatch.setattr(helpers_v2, "_PTR_ACTION_LOG", [])
    monkeypatch.setenv("PTR_FLOW_CONTEXT_PRE_CLOSE_WAIT_MS", "0")

    helpers_v2._ptr_capture_live_snapshot_before_close(page)

    payload = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    assert payload["page_url"] == page.url
    assert payload["oracle_tables"][0]["rows"][0][1] == "1003"


def test_option_selection_postcondition_accepts_trigger_value_match(monkeypatch) -> None:
    trigger = _FakeLocator(value="ES Annual Salary Basis")
    option = _FakeLocator(actionable=True)
    monkeypatch.setattr(helpers_v2, "_ptr_locator_value", lambda locator: "ES Annual Salary Basis")
    monkeypatch.setattr(helpers_v2, "_ptr_locator_text", lambda locator: "")

    assert helpers_v2._ptr_option_selection_postcondition(
        {"dialog_count": 1},
        {"dialog_count": 1},
        trigger,
        option,
        "ES Annual Salary Basis",
    )


def test_value_matches_requires_non_empty_observed_value() -> None:
    assert helpers_v2._ptr_value_matches("Project Manager", "") is False


def test_check_target_marks_checkbox_checked_via_raw_check(monkeypatch) -> None:
    locator = _CheckboxLocator(checked=False)
    page = _NavigationPage()
    waits: list[str] = []

    monkeypatch.setattr(
        helpers_v2,
        "_ptr_wait_for_field_processing",
        lambda *args, **kwargs: waits.append("done"),
    )

    helpers_v2._ptr_check_target(locator, page, "Create a job application on")

    assert locator.checked is True
    assert ("check", 3000) in locator.events
    assert waits == ["done"]


def test_check_target_falls_back_to_click_when_raw_check_is_unsupported(monkeypatch) -> None:
    locator = _CheckboxLocator(checked=False, check_raises=True)
    page = _NavigationPage()
    waits: list[str] = []

    monkeypatch.setattr(
        helpers_v2,
        "_ptr_wait_for_field_processing",
        lambda *args, **kwargs: waits.append("done"),
    )

    helpers_v2._ptr_check_target(locator, page, "Create a job application on")

    assert locator.checked is True
    assert ("check", 3000) in locator.events
    assert ("click", 3000) in locator.events
    assert waits == ["done", "done"]


def test_combobox_open_postcondition_accepts_aria_expanded_transition() -> None:
    assert helpers_v2._ptr_combobox_open_postcondition(
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

    monkeypatch.setattr(helpers_v2, "_ptr_click_combobox", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_ptr_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_ptr_strict_click", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_ptr_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_ptr_wait_for_field_processing", lambda *args, **kwargs: waited.append("done"))
    monkeypatch.setattr(helpers_v2, "_ptr_combobox_trigger_reflects_option", lambda *args, **kwargs: True)

    helpers_v2._ptr_select_combobox_option(trigger, option, page, "Salary Basis", "ES Annual Salary Basis")

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

    monkeypatch.setattr(helpers_v2, "_ptr_click_combobox", lambda *args, **kwargs: open_calls.append("open"))
    monkeypatch.setattr(helpers_v2, "_ptr_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_ptr_strict_click", strict_click)
    monkeypatch.setattr(helpers_v2, "_ptr_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(
        helpers_v2,
        "_ptr_wait_for_field_processing",
        lambda *args, **kwargs: processing_waits.append("done"),
    )
    monkeypatch.setattr(
        helpers_v2,
        "_ptr_combobox_trigger_reflects_option",
        lambda *args, **kwargs: click_count >= 2,
    )
    monkeypatch.setenv("PTR_COMBOBOX_VALUE_RETRY_COUNT", "1")

    helpers_v2._ptr_select_combobox_option(trigger, option, page, "Reporting Relationship", "Project Manager")

    assert click_count == 2
    assert open_calls == ["open", "open"]
    assert processing_waits == ["done", "done"]


def test_enter_search_value_uses_keyboard_events_for_oracle_autosuggest() -> None:
    locator = _KeyboardEntryLocator()

    helpers_v2._ptr_enter_search_value(locator, "Fu")

    assert ("press", "ControlOrMeta+A", 3000) in locator.events
    assert ("press", "Backspace", 3000) in locator.events
    assert ("press_sequentially", "Fu", 75, 3000) in locator.events
    assert ("fill", "Fu", 3000) not in locator.events


def test_enter_search_value_uses_oracle_keyboard_open_when_label_intercepts_pointer_events(monkeypatch) -> None:
    locator = _OracleKeyboardEntryLocator()
    page = _NavigationPage()

    helpers_v2._ptr_reset_strategy_tracking("search_and_select", "Hiring Manager")

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

    monkeypatch.setattr(helpers_v2, "_ptr_observe", observe)
    monkeypatch.setattr(
        helpers_v2,
        "_ptr_extract_locator_metadata",
        lambda *args, **kwargs: {"class_name": "oj-searchselect-input", "role": "combobox"},
    )
    monkeypatch.setattr(
        helpers_v2,
        "_ptr_safe_locator_eval",
        lambda *args, **kwargs: {"has_oracle_host": True},
    )

    helpers_v2._ptr_enter_search_value(locator, "Curtis Feitty", current_page=page, label="Hiring Manager")

    assert locator.focused is True
    assert ("press", "ArrowDown", 3000) in locator.events
    assert ("press", "ControlOrMeta+A", 3000) in locator.events
    assert ("press", "Backspace", 3000) in locator.events
    assert ("press_sequentially", "Curtis Feitty", 75, 3000) in locator.events
    assert helpers_v2._PTR_CURRENT_STRATEGY["recovery"] == {
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
    helpers_v2._ptr_set_script_data(
        {
            "tracked_action": "click_combobox",
            "raw": "page.get_by_role('combobox', name='Why are you changing the').click()",
            "primary_locator_expr": "page.get_by_role('combobox', name='Why are you changing the')",
        }
    )
    monkeypatch.setattr(
        helpers_v2,
        "_ptr_capture_locator_context",
        lambda *args, **kwargs: {
            "id": "whenAndWhyForm_fl_employmentWhenAndWhy.ActionReasonId|input",
            "oracle_host": {
                "tag": "oj-select-single",
                "id": "whenAndWhyForm_fl_employmentWhenAndWhy.ActionReasonId",
            },
        },
    )

    prompt = helpers_v2._ptr_build_ai_self_repair_prompt(
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
    helpers_v2._ptr_set_script_data({})


def test_ai_locator_matches_label_accepts_labelledby_text(monkeypatch) -> None:
    monkeypatch.setattr(
        helpers_v2,
        "_ptr_extract_locator_metadata",
        lambda *args, **kwargs: {"labelledby_text": "Why are you changing the manager?"},
    )

    assert helpers_v2._ptr_ai_locator_matches_label(_DateLocator("recorded"), "Why are you changing the")


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

    monkeypatch.setattr(helpers_v2, "_ptr_record_strategy_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_ptr_strict_click", lambda locator, timeout_ms=None: clicks.append(locator.name))
    monkeypatch.setattr(
        helpers_v2,
        "_ptr_enter_search_value",
        lambda locator, value, timeout_ms=None, current_page=None, label="": entered.append((locator.name, value)),
    )
    monkeypatch.setattr(helpers_v2, "_ptr_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_ptr_option_selection_postcondition", lambda *args, **kwargs: True)
    monkeypatch.setattr(helpers_v2, "_ptr_wait_for_field_processing", lambda *args, **kwargs: waited.append("done"))

    helpers_v2._ptr_select_search_trigger_option(
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

    monkeypatch.setattr(helpers_v2, "_ptr_record_strategy_attempt", lambda *args, **kwargs: None)

    def _fake_click(locator, timeout_ms=None):
        clicked.append(locator.name)
        if locator.name != "text:Supremo Candidate Selection:None":
            raise RuntimeError("candidate miss")

    monkeypatch.setattr(helpers_v2, "_ptr_strict_click", _fake_click)
    monkeypatch.setattr(helpers_v2, "_ptr_enter_search_value", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_ptr_observe", lambda *args, **kwargs: {"dialog_count": 1, "body_marker": "state"})
    monkeypatch.setattr(helpers_v2, "_ptr_option_selection_postcondition", lambda *args, **kwargs: True)
    monkeypatch.setattr(helpers_v2, "_ptr_wait_for_field_processing", lambda *args, **kwargs: None)

    helpers_v2._ptr_select_search_trigger_option(
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

    monkeypatch.setattr(helpers_v2, "_ptr_record_strategy_attempt", lambda *args, **kwargs: None)

    def _fake_click(locator, timeout_ms=None):
        clicked.append(locator.name)
        if locator.name != "text:Wan Fu:True":
            raise RuntimeError("candidate miss")

    monkeypatch.setattr(helpers_v2, "_ptr_strict_click", _fake_click)
    monkeypatch.setattr(helpers_v2, "_ptr_enter_search_value", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_ptr_observe", lambda *args, **kwargs: {"dialog_count": 1, "body_marker": "state"})
    monkeypatch.setattr(helpers_v2, "_ptr_option_selection_postcondition", lambda *args, **kwargs: True)
    monkeypatch.setattr(helpers_v2, "_ptr_wait_for_field_processing", lambda *args, **kwargs: None)

    helpers_v2._ptr_select_search_trigger_option(
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


def test_click_combobox_uses_oracle_keyboard_open_when_label_intercepts_pointer_events(monkeypatch) -> None:
    page = _NavigationPage()
    locator = _OracleKeyboardComboboxLocator()
    stored: dict[str, object] = {}

    helpers_v2._ptr_reset_strategy_tracking("click_combobox", "Why are you changing the")

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
        "_ptr_strict_click",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("label subtree intercepts pointer events")
        ),
    )
    monkeypatch.setattr(helpers_v2, "_ptr_observe", observe)
    monkeypatch.setattr(
        helpers_v2,
        "_ptr_extract_locator_metadata",
        lambda *args, **kwargs: {"class_name": "oj-searchselect-input", "role": "combobox"},
    )
    monkeypatch.setattr(
        helpers_v2,
        "_ptr_safe_locator_eval",
        lambda *args, **kwargs: {"has_oracle_host": True},
    )
    monkeypatch.setattr(helpers_v2, "_ptr_experience_repair_locators", lambda *args, **kwargs: pytest.fail("experience recovery should not run"))
    monkeypatch.setattr(helpers_v2, "_ptr_ai_repair_locators", lambda *args, **kwargs: pytest.fail("ai repair should not run"))
    monkeypatch.setattr(helpers_v2, "_ptr_store_experience_episode", lambda **kwargs: stored.update(kwargs))

    helpers_v2._ptr_click_combobox(locator, page, "Why are you changing the")

    assert locator.focused is True
    assert locator.pressed == [("ArrowDown", 3000)]
    assert helpers_v2._PTR_CURRENT_STRATEGY["recovery"] == {
        "source": "oracle_handler",
        "kind": "oracle_select_single_keyboard_open",
        "handler_name": "oracle_select_single_keyboard_open",
        "details": {
            "trigger_label": "Why are you changing the",
            "strategy_name": "oracle_select_single_arrowdown",
        },
    }
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
        ]
    )

    helpers_v2._ptr_reset_strategy_tracking("click_combobox", "Search for people to add as")
    helpers_v2._PTR_CURRENT_STRATEGY["ai_interactions"] = [
        {
            "feature": "self_repair",
            "helper": "click_combobox",
            "label": "Search for people to add as",
            "status": "success",
            "response_strategy_count": 1,
        }
    ]

    monkeypatch.setattr(helpers_v2, "_ptr_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_ptr_strict_click", lambda *args, **kwargs: None)
    monkeypatch.setattr(helpers_v2, "_ptr_generic_click_postcondition", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_ptr_experience_repair_locators", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        helpers_v2,
        "_ptr_ai_repair_locators",
        lambda *args, **kwargs: [("ai_css_1", repaired, {"kind": "css", "selector": "#search"})],
    )

    with pytest.raises(RuntimeError, match='Unable to open combobox "Search for people to add as"'):
        helpers_v2._ptr_click_combobox(recorded, page, "Search for people to add as")

    interaction = helpers_v2._PTR_CURRENT_STRATEGY["ai_interactions"][-1]
    assert interaction["repair_outcome"] == "execution_failed"
    assert interaction["last_locator_strategy"] == "ai_css_1"
    assert interaction["postcondition_kind"] == "dialog_opened"
    assert interaction["postcondition_passed"] is False
    assert 'did not open combobox "Search for people to add as"' in interaction["repair_error"]


def test_control_family_recognizes_menu_panel_helpers() -> None:
    assert helpers_v2._ptr_control_family("select_adf_menu_panel_option") == "menu_panel"


def test_locator_value_and_text_use_fast_element_handle() -> None:
    locator = _FastSnapshotLocator()

    assert helpers_v2._ptr_locator_value(locator) == "fast-value"
    assert helpers_v2._ptr_locator_text(locator) == "fast text"
    assert locator.timeout is not None


def test_launch_chromium_defaults_to_headed_when_env_missing(monkeypatch) -> None:
    monkeypatch.delenv("PTR_HEADLESS", raising=False)
    playwright = _FakePlaywright()

    result = helpers_v2._ptr_launch_chromium(playwright)

    assert result["headless"] is False


def test_runtime_exports_legacy_failure_hooks_for_generated_wrapper() -> None:
    assert "_ptr_capture_failure" in helpers_v2.__all__
    assert "_ptr_write_diagnostics" in helpers_v2.__all__
    assert "_ptr_wait_after_interaction" in helpers_v2.__all__


def test_try_oracle_home_search_uses_search_box_before_ai(monkeypatch) -> None:
    page = _OracleHomePage()
    clicked: list[str] = []

    monkeypatch.setattr(helpers_v2, "_ptr_page_signature", lambda *args, **kwargs: {"path_hint": "/fscmUI/faces/FuseWelcome"})
    monkeypatch.setattr(helpers_v2, "_ptr_observe", lambda *args, **kwargs: {"dialog_count": 0})
    monkeypatch.setattr(helpers_v2, "_ptr_locator_is_actionable", lambda locator, timeout_ms=None: locator in {page.search, page.result})
    monkeypatch.setattr(helpers_v2, "_ptr_record_strategy_attempt", lambda strategy: clicked.append(strategy))

    def _strict_click(locator, timeout_ms=None):
        clicked.append(locator.name)

    def _strict_fill(locator, value, timeout_ms=None):
        locator.filled.append(value)

    monkeypatch.setattr(helpers_v2, "_ptr_strict_click", _strict_click)
    monkeypatch.setattr(helpers_v2, "_ptr_strict_fill", _strict_fill)

    succeeded = helpers_v2._ptr_try_oracle_home_search(
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

    monkeypatch.setattr(helpers_v2, "_ptr_safe_page_eval", lambda *args, **kwargs: "complete")
    monkeypatch.setattr(helpers_v2, "_ptr_busy_indicator_count", lambda *args, **kwargs: 0)

    def _is_actionable(locator, timeout_ms=None):
        if locator is icon:
            attempts["count"] += 1
            return attempts["count"] >= 3
        return False

    monkeypatch.setattr(helpers_v2, "_ptr_locator_is_actionable", _is_actionable)

    resolved = helpers_v2._ptr_wait_for_date_icon(icon, page, "Select Date.")

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

    monkeypatch.setattr(helpers_v2, "_ptr_safe_page_eval", lambda *args, **kwargs: "complete")
    monkeypatch.setattr(helpers_v2, "_ptr_busy_indicator_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        helpers_v2,
        "_ptr_locator_is_actionable",
        lambda locator, timeout_ms=None: locator in {fallback, day},
    )
    monkeypatch.setattr(helpers_v2, "_ptr_record_strategy_attempt", lambda strategy: strategies.append(strategy))
    monkeypatch.setattr(helpers_v2, "_ptr_strict_click", lambda locator, timeout_ms=None: clicks.append(locator.name))
    monkeypatch.setattr(helpers_v2, "_ptr_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_ptr_wait_for_field_processing", lambda *args, **kwargs: settled.append(True) or None)

    helpers_v2._ptr_pick_date_via_icon(icon, day, page, "Select Date.", "28")

    assert clicks == ["fallback", "day"]
    assert "date_attr_match" in strategies
    assert "day_select" in strategies
    assert settled == [True]


def test_try_oracle_guided_action_card_clicks_card_and_detects_switch_change(monkeypatch) -> None:
    card = _ActionCardLocator("managers-card")
    page = _ActionCardPage(card)
    strategies: list[str] = []

    monkeypatch.setattr(helpers_v2, "_ptr_page_signature", lambda *args, **kwargs: {"surface_type": "guided_process"})
    monkeypatch.setattr(helpers_v2, "_ptr_observe", lambda *args, **kwargs: {"dialog_count": 0, "body_marker": "same"})
    monkeypatch.setattr(helpers_v2, "_ptr_locator_is_actionable", lambda locator, timeout_ms=None: locator in {card, card.switch})
    monkeypatch.setattr(helpers_v2, "_ptr_record_strategy_attempt", lambda strategy: strategies.append(strategy))
    monkeypatch.setattr(
        helpers_v2,
        "_ptr_extract_locator_metadata",
        lambda locator: {"aria_checked": locator.aria_checked} if locator is card.switch else {},
    )

    def _strict_click(locator, timeout_ms=None):
        if locator is card:
            card.switch.aria_checked = "true"

    monkeypatch.setattr(helpers_v2, "_ptr_strict_click", _strict_click)

    succeeded = helpers_v2._ptr_try_oracle_guided_action_card(
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

    monkeypatch.setattr(helpers_v2, "_ptr_observe", lambda *args, **kwargs: {"dialog_count": 0})
    monkeypatch.setattr(helpers_v2, "_ptr_strict_click", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("direct failed")))
    monkeypatch.setattr(helpers_v2, "_ptr_try_expand_oracle_quick_actions", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_ptr_try_oracle_home_search", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_ptr_try_oracle_guided_action_card", lambda *args, **kwargs: True)
    monkeypatch.setattr(helpers_v2, "_ptr_store_experience_episode", lambda **kwargs: recovery.setdefault("experience", kwargs))
    monkeypatch.setattr(helpers_v2, "_ptr_set_recovery_record", lambda source, kind, handler_name, details=None: recovery.update({"source": source, "kind": kind, "handler_name": handler_name, "details": details or {}}))
    monkeypatch.setattr(helpers_v2, "_ptr_experience_repair_locators", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("experience should not run")))
    monkeypatch.setattr(helpers_v2, "_ptr_ai_repair_locators", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ai should not run")))

    helpers_v2._ptr_click_with_candidates(page, "Managers Add or remove", locator, "click_button_target", lambda before, after: False)

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

    monkeypatch.setattr(helpers_v2, "_ptr_observe", lambda *args, **kwargs: {"clicked": tuple(clicked)})
    monkeypatch.setattr(helpers_v2, "_ptr_strict_click", _strict_click)
    monkeypatch.setattr(
        helpers_v2,
        "_ptr_locator_is_actionable",
        lambda target, timeout_ms=None: target in {page.quick_action, page.role_exact, page.text_exact},
    )
    monkeypatch.setattr(helpers_v2, "_ptr_try_expand_oracle_quick_actions", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_ptr_try_oracle_home_search", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_ptr_try_oracle_guided_action_card", lambda *args, **kwargs: False)
    monkeypatch.setattr(helpers_v2, "_ptr_store_experience_episode", lambda **kwargs: recovery.setdefault("experience", kwargs))
    monkeypatch.setattr(
        helpers_v2,
        "_ptr_set_recovery_record",
        lambda source, kind, handler_name, details=None: recovery.update(
            {"source": source, "kind": kind, "handler_name": handler_name, "details": details or {}}
        ),
    )
    monkeypatch.setattr(helpers_v2, "_ptr_experience_repair_locators", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("experience should not run")))
    monkeypatch.setattr(helpers_v2, "_ptr_ai_repair_locators", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ai should not run")))

    helpers_v2._ptr_click_with_candidates(
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

    monkeypatch.setattr(helpers_v2, "_ptr_register_page", lambda current_page: current_page)
    monkeypatch.setattr(helpers_v2, "_ptr_observe", lambda *args, **kwargs: next(observed_states))
    monkeypatch.setattr(
        helpers_v2,
        "_ptr_strict_click",
        lambda target, timeout_ms=None: clicked.append((target, timeout_ms)),
    )

    helpers_v2._ptr_click_table_row(locator, page, "Academic")

    assert clicked == [(locator, None)]
    assert page.waits == [250]


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

    context = helpers_v2._ptr_collect_ai_dom_candidates(page, "click_button_target", "Managers Add or remove")

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

    monkeypatch.setattr(helpers_v2, "_ptr_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_ptr_page_signature", lambda *args, **kwargs: {"surface_type": "guided_process"})
    monkeypatch.setattr(helpers_v2, "_ptr_collect_validation_messages", lambda *args, **kwargs: [])
    monkeypatch.setattr(helpers_v2, "_ptr_strict_click", lambda *args, **kwargs: None)
    monkeypatch.setenv("PTR_NAV_BUTTON_POSTCONDITION_TIMEOUT_MS", "1")

    with pytest.raises(RuntimeError, match='did not advance from step "Assignment"'):
        helpers_v2._ptr_click_navigation_button(locator, page, "Continue")


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

    monkeypatch.setattr(helpers_v2, "_ptr_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_ptr_page_signature", lambda *args, **kwargs: {"surface_type": "guided_process"})
    monkeypatch.setattr(helpers_v2, "_ptr_collect_validation_messages", lambda *args, **kwargs: [])
    monkeypatch.setattr(helpers_v2, "_ptr_strict_click", lambda *args, **kwargs: None)

    helpers_v2._ptr_click_navigation_button(locator, page, "Continue")


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

    monkeypatch.setattr(helpers_v2, "_ptr_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_ptr_page_signature", lambda *args, **kwargs: {"surface_type": "guided_process"})
    monkeypatch.setattr(helpers_v2, "_ptr_collect_validation_messages", lambda *args, **kwargs: [])
    monkeypatch.setattr(helpers_v2, "_ptr_strict_click", lambda *args, **kwargs: None)

    helpers_v2._ptr_click_navigation_button(locator, page, "Continue")


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

    monkeypatch.setattr(helpers_v2, "_ptr_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_ptr_page_signature", lambda *args, **kwargs: {"surface_type": "guided_process"})
    monkeypatch.setattr(
        helpers_v2,
        "_ptr_collect_validation_messages",
        lambda *args, **kwargs: ["Length of service is the difference between seniority date and the current application date"],
    )
    monkeypatch.setattr(helpers_v2, "_ptr_strict_click", lambda *args, **kwargs: None)

    helpers_v2._ptr_click_navigation_button(locator, page, "Continue")


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

    monkeypatch.setattr(helpers_v2, "_ptr_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_ptr_page_signature", lambda *args, **kwargs: {"surface_type": "guided_process"})
    monkeypatch.setattr(
        helpers_v2,
        "_ptr_collect_validation_messages",
        lambda *args, **kwargs: ["What's the way to change the assignment?: Select a value."],
    )
    monkeypatch.setattr(helpers_v2, "_ptr_strict_click", lambda *args, **kwargs: None)
    monkeypatch.setenv("PTR_NAV_BUTTON_POSTCONDITION_TIMEOUT_MS", "5")
    monkeypatch.setenv("PTR_NAV_BUTTON_VALIDATION_GRACE_MS", "0")

    with pytest.raises(RuntimeError, match="What's the way to change the assignment\\?: Select a value\\."):
        helpers_v2._ptr_click_navigation_button(locator, page, "Continue")


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

    monkeypatch.setattr(helpers_v2, "_ptr_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_ptr_page_signature", lambda *args, **kwargs: {"surface_type": "guided_process"})
    monkeypatch.setattr(
        helpers_v2,
        "_ptr_collect_validation_messages",
        lambda *args, **kwargs: ["Please try again later. If the issue persists, contact your help desk."],
    )
    monkeypatch.setattr(helpers_v2, "_ptr_strict_click", lambda *args, **kwargs: None)

    helpers_v2._ptr_click_navigation_button(locator, page, "Submit")

    assert page.waits == [250]


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

    monkeypatch.setattr(helpers_v2, "_ptr_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_ptr_page_signature", lambda *args, **kwargs: {"surface_type": "adf_form"})
    monkeypatch.setattr(
        helpers_v2,
        "_ptr_collect_validation_messages",
        lambda *args, **kwargs: ["Error: A selection is required."],
    )
    monkeypatch.setattr(helpers_v2, "_ptr_busy_indicator_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(helpers_v2, "_ptr_strict_click", lambda *args, **kwargs: None)
    monkeypatch.setenv("PTR_NAV_BUTTON_POSTCONDITION_TIMEOUT_MS", "5")
    monkeypatch.setenv("PTR_NAV_BUTTON_VALIDATION_GRACE_MS", "0")

    with pytest.raises(RuntimeError, match="A selection is required"):
        helpers_v2._ptr_click_navigation_button(locator, page, "Submit")


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

    monkeypatch.setattr(helpers_v2, "_ptr_observe", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(helpers_v2, "_ptr_page_signature", lambda *args, **kwargs: {"surface_type": "adf_form"})
    monkeypatch.setattr(helpers_v2, "_ptr_collect_validation_messages", lambda *args, **kwargs: [])
    monkeypatch.setattr(helpers_v2, "_ptr_strict_click", lambda *args, **kwargs: None)

    helpers_v2._ptr_click_navigation_button(locator, page, "Submit")
