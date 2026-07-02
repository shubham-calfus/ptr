"""Auto-split from helpers_v2.py. `facade` is the helpers_v2 facade: the single
shared namespace, so monkeypatching helpers_v2.X and shared _ACT_* state
behave exactly as in the original module. Call shared helpers via `facade.`."""

from __future__ import annotations

import json
from typing import Any

from playwright.sync_api import Locator, Page

try:
    from .. import helpers_v2 as facade
except ImportError:  # pragma: no cover
    from src.runtime import helpers_v2 as facade

__all__ = [
    "_act_option_selection_postcondition",
    "_act_select_option_expectations",
    "_act_select_option_state",
    "_act_adf_select_commit_contradicted",
    "_act_adf_lov_query_results_populated",
    "_act_select_option_postcondition",
    "_act_apply_select_option",
    "_act_commit_select_blur",
    "_act_disabled_target_reason",
    "_act_wait_for_select_target_enabled",
    "_act_select_state_display",
    "_act_select_target_already_satisfied",
    "_act_primary_selected_index",
    "_act_resolve_select_target",
    "_act_resolve_select_option_by_committed_marker",
    "_act_oracle_adf_commit_events",
    "_act_try_oracle_adf_component_commit",
    "_act_reset_select_to_index",
    "_act_try_commit_oracle_adf_select",
    "_act_select_option_target",
]


def _act_option_selection_postcondition(
    before: dict[str, Any],
    after: dict[str, Any],
    trigger: Locator,
    option_locator: Locator,
    option_name: str,
    *,
    page: Page | None = None,
    trigger_label: str = "",
) -> bool:
    semantic_result = facade._act_oracle_menu_option_semantic_postcondition(
        page, trigger_label, option_name, before=before
    )
    if semantic_result is not None:
        return semantic_result
    observed = facade._act_locator_value(trigger) or facade._act_locator_text(trigger)
    if facade._act_value_matches(option_name, observed):
        return True
    if int(after.get("dialog_count") or 0) < int(before.get("dialog_count") or 0):
        return True
    if facade._act_generic_click_postcondition(before, after):
        return True
    if int(before.get("dialog_count") or 0) > 0 and (
        not facade._act_locator_is_actionable(option_locator, timeout_ms=250)
    ):
        return True
    return False


def _act_select_option_expectations(
    option_args: list[Any] | None, option_kwargs: dict[str, Any] | None
) -> dict[str, list[Any]]:
    expectations: dict[str, list[Any]] = {"values": [], "labels": [], "indexes": []}

    def _record(kind: str, value: Any) -> None:
        if isinstance(value, (list, tuple)):
            for item in value:
                _record(kind, item)
            return
        if isinstance(value, dict):
            for nested_kind in ("value", "label", "index"):
                if nested_kind in value:
                    _record(nested_kind, value.get(nested_kind))
            return
        if kind == "index":
            try:
                normalized_index = int(value)
            except Exception:
                return
            if normalized_index not in expectations["indexes"]:
                expectations["indexes"].append(normalized_index)
            return
        normalized_text = str(value or "").strip()
        if not normalized_text:
            return
        bucket = "labels" if kind == "label" else "values"
        if normalized_text not in expectations[bucket]:
            expectations[bucket].append(normalized_text)

    for arg in option_args or []:
        _record("value", arg)
    for kind in ("value", "label", "index"):
        if kind in (option_kwargs or {}):
            _record(kind, option_kwargs.get(kind))
    return expectations


def _act_select_option_state(locator: Locator) -> dict[str, Any]:
    state = facade._act_safe_locator_eval(
        locator,
        '(node) => {\n            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();\n            const dedupe = (items) => Array.from(new Set(items.map(normalize).filter(Boolean)));\n            if (!node) {\n                return {\n                    value: "",\n                    selected_values: [],\n                    selected_labels: [],\n                    selected_indexes: [],\n                    text: "",\n                };\n            }\n            const selectedOptions = Array.from(node.selectedOptions || []);\n            const attr = (name) => normalize(node.getAttribute ? node.getAttribute(name) : "");\n            const nodeId = String(node.id || "");\n            return {\n                value: normalize("value" in node ? node.value : ""),\n                selected_values: dedupe(selectedOptions.map((option) => option?.value)),\n                selected_labels: dedupe(selectedOptions.map((option) => option?.label || option?.innerText || option?.textContent)),\n                selected_indexes: selectedOptions\n                    .map((option) => Number(option?.index))\n                    .filter((value) => Number.isInteger(value)),\n                text: normalize(node.innerText || node.textContent),\n                // ADF Faces selectOneChoice commit markers (see _act_adf_select_commit_contradicted).\n                title: attr("title"),\n                afov: attr("_afov"),\n                is_adf: Boolean((node.hasAttribute && node.hasAttribute("_afov")) || nodeId.indexOf("::") !== -1),\n            };\n        }',
    )
    return state if isinstance(state, dict) else {}


def _act_adf_select_commit_contradicted(state: dict[str, Any]) -> bool:
    """Return True when an ADF Faces ``<select>`` shows the requested option in
    the DOM but ADF's committed markers still reflect the prior value.

    Playwright's ``select_option`` mutates the native ``<select>`` value and
    ``selectedOptions`` directly. For an Oracle ADF ``selectOneChoice`` that is
    not enough: ADF only mirrors the committed value into the element's
    ``title`` attribute and its ``_afov`` ("original value") attribute when its
    own change handler / partial-page-refresh actually runs. When those markers
    contradict the DOM-selected option, Oracle never accepted the selection
    (e.g. the dependent "Create Transaction: Invoice" header never changes), so
    the action must not be reported as successful.

    Only ADF selects are gated (``is_adf``); plain HTML ``<select>`` elements are
    left to the existing DOM-state postcondition. A missing/blank marker is never
    treated as a contradiction, so a genuinely-committed select is never failed.
    """
    if not isinstance(state, dict) or not state.get("is_adf"):
        return False
    selected_labels = [
        label for label in state.get("selected_labels") or [] if facade._act_normalize_text(label)
    ]
    selected_value = facade._act_normalize_text(state.get("value"))
    selected_indexes = {str(index).strip() for index in state.get("selected_indexes") or []}
    title = facade._act_normalize_text(state.get("title"))
    afov = facade._act_normalize_text(state.get("afov"))
    if not title and (not afov):
        return False
    title_confirms = bool(title) and any(
        facade._act_value_matches(label, title) for label in selected_labels
    )
    afov_confirms = bool(afov) and (afov == selected_value or afov in selected_indexes)
    if title_confirms or afov_confirms:
        return False
    return True


