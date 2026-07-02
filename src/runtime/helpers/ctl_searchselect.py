"""Auto-split from helpers_v2.py. `facade` is the helpers_v2 facade: the single
shared namespace, so monkeypatching helpers_v2.X and shared _ACT_* state
behave exactly as in the original module. Call shared helpers via `facade.`."""

from __future__ import annotations

import time
from typing import Any

from playwright.sync_api import Locator, Page

try:
    from .. import helpers_v2 as facade
except ImportError:  # pragma: no cover
    from src.runtime import helpers_v2 as facade

__all__ = [
    "_act_oracle_label_value_matches",
    "_act_try_oracle_searchselect_select_option_recovery",
    "_act_oracle_searchselect_state",
    "_act_oracle_searchselect_query_matches",
    "_act_wait_for_oracle_searchselect_query",
    "_act_try_oracle_lov_trigger_direct_entry",
    "_act_wait_for_search_option_surface",
    "_act_select_search_trigger_option",
]


def _act_oracle_label_value_matches(page: Page | None, label: str, expected_value: str) -> bool:
    normalized_label = facade._act_normalize_text(label)
    normalized_value = facade._act_normalize_text(expected_value)
    if not normalized_label or not normalized_value:
        return False
    result = facade._act_safe_page_eval(
        page,
        '(payload) => {\n            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim().toLowerCase();\n            const targetLabel = normalize(payload?.label);\n            const targetValue = normalize(payload?.value);\n            if (!targetLabel || !targetValue) return false;\n\n            const isVisible = (node) => {\n                if (!node) return false;\n                const style = window.getComputedStyle(node);\n                if (!style) return false;\n                if (style.display === "none" || style.visibility === "hidden") return false;\n                const rect = node.getBoundingClientRect();\n                return rect.width > 0 && rect.height > 0;\n            };\n            const textOf = (node) => normalize(node?.innerText || node?.textContent || "");\n            const matchesValue = (text) => {\n                if (!text) return false;\n                return text === targetValue || text.includes(targetValue) || targetValue.includes(text);\n            };\n            const containsLabel = (text) => {\n                if (!text) return false;\n                return text === targetLabel || text.includes(targetLabel) || targetLabel.includes(text);\n            };\n            const controlValues = (node) => {\n                if (!node) return [];\n                const values = [];\n                const append = (value) => {\n                    const normalized = normalize(value);\n                    if (normalized) values.push(normalized);\n                };\n                append(node.value);\n                append(node.getAttribute?.("value"));\n                append(node.getAttribute?.("aria-valuetext"));\n                append(node.getAttribute?.("aria-label"));\n                const selectedOptions = Array.from(node.selectedOptions || []);\n                for (const option of selectedOptions) {\n                    append(option?.label || option?.innerText || option?.textContent);\n                    append(option?.value);\n                }\n                append(node.innerText || node.textContent);\n                return Array.from(new Set(values.filter(Boolean)));\n            };\n            const controlMatches = (node) => controlValues(node).some(matchesValue);\n            const regionMatches = (text) => {\n                if (!text) return false;\n                if (!containsLabel(text)) return false;\n                if (matchesValue(text)) return true;\n                const withoutLabel = normalize(text.replace(payload?.label || "", " "));\n                if (matchesValue(withoutLabel)) return true;\n                return false;\n            };\n            const nearbyControlMatches = (node) => {\n                if (!node) return false;\n                const controls = [];\n                const add = (candidate) => {\n                    if (!candidate || controls.includes(candidate)) return;\n                    controls.push(candidate);\n                };\n                const pushFrom = (root) => {\n                    if (!root || !root.querySelectorAll) return;\n                    for (const candidate of root.querySelectorAll("select, input, textarea, [role=\'combobox\'], oj-select-single, oj-c-select-single")) {\n                        add(candidate);\n                    }\n                };\n                const forId = normalize(node.getAttribute?.("for"));\n                if (forId) add(document.getElementById(forId));\n                pushFrom(node);\n                pushFrom(node.parentElement);\n                pushFrom(node.closest("tr, .oj-formlayout-row, .oj-flex, .oj-sm-flex, .oj-form, .AFStretchWidth"));\n                pushFrom(node.parentElement?.parentElement);\n                return controls.some((candidate) => isVisible(candidate) && controlMatches(candidate));\n            };\n            const candidateSelectors = "label, oj-label, span, div, td, th";\n\n            for (const node of document.querySelectorAll(candidateSelectors)) {\n                if (!isVisible(node)) continue;\n                const labelText = textOf(node);\n                if (!labelText || !containsLabel(labelText)) continue;\n                if (regionMatches(labelText)) return true;\n                if (nearbyControlMatches(node)) return true;\n\n                const siblingCandidates = [\n                    node.nextElementSibling,\n                    node.parentElement?.nextElementSibling,\n                ].filter(Boolean);\n                for (const candidate of siblingCandidates) {\n                    const candidateText = textOf(candidate);\n                    if (matchesValue(candidateText)) return true;\n                    if (controlMatches(candidate)) return true;\n                }\n\n                const parent = node.parentElement;\n                if (parent) {\n                    if (regionMatches(textOf(parent))) return true;\n                    const rowCandidates = Array.from(parent.children || [])\n                        .filter((child) => child !== node)\n                        .map((child) => textOf(child))\n                        .filter(Boolean);\n                    if (rowCandidates.some(matchesValue)) return true;\n                }\n\n                const ownRow = node.closest("tr, .oj-formlayout-row, .oj-flex, .oj-sm-flex, .oj-form, .AFStretchWidth");\n                if (ownRow) {\n                    if (regionMatches(textOf(ownRow))) return true;\n                    if (nearbyControlMatches(ownRow)) return true;\n                    const rowTextCandidates = Array.from(ownRow.querySelectorAll("span, div, td, th, a"))\n                        .filter((candidate) => candidate !== node && isVisible(candidate))\n                        .map((candidate) => textOf(candidate))\n                        .filter(Boolean);\n                    if (rowTextCandidates.some(matchesValue)) return true;\n                }\n\n                const grandParent = parent?.parentElement;\n                if (grandParent && regionMatches(textOf(grandParent))) return true;\n            }\n            return false;\n        }',
        {"label": label, "value": expected_value},
    )
    return bool(result)


