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


def _normalize_suite_context(values: dict[str, Any] | None) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_key, raw_value in (values or {}).items():
        key = _safe_segment(raw_key)
        value = str(raw_value or "").strip()
        if not key or not value:
            continue
        normalized[key] = value
    return normalized


def _merge_suite_context_into_recording(recording: dict[str, Any], suite_context: dict[str, str]) -> dict[str, Any]:
    merged = dict(recording)
    merged_parameters = dict(_normalize_suite_context(suite_context))
    if isinstance(recording.get("parameters"), dict):
        merged_parameters.update(recording.get("parameters") or {})
    if merged_parameters:
        merged["parameters"] = merged_parameters
    return merged


def _merge_recording_outputs_into_suite_context(
    suite_context: dict[str, str],
    recording: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, str]:
    updated = dict(suite_context)
    normalized_outputs = _normalize_suite_context(result.get("extracted_outputs"))
    if not normalized_outputs:
        return updated

    namespace = _safe_segment(recording.get("id") or recording.get("name") or recording.get("file") or "recording")
    for key, value in normalized_outputs.items():
        updated[f"{namespace}_{key}"] = value
        existing = updated.get(key)
        if existing in (None, "", value):
            updated[key] = value
        else:
            # Keep the bare key deterministic only when it is unambiguous.
            updated.pop(key, None)
    return updated


def _build_suite_context_from_previous_results(
    recordings: list[dict[str, Any]],
    previous_results: list[dict[str, Any]],
) -> dict[str, str]:
    suite_context: dict[str, str] = {}
    for recording, result in zip(recordings, previous_results, strict=False):
        suite_context = _merge_recording_outputs_into_suite_context(
            suite_context,
            recording,
            result,
        )
    return suite_context


def _recording_passed(result: dict[str, Any]) -> bool:
    return str(result.get("status") or "").strip().lower() == "passed"


def _blocked_dependency_reason(
    failed_recording: dict[str, Any],
    failed_result: dict[str, Any] | Exception,
) -> str:
    failed_name = str(
        failed_recording.get("name") or failed_recording.get("id") or failed_recording.get("file") or "upstream recording"
    ).strip()
    if isinstance(failed_result, Exception):
        detail = str(failed_result).strip() or "Child workflow raised before producing a manifest."
    else:
        detail = str(failed_result.get("error") or failed_result.get("stderr") or "").strip()

    reason = (
        f'Recording was not executed because upstream recording "{failed_name}" failed '
        "and did not produce the suite outputs required by downstream recordings."
    )
    if detail:
        return f"{reason} Upstream failure: {detail}"
    return reason


def _extract_trigger_payload(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]], str, str]:
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
    resume_from_run_id = str(trigger_data.get("resume_from_run_id", "") or "").strip()
    return test_suite_id, recordings, execution_mode, resume_from_run_id


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

    test_suite_id, recordings, execution_mode, resume_from_run_id = _extract_trigger_payload(payload)

    if not test_suite_id:
        return [{"type": "error", "message": "test_suite_id is required."}]

    if not isinstance(recordings, list) or not recordings:
        return [{"type": "error", "message": "At least one recording is required."}]

    candidate_recordings = [recording for recording in recordings if recording.get("file")]
    ordered_recordings = await _expand_recordings_for_parameter_rows(candidate_recordings)
    if not ordered_recordings:
        return [{"type": "error", "message": "No valid recording files were provided."}]

    if resume_from_run_id and execution_mode != "sequential":
        return [{"type": "error", "message": "resume_from_run_id is supported only in sequential mode."}]

    logger.info(
        "Executing %s recording(s) for suite %s in %s mode",
        len(ordered_recordings),
        test_suite_id,
        execution_mode,
    )

    parent_run_id = workflow.info().run_id

    results: list[dict[str, Any] | Exception] = []
    if execution_mode == "sequential":
        suite_context: dict[str, str] = {}
        resume_offset = 0
        if resume_from_run_id:
            try:
                resume_state = await toolExecutor.execute(
                    "load_resume_state_from_run",
                    test_suite_id,
                    resume_from_run_id,
                    ordered_recordings,
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
            except Exception as exc:
                logger.error("Failed to load resume state for run %s: %s", resume_from_run_id, exc)
                return [{"type": "error", "message": f"Failed to load resume state for run {resume_from_run_id}: {exc}"}]

            resume_offset = int(resume_state.get("resume_start_index") or 0)
            previous_results = resume_state.get("previous_results") or []
            suite_context = _build_suite_context_from_previous_results(
                ordered_recordings[:resume_offset],
                previous_results,
            )
            ordered_recordings = ordered_recordings[resume_offset:]
            logger.info(
                "Resuming suite %s from prior run %s at recording index %s (%s)",
                test_suite_id,
                resume_from_run_id,
                resume_offset,
                resume_state.get("failed_recording_name") or "unknown",
            )

        for idx, recording in enumerate(ordered_recordings, start=resume_offset):
            child_recording = _merge_suite_context_into_recording(recording, suite_context)
            child_payload = {
                "recording": child_recording,
                "test_suite_id": test_suite_id,
                "parent_run_id": parent_run_id,
            }
            try:
                result = await agentExecutor.execute(
                    "PlaywrightTestRunnerChild",
                    child_payload,
                    workflow_id=_child_workflow_id(child_recording, parent_run_id, idx),
                    task_queue=workflow.info().task_queue,
                )
                results.append(result)
                if isinstance(result, dict) and _recording_passed(result):
                    suite_context = _merge_recording_outputs_into_suite_context(
                        suite_context,
                        child_recording,
                        result,
                    )
                    continue

                if not isinstance(result, dict):
                    continue

                block_reason = _blocked_dependency_reason(child_recording, result)
                logger.warning(
                    "Stopping sequential suite %s after recording %s returned status=%s",
                    test_suite_id,
                    child_recording.get("name") or child_recording.get("file") or child_recording.get("id"),
                    result.get("status"),
                )
                remaining_recordings = ordered_recordings[(idx - resume_offset + 1) :]
                for blocked_recording in remaining_recordings:
                    blocked_result = await toolExecutor.execute(
                        "record_blocked_recording",
                        test_suite_id,
                        parent_run_id,
                        blocked_recording,
                        block_reason,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=RetryPolicy(maximum_attempts=1),
                    )
                    results.append(blocked_result)
                break
            except Exception as exc:  # pragma: no cover - runtime path
                logger.error("Child workflow failed for recording %s: %s", recording, exc)
                results.append(exc)
                block_reason = _blocked_dependency_reason(child_recording, exc)
                remaining_recordings = ordered_recordings[(idx - resume_offset + 1) :]
                for blocked_recording in remaining_recordings:
                    try:
                        blocked_result = await toolExecutor.execute(
                            "record_blocked_recording",
                            test_suite_id,
                            parent_run_id,
                            blocked_recording,
                            block_reason,
                            start_to_close_timeout=timedelta(minutes=2),
                            retry_policy=RetryPolicy(maximum_attempts=1),
                        )
                    except Exception as blocked_exc:
                        logger.error(
                            "Failed to persist blocked manifest for recording %s after upstream failure: %s",
                            blocked_recording,
                            blocked_exc,
                        )
                        blocked_result = {
                            "recording_name": str(
                                blocked_recording.get("name") or blocked_recording.get("file") or "unknown"
                            ),
                            "status": "failed",
                            "error": block_reason,
                            "result_s3_key": "",
                        }
                    results.append(blocked_result)
                break
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