def _act_adf_lov_query_results_populated(page: Page | None, locator: Locator | None) -> bool:
    """Postcondition for clicking an ADF LOV query/Search button.

    An ADF "Search and Select" LOV is an ``af:popup`` (NOT a ``role=dialog``, so ``dialog_count``
    stays 0) whose results table populates deep inside the popup -- a change the generic click
    postcondition cannot see (it is past the truncated ``body_marker``, and url/title/active-element
    do not move). So clicking the LOV's Search button looks like "nothing changed" even though the
    query ran, which previously sent the step into pointless recovery/AI for minutes. This validates
    the click the way a user does: the LOV results grid/listbox now has at least one visible row.

    Only applies when the clicked control is the LOV's internal query button (its id/name holds
    ``_afrLovInternalQueryId``); returns False for any other button, so it never loosens a normal
    button's postcondition. Row scanning walks up from the query panel a bounded number of levels so
    it stays inside the LOV popup and cannot mistake an unrelated main-page table for results.
    """
    if page is None or locator is None:
        return False
    result = facade._act_safe_locator_eval(
        locator,
        """
        (node) => {
            if (!node) return { is_lov_query: false };
            const marker = "_afrLovInternalQueryId";
            const id = String(node.id || "");
            const nm = String((node.getAttribute && node.getAttribute("name")) || "");
            if (id.indexOf(marker) === -1 && nm.indexOf(marker) === -1) {
                return { is_lov_query: false };
            }
            const win = node.ownerDocument.defaultView || window;
            const visible = (n) => {
                if (!n) return false;
                const s = win.getComputedStyle(n);
                if (!s || s.display === "none" || s.visibility === "hidden") return false;
                const r = n.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            };
            const queryPanel = node.closest('[id*="' + marker + '"]') || node;
            const rowSel =
                '[role="row"]:has([role="gridcell"]), tbody > tr, [role="option"]';
            const countRows = (g) => {
                let rows;
                try { rows = g.querySelectorAll(rowSel); }
                catch (e) { rows = g.querySelectorAll('tbody > tr, [role="option"]'); }
                let n = 0;
                for (const r of rows) {
                    if (visible(r) && (r.innerText || r.textContent || "").trim()) n += 1;
                }
                return n;
            };
            let ancestor = queryPanel;
            for (let up = 0; up < 6 && ancestor; up++) {
                ancestor = ancestor.parentElement;
                if (!ancestor) break;
                const grids = Array.from(
                    ancestor.querySelectorAll('[role="grid"], [role="listbox"], table')
                ).filter((g) => !queryPanel.contains(g) && visible(g));
                if (grids.length) {
                    let total = 0;
                    for (const g of grids) total += countRows(g);
                    return { is_lov_query: true, row_count: total };
                }
            }
            return { is_lov_query: true, row_count: 0 };
        }
        """,
    )
    if not isinstance(result, dict) or not result.get("is_lov_query"):
        return False
    try:
        row_count = int(result.get("row_count") or 0)
    except Exception:
        row_count = 0
    facade._act_update_debug_detail("adf_lov_search", {"row_count": row_count})
    return row_count > 0


def _act_select_option_postcondition(
    before_state: dict[str, Any],
    after_state: dict[str, Any],
    option_args: list[Any] | None,
    option_kwargs: dict[str, Any] | None,
) -> bool:
    expectations = facade._act_select_option_expectations(option_args, option_kwargs)

    def _normalized_strings(values: list[Any]) -> list[str]:
        return [str(value or "").strip() for value in values if str(value or "").strip()]

    def _match_all(expected: list[str], observed: list[str]) -> bool:
        if not expected:
            return True
        if not observed:
            return False
        return all(
            any(
                facade._act_value_matches(expected_value, observed_value)
                for observed_value in observed
            )
            for expected_value in expected
        )

    observed_values = _normalized_strings(
        [after_state.get("value")] + list(after_state.get("selected_values") or [])
    )
    observed_labels = _normalized_strings(list(after_state.get("selected_labels") or []))
    observed_indexes: list[int] = []
    for value in list(after_state.get("selected_indexes") or []):
        try:
            observed_indexes.append(int(value))
        except Exception:
            continue
    matched_explicit_expectation = False
    if expectations["values"]:
        matched_explicit_expectation = True
        if not _match_all([str(item) for item in expectations["values"]], observed_values):
            return False
    if expectations["labels"]:
        matched_explicit_expectation = True
        if not _match_all([str(item) for item in expectations["labels"]], observed_labels):
            return False
    if expectations["indexes"]:
        matched_explicit_expectation = True
        if not all(int(item) in observed_indexes for item in expectations["indexes"]):
            return False
    if matched_explicit_expectation:
        base_pass = True
    else:
        base_pass = after_state != before_state and bool(
            observed_values or observed_labels or observed_indexes
        )
    if not base_pass:
        return False
    # Oracle ADF selectOneChoice commit gate: a forced native-<select> value is
    # not success unless ADF actually committed it (title/_afov agree with the
    # DOM selection). Otherwise the UI shows the new option while the model and
    # dependent header stay on the old value -> the action failed.
    if facade._act_adf_select_commit_contradicted(after_state):
        return False
    return True


