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
    "_act_oracle_invoice_shows_not_validated",
    "_act_oracle_menu_option_is_completion_action",
    "_act_oracle_menu_trigger_requires_option_visibility",
    "_act_menu_panel_option_candidates",
    "_act_oracle_visible_popup_option_candidates",
    "_act_wait_for_oracle_menu_trigger_option_visibility",
    "_act_oracle_invoice_accounting_ready",
    "_act_wait_for_menu_option_semantic_condition",
    "_act_oracle_transaction_completion_advanced",
    "_act_oracle_transaction_completed",
    "_act_oracle_transaction_saved_pending_completion",
    "_act_reassert_complete_and_review",
    "_act_oracle_menu_option_requires_semantic_validation",
    "_act_oracle_menu_option_semantic_postcondition",
    "_act_menu_option_failure_message",
    "_act_menu_trigger_failure_message",
    "_act_reopen_menu_if_options_hidden",
    "_act_select_adf_menu_panel_option",
]


def _act_oracle_invoice_shows_not_validated(page: Page | None) -> bool:
    visible_text = facade._act_page_visible_text(page)
    return "not validated" in visible_text or "never validated" in visible_text


def _act_oracle_menu_option_is_completion_action(trigger_label: str) -> bool:
    return facade._act_normalize_text(trigger_label) in facade._ACT_COMPLETION_SPLIT_TRIGGERS


def _act_oracle_menu_trigger_requires_option_visibility(trigger_label: str) -> bool:
    return facade._act_normalize_text(
        trigger_label
    ) == "invoice actions" or facade._act_oracle_menu_option_is_completion_action(trigger_label)


def _act_menu_panel_option_candidates(
    current_page: Page, option: Locator, option_name: str
) -> list[tuple[str, Locator]]:
    return [
        ("raw_option", option),
        ("role_menuitem", current_page.get_by_role("menuitem", name=option_name, exact=True)),
        ("role_option", current_page.get_by_role("option", name=option_name, exact=True)),
        ("text_option", current_page.get_by_text(option_name, exact=True)),
    ]


def _act_oracle_visible_popup_option_candidates(
    current_page: Page, option_name: str
) -> list[tuple[str, Locator]]:
    page_locator = getattr(current_page, "locator", None)
    if not callable(page_locator):
        return []
    candidates: list[tuple[str, Locator]] = []
    popup_selectors = ["[role='menu']:visible", ".oj-popup:visible"]
    for selector in popup_selectors:
        try:
            popup_scope = current_page.locator(selector)
        except Exception:
            continue
        if popup_scope is None:
            continue
        get_by_role = getattr(popup_scope, "get_by_role", None)
        if callable(get_by_role):
            for role_name in ("menuitem", "link", "button", "option"):
                try:
                    candidates.append(
                        (
                            f"oracle_popup_{role_name}_{len(candidates)}",
                            popup_scope.get_by_role(role_name, name=option_name, exact=True),
                        )
                    )
                except Exception:
                    pass
        get_by_text = getattr(popup_scope, "get_by_text", None)
        if callable(get_by_text):
            try:
                candidates.append(
                    (
                        f"oracle_popup_text_{len(candidates)}",
                        popup_scope.get_by_text(option_name, exact=True),
                    )
                )
            except Exception:
                pass
    return candidates


def _act_wait_for_oracle_menu_trigger_option_visibility(
    page: Page | None, option_candidates: Sequence[tuple[str, Locator]], trigger_label: str
) -> bool:
    if not facade._act_oracle_menu_trigger_requires_option_visibility(trigger_label):
        return True
    if page is None:
        return False

    def _condition() -> bool:
        for _, candidate in option_candidates:
            if facade._act_locator_is_actionable(candidate, timeout_ms=250):
                return True
        return False

    if _condition():
        return True
    timeout_ms = max(0, facade._act_wait_ms("ACT_MENU_TRIGGER_OPTION_TIMEOUT_MS", 3000))
    if timeout_ms <= 0:
        return _condition()
    poll_ms = max(100, facade._act_wait_ms("ACT_MENU_TRIGGER_OPTION_POLL_MS", 250))
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        page.wait_for_timeout(poll_ms)
        if _condition():
            return True
    return _condition()


