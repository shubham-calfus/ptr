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
    "_act_enter_search_value",
    "_act_fill_locator_via_keyboard",
    "_act_fill_textbox",
    "_act_submit_textbox_enter",
    "_act_click_textbox",
]


def _act_enter_search_value(
    locator: Locator,
    value: str,
    timeout_ms: int | None = None,
    *,
    current_page: Page | None = None,
    label: str = "",
) -> None:
    timeout = facade._act_resolve_wait_override_ms(timeout_ms, "ACT_TEXT_ENTRY_TIMEOUT_MS", 3000)
    locator.wait_for(state="visible", timeout=timeout)
    try:
        locator.scroll_into_view_if_needed(timeout=min(timeout, 1000))
    except Exception:
        pass
    try:
        locator.click(timeout=timeout)
    except Exception as click_exc:
        page = current_page or facade._ACT_LAST_PAGE
        oracle_strategy_name = ""
        if page is not None:
            oracle_strategy_name = facade._act_try_open_oracle_select_single_with_keyboard(
                page, locator, click_exc
            )
        if not oracle_strategy_name:
            raise
        facade._act_set_recovery_record(
            "oracle_handler",
            "oracle_select_single_keyboard_open",
            "oracle_select_single_keyboard_open",
            {"trigger_label": label, "strategy_name": oracle_strategy_name},
        )
    try:
        locator.press("ControlOrMeta+A", timeout=timeout)
        locator.press("Backspace", timeout=timeout)
    except Exception:
        try:
            locator.fill("", timeout=timeout)
        except Exception:
            pass
    text = str(value or "")
    if not text:
        return
    key_delay = facade._act_wait_ms("ACT_SEARCH_KEY_DELAY_MS", 75)
    try:
        locator.press_sequentially(text, delay=key_delay, timeout=timeout)
    except Exception:
        try:
            locator.type(text, delay=key_delay, timeout=timeout)
        except Exception:
            locator.fill(text, timeout=timeout)


def _act_fill_locator_via_keyboard(locator: Locator, value: str) -> None:
    timeout = facade._act_wait_ms("ACT_TEXT_ENTRY_TIMEOUT_MS", 3000)
    locator.wait_for(state="visible", timeout=timeout)
    try:
        locator.scroll_into_view_if_needed(timeout=min(timeout, 1000))
    except Exception:
        pass
    try:
        locator.click(timeout=min(timeout, 1000))
    except Exception:
        pass
    try:
        locator.press("ControlOrMeta+A", timeout=timeout)
    except Exception:
        pass
    try:
        locator.press("Backspace", timeout=timeout)
    except Exception:
        pass
    entry_delay = max(0, min(200, facade._act_int_env("ACT_TEXT_ENTRY_KEY_DELAY_MS", 40)))
    try:
        locator.press_sequentially(str(value), delay=entry_delay, timeout=timeout)
        return
    except Exception:
        pass
    try:
        locator.type(str(value), delay=entry_delay, timeout=timeout)
        return
    except Exception:
        pass
    locator.fill(str(value), timeout=timeout)


