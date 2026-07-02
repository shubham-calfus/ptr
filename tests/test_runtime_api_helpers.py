import json

import pytest

from src.runtime import api_helpers


def test_get_runtime_params_returns_execution_parameters(monkeypatch) -> None:
    monkeypatch.setenv("ACT_EXECUTION_PARAMETERS_JSON", '{"username":"demo","count":2}')

    assert api_helpers.get_runtime_params() == {"username": "demo", "count": 2}


def test_get_runtime_params_preserves_multi_line_rows(monkeypatch) -> None:
    monkeypatch.setenv(
        "ACT_EXECUTION_PARAMETERS_JSON",
        '{"username":"demo","multi_line":[{"description":"Line 1","quantity":"-1","unit_price":"10"}]}',
    )

    assert api_helpers.get_runtime_params() == {
        "username": "demo",
        "multi_line": [{"description": "Line 1", "quantity": "-1", "unit_price": "10"}],
    }


def test_get_runtime_params_returns_empty_dict_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("ACT_EXECUTION_PARAMETERS_JSON", raising=False)

    assert api_helpers.get_runtime_params() == {}


def test_get_runtime_params_raises_on_invalid_json(monkeypatch) -> None:
    monkeypatch.setenv("ACT_EXECUTION_PARAMETERS_JSON", "{not json")

    with pytest.raises(RuntimeError):
        api_helpers.get_runtime_params()


def test_extract_writes_outputs_file(tmp_path, monkeypatch) -> None:
    api_helpers._reset_for_tests()
    output_path = tmp_path / "script-step-output.json"
    monkeypatch.setenv("ACT_SCRIPT_STEP_OUTPUT_PATH", str(output_path))

    api_helpers.extract("order_number", "PO-1009")
    api_helpers.extract("po_header_id", 300000123456789)

    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "outputs": {
            "order_number": "PO-1009",
            "po_header_id": 300000123456789,
        }
    }


def test_extract_rejects_empty_name(monkeypatch) -> None:
    api_helpers._reset_for_tests()
    monkeypatch.delenv("ACT_SCRIPT_STEP_OUTPUT_PATH", raising=False)

    with pytest.raises(ValueError):
        api_helpers.extract("   ", "value")