def _act_oracle_invoice_accounting_ready(page: Page | None) -> bool:
    result = facade._act_safe_page_eval(
        page,
        '() => {\n            const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim().toLowerCase();\n            const isVisible = (node) => {\n                if (!node) return false;\n                const style = window.getComputedStyle(node);\n                if (!style) return false;\n                if (style.display === "none" || style.visibility === "hidden") return false;\n                const rect = node.getBoundingClientRect();\n                return rect.width > 0 && rect.height > 0;\n            };\n            const hasExactInteractiveText = (selector, expected) => {\n                for (const node of document.querySelectorAll(selector)) {\n                    if (!isVisible(node)) continue;\n                    if (normalize(node.innerText || node.textContent) === expected) return true;\n                }\n                return false;\n            };\n            return {\n                has_view_accounting: hasExactInteractiveText(\'button, [role="button"], a, [role="link"]\', "view accounting"),\n                has_accounting_link: hasExactInteractiveText(\'a, [role="link"]\', "accounting"),\n            };\n        }',
    )
    if isinstance(result, dict):
        return bool(result.get("has_view_accounting") or result.get("has_accounting_link"))
    return False


def _act_wait_for_menu_option_semantic_condition(
    page: Page | None, predicate, *, timeout_env_name: str, default_timeout_ms: int
) -> bool:
    if page is None:
        return False

    def _condition() -> bool:
        try:
            return bool(predicate())
        except Exception:
            return False

    if _condition():
        return True
    timeout_ms = max(0, facade._act_wait_ms(timeout_env_name, default_timeout_ms))
    if timeout_ms <= 0:
        return _condition()
    poll_ms = max(100, facade._act_wait_ms("ACT_MENU_OPTION_SEMANTIC_POLL_MS", 250))
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        page.wait_for_timeout(poll_ms)
        if _condition():
            return True
    return _condition()


def _act_oracle_transaction_completion_advanced(
    page: Page | None, baseline: dict[str, Any] | None
) -> bool:
    if page is None:
        return False
    baseline = baseline if isinstance(baseline, dict) else {}
    try:
        current_url = str(getattr(page, "url", "") or "").strip()
    except Exception:
        current_url = ""
    try:
        current_title = str(page.title() or "").strip()
    except Exception:
        current_title = ""
    baseline_url = str(baseline.get("url") or "").strip()
    baseline_title = str(baseline.get("title") or "").strip()
    baseline_step = str(baseline.get("guided_step") or "").strip()
    current_step = str(facade._act_current_guided_step(page) or "").strip()
    baseline_guided_flow = (
        baseline.get("guided_flow") if isinstance(baseline.get("guided_flow"), dict) else {}
    )
    baseline_heading = facade._act_normalize_text(baseline_guided_flow.get("primary_heading"))
    current_guided_flow = facade._act_guided_flow_state(page)
    current_heading = facade._act_normalize_text((current_guided_flow or {}).get("primary_heading"))
    baseline_body = facade._act_normalize_text(baseline.get("body_marker"))
    current_body = facade._act_normalize_text(facade._act_body_marker(page))
    signals = {
        "url_changed": bool(baseline_url and current_url and (current_url != baseline_url)),
        "title_changed": bool(
            baseline_title and current_title and (current_title != baseline_title)
        ),
        "guided_step_changed": bool(
            baseline_step and current_step and (current_step != baseline_step)
        ),
        "heading_changed_to_review": bool(
            baseline_heading
            and current_heading
            and (current_heading != baseline_heading)
            and (
                current_heading.startswith("review transaction")
                or current_heading.startswith("view transaction")
            )
        ),
        "body_contains_review_transaction": bool(
            baseline_body
            and current_body
            and (current_body != baseline_body)
            and ("review transaction" in current_body)
            and ("review transaction" not in baseline_body)
        ),
        "status_incomplete_to_complete": bool(
            baseline_body
            and current_body
            and (current_body != baseline_body)
            and ("status complete" in current_body)
            and ("status incomplete" in baseline_body)
        ),
    }
    matched_signal = next((name for name, matched in signals.items() if matched), "")
    facade._act_set_debug_detail(
        "oracle_completion_check",
        {
            "baseline": {
                "url": baseline_url,
                "title": baseline_title,
                "guided_step": baseline_step,
                "primary_heading": baseline_heading,
                "body_marker": facade._act_trim_debug_text(baseline_body),
            },
            "current": {
                "url": current_url,
                "title": current_title,
                "guided_step": current_step,
                "primary_heading": current_heading,
                "body_marker": facade._act_trim_debug_text(current_body),
            },
            "signals": signals,
            "matched_signal": matched_signal,
            "postcondition_passed": bool(matched_signal),
        },
    )
    return bool(matched_signal)


