from __future__ import annotations

import atexit
import base64
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from playwright.sync_api import Browser, BrowserContext, Locator, Page

try:
    from ..utils.ai_repair_prompt import (
        AI_REPAIR_SYSTEM_PROMPT,
        build_ai_repair_prompt,
    )
    from ..utils.runtime_env import get_runner_env_value, load_runner_local_env
    from .experience import (
        append_episode as _experience_append_episode,
    )
    from .experience import (
        retrieve_recovery_candidates as _experience_retrieve_recovery_candidates,
    )
except ImportError:  # pragma: no cover - published/runtime fallback
    from src.runtime.experience import (
        append_episode as _experience_append_episode,
    )
    from src.runtime.experience import (
        retrieve_recovery_candidates as _experience_retrieve_recovery_candidates,
    )
    from src.utils.ai_repair_prompt import (
        AI_REPAIR_SYSTEM_PROMPT,
        build_ai_repair_prompt,
    )
    from src.utils.runtime_env import get_runner_env_value, load_runner_local_env
_ACT_LAST_PAGE: Page | None = None
_ACT_STEP_INDEX = 0
_ACT_STEP_ARTIFACTS: list[dict[str, Any]] = []
_ACT_ACTION_LOG: list[dict[str, Any]] = []
_ACT_SCRIPT_DATA: dict[str, Any] = {}
_ACT_MULTI_LINE_CONTEXT: dict[str, Any] = {}
_ACT_MULTI_LINE_SHEET_SUMMARY: dict[str, Any] = {}
_ACT_CURRENT_STRATEGY = {
    "helper": "",
    "strategy": "direct",
    "label": "",
    "attempts": [],
    "ai_interactions": [],
    "experience_interactions": [],
    "script_data": {},
    "recovery": None,
    "debug": {},
}
_ACT_DIAGNOSTICS_PATH = os.getenv("ACT_DIAGNOSTICS_PATH", "")
_ACT_FAILURE_SCREENSHOT_PATH = os.getenv("ACT_FAILURE_SCREENSHOT_PATH", "")
_ACT_STEP_ARTIFACTS_DIR = os.getenv("ACT_STEP_ARTIFACTS_DIR", "")
_ACT_EXPERIENCE_STORE_PATH = os.getenv("ACT_EXPERIENCE_STORE_PATH", "")
_ACT_RUNNER_VERSION = str(os.getenv("ACT_RUNNER_VERSION", "act-v2")).strip() or "act-v2"
_ACT_SUPPRESS_PATCH_CAPTURE = 0
_ACT_LAST_PAGE_SNAPSHOT: dict[str, Any] = {}
# Default settle wait applied after every tracked action. Overridable per run via the
# ACT_AFTER_ACTION_WAIT_MS env var (set by the tool from the recording payload); this
# literal is only the fallback when the caller does not provide a value.
_ACT_HARDCODED_AFTER_ACTION_WAIT_MS = 0
_ACT_STEEL_BROWSER_SESSION_IDS: dict[int, str] = {}
_ACT_STEEL_RELEASE_SESSION_IDS: set[str] = set()
_ACT_NEXT_STEP_SCREENSHOT_OVERRIDE_PNG: bytes | None = None
load_runner_local_env()
_ACT_POPUP_SCOPE_SELECTORS = [
    '[role="dialog"]:visible',
    '[aria-modal="true"]:visible',
    '[role="menu"]:visible',
    '[role="listbox"]:visible',
    ".oj-dialog:visible",
    ".oj-popup:visible",
]
_ACT_ORACLE_TABLE_EDITOR_ID_PATTERNS = (
    re.compile(r"(?P<table_id>.*?:at\d+:_ATp:ta\d+):\d+:i\d+(?:::[A-Za-z0-9_-]+)?$", re.IGNORECASE),
    re.compile(r"(?P<table_id>.*?:_ATp:ta\d+):\d+:i\d+(?:::[A-Za-z0-9_-]+)?$", re.IGNORECASE),
)
_ACT_COMPLETION_SPLIT_TRIGGERS = frozenset(
    {
        "complete and create another",
        "submit and create another",
    }
)
_ACT_AI_EXTRACTED: dict[str, str] = {}
_ACT_AI_EXTRACT_SYSTEM_PROMPT = (
    "You extract one specific value from provided web page evidence. "
    'Return JSON only in the form {"value": "<the exact value>"}. '
    "Return only the requested value with no labels, units, currency symbols, or "
    "extra words. Use structured page evidence and the screenshot together when both are present. "
    'If the value is not present in the provided evidence, return {"value": ""}.'
)
_ACT_AI_EXTRACT_PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")

