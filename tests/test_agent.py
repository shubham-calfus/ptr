from types import SimpleNamespace

import pytest

from src.agent.agent import (
    PlaywrightTestRunnerAgent,
    _build_suite_context_from_previous_results,
    _blocked_dependency_reason,
    _expand_recordings_for_parameter_rows,
    _merge_recording_outputs_into_suite_context,
    _merge_suite_context_into_recording,
)


@pytest.mark.asyncio
async def test_expand_recordings_for_parameter_rows_uses_expansion_tool(monkeypatch) -> None:
    calls = []

    async def _fake_tool_execute(tool_name, recordings, **kwargs):
        calls.append((tool_name, recordings, kwargs))
        return [
            {
                "id": "rec-1-row-2",
                "name": "fake_2 [row 2]",
                "file": "recordings/demo.py",
                "parameters": {
                    "username": "svc",
                },
                "parameter_row_index": 2,
            },
            {
                "id": "rec-1-row-3",
                "name": "fake_2 [row 3]",
                "file": "recordings/demo.py",
                "parameters": {
                    "username": "svc",
                },
                "parameter_row_index": 3,
            },
        ]

    monkeypatch.setattr("src.agent.agent.toolExecutor.execute", _fake_tool_execute)

    expanded = await _expand_recordings_for_parameter_rows(
        [
            {
                "id": "rec-1",
                "name": "fake_2",
                "file": "recordings/demo.py",
                "parameters": {
                    "entered_amount": "55",
                },
            }
        ]
    )

    assert len(calls) == 1
    assert calls[0][0] == "expand_recordings_for_parameter_rows"
    assert len(expanded) == 2
    assert expanded[0]["id"] == "rec-1-row-2"
    assert expanded[0]["name"] == "fake_2 [row 2]"
    assert expanded[0]["parameter_row_index"] == 2

    assert expanded[1]["id"] == "rec-1-row-3"
    assert expanded[1]["name"] == "fake_2 [row 3]"
    assert expanded[1]["parameter_row_index"] == 3


def test_merge_suite_context_into_recording_keeps_context_available_for_downstream_placeholders() -> None:
    merged = _merge_suite_context_into_recording(
        {
            "id": "rec-2",
            "file": "recordings/approve.py",
            "parameters": {
                "search_value": "{{requisition_id}}",
            },
        },
        {
            "requisition_id": "REQ-10025",
        },
    )

    assert merged["parameters"]["requisition_id"] == "REQ-10025"
    assert merged["parameters"]["search_value"] == "{{requisition_id}}"


def test_merge_recording_outputs_into_suite_context_keeps_bare_and_namespaced_keys() -> None:
    merged = _merge_recording_outputs_into_suite_context(
        {},
        {"id": "HCM_Create_Requisition"},
        {"extracted_outputs": {"requisition_id": "1003", "requisition_title": "Analyst"}},
    )

    assert merged["requisition_id"] == "1003"
    assert merged["requisition_title"] == "Analyst"
    assert merged["HCM_Create_Requisition_requisition_id"] == "1003"
    assert merged["HCM_Create_Requisition_requisition_title"] == "Analyst"


def test_merge_recording_outputs_into_suite_context_removes_ambiguous_bare_key_on_collision() -> None:
    suite_context = _merge_recording_outputs_into_suite_context(
        {},
        {"id": "create_req"},
        {"extracted_outputs": {"requisition_id": "1003"}},
    )

    merged = _merge_recording_outputs_into_suite_context(
        suite_context,
        {"id": "create_offer"},
        {"extracted_outputs": {"requisition_id": "2001"}},
    )

    assert "requisition_id" not in merged
    assert merged["create_req_requisition_id"] == "1003"
    assert merged["create_offer_requisition_id"] == "2001"