def _act_oracle_transaction_completed(page: Page | None) -> bool:
    """True when the transaction has reached its completed / Review end state.

    Absolute check (no baseline): the Review page renders a "review transaction ..." or
    "status complete" body marker, or the heading switches to "review/view transaction". Used
    to detect success without re-clicking, and to STOP the completion re-assert the instant
    Review shows -- so a transaction that already completed is never re-submitted.
    """
    if page is None:
        return False
    body = facade._act_normalize_text(facade._act_body_marker(page))
    guided_flow = facade._act_guided_flow_state(page) or {}
    heading = facade._act_normalize_text(guided_flow.get("primary_heading"))
    if body and (("review transaction" in body) or ("status complete" in body)):
        return True
    return heading.startswith("review transaction") or heading.startswith("view transaction")


def _act_oracle_transaction_saved_pending_completion(page: Page | None) -> bool:
    """True for the deterministic "save landed, completion pending" state.

    The transaction is saved and still editable ("edit transaction: <number>" heading) but
    Review / complete has NOT rendered. This is exactly the state the flaky "Complete and
    Review" leaves on a slow pod: the click committed only the save phase and the page went
    idle before the Review phase. From here one more "Complete and Review" deterministically
    reaches Review (the single-click case that already passes). Returns False once completed
    (no double submit) and False on an unsaved "create transaction" draft (nothing to re-assert).
    """
    if page is None:
        return False
    if facade._act_oracle_transaction_completed(page):
        return False
    guided_flow = facade._act_guided_flow_state(page) or {}
    heading = facade._act_normalize_text(guided_flow.get("primary_heading"))
    return heading.startswith("edit transaction")


