"""Auto-split from helpers_v2.py. `facade` is the helpers_v2 facade: the single
shared namespace, so monkeypatching helpers_v2.X and shared _ACT_* state
behave exactly as in the original module. Call shared helpers via `facade.`."""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Locator, Page

try:
    from .. import helpers_v2 as facade
except ImportError:  # pragma: no cover
    from src.runtime import helpers_v2 as facade

__all__ = [
    "_act_combobox_open_postcondition",
    "_act_combobox_trigger_reflects_option",
    "_act_try_apply_combobox_option_candidate",
    "_act_click_combobox",
    "_act_select_combobox_option",
]


def _act_combobox_open_postcondition(before: dict[str, Any], after: dict[str, Any]) -> bool:
    if int(after.get("dialog_count") or 0) > int(before.get("dialog_count") or 0):
        return True
    before_meta = before.get("target_meta") if isinstance(before.get("target_meta"), dict) else {}
    after_meta = after.get("target_meta") if isinstance(after.get("target_meta"), dict) else {}
    before_expanded = facade._act_normalize_text(before_meta.get("aria_expanded"))
    after_expanded = facade._act_normalize_text(after_meta.get("aria_expanded"))
    if after_expanded == "true" and before_expanded != after_expanded:
        return True
    return facade._act_generic_click_postcondition(before, after)


def _act_combobox_trigger_reflects_option(trigger: Locator, option_name: str) -> bool:
    observed_candidates: list[str] = []

    def _record_observed(value: Any) -> None:
        normalized = str(value or "").strip()
        if normalized:
            observed_candidates.append(normalized)

    _record_observed(facade._act_locator_value(trigger))
    _record_observed(facade._act_locator_text(trigger))
    descendant_values = facade._act_safe_locator_eval(
        trigger,
        '(node) => {\n            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();\n            const values = [];\n            const seen = new Set();\n            const record = (value) => {\n                const normalized = normalize(value);\n                if (!normalized || seen.has(normalized)) return;\n                seen.add(normalized);\n                values.push(normalized);\n            };\n\n            if (!node) return values;\n\n            record("value" in node ? node.value : "");\n            record(node.innerText || node.textContent);\n\n            const descendants = node.querySelectorAll?.("input, textarea, select") || [];\n            for (const descendant of descendants) {\n                record("value" in descendant ? descendant.value : "");\n                record(descendant.getAttribute?.("value"));\n                record(descendant.innerText || descendant.textContent);\n                if (descendant.tagName?.toLowerCase() === "select" && descendant.selectedOptions?.length) {\n                    for (const selected of descendant.selectedOptions) {\n                        record(selected.innerText || selected.textContent);\n                    }\n                }\n            }\n            return values;\n        }',
    )
    if isinstance(descendant_values, list):
        for value in descendant_values:
            _record_observed(value)
    for observed in observed_candidates:
        if facade._act_value_matches(option_name, observed):
            return True
    metadata = facade._act_extract_locator_metadata(trigger)
    if not isinstance(metadata, dict):
        return False
    for key in ("text", "oracle_host_text", "title"):
        if facade._act_value_matches(option_name, str(metadata.get(key) or "")):
            return True
    return False


def _act_try_apply_combobox_option_candidate(
    trigger: Locator, option_locator: Locator, current_page: Page, label: str, option_name: str
) -> Exception | None:
    retry_count = max(0, facade._act_int_env("ACT_COMBOBOX_VALUE_RETRY_COUNT", 1))
    last_error: Exception | None = None
    for attempt in range(retry_count + 1):
        try:
            if attempt > 0:
                facade._act_click_combobox(trigger, current_page, label)
                current_page.wait_for_timeout(
                    facade._act_wait_ms("ACT_COMBOBOX_RETRY_WAIT_MS", 250)
                )
            before = facade._act_observe(current_page, option_locator)
            facade._act_strict_click(option_locator)
            current_page.wait_for_timeout(facade._act_wait_ms("ACT_COMBOBOX_SELECT_WAIT_MS", 400))
            after = facade._act_observe(current_page, option_locator)
            if not facade._act_option_selection_postcondition(
                before, after, trigger, option_locator, option_name
            ):
                last_error = RuntimeError(
                    f'Combobox "{label}" did not reflect option "{option_name}".'
                )
                continue
            facade._act_wait_for_field_processing(
                current_page, env_name="ACT_DROPDOWN_CHANGE_PROCESSING_WAIT_MS", default_ms=5000
            )
            if facade._act_combobox_trigger_reflects_option(trigger, option_name):
                return None
            last_error = RuntimeError(
                f'Combobox "{label}" did not reflect option "{option_name}" after selection.'
            )
        except Exception as exc:
            last_error = exc
    return last_error or RuntimeError(f'Combobox "{label}" did not reflect option "{option_name}".')