# ---- functions live in the .helpers package; re-exported into this single
# ---- namespace so monkeypatching helpers_v2.X and shared _ACT_* state work.
from .helpers.core import *  # noqa: E402,F401,F403
from .helpers.ctl_buttons import *  # noqa: E402,F401,F403
from .helpers.ctl_checkbox import *  # noqa: E402,F401,F403
from .helpers.ctl_combobox import *  # noqa: E402,F401,F403
from .helpers.ctl_date import *  # noqa: E402,F401,F403
from .helpers.ctl_menu import *  # noqa: E402,F401,F403
from .helpers.ctl_numeric import *  # noqa: E402,F401,F403
from .helpers.ctl_searchselect import *  # noqa: E402,F401,F403
from .helpers.ctl_select import *  # noqa: E402,F401,F403
from .helpers.ctl_table import *  # noqa: E402,F401,F403
from .helpers.ctl_textbox import *  # noqa: E402,F401,F403
from .helpers.dispatch import *  # noqa: E402,F401,F403
from .helpers.oracle_nav import *  # noqa: E402,F401,F403
from .helpers.recovery import *  # noqa: E402,F401,F403

__all__ = [
    "_act_launch_chromium",
    "_act_register_page",
    "_act_get_multi_line_rows",
    "_act_set_multi_line_context",
    "_act_set_script_data",
    "_act_wait_ms",
    "_act_wait_after_interaction",
    "_act_capture_failure",
    "_act_write_diagnostics",
    "_act_tracked_action",
    "_act_tracked_raw_action",
    "_act_goto_page",
    "_act_raw_click",
    "_act_raw_fill",
    "_act_raw_press",
    "_act_login_submit_and_redirect",
    "_act_fill_textbox",
    "_act_submit_textbox_enter",
    "_act_click_textbox",
    "_act_click_combobox",
    "_act_click_button_target",
    "_act_check_target",
    "_act_uncheck_target",
    "_act_click_numeric_button_target",
    "_act_click_text_target",
    "_act_dblclick_text_target",
    "_act_click_table_field",
    "_act_click_table_row",
    "_act_click_listbox_option",
    "_act_select_option_target",
    "_act_select_combobox_option",
    "_act_select_search_trigger_option",
    "_act_select_adf_menu_panel_option",
    "_act_pick_date_via_icon",
    "_act_click_navigation_button",
    "_act_wait_for_post_login_redirect",
    "_act_ai_extract",
    "_act_resolve",
]


def _act_get_multi_line_rows() -> list[dict[str, str]]:
    raw = str(os.getenv("ACT_EXECUTION_PARAMETERS_JSON", "") or "").strip()
    if not raw:
        _ACT_MULTI_LINE_SHEET_SUMMARY.clear()
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        _ACT_MULTI_LINE_SHEET_SUMMARY.clear()
        return []
    raw_rows = payload.get("multi_line") if isinstance(payload, dict) else None
    if not isinstance(raw_rows, list):
        _ACT_MULTI_LINE_SHEET_SUMMARY.clear()
        return []

    rows: list[dict[str, str]] = []
    columns: list[str] = []
    skipped_rows = 0
    for raw_row in raw_rows:
        if not isinstance(raw_row, dict):
            skipped_rows += 1
            continue
        row: dict[str, str] = {}
        for key, value in raw_row.items():
            name = str(key or "").strip()
            if not name:
                continue
            if name not in columns:
                columns.append(name)
            row[name] = "" if value is None else str(value)
        if row:
            rows.append(row)
        else:
            skipped_rows += 1
    _ACT_MULTI_LINE_SHEET_SUMMARY.clear()
    _ACT_MULTI_LINE_SHEET_SUMMARY.update(
        {
            "sheet_name": "multi_line",
            "loaded_row_count": len(rows),
            "raw_row_count": len(raw_rows),
            "skipped_row_count": skipped_rows,
            "columns": columns,
        }
    )
    return rows


def _act_set_multi_line_context(
    row_index: int | None,
    row_data: dict[str, Any] | None,
    total_rows: int | None = None,
) -> None:
    _ACT_MULTI_LINE_CONTEXT.clear()
    summary = _act_clone_json_value(_ACT_MULTI_LINE_SHEET_SUMMARY) or {}
    if summary:
        _ACT_MULTI_LINE_CONTEXT["sheet_summary"] = summary
    if total_rows is not None:
        try:
            _ACT_MULTI_LINE_CONTEXT["total_rows"] = max(0, int(total_rows))
        except Exception:
            _ACT_MULTI_LINE_CONTEXT["total_rows"] = 0
    if row_index is None or not isinstance(row_data, dict):
        if _ACT_MULTI_LINE_CONTEXT:
            _ACT_MULTI_LINE_CONTEXT["scope"] = "after_loop"
        return

    normalized_row: dict[str, str] = {}
    for key, value in row_data.items():
        name = str(key or "").strip()
        if not name:
            continue
        normalized_row[name] = "" if value is None else str(value)

    _ACT_MULTI_LINE_CONTEXT.update(
        {
            "scope": "row",
            "row_index": int(row_index),
            "row_keys": list(normalized_row.keys()),
            "row_values": normalized_row,
        }
    )


atexit.register(_act_write_diagnostics)  # noqa: F405
atexit.register(_act_release_pending_steel_sessions)  # noqa: F405
_act_patch_page_methods()  # noqa: F405