def _act_apply_select_option(
    locator: Locator, option_args: list[Any] | None, option_kwargs: dict[str, Any] | None
) -> None:
    timeout_ms = facade._act_wait_ms("ACT_ACTION_TIMEOUT_MS", 3000)
    locator.wait_for(state="visible", timeout=timeout_ms)
    try:
        locator.scroll_into_view_if_needed(timeout=min(timeout_ms, 1000))
    except Exception:
        pass
    call_kwargs = dict(option_kwargs or {})
    call_kwargs.setdefault("timeout", timeout_ms)
    locator.select_option(*(option_args or []), **call_kwargs)


def _act_disabled_target_reason(locator: Locator) -> dict[str, Any]:
    """Describe HOW a control is disabled, so a disabled fast-fail is a DIAGNOSTIC, not a dead end.
    Distinguishes the field's own ``disabled`` attribute, ``aria-disabled`` on the field, and a
    disabled ANCESTOR region (the last means a whole section is gated -> a prior activation step is
    missing, not this field). Best-effort; returns {} when it can't introspect."""
    result = facade._act_safe_locator_eval(
        locator,
        "(node) => {\n"
        "    if (!node) return null;\n"
        "    const ariaDisabled = node.getAttribute"
        " && node.getAttribute('aria-disabled') === 'true';\n"
        "    const ownDisabled = Boolean(node.disabled);\n"
        "    const ancestor = node.closest"
        " && node.closest('.oj-disabled,fieldset[disabled],[aria-disabled=\"true\"]');\n"
        "    let source = '';\n"
        "    if (ownDisabled) source = 'own disabled attribute';\n"
        "    else if (ariaDisabled) source = 'aria-disabled=true on the field';\n"
        "    else if (ancestor) source = 'disabled ancestor <'"
        " + String(ancestor.tagName || '').toLowerCase()\n"
        "        + (ancestor.className ? ' class=\"' + ancestor.className + '\"' : '') + '>';\n"
        "    return {source: source, id: node.id || '',"
        " title: (node.getAttribute && node.getAttribute('title')) || '',"
        " readonly: Boolean(node.readOnly)};\n"
        "}",
    )
    return result if isinstance(result, dict) else {}


def _act_commit_select_blur(locator: Locator) -> bool:
    """Commit a select with a TRUSTED keyboard gesture so Oracle ADF runs its real onchange/onblur
    autosubmit (the PPR that re-evaluates a dependent field's ``disabled`` EL -- e.g. a Supplier LOV
    that the server enables only once the Business Unit fields commit).

    Why a real key press and not JS: ``select_option`` and JS-dispatched ``change``/``blur`` produce
    UNTRUSTED events (``isTrusted === false``), which ADF's autosubmit ignores -- so the value
    commits client-side but the server never re-evaluates the dependent's enablement. A real
    ``Tab`` goes through CDP and is trusted, firing the same autosubmit a human's dropdown pick +
    move-on would. Best-effort, never raises; returns whether the press landed. Gated by
    ACT_SELECT_BLUR_COMMIT at the call site."""
    try:
        locator.press("Tab", timeout=facade._act_wait_ms("ACT_ACTION_TIMEOUT_MS", 3000))
        return True
    except Exception:
        return False


def _act_wait_for_select_target_enabled(
    locator: Locator, current_page: Page, *, env_name: str, default_ms: int
) -> str:
    """Bounded check that a recorded ``<select>`` target is ENABLED before the runner spends the
    full strict timeout + AI repair on it. A dependent Oracle LOV (e.g. Requisitioning BU, which
    depends on Procurement BU) renders DISABLED until its dependency settles -- or stays disabled
    because it is auto-derived from another field and must not be set at all. Playwright would wait
    the whole action timeout (30s) for "enabled", then AI self-repair would flail for minutes, and
    none of it can enable a disabled control. Returns:

      "enabled"  -- visible and enabled within the window (proceed normally),
      "disabled" -- visible but still disabled after the window (caller fails fast, no AI),
      "absent"   -- not resolvable/visible in the probe (let the normal strict/AI path run, e.g.
                    the recorded locator is simply wrong rather than pointing at a disabled field).
    """
    probe = """
    (node) => {
        if (!node) return null;
        const style = window.getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        const visible = Boolean(style) && style.display !== "none"
            && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
        const ariaDisabled = node.getAttribute
            && node.getAttribute("aria-disabled") === "true";
        const host = node.closest
            && node.closest('.oj-disabled,fieldset[disabled],[aria-disabled="true"]');
        return {disabled: Boolean(node.disabled || ariaDisabled || host), visible: visible};
    }
    """
    budget = max(0, facade._act_wait_ms(env_name, default_ms))
    waited = 0
    step = 250
    saw_visible_disabled = False
    while True:
        state = facade._act_safe_locator_eval(locator, probe)
        if not isinstance(state, dict):
            return "absent"
        if state.get("visible") and not state.get("disabled"):
            return "enabled"
        if state.get("visible"):
            saw_visible_disabled = True
        if waited >= budget:
            return "disabled" if saw_visible_disabled else "absent"
        try:
            current_page.wait_for_timeout(step)
        except Exception:
            return "disabled" if saw_visible_disabled else "absent"
        waited += step


def _act_select_state_display(state: dict[str, Any]) -> str:
    labels = [str(v or "").strip() for v in (state.get("selected_labels") or []) if str(v).strip()]
    if labels:
        return ", ".join(labels)
    value = str(state.get("value") or "").strip()
    return value or "(empty)"


