from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import subprocess
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from aetherion_sdk import tool
from common_lib.storage.storage_client import RetrievalMode, storage
from common_lib.utils.logger import setup_logger
from src.utils.html_report_generator import generate_html_report_content

logger = setup_logger(__name__)

_MAX_AI_LOG_CHARS = 3_000


_MAX_AI_IMAGE_BYTES = 700_000
_MAX_AI_STEP_IMAGES = 2
_MAX_OPENAI_ERROR_CHARS = 1_600
_PLACEHOLDER_TOKEN_RE = re.compile(r"^\{\{\w+\}\}$")


def _get_bucket_name() -> str:
    bucket_name = os.getenv("STORAGE_ACTIVITIES_BUCKET", "").strip()
    if not bucket_name:
        raise RuntimeError("STORAGE_ACTIVITIES_BUCKET is not configured.")
    return bucket_name


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip())
    return cleaned.strip("._") or "unknown"


def _split_storage_object_ref(object_ref: str) -> tuple[str, str]:
    raw = str(object_ref or "").strip()
    if not raw:
        raise ValueError("Storage object key is required.")

    if raw.lower().startswith("s3://"):
        parsed = urlparse(raw)
        bucket_name = parsed.netloc.strip()
        object_key = parsed.path.lstrip("/")
        if not bucket_name or not object_key:
            raise ValueError(f"Invalid S3 object reference: {object_ref}")
        return bucket_name, object_key

    bucket_name = _get_bucket_name()
    bucket_prefix = f"{bucket_name}/"
    object_key = raw[len(bucket_prefix) :] if raw.startswith(bucket_prefix) else raw
    object_key = object_key.lstrip("/")
    if not object_key:
        raise ValueError("Storage object key is required.")
    return bucket_name, object_key


def _storage_get_bytes(object_key: str) -> bytes:
    storage.init_client()
    bucket_name, normalized_key = _split_storage_object_ref(object_key)

    if hasattr(storage, "retrieve"):
        data = storage.retrieve(
            bucket_name=bucket_name,
            object_key=normalized_key,
            retrieval_mode=RetrievalMode.FULL_OBJECT,
        )
        if isinstance(data, bytes):
            return data

    client = getattr(storage, "client", None)
    if client is None:
        raise RuntimeError("Storage client is not initialized.")

    response = client.get_object(Bucket=bucket_name, Key=normalized_key)
    return response["Body"].read()


def _storage_put_bytes(object_key: str, data: bytes, *, content_type: str) -> tuple[str, str]:
    storage.init_client()
    return storage.store_object(
        bucket_name=_get_bucket_name(),
        object_key=object_key,
        data=data,
        content_type=content_type,
    )


def _load_script_bytes(object_key: str) -> bytes:
    if not object_key.lower().endswith(".py"):
        raise ValueError(f"Unsupported recording artifact: {object_key}. playwright_test_runner can only execute .py recordings.")
    return _storage_get_bytes(object_key)


def _read_manifest(object_key: str) -> dict[str, Any]:
    manifest_bytes = _storage_get_bytes(object_key)
    return json.loads(manifest_bytes.decode("utf-8"))


def _parameterise_script(script_text: str) -> tuple[str, dict[str, str]]:
    """
    Single-pass: extract every parameterisable value from a Playwright
    recording AND replace those values with {{param_name}} placeholders.

    Handles five patterns codegen produces:
      0. page.goto("url")                            → url
      1. .fill("value") with field-name context      → text inputs
      2. .select_option(label=..) / ("value")        → <select> fields
      3. gridcell.click() after combobox.click()     → LOV / dropdown picks
      4. get_by_text().click() after "Search: X"     → popup LOV picks

    Returns (modified_script_with_placeholders, {param_name: default_value}).
    The caller merges the defaults with any runtime overrides before execution,
    so the original script never needs to be manually edited.
    """
    _GOTO_RE       = re.compile(r'(\.goto\()"([^"\\]+)"')
    _FILL_RE       = re.compile(r'(\.fill\()"([^"\\]+)"')
    _SEL_LABEL_RE  = re.compile(r'(\.select_option\(label=)"([^"\\]+)"')
    _SEL_VALUE_RE  = re.compile(r'(\.select_option\()"([^"\\]+)"')
    _FIELD_NAME_RE = re.compile(r'(?:get_by_role\("[^"]+",\s*name="([^"\\]+)"\)|get_by_label\("([^"\\]+)"\))')
    _GRIDCELL_CONTEXT_RE = re.compile(
        r'get_by_role\("(?:combobox|listbox|textbox)",\s*name="([^"\\]+)"\)\.click\('
    )
    _GRIDCELL_RE   = re.compile(r'(get_by_role\("gridcell",\s*name=)"([^"\\]+)"')
    _SEARCH_RE     = re.compile(r'get_by_title\("Search:\s*([^"\\]+)"\)')
    _TEXT_CLICK_RE = re.compile(r'(get_by_text\()"([^"\\]+)"(\)(?:\.first)?\.click\(\))')

    params: dict[str, str] = {}
    seen:   set[str]       = set()

    def _register(param: str, value: str) -> None:
        if param in seen or not value or _PLACEHOLDER_TOKEN_RE.fullmatch(value.strip()):
            return
        params[param] = value
        seen.add(param)

    pending_gridcell_context: str | None = None
    last_search:   str | None = None
    out: list[str] = []

    for line in script_text.splitlines(keepends=True):
        s = line.strip()
        new_line = line

        # Update context trackers (before replacement so context is correct)
        cb_m = _GRIDCELL_CONTEXT_RE.search(s)
        if cb_m:
            pending_gridcell_context = cb_m.group(1)
        elif s and not s.startswith("#") and not _GRIDCELL_RE.search(s):
            pending_gridcell_context = None

        srch_m = _SEARCH_RE.search(s)
        if srch_m:
            last_search = srch_m.group(1).strip()

        # Pattern 0 — page.goto(url)
        goto_m = _GOTO_RE.search(s)
        if goto_m:
            _register("url", goto_m.group(2))
            new_line = _GOTO_RE.sub(r'\1"{{url}}"', line, count=1)

        # Pattern 1 — fill
        elif _FILL_RE.search(s):
            fill_m = _FILL_RE.search(s)
            fn_m = _FIELD_NAME_RE.search(s)
            name = (fn_m.group(1) or fn_m.group(2)) if fn_m else None
            if name:
                param = _normalize_param_name(name)
                _register(param, fill_m.group(2))
                new_line = _FILL_RE.sub(rf'\1"{{{{{param}}}}}"', line, count=1)
            pending_gridcell_context = None  # fill means regular text input, not a LOV

        # Pattern 2 — select_option
        elif _SEL_LABEL_RE.search(s):
            sel_m = _SEL_LABEL_RE.search(s)
            fn_m = _FIELD_NAME_RE.search(s)
            name = (fn_m.group(1) or fn_m.group(2)) if fn_m else None
            if name:
                param = _normalize_param_name(name)
                _register(param, sel_m.group(2))
                new_line = _SEL_LABEL_RE.sub(rf'\1"{{{{{param}}}}}"', line, count=1)
        elif _SEL_VALUE_RE.search(s):
            sel_m = _SEL_VALUE_RE.search(s)
            fn_m = _FIELD_NAME_RE.search(s)
            name = (fn_m.group(1) or fn_m.group(2)) if fn_m else None
            if name:
                param = _normalize_param_name(name)
                _register(param, sel_m.group(2))
                new_line = _SEL_VALUE_RE.sub(rf'\1"{{{{{param}}}}}"', line, count=1)

        # Pattern 3 — gridcell → combobox context
        elif _GRIDCELL_RE.search(s) and ".click()" in s and pending_gridcell_context:
            gc_m = _GRIDCELL_RE.search(s)
            param = _normalize_param_name(pending_gridcell_context)
            _register(param, gc_m.group(2))
            new_line = _GRIDCELL_RE.sub(rf'\1"{{{{{param}}}}}"', line, count=1)
            pending_gridcell_context = None

        # Pattern 4 — get_by_text → search context
        elif _TEXT_CLICK_RE.search(s) and last_search:
            txt_m = _TEXT_CLICK_RE.search(s)
            param = _normalize_param_name(last_search)
            _register(param, txt_m.group(2))
            new_line = _TEXT_CLICK_RE.sub(rf'\1"{{{{{param}}}}}"\3', line, count=1)
            last_search = None

        out.append(new_line)

    return "".join(out), params


def _normalize_param_name(name: str) -> str:
    """Convert 'Receipt Number' → 'receipt_number', 'url' → 'url', etc."""
    normalized = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    aliases = {
        "starturl": "url",
        "start_url": "url",
    }
    return aliases.get(normalized, normalized)


