"""Auto-split from helpers_v2.py. `facade` is the helpers_v2 facade: the single
shared namespace, so monkeypatching helpers_v2.X and shared _ACT_* state
behave exactly as in the original module. Call shared helpers via `facade.`."""

from __future__ import annotations

import re
import time
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Locator, Page

try:
    from .. import helpers_v2 as facade
except ImportError:  # pragma: no cover
    from src.runtime import helpers_v2 as facade

__all__ = [
    "_act_patch_page_methods",
    "_act_replace_primary_locator_arg",
    "_act_universal_ai_postcondition_kind",
    "_act_try_universal_ai_action_repair",
    "_act_tracked_action",
    "_act_tracked_raw_action",
    "_act_click_with_candidates",
]


class _ActButtonCommitError(RuntimeError):
    """A button actuated on its target but its dialog/drawer never committed.

    The strict click landed on the intended control, so the recovery cascade (which exists to
    find a *different* locator) is the wrong remedy. ``_act_click_with_candidates`` re-raises this
    immediately so the step fails fast with the real reason instead of grinding Oracle handlers,
    experience reuse, and AI self-repair on a control that already clicked.
    """


def _act_patch_page_methods() -> None:
    if getattr(Page, "_act_v2_patched", False):
        return
    original_goto = Page.goto
    original_reload = Page.reload
    original_page_close = Page.close
    original_context_close = BrowserContext.close
    original_browser_close = Browser.close

    def _wrapped_goto(self, *args, **kwargs):
        facade._act_register_page(self)
        try:
            result = original_goto(self, *args, **kwargs)
            if facade._ACT_SUPPRESS_PATCH_CAPTURE <= 0:
                facade._act_capture_step("goto")
            return result
        except Exception:
            if facade._ACT_SUPPRESS_PATCH_CAPTURE <= 0:
                facade._act_capture_failure_screenshot()
            raise

    def _wrapped_reload(self, *args, **kwargs):
        facade._act_register_page(self)
        try:
            result = original_reload(self, *args, **kwargs)
            if facade._ACT_SUPPRESS_PATCH_CAPTURE <= 0:
                facade._act_capture_step("reload")
            return result
        except Exception:
            if facade._ACT_SUPPRESS_PATCH_CAPTURE <= 0:
                facade._act_capture_failure_screenshot()
            raise

    def _wrapped_page_close(self, *args, **kwargs):
        facade._act_register_page(self)
        facade._act_capture_live_snapshot_before_close(self)
        return original_page_close(self, *args, **kwargs)

    def _wrapped_context_close(self, *args, **kwargs):
        for page in facade._act_order_pages_for_snapshot(facade._act_context_pages(self)):
            facade._act_capture_live_snapshot_before_close(page)
        return original_context_close(self, *args, **kwargs)

    def _wrapped_browser_close(self, *args, **kwargs):
        pages: list[Page] = []
        for context in facade._act_browser_contexts(self):
            pages.extend(facade._act_context_pages(context))
        for page in facade._act_order_pages_for_snapshot(pages):
            facade._act_capture_live_snapshot_before_close(page)
        steel_session_id = facade._ACT_STEEL_BROWSER_SESSION_IDS.pop(id(self), "")
        try:
            return original_browser_close(self, *args, **kwargs)
        finally:
            facade._act_release_steel_session(steel_session_id)

    Page.goto = _wrapped_goto
    Page.reload = _wrapped_reload
    Page.close = _wrapped_page_close
    BrowserContext.close = _wrapped_context_close
    Browser.close = _wrapped_browser_close
    setattr(Page, "_act_v2_patched", True)


def _act_replace_primary_locator_arg(
    args: tuple[Any, ...], primary_locator: Locator | None, replacement_locator: Locator
) -> tuple[Any, ...]:
    if primary_locator is None:
        return tuple(args)
    replaced_args = list(args)
    for index, arg in enumerate(replaced_args):
        if arg is primary_locator:
            replaced_args[index] = replacement_locator
            break
    return tuple(replaced_args)


def _act_universal_ai_postcondition_kind(helper_name: str) -> str:
    normalized_helper = facade._act_normalize_text(helper_name)
    if normalized_helper in {"check_target", "uncheck_target"}:
        return "checkbox_state_changed"
    if normalized_helper == "click_table_row":
        return "row_selected"
    if normalized_helper == "click_table_field":
        return "field_focused"
    if normalized_helper == "click_navigation_button":
        return "guided_flow_advanced"
    if normalized_helper == "submit_textbox_enter":
        return "enter_submitted"
    return "action_effect"