def _act_select_target_already_satisfied(
    state: dict[str, Any], option_args: list[Any] | None, option_kwargs: dict[str, Any] | None
) -> bool:
    """True when the field's CURRENT selection already satisfies the requested option. Used to let
    an auto-derived DISABLED field pass as a no-op (the value we wanted is already set) instead of
    failing -- so the recording does not have to drop the step. A recorded select_option(arg)
    matches by value OR label, so 'already satisfied' likewise accepts a current value OR label
    match (and an explicit index match)."""
    expectations = facade._act_select_option_expectations(option_args, option_kwargs)
    observed = [str(state.get("value") or "").strip()]
    observed += [str(v or "").strip() for v in (state.get("selected_values") or [])]
    observed += [str(v or "").strip() for v in (state.get("selected_labels") or [])]
    observed = [value for value in observed if value]
    indexes: list[int] = []
    for value in state.get("selected_indexes") or []:
        try:
            indexes.append(int(value))
        except Exception:
            continue
    if any(int(idx) in indexes for idx in expectations["indexes"]):
        return True
    wanted = [str(item) for item in (expectations["values"] + expectations["labels"])]
    if not wanted:
        return False
    return any(
        any(facade._act_value_matches(want, observed_value) for observed_value in observed)
        for want in wanted
    )


def _act_primary_selected_index(state: dict[str, Any]) -> int | None:
    for value in list((state or {}).get("selected_indexes") or []):
        try:
            return int(value)
        except Exception:
            continue
    return None


def _act_resolve_select_target(
    locator: Locator, option_args: list[Any] | None, option_kwargs: dict[str, Any] | None
) -> dict[str, Any]:
    expectations = facade._act_select_option_expectations(option_args, option_kwargs)
    snapshot = facade._act_safe_locator_eval(
        locator,
        '(node, payload) => {\n            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();\n            const options = Array.from(node?.options || []);\n            const nodeId = String(node?.id || "");\n            return {\n                is_adf: Boolean((node?.hasAttribute && node.hasAttribute("_afov")) || nodeId.indexOf("::") !== -1),\n                options: options.map((option) => ({\n                    index: Number(option?.index),\n                    value: normalize(option?.value),\n                    label: normalize(option?.label || option?.innerText || option?.textContent),\n                })),\n            };\n        }',
        expectations,
    )
    if not isinstance(snapshot, dict):
        return {}
    raw_options = snapshot.get("options") or []
    options: list[dict[str, Any]] = []
    for raw_option in raw_options if isinstance(raw_options, list) else []:
        if not isinstance(raw_option, dict):
            continue
        try:
            option_index = int(raw_option.get("index"))
        except Exception:
            continue
        options.append(
            {
                "index": option_index,
                "value": str(raw_option.get("value") or "").strip(),
                "label": str(raw_option.get("label") or "").strip(),
            }
        )

    def _pick(option: dict[str, Any]) -> dict[str, Any]:
        return {
            "index": int(option.get("index")),
            "value": str(option.get("value") or "").strip(),
            "label": str(option.get("label") or "").strip(),
        }

    for expected_index in expectations["indexes"]:
        try:
            wanted_index = int(expected_index)
        except Exception:
            continue
        for option in options:
            if int(option.get("index")) == wanted_index:
                return _pick(option)
    for expected_value in expectations["values"]:
        normalized_expected = str(expected_value or "").strip()
        if not normalized_expected:
            continue
        for option in options:
            if facade._act_value_matches(normalized_expected, option.get("value")):
                return _pick(option)
    for expected_label in expectations["labels"]:
        normalized_expected = str(expected_label or "").strip()
        if not normalized_expected:
            continue
        for option in options:
            if facade._act_value_matches(normalized_expected, option.get("label")):
                return _pick(option)
    # Oracle ADF selectOneChoice often stores zero-based internal values while
    # older recordings persist the visible ordinal as "1", "2", "3". Reuse the
    # live option order only when the control clearly looks like ADF and the
    # option values are the canonical zero-based sequence.
    is_adf = bool(snapshot.get("is_adf"))
    if (
        is_adf
        and len(expectations["values"]) == 1
        and (not expectations["labels"])
        and (not expectations["indexes"])
    ):
        try:
            ordinal = int(str(expectations["values"][0] or "").strip())
        except Exception:
            ordinal = None
        option_values = [str(option.get("value") or "").strip() for option in options]
        looks_zero_based = option_values == [str(index) for index in range(len(options))]
        if ordinal is not None and looks_zero_based and (1 <= ordinal <= len(options)):
            return _pick(options[ordinal - 1])
    return {}


def _act_resolve_select_option_by_committed_marker(
    locator: Locator, state: dict[str, Any]
) -> dict[str, Any]:
    snapshot = facade._act_safe_locator_eval(
        locator,
        '(node) => {\n            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();\n            const options = Array.from(node?.options || []);\n            return {\n                options: options.map((option) => ({\n                    index: Number(option?.index),\n                    value: normalize(option?.value),\n                    label: normalize(option?.label || option?.innerText || option?.textContent),\n                })),\n            };\n        }',
    )
    if not isinstance(snapshot, dict):
        return {}
    raw_options = snapshot.get("options") or []
    options: list[dict[str, Any]] = []
    for raw_option in raw_options if isinstance(raw_options, list) else []:
        if not isinstance(raw_option, dict):
            continue
        try:
            option_index = int(raw_option.get("index"))
        except Exception:
            continue
        options.append(
            {
                "index": option_index,
                "value": str(raw_option.get("value") or "").strip(),
                "label": str(raw_option.get("label") or "").strip(),
            }
        )
    committed_afov = facade._act_normalize_text((state or {}).get("afov"))
    if committed_afov:
        for option in options:
            option_value = str(option.get("value") or "").strip()
            if option_value and facade._act_value_matches(committed_afov, option_value):
                return facade._act_clone_json_value(option)
            if committed_afov == str(option.get("index")):
                return facade._act_clone_json_value(option)
    committed_title = facade._act_normalize_text((state or {}).get("title"))
    if committed_title:
        for option in options:
            option_label = str(option.get("label") or "").strip()
            if option_label and facade._act_value_matches(committed_title, option_label):
                return facade._act_clone_json_value(option)
    return {}


