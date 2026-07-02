from __future__ import annotations

from pathlib import Path

from scripts.run_and_download_report import (
    _build_agent_command,
    _default_output_path,
    _extract_agent_result,
    _find_report_key,
)


def test_extract_agent_result_skips_env_banner() -> None:
    stdout = """✔ Loaded environment variables from .env
[
  {
    "type": "s3_download_link",
    "file_key": "playwright-test-results/suite/run-123/report.html"
  }
]
"""

    result = _extract_agent_result(stdout)

    assert isinstance(result, list)
    assert result[0]["file_key"] == "playwright-test-results/suite/run-123/report.html"


def test_find_report_key_returns_download_link_file_key() -> None:
    result = [
        {
            "type": "summary",
            "passed": 1,
            "failed": 0,
        },
        {
            "type": "s3_download_link",
            "file_key": "playwright-test-results/suite/run-123/report.html",
        },
    ]

    assert _find_report_key(result) == "playwright-test-results/suite/run-123/report.html"


def test_default_output_path_uses_wrapped_suite_payload() -> None:
    payload = {
        "0": {
            "test_suite_id": "60617d26-7e84-423d-b691-f5bded043c24",
        }
    }

    output = _default_output_path(
        Path("/tmp/test_runner"),
        payload,
        "playwright-test-results/60617d26-7e84-423d-b691-f5bded043c24/run-123/report.html",
    )

    assert output == Path("/tmp/test_runner/downloads/60617d26-7e84-423d-b691-f5bded043c24-run-123-report.html")


def test_build_agent_command_uses_wait_and_optional_id() -> None:
    command = _build_agent_command(
        aetherion_bin="./.venv/bin/aetherion",
        agent_name="ACT Agent",
        payload={"test_suite_id": "suite"},
        run_id="run-123",
        task_queue=None,
    )

    assert command == [
        "./.venv/bin/aetherion",
        "agent",
        "ACT Agent",
        '{"test_suite_id": "suite"}',
        "--wait",
        "--id",
        "run-123",
    ]