def _act_try_universal_ai_action_repair(
    *,
    action_type: str,
    label: str,
    fn,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    current_page: Page | None,
    primary_locator: Locator | None,
    last_error: Exception,
) -> tuple[bool, Any, Exception]:
    if current_page is None or primary_locator is None:
        return (False, None, last_error)
    if facade._ACT_CURRENT_STRATEGY.get("ai_interactions"):
        return (False, None, last_error)
    helper_name = facade._act_normalize_runtime_action_name(getattr(fn, "__name__", action_type))
    eligible_helpers = {
        "check_target",
        "uncheck_target",
        "click_textbox",
        "click_table_field",
        "click_table_row",
        "dblclick_text_target",
        "click_navigation_button",
        "submit_textbox_enter",
    }
    if helper_name not in eligible_helpers:
        return (False, None, last_error)
    debug_trace = facade._act_update_debug_detail(
        "universal_ai_repair",
        {
            "helper": helper_name,
            "label": label,
            "status": "requested",
            "trigger_error": facade._act_trim_debug_text(last_error, 320),
        },
    )
    postcondition_kind = facade._act_universal_ai_postcondition_kind(helper_name)
    execution_result: Any = None

    def _execute_ai_action_locator(
        strategy_name: str, ai_locator: Locator, ai_strategy: dict[str, Any]
    ) -> bool:
        nonlocal execution_result
        retry_args = facade._act_replace_primary_locator_arg(args, primary_locator, ai_locator)
        execution_result = fn(*retry_args, **kwargs)
        return True

    ai_result, latest_error = facade._act_execute_ai_repair_rounds(
        current_page=current_page,
        helper=helper_name,
        label=label,
        last_error=last_error,
        locator=primary_locator,
        postcondition_kind=postcondition_kind,
        failure_message=lambda strategy_name: f'AI strategy "{strategy_name}" did not repair "{label}" for helper "{helper_name}".',
        execute_locator=_execute_ai_action_locator,
    )
    if ai_result is None:
        debug_trace["status"] = "failed"
        debug_trace["error"] = facade._act_trim_debug_text(latest_error, 320)
        facade._act_set_debug_detail("universal_ai_repair", debug_trace)
        return (False, None, latest_error)
    strategy_name, ai_locator, ai_strategy = ai_result
    debug_trace["status"] = "validated"
    debug_trace["strategy_name"] = strategy_name
    debug_trace["locator_strategy"] = facade._act_clone_json_value(ai_strategy)
    facade._act_set_debug_detail("universal_ai_repair", debug_trace)
    facade._act_set_recovery_record(
        "ai_validated",
        "ai_locator_repair",
        "ai_locator_repair",
        {
            "helper": helper_name,
            "strategy_name": strategy_name,
            "locator_strategy": facade._act_clone_json_value(ai_strategy),
        },
    )
    facade._act_store_experience_episode(
        action_type=helper_name,
        label=label,
        page=current_page,
        locator=ai_locator,
        error=last_error,
        status="success",
        postcondition_kind=postcondition_kind,
        postcondition_passed=True,
    )
    return (True, execution_result, latest_error)