def _act_reassert_complete_and_review(
    trigger: Locator,
    option: Locator,
    current_page: Page,
    trigger_label: str,
    option_name: str,
    debug_trace: dict[str, Any],
) -> bool:
    """Deterministic recovery for the ADF completion split-button ("Complete and Review").

    The first activation on a fresh draft can land only the SAVE phase: the transaction becomes
    "edit transaction: <number>" (saved, still incomplete) and the page goes idle before the
    Complete+Review phase renders -- so the completion postcondition passes on a fast pod and
    fails on a slow one (the "same script, different result" flakiness). From that
    saved-but-incomplete state one more "Complete and Review" deterministically reaches Review
    (exactly the single-click case that already passes).

    Re-asserts up to ACT_COMPLETION_REASSERT_MAX times, each gated on the saved-pending-completion
    state, so it: never re-clicks a transaction that already reached Review (no double submit);
    never fires on an unsaved "create transaction" form; and fails honestly -- no loop -- when a
    real validation message blocks completion. Net effect: pass-always or fail-always, independent
    of pod speed.
    """
    max_attempts = max(0, int(facade._act_wait_ms("ACT_COMPLETION_REASSERT_MAX", 2)))
    if max_attempts <= 0:
        return False
    attempts_log = debug_trace.setdefault("completion_reassert_attempts", [])
    probe_ms = facade._act_wait_ms("ACT_MENU_OPTION_PROBE_MS", 1000)
    settle_ms = facade._act_wait_ms("ACT_ORACLE_PPR_SETTLE_MS", 10000)
    poll_ms = facade._act_wait_ms("ACT_ORACLE_PPR_SETTLE_POLL_MS", 250)

    def _record(attempt: int, status: str, **extra: Any) -> None:
        if isinstance(attempts_log, list):
            entry: dict[str, Any] = {"attempt": attempt, "status": status}
            entry.update(extra)
            attempts_log.append(entry)

    for attempt in range(1, max_attempts + 1):
        # Let any in-flight save/PPR settle so we judge a stable state, not a mid-render one.
        deadline = time.time() + settle_ms / 1000.0
        while time.time() < deadline and facade._act_busy_indicator_count(current_page) > 0:
            try:
                current_page.wait_for_timeout(poll_ms)
            except Exception:
                break
        probe_observation = facade._act_debug_observation_summary(
            facade._act_observe(current_page)
        )
        if facade._act_oracle_transaction_completed(current_page):
            _record(attempt, "already_complete", probe_observation=probe_observation)
            return True
        if not facade._act_oracle_transaction_saved_pending_completion(current_page):
            _record(attempt, "not_saved_pending", probe_observation=probe_observation)
            return False
        validation = facade._act_collect_validation_messages(current_page)
        if validation:
            _record(
                attempt,
                "blocked_by_validation",
                messages=[facade._act_trim_debug_text(m, 200) for m in validation],
                probe_observation=probe_observation,
            )
            return False
        clicked = False
        try:
            facade._act_strict_click(trigger)
            current_page.wait_for_timeout(facade._act_wait_ms("ACT_MENU_OPEN_WAIT_MS", 350))
            candidates = facade._act_menu_panel_option_candidates(
                current_page, option, option_name
            )[:2]
            for _strategy_name, candidate in candidates:
                if facade._act_locator_is_actionable(candidate, timeout_ms=probe_ms):
                    facade._act_strict_click(candidate)
                    clicked = True
                    break
        except Exception as exc:
            _record(
                attempt,
                "reclick_failed",
                error=facade._act_trim_debug_text(exc, 200),
                probe_observation=probe_observation,
            )
            continue
        if not clicked:
            _record(attempt, "option_not_actionable", probe_observation=probe_observation)
            continue
        reached = facade._act_wait_for_menu_option_semantic_condition(
            current_page,
            lambda: facade._act_oracle_transaction_completed(current_page),
            timeout_env_name="ACT_TRANSACTION_COMPLETE_POSTCONDITION_TIMEOUT_MS",
            default_timeout_ms=8000,
        )
        after_reclick = facade._act_debug_observation_summary(facade._act_observe(current_page))
        _record(
            attempt,
            "reached_review" if reached else "still_incomplete",
            probe_observation=after_reclick,
        )
        if reached:
            return True
    return False


def _act_oracle_menu_option_requires_semantic_validation(
    trigger_label: str, option_name: str
) -> bool:
    normalized_trigger = facade._act_normalize_text(trigger_label)
    normalized_option = facade._act_normalize_text(option_name)
    if facade._act_oracle_menu_option_is_completion_action(trigger_label):
        return True
    if normalized_trigger != "invoice actions":
        return False
    return normalized_option in {"validate", "account in final"}


def _act_oracle_menu_option_semantic_postcondition(
    page: Page | None, trigger_label: str, option_name: str, *, before: dict[str, Any] | None = None
) -> bool | None:
    if not facade._act_oracle_menu_option_requires_semantic_validation(trigger_label, option_name):
        return None
    if facade._act_oracle_menu_option_is_completion_action(trigger_label):
        return facade._act_wait_for_menu_option_semantic_condition(
            page,
            lambda: facade._act_oracle_transaction_completion_advanced(page, before),
            timeout_env_name="ACT_TRANSACTION_COMPLETE_POSTCONDITION_TIMEOUT_MS",
            default_timeout_ms=8000,
        )
    normalized_option = facade._act_normalize_text(option_name)
    if normalized_option == "validate":
        return facade._act_wait_for_menu_option_semantic_condition(
            page,
            lambda: not facade._act_oracle_invoice_shows_not_validated(page),
            timeout_env_name="ACT_INVOICE_VALIDATE_POSTCONDITION_TIMEOUT_MS",
            default_timeout_ms=20000,
        )
    if normalized_option == "account in final":
        return facade._act_wait_for_menu_option_semantic_condition(
            page,
            lambda: facade._act_oracle_invoice_accounting_ready(page),
            timeout_env_name="ACT_INVOICE_ACCOUNTING_POSTCONDITION_TIMEOUT_MS",
            default_timeout_ms=10000,
        )
    return None