def test_build_suite_context_from_previous_results_rehydrates_parent_outputs_for_resume() -> None:
    recordings = [
        {"id": "HCM_Create_Requisition"},
        {"id": "HCM_Approve_Job_Requisition"},
    ]
    previous_results = [
        {"extracted_outputs": {"requisition_id": "1008", "requisition_title": "Analyst"}},
        {"extracted_outputs": {}},
    ]

    suite_context = _build_suite_context_from_previous_results(recordings, previous_results)

    assert suite_context["requisition_id"] == "1008"
    assert suite_context["requisition_title"] == "Analyst"
    assert suite_context["HCM_Create_Requisition_requisition_id"] == "1008"
    assert suite_context["HCM_Create_Requisition_requisition_title"] == "Analyst"


def test_blocked_dependency_reason_mentions_upstream_failure_details() -> None:
    reason = _blocked_dependency_reason(
        {"name": "HCM_Create_Requisition"},
        {"status": "failed", "error": "Organization option did not appear."},
    )

    assert 'upstream recording "HCM_Create_Requisition" failed' in reason
    assert "Organization option did not appear." in reason


@pytest.mark.asyncio
async def test_sequential_suite_stops_after_first_failed_recording_and_blocks_dependents(monkeypatch) -> None:
    recordings = [
        {"id": "create", "name": "HCM_Create_Requisition", "file": "recordings/create.py"},
        {"id": "approve", "name": "HCM_Approve_Job_Requisition", "file": "recordings/approve.py"},
        {"id": "post", "name": "HCM_Move_To_Posting", "file": "recordings/post.py"},
    ]
    child_calls: list[str] = []
    tool_calls: list[tuple[str, tuple[object, ...]]] = []

    async def _fake_tool_execute(tool_name, *args, **kwargs):
        tool_calls.append((tool_name, args))
        if tool_name == "expand_recordings_for_parameter_rows":
            return args[0]
        if tool_name == "record_blocked_recording":
            recording = args[2]
            reason = args[3]
            return {
                "recording_name": recording["name"],
                "status": "failed",
                "error": reason,
                "result_s3_key": f"blocked/{recording['id']}.json",
            }
        if tool_name == "generate_html_report":
            manifest_keys = args[2]
            ordered_names = args[3]
            assert ordered_names == [recording["name"] for recording in recordings]
            assert manifest_keys == {
                "HCM_Create_Requisition": "manifest/create.json",
                "HCM_Approve_Job_Requisition": "blocked/approve.json",
                "HCM_Move_To_Posting": "blocked/post.json",
            }
            return "report.html"
        raise AssertionError(f"Unexpected tool call: {tool_name}")

    async def _fake_agent_execute(agent_name, payload, **kwargs):
        assert agent_name == "PlaywrightTestRunnerChild"
        child_calls.append(payload["recording"]["id"])
        if payload["recording"]["id"] == "create":
            return {
                "recording_name": "HCM_Create_Requisition",
                "status": "failed",
                "error": "Organization option did not appear.",
                "result_s3_key": "manifest/create.json",
                "extracted_outputs": {},
            }
        raise AssertionError("Downstream recordings should not be executed after an upstream failure.")

    monkeypatch.setattr("src.agent.agent.toolExecutor.execute", _fake_tool_execute)
    monkeypatch.setattr("src.agent.agent.agentExecutor.execute", _fake_agent_execute)
    monkeypatch.setattr(
        "src.agent.agent.workflow.info",
        lambda: SimpleNamespace(run_id="parent-run-1", task_queue="ptr-task-queue"),
    )

    response = await PlaywrightTestRunnerAgent.fn(
        {
            "test_suite_id": "suite-1",
            "execution_mode": "sequential",
            "recordings": recordings,
        }
    )

    assert child_calls == ["create"]
    blocked_calls = [args for tool_name, args in tool_calls if tool_name == "record_blocked_recording"]
    assert [call[2]["id"] for call in blocked_calls] == ["approve", "post"]
    assert "Organization option did not appear." in blocked_calls[0][3]

    summary = next(item for item in response if item.get("type") == "summary")
    assert summary["total"] == 3
    assert summary["passed"] == 0
    assert summary["failed"] == 3
