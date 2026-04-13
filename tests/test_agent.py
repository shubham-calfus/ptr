import pytest

from src.agent.agent import _expand_recordings_for_parameter_rows


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