def _act_click_combobox(locator: Locator, current_page: Page, label: str) -> None:
    facade._act_register_page(current_page)
    before = facade._act_observe(current_page, locator)
    debug_trace = facade._act_update_debug_detail(
        "click_combobox",
        {
            "label": label,
            "status": "strict_attempt",
            "before": facade._act_debug_observation_summary(before),
            "experience_attempts": [],
        },
    )
    # Fast-fail a DISABLED combobox before the full strict-click timeout + Oracle/experience/AI
    # ladder. A dependent LOV (e.g. Supplier enabled only after its controlling field commits)
    # sits disabled, and no click/keyboard/AI can open a disabled control -- so once a bounded
    # enable-wait elapses we stop with a real reason instead of ~30s + minutes of recovery.
    enablement = facade._act_wait_for_select_target_enabled(
        locator, current_page, env_name="ACT_DEPENDENT_COMBOBOX_ENABLE_WAIT_MS", default_ms=8000
    )
    debug_trace["target_enablement"] = enablement
    if enablement == "disabled":
        disabled_reason = facade._act_disabled_target_reason(locator)
        debug_trace["status"] = "disabled_fast_fail"
        debug_trace["disabled_reason"] = facade._act_clone_json_value(disabled_reason)
        facade._act_set_debug_detail("click_combobox", debug_trace)
        source = str(disabled_reason.get("source") or "unknown").strip()
        raise RuntimeError(
            f'Combobox "{label}" is disabled (disabled via {source}) and cannot be opened -- a '
            "dependent field whose controlling field is not set/committed, or it is conditionally "
            "enabled. Failed fast without the long wait or AI repair."
        )
    facade._act_set_debug_detail("click_combobox", debug_trace)
    try:
        facade._act_strict_click(locator)
        current_page.wait_for_timeout(facade._act_wait_ms("ACT_COMBOBOX_OPEN_WAIT_MS", 350))
        after = facade._act_observe(current_page, locator)
        if facade._act_combobox_open_postcondition(before, after):
            debug_trace["direct_attempt"] = {
                "status": "validated",
                "after": facade._act_debug_observation_summary(after),
            }
            debug_trace["resolved_by"] = "strict"
            debug_trace["status"] = "success"
            facade._act_set_debug_detail("click_combobox", debug_trace)
            return
        raise RuntimeError(f'Combobox "{label}" did not open.')
    except Exception as direct_exc:
        last_error: Exception = direct_exc
        debug_trace["direct_attempt"] = {
            "status": "failed",
            "error": facade._act_trim_debug_text(direct_exc, 320),
        }
        oracle_strategy_name = facade._act_try_open_oracle_select_single_with_keyboard(
            current_page, locator, direct_exc
        )
        if oracle_strategy_name:
            debug_trace["oracle_select_single_keyboard_open"] = {
                "status": "validated",
                "strategy_name": oracle_strategy_name,
            }
            debug_trace["resolved_by"] = "oracle_select_single_keyboard_open"
            debug_trace["status"] = "success"
            facade._act_set_debug_detail("click_combobox", debug_trace)
            facade._act_set_recovery_record(
                "oracle_handler",
                "oracle_select_single_keyboard_open",
                "oracle_select_single_keyboard_open",
                {"trigger_label": label, "strategy_name": oracle_strategy_name},
            )
            facade._act_store_experience_episode(
                action_type="click_combobox",
                label=label,
                page=current_page,
                locator=locator,
                error=direct_exc,
                status="success",
                postcondition_kind="dialog_opened",
                postcondition_passed=True,
            )
            return
        debug_trace["oracle_select_single_keyboard_open"] = {"status": "not_applied"}
        # Oracle label-anchored open: the recorded role+name can miss when the same field
        # renders differently per context (e.g. Business Unit is a named textbox on an Invoice
        # but an unnamed combobox on a Credit memo). Resolve the control by its visible Oracle
        # field label -- role-agnostic, single-match only -- and open that. Deterministic Oracle
        # tier, so it runs before experience/AI; the real open postcondition still gates success.
        label_locator = facade._act_oracle_label_control_locator(current_page, label)
        if label_locator is not None:
            try:
                facade._act_record_strategy_attempt("oracle_label_anchored_open")
                before_label = facade._act_observe(current_page, label_locator)
                facade._act_strict_click(label_locator)
                current_page.wait_for_timeout(facade._act_wait_ms("ACT_COMBOBOX_OPEN_WAIT_MS", 350))
                after_label = facade._act_observe(current_page, label_locator)
                if facade._act_combobox_open_postcondition(before_label, after_label):
                    debug_trace["oracle_label_anchored_open"] = {
                        "status": "validated",
                        "after": facade._act_debug_observation_summary(after_label),
                    }
                    debug_trace["resolved_by"] = "oracle_label_anchored_open"
                    debug_trace["status"] = "success"
                    facade._act_set_debug_detail("click_combobox", debug_trace)
                    facade._act_set_recovery_record(
                        "oracle_handler",
                        "oracle_label_anchored_open",
                        "oracle_label_anchored_open",
                        {"trigger_label": label},
                    )
                    facade._act_store_experience_episode(
                        action_type="click_combobox",
                        label=label,
                        page=current_page,
                        locator=label_locator,
                        error=direct_exc,
                        status="success",
                        postcondition_kind="dialog_opened",
                        postcondition_passed=True,
                    )
                    return
                debug_trace["oracle_label_anchored_open"] = {"status": "postcondition_failed"}
            except Exception as label_exc:
                last_error = label_exc
                debug_trace["oracle_label_anchored_open"] = {
                    "status": "failed",
                    "error": facade._act_trim_debug_text(label_exc, 320),
                }
            facade._act_set_debug_detail("click_combobox", debug_trace)
        else:
            debug_trace["oracle_label_anchored_open"] = {"status": "no_label_match"}
            facade._act_set_debug_detail("click_combobox", debug_trace)
        for strategy_name, experience_locator, episode in facade._act_experience_repair_locators(
            current_page, "click_combobox", label, direct_exc, locator=locator
        ):
            try:
                facade._act_record_strategy_attempt(strategy_name)
                before_experience = facade._act_observe(current_page, experience_locator)
                facade._act_strict_click(experience_locator)
                current_page.wait_for_timeout(facade._act_wait_ms("ACT_COMBOBOX_OPEN_WAIT_MS", 350))
                after_experience = facade._act_observe(current_page, experience_locator)
                if facade._act_combobox_open_postcondition(before_experience, after_experience):
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
                    facade._act_set_debug_detail("click_combobox", debug_trace)
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
                        action_type="click_combobox",
                        label=label,
                        page=current_page,
                        locator=experience_locator,
                        error=direct_exc,
                        status="success",
                        postcondition_kind="dialog_opened",
                        postcondition_passed=True,
                    )
                    return
                last_error = RuntimeError(
                    f'Experience strategy "{strategy_name}" did not open combobox "{label}".'
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

        def _execute_ai_combobox_locator(
            strategy_name: str, ai_locator: Locator, ai_strategy: dict[str, Any]
        ) -> bool:
            before_ai = facade._act_observe(current_page, ai_locator)
            facade._act_strict_click(ai_locator)
            current_page.wait_for_timeout(facade._act_wait_ms("ACT_COMBOBOX_OPEN_WAIT_MS", 350))
            after_ai = facade._act_observe(current_page, ai_locator)
            return facade._act_combobox_open_postcondition(before_ai, after_ai)

        ai_result, last_error = facade._act_execute_ai_repair_rounds(
            current_page=current_page,
            helper="click_combobox",
            label=label,
            last_error=last_error,
            locator=locator,
            postcondition_kind="dialog_opened",
            failure_message=lambda strategy_name: f'AI strategy "{strategy_name}" did not open combobox "{label}".',
            execute_locator=_execute_ai_combobox_locator,
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
            facade._act_set_debug_detail("click_combobox", debug_trace)
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
                action_type="click_combobox",
                label=label,
                page=current_page,
                locator=ai_locator,
                error=direct_exc,
                status="success",
                postcondition_kind="dialog_opened",
                postcondition_passed=True,
            )
            return
        debug_trace["ai_repair"] = {
            "status": "failed",
            "error": facade._act_trim_debug_text(last_error, 320),
        }
        debug_trace["status"] = "failed"
        debug_trace["final_error"] = facade._act_trim_debug_text(last_error, 320)
        facade._act_set_debug_detail("click_combobox", debug_trace)
        raise RuntimeError(f'Unable to open combobox "{label}".') from last_error


def _act_select_combobox_option(
    trigger: Locator, option: Locator, current_page: Page, label: str, option_name: str
) -> None:
    facade._act_register_page(current_page)
    debug_trace = facade._act_update_debug_detail(
        "select_combobox_option",
        {
            "label": label,
            "option_name": option_name,
            "status": "open_trigger",
            "option_attempts": [],
            "experience_attempts": [],
        },
    )
    # Resolve the effective trigger once. If the recorded locator can't be made visible (e.g.
    # Business Unit renders as an UNNAMED combobox under a different Transaction Class, so
    # get_by_role("textbox", name="Business Unit") binds to a hidden node), fall back to the
    # field's visible Oracle label -- role-agnostic, single-match only. Reassigning `trigger`
    # makes BOTH the open and the value-equality postcondition use a locator that resolves.
    if not facade._act_locator_is_actionable(trigger):
        label_locator = facade._act_oracle_label_control_locator(current_page, label)
        if label_locator is not None and facade._act_locator_is_actionable(label_locator):
            trigger = label_locator
            debug_trace["effective_trigger"] = "oracle_label_anchored"
            facade._act_set_debug_detail("select_combobox_option", debug_trace)
    # Disabled dependent combobox: skip-pass if it already shows the requested option (auto-derived
    # -- no need to drop the step); otherwise fail fast (a disabled control can't be set, and AI
    # can't enable it) instead of the long open ladder.
    combo_enablement = facade._act_wait_for_select_target_enabled(
        trigger, current_page, env_name="ACT_DEPENDENT_COMBOBOX_ENABLE_WAIT_MS", default_ms=8000
    )
    debug_trace["target_enablement"] = combo_enablement
    if combo_enablement == "disabled":
        if facade._act_combobox_trigger_reflects_option(trigger, option_name):
            debug_trace["status"] = "disabled_value_already_satisfied"
            facade._act_set_debug_detail("select_combobox_option", debug_trace)
            facade._act_set_recovery_record(
                "oracle_handler",
                "disabled_target_value_already_set",
                "disabled_target_value_already_set",
                {"option_name": option_name},
            )
            facade._act_store_experience_episode(
                action_type="select_combobox_option",
                label=label,
                page=current_page,
                locator=trigger,
                error=None,
                status="success",
                postcondition_kind="option_selected",
                postcondition_passed=True,
            )
            return
        debug_trace["status"] = "disabled_fast_fail"
        facade._act_set_debug_detail("select_combobox_option", debug_trace)
        raise RuntimeError(
            f'Combobox "{label}" is disabled and its value does not match "{option_name}". A '
            "disabled control cannot be set: set its controlling field so this one enables, or "
            "the requested value is not valid for the current dependency. Failed fast without AI."
        )
    facade._act_click_combobox(trigger, current_page, label)
    last_error: Exception | None = None
    option_target = str(option_name or "").strip()
    option_candidates = [
        ("raw_option", option),
        ("role_option", current_page.get_by_role("option", name=option_name)),
        ("role_cell", current_page.get_by_role("cell", name=option_name)),
        ("role_gridcell", current_page.get_by_role("gridcell", name=option_name)),
        ("text_option", current_page.get_by_text(option_name, exact=True)),
    ]
    for strategy_name, candidate in option_candidates:
        try:
            facade._act_record_strategy_attempt(strategy_name)
            resolved = candidate.first if hasattr(candidate, "first") else candidate
            candidate_error = facade._act_try_apply_combobox_option_candidate(
                trigger, resolved, current_page, label, option_name
            )
            if candidate_error is None:
                option_attempts = debug_trace.setdefault("option_attempts", [])
                if isinstance(option_attempts, list):
                    option_attempts.append({"strategy_name": strategy_name, "status": "validated"})
                debug_trace["resolved_by"] = strategy_name
                debug_trace["status"] = "success"
                facade._act_set_debug_detail("select_combobox_option", debug_trace)
                return
            last_error = candidate_error
            option_attempts = debug_trace.setdefault("option_attempts", [])
            if isinstance(option_attempts, list):
                option_attempts.append(
                    {
                        "strategy_name": strategy_name,
                        "status": "postcondition_failed",
                        "error": facade._act_trim_debug_text(candidate_error, 320),
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
        last_error = RuntimeError(f'Combobox "{label}" did not reflect option "{option_name}".')
    for strategy_name, experience_locator, episode in facade._act_experience_repair_locators(
        current_page, "select_combobox_option", option_target, last_error, locator=option
    ):
        try:
            facade._act_record_strategy_attempt(strategy_name)
            candidate_error = facade._act_try_apply_combobox_option_candidate(
                trigger, experience_locator, current_page, label, option_name
            )
            if candidate_error is None:
                experience_attempts = debug_trace.setdefault("experience_attempts", [])
                if isinstance(experience_attempts, list):
                    experience_attempts.append(
                        {
                            "strategy_name": strategy_name,
                            "status": "validated",
                            "episode_id": str(episode.get("episode_id") or "").strip(),
                            "retrieval_score": int(episode.get("retrieval_score") or 0),
                        }
                    )
                debug_trace["resolved_by"] = "experience_reuse"
                debug_trace["status"] = "success"
                facade._act_set_debug_detail("select_combobox_option", debug_trace)
                facade._act_set_recovery_record(
                    "experience_reuse",
                    str((episode.get("recovery") or {}).get("kind") or "").strip()
                    or "experience_reuse",
                    "experience_reuse",
                    {
                        "trigger_label": label,
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
                    action_type="select_combobox_option",
                    label=option_target,
                    page=current_page,
                    locator=experience_locator,
                    error=last_error,
                    status="success",
                    postcondition_kind="option_selected",
                    postcondition_passed=True,
                )
                return
            last_error = candidate_error
            experience_attempts = debug_trace.setdefault("experience_attempts", [])
            if isinstance(experience_attempts, list):
                experience_attempts.append(
                    {
                        "strategy_name": strategy_name,
                        "status": "postcondition_failed",
                        "episode_id": str(episode.get("episode_id") or "").strip(),
                        "retrieval_score": int(episode.get("retrieval_score") or 0),
                        "error": facade._act_trim_debug_text(candidate_error, 320),
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
    ai_result, last_error = facade._act_execute_ai_repair_rounds(
        current_page=current_page,
        helper="select_combobox_option",
        label=option_target,
        last_error=last_error,
        locator=option,
        postcondition_kind="option_selected",
        failure_message=lambda strategy_name: f'AI strategy "{strategy_name}" did not apply combobox option "{option_name}".',
        execute_locator=lambda strategy_name,
        ai_locator,
        ai_strategy: facade._act_try_apply_combobox_option_candidate(
            trigger, ai_locator, current_page, label, option_name
        )
        is None,
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
        facade._act_set_debug_detail("select_combobox_option", debug_trace)
        facade._act_set_recovery_record(
            "ai_validated",
            "ai_locator_repair",
            "ai_locator_repair",
            {
                "trigger_label": label,
                "option_name": option_name,
                "strategy_name": strategy_name,
                "locator_strategy": facade._act_clone_json_value(ai_strategy),
            },
        )
        facade._act_store_experience_episode(
            action_type="select_combobox_option",
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
    facade._act_set_debug_detail("select_combobox_option", debug_trace)
    raise RuntimeError(
        f'Unable to select combobox option "{option_name}" for "{label}".'
    ) from last_error
