import src.utils.html_report_generator as report_generator
from src.utils.html_report_generator import (
    _format_action_duration,
    _format_duration_minutes,
    generate_html_report_content,
)


def _action(
    *,
    step: int,
    action: str,
    label: str,
    status: str = "success",
    strategy: str = "direct",
    duration_ms: int = 1_000,
    fallback_strategies: list[str] | None = None,
    fallback_attempt_count: int | None = None,
    script_value: str | None = None,
    **extra,
) -> dict:
    strategies = list(fallback_strategies or [strategy])
    payload = {
        "step": step,
        "action": action,
        "label": label,
        "status": status,
        "strategy": strategy,
        "duration_ms": duration_ms,
        "fallback_attempt_count": fallback_attempt_count or len(strategies),
        "fallback_strategy_count": len(strategies),
        "fallback_strategies": strategies,
        "fallback_strategies_unique": list(dict.fromkeys(strategies)),
        "ai_interactions": [],
        "experience_interactions": [],
        "script_data": {"parsed_action": {}},
    }
    if script_value is not None:
        payload["script_data"]["parsed_action"]["value"] = script_value
    payload.update(extra)
    return payload


def _result(
    *,
    recording_id: str = "rec-1",
    recording_name: str = "HCM_Demo",
    status: str = "passed",
    duration_seconds: float = 125,
    action_log: list[dict] | None = None,
    step_artifacts: list[dict] | None = None,
    resolved_parameter_keys: list[str] | None = None,
    **extra,
) -> dict:
    payload = {
        "recording_id": recording_id,
        "recording_name": recording_name,
        "file_key": f"recordings/{recording_name}/{recording_name}.py",
        "status": status,
        "duration_seconds": duration_seconds,
        "exit_code": 0 if status == "passed" else 1,
        "page_title": "Demo Page",
        "page_url": "https://example.test/demo",
        "stdout": "",
        "stderr": "",
        "error": "" if status == "passed" else "Execution failed",
        "step_artifacts": step_artifacts or [],
        "screenshot_s3_key": "",
        "video_s3_key": "",
        "video_s3_keys": [],
        "resolved_parameter_keys": resolved_parameter_keys or [],
        "action_log": action_log or [],
    }
    payload.update(extra)
    return payload


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


def test_generate_html_report_content_uses_final_aetherion_layout() -> None:
    html = generate_html_report_content(
        test_suite_id="HCM_Final_Suite",
        parent_run_id="run-1",
        results=[
            _result(
                recording_name="HCM_Promote_and_change_position",
                status="failed",
                duration_seconds=257.2,
                page_url="https://example.test/should-not-show",
                action_log=[
                    _action(
                        step=1,
                        action="click_button",
                        label="Continue",
                        status="failed",
                        strategy="direct",
                        duration_ms=900,
                        error="Continue did not advance.",
                    )
                ],
            )
        ],
    )

    assert 'alt="Aetherion"' in html
    assert "Suite Runs" in html
    assert "Execution Trace" in html
    assert "Request Sent to AI" not in html
    assert "Action Timeline" not in html
    assert "AI Repair Attempts" not in html
    assert "Playwright Report" not in html
    assert "Recovery Details" not in html
    assert "Context Sent To The Model" not in html
    assert "Model Response" not in html
    assert "Parsed Repair Plan" not in html
    assert "Additional Captures" not in html
    assert "https://example.test/should-not-show" not in html


def test_generate_html_report_content_is_suite_aware_and_keeps_parameters_per_recording() -> None:
    html = generate_html_report_content(
        test_suite_id="HCM_Suite",
        parent_run_id="run-2",
        results=[
            _result(
                recording_id="create",
                recording_name="HCM_Create_Requisition",
                status="passed",
                duration_seconds=88,
                resolved_parameter_keys=["url", "username", "password", "business_unit"],
                action_log=[
                    _action(step=1, action="goto", label="Oracle", duration_ms=13005),
                    _action(step=2, action="click_link", label="Hiring", duration_ms=9500),
                ],
            ),
            _result(
                recording_id="approve",
                recording_name="HCM_Approve_Job_Requisition",
                status="passed",
                duration_seconds=144.9,
                resolved_parameter_keys=["url", "username", "password", "search_value"],
                action_log=[
                    _action(step=1, action="goto", label="Oracle", duration_ms=12500),
                    _action(step=2, action="click_button", label="Approve", duration_ms=10200),
                ],
            ),
        ],
    )

    assert "Run ID: run-2 · 2 recordings · 4 logged actions" in html
    assert "HCM_Create_Requisition" in html
    assert "HCM_Approve_Job_Requisition" in html
    assert "Resolved parameter keys used for this recording run." in html
    assert html.count("Resolved parameter keys used for this recording run.") == 2
    assert "business_unit" in html
    assert "search_value" in html
    assert '<div class="rail-title">Parameters</div>' not in html