def _extract_table_parameter_sets(rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    normalized_rows: list[tuple[int, tuple[str, ...]]] = []
    for row_index, row in enumerate(rows, start=1):
        values = tuple(str(value if value is not None else "").strip() for value in row)
        if any(values):
            normalized_rows.append((row_index, values))

    if not normalized_rows:
        return []

    first_row_index, first_row = normalized_rows[0]
    if len(normalized_rows) >= 2 and sum(1 for cell in first_row if cell) > 2:
        parameter_sets: list[dict[str, Any]] = []
        for source_row_index, data_row in normalized_rows[1:]:
            horizontal_params: dict[str, str] = {}
            for idx, header in enumerate(first_row):
                if not header or idx >= len(data_row):
                    continue
                value = data_row[idx]
                if not value:
                    continue
                normalized_header = _normalize_param_name(header)
                if normalized_header.startswith("click_"):
                    continue
                horizontal_params[normalized_header] = value
            if horizontal_params:
                parameter_sets.append(
                    {
                        "row_index": source_row_index,
                        "values": horizontal_params,
                    }
                )
        if parameter_sets:
            return parameter_sets

    header_aliases = {
        "parameter",
        "parameter name",
        "param",
        "name",
        "field",
    }
    value_aliases = {
        "value",
        "parameter value",
        "default",
        "default value",
    }

    def _header_index(cells: tuple[str, ...], aliases: set[str]) -> int | None:
        for idx, cell in enumerate(cells):
            normalized = re.sub(r"\s+", " ", cell.lower()).strip()
            if normalized in aliases:
                return idx
        return None

    param_idx = _header_index(first_row, header_aliases)
    value_idx = _header_index(first_row, value_aliases)
    start_row = 1 if param_idx is not None and value_idx is not None else 0
    if param_idx is None:
        param_idx = 0
    if value_idx is None:
        value_idx = 1

    params: dict[str, str] = {}
    first_data_row_index = first_row_index
    for source_row_index, row in normalized_rows[start_row:]:
        if param_idx >= len(row) or value_idx >= len(row):
            continue
        param_name = row[param_idx]
        param_value = row[value_idx]
        # Skip action rows (click_*), header-like rows, and rows with no value
        if not param_name or param_name.lower().startswith("click_") or not param_value:
            continue
        if not params:
            first_data_row_index = source_row_index
        params[_normalize_param_name(param_name)] = param_value
    if not params:
        return []
    return [
        {
            "row_index": first_data_row_index,
            "values": params,
        }
    ]


def _extract_table_parameters(rows: list[tuple[Any, ...]]) -> dict[str, str]:
    parameter_sets = _extract_table_parameter_sets(rows)
    if not parameter_sets:
        return {}
    return dict(parameter_sets[0].get("values") or {})


def _parse_excel_parameter_sets(raw_bytes: bytes) -> list[dict[str, Any]]:
    import io
    import openpyxl  # lazy import — only needed when a parameters file is provided

    wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
    ws = wb.active
    parameter_sets = _extract_table_parameter_sets(list(ws.iter_rows(values_only=True)))
    wb.close()
    return parameter_sets


def _parse_excel_parameters(raw_bytes: bytes) -> dict[str, str]:
    parameter_sets = _parse_excel_parameter_sets(raw_bytes)
    if not parameter_sets:
        return {}
    return dict(parameter_sets[0].get("values") or {})


def _parse_csv_parameter_sets(raw_bytes: bytes) -> list[dict[str, Any]]:
    import csv
    import io

    reader = csv.reader(io.StringIO(raw_bytes.decode("utf-8-sig")))
    return _extract_table_parameter_sets([tuple(row) for row in reader])


def _parse_csv_parameters(raw_bytes: bytes) -> dict[str, str]:
    parameter_sets = _parse_csv_parameter_sets(raw_bytes)
    if not parameter_sets:
        return {}
    return dict(parameter_sets[0].get("values") or {})


def _load_parameter_sets_from_file(file_key: str) -> list[dict[str, Any]]:
    raw_bytes = _storage_get_bytes(file_key)
    lower = file_key.lower()
    if lower.endswith(".xlsx") or lower.endswith(".xls"):
        return _parse_excel_parameter_sets(raw_bytes)
    if lower.endswith(".csv"):
        return _parse_csv_parameter_sets(raw_bytes)
    raise ValueError(f"Unsupported parameters file format: {file_key}. Use .xlsx or .csv.")


def _load_parameters_from_file(file_key: str) -> dict[str, str]:
    parameter_sets = _load_parameter_sets_from_file(file_key)
    if not parameter_sets:
        return {}
    return dict(parameter_sets[0].get("values") or {})


def _derive_parameters_file_candidates(file_key: str) -> list[str]:
    raw = str(file_key or "").strip()
    if not raw:
        return []

    bucket_name, normalized_key = _split_storage_object_ref(raw)
    script_path = Path(normalized_key)
    if script_path.suffix.lower() != ".py":
        return []

    sibling_keys = [
        str(script_path.with_name(f"{script_path.stem}_params.xlsx")),
        str(script_path.with_name(f"{script_path.stem}_params.csv")),
    ]
    candidates: list[str] = []
    raw_uses_s3_uri = raw.lower().startswith("s3://")
    raw_uses_bucket_prefix = raw.startswith(f"{bucket_name}/")
    for key in sibling_keys:
        if raw_uses_s3_uri:
            candidates.append(f"s3://{bucket_name}/{key}")
        elif raw_uses_bucket_prefix:
            candidates.append(f"{bucket_name}/{key}")
        candidates.append(key)
    return list(dict.fromkeys(candidates))


def _load_recording_parameters(
    recording: dict[str, Any],
    file_key: str,
) -> tuple[dict[str, str], str | None]:
    parameter_sets, loaded_from = _load_recording_parameter_sets(recording, file_key)
    if not parameter_sets:
        return {}, loaded_from
    return dict(parameter_sets[0].get("values") or {}), loaded_from


def _load_recording_parameter_sets(
    recording: dict[str, Any],
    file_key: str,
) -> tuple[list[dict[str, Any]], str | None]:
    explicit_file = str(recording.get("parameters_file") or "").strip()
    candidates = []
    if explicit_file:
        candidates.append(explicit_file)
    candidates.extend(_derive_parameters_file_candidates(file_key))

    seen: set[str] = set()
    errors: list[tuple[str, Exception]] = []
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            return _load_parameter_sets_from_file(candidate), candidate
        except Exception as exc:
            errors.append((candidate, exc))

    if explicit_file:
        details = "; ".join(f"{candidate}: {exc}" for candidate, exc in errors)
        raise RuntimeError(f"Failed to load parameters file. {details}")

    return [], None


def _expand_recordings_for_parameter_rows_data(
    recordings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    expanded_recordings: list[dict[str, Any]] = []

    for recording in recordings:
        if recording.get("skip_parameters_file_load"):
            expanded_recordings.append(recording)
            continue

        file_key = str(recording.get("file") or recording.get("recording_name") or "").strip()
        if not file_key:
            expanded_recordings.append(recording)
            continue

        try:
            parameter_sets, loaded_from = _load_recording_parameter_sets(recording, file_key)
        except Exception as exc:
            logger.warning("Failed to pre-expand parameters for %s: %s", file_key, exc)
            expanded_recordings.append(recording)
            continue

        if len(parameter_sets) <= 1:
            expanded_recordings.append(recording)
            continue

        base_name = str(recording.get("name") or file_key or "recording").strip() or "recording"
        base_parameters = recording.get("parameters") if isinstance(recording.get("parameters"), dict) else {}

        for set_index, parameter_set in enumerate(parameter_sets, start=1):
            row_index = int(parameter_set.get("row_index") or set_index)
            row_values = dict(parameter_set.get("values") or {})
            merged_parameters = dict(row_values)
            merged_parameters.update(base_parameters)

            expanded_recording = dict(recording)
            expanded_recording["id"] = f'{recording.get("id") or "recording"}-row-{row_index}'
            expanded_recording["name"] = f"{base_name} [row {row_index}]"
            expanded_recording["parameters"] = merged_parameters
            expanded_recording["parameters_file_key"] = loaded_from
            expanded_recording["parameter_set_index"] = set_index
            expanded_recording["parameter_row_index"] = row_index
            expanded_recording["skip_parameters_file_load"] = True
            expanded_recordings.append(expanded_recording)

    return expanded_recordings


def _normalize_parameter_values(parameters: dict[str, Any] | None) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_key, raw_value in (parameters or {}).items():
        param_name = _normalize_param_name(str(raw_key or ""))
        param_value = str(raw_value).strip() if raw_value is not None else ""
        if not param_name or not param_value:
            continue
        normalized[param_name] = param_value
    return normalized


def _parameters_to_json_object(parameters: dict[str, Any] | None) -> dict[str, str]:
    normalized = _normalize_parameter_values(parameters)
    return json.loads(json.dumps(normalized, sort_keys=True))


def _truncate_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}... [truncated]"


def _is_ai_failure_summary_enabled() -> bool:
    raw = str(os.getenv("OPENAI_FAILURE_SUMMARY_ENABLED", "true")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _get_openai_base_url() -> str:
    return os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")


def _get_openai_failure_summary_model() -> str:
    return os.getenv("OPENAI_FAILURE_SUMMARY_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"


def _summarize_openai_error(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if len(text) <= _MAX_OPENAI_ERROR_CHARS:
        return text or "unknown error"
    return f"{text[:_MAX_OPENAI_ERROR_CHARS]}... [truncated]"


def _image_path_to_data_url(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    image_bytes = path.read_bytes()
    if not image_bytes or len(image_bytes) > _MAX_AI_IMAGE_BYTES:
        return None
    return f"data:image/png;base64,{base64.b64encode(image_bytes).decode('utf-8')}"


def _extract_response_output_text(payload: dict[str, Any]) -> str:
    direct = str(payload.get("output_text") or "").strip()
    if direct:
        return direct

    parts: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            text = str(content.get("text") or "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _parse_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("AI response was not a JSON object.")
    return parsed


def _build_ai_failure_summary_prompt(result: dict[str, Any]) -> str:
    step_lines = []
    for step in list(result.get("step_artifacts") or []):
        step_lines.append(
            f"- Step {int(step.get('index') or 0)}: {str(step.get('action') or 'step')}"
        )
    step_text = "\n".join(step_lines) if step_lines else "- No captured steps available"

    return textwrap.dedent(
        f"""
        Analyze this failed Playwright test execution and return JSON only.

        Goals:
        - Identify the most likely root cause.
        - Point to the most likely failing step when possible.
        - Suggest the next debugging action or probable fix.
        - Be concise and evidence-based.
        - If evidence is weak, say so.

        Return exactly these keys:
        {{
          "headline": string,
          "summary": string,
          "failure_category": string,
          "suspected_step_index": integer or null,
          "confidence": "low" | "medium" | "high",
          "evidence": string[],
          "next_action": string
        }}

        Failure context:
        - Recording name: {result.get("recording_name") or result.get("file_key") or "unknown"}
        - File key: {result.get("file_key") or "unknown"}
        - Exit code: {result.get("exit_code")}
        - Page title: {result.get("page_title") or "unknown"}
        - Page URL: {result.get("page_url") or "unknown"}
        - Error: {_truncate_text(result.get("error"), max_chars=_MAX_AI_LOG_CHARS)}

        stderr:
        {_truncate_text(result.get("stderr"), max_chars=_MAX_AI_LOG_CHARS) or "No stderr captured."}

        stdout:
        {_truncate_text(result.get("stdout"), max_chars=_MAX_AI_LOG_CHARS) or "No stdout captured."}

        Captured steps:
        {step_text}
        """
    ).strip()


def _normalize_ai_failure_summary(payload: dict[str, Any], *, model: str) -> dict[str, Any]:
    evidence = payload.get("evidence")
    if not isinstance(evidence, list):
        evidence = []

    suspected_step_index = payload.get("suspected_step_index")
    if isinstance(suspected_step_index, bool):
        suspected_step_index = None
    elif suspected_step_index is not None:
        try:
            suspected_step_index = int(suspected_step_index)
        except (TypeError, ValueError):
            suspected_step_index = None

    confidence = str(payload.get("confidence") or "medium").strip().lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "medium"

    return {
        "status": "generated",
        "model": model,
        "headline": _truncate_text(payload.get("headline") or "AI failure summary", max_chars=160),
        "summary": _truncate_text(payload.get("summary") or "", max_chars=800),
        "failure_category": _truncate_text(payload.get("failure_category") or "unknown", max_chars=80),
        "suspected_step_index": suspected_step_index,
        "confidence": confidence,
        "evidence": [
            _truncate_text(item, max_chars=220) for item in evidence if str(item or "").strip()
        ][:4],
        "next_action": _truncate_text(payload.get("next_action") or "", max_chars=300),
    }


def _call_openai_failure_summary(
    result: dict[str, Any],
    *,
    failure_screenshot_path: Path | None,
    step_image_paths: list[Path],
) -> dict[str, Any]:
    api_key = str(os.getenv("OPENAI_API_KEY", "")).strip()
    if not _is_ai_failure_summary_enabled():
        return {
            "status": "skipped",
            "reason": "AI failure summaries are disabled by OPENAI_FAILURE_SUMMARY_ENABLED.",
        }
    if not api_key:
        return {
            "status": "skipped",
            "reason": "OPENAI_API_KEY is not configured.",
        }

    model = _get_openai_failure_summary_model()
    user_content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": _build_ai_failure_summary_prompt(result),
        }
    ]

    if failure_screenshot_path:
        failure_image = _image_path_to_data_url(failure_screenshot_path)
        if failure_image:
            user_content.append(
                {
                    "type": "input_image",
                    "image_url": failure_image,
                }
            )

    for image_path in step_image_paths[:_MAX_AI_STEP_IMAGES]:
        data_url = _image_path_to_data_url(image_path)
        if not data_url:
            continue
        user_content.append(
            {
                "type": "input_image",
                "image_url": data_url,
            }
        )

    request_payload = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "You are a senior QA engineer diagnosing Playwright failures. "
                            "Use the logs, metadata, and screenshots to produce a concise, "
                            "actionable JSON summary."
                        ),
                    }
                ],
            },
            {
                "role": "user",
                "content": user_content,
            },
        ],
        "text": {
            "format": {
                "type": "json_object",
            }
        },
        "max_output_tokens": 500,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    request = Request(
        f"{_get_openai_base_url()}/responses",
        data=json.dumps(request_payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urlopen(request, timeout=45) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        if exc.code == 401:
            return {
                "status": "error",
                "model": model,
                "reason": "AI configuration error: OPENAI_API_KEY is invalid or not configured correctly.",
            }
        return {
            "status": "error",
            "model": model,
            "reason": f"OpenAI request failed ({exc.code}): {_summarize_openai_error(details)}",
        }
    except URLError as exc:
        return {
            "status": "error",
            "model": model,
            "reason": f"Unable to reach OpenAI API: {exc.reason}",
        }
    except Exception as exc:  # pragma: no cover - network/runtime path
        return {
            "status": "error",
            "model": model,
            "reason": f"Unexpected OpenAI failure: {exc}",
        }

    try:
        output_text = _extract_response_output_text(response_payload)
        parsed = _parse_json_response(output_text)
        return _normalize_ai_failure_summary(parsed, model=model)
    except Exception as exc:
        return {
            "status": "error",
            "model": model,
            "reason": f"Failed to parse OpenAI response: {exc}",
        }


def _validate_python_playwright_script(script_text: str) -> None:
    trimmed = script_text.lstrip()

    obvious_js_markers = [
        "import {",
        "from '@playwright/test'",
        'from "@playwright/test"',
        "test(",
        "test.describe(",
        "=>",
        "const ",
        "let ",
        "await page.goto(",
    ]
    if any(marker in trimmed for marker in obvious_js_markers):
        raise ValueError(
            "Recording is not a Python Playwright script. "
            "playwright_test_runner currently supports Python recordings only."
        )

    python_markers = [
        "from playwright.async_api import",
        "from playwright.sync_api import",
        "async_playwright",
        "sync_playwright",
        "def run(",
        "def main(",
        "async def main(",
    ]
    if not any(marker in trimmed for marker in python_markers):
        raise ValueError("Recording does not look like a supported Python Playwright script.")


def _insert_after_future_imports(script_text: str, helper: str) -> str:
    lines = script_text.splitlines(keepends=True)
    idx = 0
    while idx < len(lines) and lines[idx].startswith("from __future__ import"):
        idx += 1
    prefix = "".join(lines[:idx])
    suffix = "".join(lines[idx:])
    return f"{prefix}{helper}\n\n{suffix}"


def _inject_runtime_helpers(script_text: str) -> str:
    helper = textwrap.dedent(
        '''
        import atexit
        import json
        import os
        import re
        import time
        from pathlib import Path
        from urllib.parse import urlparse

        from playwright.sync_api import Browser, BrowserContext, Locator, Page

        _PTR_LAST_PAGE = None
        _PTR_STEP_INDEX = 0
        _PTR_STEP_ARTIFACTS = []
        _PTR_STEEL_BROWSER_SESSION_IDS = {}
        _PTR_STEEL_RELEASE_SESSION_IDS = set()
        _PTR_DIAGNOSTICS_PATH = os.getenv("PTR_DIAGNOSTICS_PATH", "")
        _PTR_FAILURE_SCREENSHOT_PATH = os.getenv("PTR_FAILURE_SCREENSHOT_PATH", "")
        _PTR_STEP_ARTIFACTS_DIR = os.getenv("PTR_STEP_ARTIFACTS_DIR", "")
        _PTR_VIDEO_DIR = os.getenv("PTR_VIDEO_DIR", "")


        def _ptr_env_flag(name: str, default: str = "true") -> bool:
            return str(os.getenv(name, default)).strip().lower() not in ("false", "0", "no", "off")


        def _ptr_wait_ms(env_name: str, default: int) -> int:
            try:
                return max(0, int(os.getenv(env_name, str(default))))
            except Exception:
                return default


        def _ptr_int_env(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, str(default)))
            except Exception:
                return default


        def _ptr_window_dimensions() -> tuple[int, int]:
            width = max(960, _ptr_int_env("PTR_WINDOW_WIDTH", 1440))
            height = max(700, _ptr_int_env("PTR_WINDOW_HEIGHT", 900))
            return width, height


        def _ptr_target_viewport() -> dict[str, int]:
            window_width, window_height = _ptr_window_dimensions()
            width_margin = max(0, _ptr_int_env("PTR_VIEWPORT_WIDTH_MARGIN", 80))
            height_margin = max(0, _ptr_int_env("PTR_VIEWPORT_HEIGHT_MARGIN", 140))
            return {
                "width": max(800, window_width - width_margin),
                "height": max(600, window_height - height_margin),
            }


        _PTR_CAPTURE_STEPS = _ptr_env_flag("PTR_CAPTURE_STEPS", "true")
        _PTR_RECORD_VIDEO = _ptr_env_flag("PTR_RECORD_VIDEO", "true")
        _PTR_STEP_SCREENSHOT_FULL_PAGE = _ptr_env_flag("PTR_STEP_SCREENSHOT_FULL_PAGE", "true")
        _PTR_POPUP_SCOPE_SELECTORS = [
            '[role="dialog"]:visible',
            '[aria-modal="true"]:visible',
            '.oj-dialog:visible',
            '.oj-popup:visible',
            '.af_menu_popup:visible',
            '[role="menu"]:visible',
            '[id*="::lovDialogId"]:visible',
            '[id*="lovDialogId"]:visible',
            '[id*="::msgDlg"]:visible',
            '[id*="::dropdownPopup"]:visible',
            '[id*="::popup-container"]:visible',
            '[data-afr-popupid]:visible',
        ]


        def _ptr_safe_segment(value: str) -> str:
            cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
            return cleaned.strip("._") or "step"


        def _ptr_register_page(page):
            global _PTR_LAST_PAGE
            _PTR_LAST_PAGE = page
            # Oracle ADF and similar apps re-render the DOM after AJAX responses,
            # temporarily disabling fields for 5-10s after a selection. Default to
            # 120s to handle slow ADF page loads; overridable via PTR_TIMEOUT_MS.
            try:
                _ptr_timeout_ms = int(os.getenv("PTR_TIMEOUT_MS", "120000"))
                page.set_default_timeout(_ptr_timeout_ms)
            except Exception:
                pass
            return page


        def _ptr_is_closed_target_error(exc: Exception) -> bool:
            return "Target page, context or browser has been closed" in str(exc)


        def _ptr_get_visible_scopes(current_page):
            scopes = []
            seen = set()
            for selector in _PTR_POPUP_SCOPE_SELECTORS:
                try:
                    scoped_locator = current_page.locator(selector)
                    count = min(scoped_locator.count(), 12)
                except Exception:
                    continue

                for idx in range(count):
                    candidate = scoped_locator.nth(idx)
                    try:
                        if not candidate.is_visible():
                            continue
                    except Exception:
                        continue

                    marker = f"{selector}:{idx}"
                    if marker in seen:
                        continue
                    seen.add(marker)
                    scopes.append(candidate)

            scopes.append(current_page)
            return list(reversed(scopes))


        def _ptr_resolve_active_page(page):
            candidate_pages = []
            try:
                if page is not None and not page.is_closed():
                    candidate_pages.append(page)
            except Exception:
                pass
            try:
                if _PTR_LAST_PAGE is not None and not _PTR_LAST_PAGE.is_closed():
                    candidate_pages.append(_PTR_LAST_PAGE)
            except Exception:
                pass
            try:
                context = page.context if page is not None else None
                if context is not None:
                    candidate_pages.extend(list(getattr(context, "pages", []) or []))
            except Exception:
                pass

            seen = set()
            for candidate in reversed(candidate_pages):
                if candidate is None:
                    continue
                marker = id(candidate)
                if marker in seen:
                    continue
                seen.add(marker)
                try:
                    if not candidate.is_closed():
                        return _ptr_register_page(candidate)
                except Exception:
                    continue
            return page


        def _ptr_capture_step(action: str) -> None:
            global _PTR_STEP_INDEX
            page = _PTR_LAST_PAGE
            if page is None or not _PTR_STEP_ARTIFACTS_DIR or not _PTR_CAPTURE_STEPS:
                return
            try:
                Path(_PTR_STEP_ARTIFACTS_DIR).mkdir(parents=True, exist_ok=True)
                _PTR_STEP_INDEX += 1
                screenshot_path = (
                    Path(_PTR_STEP_ARTIFACTS_DIR)
                    / f"step_{_PTR_STEP_INDEX:03d}_{_ptr_safe_segment(action)}.png"
                )
                page.screenshot(path=str(screenshot_path), full_page=_PTR_STEP_SCREENSHOT_FULL_PAGE)
                _PTR_STEP_ARTIFACTS.append(
                    {
                        "index": _PTR_STEP_INDEX,
                        "action": action,
                        "local_path": str(screenshot_path),
                    }
                )
            except Exception:
                pass


        def _ptr_fill_textbox(primary_locator, page, label: str, value, **kwargs):
            def _ptr_normalize(value) -> str:
                return " ".join(str(value or "").lower().split())

            def _ptr_is_rich_text_locator(locator) -> bool:
                try:
                    return bool(
                        locator.evaluate(
                            """(node) => {
                                if (!node) return false;
                                if (node.matches?.('[contenteditable="true"][role="textbox"], [contenteditable="true"]')) {
                                    return true;
                                }
                                return !!node.closest?.('oj-sp-ai-input-rich-text, oj-sp-input-rich-text-2');
                            }"""
                        )
                    )
                except Exception:
                    return False

            def _ptr_commit_rich_text(current_page, locator, expected_value: str) -> bool:
                normalized_expected = _ptr_normalize(expected_value)
                try:
                    locator.focus(timeout=match_timeout_ms)
                except Exception:
                    pass
                try:
                    current_page.keyboard.press("Tab")
                except Exception:
                    pass
                try:
                    current_page.wait_for_timeout(_ptr_wait_ms("PTR_RICH_TEXT_COMMIT_WAIT_MS", 600))
                except Exception:
                    pass
                try:
                    current_value = locator.evaluate(
                        """(node) => {
                            return String(node.innerText || node.textContent || "").trim();
                        }"""
                    )
                except Exception:
                    return False
                return normalized_expected in _ptr_normalize(current_value)

            def _ptr_fill_locator(current_page, locator, locator_fill_kwargs=None):
                locator.fill(value, **(locator_fill_kwargs or fill_kwargs))
                if _ptr_is_rich_text_locator(locator):
                    if _ptr_commit_rich_text(current_page, locator, value):
                        return
                    raise RuntimeError(f'Rich text "{label}" did not retain the filled value after commit.')

            def _ptr_text_entry_locator(current_page):
                return current_page.locator(
                    'input, textarea, [role="textbox"], [role="spinbutton"], '
                    '[role="combobox"], [contenteditable="true"]'
                )

            def _ptr_collect_text_entry_candidates(current_page):
                locator = _ptr_text_entry_locator(current_page)
                try:
                    count = min(locator.count(), 100)
                except Exception:
                    return []
                candidates = []
                for idx in range(count):
                    try:
                        metadata = locator.nth(idx).evaluate(
                            r"""(node) => {
                                const values = [];
                                const push = (value) => {
                                    const text = String(value || "").trim();
                                    if (text) values.push(text);
                                };
                                push(node.getAttribute("aria-label"));
                                push(node.getAttribute("placeholder"));
                                push(node.getAttribute("name"));
                                push(node.getAttribute("title"));
                                push(node.id);
                                push(node.getAttribute("data-oj-input-id"));
                                push(node.getAttribute("data-oj-field"));

                                const labelledBy = String(
                                    node.getAttribute("aria-labelledby")
                                    || node.getAttribute("labelled-by")
                                    || ""
                                ).trim();
                                if (labelledBy) {
                                    for (const id of labelledBy.split(/\\\\s+/)) {
                                        const labelNode = document.getElementById(id);
                                        if (labelNode) {
                                            push(labelNode.innerText);
                                            push(labelNode.textContent);
                                        }
                                    }
                                }

                                if (node.id) {
                                    for (const labelNode of document.querySelectorAll(`label[for="${node.id}"]`)) {
                                        push(labelNode.textContent);
                                    }
                                }

                                const owner = node.closest(
                                    "oj-select-single, oj-input-date, oj-input-text, oj-input-number, oj-c-input-number, " +
                                    "oj-sp-ai-input-rich-text, oj-sp-input-rich-text-2, oj-validation-group"
                                );
                                if (owner) {
                                    push(owner.getAttribute("label-hint"));
                                    push(owner.getAttribute("aria-label"));
                                    push(owner.getAttribute("data-oj-field"));
                                    push(owner.getAttribute("data-oj-input-id"));
                                    push(owner.getAttribute("labelled-by"));
                                    push(owner.id);

                                    const ownerLabelledBy = String(
                                        owner.getAttribute("aria-labelledby")
                                        || owner.getAttribute("labelled-by")
                                        || ""
                                    ).trim();
                                    if (ownerLabelledBy) {
                                        for (const id of ownerLabelledBy.split(/\\\\s+/)) {
                                            const labelNode = document.getElementById(id);
                                            if (labelNode) {
                                                push(labelNode.innerText);
                                                push(labelNode.textContent);
                                            }
                                        }
                                    }

                                    owner.querySelectorAll(
                                        "label, [id$='|hint'], [id$='-label'], [id$='-suffix'], .oj-label-group"
                                    ).forEach((labelNode) => {
                                        push(labelNode.innerText);
                                        push(labelNode.textContent);
                                    });
                                }

                                let parent = node;
                                for (let depth = 0; parent && depth < 4; depth += 1) {
                                    push(parent.getAttribute && parent.getAttribute("aria-label"));
                                    push(parent.getAttribute && parent.getAttribute("label-hint"));
                                    push(parent.getAttribute && parent.getAttribute("data-oj-field"));
                                    push(parent.getAttribute && parent.getAttribute("data-oj-input-id"));
                                    push(parent.textContent);
                                    parent = parent.parentElement;
                                }

                                return values;
                            }"""
                        )
                    except Exception:
                        continue
                    haystack = _ptr_normalize(" ".join(metadata))
                    candidates.append((idx, haystack))
                return candidates

            try:
                match_timeout_ms = max(
                    1000,
                    int(os.getenv("PTR_TEXT_ENTRY_TIMEOUT_MS", "5000")),
                )
            except Exception:
                match_timeout_ms = 5000
            try:
                direct_timeout_ms = max(
                    250,
                    min(match_timeout_ms, int(os.getenv("PTR_PRIMARY_TEXT_ENTRY_TIMEOUT_MS", "1200"))),
                )
            except Exception:
                direct_timeout_ms = min(match_timeout_ms, 1200)

            fill_kwargs = dict(kwargs)
            fill_kwargs.setdefault("timeout", match_timeout_ms)

            last_exc = None
            for _ptr_page_attempt in range(2):
                current_page = _ptr_resolve_active_page(page)
                if primary_locator is not None:
                    direct_fill_kwargs = dict(fill_kwargs)
                    direct_fill_kwargs["timeout"] = min(
                        int(direct_fill_kwargs.get("timeout", match_timeout_ms)),
                        direct_timeout_ms,
                    )
                    try:
                        _ptr_fill_locator(current_page, primary_locator, direct_fill_kwargs)
                        return
                    except Exception as exc:
                        last_exc = exc
                candidates = [
                    (
                        "rich_text_aria_label",
                        current_page.locator(
                            f'[contenteditable="true"][role="textbox"][aria-label="{label}"], '
                            f'[contenteditable="true"][aria-label="{label}"]'
                        ).first,
                    ),
                    (
                        "oracle_rich_text",
                        current_page.locator(
                            f'oj-sp-ai-input-rich-text [contenteditable="true"][role="textbox"][aria-label="{label}"], '
                            f'oj-sp-input-rich-text-2 [contenteditable="true"][role="textbox"][aria-label="{label}"]'
                        ).first,
                    ),
                    ("role_textbox", current_page.get_by_role("textbox", name=label)),
                    ("role_spinbutton", current_page.get_by_role("spinbutton", name=label)),
                    ("role_combobox", current_page.get_by_role("combobox", name=label)),
                    ("label_exact", current_page.get_by_label(label, exact=True)),
                    ("label_partial", current_page.get_by_label(label, exact=False).first),
                    ("placeholder", current_page.get_by_placeholder(label, exact=False).first),
                    (
                        "aria_label",
                        current_page.locator(
                            f'[aria-label="{label}"], [aria-label*="{label}"]'
                        ).first,
                    ),
                    (
                        "oj_label_hint",
                        current_page.locator(
                            f'oj-select-single[label-hint="{label}"] input, '
                            f'oj-input-date[label-hint="{label}"] input, '
                            f'oj-input-text[label-hint="{label}"] input, '
                            f'oj-input-number[label-hint="{label}"] input, '
                            f'oj-c-input-number[label-hint="{label}"] input'
                        ).first,
                    ),
                ]

                for _ptr_strategy, locator in candidates:
                    try:
                        _ptr_fill_locator(current_page, locator)
                        return
                    except Exception as exc:
                        last_exc = exc

                normalized_label = _ptr_normalize(label)
                label_tokens = [token for token in re.findall(r"[a-z0-9]+", normalized_label) if len(token) > 1]
                matched_candidates = []
                for idx, haystack in _ptr_collect_text_entry_candidates(current_page):
                    score = 0
                    if normalized_label and normalized_label in haystack:
                        score += len(label_tokens) + 1
                    score += sum(1 for token in label_tokens if token in haystack)
                    if score > 0:
                        matched_candidates.append((score, idx))

                matched_candidates.sort(reverse=True)
                entry_locator = _ptr_text_entry_locator(current_page)
                for _score, idx in matched_candidates:
                    locator = entry_locator.nth(idx)
                    try:
                        locator.scroll_into_view_if_needed(timeout=match_timeout_ms)
                    except Exception:
                        pass
                    try:
                        _ptr_fill_locator(current_page, locator)
                        return
                    except Exception as exc:
                        last_exc = exc

                replacement_page = _ptr_resolve_active_page(page)
                if (
                    _ptr_page_attempt == 0
                    and current_page is not replacement_page
                    and _ptr_is_closed_target_error(last_exc)
                ):
                    page = replacement_page
                    continue
                break

            raise RuntimeError(
                f'Unable to fill text entry "{label}" using role/label/placeholder fallbacks.'
            ) from last_exc


        def _ptr_click_textbox(primary_locator, page, label: str, **kwargs):
            try:
                match_timeout_ms = max(
                    1000,
                    int(os.getenv("PTR_TEXT_CLICK_TIMEOUT_MS", "8000")),
                )
            except Exception:
                match_timeout_ms = 8000
            try:
                direct_timeout_ms = max(
                    250,
                    min(match_timeout_ms, int(os.getenv("PTR_PRIMARY_TEXT_CLICK_TIMEOUT_MS", "1000"))),
                )
            except Exception:
                direct_timeout_ms = min(match_timeout_ms, 1000)

            click_kwargs = dict(kwargs)
            click_kwargs.setdefault("timeout", match_timeout_ms)

            last_exc = None
            for _ptr_page_attempt in range(2):
                current_page = _ptr_resolve_active_page(page)
                if primary_locator is not None:
                    direct_click_kwargs = dict(click_kwargs)
                    direct_click_kwargs["timeout"] = min(
                        int(direct_click_kwargs.get("timeout", match_timeout_ms)),
                        direct_timeout_ms,
                    )
                    try:
                        primary_locator.scroll_into_view_if_needed(timeout=direct_timeout_ms)
                    except Exception:
                        pass
                    try:
                        primary_locator.click(**direct_click_kwargs)
                        return
                    except Exception as exc:
                        last_exc = exc
                candidates = [
                    (
                        "rich_text_aria_label",
                        current_page.locator(
                            f'[contenteditable="true"][role="textbox"][aria-label="{label}"], '
                            f'[contenteditable="true"][aria-label="{label}"]'
                        ).first,
                    ),
                    (
                        "oracle_rich_text",
                        current_page.locator(
                            f'oj-sp-ai-input-rich-text [contenteditable="true"][role="textbox"][aria-label="{label}"], '
                            f'oj-sp-input-rich-text-2 [contenteditable="true"][role="textbox"][aria-label="{label}"]'
                        ).first,
                    ),
                    ("role_textbox_exact", current_page.get_by_role("textbox", name=label, exact=True).first),
                    ("role_textbox_partial", current_page.get_by_role("textbox", name=label, exact=False).first),
                    ("label_exact", current_page.get_by_label(label, exact=True).first),
                    ("label_partial", current_page.get_by_label(label, exact=False).first),
                    ("placeholder", current_page.get_by_placeholder(label, exact=False).first),
                    (
                        "aria_label",
                        current_page.locator(
                            f'[aria-label="{label}"], [aria-label*="{label}"]'
                        ).first,
                    ),
                    (
                        "oj_label_hint",
                        current_page.locator(
                            f'oj-input-text[label-hint="{label}"] input, '
                            f'oj-input-date[label-hint="{label}"] input, '
                            f'oj-input-number[label-hint="{label}"] input, '
                            f'oj-sp-ai-input-rich-text [contenteditable="true"], '
                            f'oj-sp-input-rich-text-2 [contenteditable="true"]'
                        ).first,
                    ),
                ]

                for _ptr_strategy, locator in candidates:
                    try:
                        locator.scroll_into_view_if_needed(timeout=match_timeout_ms)
                    except Exception:
                        pass
                    try:
                        locator.click(**click_kwargs)
                        return
                    except Exception as exc:
                        last_exc = exc

                replacement_page = _ptr_resolve_active_page(page)
                if (
                    _ptr_page_attempt == 0
                    and current_page is not replacement_page
                    and _ptr_is_closed_target_error(last_exc)
                ):
                    page = replacement_page
                    continue
                break

            raise RuntimeError(f'Unable to click text entry "{label}".') from last_exc


        def _ptr_click_text_target(page, label: str, **kwargs):
            def _ptr_normalize(value) -> str:
                return " ".join(str(value or "").lower().split())

            def _ptr_clickable_locator(scope):
                return scope.locator(
                    'button, a, [role="button"], [role="tab"], [role="link"], '
                    '[role="menuitem"], [role="option"], [role="cell"], [aria-label], [title]'
                )

            def _ptr_collect_click_candidates(scope):
                locator = _ptr_clickable_locator(scope)
                try:
                    count = min(locator.count(), 100)
                except Exception:
                    return []
                candidates = []
                for idx in range(count):
                    try:
                        metadata = locator.nth(idx).evaluate(
                            r"""(node) => {
                                const values = [];
                                const push = (value) => {
                                    const text = String(value || "").trim();
                                    if (text) values.push(text);
                                };
                                push(node.getAttribute("aria-label"));
                                push(node.getAttribute("title"));
                                push(node.getAttribute("name"));
                                push(node.id);
                                push(node.getAttribute("role"));
                                push(node.innerText);
                                push(node.textContent);

                                let parent = node;
                                for (let depth = 0; parent && depth < 3; depth += 1) {
                                    push(parent.getAttribute && parent.getAttribute("aria-label"));
                                    push(parent.textContent);
                                    parent = parent.parentElement;
                                }

                                return values;
                            }"""
                        )
                    except Exception:
                        continue
                    haystack = _ptr_normalize(" ".join(metadata))
                    candidates.append((idx, haystack))
                return candidates

            try:
                match_timeout_ms = max(
                    1000,
                    int(os.getenv("PTR_TEXT_CLICK_TIMEOUT_MS", "5000")),
                )
            except Exception:
                match_timeout_ms = 5000

            click_kwargs = dict(kwargs)
            click_kwargs.setdefault("timeout", match_timeout_ms)

            last_exc = None
            for _ptr_page_attempt in range(2):
                current_page = _ptr_resolve_active_page(page)
                normalized_label = _ptr_normalize(label)
                label_tokens = [token for token in re.findall(r"[a-z0-9]+", normalized_label) if len(token) > 1]

                for scope in _ptr_get_visible_scopes(current_page):
                    candidates = [
                        ("text_exact", scope.get_by_text(label, exact=True).first),
                        ("role_tab_exact", scope.get_by_role("tab", name=label, exact=True).first),
                        ("role_button_exact", scope.get_by_role("button", name=label, exact=True).first),
                        ("role_link_exact", scope.get_by_role("link", name=label, exact=True).first),
                        ("role_menuitem_exact", scope.get_by_role("menuitem", name=label, exact=True).first),
                        ("role_option_exact", scope.get_by_role("option", name=label, exact=True).first),
                        ("role_cell_exact", scope.get_by_role("cell", name=label, exact=True).first),
                        ("text_partial", scope.get_by_text(label, exact=False).first),
                        ("role_tab_partial", scope.get_by_role("tab", name=label, exact=False).first),
                        ("role_button_partial", scope.get_by_role("button", name=label, exact=False).first),
                        ("role_link_partial", scope.get_by_role("link", name=label, exact=False).first),
                        (
                            "aria_label",
                            scope.locator(
                                f'[aria-label="{label}"], [aria-label*="{label}"]'
                            ).first,
                        ),
                        (
                            "title",
                            scope.locator(
                                f'[title="{label}"], [title*="{label}"]'
                            ).first,
                        ),
                    ]

                    for _ptr_strategy, locator in candidates:
                        try:
                            locator.scroll_into_view_if_needed(timeout=match_timeout_ms)
                        except Exception:
                            pass
                        try:
                            return locator.click(**click_kwargs)
                        except Exception as exc:
                            last_exc = exc

                    matched_candidates = []
                    for idx, haystack in _ptr_collect_click_candidates(scope):
                        score = 0
                        has_full_label_match = bool(normalized_label and normalized_label in haystack)
                        has_all_token_match = bool(
                            len(label_tokens) > 1
                            and label_tokens
                            and all(token in haystack for token in label_tokens)
                        )
                        if has_full_label_match:
                            score += len(label_tokens) + 1
                        elif has_all_token_match:
                            score += len(label_tokens)
                        elif len(label_tokens) == 1:
                            score += sum(1 for token in label_tokens if token in haystack)
                        if score > 0:
                            matched_candidates.append((score, idx))

                    matched_candidates.sort(reverse=True)
                    click_locator = _ptr_clickable_locator(scope)
                    for _score, idx in matched_candidates:
                        locator = click_locator.nth(idx)
                        try:
                            locator.scroll_into_view_if_needed(timeout=match_timeout_ms)
                        except Exception:
                            pass
                        try:
                            return locator.click(**click_kwargs)
                        except Exception as exc:
                            last_exc = exc

                replacement_page = _ptr_resolve_active_page(page)
                if (
                    _ptr_page_attempt == 0
                    and current_page is not replacement_page
                    and _ptr_is_closed_target_error(last_exc)
                ):
                    page = replacement_page
                    continue
                break

            raise RuntimeError(
                f'Unable to click text target "{label}" using text/role fallbacks.'
            ) from last_exc


        def _ptr_click_button_target(page, label: str, **kwargs):
            def _ptr_normalize(value) -> str:
                return " ".join(str(value or "").lower().split())

            def _ptr_button_locator(scope):
                return scope.locator(
                    'button, [role="button"], input[type="button"], input[type="submit"], '
                    'a[role="button"], [aria-label], [title]'
                )

            def _ptr_collect_button_candidates(scope):
                locator = _ptr_button_locator(scope)
                try:
                    count = min(locator.count(), 100)
                except Exception:
                    return []
                candidates = []
                for idx in range(count):
                    try:
                        metadata = locator.nth(idx).evaluate(
                            """(node) => {
                                const values = [];
                                const push = (value) => {
                                    const text = String(value || "").trim();
                                    if (text) values.push(text);
                                };
                                push(node.getAttribute("aria-label"));
                                push(node.getAttribute("title"));
                                push(node.getAttribute("name"));
                                push(node.id);
                                push(node.getAttribute("role"));
                                push(node.innerText);
                                push(node.textContent);

                                let parent = node;
                                for (let depth = 0; parent && depth < 4; depth += 1) {
                                    push(parent.getAttribute && parent.getAttribute("aria-label"));
                                    push(parent.getAttribute && parent.getAttribute("title"));
                                    push(parent.innerText);
                                    push(parent.textContent);
                                    parent = parent.parentElement;
                                }

                                return values;
                            }"""
                        )
                    except Exception:
                        continue
                    haystack = _ptr_normalize(" ".join(metadata))
                    candidates.append((idx, haystack))
                return candidates

            try:
                match_timeout_ms = max(
                    1000,
                    int(os.getenv("PTR_BUTTON_CLICK_TIMEOUT_MS", os.getenv("PTR_TEXT_CLICK_TIMEOUT_MS", "5000"))),
                )
            except Exception:
                match_timeout_ms = 5000

            click_kwargs = dict(kwargs)
            click_kwargs.setdefault("timeout", match_timeout_ms)

            last_exc = None
            for _ptr_page_attempt in range(2):
                current_page = _ptr_resolve_active_page(page)
                scopes = _ptr_get_visible_scopes(current_page)
                normalized_label = _ptr_normalize(label)
                label_tokens = [token for token in re.findall(r"[a-z0-9]+", normalized_label) if len(token) > 1]

                for scope in scopes:
                    candidates = [
                        ("role_button_exact", scope.get_by_role("button", name=label, exact=True).first),
                        ("role_button_partial", scope.get_by_role("button", name=label, exact=False).first),
                        ("css_button_text", scope.locator(f'button:has-text("{label}"), [role="button"]:has-text("{label}")').first),
                        ("aria_label", scope.locator(f'[aria-label="{label}"], [aria-label*="{label}"]').first),
                        ("title", scope.locator(f'[title="{label}"], [title*="{label}"]').first),
                    ]

                    for _ptr_strategy, locator in candidates:
                        try:
                            locator.scroll_into_view_if_needed(timeout=match_timeout_ms)
                        except Exception:
                            pass
                        try:
                            return locator.click(**click_kwargs)
                        except Exception as exc:
                            last_exc = exc

                    matched_candidates = []
                    for idx, haystack in _ptr_collect_button_candidates(scope):
                        score = 0
                        has_full_label_match = bool(normalized_label and normalized_label in haystack)
                        has_all_token_match = bool(
                            len(label_tokens) > 1
                            and label_tokens
                            and all(token in haystack for token in label_tokens)
                        )

                        if has_full_label_match:
                            score += len(label_tokens) + 1
                        elif has_all_token_match:
                            score += len(label_tokens)
                        elif len(label_tokens) == 1:
                            score += sum(1 for token in label_tokens if token in haystack)
                        if score > 0:
                            matched_candidates.append((score, idx))

                    matched_candidates.sort(reverse=True)
                    button_locator = _ptr_button_locator(scope)
                    for _score, idx in matched_candidates:
                        locator = button_locator.nth(idx)
                        try:
                            locator.scroll_into_view_if_needed(timeout=match_timeout_ms)
                        except Exception:
                            pass
                        try:
                            return locator.click(**click_kwargs)
                        except Exception as exc:
                            last_exc = exc

                replacement_page = _ptr_resolve_active_page(page)
                if (
                    _ptr_page_attempt == 0
                    and current_page is not replacement_page
                    and _ptr_is_closed_target_error(last_exc)
                ):
                    page = replacement_page
                    continue
                break

            raise RuntimeError(
                f'Unable to click button target "{label}" using button/dialog fallbacks.'
            ) from last_exc


        def _ptr_select_search_trigger_option(
            page,
            title: str,
            option_label: str,
            option_kind: str = "text",
            option_exact: bool = False,
            **kwargs,
        ):
            def _ptr_locator_is_interactable(locator) -> bool:
                try:
                    return bool(
                        locator.evaluate(
                            """(node) => {
                                const isVisible = (candidate) => {
                                    if (!candidate) return false;
                                    const style = window.getComputedStyle(candidate);
                                    if (style.display === "none" || style.visibility === "hidden") return false;
                                    if (candidate.getAttribute && candidate.getAttribute("aria-hidden") === "true") return false;
                                    return !!(candidate.offsetWidth || candidate.offsetHeight || candidate.getClientRects().length);
                                };

                                const isDisabled = (candidate) => {
                                    if (!candidate) return false;
                                    if (candidate.hasAttribute?.("disabled")) return true;
                                    const ariaDisabled = String(candidate.getAttribute?.("aria-disabled") || "").toLowerCase();
                                    if (ariaDisabled === "true") return true;
                                    const cls = String(candidate.className || "");
                                    return cls.includes("AFDisabled") || cls.includes("oj-disabled") || cls.includes("oj-read-only");
                                };

                                if (!node || !isVisible(node) || isDisabled(node)) return false;
                                let parent = node.parentElement;
                                for (let depth = 0; parent && depth < 5; depth += 1) {
                                    if (isDisabled(parent)) return false;
                                    parent = parent.parentElement;
                                }
                                return true;
                            }"""
                        )
                    )
                except Exception:
                    return False

            def _ptr_option_candidates(current_page, preferred_only: bool = False):
                factories = []
                seen: set[tuple[str, bool]] = set()

                def _append(kind: str, exact: bool) -> None:
                    key = (kind, exact)
                    if key in seen:
                        return
                    seen.add(key)
                    if kind == "text":
                        factories.append(
                            (
                                f"text_{'exact' if exact else 'partial'}",
                                lambda kind=kind, exact=exact: current_page.get_by_text(
                                    option_label, exact=exact
                                ),
                            )
                        )
                        return
                    if kind in {"option", "cell", "gridcell"}:
                        factories.append(
                            (
                                f"{kind}_{'exact' if exact else 'partial'}",
                                lambda kind=kind, exact=exact: current_page.get_by_role(
                                    kind, name=option_label, exact=exact
                                ),
                            )
                        )

                preferred_kind = option_kind if option_kind in {"text", "option", "cell", "gridcell"} else "text"
                _append(preferred_kind, bool(option_exact))
                _append(preferred_kind, not bool(option_exact))

                if preferred_only:
                    return factories

                for kind in ("text", "option", "cell", "gridcell"):
                    _append(kind, True)
                for kind in ("text", "option", "cell", "gridcell"):
                    _append(kind, False)
                return factories

            def _ptr_find_visible_option_locator(current_page, preferred_only: bool = False):
                option_candidates = _ptr_option_candidates(current_page, preferred_only=preferred_only)

                for _ptr_strategy, factory in option_candidates:
                    try:
                        locator = factory()
                        count = min(locator.count(), 20)
                    except Exception:
                        continue
                    for idx in range(count):
                        candidate = locator.nth(idx)
                        if _ptr_locator_is_interactable(candidate):
                            return candidate
                return None

            def _ptr_click_search_trigger(current_page, timeout_ms: int):
                trigger_candidates = [
                    current_page.get_by_title(title, exact=True).first,
                    current_page.get_by_title(title, exact=False).first,
                    current_page.locator(f'a[title="{title}"]').first,
                    current_page.locator(f'[title="{title}"]').first,
                ]
                click_error = None
                for trigger_locator in trigger_candidates:
                    try:
                        trigger_locator.scroll_into_view_if_needed(timeout=timeout_ms)
                    except Exception:
                        pass
                    if not _ptr_locator_is_interactable(trigger_locator):
                        continue
                    try:
                        trigger_locator.click(timeout=timeout_ms)
                        return True
                    except Exception as exc:
                        click_error = exc
                if click_error is not None:
                    raise click_error
                return False

            try:
                primary_timeout_ms = max(
                    400,
                    int(os.getenv("PTR_PRIMARY_SEARCH_TRIGGER_TIMEOUT_MS", "1200")),
                )
            except Exception:
                primary_timeout_ms = 1200
            try:
                match_timeout_ms = max(
                    1000,
                    int(os.getenv("PTR_SEARCH_POPUP_TIMEOUT_MS", "10000")),
                )
            except Exception:
                match_timeout_ms = 10000
            primary_timeout_ms = min(primary_timeout_ms, match_timeout_ms)
            post_open_wait_ms = _ptr_wait_ms("PTR_SEARCH_POPUP_POST_OPEN_WAIT_MS", 700)
            fast_poll_wait_ms = _ptr_wait_ms("PTR_PRIMARY_SEARCH_TRIGGER_POLL_MS", 150)

            click_kwargs = dict(kwargs)
            click_kwargs.setdefault("timeout", match_timeout_ms)

            last_exc = None
            for _ptr_page_attempt in range(2):
                current_page = _ptr_resolve_active_page(page)

                option_locator = _ptr_find_visible_option_locator(current_page, preferred_only=True)
                if option_locator is not None:
                    try:
                        option_locator.scroll_into_view_if_needed(timeout=primary_timeout_ms)
                    except Exception:
                        pass
                    try:
                        option_locator.click(timeout=primary_timeout_ms)
                        return
                    except Exception as exc:
                        last_exc = exc

                opened = False
                try:
                    opened = _ptr_click_search_trigger(current_page, primary_timeout_ms)
                except Exception as exc:
                    last_exc = exc

                if opened:
                    try:
                        current_page.wait_for_timeout(min(post_open_wait_ms, primary_timeout_ms))
                    except Exception:
                        pass
                    fast_deadline = time.time() + (primary_timeout_ms / 1000.0)
                    while time.time() < fast_deadline:
                        option_locator = _ptr_find_visible_option_locator(current_page, preferred_only=True)
                        if option_locator is not None:
                            try:
                                option_locator.scroll_into_view_if_needed(timeout=primary_timeout_ms)
                            except Exception:
                                pass
                            try:
                                option_locator.click(timeout=primary_timeout_ms)
                                return
                            except Exception as exc:
                                last_exc = exc
                        try:
                            current_page.wait_for_timeout(fast_poll_wait_ms)
                        except Exception:
                            pass

                deadline = time.time() + (match_timeout_ms / 1000.0)
                last_open_attempt = time.time() if opened else 0.0
                while time.time() < deadline:
                    option_locator = _ptr_find_visible_option_locator(current_page)
                    if option_locator is not None:
                        try:
                            option_locator.scroll_into_view_if_needed(timeout=match_timeout_ms)
                        except Exception:
                            pass
                        option_locator.click(**click_kwargs)
                        return

                    clicked = False
                    if time.time() - last_open_attempt >= 0.8:
                        try:
                            clicked = _ptr_click_search_trigger(current_page, match_timeout_ms)
                            if clicked:
                                last_open_attempt = time.time()
                        except Exception as exc:
                            last_exc = exc

                    try:
                        current_page.wait_for_timeout(post_open_wait_ms if clicked else 200)
                    except Exception:
                        pass

                replacement_page = _ptr_resolve_active_page(page)
                if (
                    _ptr_page_attempt == 0
                    and current_page is not replacement_page
                    and _ptr_is_closed_target_error(last_exc)
                ):
                    page = replacement_page
                    continue
                break

            raise RuntimeError(
                f'Unable to select "{option_label}" from search trigger "{title}".'
            ) from last_exc


        def _ptr_select_search_popup_option(page, title: str, option_label: str, **kwargs):
            return _ptr_select_search_trigger_option(page, title, option_label, **kwargs)


        def _ptr_select_adf_menu_panel_option(page, trigger_label: str, option_label: str, trigger_kind: str = "title", **kwargs):
            _ptr_hardcoded_options = {
                "Complete and Review",
                "Complete and Close",
                "Save and Close",
                "Post to Ledger",
            }

            def _ptr_locator_is_interactable(locator) -> bool:
                try:
                    return bool(
                        locator.evaluate(
                            """(node) => {
                                const isVisible = (candidate) => {
                                    if (!candidate) return false;
                                    const style = window.getComputedStyle(candidate);
                                    if (style.display === "none" || style.visibility === "hidden") return false;
                                    if (candidate.getAttribute && candidate.getAttribute("aria-hidden") === "true") return false;
                                    return !!(candidate.offsetWidth || candidate.offsetHeight || candidate.getClientRects().length);
                                };

                                const isDisabled = (candidate) => {
                                    if (!candidate) return false;
                                    if (candidate.hasAttribute?.("disabled")) return true;
                                    const ariaDisabled = String(candidate.getAttribute?.("aria-disabled") || "").toLowerCase();
                                    if (ariaDisabled === "true") return true;
                                    const cls = String(candidate.className || "");
                                    return cls.includes("AFDisabled") || cls.includes("oj-disabled") || cls.includes("oj-read-only");
                                };

                                if (!node || !isVisible(node) || isDisabled(node)) return false;
                                let parent = node.parentElement;
                                for (let depth = 0; parent && depth < 5; depth += 1) {
                                    if (isDisabled(parent)) return false;
                                    parent = parent.parentElement;
                                }
                                return true;
                            }"""
                        )
                    )
                except Exception:
                    return False

            def _ptr_get_trigger_candidates(current_page):
                if trigger_kind == "title":
                    hardcoded_title_arrow_candidates = []
                    if trigger_label in {"Complete and Create Another", "Submit and Create Another"}:
                        hardcoded_title_arrow_candidates.extend(
                            [
                                current_page.locator(
                                    f'a[id$="newTrx::popEl"][title="{trigger_label}"]'
                                ).first,
                                current_page.locator(
                                    f'[id$="newTrx::popEl"][title="{trigger_label}"]'
                                ).first,
                            ]
                        )
                    elif trigger_label == "Save":
                        hardcoded_title_arrow_candidates.extend(
                            [
                                current_page.locator('a[id$="saveMenu::popEl"][title="Save"]').first,
                                current_page.locator('[id$="saveMenu::popEl"][title="Save"]').first,
                            ]
                        )

                    popel_exact = current_page.locator(
                        f'a[id$="::popEl"][title="{trigger_label}"]'
                    )
                    popel_any = current_page.locator(
                        f'[id$="::popEl"][title="{trigger_label}"]'
                    )
                    return hardcoded_title_arrow_candidates + [
                        # ADF split-button: target ONLY the dropdown arrow (::popEl), never
                        # the primary button action.
                        # Prefer the second matching arrow when duplicate title matches exist.
                        popel_exact.nth(1),
                        popel_exact.first,
                        popel_any.nth(1),
                        popel_any.first,
                    ]
                if trigger_kind == "link":
                    return [
                        current_page.get_by_role("menuitem", name=trigger_label, exact=True).first,
                        current_page.locator(f'[role="menuitem"][aria-label="{trigger_label}"]').first,
                        current_page.get_by_role("link", name=trigger_label, exact=True).first,
                        current_page.get_by_role("link", name=trigger_label, exact=False).first,
                    ]
                return [
                    current_page.get_by_role("button", name=trigger_label, exact=True).first,
                    current_page.get_by_role("button", name=trigger_label, exact=False).first,
                ]

            def _ptr_get_primary_trigger_candidates(current_page):
                if trigger_kind == "title":
                    return [
                        current_page.get_by_title(trigger_label, exact=True).first,
                        current_page.get_by_title(trigger_label, exact=False).first,
                        current_page.locator(f'a[title="{trigger_label}"]').first,
                        current_page.locator(f'[title="{trigger_label}"]').first,
                    ]
                if trigger_kind == "link":
                    return [
                        current_page.get_by_role("link", name=trigger_label, exact=True).first,
                        current_page.get_by_role("link", name=trigger_label, exact=False).first,
                        current_page.locator(f'[role="menuitem"][aria-label="{trigger_label}"]').first,
                    ]
                return [
                    current_page.get_by_role("button", name=trigger_label, exact=True).first,
                    current_page.get_by_role("button", name=trigger_label, exact=False).first,
                ]

            def _ptr_get_trigger_bound_scopes(current_page, trigger_locator):
                scopes = []
                if trigger_locator is None:
                    return scopes
                popup_id = ""
                try:
                    popup_id = str(
                        trigger_locator.evaluate(
                            """(node) => {
                                const getPopupId = (candidate) => {
                                    if (!candidate || !candidate.getAttribute) return "";
                                    return String(candidate.getAttribute("_afrpopid") || "");
                                };
                                let popup = getPopupId(node);
                                if (popup) return popup;
                                let parent = node.parentElement;
                                for (let depth = 0; parent && depth < 6; depth += 1) {
                                    popup = getPopupId(parent);
                                    if (popup) return popup;
                                    parent = parent.parentElement;
                                }
                                const nodeId = String(node.id || "");
                                if (nodeId.endsWith("::popEl")) {
                                    return nodeId.slice(0, -7);
                                }
                                return "";
                            }"""
                        )
                        or ""
                    ).strip()
                except Exception:
                    popup_id = ""
                if not popup_id:
                    return scopes
                popup_candidates = [
                    current_page.locator(f'xpath=//*[@id="{popup_id}"]').first,
                    current_page.locator(f'xpath=//*[@id="{popup_id}::menu"]').first,
                    current_page.locator(
                        f'xpath=//*[@id="{popup_id}"]//*[@role="menu"] | //*[@id="{popup_id}"]//table[@role="menu"]'
                    ).first,
                ]
                for candidate in popup_candidates:
                    if _ptr_locator_is_interactable(candidate):
                        scopes.append(candidate)
                return scopes

            def _ptr_get_menu_scopes(current_page, trigger_locator=None):
                scopes = []
                if trigger_locator is not None:
                    scopes.extend(_ptr_get_trigger_bound_scopes(current_page, trigger_locator))
                for scope in _ptr_get_visible_scopes(current_page):
                    if scope is current_page:
                        continue
                    scopes.append(scope)
                return scopes

            def _ptr_find_visible_option_locator(current_page, trigger_locator=None):
                scopes = _ptr_get_menu_scopes(current_page, trigger_locator)
                hardcoded_option_candidates = []
                if option_label == "Complete and Review":
                    hardcoded_option_candidates.extend(
                        [
                            current_page.locator('[id$="reviewBTN"][role="menuitem"]').first,
                            current_page.locator('[id$="reviewBTN"] td.xo2').first,
                        ]
                    )
                elif option_label == "Complete and Close":
                    hardcoded_option_candidates.extend(
                        [
                            current_page.locator('[id$="closeBTN"][role="menuitem"]').first,
                            current_page.locator('[id$="closeBTN"] td.xo2').first,
                        ]
                    )
                elif option_label == "Save and Close":
                    hardcoded_option_candidates.extend(
                        [
                            current_page.locator('[id$="cmi10"][role="menuitem"]').first,
                            current_page.locator('tr[role="menuitem"]:has(td.xo2:has-text("Save and Close"))').first,
                        ]
                    )

                for locator in hardcoded_option_candidates:
                    if _ptr_locator_is_interactable(locator):
                        return locator

                option_candidates = [
                    lambda scope: scope.get_by_role("menuitem", name=option_label, exact=True).first,
                    lambda scope: scope.locator(f'tr[role="menuitem"]:has-text("{option_label}")').first,
                    lambda scope: scope.locator(
                        f'xpath=.//td[contains(@class,"xo2") and normalize-space()="{option_label}"]/ancestor::tr[@role="menuitem"][1]'
                    ).first,
                    lambda scope: scope.locator(f'td.xo2:has-text("{option_label}")').first,
                    lambda scope: scope.locator(
                        f'xpath=.//*[@role="menuitem"][.//td[contains(@class,"xo2") and normalize-space()="{option_label}"]]'
                    ).first,
                    lambda scope: scope.locator(
                        f'xpath=.//td[contains(@class,"xo2") and normalize-space()="{option_label}"]'
                    ).first,
                    lambda scope: scope.get_by_text(option_label, exact=True).first,
                    lambda scope: scope.get_by_text(option_label, exact=False).first,
                ]

                for scope in scopes:
                    for factory in option_candidates:
                        try:
                            locator = factory(scope)
                        except Exception:
                            continue
                        if _ptr_locator_is_interactable(locator):
                                return locator

                # Last-resort global lookup for ADF menu panels rendered in detached
                # containers where trigger-bound scope resolution can miss.
                global_candidates = [
                    current_page.locator(
                        f'xpath=//tr[@role="menuitem"][.//td[contains(@class,"xo2") and normalize-space()="{option_label}"]]'
                    ).first,
                    current_page.locator(
                        f'xpath=//*[@role="menuitem" and normalize-space()="{option_label}" and ancestor::*[@role="menu" or contains(@class,"af_menu_popup")]]'
                    ).first,
                    current_page.locator(
                        f'xpath=//td[contains(@class,"xo2") and normalize-space()="{option_label}" and ancestor::*[@role="menu" or contains(@class,"af_menu_popup")]]'
                    ).first,
                    current_page.get_by_role("menuitem", name=option_label, exact=True).first,
                    current_page.get_by_text(option_label, exact=True).first,
                ]
                for locator in global_candidates:
                    if _ptr_locator_is_interactable(locator):
                        return locator
                return None

            def _ptr_click_option_via_trigger_panel_dom(current_page, trigger_locator):
                if trigger_locator is None:
                    return False
                try:
                    handle = trigger_locator.element_handle(timeout=1000)
                except Exception:
                    handle = None
                if handle is None:
                    return False
                try:
                    return bool(
                        current_page.evaluate(
                            r"""(payload) => {
                                const { triggerNode, optionLabel } = payload;
                                const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
                                const isVisible = (node) => {
                                    if (!node) return false;
                                    const style = window.getComputedStyle(node);
                                    if (style.display === "none" || style.visibility === "hidden") return false;
                                    if (node.getAttribute && node.getAttribute("aria-hidden") === "true") return false;
                                    return !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
                                };
                                const getPopupId = (node) => {
                                    if (!node) return "";
                                    const direct = String(node.getAttribute?.("_afrpopid") || "");
                                    if (direct) return direct;
                                    let parent = node.parentElement;
                                    for (let depth = 0; parent && depth < 6; depth += 1) {
                                        const value = String(parent.getAttribute?.("_afrpopid") || "");
                                        if (value) return value;
                                        parent = parent.parentElement;
                                    }
                                    const nodeId = String(node.id || "");
                                    if (nodeId.endsWith("::popEl")) return nodeId.slice(0, -7);
                                    return "";
                                };

                                const popupId = getPopupId(triggerNode);
                                if (!popupId) return false;
                                const roots = [
                                    document.getElementById(popupId),
                                    document.getElementById(`${popupId}::menu`),
                                ].filter(Boolean);
                                const wanted = normalize(optionLabel).toLowerCase();
                                for (const root of roots) {
                                    if (!isVisible(root)) continue;
                                    const candidates = root.querySelectorAll('[role="menuitem"], tr[role="menuitem"], td.xo2, .xo2');
                                    for (const node of candidates) {
                                        if (!isVisible(node)) continue;
                                        const text = normalize(node.innerText || node.textContent || "");
                                        if (!text) continue;
                                        if (text.toLowerCase() === wanted || text.toLowerCase().includes(wanted)) {
                                            const clickable =
                                                node.closest?.('[role="menuitem"]') ||
                                                node.closest?.('tr[role="menuitem"]') ||
                                                node;
                                            clickable.click?.();
                                            return true;
                                        }
                                    }
                                }
                                return false;
                            }""",
                            {"triggerNode": handle, "optionLabel": option_label},
                        )
                    )
                except Exception:
                    return False

            def _ptr_click_menu_option_locator(current_page, option_locator, timeout_ms: int):
                row_target = None
                cell_target = None
                click_targets = [option_locator]
                try:
                    row_target = option_locator.locator('xpath=ancestor-or-self::*[@role="menuitem"][1]').first
                    click_targets.append(row_target)
                except Exception:
                    pass
                try:
                    row_target = row_target or option_locator.locator('xpath=ancestor-or-self::tr[@role="menuitem"][1]').first
                    click_targets.append(row_target)
                except Exception:
                    pass
                try:
                    cell_target = option_locator.locator('xpath=ancestor-or-self::tr[@role="menuitem"][1]//td[contains(@class,"xo2")][1]').first
                    click_targets.append(cell_target)
                except Exception:
                    pass

                seen = set()
                for target in click_targets:
                    marker = id(target)
                    if marker in seen:
                        continue
                    seen.add(marker)
                    try:
                        target.scroll_into_view_if_needed(timeout=timeout_ms)
                    except Exception:
                        pass
                    try:
                        target.click(timeout=timeout_ms)
                        return True
                    except Exception:
                        pass
                    try:
                        target.click(timeout=timeout_ms, force=True)
                        return True
                    except Exception:
                        pass

                # ADF sometimes activates menu rows only via keyboard focus/enter.
                try:
                    keyboard_target = row_target or option_locator
                    keyboard_target.focus(timeout=timeout_ms)
                    current_page.keyboard.press("Enter")
                    return True
                except Exception:
                    pass
                try:
                    keyboard_target = row_target or option_locator
                    keyboard_target.focus(timeout=timeout_ms)
                    current_page.keyboard.press(" ")
                    return True
                except Exception:
                    pass

                try:
                    handle = option_locator.element_handle(timeout=1000)
                except Exception:
                    handle = None
                if handle is None:
                    return False

                try:
                    return bool(
                        current_page.evaluate(
                            """(node) => {
                                if (!node) return false;
                                const target =
                                    node.closest?.('[role="menuitem"]') ||
                                    node.closest?.('tr[role="menuitem"]') ||
                                    node;
                                const dispatch = (type) =>
                                    target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                                dispatch("mouseover");
                                dispatch("mousedown");
                                dispatch("mouseup");
                                target.click?.();
                                return true;
                            }""",
                            handle,
                        )
                    )
                except Exception:
                    return False

            def _ptr_try_hardcoded_split_button_option(current_page, before_signature) -> bool:
                if trigger_kind != "title":
                    return False
                fast_paths = {
                    ("Submit and Create Another", "Submit"): {
                        "trigger": 'a[id$="::popEl"][title="Submit and Create Another"], [id$="::popEl"][title="Submit and Create Another"]',
                        "options": [
                            'xpath=//tr[@role="menuitem"][.//td[contains(@class,"xo2") and normalize-space()="Submit"]]',
                            'xpath=//td[contains(@class,"xo2") and normalize-space()="Submit"]',
                        ],
                    },
                }
                fast_path = fast_paths.get((trigger_label, option_label))
                if fast_path is None:
                    return False

                def _ptr_dom_click_trigger() -> bool:
                    try:
                        return bool(
                            current_page.evaluate(
                                """(selector) => {
                                    const isVisible = (node) => {
                                        if (!node) return false;
                                        const style = window.getComputedStyle(node);
                                        if (style.display === "none" || style.visibility === "hidden") return false;
                                        if (node.getAttribute && node.getAttribute("aria-hidden") === "true") return false;
                                        return !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
                                    };
                                    const fire = (target, type, extra = {}) =>
                                        target.dispatchEvent(
                                            new MouseEvent(type, {
                                                bubbles: true,
                                                cancelable: true,
                                                view: window,
                                                ...extra,
                                            })
                                        );
                                    const trigger = Array.from(document.querySelectorAll(selector)).find(isVisible);
                                    if (!trigger) return false;
                                    try { trigger.focus?.(); } catch {}
                                    try { fire(trigger, "mouseover"); } catch {}
                                    try { fire(trigger, "mousedown", { buttons: 1 }); } catch {}
                                    try { fire(trigger, "mouseup", { buttons: 1 }); } catch {}
                                    try { trigger.click?.(); } catch {}
                                    return true;
                                }""",
                                fast_path["trigger"],
                            )
                        )
                    except Exception:
                        return False

                def _ptr_dom_click_option() -> bool:
                    try:
                        return bool(
                            current_page.evaluate(
                                """(optionLabel) => {
                                    const normalize = (value) =>
                                        String(value || "").replace(/\\s+/g, " ").trim().toLowerCase();
                                    const isVisible = (node) => {
                                        if (!node) return false;
                                        const style = window.getComputedStyle(node);
                                        if (style.display === "none" || style.visibility === "hidden") return false;
                                        if (node.getAttribute && node.getAttribute("aria-hidden") === "true") return false;
                                        return !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
                                    };
                                    const fire = (target, type, extra = {}) =>
                                        target.dispatchEvent(
                                            new MouseEvent(type, {
                                                bubbles: true,
                                                cancelable: true,
                                                view: window,
                                                ...extra,
                                            })
                                        );
                                    const wanted = normalize(optionLabel);
                                    const rows = Array.from(
                                        document.querySelectorAll('tr[role="menuitem"], [role="menuitem"], td.xo2')
                                    );
                                    for (const node of rows) {
                                        if (!isVisible(node)) continue;
                                        const text = normalize(node.innerText || node.textContent || "");
                                        if (!text) continue;
                                        if (text !== wanted && !text.includes(wanted)) continue;
                                        const row =
                                            node.closest?.('tr[role="menuitem"]') ||
                                            node.closest?.('[role="menuitem"]') ||
                                            node;
                                        const cell = row.querySelector?.("td.xo2") || node;
                                        const target = isVisible(cell) ? cell : row;
                                        try { row.focus?.(); } catch {}
                                        try { fire(target, "mouseover"); } catch {}
                                        try { fire(target, "mousedown", { buttons: 1 }); } catch {}
                                        try { fire(target, "mouseup", { buttons: 1 }); } catch {}
                                        try { target.click?.(); } catch {}
                                        try {
                                            row.dispatchEvent(
                                                new KeyboardEvent("keydown", {
                                                    key: "Enter",
                                                    code: "Enter",
                                                    bubbles: true,
                                                    cancelable: true,
                                                })
                                            );
                                        } catch {}
                                        try {
                                            row.dispatchEvent(
                                                new KeyboardEvent("keyup", {
                                                    key: "Enter",
                                                    code: "Enter",
                                                    bubbles: true,
                                                    cancelable: true,
                                                })
                                            );
                                        } catch {}
                                        return true;
                                    }
                                    return false;
                                }""",
                                option_label,
                            )
                        )
                    except Exception:
                        return False

                option_locator = None
                for option_selector in fast_path["options"]:
                    candidate = current_page.locator(option_selector).first
                    if _ptr_locator_is_interactable(candidate):
                        option_locator = candidate
                        break

                trigger_locator = current_page.locator(fast_path["trigger"]).first
                if option_locator is None:
                    if _ptr_dom_click_trigger():
                        try:
                            current_page.wait_for_timeout(
                                min(
                                    _ptr_wait_ms("PTR_ADF_SPLIT_BUTTON_POST_OPEN_WAIT_MS", 120),
                                    primary_timeout_ms,
                                )
                            )
                        except Exception:
                            pass
                        for option_selector in fast_path["options"]:
                            candidate = current_page.locator(option_selector).first
                            if _ptr_locator_is_interactable(candidate):
                                option_locator = candidate
                                break

                    if option_locator is None:
                        try:
                            trigger_locator.scroll_into_view_if_needed(timeout=primary_timeout_ms)
                        except Exception:
                            pass

                        open_attempted = False
                        for force_click in (False, True):
                            try:
                                trigger_locator.click(timeout=primary_timeout_ms, force=force_click)
                                open_attempted = True
                                break
                            except Exception:
                                continue

                        if not open_attempted:
                            return False

                        try:
                            current_page.wait_for_timeout(
                                min(
                                    _ptr_wait_ms("PTR_ADF_SPLIT_BUTTON_POST_OPEN_WAIT_MS", 120),
                                    primary_timeout_ms,
                                )
                            )
                        except Exception:
                            pass

                        for option_selector in fast_path["options"]:
                            candidate = current_page.locator(option_selector).first
                            if _ptr_locator_is_interactable(candidate):
                                option_locator = candidate
                                break

                        if option_locator is None:
                            return False

                if not _ptr_dom_click_option() and not _ptr_click_menu_option_locator(current_page, option_locator, primary_timeout_ms):
                    return False

                if _ptr_wait_for_menu_action_effect(current_page, before_signature, trigger_locator):
                    return True

                try:
                    option_still_visible = _ptr_find_visible_option_locator(current_page, trigger_locator) is not None
                except Exception:
                    option_still_visible = False
                return not option_still_visible

            def _ptr_click_hardcoded_adf_option_dom(current_page):
                if option_label not in _ptr_hardcoded_options:
                    return False
                try:
                    return bool(
                        current_page.evaluate(
                            r"""(payload) => {
                                const { optionLabel } = payload;
                                const normalize = (value) =>
                                    String(value || "").replace(/\\s+/g, " ").trim().toLowerCase();
                                const isVisible = (node) => {
                                    if (!node) return false;
                                    const style = window.getComputedStyle(node);
                                    if (style.display === "none" || style.visibility === "hidden") return false;
                                    if (node.getAttribute && node.getAttribute("aria-hidden") === "true") return false;
                                    return !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
                                };
                                const fireMouse = (target, type, extra = {}) =>
                                    target.dispatchEvent(
                                        new MouseEvent(type, {
                                            bubbles: true,
                                            cancelable: true,
                                            view: window,
                                            ...extra,
                                        })
                                    );
                                const activate = (node) => {
                                    if (!node || !isVisible(node)) return false;
                                    const row =
                                        node.closest?.('tr[role="menuitem"]') ||
                                        node.closest?.('[role="menuitem"]') ||
                                        node;
                                    if (!isVisible(row)) return false;
                                    row.scrollIntoView?.({ block: "center", inline: "nearest" });
                                    row.focus?.();
                                    const cell = row.querySelector?.("td.xo2") || node;
                                    const rect = row.getBoundingClientRect();
                                    const x = Math.floor(rect.left + Math.max(2, rect.width / 2));
                                    const y = Math.floor(rect.top + Math.max(2, Math.min(rect.height / 2, 12)));
                                    const hit = document.elementFromPoint(x, y);
                                    const clickTarget = hit && row.contains(hit) ? hit : cell;
                                    try { fireMouse(clickTarget, "mouseover"); } catch {}
                                    try { fireMouse(clickTarget, "mousedown", { buttons: 1 }); } catch {}
                                    try { fireMouse(clickTarget, "mouseup", { buttons: 1 }); } catch {}
                                    try { clickTarget.click?.(); } catch {}
                                    try {
                                        row.dispatchEvent(
                                            new KeyboardEvent("keydown", {
                                                key: "Enter",
                                                code: "Enter",
                                                bubbles: true,
                                                cancelable: true,
                                            })
                                        );
                                    } catch {}
                                    try {
                                        row.dispatchEvent(
                                            new KeyboardEvent("keyup", {
                                                key: "Enter",
                                                code: "Enter",
                                                bubbles: true,
                                                cancelable: true,
                                            })
                                        );
                                    } catch {}
                                    return true;
                                };

                                const wanted = normalize(optionLabel);
                                const hardcodedIds = {
                                    "complete and review": ["reviewBTN"],
                                    "complete and close": ["closeBTN"],
                                    "save and close": ["cmi10"],
                                };
                                const idSuffixes = hardcodedIds[wanted] || [];
                                for (const suffix of idSuffixes) {
                                    const row = document.querySelector(`tr[id$="${suffix}"][role="menuitem"], [role="menuitem"][id$="${suffix}"]`);
                                    if (activate(row)) return true;
                                }

                                const rows = Array.from(document.querySelectorAll('tr[role="menuitem"], [role="menuitem"]'));
                                for (const row of rows) {
                                    if (!isVisible(row)) continue;
                                    const rowText = normalize(row.innerText || row.textContent || "");
                                    const cell = row.querySelector?.("td.xo2");
                                    const cellText = normalize(cell?.innerText || cell?.textContent || "");
                                    if (!rowText && !cellText) continue;
                                    if (rowText === wanted || cellText === wanted || rowText.includes(wanted) || cellText.includes(wanted)) {
                                        if (activate(cell || row)) return true;
                                    }
                                }
                                return false;
                            }""",
                            {"optionLabel": option_label},
                        )
                    )
                except Exception:
                    return False

            def _ptr_get_menu_action_signature(current_page):
                try:
                    return current_page.evaluate(
                        r"""() => {
                            const isVisible = (node) => {
                                if (!node) return false;
                                const style = window.getComputedStyle(node);
                                if (style.display === "none" || style.visibility === "hidden") return false;
                                if (node.getAttribute && node.getAttribute("aria-hidden") === "true") return false;
                                return !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
                            };

                            const normalize = (value) =>
                                String(value || "").replace(/\\s+/g, " ").trim();

                            const values = [window.location.pathname || "", window.location.search || ""];
                            const selectors = [
                                'h1',
                                'h2',
                                '[role="heading"]',
                                'button',
                                '[role="button"]',
                                'a',
                                '[role="menuitem"]',
                                '.xrk',
                                '.xo2',
                                '.oj-dialog',
                                '.af_menu_popup',
                            ];

                            for (const selector of selectors) {
                                const nodes = Array.from(document.querySelectorAll(selector)).filter(isVisible).slice(0, 12);
                                for (const node of nodes) {
                                    const text = normalize(node.innerText || node.textContent || node.getAttribute?.("aria-label") || node.getAttribute?.("title") || "");
                                    if (text) values.push(text);
                                }
                            }

                            return normalize(values.join(" | ")).slice(0, 1200);
                        }"""
                    )
                except Exception:
                    return ""

            def _ptr_is_menu_action_busy(current_page):
                try:
                    return bool(
                        current_page.evaluate(
                            """() => {
                                const isVisible = (node) => {
                                    if (!node) return false;
                                    const style = window.getComputedStyle(node);
                                    if (style.display === "none" || style.visibility === "hidden") return false;
                                    if (node.getAttribute && node.getAttribute("aria-hidden") === "true") return false;
                                    return !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
                                };

                                const selectors = [
                                    '[role="progressbar"]',
                                    '[aria-busy="true"]',
                                    '.oj-dialog',
                                    '.oj-progress-bar',
                                    '.oj-progress-circle',
                                    'oj-progress-bar',
                                    'oj-progress-circle',
                                    '.AFBlockingGlassPane',
                                    '[class*="progress"]',
                                ];

                                return Array.from(document.querySelectorAll(selectors.join(","))).some(isVisible);
                            }"""
                        )
                    )
                except Exception:
                    return False

            def _ptr_wait_for_menu_action_effect(current_page, before_signature, trigger_locator=None):
                effect_timeout_ms = _ptr_wait_ms("PTR_MENU_PANEL_EFFECT_TIMEOUT_MS", 8000)
                deadline = time.time() + (effect_timeout_ms / 1000.0)
                saw_busy = False

                while time.time() < deadline:
                    is_busy = _ptr_is_menu_action_busy(current_page)
                    if is_busy:
                        saw_busy = True

                    after_signature = _ptr_get_menu_action_signature(current_page)
                    option_still_visible = _ptr_find_visible_option_locator(current_page, trigger_locator) is not None

                    if before_signature and after_signature and after_signature != before_signature:
                        if not is_busy:
                            return True

                    # For split-button/menu-panel actions, menu dismissal itself is a
                    # strong success signal even when URL/title do not change instantly.
                    if not option_still_visible and not is_busy:
                        return True

                    if saw_busy and not is_busy and not option_still_visible:
                        return True

                    try:
                        current_page.wait_for_timeout(200)
                    except Exception:
                        pass

                return False

            try:
                primary_timeout_ms = max(
                    400,
                    int(os.getenv("PTR_PRIMARY_ADF_MENU_PANEL_TIMEOUT_MS", "1200")),
                )
            except Exception:
                primary_timeout_ms = 1200
            try:
                match_timeout_ms = max(
                    1000,
                    int(os.getenv("PTR_ADF_MENU_PANEL_TIMEOUT_MS", "10000")),
                )
            except Exception:
                match_timeout_ms = 10000
            primary_timeout_ms = min(primary_timeout_ms, match_timeout_ms)

            click_kwargs = dict(kwargs)
            click_kwargs.setdefault("timeout", match_timeout_ms)

            last_exc = None
            for _ptr_page_attempt in range(2):
                current_page = _ptr_resolve_active_page(page)
                before_signature = _ptr_get_menu_action_signature(current_page)
                primary_trigger_candidates = _ptr_get_primary_trigger_candidates(current_page)
                trigger_candidates = _ptr_get_trigger_candidates(current_page)
                deadline = time.time() + (match_timeout_ms / 1000.0)
                active_trigger_locator = None
                tried_primary_codegen_click = False

                if _ptr_try_hardcoded_split_button_option(current_page, before_signature):
                    return

                while time.time() < deadline:
                    option_locator = _ptr_find_visible_option_locator(current_page, active_trigger_locator)
                    if option_locator is None:
                        if not tried_primary_codegen_click:
                            tried_primary_codegen_click = True
                            primary_click_kwargs = dict(click_kwargs)
                            primary_click_kwargs["timeout"] = min(
                                int(primary_click_kwargs.get("timeout", match_timeout_ms)),
                                primary_timeout_ms,
                            )
                            for trigger_locator in primary_trigger_candidates:
                                try:
                                    trigger_locator.scroll_into_view_if_needed(timeout=primary_timeout_ms)
                                except Exception:
                                    pass
                                if not _ptr_locator_is_interactable(trigger_locator):
                                    continue
                                try:
                                    trigger_locator.click(**primary_click_kwargs)
                                except Exception as exc:
                                    last_exc = exc
                                    continue
                                try:
                                    current_page.wait_for_timeout(
                                        min(
                                            _ptr_wait_ms("PTR_ADF_MENU_PANEL_POST_OPEN_WAIT_MS", 250),
                                            primary_timeout_ms,
                                        )
                                    )
                                except Exception:
                                    pass
                                option_locator = _ptr_find_visible_option_locator(current_page, trigger_locator)
                                if option_locator is not None:
                                    active_trigger_locator = trigger_locator
                                    break

                        opened = False
                        if option_locator is None:
                            for trigger_locator in trigger_candidates:
                                try:
                                    trigger_locator.scroll_into_view_if_needed(timeout=match_timeout_ms)
                                except Exception:
                                    pass
                                # ADF ::popEl elements are often zero-dimensional anchors;
                                # skip the interactability gate for them and rely on force click.
                                is_pop_el = False
                                try:
                                    el_id = trigger_locator.get_attribute("id", timeout=1000) or ""
                                    is_pop_el = el_id.endswith("::popEl")
                                except Exception:
                                    pass
                                if not is_pop_el and not _ptr_locator_is_interactable(trigger_locator):
                                    continue
                                try:
                                    force_kwargs = dict(click_kwargs)
                                    if is_pop_el:
                                        force_kwargs["force"] = True
                                    trigger_locator.click(**force_kwargs)
                                    opened = True
                                    active_trigger_locator = trigger_locator
                                except Exception as exc:
                                    last_exc = exc
                                    # Retry with force=True to bypass actionability checks
                                    try:
                                        force_kwargs = dict(click_kwargs)
                                        force_kwargs["force"] = True
                                        trigger_locator.click(**force_kwargs)
                                        opened = True
                                        active_trigger_locator = trigger_locator
                                    except Exception:
                                        try:
                                            trigger_locator.focus(timeout=match_timeout_ms)
                                        except Exception:
                                            continue
                                        for key in ("ArrowDown", "Enter"):
                                            try:
                                                current_page.keyboard.press(key)
                                                opened = True
                                                active_trigger_locator = trigger_locator
                                                break
                                            except Exception as keyboard_exc:
                                                last_exc = keyboard_exc
                                        if not opened:
                                            continue

                                try:
                                    current_page.wait_for_timeout(_ptr_wait_ms("PTR_ADF_MENU_PANEL_POST_OPEN_WAIT_MS", 250))
                                except Exception:
                                    pass

                                option_locator = _ptr_find_visible_option_locator(current_page, trigger_locator)
                                if option_locator is not None:
                                    active_trigger_locator = trigger_locator
                                    break

                        # JS-based fallback: target the ADF split-button arrow only.
                        # Do not click the main button text for title-triggered menus.
                        if option_locator is None and trigger_kind == "title":
                            try:
                                current_page.evaluate(
                                    """(label) => {
                                        const isVisible = (node) => {
                                            if (!node) return false;
                                            const style = window.getComputedStyle(node);
                                            if (style.display === "none" || style.visibility === "hidden") return false;
                                            if (node.getAttribute && node.getAttribute("aria-hidden") === "true") return false;
                                            return !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
                                        };
                                        const popEls = Array.from(
                                            document.querySelectorAll(
                                                'a[id$="::popEl"][title="' + label + '"], [id$="::popEl"][title="' + label + '"]'
                                            )
                                        ).filter(isVisible);
                                        const preferred = popEls[1] || popEls[0];
                                        if (preferred) { preferred.click(); return; }
                                    }""",
                                    trigger_label,
                                )
                                current_page.wait_for_timeout(_ptr_wait_ms("PTR_ADF_MENU_PANEL_POST_OPEN_WAIT_MS", 250))
                                option_locator = _ptr_find_visible_option_locator(current_page, active_trigger_locator)
                            except Exception:
                                pass

                        if option_locator is None:
                            try:
                                current_page.wait_for_timeout(150)
                            except Exception:
                                pass
                            continue

                    clicked_option = False
                    try:
                        clicked_option = _ptr_click_menu_option_locator(current_page, option_locator, match_timeout_ms)
                    except Exception as exc:
                        last_exc = exc
                        clicked_option = False
                    if not clicked_option:
                        last_exc = RuntimeError(
                            f'Unable to click menu option "{option_label}" from "{trigger_label}".'
                        )
                        if _ptr_click_hardcoded_adf_option_dom(current_page):
                            option_still_visible = _ptr_find_visible_option_locator(current_page, active_trigger_locator) is not None
                            if not option_still_visible:
                                return
                            if _ptr_wait_for_menu_action_effect(current_page, before_signature, active_trigger_locator):
                                return
                        if _ptr_click_option_via_trigger_panel_dom(current_page, active_trigger_locator):
                            if _ptr_wait_for_menu_action_effect(current_page, before_signature, active_trigger_locator):
                                return
                        try:
                            current_page.wait_for_timeout(150)
                        except Exception:
                            pass
                        continue

                    if _ptr_wait_for_menu_action_effect(current_page, before_signature, active_trigger_locator):
                        return

                    # Hardcoded Oracle ADF menu options frequently execute an in-place
                    # action without URL/title signature change. If the option is no
                    # longer visible, treat it as success.
                    if option_label in _ptr_hardcoded_options:
                        try:
                            option_still_visible = _ptr_find_visible_option_locator(current_page, active_trigger_locator) is not None
                        except Exception:
                            option_still_visible = False
                        if not option_still_visible:
                            return

                    # Some ADF menu rows render visible text but require a stronger
                    # row-level dispatch than the initial locator click.
                    retried = False
                    try:
                        force_kwargs = dict(click_kwargs)
                        force_kwargs["force"] = True
                        option_locator.click(**force_kwargs)
                        retried = True
                    except Exception:
                        pass
                    if not retried:
                        retried = _ptr_click_option_via_trigger_panel_dom(current_page, active_trigger_locator)
                    if retried and _ptr_wait_for_menu_action_effect(current_page, before_signature, active_trigger_locator):
                        return

                    last_exc = RuntimeError(
                        f'Menu panel option "{option_label}" from "{trigger_label}" did not trigger a visible page change.'
                    )
                    try:
                        current_page.wait_for_timeout(300)
                    except Exception:
                        pass

                replacement_page = _ptr_resolve_active_page(page)
                if (
                    _ptr_page_attempt == 0
                    and current_page is not replacement_page
                    and _ptr_is_closed_target_error(last_exc)
                ):
                    page = replacement_page
                    continue
                break

            raise RuntimeError(
                f'Unable to select ADF menu panel option "{option_label}" from "{trigger_label}".'
            ) from last_exc


        def _ptr_select_combobox_option(page, label: str, option_label: str, **kwargs):
            def _ptr_normalize(value) -> str:
                return " ".join(str(value or "").lower().split())

            def _ptr_combobox_click_locator(current_page):
                return current_page.locator(
                    'input[role="combobox"], [role="combobox"], '
                    'oj-select-single .oj-searchselect-main-field, '
                    'oj-select-single .oj-searchselect-arrow, '
                    'oj-select-single .oj-searchselect-open-icon, '
                    'oj-select-single .oj-searchselect-input'
                )

            def _ptr_collect_combobox_candidates(current_page):
                locator = _ptr_combobox_click_locator(current_page)
                try:
                    count = min(locator.count(), 120)
                except Exception:
                    return []
                candidates = []
                for idx in range(count):
                    try:
                        metadata = locator.nth(idx).evaluate(
                            """(node) => {
                                const values = [];
                                const push = (value) => {
                                    const text = String(value || "").trim();
                                    if (text) values.push(text);
                                };
                                push(node.getAttribute("aria-label"));
                                push(node.getAttribute("placeholder"));
                                push(node.getAttribute("name"));
                                push(node.id);
                                push(node.getAttribute("role"));

                                const labelledBy = String(
                                    node.getAttribute("aria-labelledby")
                                    || node.getAttribute("labelled-by")
                                    || ""
                                ).trim();
                                if (labelledBy) {
                                    for (const id of labelledBy.split(/\\\\s+/)) {
                                        const labelNode = document.getElementById(id);
                                        if (labelNode) {
                                            push(labelNode.innerText);
                                            push(labelNode.textContent);
                                        }
                                    }
                                }

                                const owner = node.closest("oj-select-single, oj-input-date");
                                if (owner) {
                                    push(owner.getAttribute("label-hint"));
                                    push(owner.getAttribute("aria-label"));
                                    push(owner.getAttribute("labelled-by"));
                                    push(owner.id);

                                    const ownerLabelledBy = String(
                                        owner.getAttribute("aria-labelledby")
                                        || owner.getAttribute("labelled-by")
                                        || ""
                                    ).trim();
                                    if (ownerLabelledBy) {
                                        for (const id of ownerLabelledBy.split(/\\\\s+/)) {
                                            const labelNode = document.getElementById(id);
                                            if (labelNode) {
                                                push(labelNode.innerText);
                                                push(labelNode.textContent);
                                            }
                                        }
                                    }

                                    owner.querySelectorAll("label, [id$='|hint'], [id$='-label'], .oj-label-group")
                                        .forEach((labelNode) => {
                                            push(labelNode.innerText);
                                            push(labelNode.textContent);
                                        });
                                }

                                let parent = node;
                                for (let depth = 0; parent && depth < 4; depth += 1) {
                                    push(parent.getAttribute && parent.getAttribute("aria-label"));
                                    push(parent.getAttribute && parent.getAttribute("label-hint"));
                                    push(parent.innerText);
                                    push(parent.textContent);
                                    parent = parent.parentElement;
                                }

                                return values;
                            }"""
                        )
                    except Exception:
                        continue
                    haystack = _ptr_normalize(" ".join(metadata))
                    candidates.append((idx, haystack))
                return candidates

            def _ptr_click_combobox(current_page, target_label: str):
                combobox_candidates = [
                    ("role_combobox_exact", current_page.get_by_role("combobox", name=target_label, exact=True).first),
                    ("role_combobox_partial", current_page.get_by_role("combobox", name=target_label, exact=False).first),
                    ("label_exact", current_page.get_by_label(target_label, exact=True).first),
                    ("label_partial", current_page.get_by_label(target_label, exact=False).first),
                    (
                        "aria_label",
                        current_page.locator(
                            f'[aria-label="{target_label}"], [aria-label*="{target_label}"]'
                        ).first,
                    ),
                    (
                        "oj_has_text_wrapper",
                        current_page.locator(
                            f'oj-select-single:has-text("{target_label}") .oj-searchselect-main-field, '
                            f'oj-select-single:has-text("{target_label}") .oj-searchselect-arrow, '
                            f'oj-select-single:has-text("{target_label}") input[role="combobox"]'
                        ).first,
                    ),
                    (
                        "oj_label_hint_input",
                        current_page.locator(
                            f'oj-select-single[label-hint="{target_label}"] input[role="combobox"], '
                            f'oj-select-single[label-hint="{target_label}"] .oj-searchselect-arrow'
                        ).first,
                    ),
                ]

                for _ptr_strategy, combobox in combobox_candidates:
                    try:
                        combobox.scroll_into_view_if_needed(timeout=match_timeout_ms)
                    except Exception:
                        pass
                    try:
                        combobox.click(**click_kwargs)
                        return
                    except Exception as exc:
                        nonlocal last_exc
                        last_exc = exc

                normalized_label = _ptr_normalize(target_label)
                label_tokens = [token for token in re.findall(r"[a-z0-9]+", normalized_label) if len(token) > 1]
                matched_candidates = []
                for idx, haystack in _ptr_collect_combobox_candidates(current_page):
                    score = 0
                    if normalized_label and normalized_label in haystack:
                        score += len(label_tokens) + 1
                    score += sum(1 for token in label_tokens if token in haystack)
                    if score > 0:
                        matched_candidates.append((score, idx))

                matched_candidates.sort(reverse=True)
                combobox_locator = _ptr_combobox_click_locator(current_page)
                for _score, idx in matched_candidates:
                    locator = combobox_locator.nth(idx)
                    try:
                        locator.scroll_into_view_if_needed(timeout=match_timeout_ms)
                    except Exception:
                        pass
                    try:
                        locator.click(**click_kwargs)
                        return
                    except Exception as exc:
                        last_exc = exc

                raise RuntimeError(f'Unable to open combobox "{target_label}".') from last_exc

            try:
                match_timeout_ms = max(
                    1000,
                    int(os.getenv("PTR_COMBOBOX_TIMEOUT_MS", "8000")),
                )
            except Exception:
                match_timeout_ms = 8000

            click_kwargs = dict(kwargs)
            click_kwargs.setdefault("timeout", match_timeout_ms)

            last_exc = None
            for _ptr_page_attempt in range(2):
                current_page = _ptr_resolve_active_page(page)
                option_candidates = [
                    ("role_option_exact", lambda: current_page.get_by_role("option", name=option_label, exact=True).first),
                    ("role_cell_exact", lambda: current_page.get_by_role("cell", name=option_label, exact=True).first),
                    ("role_gridcell_exact", lambda: current_page.get_by_role("gridcell", name=option_label, exact=True).first),
                    ("text_exact", lambda: current_page.get_by_text(option_label, exact=True).first),
                    ("role_option_partial", lambda: current_page.get_by_role("option", name=option_label, exact=False).first),
                    ("role_cell_partial", lambda: current_page.get_by_role("cell", name=option_label, exact=False).first),
                    ("role_gridcell_partial", lambda: current_page.get_by_role("gridcell", name=option_label, exact=False).first),
                    ("text_partial", lambda: current_page.get_by_text(option_label, exact=False).first),
                ]

                try:
                    _ptr_click_combobox(current_page, label)
                except Exception as exc:
                    last_exc = exc
                    replacement_page = _ptr_resolve_active_page(page)
                    if (
                        _ptr_page_attempt == 0
                        and current_page is not replacement_page
                        and _ptr_is_closed_target_error(last_exc)
                    ):
                        page = replacement_page
                        continue
                    break

                try:
                    current_page.wait_for_timeout(300)
                except Exception:
                    pass

                for _ptr_option_strategy, locator_factory in option_candidates:
                    option_locator = locator_factory()
                    try:
                        option_locator.scroll_into_view_if_needed(timeout=match_timeout_ms)
                    except Exception:
                        pass
                    try:
                        option_locator.click(**click_kwargs)
                        return
                    except Exception as exc:
                        last_exc = exc

                replacement_page = _ptr_resolve_active_page(page)
                if (
                    _ptr_page_attempt == 0
                    and current_page is not replacement_page
                    and _ptr_is_closed_target_error(last_exc)
                ):
                    page = replacement_page
                    continue
                break

            raise RuntimeError(
                f'Unable to select combobox option "{option_label}" for "{label}".'
            ) from last_exc


        def _ptr_click_combobox(page, label: str, **kwargs):
            try:
                match_timeout_ms = max(
                    1000,
                    int(os.getenv("PTR_COMBOBOX_TIMEOUT_MS", "8000")),
                )
            except Exception:
                match_timeout_ms = 8000

            click_kwargs = dict(kwargs)
            click_kwargs.setdefault("timeout", match_timeout_ms)

            last_exc = None
            for _ptr_page_attempt in range(2):
                current_page = _ptr_resolve_active_page(page)
                try:
                    _ptr_select_combobox_option.__closure__  # keep helper block structure stable
                except Exception:
                    pass
                try:
                    # Reuse the open-path logic inside the selection helper by attempting
                    # to open the combobox without selecting any option.
                    def _ptr_open_only():
                        def _ptr_normalize(value) -> str:
                            return " ".join(str(value or "").lower().split())

                        combobox_locator = current_page.locator(
                            'input[role="combobox"], [role="combobox"], '
                            'oj-select-single .oj-searchselect-main-field, '
                            'oj-select-single .oj-searchselect-arrow, '
                            'oj-select-single .oj-searchselect-open-icon, '
                            'oj-select-single .oj-searchselect-input'
                        )

                        direct_candidates = [
                            current_page.get_by_role("combobox", name=label, exact=True).first,
                            current_page.get_by_role("combobox", name=label, exact=False).first,
                            current_page.get_by_label(label, exact=True).first,
                            current_page.get_by_label(label, exact=False).first,
                            current_page.locator(
                                f'[aria-label="{label}"], [aria-label*="{label}"]'
                            ).first,
                            current_page.locator(
                                f'oj-select-single:has-text("{label}") .oj-searchselect-main-field, '
                                f'oj-select-single:has-text("{label}") .oj-searchselect-arrow, '
                                f'oj-select-single:has-text("{label}") input[role="combobox"]'
                            ).first,
                            current_page.locator(
                                f'oj-select-single[label-hint="{label}"] input[role="combobox"], '
                                f'oj-select-single[label-hint="{label}"] .oj-searchselect-arrow'
                            ).first,
                        ]

                        for locator in direct_candidates:
                            try:
                                locator.scroll_into_view_if_needed(timeout=match_timeout_ms)
                            except Exception:
                                pass
                            try:
                                locator.click(**click_kwargs)
                                return
                            except Exception as exc:
                                nonlocal last_exc
                                last_exc = exc

                        label_tokens = [token for token in re.findall(r"[a-z0-9]+", _ptr_normalize(label)) if len(token) > 1]
                        matched_candidates = []
                        try:
                            count = min(combobox_locator.count(), 120)
                        except Exception:
                            count = 0

                        for idx in range(count):
                            try:
                                haystack = combobox_locator.nth(idx).evaluate(
                                    """(node) => {
                                        const values = [];
                                        const push = (value) => {
                                            const text = String(value || "").trim();
                                            if (text) values.push(text);
                                        };
                                        push(node.getAttribute("aria-label"));
                                        push(node.getAttribute("placeholder"));
                                        push(node.id);
                                        const owner = node.closest("oj-select-single, oj-input-date");
                                        if (owner) {
                                            push(owner.getAttribute("label-hint"));
                                            push(owner.innerText);
                                            push(owner.textContent);
                                        }
                                        let parent = node;
                                        for (let depth = 0; parent && depth < 4; depth += 1) {
                                            push(parent.getAttribute && parent.getAttribute("label-hint"));
                                            push(parent.innerText);
                                            push(parent.textContent);
                                            parent = parent.parentElement;
                                        }
                                        return values.join(" ").toLowerCase();
                                    }"""
                                )
                            except Exception:
                                continue
                            score = 0
                            normalized_label = _ptr_normalize(label)
                            if normalized_label and normalized_label in haystack:
                                score += len(label_tokens) + 1
                            score += sum(1 for token in label_tokens if token in haystack)
                            if score > 0:
                                matched_candidates.append((score, idx))

                        matched_candidates.sort(reverse=True)
                        for _score, idx in matched_candidates:
                            locator = combobox_locator.nth(idx)
                            try:
                                locator.scroll_into_view_if_needed(timeout=match_timeout_ms)
                            except Exception:
                                pass
                            try:
                                locator.click(**click_kwargs)
                                return
                            except Exception as exc:
                                last_exc = exc

                        raise RuntimeError(f'Unable to open combobox "{label}".') from last_exc

                    _ptr_open_only()
                    return
                except Exception as exc:
                    last_exc = exc
                    replacement_page = _ptr_resolve_active_page(page)
                    if (
                        _ptr_page_attempt == 0
                        and current_page is not replacement_page
                        and _ptr_is_closed_target_error(last_exc)
                    ):
                        page = replacement_page
                        continue
                    break

            raise RuntimeError(f'Unable to click combobox "{label}".') from last_exc


        def _ptr_click_outside_control(current_page, control_locator, *, timeout_ms: int) -> None:
            viewport = None
            try:
                viewport = current_page.viewport_size
            except Exception:
                viewport = None

            candidate_positions = []
            try:
                box = control_locator.bounding_box()
            except Exception:
                box = None

            if box:
                center_x = box["x"] + (box["width"] / 2)
                center_y = box["y"] + min(box["height"] / 2, 24)
                candidate_positions.append(
                    {
                        "x": max(8, box["x"] - 16),
                        "y": max(8, center_y),
                    }
                )
                if viewport and box["x"] + box["width"] + 16 < viewport.get("width", 0):
                    candidate_positions.append(
                        {
                            "x": min(viewport.get("width", 0) - 8, box["x"] + box["width"] + 16),
                            "y": max(8, center_y),
                        }
                    )
                    candidate_positions.append(
                        {
                            "x": max(8, min(viewport.get("width", 0) - 8, center_x)),
                            "y": max(
                                8,
                                min(
                                    viewport.get("height", 0) - 8,
                                    box["y"] + box["height"] + 56,
                                ),
                            ),
                        }
                    )
                if viewport:
                    candidate_positions.append(
                        {
                            "x": max(8, min(viewport.get("width", 0) / 2, box["x"] + 24)),
                            "y": max(8, min(viewport.get("height", 0) - 8, box["y"] + box["height"] + 24)),
                        }
                    )

            if viewport:
                candidate_positions.extend(
                    [
                        {"x": 24, "y": max(24, viewport.get("height", 0) - 40)},
                        {"x": max(24, viewport.get("width", 0) / 2), "y": max(24, viewport.get("height", 0) - 40)},
                    ]
                )

            last_exc = None
            for position in candidate_positions:
                try:
                    current_page.mouse.click(position["x"], position["y"], timeout=timeout_ms)
                    return
                except Exception as exc:
                    last_exc = exc

            if last_exc:
                raise last_exc


        def _ptr_pick_date_via_icon(page, title: str, day_label: str, **kwargs):
            try:
                match_timeout_ms = max(
                    1000,
                    int(os.getenv("PTR_DATE_PICKER_TIMEOUT_MS", "10000")),
                )
            except Exception:
                match_timeout_ms = 10000
            post_select_wait_ms = _ptr_wait_ms("PTR_DATE_POST_SELECT_WAIT_MS", 6000)

            click_kwargs = dict(kwargs)
            click_kwargs.setdefault("timeout", match_timeout_ms)

            last_exc = None
            for _ptr_page_attempt in range(2):
                current_page = _ptr_resolve_active_page(page)
                icon_candidates = [
                    current_page.get_by_title(title, exact=True).first,
                    current_page.get_by_title(title, exact=False).first,
                    current_page.locator(f'[title="{title}"]').first,
                ]
                icon_locator = None
                input_locator = None
                control_locator = None
                popup_locator = None

                for candidate in icon_candidates:
                    try:
                        candidate.scroll_into_view_if_needed(timeout=match_timeout_ms)
                    except Exception:
                        pass
                    try:
                        candidate.count()
                    except Exception:
                        continue
                    icon_locator = candidate
                    control_locator = candidate.locator('xpath=ancestor::oj-input-date[1]').first
                    input_locator = control_locator.locator('input[role="combobox"], input').first
                    popup_locator = control_locator.locator('.oj-datepicker-popup').first
                    break

                if icon_locator is None or input_locator is None or control_locator is None:
                    last_exc = RuntimeError(f'Unable to locate date picker icon "{title}".')
                    break

                try:
                    icon_locator.click(**click_kwargs)
                except Exception as exc:
                    last_exc = exc
                    replacement_page = _ptr_resolve_active_page(page)
                    if (
                        _ptr_page_attempt == 0
                        and current_page is not replacement_page
                        and _ptr_is_closed_target_error(last_exc)
                    ):
                        page = replacement_page
                        continue
                    break

                try:
                    current_page.wait_for_timeout(200)
                except Exception:
                    pass

                def _ptr_wait_for_visible_date_value(timeout_ms: int, *, settle_ms: int = 500) -> str:
                    deadline = time.time() + (timeout_ms / 1000.0)
                    stable_since = None
                    stable_value = ""
                    last_seen = ""
                    while time.time() < deadline:
                        try:
                            current_value = str(input_locator.input_value() or "").strip()
                        except Exception:
                            current_value = ""
                        if current_value:
                            last_seen = current_value
                            if stable_since is None or current_value != stable_value:
                                stable_since = time.time()
                                stable_value = current_value
                            elif (time.time() - stable_since) * 1000 >= settle_ms:
                                return current_value
                        else:
                            stable_since = None
                            stable_value = ""
                        try:
                            current_page.wait_for_timeout(100)
                        except Exception:
                            break
                    return last_seen

                day_candidates = [
                    current_page.get_by_role("button", name=day_label, exact=True).first,
                    current_page.get_by_role("gridcell", name=day_label, exact=True).first,
                    current_page.get_by_role("cell", name=day_label, exact=True).first,
                    current_page.get_by_text(day_label, exact=True).first,
                ]
                selected_day = False
                for day_locator in day_candidates:
                    try:
                        day_locator.scroll_into_view_if_needed(timeout=match_timeout_ms)
                    except Exception:
                        pass
                    try:
                        day_locator.click(**click_kwargs)
                        selected_day = True
                        break
                    except Exception as exc:
                        last_exc = exc
                if not selected_day:
                    break

                if popup_locator is not None:
                    try:
                        popup_locator.wait_for(state="hidden", timeout=min(match_timeout_ms, 2500))
                    except Exception:
                        try:
                            current_page.wait_for_timeout(250)
                        except Exception:
                            pass

                selected_value = _ptr_wait_for_visible_date_value(
                    min(match_timeout_ms, 2500),
                    settle_ms=350,
                )
                if not selected_value:
                    try:
                        selected_value = str(input_locator.input_value() or "").strip()
                    except Exception as exc:
                        last_exc = exc
                        selected_value = ""

                if not selected_value:
                    try:
                        _ptr_click_outside_control(
                            current_page,
                            control_locator,
                            timeout_ms=match_timeout_ms,
                        )
                    except Exception as exc:
                        last_exc = exc

                    selected_value = _ptr_wait_for_visible_date_value(
                        min(match_timeout_ms, 2000),
                        settle_ms=350,
                    )

                if not selected_value:
                    last_exc = RuntimeError(
                        f'Date selection did not populate the input after clicking day "{day_label}".'
                    )
                    break

                persisted_value = ""

                try:
                    current_page.keyboard.press("Tab")
                    if post_select_wait_ms > 0:
                        current_page.wait_for_timeout(post_select_wait_ms)
                    persisted_value = _ptr_wait_for_visible_date_value(
                        min(match_timeout_ms, 1500),
                        settle_ms=900,
                    )
                except Exception as exc:
                    last_exc = exc

                if persisted_value and persisted_value == selected_value:
                    return

                try:
                    _ptr_click_outside_control(
                        current_page,
                        control_locator,
                        timeout_ms=match_timeout_ms,
                    )
                    if post_select_wait_ms > 0:
                        current_page.wait_for_timeout(post_select_wait_ms)
                    persisted_value = _ptr_wait_for_visible_date_value(
                        min(match_timeout_ms, 1500),
                        settle_ms=900,
                    )
                except Exception as exc:
                    last_exc = exc

                if persisted_value and persisted_value == selected_value:
                    return

                last_exc = RuntimeError(
                    f'Date value "{selected_value}" for "{title}" did not persist after selection.'
                )
                break

            raise RuntimeError(
                f'Unable to commit date picker value for "{title}" with day "{day_label}".'
            ) from last_exc


        def _ptr_click_navigation_button(page, label: str, **kwargs):
            def _ptr_get_step_title(current_page) -> str:
                def _ptr_is_noise_step_title(value: str) -> bool:
                    normalized = " ".join(str(value or "").split()).strip().lower()
                    if not normalized:
                        return True
                    return normalized in {
                        "need help? contact us.",
                        "need help? contact us",
                        "info to include",
                    }

                candidates = [
                    current_page.get_by_role("heading", level=1).first,
                    current_page.locator('[role="heading"][aria-level="1"]').first,
                ]
                for locator in candidates:
                    try:
                        text = str(locator.inner_text(timeout=1000) or "").strip()
                    except Exception:
                        continue
                    if text:
                        if _ptr_is_noise_step_title(text):
                            continue
                        return text
                return ""

            def _ptr_commit_active_rich_text(current_page) -> None:
                try:
                    should_commit = bool(
                        current_page.evaluate(
                            """() => {
                                const active = document.activeElement;
                                if (!active) return false;
                                if (active.matches?.('[contenteditable="true"][role="textbox"], [contenteditable="true"]')) {
                                    return true;
                                }
                                return !!active.closest?.('oj-sp-ai-input-rich-text, oj-sp-input-rich-text-2');
                            }"""
                        )
                    )
                except Exception:
                    should_commit = False

                if not should_commit:
                    return

                try:
                    current_page.keyboard.press("Tab")
                except Exception:
                    return
                try:
                    current_page.wait_for_timeout(_ptr_wait_ms("PTR_NAV_EDITOR_COMMIT_WAIT_MS", 600))
                except Exception:
                    pass

            def _ptr_get_validation_summary(current_page) -> dict:
                try:
                        return current_page.evaluate(
                            r"""() => {
                            const isVisible = (node) => {
                                if (!node) return false;
                                const style = window.getComputedStyle(node);
                                if (style.display === "none" || style.visibility === "hidden") return false;
                                if (node.getAttribute && node.getAttribute("aria-hidden") === "true") return false;
                                return !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
                            };

                            const normalizeText = (value) =>
                                String(value || "").trim().replace(/\\\\s+/g, " ");

                            const getLabelText = (node) => {
                                if (!node) return "";
                                const ariaLabel = node.getAttribute && node.getAttribute("aria-label");
                                if (ariaLabel) return normalizeText(ariaLabel);

                                const labelledBy = node.getAttribute && (node.getAttribute("labelled-by") || node.getAttribute("aria-labelledby"));
                                if (labelledBy) {
                                    const pieces = labelledBy
                                        .split(/\\\\s+/)
                                        .map((id) => document.getElementById(id))
                                        .filter(Boolean)
                                        .map((labelNode) => normalizeText(labelNode.innerText || labelNode.textContent || ""))
                                        .filter(Boolean);
                                    if (pieces.length) return pieces.join(" ");
                                }

                                const hintNode = node.querySelector && node.querySelector('span[id$="|hint"]');
                                if (hintNode) {
                                    const hintText = normalizeText(hintNode.innerText || hintNode.textContent || "");
                                    if (hintText) return hintText;
                                }

                                const labelNode = node.querySelector && node.querySelector("oj-label label, label");
                                if (labelNode) {
                                    const labelText = normalizeText(labelNode.innerText || labelNode.textContent || "");
                                    if (labelText) return labelText;
                                }

                                return "";
                            };

                            const getFieldValue = (node) => {
                                if (!node || !node.querySelector) return "";

                                const readonlyNode = node.querySelector('.oj-text-field-readonly');
                                const readonlyText = normalizeText(readonlyNode?.innerText || readonlyNode?.textContent || "");
                                if (readonlyText) return readonlyText;

                                const richTextNode = node.querySelector('[contenteditable="true"][role="textbox"]');
                                const richText = normalizeText(richTextNode?.innerText || richTextNode?.textContent || "");
                                if (richText) return richText;

                                const inputNode = node.querySelector('input:not([type="hidden"]):not([disabled]), textarea:not([disabled])');
                                const inputValue = normalizeText(inputNode?.value || inputNode?.getAttribute?.("value") || "");
                                if (inputValue) return inputValue;

                                return "";
                            };

                            const requiredWrappers = Array.from(
                                document.querySelectorAll(
                                    [
                                        'oj-select-single.oj-required',
                                        'oj-input-date.oj-required',
                                        'oj-input-text.oj-required',
                                        'oj-input-number.oj-required',
                                        'oj-text-area.oj-required',
                                        'oj-sp-ai-input-rich-text',
                                        'oj-sp-input-rich-text-2',
                                        'oj-c-input-number',
                                        '[aria-required="true"]',
                                    ].join(",")
                                )
                            );

                            const selectors = [
                                '[aria-invalid="true"]',
                                '.oj-invalid',
                                '.oj-required.oj-has-no-value',
                                '.oj-required.oj-searchselect-no-value',
                                '.oj-user-assistance-inline-container',
                                '.oj-message-error',
                                '.oj-message-banner',
                            ];

                            const nodes = Array.from(document.querySelectorAll(selectors.join(",")));
                            const messages = [];
                            let invalidCount = 0;
                            const seenRequired = new Set();

                            for (const node of nodes) {
                                if (!isVisible(node)) continue;
                                if (
                                    node.matches('[aria-invalid="true"], .oj-invalid, .oj-required.oj-has-no-value, .oj-required.oj-searchselect-no-value')
                                ) {
                                    invalidCount += 1;
                                }
                                const text = String(node.innerText || node.textContent || "").trim().replace(/\\\\s+/g, " ");
                                if (text && !messages.includes(text)) {
                                    messages.push(text);
                                }
                            }

                            for (const rawNode of requiredWrappers) {
                                const node =
                                    rawNode.closest?.('oj-select-single, oj-input-date, oj-input-text, oj-input-number, oj-text-area, oj-c-input-number, oj-sp-ai-input-rich-text, oj-sp-input-rich-text-2') ||
                                    rawNode;
                                if (!node || seenRequired.has(node) || !isVisible(node)) continue;
                                seenRequired.add(node);

                                const label = getLabelText(node);
                                const value = getFieldValue(node);
                                const hasVisibleError = Array.from(
                                    node.querySelectorAll?.('.oj-message-error, .oj-user-assistance-inline-container, [aria-invalid="true"]') || []
                                ).some(isVisible);

                                const explicitlyRequired =
                                    node.classList?.contains("oj-required") ||
                                    node.getAttribute?.("aria-required") === "true" ||
                                    node.querySelector?.('[aria-required="true"]');

                                if (!explicitlyRequired) continue;
                                if (value) continue;

                                invalidCount += 1;
                                const detail = label || "Required field";
                                if (!messages.includes(detail)) {
                                    messages.push(detail);
                                } else if (hasVisibleError) {
                                    const detailWithSuffix = `${detail} is required.`;
                                    if (!messages.includes(detailWithSuffix)) {
                                        messages.push(detailWithSuffix);
                                    }
                                }
                            }

                            return {
                                invalidCount,
                                messages: messages.slice(0, 3),
                            };
                        }"""
                    )
                except Exception:
                    return {"invalidCount": 0, "messages": []}

            def _ptr_is_page_busy(current_page) -> bool:
                try:
                    return bool(
                        current_page.evaluate(
                            r"""() => {
                                const isVisible = (node) => {
                                    if (!node) return false;
                                    const style = window.getComputedStyle(node);
                                    if (style.display === "none" || style.visibility === "hidden") return false;
                                    if (node.getAttribute && node.getAttribute("aria-hidden") === "true") return false;
                                    return !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
                                };

                                const selectors = [
                                    '[role="progressbar"]',
                                    '[aria-busy="true"]',
                                    'oj-c-progress-circle',
                                    'oj-c-progress-bar',
                                    'oj-progress-bar',
                                    'oj-progress-circle',
                                    '.oj-progress-bar',
                                    '.oj-progress-circle',
                                    'oj-skeleton',
                                    'oj-c-skeleton',
                                    '.oj-skeleton',
                                    '.oj-c-skeleton',
                                    '[class*="oj-progress"]',
                                    '[class*="oj-skeleton"]',
                                    '[class*="oj-c-skeleton"]',
                                ];

                                return Array.from(document.querySelectorAll(selectors.join(","))).some(isVisible);
                            }"""
                        )
                    )
                except Exception:
                    return False

            def _ptr_has_settled_form_content(current_page) -> bool:
                try:
                    return bool(
                        current_page.evaluate(
                            """() => {
                                const isVisible = (node) => {
                                    if (!node) return false;
                                    const style = window.getComputedStyle(node);
                                    if (style.display === "none" || style.visibility === "hidden") return false;
                                    if (node.getAttribute && node.getAttribute("aria-hidden") === "true") return false;
                                    return !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
                                };

                                const selectors = [
                                    '[role="combobox"]',
                                    '[role="textbox"]',
                                    '[contenteditable="true"]',
                                    'textarea',
                                    'oj-select-single',
                                    'oj-input-date',
                                    'oj-input-number',
                                    'oj-c-input-number',
                                    'oj-input-text',
                                    'oj-sp-ai-input-rich-text',
                                    'oj-sp-input-rich-text-2',
                                ];

                                const visibleCount = Array.from(document.querySelectorAll(selectors.join(","))).filter(isVisible).length;
                                return visibleCount >= 2;
                            }"""
                        )
                    )
                except Exception:
                    return False

            def _ptr_get_step_index(current_page) -> int | None:
                try:
                    return current_page.evaluate(
                        """() => {
                            const isVisible = (node) => {
                                if (!node) return false;
                                const style = window.getComputedStyle(node);
                                if (style.display === "none" || style.visibility === "hidden") return false;
                                if (node.getAttribute && node.getAttribute("aria-hidden") === "true") return false;
                                return !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
                            };

                            const parseStepText = (text) => {
                                const match = String(text || "").match(/Step\\\\s+(\\\\d+)\\\\s+of\\\\s+(\\\\d+)/i);
                                if (!match) return null;
                                const current = Number.parseInt(match[1], 10);
                                return Number.isFinite(current) ? current : null;
                            };

                            const labelledNodes = Array.from(
                                document.querySelectorAll('[aria-label^="Step "], [title^="Step "]')
                            );
                            for (const node of labelledNodes) {
                                if (!isVisible(node)) continue;
                                const labelled = parseStepText(node.getAttribute("aria-label"));
                                if (labelled) return labelled;
                                const titled = parseStepText(node.getAttribute("title"));
                                if (titled) return titled;
                            }

                            const numberNode = Array.from(
                                document.querySelectorAll('.oj-sp-guided-process-step-number')
                            ).find(isVisible);
                            if (numberNode) {
                                const value = Number.parseInt(String(numberNode.textContent || "").trim(), 10);
                                if (Number.isFinite(value)) return value;
                            }

                            return null;
                        }"""
                    )
                except Exception:
                    return None

            click_kwargs = dict(kwargs)
            current_page = _ptr_resolve_active_page(page)
            step_before = _ptr_get_step_title(current_page)
            step_index_before = _ptr_get_step_index(current_page)

            last_exc = None
            def _ptr_click_current_navigation_button(current_page) -> None:
                nonlocal last_exc
                button_candidates = [
                    current_page.get_by_role("button", name=label, exact=True).first,
                    current_page.get_by_role("button", name=label, exact=False).first,
                    current_page.get_by_role("link", name=label, exact=True).first,
                    current_page.get_by_role("link", name=label, exact=False).first,
                ]
                clicked = False
                for button_locator in button_candidates:
                    _ptr_commit_active_rich_text(current_page)
                    try:
                        button_locator.scroll_into_view_if_needed(timeout=click_kwargs.get("timeout", 5000))
                    except Exception:
                        pass
                    try:
                        button_locator.click(**click_kwargs)
                        clicked = True
                        break
                    except Exception as exc:
                        last_exc = exc

                if not clicked and label in {"Back", "Go back"}:
                    try:
                        current_page.go_back(
                            wait_until="domcontentloaded",
                            timeout=click_kwargs.get("timeout", 5000),
                        )
                        clicked = True
                    except Exception as exc:
                        last_exc = exc

                if not clicked:
                    raise RuntimeError(f'Unable to click navigation button "{label}".') from last_exc

            _ptr_click_current_navigation_button(current_page)

            try:
                current_page.wait_for_timeout(_ptr_wait_ms("PTR_NAV_BUTTON_WAIT_MS", 2000))
            except Exception:
                pass

            if label not in {"Continue", "Submit", "Next", "Review"}:
                return

            advance_timeout_ms = _ptr_wait_ms("PTR_NAV_ADVANCE_TIMEOUT_MS", 4000)
            stable_step_ms = _ptr_wait_ms("PTR_NAV_STEP_STABLE_MS", 1200)
            busy_extra_timeout_ms = _ptr_wait_ms("PTR_NAV_BUSY_EXTRA_TIMEOUT_MS", 12000)
            transition_extra_timeout_ms = _ptr_wait_ms("PTR_NAV_TRANSITION_EXTRA_TIMEOUT_MS", 8000)
            retry_timeout_ms = _ptr_wait_ms("PTR_NAV_RETRY_TIMEOUT_MS", 5000)
            retry_pause_ms = _ptr_wait_ms("PTR_NAV_RETRY_PAUSE_MS", 400)
            final_settle_timeout_ms = _ptr_wait_ms("PTR_NAV_FINAL_SETTLE_TIMEOUT_MS", 4000)

            def _ptr_wait_for_step_transition(base_timeout_ms: int):
                deadline = time.time() + (base_timeout_ms / 1000.0)
                busy_deadline = deadline + (busy_extra_timeout_ms / 1000.0)
                transition_deadline = deadline + (transition_extra_timeout_ms / 1000.0)
                stable_transition_key = ""
                stable_since = None
                saw_busy = False
                saw_step_change = False
                while (
                    time.time() < deadline
                    or (saw_busy and time.time() < busy_deadline)
                    or (saw_step_change and time.time() < transition_deadline)
                ):
                    current_page = _ptr_resolve_active_page(page)
                    step_after = _ptr_get_step_title(current_page)
                    step_index_after = _ptr_get_step_index(current_page)
                    is_busy = _ptr_is_page_busy(current_page)
                    has_form_content = _ptr_has_settled_form_content(current_page)
                    if is_busy:
                        saw_busy = True

                    index_changed = (
                        step_index_before is not None
                        and step_index_after is not None
                        and step_index_after != step_index_before
                    )

                    if (step_before and step_after and step_after != step_before) or index_changed:
                        saw_step_change = True

                    if ((step_before and step_after and step_after != step_before) or index_changed) and (not is_busy or has_form_content):
                        transition_key = f"{step_index_after or ''}|{step_after}"
                        if transition_key != stable_transition_key:
                            stable_transition_key = transition_key
                            stable_since = time.time()
                        elif stable_since and (time.time() - stable_since) * 1000 >= stable_step_ms:
                            return current_page, step_after, step_index_after, saw_busy, saw_step_change, True
                    else:
                        stable_transition_key = ""
                        stable_since = None
                    try:
                        current_page.wait_for_timeout(150)
                    except Exception:
                        break

                current_page = _ptr_resolve_active_page(page)
                step_after = _ptr_get_step_title(current_page)
                step_index_after = _ptr_get_step_index(current_page)
                return current_page, step_after, step_index_after, saw_busy, saw_step_change, False

            def _ptr_wait_for_current_step_settle(expected_step: str, expected_step_index: int | None, base_timeout_ms: int) -> bool:
                deadline = time.time() + (base_timeout_ms / 1000.0)
                stable_since = None
                while time.time() < deadline:
                    current_page = _ptr_resolve_active_page(page)
                    step_after = _ptr_get_step_title(current_page)
                    step_index_after = _ptr_get_step_index(current_page)
                    is_busy = _ptr_is_page_busy(current_page)
                    has_form_content = _ptr_has_settled_form_content(current_page)
                    same_expected_step = (
                        ((not expected_step) or step_after == expected_step)
                        and (
                            expected_step_index is None
                            or step_index_after is None
                            or step_index_after == expected_step_index
                        )
                    )
                    if same_expected_step and (not is_busy or has_form_content):
                        if stable_since is None:
                            stable_since = time.time()
                        elif (time.time() - stable_since) * 1000 >= stable_step_ms:
                            return True
                    else:
                        stable_since = None
                    try:
                        current_page.wait_for_timeout(150)
                    except Exception:
                        break
                return False

            current_page, step_after, step_index_after, saw_busy, saw_step_change, stabilized = _ptr_wait_for_step_transition(
                advance_timeout_ms
            )

            effective_timeout_ms = advance_timeout_ms
            if saw_busy:
                effective_timeout_ms += busy_extra_timeout_ms
            if saw_step_change:
                effective_timeout_ms = max(
                    effective_timeout_ms,
                    advance_timeout_ms + transition_extra_timeout_ms,
                )
            same_step = (
                ((not step_before) or step_after == step_before)
                and (
                    step_index_before is None
                    or step_index_after is None
                    or step_index_after == step_index_before
                )
            )
            step_changed = (
                (step_before and step_after and step_after != step_before)
                or (
                    step_index_before is not None
                    and step_index_after is not None
                    and step_index_after != step_index_before
                )
            )
            if not same_step and stabilized:
                return
            if step_changed and _ptr_wait_for_current_step_settle(step_after, step_index_after, final_settle_timeout_ms):
                return
            if same_step:
                summary = _ptr_get_validation_summary(current_page)
                invalid_count = int(summary.get("invalidCount") or 0)
                messages = [str(item).strip() for item in (summary.get("messages") or []) if str(item).strip()]
                if invalid_count or messages:
                    detail = "; ".join(messages) if messages else f"{invalid_count} required or invalid field(s) remain on the page."
                    raise RuntimeError(
                        f'Navigation button "{label}" did not advance from step "{step_before}". {detail}'
                    )
                if saw_step_change:
                    raise RuntimeError(
                        f'Navigation button "{label}" started leaving step "{step_before}" but returned before the transition stabilized.'
                    )
                if label == "Submit":
                    # Final submit actions in Oracle often keep the user on the same
                    # guided-process shell while server-side submission completes.
                    # If there are no visible validation blockers, treat as success.
                    return
                try:
                    current_page.wait_for_timeout(retry_pause_ms)
                except Exception:
                    pass
                try:
                    _ptr_click_current_navigation_button(current_page)
                    try:
                        current_page.wait_for_timeout(_ptr_wait_ms("PTR_NAV_BUTTON_WAIT_MS", 2000))
                    except Exception:
                        pass
                    current_page, step_after, step_index_after, retry_saw_busy, retry_saw_step_change, retry_stabilized = _ptr_wait_for_step_transition(
                        retry_timeout_ms
                    )
                    saw_busy = saw_busy or retry_saw_busy
                    saw_step_change = saw_step_change or retry_saw_step_change
                    effective_timeout_ms += retry_pause_ms + retry_timeout_ms
                    same_step = (
                        ((not step_before) or step_after == step_before)
                        and (
                            step_index_before is None
                            or step_index_after is None
                            or step_index_after == step_index_before
                        )
                    )
                    if not same_step and retry_stabilized:
                        return
                except Exception as exc:
                    last_exc = exc
                raise RuntimeError(
                    f'Navigation button "{label}" did not advance from step "{step_before}" within {effective_timeout_ms}ms.'
                )
            if step_changed:
                changed_step_grace_timeout_ms = _ptr_wait_ms("PTR_NAV_CHANGED_STEP_GRACE_TIMEOUT_MS", 12000)
                if _ptr_wait_for_current_step_settle(step_after, step_index_after, changed_step_grace_timeout_ms):
                    return

                current_page = _ptr_resolve_active_page(page)
                step_after_grace = _ptr_get_step_title(current_page)
                step_index_after_grace = _ptr_get_step_index(current_page)
                still_changed = (
                    (step_before and step_after_grace and step_after_grace != step_before)
                    or (
                        step_index_before is not None
                        and step_index_after_grace is not None
                        and step_index_after_grace != step_index_before
                    )
                )

                # Oracle Redwood often keeps the next step in a long loading state
                # after navigation. If we have definitively moved to the next step,
                # treat this click as successful and let the next action perform its
                # own wait/visibility checks.
                if still_changed:
                    return

                raise RuntimeError(
                    f'Navigation button "{label}" moved to step "{step_after}" but the transition did not stabilize.'
                )


        def _ptr_write_diagnostics() -> None:
            if not _PTR_DIAGNOSTICS_PATH:
                return
            payload = {
                "page_url": None,
                "page_title": None,
                "failure_screenshot_path": None,
                "step_artifacts": _PTR_STEP_ARTIFACTS,
            }
            page = _PTR_LAST_PAGE
            if page is not None:
                try:
                    payload["page_url"] = page.url
                except Exception:
                    payload["page_url"] = None
                try:
                    payload["page_title"] = page.title()
                except Exception:
                    payload["page_title"] = None
            if _PTR_FAILURE_SCREENSHOT_PATH and Path(_PTR_FAILURE_SCREENSHOT_PATH).exists():
                payload["failure_screenshot_path"] = _PTR_FAILURE_SCREENSHOT_PATH
            try:
                Path(_PTR_DIAGNOSTICS_PATH).write_text(json.dumps(payload), encoding="utf-8")
            except Exception:
                pass


        def _ptr_capture_failure(_exc) -> None:
            page = _PTR_LAST_PAGE
            if page is not None and _PTR_FAILURE_SCREENSHOT_PATH:
                try:
                    page.screenshot(path=_PTR_FAILURE_SCREENSHOT_PATH, full_page=True)
                except Exception:
                    pass
            _ptr_write_diagnostics()


        def _ptr_get_retry_count(env_name: str, default: int) -> int:
            try:
                return max(0, int(os.getenv(env_name, str(default))))
            except Exception:
                return default


        def _ptr_is_transient_navigation_error(exc: Exception) -> bool:
            message = str(exc)
            transient_markers = (
                "ERR_NAME_NOT_RESOLVED",
                "ERR_NETWORK_CHANGED",
                "ERR_INTERNET_DISCONNECTED",
                "ERR_CONNECTION_RESET",
                "ERR_CONNECTION_CLOSED",
                "ERR_CONNECTION_TIMED_OUT",
                "ERR_TIMED_OUT",
                "NS_ERROR_UNKNOWN_HOST",
            )
            return any(marker in message for marker in transient_markers)


        def _ptr_get_requested_url(args, kwargs) -> str:
            if args:
                return str(args[0] or "").strip()
            return str(kwargs.get("url") or "").strip()


        def _ptr_same_origin(first_url: str, second_url: str) -> bool:
            try:
                first = urlparse(str(first_url or ""))
                second = urlparse(str(second_url or ""))
            except Exception:
                return False
            if not first.scheme or not first.netloc or not second.scheme or not second.netloc:
                return False
            return (first.scheme, first.netloc) == (second.scheme, second.netloc)


        def _ptr_is_recoverable_aborted_navigation(page, requested_url: str, exc: Exception) -> bool:
            if "ERR_ABORTED" not in str(exc):
                return False
            try:
                current_url = str(page.url or "").strip()
            except Exception:
                return False
            if not current_url or current_url == "about:blank":
                return False
            if requested_url and (current_url == requested_url or _ptr_same_origin(current_url, requested_url)):
                return True
            return False


        def _ptr_wait_for_post_login_redirect(page, expected_url: str = "") -> None:
            wait_timeout_ms = _ptr_wait_ms("PTR_LOGIN_REDIRECT_WAIT_MS", 12000)
            deadline = time.time() + (wait_timeout_ms / 1000.0)
            last_url = ""

            while time.time() < deadline:
                current_page = _ptr_resolve_active_page(page)
                try:
                    current_url = str(current_page.url or "").strip()
                except Exception:
                    current_url = ""

                if current_url and current_url != "about:blank":
                    last_url = current_url
                    if (
                        not expected_url
                        or current_url == expected_url
                        or _ptr_same_origin(current_url, expected_url)
                    ):
                        try:
                            current_page.wait_for_load_state("domcontentloaded", timeout=1500)
                        except Exception:
                            pass
                        return

                try:
                    current_page.wait_for_timeout(250)
                except Exception:
                    pass

            if expected_url:
                raise RuntimeError(
                    f'Post-login redirect did not settle for "{expected_url}". Last URL: "{last_url or "unknown"}".'
                )


        def _ptr_release_steel_session(session_id: str) -> None:
            if not session_id or session_id not in _PTR_STEEL_RELEASE_SESSION_IDS:
                return
            try:
                from steel import Steel

                Steel().sessions.release(session_id)
            except Exception:
                pass
            finally:
                _PTR_STEEL_RELEASE_SESSION_IDS.discard(session_id)


        def _ptr_release_pending_steel_sessions() -> None:
            for _ptr_session_id in list(_PTR_STEEL_RELEASE_SESSION_IDS):
                _ptr_release_steel_session(_ptr_session_id)


        def _ptr_launch_chromium(playwright, *args, **kwargs):
            browser_type = getattr(playwright, "chromium")
            browser_provider = str(os.getenv("PTR_BROWSER_PROVIDER", "")).strip().lower()
            if not browser_provider:
                browser_provider = "steel" if str(os.getenv("STEEL_API_KEY", "")).strip() else "local"

            if browser_provider == "steel":
                steel_api_key = str(os.getenv("STEEL_API_KEY", "")).strip()
                if not steel_api_key:
                    raise RuntimeError(
                        "PTR_BROWSER_PROVIDER=steel but STEEL_API_KEY is not configured."
                    )
                from steel import Steel
                from urllib.parse import urlencode as _ptr_urlencode

                steel_session_id = str(os.getenv("STEEL_SESSION_ID", "")).strip()
                steel_connect_url = (
                    str(os.getenv("STEEL_CONNECT_URL", "wss://connect.steel.dev")).strip()
                    or "wss://connect.steel.dev"
                )
                steel_client = Steel(steel_api_key=steel_api_key)

                connect_kwargs = {}
                connect_timeout = kwargs.get("timeout")
                if connect_timeout is not None:
                    connect_kwargs["timeout"] = connect_timeout
                connect_over_cdp_fn = getattr(browser_type, "connect_over_cdp")
                steel_connect_retries = _ptr_get_retry_count("PTR_STEEL_CONNECT_RETRIES", 2)
                try:
                    steel_session_timeout_ms = max(
                        60000,
                        int(os.getenv("PTR_STEEL_SESSION_TIMEOUT_MS", "900000")),
                    )
                except Exception:
                    steel_session_timeout_ms = 900000

                last_exc = None
                for attempt in range(steel_connect_retries + 1):
                    created_session_id = ""
                    try:
                        if steel_session_id:
                            steel_session = steel_client.sessions.retrieve(steel_session_id)
                            should_release_session = False
                        else:
                            create_kwargs = {
                                "api_timeout": steel_session_timeout_ms,
                            }
                            if "headless" in kwargs:
                                create_kwargs["headless"] = bool(kwargs.get("headless"))
                            steel_session = steel_client.sessions.create(**create_kwargs)
                            created_session_id = steel_session.id
                            should_release_session = True
                            _PTR_STEEL_RELEASE_SESSION_IDS.add(created_session_id)

                        connect_url = (
                            f"{steel_connect_url}?{_ptr_urlencode({'apiKey': steel_api_key, 'sessionId': steel_session.id})}"
                        )
                        browser = connect_over_cdp_fn(
                            connect_url,
                            **connect_kwargs,
                        )
                        if should_release_session:
                            _PTR_STEEL_BROWSER_SESSION_IDS[id(browser)] = steel_session.id
                        return browser
                    except Exception as _steel_exc:
                        _steel_exc_text = str(_steel_exc)
                        if (
                            _steel_exc.__class__.__name__ == "AuthenticationError"
                            or "Authentication failed" in _steel_exc_text
                            or "Unauthorized" in _steel_exc_text
                        ):
                            raise RuntimeError(
                                "Steel authentication failed while creating the browser session. "
                                "Verify STEEL_API_KEY in the worker environment and restart both "
                                "the tool and agent workers after updating it."
                            ) from _steel_exc
                        last_exc = _steel_exc
                        if created_session_id:
                            _ptr_release_steel_session(created_session_id)
                        if attempt >= steel_connect_retries:
                            raise
                        time.sleep(min(2 ** attempt, 5))

                raise last_exc

            if browser_provider not in ("local", ""):
                raise RuntimeError(f"Unsupported PTR_BROWSER_PROVIDER: {browser_provider}")

            # --- Local launch ---
            executable_path = str(os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", "")).strip()
            if executable_path:
                kwargs.setdefault("executable_path", executable_path)
            # Args to bypass Akamai/Cloudflare bot detection
            _stealth_args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-automation",
                "--disable-infobars",
                "--excludeSwitches=enable-automation",
                "--useAutomationExtension=false",
                "--disable-web-security",
                "--allow-running-insecure-content",
                "--ignore-certificate-errors",
            ]
            blocked_launch_args = {"--start-maximized", "--start-fullscreen"}
            existing_args = [
                str(arg)
                for arg in list(kwargs.get("args") or [])
                if str(arg) not in blocked_launch_args
            ]
            window_width, window_height = _ptr_window_dimensions()
            if not any(str(arg).startswith("--window-size=") for arg in existing_args):
                existing_args.append(f"--window-size={window_width},{window_height}")
            for _a in _stealth_args:
                if _a not in existing_args:
                    existing_args.append(_a)
            kwargs["args"] = existing_args
            launch_fn = getattr(browser_type, "launch")
            try:
                return launch_fn(*args, **kwargs)
            except Exception as _launch_exc:
                if "Executable doesn't exist" in str(_launch_exc):
                    import subprocess as _sp, sys as _sys
                    try:
                        _sp.run(
                            [_sys.executable, "-m", "playwright", "install", "chromium"],
                            check=True,
                            capture_output=True,
                            text=True,
                            timeout=180,
                        )
                    except _sp.TimeoutExpired as _install_exc:
                        raise RuntimeError(
                            "Chromium is not installed and automatic "
                            "`playwright install chromium` timed out after 180 seconds."
                        ) from _install_exc
                    except _sp.CalledProcessError as _install_exc:
                        _details = (
                            (_install_exc.stderr or _install_exc.stdout or "").strip()
                            or f"exit code {_install_exc.returncode}"
                        )
                        raise RuntimeError(
                            "Chromium is not installed and automatic "
                            f"`playwright install chromium` failed: {_details}"
                        ) from _install_exc
                    return launch_fn(*args, **kwargs)
                raise


        atexit.register(_ptr_write_diagnostics)
        atexit.register(_ptr_release_pending_steel_sessions)

        _ptr_original_browser_new_context = Browser.new_context
        def _ptr_browser_new_context(self, *args, **kwargs):
            if _PTR_RECORD_VIDEO and _PTR_VIDEO_DIR:
                Path(_PTR_VIDEO_DIR).mkdir(parents=True, exist_ok=True)
                kwargs.setdefault("record_video_dir", _PTR_VIDEO_DIR)
            if not kwargs.get("no_viewport"):
                target_viewport = _ptr_target_viewport()
                viewport = kwargs.get("viewport")
                if isinstance(viewport, dict):
                    try:
                        viewport_width = int(viewport.get("width", target_viewport["width"]))
                    except Exception:
                        viewport_width = target_viewport["width"]
                    try:
                        viewport_height = int(viewport.get("height", target_viewport["height"]))
                    except Exception:
                        viewport_height = target_viewport["height"]
                    kwargs["viewport"] = {
                        "width": max(800, min(viewport_width, target_viewport["width"])),
                        "height": max(600, min(viewport_height, target_viewport["height"])),
                    }
                elif viewport is None:
                    kwargs.setdefault("viewport", target_viewport)
            # Spoof a real Chrome user agent to bypass Akamai bot detection
            kwargs.setdefault(
                "user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            )
            kwargs.setdefault("locale", "en-US")
            return _ptr_original_browser_new_context(self, *args, **kwargs)
        Browser.new_context = _ptr_browser_new_context

        _ptr_original_browser_close = Browser.close
        def _ptr_browser_close(self, *args, **kwargs):
            steel_session_id = _PTR_STEEL_BROWSER_SESSION_IDS.pop(id(self), "")
            try:
                return _ptr_original_browser_close(self, *args, **kwargs)
            finally:
                _ptr_release_steel_session(steel_session_id)
        Browser.close = _ptr_browser_close

        _ptr_original_context_new_page = BrowserContext.new_page
        def _ptr_context_new_page(self, *args, **kwargs):
            page = _ptr_original_context_new_page(self, *args, **kwargs)
            return _ptr_register_page(page)
        BrowserContext.new_page = _ptr_context_new_page

        _ptr_original_page_goto = Page.goto
        def _ptr_page_goto(self, *args, **kwargs):
            _ptr_register_page(self)
            goto_retries = _ptr_get_retry_count("PTR_GOTO_RETRIES", 1)
            requested_url = _ptr_get_requested_url(args, kwargs)
            last_exc = None
            for attempt in range(goto_retries + 1):
                try:
                    result = _ptr_original_page_goto(self, *args, **kwargs)
                    break
                except Exception as exc:
                    last_exc = exc
                    if "ERR_ABORTED" in str(exc):
                        self.wait_for_timeout(min(1000 * (attempt + 1), 3000))
                        if _ptr_is_recoverable_aborted_navigation(self, requested_url, exc):
                            try:
                                self.wait_for_load_state("domcontentloaded", timeout=10000)
                            except Exception:
                                pass
                            _ptr_capture_step("goto")
                            return None
                    if not _ptr_is_transient_navigation_error(exc) or attempt >= goto_retries:
                        raise
                    self.wait_for_timeout(min(1000 * (attempt + 1), 3000))
            else:
                raise last_exc
            _ptr_capture_step("goto")
            return result
        Page.goto = _ptr_page_goto

        _ptr_original_page_reload = Page.reload
        def _ptr_page_reload(self, *args, **kwargs):
            _ptr_register_page(self)
            result = _ptr_original_page_reload(self, *args, **kwargs)
            _ptr_capture_step("reload")
            return result
        Page.reload = _ptr_page_reload

        def _ptr_wrap_locator_action(method_name: str) -> None:
            original = getattr(Locator, method_name)
            def _wrapped(self, *args, **kwargs):
                try:
                    result = original(self, *args, **kwargs)
                except Exception as exc:
                    # When a click fails because a loading overlay is intercepting pointer
                    # events, wait for the page to settle then retry with force=True so the
                    # click is dispatched directly to the underlying element.
                    if method_name == "click" and "intercepts pointer events" in str(exc):
                        page = _PTR_LAST_PAGE
                        if page is not None:
                            try:
                                page.wait_for_load_state("networkidle", timeout=10000)
                            except Exception:
                                pass
                        result = original(self, force=True)
                    else:
                        raise
                _ptr_capture_step(method_name)
                return result
            setattr(Locator, method_name, _wrapped)

        for _ptr_method in (
            "click",
            "fill",
            "press",
            "check",
            "uncheck",
            "select_option",
            "set_input_files",
        ):
            _ptr_wrap_locator_action(_ptr_method)
        '''
    ).strip()

    instrumented = _insert_after_future_imports(script_text, helper)
    _ptr_headless = os.getenv("PTR_HEADLESS", "true").strip().lower() not in ("false", "0", "no")
    if _ptr_headless:
        instrumented = re.sub(r"headless\s*=\s*False", "headless=True", instrumented)
    # Oracle Fusion runs persistent background XHR (heartbeat, analytics) so networkidle
    # never fires — replace with a fixed 3-second wait to let ADF PPR complete.
    instrumented = re.sub(
        r'page\.wait_for_load_state\(\s*["\']networkidle["\']\s*(?:,\s*timeout\s*=\s*\d+)?\s*\)',
        "page.wait_for_timeout(3000)",
        instrumented,
    )
    instrumented = instrumented.replace("channel=\"chromium\", ", "")
    instrumented = instrumented.replace("channel=\"chromium\"", "")
    instrumented = re.sub(r"playwright\.chromium\.launch\(", "_ptr_launch_chromium(playwright, ", instrumented)

    sync_pattern = re.compile(
        r"with sync_playwright\(\) as playwright:\n(?P<indent>[ \t]+)run\(playwright\)",
        re.MULTILINE,
    )

    def _sync_repl(match: re.Match[str]) -> str:
        indent = match.group("indent")
        return (
            "with sync_playwright() as playwright:\n"
            f"{indent}try:\n"
            f"{indent}    run(playwright)\n"
            f"{indent}except Exception as exc:\n"
            f"{indent}    _ptr_capture_failure(exc)\n"
            f"{indent}    raise\n"
            f"{indent}finally:\n"
            f"{indent}    _ptr_write_diagnostics()"
        )

    instrumented, count = sync_pattern.subn(_sync_repl, instrumented, count=1)
    if count == 0:
        raise ValueError(
            "Could not instrument recording execution. Expected a sync_playwright runner block."
        )

    return instrumented


def _inject_network_idle_waits(script_text: str) -> str:
    """
    Insert a short fixed wait after .click() calls that are followed by an
    interaction on a different element.

    Oracle ADF / Fusion apps fire AJAX on every field change and then re-render
    the form, temporarily disabling dependent fields.  A fixed 1.5 s pause is
    enough for the AJAX request to start without waiting so long that Oracle's
    background polling cycle fires another XHR round-trip that causes the form
    to switch layout panels (which permanently disables fields that require a
    different interaction order).  The remaining wait for the element to become
    enabled is handled by the 60 s default action timeout set at page creation.

    wait_for_load_state("networkidle") was tried but caused panel switches in
    Oracle ADF: it delayed the next action past the point where Oracle's polling
    XHR triggered a form recalculation, changing the form layout irreversibly.
    """
    # Matches a complete .click() call on a locator, capturing indent + locator expression.
    # Non-greedy expr so we capture everything up to the *last* .click( on the line.
    _CLICK_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<expr>.+)\.click\(.*\)\s*$")
    # Matches any of the tracked locator actions; used to detect the next action line.
    _ACTION_RE = re.compile(
        r"^[ \t]*(?P<expr>.+?)\.(?:click|fill|press|select_option|check|uncheck|set_input_files)\("
    )
    _NAV_BUTTON_RE = re.compile(
        r'get_by_role\("button",\s*name="(?:Continue|Submit|Save|Done|Next|Review|Back|Go back)"'
    )

    lines = script_text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        click_m = _CLICK_RE.match(line)
        if click_m:
            indent = click_m.group("indent")
            locator = click_m.group("expr").strip()
            if _NAV_BUTTON_RE.search(locator):
                out.append(line)
                page_var = locator.split(".")[0]
                out.append(
                    f'{indent}{page_var}.wait_for_timeout(_ptr_wait_ms("PTR_NAV_BUTTON_WAIT_MS", 2000))\n'
                )
                i += 1
                continue
            # Scan forward for the next non-blank, non-comment action line.
            j = i + 1
            while j < len(lines) and (
                not lines[j].strip() or lines[j].lstrip().startswith("#")
            ):
                j += 1
            if j < len(lines):
                next_m = _ACTION_RE.match(lines[j])
                if next_m and next_m.group("expr").strip() != locator:
                    # Different element follows — give the page a moment to start
                    # processing the click without waiting for full network idle.
                    out.append(line)
                    page_var = locator.split(".")[0]
                    out.append(
                        f'{indent}{page_var}.wait_for_timeout(_ptr_wait_ms("PTR_POST_CLICK_WAIT_MS", 1500))\n'
                    )
                    i += 1
                    continue
        out.append(line)
        i += 1
    return "".join(out)


def _strip_redundant_textbox_focus_clicks(script_text: str) -> str:
    """
    Remove codegen-style textbox focus transitions that do not contribute to
    the interaction flow.

    Common flaky patterns:
    - click textbox -> fill same textbox
    - click textbox -> press same textbox
    - fill textbox -> press("Tab") -> interact with a different field/button

    The first two are redundant because Playwright will focus during fill/press.
    The Tab case is a codegen artifact that is usually unnecessary because the
    next locator action will focus the target element directly.
    """
    _TEXTBOX_CLICK_RE = re.compile(
        r'^(?P<indent>[ \t]*)(?P<expr>.+?get_by_role\("textbox",.+?\))\.click\(.*\)\s*$'
    )
    _TEXTBOX_PRESS_TAB_RE = re.compile(
        r'^(?P<indent>[ \t]*)(?P<expr>.+?get_by_role\("textbox",.+?\))\.press\("Tab"\)\s*$'
    )
    _ACTION_RE = re.compile(
        r'^[ \t]*(?P<expr>.+?)\.(?P<method>click|fill|press|select_option|check|uncheck|set_input_files)\('
    )

    lines = script_text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        textbox_click_m = _TEXTBOX_CLICK_RE.match(line)
        if textbox_click_m:
            locator = textbox_click_m.group("expr").strip()
            j = i + 1
            while j < len(lines) and (
                not lines[j].strip() or lines[j].lstrip().startswith("#")
            ):
                j += 1
            if j < len(lines):
                next_m = _ACTION_RE.match(lines[j])
                if next_m:
                    next_locator = next_m.group("expr").strip()
                    next_method = next_m.group("method")
                    if next_locator == locator and next_method in {"fill", "press"}:
                        i += 1
                        continue
        textbox_press_tab_m = _TEXTBOX_PRESS_TAB_RE.match(line)
        if textbox_press_tab_m:
            locator = textbox_press_tab_m.group("expr").strip()

            prev_m = None
            for previous_line in reversed(out):
                if not previous_line.strip() or previous_line.lstrip().startswith("#"):
                    continue
                prev_m = _ACTION_RE.match(previous_line)
                break

            j = i + 1
            while j < len(lines) and (
                not lines[j].strip() or lines[j].lstrip().startswith("#")
            ):
                j += 1
            next_m = _ACTION_RE.match(lines[j]) if j < len(lines) else None

            if prev_m and next_m:
                prev_locator = prev_m.group("expr").strip()
                prev_method = prev_m.group("method")
                next_locator = next_m.group("expr").strip()
                if prev_locator == locator and prev_method == "fill" and next_locator != locator:
                    i += 1
                    continue
        out.append(line)
        i += 1
    return "".join(out)


def _rewrite_textbox_click_calls(script_text: str) -> str:
    _TEXTBOX_CLICK_RE = re.compile(
        r'(?P<locator>(?P<page>\b\w+\b)\.get_by_role\("textbox",\s*name="(?P<label>[^"\\]+)"(?:,\s*exact\s*=\s*(?P<exact>True|False))?\))\.click\((?P<args>.*?)\)'
    )

    def _repl(match: re.Match[str]) -> str:
        locator_expr = match.group("locator")
        page_var = match.group("page")
        label = match.group("label")
        exact_value = match.group("exact")
        if exact_value == "False":
            return match.group(0)
        args_expr = (match.group("args") or "").strip()
        if args_expr:
            return f'_ptr_click_textbox({locator_expr}, {page_var}, "{label}", {args_expr})'
        return f'_ptr_click_textbox({locator_expr}, {page_var}, "{label}")'

    rewritten_lines = []
    for line in script_text.splitlines(keepends=True):
        rewritten_lines.append(_TEXTBOX_CLICK_RE.sub(_repl, line))
    return "".join(rewritten_lines)


def _substitute_parameters(script_text: str, parameters: dict[str, Any]) -> str:
    """
    Replace {{variable}} placeholders in the script with runtime values.

    Scripts can use {{my_var}} anywhere a literal value appears — in fill(),
    select_option(label=...), get_by_text(), get_by_role(name=...), etc.
    Values are sourced from the recording's `parameters` dict supplied at
    trigger time.  Unresolved placeholders are left as-is so the script still
    runs and the missing variable is obvious from the error.
    """
    unresolved: list[str] = []
    for key, value in parameters.items():
        placeholder = f"{{{{{key}}}}}"
        if placeholder in script_text:
            script_text = script_text.replace(placeholder, str(value))
    for match in re.finditer(r"\{\{(\w+)\}\}", script_text):
        unresolved.append(match.group(1))
    if unresolved:
        logger.warning(
            "Script has unresolved parameter placeholders: %s",
            ", ".join(sorted(set(unresolved))),
        )
    return script_text


def _rewrite_textbox_fill_calls(script_text: str) -> str:
    _TEXTBOX_FILL_RE = re.compile(
        r'(?P<locator>(?P<page>\b\w+\b)\.get_by_role\("textbox",\s*name="(?P<label>[^"\\]+)"\))\.fill\((?P<value>.+?)\)'
    )

    def _repl(match: re.Match[str]) -> str:
        locator_expr = match.group("locator")
        page_var = match.group("page")
        label = match.group("label")
        value_expr = match.group("value")
        return f'_ptr_fill_textbox({locator_expr}, {page_var}, "{label}", {value_expr})'

    rewritten_lines = []
    for line in script_text.splitlines(keepends=True):
        rewritten_lines.append(_TEXTBOX_FILL_RE.sub(_repl, line))
    return "".join(rewritten_lines)


def _rewrite_exact_text_click_calls(script_text: str) -> str:
    _TEXT_CLICK_RE = re.compile(
        r'(?P<page>\b\w+\b)\.get_by_text\("(?P<label>[^"\\]+)"(?:,\s*exact\s*=\s*(?P<exact>True|False))?\)\.click\((?P<args>.*?)\)'
    )

    def _repl(match: re.Match[str]) -> str:
        page_var = match.group("page")
        label = match.group("label")
        exact_value = match.group("exact")
        # Keep multi-word exact text clicks raw. In Oracle ADF/Redwood these are
        # often menu-panel items like "Post to Ledger" or "Complete and Review",
        # and the recorded direct locator is safer than the broad text helper.
        if exact_value == "False" or re.search(r"\s", label):
            return match.group(0)
        args_expr = (match.group("args") or "").strip()
        if args_expr:
            return f'_ptr_click_text_target({page_var}, "{label}", {args_expr})'
        return f'_ptr_click_text_target({page_var}, "{label}")'

    rewritten_lines = []
    for line in script_text.splitlines(keepends=True):
        rewritten_lines.append(_TEXT_CLICK_RE.sub(_repl, line))
    return "".join(rewritten_lines)


def _rewrite_exact_button_click_calls(script_text: str) -> str:
    _BUTTON_CLICK_RE = re.compile(
        r'(?P<page>\b\w+\b)\.get_by_role\("button",\s*name="(?P<label>[^"\\]+)"(?:,\s*exact\s*=\s*(?P<exact>True|False))?\)\.click\((?P<args>.*?)\)'
    )
    nav_labels = {"Continue", "Submit", "Next", "Review", "Back", "Go back"}

    def _repl(match: re.Match[str]) -> str:
        page_var = match.group("page")
        label = match.group("label")
        exact_value = match.group("exact")
        if exact_value == "False" or label in nav_labels or label.strip().isdigit():
            return match.group(0)
        args_expr = (match.group("args") or "").strip()
        if args_expr:
            return f'_ptr_click_button_target({page_var}, "{label}", {args_expr})'
        return f'_ptr_click_button_target({page_var}, "{label}")'

    rewritten_lines = []
    for line in script_text.splitlines(keepends=True):
        rewritten_lines.append(_BUTTON_CLICK_RE.sub(_repl, line))
    return "".join(rewritten_lines)


def _rewrite_search_popup_selection_calls(script_text: str) -> str:
    _SEARCH_ICON_CLICK_RE = re.compile(
        r'^(?P<indent>[ \t]*)(?P<page>\b\w+\b)\.get_by_title\("(?P<title>Search:\s*[^"\\]+)"(?:,\s*exact\s*=\s*(?P<exact>True|False))?\)\.click\(\s*\)\s*$'
    )
    _TEXT_CLICK_RE = re.compile(
        r'^(?P<indent>[ \t]*)(?P<page>\b\w+\b)\.get_by_text\("(?P<option>[^"\\]+)"(?:,\s*exact\s*=\s*(?P<exact>True|False))?\)\.click\(\s*\)\s*$'
    )
    _ROLE_CLICK_RE = re.compile(
        r'^(?P<indent>[ \t]*)(?P<page>\b\w+\b)\.get_by_role\("(?P<role>option|cell|gridcell)",\s*name="(?P<option>[^"\\]+)"(?:,\s*exact\s*=\s*(?P<exact>True|False))?\)(?:\.first)?\.click\(\s*\)\s*$'
    )

    lines = script_text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        icon_match = _SEARCH_ICON_CLICK_RE.match(line)
        if not icon_match or icon_match.group("exact") == "False":
            out.append(line)
            i += 1
            continue

        j = i + 1
        skipped_lines: list[str] = []
        while j < len(lines) and (
            not lines[j].strip() or lines[j].lstrip().startswith("#")
        ):
            skipped_lines.append(lines[j])
            j += 1

        option_match = _TEXT_CLICK_RE.match(lines[j]) if j < len(lines) else None
        role_option_match = _ROLE_CLICK_RE.match(lines[j]) if j < len(lines) else None
        if (
            option_match
            and option_match.group("page") == icon_match.group("page")
            and option_match.group("exact") != "False"
        ):
            indent = icon_match.group("indent")
            page_var = icon_match.group("page")
            title = icon_match.group("title")
            option = option_match.group("option")
            option_exact = "True" if option_match.group("exact") == "True" else "False"
            out.append(
                f'{indent}_ptr_select_search_trigger_option({page_var}, "{title}", "{option}", '
                f'option_kind="text", option_exact={option_exact})\n'
            )
            i = j + 1
            continue
        if (
            role_option_match
            and role_option_match.group("page") == icon_match.group("page")
            and role_option_match.group("exact") != "False"
        ):
            indent = icon_match.group("indent")
            page_var = icon_match.group("page")
            title = icon_match.group("title")
            option = role_option_match.group("option")
            option_role = role_option_match.group("role")
            option_exact = "True" if role_option_match.group("exact") == "True" else "False"
            out.append(
                f'{indent}_ptr_select_search_trigger_option({page_var}, "{title}", "{option}", '
                f'option_kind="{option_role}", option_exact={option_exact})\n'
            )
            i = j + 1
            continue

        out.append(line)
        out.extend(skipped_lines)
        i = j
    return "".join(out)


def _rewrite_adf_menu_panel_selection_calls(script_text: str) -> str:
    _TITLE_TRIGGER_RE = re.compile(
        r'^(?P<indent>[ \t]*)(?P<page>\b\w+\b)\.get_by_title\("(?P<trigger>[^"\\]+)"(?:,\s*exact\s*=\s*(?P<exact>True|False))?\)\.click\(\s*\)\s*$'
    )
    _LINK_TRIGGER_RE = re.compile(
        r'^(?P<indent>[ \t]*)(?P<page>\b\w+\b)\.get_by_role\("link",\s*name="(?P<trigger>[^"\\]+)"(?:,\s*exact\s*=\s*(?P<exact>True|False))?\)\.click\(\s*\)\s*$'
    )
    _TEXT_OPTION_RE = re.compile(
        r'^(?P<indent>[ \t]*)(?P<page>\b\w+\b)\.get_by_text\("(?P<option>[^"\\]+)"(?:,\s*exact\s*=\s*(?P<exact>True|False))?\)\.click\(\s*\)\s*$'
    )

    lines = script_text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        trigger_match = _TITLE_TRIGGER_RE.match(line)
        trigger_kind = "title"
        if trigger_match and (
            trigger_match.group("exact") == "False"
            or trigger_match.group("trigger").lower().startswith("search:")
            or "select date" in trigger_match.group("trigger").lower()
        ):
            trigger_match = None
        if trigger_match is None:
            trigger_match = _LINK_TRIGGER_RE.match(line)
            trigger_kind = "link"
            if trigger_match and trigger_match.group("exact") == "False":
                trigger_match = None

        if not trigger_match:
            out.append(line)
            i += 1
            continue

        j = i + 1
        skipped_lines: list[str] = []
        while j < len(lines) and (
            not lines[j].strip() or lines[j].lstrip().startswith("#")
        ):
            skipped_lines.append(lines[j])
            j += 1

        option_match = _TEXT_OPTION_RE.match(lines[j]) if j < len(lines) else None
        if (
            option_match
            and option_match.group("page") == trigger_match.group("page")
            and option_match.group("exact") != "False"
        ):
            indent = trigger_match.group("indent")
            page_var = trigger_match.group("page")
            trigger = trigger_match.group("trigger")
            option = option_match.group("option")
            out.append(
                f'{indent}_ptr_select_adf_menu_panel_option({page_var}, "{trigger}", "{option}", trigger_kind="{trigger_kind}")\n'
            )
            i = j + 1
            continue

        out.append(line)
        out.extend(skipped_lines)
        i = j
    return "".join(out)


def _rewrite_combobox_selection_calls(script_text: str) -> str:
    _COMBOBOX_CLICK_RE = re.compile(
        r'^(?P<indent>[ \t]*)(?P<page>\b\w+\b)\.get_by_role\("combobox",\s*name="(?P<label>[^"\\]+)"(?:,\s*exact\s*=\s*(?P<exact>True|False))?\)(?:\.locator\("a"\))?\.click\(\s*\)\s*$'
    )
    _OPTION_CLICK_RE = re.compile(
        r'^(?P<indent>[ \t]*)(?P<page>\b\w+\b)\.get_by_role\("(?P<role>option|cell|gridcell)",\s*name="(?P<option>[^"\\]+)"(?:,\s*exact\s*=\s*(?P<exact>True|False))?\)(?:\.first)?\.click\(\s*\)\s*$'
    )

    lines = script_text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        combo_match = _COMBOBOX_CLICK_RE.match(line)
        if not combo_match or combo_match.group("exact") == "False":
            out.append(line)
            i += 1
            continue

        j = i + 1
        skipped_lines: list[str] = []
        while j < len(lines) and (
            not lines[j].strip() or lines[j].lstrip().startswith("#")
        ):
            skipped_lines.append(lines[j])
            j += 1

        option_match = _OPTION_CLICK_RE.match(lines[j]) if j < len(lines) else None
        if (
            option_match
            and option_match.group("page") == combo_match.group("page")
            and option_match.group("exact") != "False"
        ):
            indent = combo_match.group("indent")
            page_var = combo_match.group("page")
            label = combo_match.group("label")
            option = option_match.group("option")
            out.append(f'{indent}_ptr_select_combobox_option({page_var}, "{label}", "{option}")\n')
            i = j + 1
            continue

        out.append(line)
        out.extend(skipped_lines)
        i = j
    return "".join(out)


def _rewrite_combobox_click_calls(script_text: str) -> str:
    _COMBOBOX_CLICK_RE = re.compile(
        r'(?P<page>\b\w+\b)\.get_by_role\("combobox",\s*name="(?P<label>[^"\\]+)"(?:,\s*exact\s*=\s*(?P<exact>True|False))?\)(?:\.locator\("a"\))?\.click\((?P<args>.*?)\)'
    )

    def _repl(match: re.Match[str]) -> str:
        page_var = match.group("page")
        label = match.group("label")
        exact_value = match.group("exact")
        if exact_value == "False":
            return match.group(0)
        args_expr = (match.group("args") or "").strip()
        if args_expr:
            return f'_ptr_click_combobox({page_var}, "{label}", {args_expr})'
        return f'_ptr_click_combobox({page_var}, "{label}")'

    rewritten_lines = []
    for line in script_text.splitlines(keepends=True):
        rewritten_lines.append(_COMBOBOX_CLICK_RE.sub(_repl, line))
    return "".join(rewritten_lines)


def _rewrite_date_picker_click_calls(script_text: str) -> str:
    _DATE_ICON_CLICK_RE = re.compile(
        r'^(?P<indent>[ \t]*)(?P<page>\b\w+\b)\.get_by_title\("(?P<title>[^"\\]+)"(?:,\s*exact\s*=\s*(?P<exact>True|False))?\)\.click\(\s*\)\s*$'
    )
    _DAY_CLICK_RE = re.compile(
        r'^(?P<indent>[ \t]*)(?P<page>\b\w+\b)\.get_by_role\("(?P<role>button|cell|gridcell)",\s*name="(?P<day>[0-9]{1,2})"(?:,\s*exact\s*=\s*(?P<exact>True|False))?\)\.click\(\s*\)\s*$'
    )

    lines = script_text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        icon_match = _DATE_ICON_CLICK_RE.match(line)
        title = icon_match.group("title") if icon_match else ""
        if (
            not icon_match
            or icon_match.group("exact") == "False"
            or "select date" not in title.lower()
        ):
            out.append(line)
            i += 1
            continue

        j = i + 1
        skipped_lines: list[str] = []
        while j < len(lines) and (
            not lines[j].strip() or lines[j].lstrip().startswith("#")
        ):
            skipped_lines.append(lines[j])
            j += 1

        day_match = _DAY_CLICK_RE.match(lines[j]) if j < len(lines) else None
        if (
            day_match
            and day_match.group("page") == icon_match.group("page")
            and day_match.group("exact") != "False"
        ):
            indent = icon_match.group("indent")
            page_var = icon_match.group("page")
            day_label = day_match.group("day")
            out.append(f'{indent}_ptr_pick_date_via_icon({page_var}, "{title}", "{day_label}")\n')
            i = j + 1
            continue

        out.append(line)
        out.extend(skipped_lines)
        i = j
    return "".join(out)


def _rewrite_navigation_button_click_calls(script_text: str) -> str:
    _NAV_BUTTON_CLICK_RE = re.compile(
        r'(?P<page>\b\w+\b)\.get_by_role\("button",\s*name="(?P<label>Continue|Submit|Next|Review|Back|Go back)"(?:,\s*exact\s*=\s*(?P<exact>True|False))?\)\.click\((?P<args>.*?)\)'
    )

    def _repl(match: re.Match[str]) -> str:
        page_var = match.group("page")
        label = match.group("label")
        exact_value = match.group("exact")
        if exact_value == "False":
            return match.group(0)
        args_expr = (match.group("args") or "").strip()
        if args_expr:
            return f'_ptr_click_navigation_button({page_var}, "{label}", {args_expr})'
        return f'_ptr_click_navigation_button({page_var}, "{label}")'

    rewritten_lines = []
    for line in script_text.splitlines(keepends=True):
        rewritten_lines.append(_NAV_BUTTON_CLICK_RE.sub(_repl, line))
    return "".join(rewritten_lines)


def _rewrite_post_login_goto_calls(script_text: str) -> str:
    _PASSWORD_ENTER_RE = re.compile(
        r'^(?P<indent>[ \t]*)(?P<page>\b\w+\b)\.get_by_role\("textbox",\s*name="Password"\)\.press\("Enter"\)\s*$'
    )
    _GOTO_RE = re.compile(
        r'^(?P<indent>[ \t]*)(?P<page>\b\w+\b)\.goto\("(?P<url>[^"\\]+)"(?:,\s*.*)?\)\s*$'
    )

    lines = script_text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        password_enter_m = _PASSWORD_ENTER_RE.match(line)
        if not password_enter_m:
            out.append(line)
            i += 1
            continue

        out.append(line)
        j = i + 1
        skipped_lines: list[str] = []
        while j < len(lines) and (not lines[j].strip() or lines[j].lstrip().startswith("#")):
            skipped_lines.append(lines[j])
            j += 1

        if j < len(lines):
            goto_m = _GOTO_RE.match(lines[j])
            if goto_m and goto_m.group("page") == password_enter_m.group("page"):
                indent = goto_m.group("indent")
                page_var = goto_m.group("page")
                url = goto_m.group("url")
                out.extend(skipped_lines)
                out.append(f'{indent}_ptr_wait_for_post_login_redirect({page_var}, "{url}")\n')
                i = j + 1
                continue

        out.extend(skipped_lines)
        i += 1

    return "".join(out)


def _prepare_script_for_execution(script_text: str, parameters: dict[str, Any] | None = None) -> str:
    _validate_python_playwright_script(script_text)
    if parameters:
        script_text = _substitute_parameters(script_text, parameters)
    script_text = _strip_redundant_textbox_focus_clicks(script_text)
    script_text = _rewrite_textbox_click_calls(script_text)
    script_text = _rewrite_textbox_fill_calls(script_text)
    script_text = _rewrite_search_popup_selection_calls(script_text)
    script_text = _rewrite_adf_menu_panel_selection_calls(script_text)
    script_text = _rewrite_exact_text_click_calls(script_text)
    script_text = _rewrite_combobox_selection_calls(script_text)
    script_text = _rewrite_combobox_click_calls(script_text)
    script_text = _rewrite_date_picker_click_calls(script_text)
    script_text = _rewrite_navigation_button_click_calls(script_text)
    script_text = _rewrite_post_login_goto_calls(script_text)
    script_text = _rewrite_exact_button_click_calls(script_text)
    script_text = _inject_network_idle_waits(script_text)
    return _inject_runtime_helpers(script_text)


def _read_failure_diagnostics(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read diagnostics from %s", path)
        return {}


def _run_python_script(
    script_path: Path,
    working_dir: Path,
    *,
    timeout_seconds: int,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    run_env = dict(env)
    run_env.setdefault("PYTHONUNBUFFERED", "1")
    python_bin = str(run_env.get("PLAYWRIGHT_TEST_PYTHON_BIN") or "python3").strip() or "python3"
    return subprocess.run(
        [python_bin, str(script_path)],
        cwd=str(working_dir),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=run_env,
    )


@tool()
async def expand_recordings_for_parameter_rows(recordings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_expand_recordings_for_parameter_rows_data, recordings)


@tool()
async def execute_recording_script(
    recording: dict[str, Any],
    test_suite_id: str,
    parent_run_id: str,
) -> dict[str, Any]:
    recording_id = str(recording.get("id") or "unknown")
    file_key = str(recording.get("file") or recording.get("recording_name") or "").strip()
    recording_name = str(recording.get("name") or "").strip() or file_key or recording_id
    parameter_row_index = recording.get("parameter_row_index")
    parameter_set_index = recording.get("parameter_set_index")
    artifact_identity = file_key or recording_name or recording_id
    if parameter_row_index not in (None, ""):
        artifact_identity = f"{artifact_identity}__row_{parameter_row_index}"
    elif parameter_set_index not in (None, ""):
        artifact_identity = f"{artifact_identity}__set_{parameter_set_index}"

    artifact_prefix = (
        f"playwright-test-results/{_safe_segment(test_suite_id)}/{_safe_segment(parent_run_id)}"
        f"/{_safe_segment(artifact_identity)}"
    )
    manifest_key = f"{artifact_prefix}/manifest.json"

    result: dict[str, Any] = {
        "recording_id": recording_id,
        "recording_name": recording_name,
        "file_key": file_key,
        "parameter_row_index": parameter_row_index,
        "parameter_set_index": parameter_set_index,
        "status": "failed",
        "exit_code": -1,
        "duration_seconds": 0,
        "stdout": "",
        "stderr": "",
        "error": None,
        "page_url": None,
        "page_title": None,
        "screenshot_s3_key": None,
        "video_s3_key": None,
        "video_s3_keys": [],
        "step_artifacts": [],
        "ai_failure_summary": None,
        "parameters_file_key": None,
        "resolved_parameter_count": 0,
        "resolved_parameter_keys": [],
    }

    start_time = time.time()

    if not file_key:
        result["error"] = "Recording file key is required."
        _storage_put_bytes(
            manifest_key,
            json.dumps(result, indent=2).encode("utf-8"),
            content_type="application/json",
        )
        result["result_s3_key"] = manifest_key
        return result

    try:
        raw_script_bytes = await asyncio.to_thread(_load_script_bytes, file_key)
        logger.info("Downloaded recording script for %s (%s bytes)", file_key, len(raw_script_bytes))

        # Auto-parameterise: extract hardcoded values as defaults and inject
        # {{placeholders}} in one pass — no manual script editing required.
        parameterised_script, default_params = _parameterise_script(raw_script_bytes.decode("utf-8"))
        logger.info("Auto-extracted %d default parameter(s) from script", len(default_params))

        # Merge order: script defaults → Excel file overrides → inline overrides.
        # Before execution we normalize the merged values into a JSON object and
        # substitute placeholders from that JSON payload.
        parameters: dict[str, str] = _normalize_parameter_values(default_params)
        parameters_file_key = str(recording.get("parameters_file_key") or "").strip() or None
        if not bool(recording.get("skip_parameters_file_load")):
            try:
                file_params, loaded_from = await asyncio.to_thread(_load_recording_parameters, recording, file_key)
                if file_params:
                    parameters.update(_normalize_parameter_values(file_params))
                    parameters_file_key = loaded_from
                    logger.info("Loaded %d parameter(s) from %s", len(file_params), loaded_from)
                elif loaded_from:
                    parameters_file_key = loaded_from
            except Exception as exc:
                parameters_file = str(recording.get("parameters_file") or "").strip()
                logger.warning("Failed to load parameters file %s: %s", parameters_file or file_key, exc)
        inline = _normalize_parameter_values(recording.get("parameters") or {})
        parameters.update(inline)

        execution_parameters = _parameters_to_json_object(parameters)
        result["parameters_file_key"] = parameters_file_key
        result["resolved_parameter_count"] = len(execution_parameters)
        result["resolved_parameter_keys"] = sorted(execution_parameters)
        logger.info(
            "Resolved %d execution parameter(s) for %s: %s",
            len(execution_parameters),
            file_key,
            ", ".join(sorted(execution_parameters)),
        )

        prepared_script = _prepare_script_for_execution(
            parameterised_script,
            execution_parameters or None,
        )
    except Exception as exc:
        logger.exception("Failed to download or prepare recording script for %s", file_key)
        result["error"] = f"Failed to download or prepare recording script: {exc}"
        _storage_put_bytes(
            manifest_key,
            json.dumps(result, indent=2).encode("utf-8"),
            content_type="application/json",
        )
        result["result_s3_key"] = manifest_key
        return result

    with tempfile.TemporaryDirectory(prefix="playwright-test-runner-") as temp_dir:
        working_dir = Path(temp_dir)
        script_path = working_dir / f"{_safe_segment(Path(file_key).stem)}.py"
        script_path.write_text(prepared_script, encoding="utf-8")

        diagnostics_path = working_dir / "diagnostics.json"
        failure_screenshot_path = working_dir / "failure.png"
        step_artifacts_dir = working_dir / "steps"
        step_artifacts_dir.mkdir(parents=True, exist_ok=True)
        video_dir = working_dir / "video"
        video_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["PTR_DIAGNOSTICS_PATH"] = str(diagnostics_path)
        env["PTR_FAILURE_SCREENSHOT_PATH"] = str(failure_screenshot_path)
        env["PTR_STEP_ARTIFACTS_DIR"] = str(step_artifacts_dir)
        env["PTR_VIDEO_DIR"] = str(video_dir)

        python_bin = str(env.get("PLAYWRIGHT_TEST_PYTHON_BIN") or "python3").strip() or "python3"

        try:
            completed = await asyncio.to_thread(
                _run_python_script,
                script_path,
                working_dir,
                timeout_seconds=900,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            completed = subprocess.CompletedProcess(
                args=[python_bin, str(script_path)],
                returncode=124,
                stdout=exc.stdout or "",
                stderr=exc.stderr or f"Timed out after {exc.timeout}s",
            )
        except Exception as exc:
            logger.exception("Unexpected execution failure for %s", file_key)
            completed = subprocess.CompletedProcess(
                args=[python_bin, str(script_path)],
                returncode=1,
                stdout="",
                stderr=str(exc),
            )

        diagnostics = _read_failure_diagnostics(diagnostics_path)

        result["exit_code"] = int(completed.returncode)
        result["duration_seconds"] = round(time.time() - start_time, 3)
        result["stdout"] = completed.stdout or ""
        result["stderr"] = completed.stderr or ""
        result["status"] = "passed" if completed.returncode == 0 else "failed"
        result["error"] = None if completed.returncode == 0 else (completed.stderr or "Execution failed")
        result["page_url"] = diagnostics.get("page_url")
        result["page_title"] = diagnostics.get("page_title")

        failure_local_path = diagnostics.get("failure_screenshot_path")
        failure_screenshot_path: Path | None = None
        if failure_local_path and Path(failure_local_path).exists():
            failure_screenshot_path = Path(failure_local_path)
            screenshot_key = f"{artifact_prefix}/failure.png"
            _storage_put_bytes(
                screenshot_key,
                failure_screenshot_path.read_bytes(),
                content_type="image/png",
            )
            result["screenshot_s3_key"] = screenshot_key

        step_artifacts: list[dict[str, Any]] = []
        step_image_paths: list[Path] = []
        for item in diagnostics.get("step_artifacts") or []:
            local_path = Path(str(item.get("local_path") or ""))
            if not local_path.exists():
                continue
            step_image_paths.append(local_path)
            screenshot_key = f"{artifact_prefix}/steps/{local_path.name}"
            _storage_put_bytes(
                screenshot_key,
                local_path.read_bytes(),
                content_type="image/png",
            )
            step_artifacts.append(
                {
                    "index": int(item.get("index") or 0),
                    "action": str(item.get("action") or "step"),
                    "screenshot_s3_key": screenshot_key,
                }
            )
        result["step_artifacts"] = step_artifacts

        # Oracle Fusion (and similar apps) open task pages in a new browser page,
        # so Playwright produces one .webm file per page. Upload all of them so
        # the caller can see the full recording across every page that was opened.
        video_files = sorted(video_dir.glob("*.webm"), key=lambda p: p.stat().st_mtime)
        video_s3_keys: list[str] = []
        for idx, vf in enumerate(video_files):
            vkey = f"{artifact_prefix}/recording_{idx}.webm"
            _storage_put_bytes(vkey, vf.read_bytes(), content_type="video/webm")
            video_s3_keys.append(vkey)
        if video_s3_keys:
            # Expose both the full list and a convenience key pointing to the last
            # (most recently opened) page — that is normally the one where the
            # failure occurred.
            result["video_s3_keys"] = video_s3_keys
            result["video_s3_key"] = video_s3_keys[-1]

        if result["status"] != "passed":
            result["ai_failure_summary"] = await asyncio.to_thread(
                _call_openai_failure_summary,
                result,
                failure_screenshot_path=failure_screenshot_path,
                step_image_paths=step_image_paths,
            )

    logger.info(
        "Finished recording %s with status=%s exit_code=%s duration=%ss",
        file_key,
        result["status"],
        result["exit_code"],
        result["duration_seconds"],
    )

    _storage_put_bytes(
        manifest_key,
        json.dumps(result, indent=2).encode("utf-8"),
        content_type="application/json",
    )
    result["result_s3_key"] = manifest_key
    return result


@tool()
async def generate_html_report(
    test_suite_id: str,
    parent_run_id: str,
    manifest_keys: dict[str, str],
    ordered_names: list[str],
) -> str:
    results: list[dict[str, Any]] = []
    for name in ordered_names:
        manifest_key = manifest_keys.get(name, "")
        if not manifest_key:
            results.append(
                {
                    "recording_name": name,
                    "status": "failed",
                    "duration_seconds": 0,
                    "stdout": "",
                    "stderr": "",
                    "error": "No manifest found for this recording.",
                    "page_url": None,
                    "page_title": None,
                    "screenshot_s3_key": None,
                    "step_artifacts": [],
                }
            )
            continue

        try:
            manifest = await asyncio.to_thread(_read_manifest, manifest_key)
            results.append(manifest)
        except Exception as exc:
            logger.exception("Failed to read manifest %s", manifest_key)
            results.append(
                {
                    "recording_name": name,
                    "status": "failed",
                    "duration_seconds": 0,
                    "stdout": "",
                    "stderr": "",
                    "error": f"Failed to load manifest: {exc}",
                    "page_url": None,
                    "page_title": None,
                    "screenshot_s3_key": None,
                    "step_artifacts": [],
                }
            )

    html_content = generate_html_report_content(
        test_suite_id=test_suite_id,
        parent_run_id=parent_run_id,
        results=results,
    )
    report_key = (
        f"playwright-test-results/{_safe_segment(test_suite_id)}/{_safe_segment(parent_run_id)}/report.html"
    )
    _storage_put_bytes(report_key, html_content.encode("utf-8"), content_type="text/html")
    return report_key