def _act_tracked_action(action_type: str, label: str, fn, *args, **kwargs):
    page = facade._act_resolve_page(args)
    primary_locator = facade._act_resolve_primary_locator(args)
    if page is not None:
        facade._act_register_page(page)
    facade._act_reset_strategy_tracking(action_type, label)
    script_data = facade._act_current_script_data()
    multi_line_context = (
        script_data.get("multi_line_context") if isinstance(script_data, dict) else None
    )
    if isinstance(multi_line_context, dict) and multi_line_context:
        facade._act_set_debug_detail("multi_line_context", multi_line_context)
    facade._act_record_strategy_attempt("direct")
    start = time.time()
    try:
        result = fn(*args, **kwargs)
        current_page = facade._ACT_LAST_PAGE or page
        facade._act_wait_after_interaction(current_page)
        facade._act_capture_step(action_type)
        facade._act_finalize_action_log(
            action_type, label, "success", int((time.time() - start) * 1000), page=current_page
        )
        return result
    except Exception as exc:
        current_page = facade._ACT_LAST_PAGE or page
        repaired, repaired_result, final_error = facade._act_try_universal_ai_action_repair(
            action_type=action_type,
            label=label,
            fn=fn,
            args=args,
            kwargs=kwargs,
            current_page=current_page,
            primary_locator=primary_locator,
            last_error=exc,
        )
        if repaired:
            current_page = facade._ACT_LAST_PAGE or current_page
            facade._act_wait_after_interaction(current_page)
            facade._act_capture_step(action_type)
            facade._act_finalize_action_log(
                action_type, label, "success", int((time.time() - start) * 1000), page=current_page
            )
            return repaired_result
        facade._act_capture_step(action_type)
        facade._act_capture_failure_screenshot()
        facade._act_store_experience_episode(
            action_type=facade._act_normalize_runtime_action_name(
                getattr(fn, "__name__", action_type)
            ),
            label=label,
            page=current_page,
            locator=primary_locator,
            error=final_error,
            status="failed",
            postcondition_kind="none",
            postcondition_passed=False,
        )
        facade._act_finalize_action_log(
            action_type,
            label,
            "failed",
            int((time.time() - start) * 1000),
            error=final_error,
            page=current_page,
        )
        if final_error is not exc:
            raise final_error from exc
        raise


def _act_tracked_raw_action(
    action_type: str,
    label: str,
    raw_source: str,
    global_scope: dict[str, Any],
    local_scope: dict[str, Any],
    *,
    page: Page | None = None,
    locator: Locator | None = None,
):
    current_page = page or facade._ACT_LAST_PAGE
    if current_page is not None:
        facade._act_register_page(current_page)
    facade._act_reset_strategy_tracking(action_type, label)
    script_data = facade._act_current_script_data()
    multi_line_context = (
        script_data.get("multi_line_context") if isinstance(script_data, dict) else None
    )
    if isinstance(multi_line_context, dict) and multi_line_context:
        facade._act_set_debug_detail("multi_line_context", multi_line_context)
    facade._act_record_strategy_attempt("raw_inline")
    start = time.time()
    exec_globals = dict(global_scope or {})
    exec_globals.setdefault("re", re)
    try:
        exec(str(raw_source or ""), exec_globals, local_scope)
        current_page = facade._ACT_LAST_PAGE or current_page
        facade._act_wait_after_interaction(current_page)
        facade._act_capture_step(action_type)
        facade._act_finalize_action_log(
            action_type, label, "success", int((time.time() - start) * 1000), page=current_page
        )
    except Exception as exc:
        current_page = facade._ACT_LAST_PAGE or current_page
        facade._act_capture_step(action_type)
        facade._act_capture_failure_screenshot()
        facade._act_store_experience_episode(
            action_type=facade._act_normalize_runtime_action_name(action_type),
            label=label,
            page=current_page,
            locator=locator,
            error=exc,
            status="failed",
            postcondition_kind="none",
            postcondition_passed=False,
        )
        facade._act_finalize_action_log(
            action_type,
            label,
            "failed",
            int((time.time() - start) * 1000),
            error=exc,
            page=current_page,
        )
        raise