def _act_try_oracle_searchselect_select_option_recovery(
    locator: Locator,
    current_page: Page,
    label: str,
    option_args: list[Any] | None,
    option_kwargs: dict[str, Any] | None,
    resolved_target: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    metadata = facade._act_extract_locator_metadata(locator)
    target = facade._act_clone_json_value(
        resolved_target or {}
    ) or facade._act_resolve_select_target(locator, option_args, option_kwargs)
    expectations = facade._act_select_option_expectations(option_args, option_kwargs)
    debug_trace: dict[str, Any] = {
        "label": label,
        "metadata": facade._act_clone_json_value(metadata),
        "target": facade._act_clone_json_value(target),
        "expectations": facade._act_clone_json_value(expectations),
        "trigger_attempts": [],
    }
    option_name = str(target.get("label") or "").strip()
    if not option_name:
        explicit_labels = [
            str(item or "").strip()
            for item in expectations.get("labels") or []
            if str(item or "").strip()
        ]
        explicit_values = [
            str(item or "").strip()
            for item in expectations.get("values") or []
            if str(item or "").strip()
        ]
        option_name = (explicit_labels[0] if explicit_labels else "") or (
            explicit_values[0] if explicit_values else ""
        )
    if not option_name:
        debug_trace["status"] = "skipped_missing_option_name"
        facade._act_set_debug_detail("oracle_searchselect_recovery", debug_trace)
        return None
    fill_value = str(target.get("label") or "").strip() or option_name
    trigger_candidates: list[tuple[str, Locator]] = []
    direct_tag = str(metadata.get("tag") or "").strip().lower()
    direct_role = str(metadata.get("role") or "").strip().lower()
    oracle_host_tag = str(metadata.get("oracle_host_tag") or "").strip().lower()
    if (
        direct_role == "combobox"
        or direct_tag in {"oj-select-single", "oj-c-select-single"}
        or oracle_host_tag in {"oj-select-single", "oj-c-select-single"}
    ):
        trigger_candidates.append(("oracle_searchselect_direct", locator))
    get_by_role = getattr(current_page, "get_by_role", None)
    if callable(get_by_role):
        try:
            trigger_candidates.append(
                (
                    "oracle_searchselect_role_combobox",
                    current_page.get_by_role("combobox", name=label, exact=True),
                )
            )
        except Exception:
            pass
    seen_trigger_ids: set[int] = set()
    last_error: Exception | None = None
    for strategy_name, candidate in trigger_candidates:
        attempt: dict[str, Any] = {"strategy_name": strategy_name}
        try:
            resolved = candidate.first if hasattr(candidate, "first") else candidate
        except Exception:
            resolved = candidate
        if resolved is None:
            attempt["status"] = "empty_candidate"
            debug_trace["trigger_attempts"].append(attempt)
            continue
        resolved_id = id(resolved)
        if resolved_id in seen_trigger_ids:
            attempt["status"] = "duplicate_candidate"
            debug_trace["trigger_attempts"].append(attempt)
            continue
        seen_trigger_ids.add(resolved_id)
        label_match = facade._act_ai_locator_matches_label(resolved, label)
        attempt["label_match"] = label_match
        if not label_match:
            attempt["status"] = "label_mismatch"
            debug_trace["trigger_attempts"].append(attempt)
            continue
        try:
            option_locator = current_page.get_by_text(option_name, exact=True)
            facade._act_select_search_trigger_option(
                resolved,
                option_locator,
                current_page,
                label,
                option_name,
                option_exact=True,
                fill_value=fill_value,
                allow_repair=False,
            )
            attempt["status"] = "success"
            debug_trace["trigger_attempts"].append(attempt)
            debug_trace["status"] = "success"
            facade._act_set_debug_detail("oracle_searchselect_recovery", debug_trace)
            return {
                "strategy_name": strategy_name,
                "target_index": int(target.get("index"))
                if isinstance(target.get("index"), int)
                else -1,
                "target_value": str(target.get("value") or "").strip(),
                "target_label": str(target.get("label") or "").strip(),
                "option_name": option_name,
                "fill_value": fill_value,
            }
        except Exception as exc:
            last_error = exc
            attempt["status"] = "failed"
            attempt["error"] = facade._act_trim_debug_text(exc, 400)
            debug_trace["trigger_attempts"].append(attempt)
    debug_trace["status"] = "failed"
    if last_error is not None:
        debug_trace["last_error"] = facade._act_trim_debug_text(last_error, 400)
    facade._act_set_debug_detail("oracle_searchselect_recovery", debug_trace)
    return None


def _act_oracle_searchselect_state(page: Page | None) -> dict[str, Any]:
    state = facade._act_safe_page_eval(
        page,
        '() => {\n            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();\n            const isVisible = (node) => {\n                if (!node) return false;\n                const style = window.getComputedStyle(node);\n                if (!style) return false;\n                if (style.visibility === "hidden" || style.display === "none") return false;\n                const rect = node.getBoundingClientRect();\n                return rect.width > 0 && rect.height > 0;\n            };\n            const searchInputSelector = [\n                "input[role=\'combobox\']",\n                "input.oj-inputtext-input",\n                "input.oj-searchselect-input",\n                "input.oj-searchselect-filter-text-field"\n            ].join(", ");\n            const inferHostIdFromFilterInput = (node) => {\n                const id = normalize(node?.id);\n                if (!id) return "";\n                const match = id.match(/^oj-searchselect-filter-(.+?)(?:\\|input)?$/);\n                return normalize(match?.[1]);\n            };\n\n            const hosts = Array.from(document.querySelectorAll("oj-select-single, oj-c-select-single"))\n                .filter((host) => {\n                    if (!isVisible(host)) return false;\n                    return (\n                        host.classList?.contains("oj-listbox-dropdown-open")\n                        || Boolean(host.querySelector?.(".oj-searchselect-filter"))\n                        || Boolean(host.querySelector?.(".oj-searchselect-filter-text-field"))\n                        || Boolean(host.querySelector?.("input[role=\'combobox\'][aria-expanded=\'true\']"))\n                    );\n                });\n\n            const activeElement = document.activeElement;\n            const activeFilterInput = activeElement?.matches?.(searchInputSelector) ? activeElement : null;\n            const inferredHostId = inferHostIdFromFilterInput(activeFilterInput);\n\n            let host = activeElement?.closest?.("oj-select-single, oj-c-select-single");\n            if (!host && inferredHostId) {\n                const inferredHost = document.getElementById(inferredHostId);\n                if (inferredHost?.matches?.("oj-select-single, oj-c-select-single")) {\n                    host = inferredHost;\n                }\n            }\n            if (!host && hosts.length === 1) host = hosts[0];\n            if (!host && !activeFilterInput) return {};\n            if (host && !isVisible(host) && !activeFilterInput) return {};\n\n            const liveRegion = host?.querySelector?.(".oj-listbox-liveregion");\n            const filterInput = activeFilterInput || host?.querySelector?.(searchInputSelector);\n            const liveText = normalize(liveRegion?.innerText || liveRegion?.textContent);\n            const hostText = normalize(host?.innerText || host?.textContent);\n            const filterValue = normalize(\n                (filterInput && "value" in filterInput ? filterInput.value : "")\n                || filterInput?.getAttribute?.("value")\n            );\n\n            return {\n                host_id: normalize(host?.id || inferredHostId),\n                open: true,\n                no_matches: /no matches found/i.test(liveText || hostText),\n                live_text: liveText,\n                host_text: hostText,\n                filter_value: filterValue,\n            };\n        }',
    )
    return state if isinstance(state, dict) else {}


def _act_oracle_searchselect_query_matches(state: dict[str, Any], requested_query: str) -> bool:
    desired = facade._act_normalize_text(requested_query)
    if not desired:
        return True
    actual = facade._act_normalize_text((state or {}).get("filter_value"))
    return bool(actual) and actual == desired


def _act_wait_for_oracle_searchselect_query(
    page: Page | None, requested_query: str, *, timeout_ms: int | None = None
) -> tuple[dict[str, Any], bool]:
    timeout = facade._act_resolve_wait_override_ms(
        timeout_ms, "ACT_SEARCH_QUERY_REFLECT_TIMEOUT_MS", 1500
    )
    deadline = time.time() + max(timeout, 0) / 1000.0
    last_state: dict[str, Any] = {}
    while True:
        last_state = facade._act_oracle_searchselect_state(page)
        if facade._act_oracle_searchselect_query_matches(last_state, requested_query):
            return (last_state, True)
        if time.time() >= deadline:
            return (last_state, False)
        if page is None:
            return (last_state, False)
        page.wait_for_timeout(min(100, max(1, int((deadline - time.time()) * 1000))))


def _act_try_oracle_lov_trigger_direct_entry(
    trigger: Locator,
    current_page: Page,
    title: str,
    option_name: str,
    *,
    fill_value: str | None = None,
) -> dict[str, Any] | None:
    normalized_title = str(title or "").strip()
    if not normalized_title.lower().startswith("search:"):
        return None
    field_label = normalized_title.split(":", 1)[1].strip()
    requested_value = str(fill_value or option_name or "").strip()
    if not field_label or not requested_value:
        return None
    metadata = facade._act_extract_locator_metadata(trigger)
    trigger_id = str(metadata.get("id") or "").strip()
    candidate_builders: list[tuple[str, Locator]] = []
    page_locator = getattr(current_page, "locator", None)
    if trigger_id.endswith("::lovIconId") and callable(page_locator):
        base_id = trigger_id[: -len("::lovIconId")]
        if base_id:
            candidate_builders.append(
                ("oracle_lov_icon_content_id", current_page.locator(f'[id="{base_id}::content"]'))
            )
    get_by_role = getattr(current_page, "get_by_role", None)
    if callable(get_by_role):
        try:
            candidate_builders.append(
                (
                    "oracle_lov_combobox_label",
                    current_page.get_by_role("combobox", name=field_label, exact=True),
                )
            )
        except Exception:
            pass
        try:
            candidate_builders.append(
                (
                    "oracle_lov_textbox_label",
                    current_page.get_by_role("textbox", name=field_label, exact=True),
                )
            )
        except Exception:
            pass
    timeout_ms = facade._act_wait_ms("ACT_ACTION_TIMEOUT_MS", 3000)
    for strategy_name, candidate in candidate_builders:
        try:
            resolved = candidate.first if hasattr(candidate, "first") else candidate
            candidate_metadata = facade._act_extract_locator_metadata(resolved)
            candidate_tag = str(candidate_metadata.get("tag") or "").strip().lower()
            candidate_role = str(candidate_metadata.get("role") or "").strip().lower()
            if candidate_tag not in {"input", "textarea", "select"} and candidate_role not in {
                "combobox",
                "textbox",
            }:
                continue
            if str(candidate_metadata.get("disabled") or "").strip().lower() == "true":
                continue
            if str(candidate_metadata.get("aria_disabled") or "").strip().lower() == "true":
                continue
            if not facade._act_locator_visible(resolved, timeout_ms=300):
                continue
            facade._act_record_strategy_attempt(strategy_name)
            facade._act_enter_search_value(
                resolved,
                requested_value,
                timeout_ms=facade._act_wait_ms("ACT_TEXT_ENTRY_TIMEOUT_MS", 3000),
                current_page=current_page,
                label=field_label,
            )
            try:
                resolved.press("Tab", timeout=timeout_ms)
            except TypeError:
                resolved.press("Tab")
            current_page.wait_for_timeout(
                facade._act_wait_ms("ACT_LOV_DIRECT_ENTRY_COMMIT_WAIT_MS", 350)
            )
            facade._act_wait_for_field_processing(
                current_page, env_name="ACT_DROPDOWN_CHANGE_PROCESSING_WAIT_MS", default_ms=5000
            )
            observed = facade._act_locator_value(resolved) or facade._act_locator_text(resolved)
            if facade._act_value_matches(
                option_name, observed
            ) or facade._act_oracle_label_value_matches(current_page, field_label, option_name):
                return {
                    "strategy_name": strategy_name,
                    "field_label": field_label,
                    "requested_value": requested_value,
                    "observed_value": observed,
                }
        except Exception:
            continue
    return None


def _act_wait_for_search_option_surface(
    page: Page | None, option: Locator, *, timeout_ms: int | None = None
) -> dict[str, bool]:
    timeout = facade._act_resolve_wait_override_ms(
        timeout_ms, "ACT_SEARCH_SURFACE_TIMEOUT_MS", 1500
    )
    deadline = time.time() + max(timeout, 0) / 1000.0
    poll_ms = max(100, facade._act_wait_ms("ACT_SEARCH_SURFACE_POLL_MS", 150))
    while True:
        option_visible = facade._act_locator_visible(option, timeout_ms=min(250, max(timeout, 0)))
        popup_open = bool(
            facade._act_safe_page_eval(
                page,
                '(selectors) => {\n                    const isVisible = (node) => {\n                        if (!node) return false;\n                        const style = window.getComputedStyle(node);\n                        if (!style) return false;\n                        if (style.display === "none" || style.visibility === "hidden") return false;\n                        const rect = node.getBoundingClientRect();\n                        return rect.width > 0 && rect.height > 0;\n                    };\n\n                    for (const selector of selectors || []) {\n                        const nodes = Array.from(document.querySelectorAll(selector.replace(/:visible/g, "")));\n                        if (nodes.some(isVisible)) return true;\n                    }\n                    return false;\n                }',
                list(facade._ACT_POPUP_SCOPE_SELECTORS),
            )
        )
        if option_visible or popup_open or time.time() >= deadline:
            return {"option_visible": option_visible, "popup_open": popup_open}
        if page is None:
            return {"option_visible": option_visible, "popup_open": popup_open}
        page.wait_for_timeout(min(poll_ms, max(1, int((deadline - time.time()) * 1000))))


def _act_select_search_trigger_option(
    trigger: Locator,
    option: Locator,
    current_page: Page,
    title: str,
    option_name: str,
    *,
    option_kind: str = "text",
    option_exact: bool | None = None,
    fill_value: str | None = None,
    allow_repair: bool = True,
) -> None:
    facade._act_register_page(current_page)
    debug_trace = facade._act_update_debug_detail(
        "select_search_trigger_option",
        {
            "title": title,
            "option_name": option_name,
            "fill_value": facade._act_trim_debug_text(fill_value, 120),
            "status": "open_or_search",
            "option_attempts": [],
            "experience_attempts": [],
        },
    )
    search_timeout_ms = facade._act_wait_ms("ACT_TEXT_ENTRY_TIMEOUT_MS", 3000)
    raw_option_timeout_ms = facade._act_wait_ms("ACT_SEARCH_RESULT_TIMEOUT_MS", 6000)
    option_candidate_timeout_ms = facade._act_wait_ms(
        "ACT_SEARCH_OPTION_CANDIDATE_TIMEOUT_MS", 1500
    )
    option_already_visible = False
    if fill_value is not None:
        facade._act_enter_search_value(
            trigger,
            fill_value,
            timeout_ms=search_timeout_ms,
            current_page=current_page,
            label=title,
        )
    else:
        facade._act_strict_click(trigger)
    current_page.wait_for_timeout(facade._act_wait_ms("ACT_SEARCH_RESULTS_WAIT_MS", 750))
    oracle_search_state = facade._act_oracle_searchselect_state(current_page)
    if fill_value is not None:
        oracle_search_state, query_reflected = facade._act_wait_for_oracle_searchselect_query(
            current_page,
            fill_value,
            timeout_ms=facade._act_wait_ms("ACT_SEARCH_QUERY_REFLECT_TIMEOUT_MS", 1500),
        )
        if not query_reflected:
            facade._act_enter_search_value(
                trigger,
                fill_value,
                timeout_ms=search_timeout_ms,
                current_page=current_page,
                label=title,
            )
            current_page.wait_for_timeout(facade._act_wait_ms("ACT_SEARCH_RESULTS_WAIT_MS", 750))
            oracle_search_state, query_reflected = facade._act_wait_for_oracle_searchselect_query(
                current_page,
                fill_value,
                timeout_ms=facade._act_wait_ms("ACT_SEARCH_QUERY_REFLECT_TIMEOUT_MS", 1500),
            )
            if not query_reflected:
                option_already_visible = facade._act_locator_visible(
                    option, timeout_ms=raw_option_timeout_ms
                )
            if not query_reflected and (not option_already_visible):
                debug_trace["oracle_search_state"] = {
                    "open": bool(oracle_search_state.get("open")),
                    "no_matches": bool(oracle_search_state.get("no_matches")),
                    "filter_value": facade._act_trim_debug_text(
                        oracle_search_state.get("filter_value"), 160
                    ),
                    "live_text": facade._act_trim_debug_text(
                        oracle_search_state.get("live_text"), 160
                    ),
                    "query_reflected": False,
                    "option_already_visible": False,
                }
                debug_trace["status"] = "failed"
                visible_query = (
                    str(oracle_search_state.get("filter_value") or "").strip() or "unknown"
                )
                debug_trace["final_error"] = facade._act_trim_debug_text(
                    f'Oracle search-select "{title}" did not reflect requested query "{fill_value}". Visible query: "{visible_query}"',
                    320,
                )
                facade._act_set_debug_detail("select_search_trigger_option", debug_trace)
                raise RuntimeError(
                    f'Oracle search-select "{title}" did not reflect requested query "{fill_value}". Visible query: "{visible_query}"'
                )
        debug_trace["oracle_search_state"] = {
            "open": bool(oracle_search_state.get("open")),
            "no_matches": bool(oracle_search_state.get("no_matches")),
            "filter_value": facade._act_trim_debug_text(
                oracle_search_state.get("filter_value"), 160
            ),
            "live_text": facade._act_trim_debug_text(oracle_search_state.get("live_text"), 160),
            "query_reflected": bool(query_reflected),
            "option_already_visible": bool(option_already_visible),
        }
    if bool(oracle_search_state.get("no_matches")) and (not option_already_visible):
        option_already_visible = facade._act_locator_visible(
            option, timeout_ms=min(raw_option_timeout_ms, 750)
        )
    if bool(oracle_search_state.get("no_matches")) and (not option_already_visible):
        query_text = str(
            oracle_search_state.get("filter_value") or fill_value or option_name or ""
        ).strip()
        visible_state = (
            str(oracle_search_state.get("live_text") or "No matches found").strip()
            or "No matches found"
        )
        debug_trace["oracle_search_state"] = {
            "open": bool(oracle_search_state.get("open")),
            "no_matches": True,
            "filter_value": facade._act_trim_debug_text(query_text, 160),
            "live_text": facade._act_trim_debug_text(visible_state, 160),
            "query_reflected": debug_trace.get("oracle_search_state", {}).get("query_reflected"),
            "option_already_visible": bool(option_already_visible),
        }
        debug_trace["status"] = "failed"
        debug_trace["final_error"] = facade._act_trim_debug_text(
            f'Oracle search-select "{title}" returned no matches for query "{query_text}" while looking for option "{option_name}". Visible state: {visible_state}',
            320,
        )
        facade._act_set_debug_detail("select_search_trigger_option", debug_trace)
        raise RuntimeError(
            f'Oracle search-select "{title}" returned no matches for query "{query_text}" while looking for option "{option_name}". Visible state: {visible_state}'
        )
    search_surface = {"option_visible": False, "popup_open": False}
    oracle_lov_direct_entry: dict[str, Any] | None = None
    if fill_value is None and (not bool(oracle_search_state.get("open"))):
        search_surface = facade._act_wait_for_search_option_surface(
            current_page,
            option,
            timeout_ms=facade._act_wait_ms("ACT_SEARCH_SURFACE_TIMEOUT_MS", 1500),
        )
        debug_trace["search_surface"] = dict(search_surface)
        if not search_surface.get("option_visible") and (not search_surface.get("popup_open")):
            oracle_lov_direct_entry = facade._act_try_oracle_lov_trigger_direct_entry(
                trigger, current_page, title, option_name, fill_value=fill_value
            )
            if oracle_lov_direct_entry:
                debug_trace["oracle_lov_direct_entry"] = {
                    "status": "validated",
                    "details": facade._act_clone_json_value(oracle_lov_direct_entry),
                }
                debug_trace["resolved_by"] = "oracle_lov_direct_entry"
                debug_trace["status"] = "success"
                facade._act_set_debug_detail("select_search_trigger_option", debug_trace)
                facade._act_set_recovery_record(
                    "oracle_handler",
                    "oracle_lov_direct_entry",
                    "oracle_lov_direct_entry",
                    {"title": title, "option_name": option_name, **oracle_lov_direct_entry},
                )
                facade._act_store_experience_episode(
                    action_type="select_search_trigger_option",
                    label=str(option_name or "").strip(),
                    page=current_page,
                    locator=trigger,
                    error=None,
                    status="success",
                    postcondition_kind="option_selected",
                    postcondition_passed=True,
                )
                return
            debug_trace["oracle_lov_direct_entry"] = {"status": "not_applied"}
            debug_trace["status"] = "failed"
            debug_trace["final_error"] = facade._act_trim_debug_text(
                f'Search trigger "{title}" did not open a visible search surface for option "{option_name}".',
                320,
            )
            facade._act_set_debug_detail("select_search_trigger_option", debug_trace)
            raise RuntimeError(
                f'Search trigger "{title}" did not open a visible search surface for option "{option_name}".'
            )
    last_error: Exception | None = None
    option_target = str(option_name or "").strip()
    option_candidates = [
        ("raw_option", option),
        ("role_option", current_page.get_by_role("option", name=option_name)),
        ("role_cell", current_page.get_by_role("cell", name=option_name)),
        ("role_gridcell", current_page.get_by_role("gridcell", name=option_name)),
        (
            "text_option",
            current_page.get_by_text(option_name)
            if option_exact is None
            else current_page.get_by_text(option_name, exact=option_exact),
        ),
    ]
    for strategy_name, candidate in option_candidates:
        try:
            resolved = candidate.first if hasattr(candidate, "first") else candidate
            facade._act_record_strategy_attempt(strategy_name)
            before = facade._act_observe(current_page, resolved)
            timeout_ms = (
                raw_option_timeout_ms
                if strategy_name == "raw_option"
                else option_candidate_timeout_ms
            )
            facade._act_strict_click(resolved, timeout_ms=timeout_ms)
            current_page.wait_for_timeout(facade._act_wait_ms("ACT_POST_CLICK_WAIT_MS", 250))
            after = facade._act_observe(current_page, resolved)
            if facade._act_option_selection_postcondition(
                before, after, trigger, resolved, option_name
            ):
                facade._act_wait_for_field_processing(
                    current_page, env_name="ACT_DROPDOWN_CHANGE_PROCESSING_WAIT_MS", default_ms=5000
                )
                option_attempts = debug_trace.setdefault("option_attempts", [])
                if isinstance(option_attempts, list):
                    option_attempts.append(
                        {
                            "strategy_name": strategy_name,
                            "status": "validated",
                            "after": facade._act_debug_observation_summary(after),
                        }
                    )
                debug_trace["resolved_by"] = strategy_name
                debug_trace["status"] = "success"
                facade._act_set_debug_detail("select_search_trigger_option", debug_trace)
                return
            last_error = RuntimeError(
                f'Search trigger "{title}" did not apply option "{option_name}".'
            )
            option_attempts = debug_trace.setdefault("option_attempts", [])
            if isinstance(option_attempts, list):
                option_attempts.append(
                    {
                        "strategy_name": strategy_name,
                        "status": "postcondition_failed",
                        "error": facade._act_trim_debug_text(last_error, 320),
                    }
                )
        except Exception as exc:
            last_error = exc
            option_attempts = debug_trace.setdefault("option_attempts", [])
            if isinstance(option_attempts, list):
                option_attempts.append(
                    {
                        "strategy_name": strategy_name,
                        "status": "failed",
                        "error": facade._act_trim_debug_text(exc, 320),
                    }
                )
    if last_error is None:
        last_error = RuntimeError(f'Search trigger "{title}" did not apply option "{option_name}".')
    if not allow_repair:
        debug_trace["status"] = "failed"
        debug_trace["final_error"] = facade._act_trim_debug_text(last_error, 320)
        facade._act_set_debug_detail("select_search_trigger_option", debug_trace)
        raise RuntimeError(
            f'Unable to apply search option "{option_name}" for "{title}".'
        ) from last_error
    for strategy_name, experience_locator, episode in facade._act_experience_repair_locators(
        current_page, "select_search_trigger_option", option_target, last_error, locator=option
    ):
        try:
            facade._act_record_strategy_attempt(strategy_name)
            before_experience = facade._act_observe(current_page, experience_locator)
            facade._act_strict_click(experience_locator)
            current_page.wait_for_timeout(facade._act_wait_ms("ACT_POST_CLICK_WAIT_MS", 250))
            after_experience = facade._act_observe(current_page, experience_locator)
            if facade._act_option_selection_postcondition(
                before_experience, after_experience, trigger, experience_locator, option_name
            ):
                facade._act_wait_for_field_processing(
                    current_page, env_name="ACT_DROPDOWN_CHANGE_PROCESSING_WAIT_MS", default_ms=5000
                )
                experience_attempts = debug_trace.setdefault("experience_attempts", [])
                if isinstance(experience_attempts, list):
                    experience_attempts.append(
                        {
                            "strategy_name": strategy_name,
                            "status": "validated",
                            "episode_id": str(episode.get("episode_id") or "").strip(),
                            "retrieval_score": int(episode.get("retrieval_score") or 0),
                            "after": facade._act_debug_observation_summary(after_experience),
                        }
                    )
                debug_trace["resolved_by"] = "experience_reuse"
                debug_trace["status"] = "success"
                facade._act_set_debug_detail("select_search_trigger_option", debug_trace)
                facade._act_set_recovery_record(
                    "experience_reuse",
                    str((episode.get("recovery") or {}).get("kind") or "").strip()
                    or "experience_reuse",
                    "experience_reuse",
                    {
                        "trigger_label": title,
                        "option_name": option_name,
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
                    action_type="select_search_trigger_option",
                    label=option_target,
                    page=current_page,
                    locator=experience_locator,
                    error=last_error,
                    status="success",
                    postcondition_kind="option_selected",
                    postcondition_passed=True,
                )
                return
            last_error = RuntimeError(
                f'Experience strategy "{strategy_name}" did not apply search option "{option_name}".'
            )
            experience_attempts = debug_trace.setdefault("experience_attempts", [])
            if isinstance(experience_attempts, list):
                experience_attempts.append(
                    {
                        "strategy_name": strategy_name,
                        "status": "postcondition_failed",
                        "episode_id": str(episode.get("episode_id") or "").strip(),
                        "retrieval_score": int(episode.get("retrieval_score") or 0),
                        "error": facade._act_trim_debug_text(last_error, 320),
                    }
                )
        except Exception as exc:
            last_error = exc
            experience_attempts = debug_trace.setdefault("experience_attempts", [])
            if isinstance(experience_attempts, list):
                experience_attempts.append(
                    {
                        "strategy_name": strategy_name,
                        "status": "failed",
                        "episode_id": str(episode.get("episode_id") or "").strip(),
                        "retrieval_score": int(episode.get("retrieval_score") or 0),
                        "error": facade._act_trim_debug_text(exc, 320),
                    }
                )
    if not debug_trace.get("experience_attempts"):
        debug_trace["experience_attempts"] = [{"status": "no_candidates"}]

    def _execute_ai_search_option_locator(
        strategy_name: str, ai_locator: Locator, ai_strategy: dict[str, Any]
    ) -> bool:
        before_ai = facade._act_observe(current_page, ai_locator)
        facade._act_strict_click(ai_locator)
        current_page.wait_for_timeout(facade._act_wait_ms("ACT_POST_CLICK_WAIT_MS", 250))
        after_ai = facade._act_observe(current_page, ai_locator)
        if not facade._act_option_selection_postcondition(
            before_ai, after_ai, trigger, ai_locator, option_name
        ):
            return False
        facade._act_wait_for_field_processing(
            current_page, env_name="ACT_DROPDOWN_CHANGE_PROCESSING_WAIT_MS", default_ms=5000
        )
        return True

    ai_result, last_error = facade._act_execute_ai_repair_rounds(
        current_page=current_page,
        helper="select_search_trigger_option",
        label=option_target,
        last_error=last_error,
        locator=option,
        postcondition_kind="option_selected",
        failure_message=lambda strategy_name: f'AI strategy "{strategy_name}" did not apply search option "{option_name}".',
        execute_locator=_execute_ai_search_option_locator,
    )
    if ai_result is not None:
        strategy_name, ai_locator, ai_strategy = ai_result
        debug_trace["ai_repair"] = {
            "status": "validated",
            "strategy_name": strategy_name,
            "locator_strategy": facade._act_clone_json_value(ai_strategy),
        }
        debug_trace["resolved_by"] = "ai_locator_repair"
        debug_trace["status"] = "success"
        facade._act_set_debug_detail("select_search_trigger_option", debug_trace)
        facade._act_set_recovery_record(
            "ai_validated",
            "ai_locator_repair",
            "ai_locator_repair",
            {
                "trigger_label": title,
                "option_name": option_name,
                "strategy_name": strategy_name,
                "locator_strategy": facade._act_clone_json_value(ai_strategy),
            },
        )
        facade._act_store_experience_episode(
            action_type="select_search_trigger_option",
            label=option_target,
            page=current_page,
            locator=ai_locator,
            error=last_error,
            status="success",
            postcondition_kind="option_selected",
            postcondition_passed=True,
        )
        return
    debug_trace["ai_repair"] = {
        "status": "failed",
        "error": facade._act_trim_debug_text(last_error, 320),
    }
    debug_trace["status"] = "failed"
    debug_trace["final_error"] = facade._act_trim_debug_text(last_error, 320)
    facade._act_set_debug_detail("select_search_trigger_option", debug_trace)
    raise RuntimeError(
        f'Unable to apply search option "{option_name}" for "{title}".'
    ) from last_error