def _act_oracle_adf_commit_events(locator: Locator) -> bool:
    result = facade._act_safe_locator_eval(
        locator,
        '(node) => {\n            if (!node) return false;\n            const fire = (name) => node.dispatchEvent(new Event(name, { bubbles: true, cancelable: true }));\n            try {\n                node.focus?.();\n            } catch (error) {\n                return false;\n            }\n            fire("input");\n            fire("change");\n            fire("blur");\n            fire("focusout");\n            return true;\n        }',
    )
    return bool(result)


def _act_try_oracle_adf_component_commit(
    locator: Locator, target: dict[str, Any]
) -> dict[str, Any] | None:
    target_value = str(target.get("value") or "").strip()
    target_label = str(target.get("label") or "").strip()
    target_index = target.get("index")
    if isinstance(target_index, bool):
        target_index = int(target_index)
    elif not isinstance(target_index, int):
        try:
            target_index = int(target_index)
        except Exception:
            target_index = -1
    result = facade._act_safe_locator_eval(
        locator,
        '(node, payload) => {\n            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();\n            if (!node) return { ok: false, reason: "missing_node" };\n            const contentId = String(node.id || "").trim();\n            const baseId = contentId.endsWith("::content") ? contentId.slice(0, -"::content".length) : contentId;\n            const win = node.ownerDocument?.defaultView || window;\n            const adfPage = win?.AdfPage?.PAGE || null;\n            const component = (\n                adfPage?.findComponentByAbsoluteId?.(baseId)\n                || adfPage?.findComponent?.(baseId)\n                || null\n            );\n            if (!component) {\n                return { ok: false, reason: "missing_component", base_id: baseId, content_id: contentId };\n            }\n\n            const targetValue = normalize(payload?.value);\n            const targetIndex = Number(payload?.index);\n            const used = [];\n\n            try { node.focus?.(); used.push("focus"); } catch (error) {}\n            try {\n                if (Number.isInteger(targetIndex) && node.options && targetIndex >= 0 && targetIndex < node.options.length) {\n                    node.selectedIndex = targetIndex;\n                    used.push("selectedIndex");\n                } else if (targetValue) {\n                    node.value = targetValue;\n                    used.push("value");\n                }\n            } catch (error) {}\n\n            const fire = (name) => {\n                try {\n                    node.dispatchEvent(new Event(name, { bubbles: true, cancelable: true }));\n                    used.push(`event:${name}`);\n                } catch (error) {}\n            };\n\n            fire("input");\n            fire("change");\n\n            try {\n                if (typeof component.setValue === "function" && targetValue) {\n                    component.setValue(targetValue);\n                    used.push("component.setValue");\n                }\n            } catch (error) {}\n\n            try {\n                if (typeof component.processUpdates === "function") {\n                    component.processUpdates(node);\n                    used.push("component.processUpdates");\n                }\n            } catch (error) {}\n\n            try {\n                if (typeof component._handleBlur === "function") {\n                    component._handleBlur();\n                    used.push("component._handleBlur");\n                }\n            } catch (error) {}\n\n            try {\n                if (win?.AdfCustomEvent?.queue) {\n                    win.AdfCustomEvent.queue(component, "valueChange", { value: targetValue }, true);\n                    used.push("AdfCustomEvent.queue");\n                }\n            } catch (error) {}\n\n            fire("blur");\n            fire("focusout");\n\n            return {\n                ok: true,\n                base_id: baseId,\n                content_id: contentId,\n                used,\n            };\n        }',
        {"value": target_value, "index": target_index},
    )
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    return {
        "strategy_name": "oracle_adf_select_component_commit",
        "component_id": str(result.get("base_id") or "").strip(),
        "content_id": str(result.get("content_id") or "").strip(),
        "used": facade._act_clone_json_value(result.get("used") or []),
        "target_index": target_index,
        "target_value": target_value,
        "target_label": target_label,
    }


def _act_reset_select_to_index(locator: Locator, index: int) -> bool:
    result = facade._act_safe_locator_eval(
        locator,
        "(node, payload) => {\n            if (!node || !node.options) return false;\n            const nextIndex = Number(payload?.index);\n            if (!Number.isInteger(nextIndex) || nextIndex < 0 || nextIndex >= node.options.length) return false;\n            node.selectedIndex = nextIndex;\n            return true;\n        }",
        {"index": index},
    )
    return bool(result)