def test_generate_html_report_content_renders_flow_context_inputs_outputs_and_ai_extractors() -> None:
    html = generate_html_report_content(
        test_suite_id="HCM_First_3",
        parent_run_id="run-flow-context",
        results=[
            _result(
                recording_name="HCM_Create_Requisition",
                status="passed",
                duration_seconds=88,
                resolved_parameter_keys=["url", "username", "password"],
                flow_input_status={
                    "search_value": {
                        "name": "search_value",
                        "label": "Search Value",
                        "required": True,
                        "status": "available",
                        "value": "1003",
                        "error": "",
                    }
                },
                flow_output_results=[
                    {
                        "name": "requisition_id",
                        "label": "Requisition Number",
                        "required": True,
                        "status": "extracted",
                        "value": "1003",
                        "source": "ai",
                        "attempts": [
                            {"source": "oracle_table", "status": "miss", "detail": "No captured oracle tables"},
                            {"source": "ai", "status": "matched", "detail": "Found requisition number in confirmation text"},
                        ],
                        "ai_interaction": {
                            "status": "success",
                            "feature": "flow_context_extraction",
                            "model": "gpt-4.1-mini",
                            "system_prompt": "Extract one field",
                            "user_prompt": "Find the requisition number",
                            "response_text": '{"value":"1003","reason":"Found in confirmation text"}',
                            "parsed_response": {
                                "value": "1003",
                                "reason": "Found in confirmation text",
                            },
                            "usage": {"input_tokens": 120, "output_tokens": 32, "total_tokens": 152},
                        },
                    }
                ],
                action_log=[
                    _action(step=1, action="goto", label="Oracle", duration_ms=13005),
                ],
            )
        ],
    )

    assert "Flow Context" in html
    assert "Workbook-defined parent inputs and extracted outputs for this recording run." in html
    assert "Inputs" in html
    assert "Extracted Outputs" in html
    assert "Search Value" in html
    assert "Requisition Number" in html
    assert "Found requisition number in confirmation text" in html
    assert "Request Sent to AI" in html
    assert "Model Output" in html


def test_generate_html_report_content_renders_combined_ai_request_and_model_output() -> None:
    interaction = {
        "feature": "self_repair",
        "helper": "click_text_target",
        "label": "Notifications",
        "status": "success",
        "repair_outcome": "validated",
        "model": "gpt-4.1-mini",
        "endpoint": "https://api.openai.com/v1/responses",
        "system_prompt": "You are a senior Playwright locator repair assistant. Return concise JSON only.",
        "user_prompt": (
            "Find the Notifications control.\n"
            "Recorded script data JSON:\n"
            '{"tracked_action":"click_text","helper_name":"_ptr_click_text_target"}\n'
            "Recorded target context JSON:\n"
            '{"text":"Notifications (7 unread)","tag":"title"}\n'
            "DOM candidates JSON:\n"
            '{"helper":"click_text_target","label":"Notifications","candidates":[{"tag":"a","id":"pt1:_UISatr:0:cil1","title":"Notifications (7 unread)","text":"Notifications (7 unread)"},{"tag":"a","id":"d1::skip","text":"Skip to main content"}]}'
        ),
        "response_text": (
            '{"strategies":[{"kind":"css","selector":"#pt1\\\\:_UISatr\\\\:0\\\\:cil1",'
            '"reason":"Use the stable id selector."},'
            '{"kind":"xpath","selector":"//a[@id=\'pt1:_UISatr:0:cil1\']","reason":"Fallback XPath selector."},'
            '{"kind":"text","text":"Notifications (7 unread)","exact":true,"reason":"Fallback visible text."}]}'
        ),
        "parsed_response": {
            "strategies": [
                {"kind": "css", "selector": r"#pt1\:_UISatr\:0\:cil1", "reason": "Use the stable id selector."},
                {"kind": "xpath", "selector": "//a[@id='pt1:_UISatr:0:cil1']", "reason": "Fallback XPath selector."},
                {"kind": "text", "text": "Notifications (7 unread)", "exact": True, "reason": "Fallback visible text."},
            ]
        },
        "locator_strategies": ["ai_css_1", "ai_xpath_2", "ai_text_3"],
        "validated_locator_strategy": "ai_css_1",
        "last_locator_strategy": "ai_css_1",
        "usage": {"input_tokens": 4949, "output_tokens": 179, "total_tokens": 5128},
    }

    html = generate_html_report_content(
        test_suite_id="HCM_Approve_Job_Requisition",
        parent_run_id="run-ai",
        results=[
            _result(
                recording_name="HCM_Approve_Job_Requisition",
                status="passed",
                duration_seconds=144.9,
                resolved_parameter_keys=["notifications_label", "password", "search_value", "url", "username"],
                action_log=[
                    _action(
                        step=7,
                        action="click_text",
                        label="Notifications",
                        status="success",
                        strategy="ai_css_1",
                        duration_ms=26668,
                        fallback_strategies=["direct", "experience_lookup", "ai_self_repair_lookup", "ai_css_1"],
                        recovery={
                            "handler_name": "ai_locator_repair",
                            "kind": "ai_locator_repair",
                        },
                        ai_interactions=[interaction],
                    )
                ],
            )
        ],
    )

    assert "Execution Path" in html
    assert "AI self-repair details" in html
    assert "Request Sent to AI" in html
    assert "Model Output" in html
    assert "recorded_script_data" in html
    assert "recorded_target_context" in html
    assert "dom_candidates" in html
    assert "Validated" in html
    assert "Suggested" in html
    assert "Elements Sent to AI" not in html
    assert "Failure Sent to AI" not in html
    assert "json-pre" in html


