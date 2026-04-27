import src.utils.html_report_generator as report_generator
from src.utils.html_report_generator import (
    _format_action_duration,
    _format_duration_minutes,
    generate_html_report_content,
)


def test_format_duration_minutes_uses_minute_units() -> None:
    assert _format_duration_minutes(30) == "0.5 mins"
    assert _format_duration_minutes(60) == "1 min"
    assert _format_duration_minutes(125) == "2.1 mins"


def test_format_action_duration_uses_clock_style() -> None:
    assert _format_action_duration(0) == "0:00"
    assert _format_action_duration(321) == "<0:01"
    assert _format_action_duration(1329) == "0:01"
    assert _format_action_duration(5545) == "0:06"
    assert _format_action_duration(65_000) == "1:05"


def test_generate_html_report_content_renders_minutes_in_summary_and_result_cards() -> None:
    html = generate_html_report_content(
        test_suite_id="suite-1",
        parent_run_id="run-1",
        results=[
            {
                "recording_id": "rec-1",
                "recording_name": "recordings/demo.py",
                "file_key": "recordings/demo.py",
                "status": "passed",
                "duration_seconds": 125,
                "exit_code": 0,
                "page_title": "Demo Page",
                "stdout": "",
                "stderr": "",
                "error": "",
                "step_artifacts": [],
                "screenshot_s3_key": "",
                "video_s3_key": "",
                "video_s3_keys": [],
            }
        ],
    )

    assert "Total Duration" in html
    assert "2.1 mins" in html
    assert "125s" not in html


def test_generate_html_report_content_prefers_recording_name_over_file_key() -> None:
    html = generate_html_report_content(
        test_suite_id="suite-1",
        parent_run_id="run-1",
        results=[
            {
                "recording_id": "rec-1-row-2",
                "recording_name": "fake_2 [row 2]",
                "file_key": "recordings/8279897e-21b5-4781-ab82-fd4bd0095355/fake_2.py",
                "status": "passed",
                "duration_seconds": 30,
                "exit_code": 0,
                "page_title": "Demo Page",
                "stdout": "",
                "stderr": "",
                "error": "",
                "step_artifacts": [],
                "screenshot_s3_key": "",
                "video_s3_key": "",
                "video_s3_keys": [],
            }
        ],
    )

    assert "fake 2 [row 2]" in html


def test_generate_html_report_content_renders_fallback_attempt_summary_and_trace() -> None:
    html = generate_html_report_content(
        test_suite_id="suite-1",
        parent_run_id="run-1",
        results=[
            {
                "recording_id": "rec-1",
                "recording_name": "recordings/demo.py",
                "file_key": "recordings/demo.py",
                "status": "failed",
                "duration_seconds": 42,
                "exit_code": 1,
                "page_title": "Demo Page",
                "stdout": "",
                "stderr": "boom",
                "error": "Execution failed",
                "step_artifacts": [],
                "screenshot_s3_key": "",
                "video_s3_key": "",
                "video_s3_keys": [],
                "action_log": [
                    {
                        "step": 3,
                        "action": "click_textbox",
                        "label": "Notes",
                        "status": "failed",
                        "strategy": "oj_label_hint",
                        "duration_ms": 321,
                        "fallback_attempt_count": 3,
                        "fallback_strategy_count": 3,
                        "fallback_strategies": [
                            "label_exact",
                            "placeholder",
                            "oj_label_hint",
                        ],
                        "fallback_strategies_unique": [
                            "label_exact",
                            "placeholder",
                            "oj_label_hint",
                        ],
                        "error": 'Unable to click text entry "Notes".',
                        "failure_context": {
                            "helper": "click_textbox",
                            "label": "Notes",
                            "page_title": "Demo Page",
                            "page_url": "https://example.test/form",
                            "ready_state": "complete",
                            "busy_indicator_count": 0,
                            "dom_candidate_count": 1,
                            "active_element": {
                                "tag": "input",
                                "role": "textbox",
                                "label_hint": "Notes",
                                "html": '<input aria-label="Notes" />',
                            },
                            "dom_context": {
                                "helper": "click_textbox",
                                "label": "Notes",
                                "candidates": [
                                    {
                                        "tag": "oj-c-text-area",
                                        "role": "",
                                        "label_hint": "Notes",
                                        "text": "Required",
                                        "html": '<oj-c-text-area label-hint="Notes">Required</oj-c-text-area>',
                                    }
                                ],
                            },
                        },
                    },
                    {
                        "step": 4,
                        "action": "navigation_button",
                        "label": "Continue",
                        "status": "success",
                        "strategy": "direct",
                        "duration_ms": 98,
                        "fallback_attempt_count": 0,
                        "fallback_strategy_count": 0,
                        "fallback_strategies": [],
                        "fallback_strategies_unique": [],
                    },
                ],
            }
        ],
    )

    assert "Action Timeline" in html
    assert "3 attempts across 1 step" in html
    assert "Each recorded script action is shown as one debug card with its screenshot, outcome, recovery path, and failure context together." in html
    assert "Recorded 2 script actions: 1 completed action and 1 failed action. 1 action needed recovery, with 3 recovery attempts in total. No AI-assisted repair calls were needed." in html
    assert "The runner could not complete this step after 3 recovery attempts across 3 strategies." in html
    assert "The recorded target worked on the first attempt." in html
    assert "Resolution: Recorded target" in html
    assert "Completed" in html
    assert "&lt;0:01" in html
    assert "Exact label match" in html
    assert "Placeholder match" in html
    assert "Oracle Label Hint" in html
    assert "Recovery Details" in html
    assert "Why This Step Failed" in html
    assert "Page Context At Failure" in html
    assert "Elements Checked" in html
    assert "Required" in html
    assert "No screenshot was captured for this action." in html