def _act_fill_textbox(locator: Locator, current_page: Page, label: str, value: str) -> None:
    facade._act_register_page(current_page)
    before = facade._act_observe(current_page, locator)
    debug_trace = facade._act_update_debug_detail(
        "fill_textbox",
        {
            "label": label,
            "requested_value": facade._act_trim_debug_text(value, 120),
            "status": "strict_attempt",
            "experience_attempts": [],
            "before": facade._act_debug_observation_summary(before),
        },
    )
    try:
        facade._act_strict_fill(locator, value)
        facade._act_wait_for_field_processing(
            current_page, env_name="ACT_TEXTBOX_CHANGE_PROCESSING_WAIT_MS", default_ms=500
        )
        after = facade._act_observe(current_page, locator)
        observed = facade._act_locator_value(locator) or facade._act_locator_text(locator)
        if facade._act_value_matches(value, observed):
            debug_trace["direct_attempt"] = {
                "status": "validated",
                "observed_value": facade._act_trim_debug_text(observed, 160),
                "after": facade._act_debug_observation_summary(after),
            }
            debug_trace["resolved_by"] = "strict"
            debug_trace["status"] = "success"
            facade._act_set_debug_detail("fill_textbox", debug_trace)
            return
        raise RuntimeError(f'Textbox "{label}" did not reflect the requested value.')
    except Exception as direct_exc:
        after_failure = facade._act_observe(current_page, locator)
        observed_failure = facade._act_locator_value(locator) or facade._act_locator_text(locator)
        last_error: Exception = direct_exc
        debug_trace["direct_attempt"] = {
            "status": "failed",
            "error": facade._act_trim_debug_text(direct_exc, 320),
            "observed_value": facade._act_trim_debug_text(observed_failure, 160),
            "after": facade._act_debug_observation_summary(after_failure),
        }
        try:
            oracle_recovery = facade._act_try_oracle_table_active_editor_fill(
                current_page, locator, label, value
            )
            if oracle_recovery:
                debug_trace["oracle_table_active_editor_fill"] = {
                    "status": "validated",
                    "details": facade._act_clone_json_value(oracle_recovery),
                }
                debug_trace["resolved_by"] = "oracle_table_active_editor_fill"
                debug_trace["status"] = "success"
                facade._act_set_debug_detail("fill_textbox", debug_trace)
                facade._act_set_recovery_record(
                    "oracle_handler",
                    "oracle_table_active_editor_fill",
                    "oracle_table_active_editor_fill",
                    oracle_recovery,
                )
                facade._act_store_experience_episode(
                    action_type="fill_textbox",
                    label=label,
                    page=current_page,
                    locator=locator,
                    error=direct_exc,
                    status="success",
                    postcondition_kind="field_value_changed",
                    postcondition_passed=True,
                )
                return
        except Exception as exc:
            last_error = exc
            debug_trace["oracle_table_active_editor_fill"] = {
                "status": "failed",
                "error": facade._act_trim_debug_text(exc, 320),
            }
        if "oracle_table_active_editor_fill" not in debug_trace:
            debug_trace["oracle_table_active_editor_fill"] = {"status": "not_applied"}
        try:
            oracle_spinbutton_recovery = facade._act_try_oracle_spinbutton_fill(
                current_page, locator, label, value
            )
            if oracle_spinbutton_recovery:
                debug_trace["oracle_spinbutton_fill"] = {
                    "status": "validated",
                    "details": facade._act_clone_json_value(oracle_spinbutton_recovery),
                }
                debug_trace["resolved_by"] = "oracle_spinbutton_fill"
                debug_trace["status"] = "success"
                facade._act_set_debug_detail("fill_textbox", debug_trace)
                facade._act_set_recovery_record(
                    "oracle_handler",
                    "oracle_spinbutton_fill",
                    "oracle_spinbutton_fill",
                    oracle_spinbutton_recovery,
                )
                facade._act_store_experience_episode(
                    action_type="fill_textbox",
                    label=label,
                    page=current_page,
                    locator=locator,
                    error=direct_exc,
                    status="success",
                    postcondition_kind="field_value_changed",
                    postcondition_passed=True,
                )
                return
        except Exception as exc:
            last_error = exc
            debug_trace["oracle_spinbutton_fill"] = {
                "status": "failed",
                "error": facade._act_trim_debug_text(exc, 320),
            }
        if "oracle_spinbutton_fill" not in debug_trace:
            debug_trace["oracle_spinbutton_fill"] = {"status": "not_applied"}
        for strategy_name, experience_locator, episode in facade._act_experience_repair_locators(
            current_page, "fill_textbox", label, direct_exc, locator=locator
        ):
            try:
                facade._act_record_strategy_attempt(strategy_name)
                facade._act_strict_fill(experience_locator, value)
                facade._act_wait_for_field_processing(
                    current_page, env_name="ACT_TEXTBOX_CHANGE_PROCESSING_WAIT_MS", default_ms=500
                )
                observed = facade._act_locator_value(
                    experience_locator
                ) or facade._act_locator_text(experience_locator)
                if facade._act_value_matches(value, observed):
                    experience_attempts = debug_trace.setdefault("experience_attempts", [])
                    if isinstance(experience_attempts, list):
                        experience_attempts.append(
                            {
                                "strategy_name": strategy_name,
                                "status": "validated",
                                "episode_id": str(episode.get("episode_id") or "").strip(),
                                "retrieval_score": int(episode.get("retrieval_score") or 0),
                                "observed_value": facade._act_trim_debug_text(observed, 160),
                            }
                        )
                    debug_trace["resolved_by"] = "experience_reuse"
                    debug_trace["status"] = "success"
                    facade._act_set_debug_detail("fill_textbox", debug_trace)
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
                        action_type="fill_textbox",
                        label=label,
                        page=current_page,
                        locator=experience_locator,
                        error=direct_exc,
                        status="success",
                        postcondition_kind="field_value_changed",
                        postcondition_passed=True,
                    )
                    return
                last_error = RuntimeError(
                    f'Experience strategy "{strategy_name}" did not satisfy fill postcondition for "{label}".'
                )
                experience_attempts = debug_trace.setdefault("experience_attempts", [])
                if isinstance(experience_attempts, list):
                    experience_attempts.append(
                        {
                            "strategy_name": strategy_name,
                            "status": "postcondition_failed",
                            "episode_id": str(episode.get("episode_id") or "").strip(),
                            "retrieval_score": int(episode.get("retrieval_score") or 0),
                            "observed_value": facade._act_trim_debug_text(observed, 160),
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

        def _execute_ai_fill_locator(
            strategy_name: str, ai_locator: Locator, ai_strategy: dict[str, Any]
        ) -> bool:
            facade._act_strict_fill(ai_locator, value)
            facade._act_wait_for_field_processing(
                current_page, env_name="ACT_TEXTBOX_CHANGE_PROCESSING_WAIT_MS", default_ms=500
            )
            observed = facade._act_locator_value(ai_locator) or facade._act_locator_text(ai_locator)
            return facade._act_value_matches(value, observed)

        ai_result, last_error = facade._act_execute_ai_repair_rounds(
            current_page=current_page,
            helper="fill_textbox",
            label=label,
            last_error=last_error,
            value=value,
            locator=locator,
            postcondition_kind="field_value_changed",
            failure_message=lambda strategy_name: f'AI strategy "{strategy_name}" did not satisfy fill postcondition for "{label}".',
            execute_locator=_execute_ai_fill_locator,
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
            facade._act_set_debug_detail("fill_textbox", debug_trace)
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
                action_type="fill_textbox",
                label=label,
                page=current_page,
                locator=ai_locator,
                error=direct_exc,
                status="success",
                postcondition_kind="field_value_changed",
                postcondition_passed=True,
            )
            return
        debug_trace["ai_repair"] = {
            "status": "failed",
            "error": facade._act_trim_debug_text(last_error, 320),
        }
        debug_trace["status"] = "failed"
        debug_trace["final_error"] = facade._act_trim_debug_text(last_error, 320)
        facade._act_set_debug_detail("fill_textbox", debug_trace)
        raise RuntimeError(
            f'Unable to fill textbox "{label}" using strict execution and AI self-repair.'
        ) from last_error


def _act_submit_textbox_enter(locator: Locator, current_page: Page, label: str) -> None:
    facade._act_register_page(current_page)
    before = facade._act_observe(current_page, locator)
    locator.press("Enter")
    current_page.wait_for_timeout(facade._act_wait_ms("ACT_POST_ENTER_WAIT_MS", 400))
    after = facade._act_observe(current_page, locator)
    if facade._act_generic_click_postcondition(before, after):
        return


def _act_click_textbox(locator: Locator, current_page: Page, label: str) -> None:
    facade._act_register_page(current_page)
    before = facade._act_observe(current_page, locator)
    facade._act_strict_click(locator)
    after = facade._act_observe(current_page, locator)
    if facade._act_generic_click_postcondition(before, after):
        return
    raise RuntimeError(f'Textbox "{label}" was clicked but focus/state did not change.')