def test_generate_html_report_content_hides_screenshot_object_keys_but_embeds_images(monkeypatch) -> None:
    monkeypatch.setattr(
        report_generator,
        "_to_data_uri",
        lambda key: "data:image/png;base64,AAAA" if key else None,
    )

    html = generate_html_report_content(
        test_suite_id="HCM_Failure",
        parent_run_id="run-img",
        results=[
            _result(
                recording_name="HCM_Promote_and_change_position",
                status="failed",
                duration_seconds=257.2,
                screenshot_s3_key="playwright-test-results/failure.png",
                step_artifacts=[
                    {
                        "index": 1,
                        "action": "date_pick",
                        "screenshot_s3_key": "playwright-test-results/steps/step_001_date_pick.png",
                    }
                ],
                action_log=[
                    _action(
                        step=1,
                        action="date_pick",
                        label="Select Date.",
                        status="failed",
                        strategy="direct",
                        duration_ms=5700,
                        error='Date option "30" did not become ready.',
                        failure_context={
                            "helper": "date_pick",
                            "page_title": "Promote and Change Position",
                            "ready_state": "complete",
                            "busy_indicator_count": 0,
                            "active_element": {
                                "tag": "table",
                                "role": "grid",
                                "text": "1 2 3 4 5 6 7",
                            },
                            "dom_context": {
                                "candidates": [
                                    {"tag": "span", "text": "Select Date."},
                                    {"tag": "a", "text": "April", "title": "April"},
                                ]
                            },
                        },
                    )
                ],
            )
        ],
    )

    assert "Failure Context" in html
    assert "Active Element" in html
    assert "DOM Candidates" in html
    assert "data:image/png;base64,AAAA" in html
    assert "playwright-test-results/failure.png" not in html
    assert "playwright-test-results/steps/step_001_date_pick.png" not in html


def test_generate_html_report_content_formats_duration_cards_with_small_units() -> None:
    html = generate_html_report_content(
        test_suite_id="HCM_Durations",
        parent_run_id="run-dur",
        results=[
            _result(
                recording_name="HCM_Move_To_Posting",
                status="passed",
                duration_seconds=257.2,
                action_log=[_action(step=1, action="click_button", label="Save", duration_ms=10300)],
            )
        ],
    )

    assert 'class="stat-val b duration-value"' in html
    assert html.count('class="dur-unit">m<') >= 2
    assert html.count('class="dur-unit">s<') >= 2


def test_generate_html_report_content_uses_green_status_chip_for_success_actions() -> None:
    html = generate_html_report_content(
        test_suite_id="HCM_Status_Colors",
        parent_run_id="run-status",
        results=[
            _result(
                recording_name="HCM_Approve_Job_Requisition",
                status="passed",
                duration_seconds=144.9,
                action_log=[
                    _action(step=1, action="click_button", label="Approve", status="success", duration_ms=10301),
                    _action(step=2, action="click_button", label="Done", status="failed", duration_ms=3000, error="Done did not appear"),
                ],
            )
        ],
    )

    assert 'status-chip status-passed">success<' in html
    assert 'status-chip status-failed">failed<' in html


def test_generate_html_report_content_masks_password_literals_in_execution_trace_and_script() -> None:
    secret = "Abc&123!"
    html = generate_html_report_content(
        test_suite_id="HCM_Mask_Password",
        parent_run_id="run-mask-password",
        results=[
            _result(
                recording_name="HCM_Login",
                status="passed",
                duration_seconds=15,
                action_log=[
                    _action(
                        step=5,
                        action="fill_textbox",
                        label="Password",
                        status="success",
                        duration_ms=10057,
                        script_value=secret,
                        script_data={
                            "parsed_action": {
                                "value": secret,
                                "name": "Password",
                            },
                            "raw": f'page.get_by_role("textbox", name="Password").fill("{secret}")',
                        },
                    )
                ],
            )
        ],
    )

    assert "Password" in html
    assert "*****" in html
    assert secret not in html
    assert "Abc&amp;123!" not in html