def test_generate_html_report_content_renders_step_level_ai_trace_details() -> None:
    html = generate_html_report_content(
        test_suite_id="suite-1",
        parent_run_id="run-1",
        results=[
            {
                "recording_id": "rec-ai",
                "recording_name": "recordings/demo_ai.py",
                "file_key": "recordings/demo_ai.py",
                "status": "failed",
                "duration_seconds": 25,
                "exit_code": 1,
                "page_title": "Demo Page",
                "stdout": "",
                "stderr": "boom",
                "error": "Execution failed",
                "step_artifacts": [],
                "screenshot_s3_key": "",
                "video_s3_key": "",
                "video_s3_keys": [],
                "action_log": [
                    {
                        "step": 7,
                        "action": "click_textbox",
                        "label": "Notes",
                        "status": "failed",
                        "strategy": "ai_css_1",
                        "duration_ms": 902,
                        "fallback_attempt_count": 5,
                        "fallback_strategy_count": 5,
                        "fallback_strategies": [
                            "label_exact",
                            "placeholder",
                            "oj_label_hint",
                            "ai_self_repair_lookup",
                            "ai_css_1",
                        ],
                        "fallback_strategies_unique": [
                            "label_exact",
                            "placeholder",
                            "oj_label_hint",
                            "ai_self_repair_lookup",
                            "ai_css_1",
                        ],
                        "error": 'Unable to click text entry "Notes".',
                        "ai_interactions": [
                            {
                                "feature": "self_repair",
                                "helper": "click_textbox",
                                "label": "Notes",
                                "status": "success",
                                "model": "gpt-4.1-mini",
                                "endpoint": "https://api.openai.com/v1/responses",
                                "system_prompt": "You are a senior Playwright locator repair assistant.",
                                "user_prompt": "Find the Notes field from these DOM candidates.",
                                "response_text": '{"strategies":[{"kind":"css","selector":"oj-c-text-area[label-hint=\\"Notes\\"] textarea"}]}',
                                "parsed_response": {
                                    "strategies": [
                                        {
                                            "kind": "css",
                                            "selector": 'oj-c-text-area[label-hint="Notes"] textarea',
                                        }
                                    ]
                                },
                                "response_strategy_count": 1,
                                "response_strategies": [
                                    {
                                        "kind": "css",
                                        "selector": 'oj-c-text-area[label-hint="Notes"] textarea',
                                    }
                                ],
                                "locator_candidate_count": 1,
                                "locator_strategies": ["ai_css_1"],
                                "dom_candidate_count": 4,
                                "max_output_tokens": 400,
                                "http_status": 200,
                            },
                            {
                                "feature": "self_repair",
                                "helper": "click_textbox",
                                "label": "Notes",
                                "status": "request_error",
                                "model": "gpt-4.1-mini",
                                "endpoint": "https://api.openai.com/v1/responses",
                                "system_prompt": "You are a senior Playwright locator repair assistant.",
                                "user_prompt": "Find the Notes field from these DOM candidates.",
                                "error": "HTTP Error 429: Too Many Requests",
                                "error_type": "HTTPError",
                                "error_response_body": '{"error":{"message":"rate limited"}}',
                                "dom_candidate_count": 4,
                                "max_output_tokens": 400,
                                "http_status": 429,
                            },
                        ],
                    }
                ],
            }
        ],
    )

    assert "AI Repair Attempts" in html
    assert "1 AI self-repair call recorded." not in html
    assert "2 AI-assisted repair calls recorded." in html
    assert "Repair Attempt 1" in html
    assert "Repair Attempt 2" in html
    assert "System Instructions" in html
    assert "Context Sent To The Model" in html
    assert "Model Response" in html
    assert "Parsed Repair Plan" in html
    assert "Raw API Error" in html
    assert "Find the Notes field from these DOM candidates." in html
    assert "Too Many Requests" in html