def _act_menu_option_failure_message(trigger_label: str, option_name: str) -> str:
    if facade._act_oracle_menu_option_is_completion_action(trigger_label):
        return f'"{option_name}" did not complete the transaction: the flow did not advance (no navigation / guided-step change), so it stays in the editable draft.'
    if facade._act_oracle_menu_option_requires_semantic_validation(trigger_label, option_name):
        normalized_option = facade._act_normalize_text(option_name)
        if normalized_option == "validate":
            return 'Invoice Actions "Validate" did not clear the "Not validated" status.'
        if normalized_option == "account in final":
            return 'Invoice Actions "Account in Final" did not expose the Accounting action.'
    return f'Menu panel "{trigger_label}" did not apply option "{option_name}".'


def _act_menu_trigger_failure_message(trigger_label: str, option_name: str) -> str:
    if facade._act_oracle_menu_trigger_requires_option_visibility(trigger_label):
        return f'{trigger_label} did not expose menu option "{option_name}".'
    return f'Menu panel "{trigger_label}" did not expose option "{option_name}".'


def _act_reopen_menu_if_options_hidden(
    trigger: Locator, current_page: Page, candidate: Locator
) -> None:
    probe_ms = facade._act_wait_ms("ACT_MENU_REOPEN_PROBE_MS", 300)
    if facade._act_locator_is_actionable(candidate, timeout_ms=probe_ms):
        return
    try:
        facade._act_strict_click(trigger)
        current_page.wait_for_timeout(facade._act_wait_ms("ACT_MENU_OPEN_WAIT_MS", 350))
    except Exception:
        pass