def _act_click_with_candidates(
    page: Page, label: str, locator: Locator, helper: str, postcondition
):
    requested_label = str(label or "").strip()
    resolved_label = facade._act_resolve(requested_label)
    if resolved_label is None:
        resolved_label = requested_label
    label = str(resolved_label).strip() or requested_label
    before = facade._act_observe(page, locator)
    debug_payload = {
        "helper": helper,
        "label": label,
        "status": "strict_attempt",
        "before": facade._act_debug_observation_summary(before),
        "experience_attempts": [],
    }
    if requested_label and requested_label != label:
        debug_payload["requested_label"] = requested_label
    debug_trace = facade._act_update_debug_detail("click_with_candidates", debug_payload)
    try:
        facade._act_strict_click(locator)
        after = facade._act_observe(page, locator)
        after = facade._act_settle_click_postcondition(
            page, locator, postcondition, before, after
        )
        if postcondition(before, after):
            debug_trace["direct_attempt"] = {
                "status": "validated",
                "after": facade._act_debug_observation_summary(after),
            }
            debug_trace["resolved_by"] = "strict"
            debug_trace["status"] = "success"
            facade._act_set_debug_detail("click_with_candidates", debug_trace)
            return
        no_commit = facade._act_button_no_commit_failure(helper, page, label, before, after)
        if no_commit is not None:
            debug_trace["direct_attempt"] = {
                "status": "no_commit",
                "after": facade._act_debug_observation_summary(after),
                "validation_messages": no_commit.get("validation_messages") or [],
            }
            debug_trace["status"] = "failed"
            debug_trace["final_error"] = facade._act_trim_debug_text(no_commit["reason"], 320)
            facade._act_set_debug_detail("click_with_candidates", debug_trace)
            raise _ActButtonCommitError(no_commit["reason"])
        raise RuntimeError(f'Action "{label}" completed but no postcondition changed.')
    except _ActButtonCommitError:
        raise
    except Exception as direct_exc:
        last_error: Exception = direct_exc
        debug_trace["direct_attempt"] = {
            "status": "failed",
            "error": facade._act_trim_debug_text(direct_exc, 320),
        }
        ppr_after = facade._act_retry_strict_click_after_oracle_ppr(
            page, label, locator, postcondition, before
        )
        if ppr_after is not None:
            debug_trace["oracle_ppr_settle_retry"] = {
                "status": "validated",
                "after": facade._act_debug_observation_summary(ppr_after),
            }
            debug_trace["resolved_by"] = "oracle_ppr_settle_retry"
            debug_trace["status"] = "success"
            facade._act_set_debug_detail("click_with_candidates", debug_trace)
            facade._act_set_recovery_record(
                "minimal_wait",
                "oracle_ppr_settle_retry",
                "oracle_ppr_settle_retry",
                {"label": label},
            )
            facade._act_store_experience_episode(
                action_type=helper,
                label=label,
                page=page,
                locator=locator,
                error=last_error,
                status="success",
                postcondition_kind="action_effect",
                postcondition_passed=True,
            )
            return
        debug_trace["oracle_ppr_settle_retry"] = {"status": "not_applied"}
        quick_actions_expanded = False
        if facade._act_try_expand_oracle_quick_actions(page, label):
            quick_actions_expanded = True
            debug_trace["oracle_quick_actions_expand"] = {"attempted": True, "status": "retrying"}
            try:
                facade._act_strict_click(locator)
                after = facade._act_observe(page, locator)
                if postcondition(before, after):
                    debug_trace["oracle_quick_actions_expand"] = {
                        "attempted": True,
                        "status": "validated",
                        "after": facade._act_debug_observation_summary(after),
                    }
                    debug_trace["resolved_by"] = "oracle_quick_actions_expand"
                    debug_trace["status"] = "success"
                    facade._act_set_debug_detail("click_with_candidates", debug_trace)
                    facade._act_set_recovery_record(
                        "oracle_handler",
                        "quick_action_expand",
                        "oracle_quick_actions_expand",
                        {"trigger": "Show more quick actions"},
                    )
                    facade._act_store_experience_episode(
                        action_type=helper,
                        label=label,
                        page=page,
                        locator=locator,
                        error=direct_exc,
                        status="success",
                        postcondition_kind="action_effect",
                        postcondition_passed=True,
                    )
                    return
                last_error = RuntimeError(
                    f'Action "{label}" still had no postcondition after expanding quick actions.'
                )
                debug_trace["oracle_quick_actions_expand"] = {
                    "attempted": True,
                    "status": "postcondition_failed",
                    "error": facade._act_trim_debug_text(last_error, 320),
                }
            except Exception as exc:
                last_error = exc
                debug_trace["oracle_quick_actions_expand"] = {
                    "attempted": True,
                    "status": "failed",
                    "error": facade._act_trim_debug_text(exc, 320),
                }
        quick_action_strategy = facade._act_try_oracle_quick_action_exact_match(
            page, label, last_error, postcondition, allow_after_expand=quick_actions_expanded
        )
        if quick_action_strategy:
            debug_trace["oracle_quick_action_exact_match"] = {
                "status": "validated",
                "strategy_name": quick_action_strategy,
            }
            debug_trace["resolved_by"] = "oracle_quick_action_exact_match"
            debug_trace["status"] = "success"
            facade._act_set_debug_detail("click_with_candidates", debug_trace)
            facade._act_set_recovery_record(
                "oracle_handler",
                "quick_action_exact_match",
                "oracle_quick_action_exact_match",
                {"label": label, "strategy_name": quick_action_strategy},
            )
            facade._act_store_experience_episode(
                action_type=helper,
                label=label,
                page=page,
                locator=locator,
                error=last_error,
                status="success",
                postcondition_kind="action_effect",
                postcondition_passed=True,
            )
            return
        debug_trace["oracle_quick_action_exact_match"] = {
            "status": "not_applied",
            "after_expand": quick_actions_expanded,
        }
        notification_badge_strategy = facade._act_try_oracle_notification_badge(
            page, label, postcondition
        )
        if notification_badge_strategy:
            debug_trace["oracle_notification_badge"] = {
                "status": "validated",
                "strategy_name": notification_badge_strategy,
            }
            debug_trace["resolved_by"] = "oracle_notification_badge"
            debug_trace["status"] = "success"
            facade._act_set_debug_detail("click_with_candidates", debug_trace)
            facade._act_set_recovery_record(
                "oracle_handler",
                "notification_badge",
                "oracle_notification_badge",
                {"label": label, "strategy_name": notification_badge_strategy},
            )
            facade._act_store_experience_episode(
                action_type=helper,
                label=label,
                page=page,
                locator=locator,
                error=last_error,
                status="success",
                postcondition_kind="action_effect",
                postcondition_passed=True,
            )
            return
        debug_trace["oracle_notification_badge"] = {"status": "not_applied"}
        if helper in {"click_button_target", "click_numeric_button_target"}:
            recorded_button_strategy = facade._act_try_oracle_recorded_button_context(
                page, locator, label, last_error, postcondition
            )
            if recorded_button_strategy:
                debug_trace["oracle_recorded_button_context"] = {
                    "status": "validated",
                    "strategy_name": recorded_button_strategy,
                }
                debug_trace["resolved_by"] = "oracle_recorded_button_context"
                debug_trace["status"] = "success"
                facade._act_set_debug_detail("click_with_candidates", debug_trace)
                facade._act_set_recovery_record(
                    "oracle_handler",
                    "recorded_button_context",
                    "oracle_recorded_button_context",
                    {"label": label, "strategy_name": recorded_button_strategy},
                )
                facade._act_store_experience_episode(
                    action_type=helper,
                    label=label,
                    page=page,
                    locator=locator,
                    error=last_error,
                    status="success",
                    postcondition_kind="action_effect",
                    postcondition_passed=True,
                )
                return
            debug_trace["oracle_recorded_button_context"] = {"status": "not_applied"}
        if facade._act_try_oracle_home_search(page, label, postcondition):
            debug_trace["oracle_home_search"] = {"status": "validated", "search_label": label}
            debug_trace["resolved_by"] = "oracle_home_search"
            debug_trace["status"] = "success"
            facade._act_set_debug_detail("click_with_candidates", debug_trace)
            facade._act_set_recovery_record(
                "oracle_handler", "home_search", "oracle_home_search", {"search_label": label}
            )
            facade._act_store_experience_episode(
                action_type=helper,
                label=label,
                page=page,
                locator=locator,
                error=last_error,
                status="success",
                postcondition_kind="action_effect",
                postcondition_passed=True,
            )
            return
        debug_trace["oracle_home_search"] = {"status": "not_applied"}
        if helper in {
            "click_button_target",
            "click_numeric_button_target",
        } and facade._act_try_oracle_guided_action_card(page, label, postcondition):
            debug_trace["oracle_guided_action_card"] = {"status": "validated", "label": label}
            debug_trace["resolved_by"] = "oracle_guided_action_card"
            debug_trace["status"] = "success"
            facade._act_set_debug_detail("click_with_candidates", debug_trace)
            facade._act_set_recovery_record(
                "oracle_handler",
                "guided_action_card",
                "oracle_guided_action_card",
                {"label": label},
            )
            facade._act_store_experience_episode(
                action_type=helper,
                label=label,
                page=page,
                locator=locator,
                error=last_error,
                status="success",
                postcondition_kind="action_card_selected",
                postcondition_passed=True,
            )
            return
            debug_trace["oracle_guided_action_card"] = {"status": "not_applied"}
        if helper in {"click_button_target", "click_numeric_button_target"}:
            warning_dialog_recovery = facade._act_try_dismiss_oracle_warning_dialog(
                page, locator, label, last_error, observation=before
            )
            if warning_dialog_recovery:
                debug_trace["oracle_warning_dialog_dismiss"] = {
                    "status": "validated",
                    "details": facade._act_clone_json_value(warning_dialog_recovery),
                }
                debug_trace["resolved_by"] = "oracle_warning_dialog_dismiss"
                debug_trace["status"] = "success"
                facade._act_set_debug_detail("click_with_candidates", debug_trace)
                facade._act_set_recovery_record(
                    "oracle_handler",
                    "warning_dialog_dismiss",
                    "oracle_warning_dialog_dismiss",
                    warning_dialog_recovery,
                )
                facade._act_store_experience_episode(
                    action_type=helper,
                    label=label,
                    page=page,
                    locator=locator,
                    error=last_error,
                    status="success",
                    postcondition_kind="warning_dialog_dismissed",
                    postcondition_passed=True,
                )
                return
            debug_trace["oracle_warning_dialog_dismiss"] = {"status": "not_applied"}
        if helper == "click_button_target":
            optional_warning_skip = facade._act_try_skip_optional_oracle_warning_ok(
                page, locator, label, last_error
            )
            if optional_warning_skip:
                debug_trace["oracle_optional_warning_ok_absent"] = {
                    "status": "validated",
                    "details": facade._act_clone_json_value(optional_warning_skip),
                }
                debug_trace["resolved_by"] = "oracle_optional_warning_ok_absent"
                debug_trace["status"] = "success"
                facade._act_set_debug_detail("click_with_candidates", debug_trace)
                facade._act_set_recovery_record(
                    "oracle_handler",
                    "optional_warning_ok_absent",
                    "oracle_optional_warning_ok_absent",
                    optional_warning_skip,
                )
                facade._act_store_experience_episode(
                    action_type=helper,
                    label=label,
                    page=page,
                    locator=locator,
                    error=last_error,
                    status="success",
                    postcondition_kind="dialog_absent",
                    postcondition_passed=True,
                )
                return
            debug_trace["oracle_optional_warning_ok_absent"] = {"status": "not_applied"}
        for strategy_name, experience_locator, episode in facade._act_experience_repair_locators(
            page, helper, label, last_error, locator=locator
        ):
            try:
                facade._act_record_strategy_attempt(strategy_name)
                before_experience = facade._act_observe(page, experience_locator)
                facade._act_strict_click(experience_locator)
                after_experience = facade._act_observe(page, experience_locator)
                if postcondition(before_experience, after_experience):
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
                    facade._act_set_debug_detail("click_with_candidates", debug_trace)
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
                        action_type=helper,
                        label=label,
                        page=page,
                        locator=experience_locator,
                        error=last_error,
                        status="success",
                        postcondition_kind="action_effect",
                        postcondition_passed=True,
                    )
                    return
                last_error = RuntimeError(
                    f'Experience strategy "{strategy_name}" did not satisfy postcondition for "{label}".'
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

        def _execute_ai_click_locator(
            strategy_name: str, ai_locator: Locator, ai_strategy: dict[str, Any]
        ) -> bool:
            before_ai = facade._act_observe(page, ai_locator)
            facade._act_strict_click(ai_locator)
            after_ai = facade._act_observe(page, ai_locator)
            return postcondition(before_ai, after_ai)

        ai_result, last_error = facade._act_execute_ai_repair_rounds(
            current_page=page,
            helper=helper,
            label=label,
            last_error=last_error,
            locator=locator,
            postcondition_kind="action_effect",
            failure_message=lambda strategy_name: f'AI strategy "{strategy_name}" did not satisfy postcondition for "{label}".',
            execute_locator=_execute_ai_click_locator,
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
            facade._act_set_debug_detail("click_with_candidates", debug_trace)
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
                action_type=helper,
                label=label,
                page=page,
                locator=ai_locator,
                error=last_error,
                status="success",
                postcondition_kind="action_effect",
                postcondition_passed=True,
            )
            return
        debug_trace["ai_repair"] = {
            "status": "failed",
            "error": facade._act_trim_debug_text(last_error, 320),
        }
        debug_trace["status"] = "failed"
        debug_trace["final_error"] = facade._act_trim_debug_text(last_error, 320)
        facade._act_set_debug_detail("click_with_candidates", debug_trace)
        raise RuntimeError(
            f'Unable to click target "{label}" after strict execution, Oracle recovery, and AI self-repair.'
        ) from last_error
