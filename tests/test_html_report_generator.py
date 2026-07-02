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


def test_generate_html_report_content_omits_suite_level_failure_callout_but_keeps_recording_failure_details() -> None:
    html = generate_html_report_content(
        test_suite_id="HCM_Failure_Callout",
        parent_run_id="run-callout",
        results=[
            _result(
                recording_name="HCM_Promote_and_change_position",
                status="failed",
                duration_seconds=257.2,
                ai_failure_summary={
                    "headline": "Unable to open control",
                    "summary": "Oracle control did not become actionable.",
                    "next_action": "Inspect deterministic handler.",
                    "failure_category": "Failure",
                },
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

    assert 'class="failure-card suite-callout"' not in html
    assert 'class="failure-card recording-callout"' in html
    assert "Inspect deterministic handler." in html


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
    assert html.count('<div class="trace-title">Parameters</div>') == 2
    assert "business_unit" in html
    assert "search_value" in html
    assert '<div class="rail-title">Parameters</div>' not in html


def test_generate_html_report_content_renders_script_step_extracted_outputs() -> None:
    html = generate_html_report_content(
        test_suite_id="PO_Script_Output",
        parent_run_id="run-script-output",
        results=[
            _result(
                recording_name="create_po",
                status="passed",
                duration_seconds=3,
                execution_mode="script_step",
                extracted_outputs={"order_number": "PO-1009"},
            )
        ],
    )

    assert "Extracted Outputs" in html
    assert "order_number" in html
    assert "PO-1009" in html
    assert "script step" in html


def test_generate_html_report_content_renders_combined_ai_request_and_model_output() -> None:
    interaction = {
        "feature": "self_repair",
        "helper": "click_text_target",
        "label": "Notifications",
        "status": "success",
        "repair_outcome": "validated",
        "model": "gpt-5.4-mini",
        "endpoint": "https://api.openai.com/v1/responses",
        "system_prompt": "You are a senior Playwright locator repair assistant. Return concise JSON only.",
        "user_prompt": (
            "Find the Notifications control.\n"
            "Requested action value JSON:\n"
            '{"type":"raw","value":"Notifications"}\n'
            "Recorded script data JSON:\n"
            '{"tracked_action":"click_text","helper_name":"_act_click_text_target"}\n'
            "Recorded target context JSON:\n"
            '{"text":"Notifications (7 unread)","tag":"title"}\n'
            "Relevant DOM candidates JSON:\n"
            '{"helper":"click_text_target","label":"Notifications","candidates":[{"tag":"a","id":"pt1:_UISatr:0:cil1","title":"Notifications (7 unread)","text":"Notifications (7 unread)"},{"tag":"a","id":"d1::skip","text":"Skip to main content"}]}'
            "\nRetry feedback JSON:\n"
            '{"round":1,"execution_error":"Timed out waiting for locator"}'
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
        "page_screenshot": {
            "status": "captured",
            "media_type": "image/jpeg",
            "format": "jpeg",
            "full_page": True,
            "scale": "css",
            "quality": 45,
            "image_url": "data:image/jpeg;base64,BBBB",
        },
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
    assert "Raw Prompt" in html
    assert "requested_action_value" in html
    assert "recorded_script_data" in html
    assert "recorded_target_context" in html
    assert "dom_candidates" in html
    assert "retry_feedback" in html
    assert "page_screenshot" in html
    assert "Screenshot Sent to AI" in html
    assert 'src="data:image/jpeg;base64,BBBB"' in html
    assert "Validated" in html
    assert "Suggested" in html
    assert "Elements Sent to AI" not in html
    assert "Failure Sent to AI" not in html
    assert "json-pre" in html


def test_generate_html_report_content_renders_ai_request_errors_readably() -> None:
    interaction = {
        "feature": "self_repair",
        "helper": "select_search_trigger_option",
        "label": "Search: Transaction Type",
        "status": "request_error",
        "model": "gpt-5.4-mini",
        "endpoint": "https://api.openai.com/v1/responses",
        "user_prompt": (
            "Repair the Oracle selector.\n"
            "Recorded script data JSON:\n"
            '{"tracked_action":"search_and_select"}\n'
            "Relevant DOM candidates JSON:\n"
            '{"helper":"select_search_trigger_option","label":"Search: Transaction Type","candidates":[{"tag":"a","text":"Search..."}]}'
        ),
        "last_error": "Locator.wait_for: Timeout 30000ms exceeded.",
        "error_type": "HTTPError",
        "error": "HTTP Error 404: Not Found",
        "http_status": 404,
        "error_response_body": '{"error":{"message":"Unknown endpoint","type":"invalid_request_error"}}',
    }

    html = generate_html_report_content(
        test_suite_id="AR_Credit_Memo",
        parent_run_id="run-ai-error",
        results=[
            _result(
                recording_name="AR_Credit_Memo",
                status="failed",
                action_log=[
                    _action(
                        step=15,
                        action="search_and_select",
                        label="Search: Transaction Type",
                        status="failed",
                        strategy="ai_self_repair_lookup",
                        fallback_strategies=["direct", "experience_lookup", "ai_self_repair_lookup"],
                        ai_interactions=[interaction],
                    )
                ],
            )
        ],
    )

    assert "AI self-repair details" in html
    assert "Request Sent to AI" in html
    assert "Request Error" in html
    assert "http_status" in html
    assert "404" in html
    assert "invalid_request_error" in html
    assert "Unknown endpoint" in html
    assert "Raw Prompt" in html


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


def test_generate_html_report_content_renders_debug_settings_and_step_debug_trace() -> None:
    html = generate_html_report_content(
        test_suite_id="AR_Prepayment_Application",
        parent_run_id="run-debug",
        results=[
            _result(
                recording_name="AR_Prepayment_Application",
                debug_settings={
                    "after_action_wait_ms": 2000,
                    "capture_steps": True,
                    "record_video": False,
                    "step_screenshot_full_page": True,
                    "page_text_snapshot_max_chars": 24000,
                    "debug_trace": True,
                },
                action_log=[
                    _action(
                        step=39,
                        action="adf_menu_select",
                        label="Complete and Create Another",
                        debug={
                            "oracle_completion_check": {
                                "matched_signal": "heading_changed_to_review",
                                "postcondition_passed": True,
                            }
                        },
                    )
                ],
            )
        ],
    )

    assert "Debug Settings" in html
    assert "Debug Trace" in html
    assert "oracle_completion_check" in html
    assert "heading_changed_to_review" in html
    assert "page_text_snapshot_max_chars" in html


def test_generate_html_report_content_compacts_long_dom_candidates() -> None:
    html = generate_html_report_content(
        test_suite_id="PO_Invoice",
        parent_run_id="run-dom-candidates",
        results=[
            _result(
                recording_name="PO_Invoice",
                status="failed",
                action_log=[
                    _action(
                        step=35,
                        action="adf_menu_select",
                        label="Invoice Actions",
                        status="failed",
                        failure_context={
                            "helper": "adf_menu_select",
                            "page_title": "Create Invoice",
                            "ready_state": "complete",
                            "busy_indicator_count": 0,
                            "dom_context": {
                                "candidates": [
                                    {
                                        "tag": "a",
                                        "role": "menuitem",
                                        "text": "Invoice Actions",
                                    },
                                    {
                                        "tag": "select",
                                        "title": "USD - US Dollar",
                                        "text": "USD - US Dollar EUR - Euro GBP - Pound Sterling JPY - Yen " * 10,
                                    },
                                ]
                            },
                        },
                    )
                ],
            )
        ],
    )

    assert "DOM Candidates (2)" in html
    assert "Show full" in html
    assert 'title="USD - US Dollar EUR - Euro GBP - Pound Sterling' in html
    assert "Invoice Actions" in html


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


def test_generate_html_report_content_renders_executed_script_from_artifact_key(monkeypatch) -> None:
    script_key = "playwright-test-results/demo-suite/run-1/demo-recording/executed_script.py"

    def _fake_load_bytes(object_key: str | None) -> bytes | None:
        if object_key == script_key:
            return b"print('hello from executed script')\n"
        return None

    monkeypatch.setattr(report_generator, "_load_bytes", _fake_load_bytes)
    report_generator._load_text_object.cache_clear()

    html = generate_html_report_content(
        test_suite_id="HCM_Executed_Script",
        parent_run_id="run-script",
        results=[
            _result(
                recording_name="HCM_Login",
                status="passed",
                duration_seconds=15,
                executed_script_s3_key=script_key,
            )
        ],
    )

    assert "Executed Script" in html
    # Executed Script is a developer-only block: it carries the `dev-only` class so the
    # report's End User view hides it (the Developer view tab reveals it).
    assert 'class="recording-script-block executed-script-details dev-only"' in html
    assert "print(&#x27;hello from executed script&#x27;)" in html

    report_generator._load_text_object.cache_clear()


def test_report_view_tabs_default_to_end_user_and_gate_dev_only_blocks() -> None:
    """The report ships an End User / Developer view toggle. End User is the default;
    developer-only diagnostics carry the `dev-only` class and are hidden via CSS in that
    view, while their data stays embedded so the Developer tab can reveal it."""
    html = generate_html_report_content(
        test_suite_id="O2C_SO_Create",
        parent_run_id="run-view",
        results=[
            _result(
                recording_name="O2C_SO_Create",
                status="passed",
                extracted_outputs={"sales_order_number": "1234"},
                debug_settings={"after_action_wait_ms": 0, "debug_trace": False},
                action_log=[
                    _action(
                        step=1,
                        action="select_search_trigger_option",
                        label="Search: Warehouse",
                        status="success",
                        debug={"select_search_trigger_option": {"status": "success"}},
                        script_data={
                            "raw": 'ai_extract("sales_order_number", "...")',
                            "parsed_action": {"value": "CN_SJ"},
                        },
                        ai_interactions=[
                            {
                                "model": "gpt",
                                "status": "validated",
                                "user_prompt": "x",
                                "response_text": '{"value": 1}',
                            }
                        ],
                    )
                ],
            )
        ],
    )

    # Default view is End User, and the toggle + gating CSS/JS are present.
    assert '<body class="view-user">' in html
    assert 'class="view-tabs"' in html
    assert "setView('user')" in html and "setView('dev')" in html
    assert "function setView(mode)" in html
    assert "body.view-user .dev-only{display:none!important}" in html

    # Developer-only diagnostics are tagged dev-only (hidden in End User view)...
    assert "detail-card debug-card dev-only" in html  # Debug Settings + Debug Trace
    assert "path-ai-inline dev-only" in html  # AI self-repair internals
    # ...but the data is still embedded so the Developer view can show it.
    assert "Debug Trace" in html
    # The per-step recorded script is NOT dev-only: visible in the End User view too.
    assert '<div class="detail-card"><div class="dc-title">Recorded Script</div>' in html

    # End-user content (Extracted Outputs) is NOT gated behind dev-only.
    assert "Extracted Outputs" in html
    assert 'class="ctx-section"' in html


def test_report_header_is_consolidated_and_sidebar_stats_not_duplicated() -> None:
    """Suite name shows once (in the nav header, humanized), the duplicate hero title is
    gone, and the sidebar no longer repeats the suite-level stat rows (kept in main content)."""
    html = generate_html_report_content(
        test_suite_id="O2C_SO_Create_YeuTest",
        parent_run_id="run-1",
        results=[_result(recording_name="O2C_SO_Create_YeuTest", status="passed")],
    )
    # Suite name in the nav header (humanized), not duplicated as a big hero title.
    assert '<span class="nav-run-name">O2C SO Create YeuTest</span>' in html
    assert 'class="hero-title"' not in html
    # Sidebar no longer repeats the suite stats (Status/Recordings/Passed/... rc-rows).
    assert ">Run Details<" not in html
    assert 'class="rc-row"' not in html
    # The Runs navigation stays in the sidebar.
    assert '<div class="rail-title">Runs</div>' in html


def test_recording_cards_get_status_shadow_and_passed_has_no_banner() -> None:
    """Suite-run cards get a small green/red status shadow (no banner, no top border).
    Passed runs render no banner; failed runs keep the red detail callout."""
    passed = generate_html_report_content(
        test_suite_id="Pass_Suite",
        parent_run_id="run-pass",
        results=[
            _result(
                recording_name="Pass_Rec",
                status="passed",
                action_log=[
                    _action(step=1, action="click_button", label="Submit", status="success")
                ],
            )
        ],
    )
    # Passed card carries the green status-shadow hook; no banner is rendered.
    assert 'data-failed="false"' in passed
    assert '.recording-item[data-failed="false"]{box-shadow:0 6px 18px rgba(14,159,110,.12)}' in passed
    assert "success-card" not in passed  # the old green banner is gone entirely
    assert "failure-card recording-callout" not in passed  # passed shows no banner

    failed = generate_html_report_content(
        test_suite_id="Fail_Suite",
        parent_run_id="run-fail",
        results=[
            _result(
                recording_name="Fail_Rec",
                status="failed",
                error="boom",
                action_log=[
                    _action(
                        step=1, action="click_button", label="Submit", status="failed", error="boom"
                    )
                ],
            )
        ],
    )
    assert 'data-failed="true"' in failed
    assert '.recording-item[data-failed="true"]{box-shadow:0 6px 18px rgba(224,60,75,.14)}' in failed
    assert "failure-card recording-callout" in failed  # failed keeps the red detail banner


def test_generate_html_report_content_marks_raw_script_fallback_runs(monkeypatch) -> None:
    script_key = "playwright-test-results/demo-suite/run-raw/demo-recording/executed_script.py"

    def _fake_load_bytes(object_key: str | None) -> bytes | None:
        if object_key == script_key:
            return b"page.locator('.xen').first.click()\n"
        return None

    monkeypatch.setattr(report_generator, "_load_bytes", _fake_load_bytes)
    report_generator._load_text_object.cache_clear()

    html = generate_html_report_content(
        test_suite_id="HCM_Raw_Fallback",
        parent_run_id="run-raw-fallback",
        results=[
            _result(
                recording_name="PO_Process_Requisitions",
                status="failed",
                duration_seconds=5,
                executed_script_s3_key=script_key,
                execution_mode="raw_script_fallback",
                preparation_warning="AST generator found actions outside resilient helper coverage.",
            )
        ],
    )

    assert "Execution Mode" in html
    assert "Raw Script Fallback" in html
    assert "Preparation Fallback" in html
    assert "Substituted raw recording executed after AST coverage fallback." in html

    report_generator._load_text_object.cache_clear()


def test_generate_html_report_content_marks_script_step_runs(monkeypatch) -> None:
    script_key = "playwright-test-results/demo-suite/run-script-step/demo-recording/executed_script.py"

    def _fake_load_bytes(object_key: str | None) -> bytes | None:
        if object_key == script_key:
            return b"extract_name = 'order_number'\nextract_value = 'PO-1009'\n"
        return None

    monkeypatch.setattr(report_generator, "_load_bytes", _fake_load_bytes)
    report_generator._load_text_object.cache_clear()

    html = generate_html_report_content(
        test_suite_id="PO_Script_Step",
        parent_run_id="run-script-step",
        results=[
            _result(
                recording_name="create_po",
                status="passed",
                duration_seconds=3,
                executed_script_s3_key=script_key,
                execution_mode="script_step",
            )
        ],
    )

    assert "Execution Mode" in html
    assert "Script Step" in html
    assert "Plain Python script step executed with runner parameter context." in html

    report_generator._load_text_object.cache_clear()


def test_generate_html_report_content_counts_raw_inline_action_as_fallback() -> None:
    html = generate_html_report_content(
        test_suite_id="HCM_Raw_Inline",
        parent_run_id="run-raw-inline",
        results=[
            _result(
                recording_name="PO_Process_Requisitions",
                status="failed",
                duration_seconds=12,
                action_log=[
                    _action(
                        step=1,
                        action="click",
                        label=".xen",
                        status="failed",
                        strategy="raw_inline",
                        error="Locator.click: Timeout 30000ms exceeded.",
                        script_data={"raw": 'page.locator(".xen").first.click()', "parsed_action": {}},
                    )
                ],
            )
        ],
    )

    assert "Raw Inline" in html
    assert '<span class="spill spill-fb">Fallback</span>' in html
    assert '<span class="label">Fallback Steps</span><span class="value ">1</span>' in html


def test_generate_html_report_content_masks_password_literals_in_execution_trace_and_script(monkeypatch) -> None:
    secret = "Abc&123!"
    script_key = "playwright-test-results/demo-suite/run-mask/HCM_Login/executed_script.py"

    def _fake_load_bytes(object_key: str | None) -> bytes | None:
        if object_key == script_key:
            return (
                "page.get_by_role(\"textbox\", name=\"Password\").fill(\"Abc&123!\")\n".encode("utf-8")
            )
        return None

    monkeypatch.setattr(report_generator, "_load_bytes", _fake_load_bytes)
    report_generator._load_text_object.cache_clear()

    html = generate_html_report_content(
        test_suite_id="HCM_Mask_Password",
        parent_run_id="run-mask-password",
        results=[
            _result(
                recording_name="HCM_Login",
                status="passed",
                duration_seconds=15,
                executed_script_s3_key=script_key,
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

    report_generator._load_text_object.cache_clear()


def test_execution_path_shows_control_type_and_failure_context_is_dev_only() -> None:
    """Control type is surfaced visibly in the Execution Path stats (not only in the debug
    JSON), and the Failure Context detail is developer-only."""
    html = generate_html_report_content(
        test_suite_id="Ctl_Suite",
        parent_run_id="run-ctl",
        results=[
            _result(
                recording_name="Ctl_Rec",
                status="failed",
                error="boom",
                action_log=[
                    _action(
                        step=1,
                        action="select_search_trigger_option",
                        label="Search: Warehouse",
                        status="failed",
                        error="boom",
                        debug={
                            "select_search_trigger_option": {
                                "active_element": {
                                    "tag": "input",
                                    "control_type": "oj-c-select-single (Redwood Core Pack)",
                                }
                            }
                        },
                        failure_context={"active_element": {"tag": "input", "role": "combobox"}},
                    )
                ],
            )
        ],
    )
    assert '<span class="kk">Control</span>' in html
    assert "oj-c-select-single (Redwood Core Pack)" in html
    assert "failure-context-card dev-only" in html


def test_low_value_subtitles_are_removed() -> None:
    """The two no-value subtitles are gone; the section titles themselves remain."""
    html = generate_html_report_content(
        test_suite_id="Sub_Suite",
        parent_run_id="run-sub",
        results=[
            _result(
                recording_name="Sub_Rec",
                status="passed",
                resolved_parameter_keys=["username"],
                extracted_outputs={"order": "1"},
                action_log=[_action(step=1, action="click_button", label="Go", status="success")],
            )
        ],
    )
    assert "Resolved parameter keys used for this recording run." not in html
    assert "Values captured via ai_extract() / api_helpers.extract()" not in html
    assert ">Parameters</div>" in html
    assert ">Extracted Outputs</div>" in html