def _act_select_adf_menu_panel_option(
    trigger: Locator,
    option: Locator,
    current_page: Page,
    trigger_label: str,
    option_name: str,
    *,
    trigger_kind: str = "title",
) -> None:
    facade._act_register_page(current_page)
    debug_trace = facade._act_update_debug_detail(
        "select_adf_menu_panel_option",
        {
            "trigger_label": trigger_label,
            "option_name": option_name,
            "status": "open_menu",
            "option_attempts": [],
            "experience_attempts": [],
        },
    )
    facade._act_strict_click(trigger)
    current_page.wait_for_timeout(facade._act_wait_ms("ACT_MENU_OPEN_WAIT_MS", 350))
    is_completion = facade._act_oracle_menu_option_is_completion_action(trigger_label)
    option_candidates = facade._act_menu_panel_option_candidates(current_page, option, option_name)
    if is_completion:
        option_candidates = option_candidates[:2]
    menu_option_visible = facade._act_wait_for_oracle_menu_trigger_option_visibility(
        current_page, option_candidates, trigger_label
    )
    if not menu_option_visible and (not is_completion):
        debug_trace["status"] = "failed"
        debug_trace["final_error"] = facade._act_trim_debug_text(
            facade._act_menu_trigger_failure_message(trigger_label, option_name), 320
        )
        facade._act_set_debug_detail("select_adf_menu_panel_option", debug_trace)
        raise RuntimeError(facade._act_menu_trigger_failure_message(trigger_label, option_name))
    if not menu_option_visible and is_completion:
        debug_trace["menu_visibility_probe"] = "not_visible_continuing_with_completion_candidates"
        popup_candidates = facade._act_oracle_visible_popup_option_candidates(
            current_page, option_name
        )
        if popup_candidates:
            option_candidates = popup_candidates + option_candidates
    last_error: Exception | None = None
    semantic_failure_after_click = False
    option_target = str(option_name or "").strip()
    option_probe_ms = facade._act_wait_ms("ACT_MENU_OPTION_PROBE_MS", 1000)
    for index, (strategy_name, candidate) in enumerate(option_candidates):
        try:
            facade._act_record_strategy_attempt(strategy_name)
            if is_completion:
                facade._act_reopen_menu_if_options_hidden(trigger, current_page, candidate)
                if not facade._act_locator_is_actionable(candidate, timeout_ms=option_probe_ms):
                    option_attempts = debug_trace.setdefault("option_attempts", [])
                    if isinstance(option_attempts, list):
                        option_attempts.append(
                            {"strategy_name": strategy_name, "status": "not_actionable"}
                        )
                    continue
            else:
                if index > 0:
                    facade._act_reopen_menu_if_options_hidden(trigger, current_page, candidate)
                if not facade._act_locator_is_actionable(candidate, timeout_ms=option_probe_ms):
                    option_attempts = debug_trace.setdefault("option_attempts", [])
                    if isinstance(option_attempts, list):
                        option_attempts.append(
                            {"strategy_name": strategy_name, "status": "not_actionable"}
                        )
                    continue
            before = facade._act_observe(current_page, candidate)
            facade._act_strict_click(candidate)
            current_page.wait_for_timeout(facade._act_wait_ms("ACT_POST_CLICK_WAIT_MS", 250))
            after = facade._act_observe(current_page, candidate)
            if facade._act_option_selection_postcondition(
                before,
                after,
                trigger,
                candidate,
                option_name,
                page=current_page,
                trigger_label=trigger_label,
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
                facade._act_set_debug_detail("select_adf_menu_panel_option", debug_trace)
                return
            last_error = RuntimeError(
                facade._act_menu_option_failure_message(trigger_label, option_name)
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
            if facade._act_oracle_menu_option_requires_semantic_validation(
                trigger_label, option_name
            ) and (not is_completion):
                semantic_failure_after_click = True
                break
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
        if is_completion:
            last_error = RuntimeError(
                facade._act_menu_trigger_failure_message(trigger_label, option_name)
            )
        else:
            last_error = RuntimeError(
                facade._act_menu_option_failure_message(trigger_label, option_name)
            )
    if semantic_failure_after_click:
        debug_trace["status"] = "failed"
        debug_trace["final_error"] = facade._act_trim_debug_text(last_error, 320)
        facade._act_set_debug_detail("select_adf_menu_panel_option", debug_trace)
        raise last_error
    if is_completion:
        # try B/C: the first activation can land only the save phase, leaving the transaction
        # saved-but-incomplete ("edit transaction: <n>", idle) on a slow pod. Re-assert
        # "Complete and Review" from that state until Review renders -- deterministic and
        # gated so a completed transaction is never re-submitted. See
        # _act_reassert_complete_and_review.
        if facade._act_reassert_complete_and_review(
            trigger, option, current_page, trigger_label, option_name, debug_trace
        ):
            debug_trace["resolved_by"] = "completion_reassert"
            debug_trace["status"] = "success"
            facade._act_set_debug_detail("select_adf_menu_panel_option", debug_trace)
            return
        debug_trace["status"] = "failed"
        debug_trace["final_error"] = facade._act_trim_debug_text(last_error, 320)
        facade._act_set_debug_detail("select_adf_menu_panel_option", debug_trace)
        raise last_error
    for strategy_name, experience_locator, episode in facade._act_experience_repair_locators(
        current_page, "select_adf_menu_panel_option", option_target, last_error, locator=option
    ):
        try:
            facade._act_record_strategy_attempt(strategy_name)
            before_experience = facade._act_observe(current_page, experience_locator)
            facade._act_strict_click(experience_locator)
            current_page.wait_for_timeout(facade._act_wait_ms("ACT_POST_CLICK_WAIT_MS", 250))
            after_experience = facade._act_observe(current_page, experience_locator)
            if facade._act_option_selection_postcondition(
                before_experience,
                after_experience,
                trigger,
                experience_locator,
                option_name,
                page=current_page,
                trigger_label=trigger_label,
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
                facade._act_set_debug_detail("select_adf_menu_panel_option", debug_trace)
                facade._act_set_recovery_record(
                    "experience_reuse",
                    str((episode.get("recovery") or {}).get("kind") or "").strip()
                    or "experience_reuse",
                    "experience_reuse",
                    {
                        "trigger_label": trigger_label,
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
                    action_type="select_adf_menu_panel_option",
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
                facade._act_menu_option_failure_message(trigger_label, option_name)
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
            if facade._act_oracle_menu_option_requires_semantic_validation(
                trigger_label, option_name
            ):
                semantic_failure_after_click = True
                break
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
    if semantic_failure_after_click:
        debug_trace["status"] = "failed"
        debug_trace["final_error"] = facade._act_trim_debug_text(last_error, 320)
        facade._act_set_debug_detail("select_adf_menu_panel_option", debug_trace)
        raise last_error
    if not debug_trace.get("experience_attempts"):
        debug_trace["experience_attempts"] = [{"status": "no_candidates"}]

    def _execute_ai_menu_locator(
        strategy_name: str, ai_locator: Locator, ai_strategy: dict[str, Any]
    ) -> bool:
        nonlocal semantic_failure_after_click
        before_ai = facade._act_observe(current_page, ai_locator)
        facade._act_strict_click(ai_locator)
        current_page.wait_for_timeout(facade._act_wait_ms("ACT_POST_CLICK_WAIT_MS", 250))
        after_ai = facade._act_observe(current_page, ai_locator)
        if facade._act_option_selection_postcondition(
            before_ai,
            after_ai,
            trigger,
            ai_locator,
            option_name,
            page=current_page,
            trigger_label=trigger_label,
        ):
            facade._act_wait_for_field_processing(
                current_page, env_name="ACT_DROPDOWN_CHANGE_PROCESSING_WAIT_MS", default_ms=5000
            )
            return True
        if facade._act_oracle_menu_option_requires_semantic_validation(trigger_label, option_name):
            semantic_failure_after_click = True
        return False

    ai_result, last_error = facade._act_execute_ai_repair_rounds(
        current_page=current_page,
        helper="select_adf_menu_panel_option",
        label=option_target,
        last_error=last_error,
        locator=option,
        postcondition_kind="option_selected",
        failure_message=lambda strategy_name: facade._act_menu_option_failure_message(
            trigger_label, option_name
        ),
        execute_locator=_execute_ai_menu_locator,
    )
    if semantic_failure_after_click:
        debug_trace["status"] = "failed"
        debug_trace["final_error"] = facade._act_trim_debug_text(last_error, 320)
        facade._act_set_debug_detail("select_adf_menu_panel_option", debug_trace)
        raise last_error
    if ai_result is not None:
        strategy_name, ai_locator, ai_strategy = ai_result
        debug_trace["ai_repair"] = {
            "status": "validated",
            "strategy_name": strategy_name,
            "locator_strategy": facade._act_clone_json_value(ai_strategy),
        }
        debug_trace["resolved_by"] = "ai_locator_repair"
        debug_trace["status"] = "success"
        facade._act_set_debug_detail("select_adf_menu_panel_option", debug_trace)
        facade._act_set_recovery_record(
            "ai_validated",
            "ai_locator_repair",
            "ai_locator_repair",
            {
                "trigger_label": trigger_label,
                "option_name": option_name,
                "strategy_name": strategy_name,
                "locator_strategy": facade._act_clone_json_value(ai_strategy),
            },
        )
        facade._act_store_experience_episode(
            action_type="select_adf_menu_panel_option",
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
    facade._act_set_debug_detail("select_adf_menu_panel_option", debug_trace)
    raise RuntimeError(
        f'Unable to apply menu option "{option_name}" for "{trigger_label}".'
    ) from last_error
