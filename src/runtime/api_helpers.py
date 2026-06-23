from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Outputs recorded via extract() during a plain-Python script step. Each call
# rewrites the runner-provided output file so the runner can surface them after
# the run finishes.
_EXTRACTED_OUTPUTS: dict[str, Any] = {}


def get_runtime_params() -> dict[str, Any]:
    """Return the resolved run parameters (Excel + inline overrides) as a dict.

    The runner resolves parameters before execution and passes them in as a
    JSON object, so a script just reads them in one call::

        params = get_runtime_params()
        username = params["username"]
    """
    raw = str(os.getenv("PTR_EXECUTION_PARAMETERS_JSON", "") or "").strip()
    if not raw:
        return {}
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("PTR_EXECUTION_PARAMETERS_JSON was not valid JSON.") from exc
    return dict(values) if isinstance(values, dict) else {}


def extract(name: str, value: Any) -> None:
    """Record a named output value to surface in the run's extracted outputs.

    e.g. ``extract("order_number", data["OrderNumber"])``.
    """
    output_name = str(name or "").strip()
    if not output_name:
        raise ValueError("extract name must be a non-empty string")
    _EXTRACTED_OUTPUTS[output_name] = value

    output_path = str(os.getenv("PTR_SCRIPT_STEP_OUTPUT_PATH", "") or "").strip()
    if not output_path:
        return
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"outputs": _EXTRACTED_OUTPUTS}, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _reset_for_tests() -> None:
    _EXTRACTED_OUTPUTS.clear()