def test_generate_html_report_content_ai_lookup_success_is_not_shown_as_action_success() -> None:
    html = generate_html_report_content(
        test_suite_id="suite-1",
        parent_run_id="run-1",
        results=[
            {
                "recording_id": "rec-ai-lookup",
                "recording_name": "recordings/demo.py",
                "file_key": "recordings/demo.py",
                "status": "failed",
                "duration_seconds": 8,
                "exit_code": 1,
                "page_title": "Demo Page",
                "stdout": "",
                "stderr": "",
                "error": "Execution failed",
                "step_artifacts": [],
                "screenshot_s3_key": "",
                "video_s3_key": "",
                "video_s3_keys": [],
                "action_log": [
                    {
                        "step": 1,
                        "action": "click_combobox",
                        "label": "Search for people to add as",
                        "status": "failed",
                        "strategy": "ai_css_1",
                        "duration_ms": 612,
                        "fallback_attempt_count": 2,
                        "fallback_strategy_count": 2,
                        "fallback_strategies": ["ai_self_repair_lookup", "ai_css_1"],
                        "fallback_strategies_unique": ["ai_self_repair_lookup", "ai_css_1"],
                        "error": 'Unable to open combobox "Search for people to add as".',
                        "ai_interactions": [
                            {
                                "feature": "self_repair",
                                "helper": "click_combobox",
                                "label": "Search for people to add as",
                                "status": "success",
                                "model": "gpt-4.1-mini",
                                "endpoint": "https://api.openai.com/v1/responses",
                                "response_strategy_count": 3,
                                "locator_candidate_count": 2,
                                "dom_candidate_count": 8,
                            }
                        ],
                    }
                ],
            }
        ],
    )

    assert "Suggestions Found" in html
    assert 'trace-chip trace-chip-success">Success<' not in html


