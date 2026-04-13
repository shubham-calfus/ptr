from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from typing import Any

from aetherion_sdk import agent, agentExecutor, toolExecutor
from common_lib.utils.logger import setup_logger
from temporalio import workflow
from temporalio.common import RetryPolicy

logger = setup_logger(__name__)


def _safe_segment(value: Any) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return cleaned.strip("._") or "unknown"


def _child_workflow_id(recording: dict[str, Any], parent_run_id: str, index: int) -> str:
    # Ensure uniqueness even when client sends duplicate recording IDs.
    recording_id = _safe_segment(recording.get("id") or f"idx-{index}")
    recording_file = _safe_segment(recording.get("file") or recording.get("name") or f"recording-{index}")
    return f"ptr-child-{recording_id}-{recording_file}-{index}-{parent_run_id}"


def _extract_trigger_payload(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]], str]:
    if isinstance(payload.get("0"), dict):
        trigger_data = payload["0"]
    elif isinstance(payload.get("triggers"), list) and payload["triggers"]:
        trigger_data = payload["triggers"][0]
    elif isinstance(payload.get("triggers"), dict):
        trigger_data = payload["triggers"]
    else:
        trigger_data = payload

    test_suite_id = str(trigger_data.get("test_suite_id", "")).strip()
    recordings = trigger_data.get("recordings") or []
    execution_mode = str(trigger_data.get("execution_mode", "parallel") or "parallel").lower()
    if execution_mode not in {"parallel", "sequential"}:
        execution_mode = "parallel"
    return test_suite_id, recordings, execution_mode


async def _expand_recordings_for_parameter_rows(
    recordings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    try:
        expanded_recordings = await toolExecutor.execute(
            "expand_recordings_for_parameter_rows",
            recordings,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
    except Exception as exc:  # pragma: no cover - runtime path
        logger.warning("Failed to expand recordings for parameter rows: %s", exc)
        return recordings

    if not isinstance(expanded_recordings, list) or not expanded_recordings:
        return recordings
    return expanded_recordings


@agent(name="PlaywrightTestRunnerChild")
async def PlaywrightTestRunnerChild(payload: dict[str, Any]) -> dict[str, Any]:
    recording = payload.get("recording") or {}
    test_suite_id = str(payload.get("test_suite_id", "")).strip()
    parent_run_id = str(payload.get("parent_run_id", "")).strip()

    if not recording.get("file"):
        raise ValueError("Child workflow requires recording.file")

    result = await toolExecutor.execute(
        "execute_recording_script",
        recording,
        test_suite_id,
        parent_run_id,
        start_to_close_timeout=timedelta(minutes=15),
        retry_policy=RetryPolicy(maximum_attempts=1),
    )
    return result


@agent(name="playwright_test_runner")
async def PlaywrightTestRunnerAgent(payload: dict[str, Any]) -> list[dict[str, Any]]:
    logger.info("Starting PlaywrightTestRunnerAgent with payload: %s", payload)

    test_suite_id, recordings, execution_mode = _extract_trigger_payload(payload)

    if not test_suite_id:
        return [{"type": "error", "message": "test_suite_id is required."}]

    if not isinstance(recordings, list) or not recordings:
        return [{"type": "error", "message": "At least one recording is required."}]

    candidate_recordings = [recording for recording in recordings if recording.get("file")]
    ordered_recordings = await _expand_recordings_for_parameter_rows(candidate_recordings)
    if not ordered_recordings:
        return [{"type": "error", "message": "No valid recording files were provided."}]

    logger.info(
        "Executing %s recording(s) for suite %s in %s mode",
        len(ordered_recordings),
        test_suite_id,
        execution_mode,
    )

    parent_run_id = workflow.info().run_id

    results: list[dict[str, Any] | Exception] = []
    if execution_mode == "sequential":
        for idx, recording in enumerate(ordered_recordings):
            child_payload = {
                "recording": recording,
                "test_suite_id": test_suite_id,
                "parent_run_id": parent_run_id,
            }
            try:
                result = await agentExecutor.execute(
                    "PlaywrightTestRunnerChild",
                    child_payload,
                    workflow_id=_child_workflow_id(recording, parent_run_id, idx),
                    task_queue=workflow.info().task_queue,
                )
                results.append(result)
            except Exception as exc:  # pragma: no cover - runtime path
                logger.error("Child workflow failed for recording %s: %s", recording, exc)
                results.append(exc)
    else:
        child_tasks = [
            agentExecutor.execute(
                "PlaywrightTestRunnerChild",
                {
                    "recording": recording,
                    "test_suite_id": test_suite_id,
                    "parent_run_id": parent_run_id,
                },
                workflow_id=_child_workflow_id(recording, parent_run_id, idx),
                task_queue=workflow.info().task_queue,
            )
            for idx, recording in enumerate(ordered_recordings)
        ]
        results = await asyncio.gather(*child_tasks, return_exceptions=True)

    manifest_keys: dict[str, str] = {}
    ordered_names: list[str] = []
    passed = 0
    failed = 0

    for recording, result in zip(ordered_recordings, results, strict=False):
        display_name = str(recording.get("name") or recording.get("file") or "unknown")
        ordered_names.append(display_name)
        if isinstance(result, Exception):
            failed += 1
            manifest_keys[display_name] = ""
            continue

        if result.get("status") == "passed":
            passed += 1
        else:
            failed += 1
        manifest_keys[display_name] = result.get("result_s3_key", "")

    report_s3_key = await toolExecutor.execute(
        "generate_html_report",
        test_suite_id,
        parent_run_id,
        manifest_keys,
        ordered_names,
        start_to_close_timeout=timedelta(minutes=5),
        retry_policy=RetryPolicy(maximum_attempts=1),
    )

    return [
        {
            "type": "s3_download_link",
            "title": "Playwright Test Suite Report",
            "file_key": report_s3_key,
            "label": "Download HTML Report",
            "extension": "html",
        },
        {
            "type": "summary",
            "test_suite_id": test_suite_id,
            "total": len(ordered_recordings),
            "passed": passed,
            "failed": failed,
            "execution_mode": execution_mode,
        },
    ]
