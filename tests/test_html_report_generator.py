from src.utils.html_report_generator import (
    _format_duration_minutes,
    generate_html_report_content,
)


def test_format_duration_minutes_uses_minute_units() -> None:
    assert _format_duration_minutes(30) == "0.5 mins"
    assert _format_duration_minutes(60) == "1 min"
    assert _format_duration_minutes(125) == "2.1 mins"


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