def test_generate_html_report_content_renders_failed_ai_repair_attempt_with_runtime_outcome() -> None:
    html = generate_html_report_content(
        test_suite_id="suite-1",
        parent_run_id="run-1",
        results=[
            {
                "recording_id": "rec-ai-failed",
                "recording_name": "recordings/demo.py",
                "file_key": "recordings/demo.py",
                "status": "failed",
                "duration_seconds": 8,
                "exit_code": 1,
                "page_title": "Demo Page",
                "stdout": "",
                "stderr": "",
                "error": "Execution failed",
                "step_artifacts": [],
                "screenshot_s3_key": "",
                "video_s3_key": "",
                "video_s3_keys": [],
                "action_log": [
                    {
                        "step": 1,
                        "action": "click_combobox",
                        "label": "Search for people to add as",
                        "status": "failed",
                        "strategy": "ai_css_2",
                        "duration_ms": 612,
                        "fallback_attempt_count": 3,
                        "fallback_strategy_count": 3,
                        "fallback_strategies": ["ai_self_repair_lookup", "ai_role_1", "ai_css_2"],
                        "fallback_strategies_unique": ["ai_self_repair_lookup", "ai_role_1", "ai_css_2"],
                        "error": 'Unable to open combobox "Search for people to add as".',
                        "ai_interactions": [
                            {
                                "feature": "self_repair",
                                "helper": "click_combobox",
                                "label": "Search for people to add as",
                                "status": "success",
                                "repair_outcome": "execution_failed",
                                "last_locator_strategy": "ai_css_2",
                                "postcondition_kind": "dialog_opened",
                                "postcondition_passed": False,
                                "repair_error": 'AI strategy "ai_css_2" did not open combobox "Search for people to add as".',
                                "model": "gpt-4.1-mini",
                                "endpoint": "https://api.openai.com/v1/responses",
                                "response_strategy_count": 3,
                                "locator_candidate_count": 2,
                                "dom_candidate_count": 8,
                            }
                        ],
                    }
                ],
            }
        ],
    )

    assert 'trace-chip trace-chip-failed">Failed<' in html
    assert "Locator: AI repaired CSS locator" in html
    assert "did not open combobox" in html


def test_generate_html_report_content_step_gallery_shows_final_action_outcome() -> None:
    html = generate_html_report_content(
        test_suite_id="suite-1",
        parent_run_id="run-1",
        results=[
            {
                "recording_id": "rec-steps",
                "recording_name": "recordings/demo_steps.py",
                "file_key": "recordings/demo_steps.py",
                "status": "failed",
                "duration_seconds": 12,
                "exit_code": 1,
                "page_title": "Demo Page",
                "stdout": "",
                "stderr": "",
                "error": "Execution failed",
                "step_artifacts": [
                    {"index": 1, "action": "goto", "screenshot_s3_key": ""},
                    {"index": 2, "action": "date_pick", "screenshot_s3_key": ""},
                    {"index": 3, "action": "navigation_button", "screenshot_s3_key": ""},
                ],
                "action_log": [
                    {
                        "step": 1,
                        "action": "date_pick",
                        "label": "Select Date.",
                        "status": "success",
                        "strategy": "day_select",
                        "duration_ms": 5200,
                        "fallback_attempt_count": 1,
                        "fallback_strategy_count": 1,
                        "fallback_strategies": ["day_select"],
                        "fallback_strategies_unique": ["day_select"],
                    },
                    {
                        "step": 2,
                        "action": "navigation_button",
                        "label": "Continue",
                        "status": "failed",
                        "strategy": "direct",
                        "duration_ms": 900,
                        "fallback_attempt_count": 0,
                        "fallback_strategy_count": 0,
                        "fallback_strategies": [],
                        "fallback_strategies_unique": [],
                        "error": "Continue did not advance.",
                    },
                ],
                "screenshot_s3_key": "",
                "video_s3_key": "",
                "video_s3_keys": [],
            }
        ],
    )

    assert "Final action outcome: Completed." in html
    assert "Final action outcome: Failed." in html


def test_generate_html_report_content_groups_screenshot_and_trace_in_same_action_card() -> None:
    html = generate_html_report_content(
        test_suite_id="suite-1",
        parent_run_id="run-1",
        results=[
            {
                "recording_id": "rec-combined",
                "recording_name": "recordings/demo_combined.py",
                "file_key": "recordings/demo_combined.py",
                "status": "failed",
                "duration_seconds": 12,
                "exit_code": 1,
                "page_title": "Demo Page",
                "stdout": "",
                "stderr": "",
                "error": "Execution failed",
                "step_artifacts": [
                    {"index": 1, "action": "goto", "screenshot_s3_key": ""},
                    {"index": 2, "action": "click_button", "screenshot_s3_key": ""},
                ],
                "action_log": [
                    {
                        "step": 1,
                        "action": "click_button",
                        "label": "Continue",
                        "status": "failed",
                        "strategy": "direct",
                        "duration_ms": 900,
                        "fallback_attempt_count": 0,
                        "fallback_strategy_count": 0,
                        "fallback_strategies": [],
                        "fallback_strategies_unique": [],
                        "error": "Continue did not advance.",
                    },
                ],
                "screenshot_s3_key": "",
                "video_s3_key": "",
                "video_s3_keys": [],
            }
        ],
    )

    assert "Captured immediately after script step 2. Final action outcome: Failed." in html
    assert "Additional Captures" in html