def _act_try_commit_oracle_adf_select(
    locator: Locator,
    current_page: Page,
    before_state: dict[str, Any],
    option_args: list[Any] | None,
    option_kwargs: dict[str, Any] | None,
) -> dict[str, Any] | None:
    contradicted_state = facade._act_select_option_state(locator)
    if not facade._act_adf_select_commit_contradicted(contradicted_state):
        return None
    timeout_ms = facade._act_wait_ms("ACT_ACTION_TIMEOUT_MS", 3000)
    wait_ms = facade._act_wait_ms("ACT_ADF_SELECT_COMMIT_WAIT_MS", 250)
    target = facade._act_resolve_select_target(locator, option_args, option_kwargs)
    if not target:

        def _compact_text(value: Any) -> str:
            return " ".join(str(value or "").split())

        contradicted_index = facade._act_primary_selected_index(contradicted_state)
        contradicted_value = _compact_text(contradicted_state.get("value"))
        selected_values = [
            _compact_text(value)
            for value in list(contradicted_state.get("selected_values") or [])
            if _compact_text(value)
        ]
        selected_labels = [
            _compact_text(value)
            for value in list(contradicted_state.get("selected_labels") or [])
            if _compact_text(value)
        ]
        fallback_value = contradicted_value or (selected_values[0] if selected_values else "")
        fallback_label = selected_labels[0] if selected_labels else ""
        if contradicted_index is not None or fallback_value or fallback_label:
            target = {
                "index": contradicted_index if contradicted_index is not None else -1,
                "value": fallback_value,
                "label": fallback_label,
            }
    before_index = facade._act_primary_selected_index(before_state)
    inferred_committed_option: dict[str, Any] = {}
    if before_index is None:
        inferred_committed_option = facade._act_resolve_select_option_by_committed_marker(
            locator, contradicted_state
        )
        inferred_index = inferred_committed_option.get("index")
        if isinstance(inferred_index, bool):
            before_index = int(inferred_index)
        elif isinstance(inferred_index, int):
            before_index = inferred_index
        else:
            try:
                before_index = int(inferred_index)
            except Exception:
                before_index = None
    target_index = target.get("index")
    if isinstance(target_index, bool):
        target_index = int(target_index)
    elif not isinstance(target_index, int):
        try:
            target_index = int(target_index)
        except Exception:
            target_index = None
    if before_index is not None and target_index is not None and (before_index != target_index):
        try:
            locator.focus(timeout=timeout_ms)
        except Exception:
            pass
        if facade._act_reset_select_to_index(locator, before_index):
            direction = "ArrowDown" if target_index > before_index else "ArrowUp"
            for _ in range(abs(target_index - before_index)):
                locator.press(direction, timeout=timeout_ms)
            try:
                locator.press("Tab", timeout=timeout_ms)
            except Exception:
                pass
            current_page.wait_for_timeout(wait_ms)
            facade._act_wait_for_field_processing(
                current_page, env_name="ACT_DROPDOWN_CHANGE_PROCESSING_WAIT_MS", default_ms=5000
            )
            keyboard_state = facade._act_select_option_state(locator)
            if facade._act_select_option_postcondition(
                before_state, keyboard_state, option_args, option_kwargs
            ):
                details = {
                    "strategy_name": "oracle_adf_select_keyboard_commit",
                    "target_index": target_index,
                    "target_value": str(target.get("value") or "").strip(),
                    "target_label": str(target.get("label") or "").strip(),
                }
                if inferred_committed_option:
                    details["committed_index_source"] = "adf_marker"
                    details["committed_value"] = str(
                        inferred_committed_option.get("value") or ""
                    ).strip()
                    details["committed_label"] = str(
                        inferred_committed_option.get("label") or ""
                    ).strip()
                return details
    component_details = facade._act_try_oracle_adf_component_commit(locator, target)
    if component_details:
        current_page.wait_for_timeout(wait_ms)
        facade._act_wait_for_field_processing(
            current_page, env_name="ACT_DROPDOWN_CHANGE_PROCESSING_WAIT_MS", default_ms=5000
        )
        component_state = facade._act_select_option_state(locator)
        if facade._act_select_option_postcondition(
            before_state, component_state, option_args, option_kwargs
        ):
            if inferred_committed_option:
                component_details["committed_index_source"] = "adf_marker"
                component_details["committed_value"] = str(
                    inferred_committed_option.get("value") or ""
                ).strip()
                component_details["committed_label"] = str(
                    inferred_committed_option.get("label") or ""
                ).strip()
            return component_details
    if facade._act_oracle_adf_commit_events(locator):
        current_page.wait_for_timeout(wait_ms)
        facade._act_wait_for_field_processing(
            current_page, env_name="ACT_DROPDOWN_CHANGE_PROCESSING_WAIT_MS", default_ms=5000
        )
        event_state = facade._act_select_option_state(locator)
        if facade._act_select_option_postcondition(
            before_state, event_state, option_args, option_kwargs
        ):
            return {
                "strategy_name": "oracle_adf_select_event_commit",
                "target_index": target_index if target_index is not None else -1,
                "target_value": str(target.get("value") or "").strip(),
                "target_label": str(target.get("label") or "").strip(),
            }
    return None