def test_generate_html_report_content_embeds_failure_capture_inside_failed_action_card(monkeypatch) -> None:
    monkeypatch.setattr(
        report_generator,
        "_to_data_uri",
        lambda key: "data:image/png;base64,ZmFrZQ==" if key == "failure.png" else "",
    )

    html = generate_html_report_content(
        test_suite_id="suite-1",
        parent_run_id="run-1",
        results=[
            {
                "recording_id": "rec-failure-image",
                "recording_name": "recordings/demo_failure_image.py",
                "file_key": "recordings/demo_failure_image.py",
                "status": "failed",
                "duration_seconds": 12,
                "exit_code": 1,
                "page_title": "Demo Page",
                "stdout": "",
                "stderr": "",
                "error": "Execution failed",
                "step_artifacts": [],
                "action_log": [
                    {
                        "step": 1,
                        "action": "navigation_button",
                        "label": "Continue",
                        "status": "failed",
                        "strategy": "direct",
                        "duration_ms": 900,
                        "fallback_attempt_count": 0,
                        "fallback_strategy_count": 0,
                        "fallback_strategies": [],
                        "fallback_strategies_unique": [],
                        "error": "Continue did not advance.",
                    }
                ],
                "screenshot_s3_key": "failure.png",
                "video_s3_key": "",
                "video_s3_keys": [],
            }
        ],
    )

    assert "Failure capture" in html
    assert "Captured at the point of failure for this action." in html
    assert "Failure Screenshot" not in html


def test_generate_html_report_content_renders_recovery_details_and_experience_lookup() -> None:
    html = generate_html_report_content(
        test_suite_id="suite-1",
        parent_run_id="run-1",
        results=[
            {
                "recording_id": "rec-recovery",
                "recording_name": "recordings/demo_recovery.py",
                "file_key": "recordings/demo_recovery.py",
                "status": "passed",
                "duration_seconds": 18,
                "exit_code": 0,
                "page_title": "Demo Page",
                "stdout": "",
                "stderr": "",
                "error": "",
                "step_artifacts": [],
                "screenshot_s3_key": "",
                "video_s3_key": "",
                "video_s3_keys": [],
                "action_log": [
                    {
                        "step": 1,
                        "action": "click_link",
                        "label": "Home",
                        "status": "success",
                        "strategy": "ai_css_1",
                        "duration_ms": 742,
                        "fallback_attempt_count": 4,
                        "fallback_strategy_count": 4,
                        "fallback_strategies": [
                            "direct",
                            "experience_lookup",
                            "ai_self_repair_lookup",
                            "ai_css_1",
                        ],
                        "fallback_strategies_unique": [
                            "direct",
                            "experience_lookup",
                            "ai_self_repair_lookup",
                            "ai_css_1",
                        ],
                        "experience_interactions": [
                            {
                                "feature": "experience_recovery",
                                "helper": "click_text_target",
                                "label": "Home",
                                "status": "miss",
                                "candidate_count": 0,
                            }
                        ],
                        "recovery": {
                            "source": "ai_validated",
                            "kind": "ai_locator_repair",
                            "handler_name": "ai_locator_repair",
                            "details": {
                                "strategy_name": "ai_css_1",
                                "locator_strategy": {
                                    "kind": "css",
                                    "selector": "#pt1\\:_UIShome",
                                },
                            },
                        },
                    }
                ],
            }
        ],
    )

    assert "Recovery Details" in html
    assert "The runner tried 3 recovery attempts across 3 strategies." in html
    assert "Final resolution: an AI-suggested locator that passed validation (AI repaired CSS locator)." in html
    assert "Learned recovery lookup" in html
    assert "AI repair lookup" in html
    assert "AI repaired CSS locator" in html
    assert "Recovery Debug Data" in html