def _act_select_option_target(
    locator: Locator,
    current_page: Page,
    label: str,
    option_args: list[Any] | None,
    option_kwargs: dict[str, Any] | None,
) -> None:
    facade._act_register_page(current_page)
    target_description = json.dumps(
        facade._act_clone_json_value({"args": option_args or [], "kwargs": option_kwargs or {}}),
        ensure_ascii=False,
    )
    debug_trace: dict[str, Any] = {
        "label": label,
        "target_description": facade._act_clone_json_value(
            {"args": option_args or [], "kwargs": option_kwargs or {}}
        ),
    }
    facade._act_set_debug_detail("select_option_target", debug_trace)
    initial_target = facade._act_resolve_select_target(locator, option_args, option_kwargs)
    debug_trace["initial_target"] = facade._act_clone_json_value(initial_target)
    facade._act_set_debug_detail("select_option_target", debug_trace)
    before_state = facade._act_select_option_state(locator)
    debug_trace["before_state"] = facade._act_clone_json_value(before_state)
    facade._act_set_debug_detail("select_option_target", debug_trace)
    # Fast-fail on a DISABLED dependent target before burning the full strict timeout + AI rounds.
    # Deterministic Oracle tier: a dependent LOV (e.g. Requisitioning BU off Procurement BU) sits
    # disabled until its dependency settles, or stays disabled because it is auto-derived. AI can
    # never enable a disabled control, so once a bounded enable-wait elapses we stop with a real
    # reason instead of ~30s of waiting + minutes of futile self-repair.
    enablement = facade._act_wait_for_select_target_enabled(
        locator, current_page, env_name="ACT_DEPENDENT_SELECT_ENABLE_WAIT_MS", default_ms=8000
    )
    debug_trace["target_enablement"] = enablement
    facade._act_set_debug_detail("select_option_target", debug_trace)
    if enablement == "disabled":
        # Re-read: the value may have finished auto-deriving during the enable-wait.
        disabled_state = facade._act_select_option_state(locator)
        debug_trace["disabled_state"] = facade._act_clone_json_value(disabled_state)
        # Auto-derived field that ALREADY holds the requested value -> skip-pass (no-op), so the
        # recording does not have to drop the step. Only a disabled field whose value DIFFERS is a
        # real failure (a disabled control cannot be set, and AI cannot enable it).
        if facade._act_select_target_already_satisfied(disabled_state, option_args, option_kwargs):
            debug_trace["status"] = "disabled_value_already_satisfied"
            facade._act_set_debug_detail("select_option_target", debug_trace)
            facade._act_set_recovery_record(
                "oracle_handler",
                "disabled_target_value_already_set",
                "disabled_target_value_already_set",
                {"target": target_description, "current": disabled_state},
            )
            facade._act_store_experience_episode(
                action_type="select_option_target",
                label=label,
                page=current_page,
                locator=locator,
                error=None,
                status="success",
                postcondition_kind="option_selected",
                postcondition_passed=True,
            )
            return
        disabled_reason = facade._act_disabled_target_reason(locator)
        debug_trace["status"] = "disabled_fast_fail"
        debug_trace["disabled_reason"] = facade._act_clone_json_value(disabled_reason)
        facade._act_set_debug_detail("select_option_target", debug_trace)
        current_display = facade._act_select_state_display(disabled_state)
        source = str(disabled_reason.get("source") or "unknown").strip()
        raise RuntimeError(
            f'Select target "{label}" is disabled (disabled via {source}) and its current value '
            f"({current_display}) does not match the requested {target_description}. A disabled "
            "field cannot be set: set its controlling field to the value that derives the requested "
            "one, or the requested value is not valid for the current dependency. Failed fast."
        )
    try:
        facade._act_apply_select_option(locator, option_args, option_kwargs)
        # Trusted Tab so ADF runs its real autosubmit PPR and the server re-evaluates dependent
        # fields' enablement (select_option only fires an UNTRUSTED change, which ADF ignores --
        # leaving a downstream server-gated LOV like Supplier stuck disabled).
        if facade._act_env_flag("ACT_SELECT_BLUR_COMMIT", "true"):
            debug_trace["blur_commit"] = facade._act_commit_select_blur(locator)
            facade._act_set_debug_detail("select_option_target", debug_trace)
        facade._act_wait_for_field_processing(
            current_page, env_name="ACT_DROPDOWN_CHANGE_PROCESSING_WAIT_MS", default_ms=5000
        )
        after_state = facade._act_select_option_state(locator)
        direct_postcondition = facade._act_select_option_postcondition(
            before_state, after_state, option_args, option_kwargs
        )
        debug_trace["after_state"] = facade._act_clone_json_value(after_state)
        debug_trace["direct_postcondition_passed"] = direct_postcondition
        after_state_contradicted = facade._act_adf_select_commit_contradicted(after_state)
        debug_trace["adf_commit_contradicted"] = after_state_contradicted
        facade._act_set_debug_detail("select_option_target", debug_trace)
        if direct_postcondition:
            return
        oracle_recovery = None
        if after_state_contradicted:
            oracle_recovery = facade._act_try_commit_oracle_adf_select(
                locator, current_page, before_state, option_args, option_kwargs
            )
            debug_trace["oracle_adf_commit_recovery"] = facade._act_clone_json_value(
                oracle_recovery
            )
            facade._act_set_debug_detail("select_option_target", debug_trace)
            if oracle_recovery:
                facade._act_set_recovery_record(
                    "oracle_handler",
                    "oracle_adf_select_commit",
                    "oracle_adf_select_commit",
                    oracle_recovery,
                )
                facade._act_store_experience_episode(
                    action_type="select_option_target",
                    label=label,
                    page=current_page,
                    locator=locator,
                    error=None,
                    status="success",
                    postcondition_kind="option_selected",
                    postcondition_passed=True,
                )
                return
        semantic_target = initial_target or facade._act_resolve_select_target(
            locator, option_args, option_kwargs
        )
        semantic_label = str(semantic_target.get("label") or "").strip()
        debug_trace["semantic_target"] = facade._act_clone_json_value(semantic_target)
        semantic_match = (
            not after_state_contradicted
            and bool(semantic_label)
            and facade._act_oracle_label_value_matches(current_page, label, semantic_label)
        )
        debug_trace["semantic_label_match"] = semantic_match
        facade._act_set_debug_detail("select_option_target", debug_trace)
        if semantic_match:
            facade._act_set_recovery_record(
                "oracle_handler",
                "oracle_label_value_already_selected",
                "oracle_label_value_already_selected",
                {
                    "target_value": str(semantic_target.get("value") or "").strip(),
                    "target_label": semantic_label,
                },
            )
            facade._act_store_experience_episode(
                action_type="select_option_target",
                label=label,
                page=current_page,
                locator=locator,
                error=None,
                status="success",
                postcondition_kind="option_selected",
                postcondition_passed=True,
            )
            return
        if "oracle_adf_commit_recovery" not in debug_trace:
            debug_trace["oracle_adf_commit_recovery"] = None
            facade._act_set_debug_detail("select_option_target", debug_trace)
        oracle_searchselect_recovery = facade._act_try_oracle_searchselect_select_option_recovery(
            locator, current_page, label, option_args, option_kwargs, resolved_target=initial_target
        )
        debug_trace["oracle_searchselect_recovery"] = facade._act_clone_json_value(
            oracle_searchselect_recovery
        )
        facade._act_set_debug_detail("select_option_target", debug_trace)
        if oracle_searchselect_recovery:
            facade._act_set_recovery_record(
                "oracle_handler",
                "oracle_searchselect_select_option",
                "oracle_searchselect_select_option",
                oracle_searchselect_recovery,
            )
            facade._act_store_experience_episode(
                action_type="select_option_target",
                label=label,
                page=current_page,
                locator=locator,
                error=None,
                status="success",
                postcondition_kind="option_selected",
                postcondition_passed=True,
            )
            return
        debug_trace["status"] = "direct_failed"
        facade._act_set_debug_detail("select_option_target", debug_trace)
        raise RuntimeError(
            f'Select "{label}" did not reflect the requested option selection {target_description}.'
        )
    except Exception as direct_exc:
        debug_trace["direct_error"] = facade._act_trim_debug_text(direct_exc, 400)
        facade._act_set_debug_detail("select_option_target", debug_trace)
        last_error: Exception = direct_exc
        for strategy_name, experience_locator, episode in facade._act_experience_repair_locators(
            current_page, "select_option_target", label, direct_exc, locator=locator
        ):
            try:
                facade._act_record_strategy_attempt(strategy_name)
                before_experience = facade._act_select_option_state(experience_locator)
                facade._act_apply_select_option(experience_locator, option_args, option_kwargs)
                facade._act_wait_for_field_processing(
                    current_page, env_name="ACT_DROPDOWN_CHANGE_PROCESSING_WAIT_MS", default_ms=5000
                )
                after_experience = facade._act_select_option_state(experience_locator)
                if facade._act_select_option_postcondition(
                    before_experience, after_experience, option_args, option_kwargs
                ):
                    debug_trace["experience_reuse"] = {
                        "strategy_name": strategy_name,
                        "status": "success",
                    }
                    facade._act_set_debug_detail("select_option_target", debug_trace)
                    facade._act_set_recovery_record(
                        "experience_reuse",
                        str((episode.get("recovery") or {}).get("kind") or "").strip()
                        or "experience_reuse",
                        "experience_reuse",
                        {
                            "episode_id": str(episode.get("episode_id") or "").strip(),
                            "retrieval_score": int(episode.get("retrieval_score") or 0),
                            "locator_strategy": facade._act_clone_json_value(
                                ((episode.get("recovery") or {}).get("details") or {}).get(
                                    "locator_strategy"
                                )
                                or {}
                            ),
                        },
                    )
                    facade._act_store_experience_episode(
                        action_type="select_option_target",
                        label=label,
                        page=current_page,
                        locator=experience_locator,
                        error=direct_exc,
                        status="success",
                        postcondition_kind="option_selected",
                        postcondition_passed=True,
                    )
                    return
                if facade._act_try_commit_oracle_adf_select(
                    experience_locator, current_page, before_experience, option_args, option_kwargs
                ):
                    debug_trace["experience_reuse"] = {
                        "strategy_name": strategy_name,
                        "status": "oracle_commit_success",
                    }
                    facade._act_set_debug_detail("select_option_target", debug_trace)
                    facade._act_set_recovery_record(
                        "experience_reuse",
                        str((episode.get("recovery") or {}).get("kind") or "").strip()
                        or "experience_reuse",
                        "experience_reuse",
                        {
                            "episode_id": str(episode.get("episode_id") or "").strip(),
                            "retrieval_score": int(episode.get("retrieval_score") or 0),
                            "locator_strategy": facade._act_clone_json_value(
                                ((episode.get("recovery") or {}).get("details") or {}).get(
                                    "locator_strategy"
                                )
                                or {}
                            ),
                        },
                    )
                    facade._act_store_experience_episode(
                        action_type="select_option_target",
                        label=label,
                        page=current_page,
                        locator=experience_locator,
                        error=direct_exc,
                        status="success",
                        postcondition_kind="option_selected",
                        postcondition_passed=True,
                    )
                    return
                last_error = RuntimeError(
                    f'Experience strategy "{strategy_name}" did not satisfy select_option for "{label}".'
                )
                debug_trace["experience_reuse"] = {
                    "strategy_name": strategy_name,
                    "status": "postcondition_failed",
                    "error": facade._act_trim_debug_text(last_error, 300),
                }
                facade._act_set_debug_detail("select_option_target", debug_trace)
            except Exception as exc:
                last_error = exc
                debug_trace["experience_reuse"] = {
                    "strategy_name": strategy_name,
                    "status": "failed",
                    "error": facade._act_trim_debug_text(exc, 300),
                }
                facade._act_set_debug_detail("select_option_target", debug_trace)

        def _execute_ai_select_locator(
            strategy_name: str, ai_locator: Locator, ai_strategy: dict[str, Any]
        ) -> bool:
            before_ai = facade._act_select_option_state(ai_locator)
            facade._act_apply_select_option(ai_locator, option_args, option_kwargs)
            facade._act_wait_for_field_processing(
                current_page, env_name="ACT_DROPDOWN_CHANGE_PROCESSING_WAIT_MS", default_ms=5000
            )
            after_ai = facade._act_select_option_state(ai_locator)
            if facade._act_select_option_postcondition(
                before_ai, after_ai, option_args, option_kwargs
            ):
                return True
            return bool(
                facade._act_try_commit_oracle_adf_select(
                    ai_locator, current_page, before_ai, option_args, option_kwargs
                )
            )

        ai_result, last_error = facade._act_execute_ai_repair_rounds(
            current_page=current_page,
            helper="select_option_target",
            label=label,
            last_error=last_error,
            value=target_description,
            locator=locator,
            postcondition_kind="option_selected",
            failure_message=lambda strategy_name: f'AI strategy "{strategy_name}" did not satisfy select_option for "{label}".',
            execute_locator=_execute_ai_select_locator,
        )
        if ai_result is not None:
            strategy_name, ai_locator, ai_strategy = ai_result
            debug_trace["ai_repair"] = {
                "strategy_name": strategy_name,
                "status": "success",
                "locator_strategy": facade._act_clone_json_value(ai_strategy),
            }
            facade._act_set_debug_detail("select_option_target", debug_trace)
            facade._act_set_recovery_record(
                "ai_validated",
                "ai_locator_repair",
                "ai_locator_repair",
                {
                    "strategy_name": strategy_name,
                    "locator_strategy": facade._act_clone_json_value(ai_strategy),
                },
            )
            facade._act_store_experience_episode(
                action_type="select_option_target",
                label=label,
                page=current_page,
                locator=ai_locator,
                error=direct_exc,
                status="success",
                postcondition_kind="option_selected",
                postcondition_passed=True,
            )
            return
        debug_trace["ai_repair"] = {
            "status": "failed",
            "error": facade._act_trim_debug_text(last_error, 300),
        }
        debug_trace["status"] = "failed"
        facade._act_set_debug_detail("select_option_target", debug_trace)
        raise RuntimeError(
            f'Unable to apply select_option for "{label}" with target {target_description}.'
        ) from last_error
