#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _safe_segment(value: Any) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return cleaned.strip("._") or "unknown"


def _detect_packages(project_root: Path) -> tuple[str, ...]:
    src_path = project_root / "src"
    return tuple(
        child.name
        for child in src_path.iterdir()
        if child.is_dir() and (child / "__init__.py").exists()
    )


def _load_payload(*, payload_json: str | None, payload_file: str | None) -> dict[str, Any]:
    if payload_json:
        payload = json.loads(payload_json)
    elif payload_file:
        payload = json.loads(Path(payload_file).read_text(encoding="utf-8"))
    else:
        raise ValueError("Either --payload-json or --payload-file is required.")
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a JSON object.")
    return payload


def _extract_agent_result(stdout: str) -> Any:
    text = str(stdout or "").strip()
    if not text:
        raise ValueError("Agent produced no stdout to parse.")

    for index, char in enumerate(text):
        if char not in "[{":
            continue
        candidate = text[index:].strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ValueError(f"Could not parse JSON agent result from stdout:\n{text}")


def _find_report_key(result: Any) -> str:
    if isinstance(result, dict):
        result_items = [result]
    elif isinstance(result, list):
        result_items = [item for item in result if isinstance(item, dict)]
    else:
        result_items = []

    for item in result_items:
        if str(item.get("type") or "").strip() == "s3_download_link":
            file_key = str(item.get("file_key") or "").strip()
            if file_key:
                return file_key
    raise ValueError(f"No s3_download_link file_key found in agent result: {json.dumps(result, indent=2)}")


def _extract_suite_payload(payload: dict[str, Any]) -> dict[str, Any]:
    wrapped = payload.get("0")
    if isinstance(wrapped, dict):
        return wrapped
    return payload


def _default_output_path(project_root: Path, payload: dict[str, Any], report_key: str) -> Path:
    suite_payload = _extract_suite_payload(payload)
    suite_id = _safe_segment(suite_payload.get("test_suite_id") or "test_suite")
    report_parts = [part for part in Path(report_key).parts if part not in (".", "")]
    run_id = _safe_segment(report_parts[-2] if len(report_parts) >= 2 else "latest")
    return project_root / "downloads" / f"{suite_id}-{run_id}-report.html"


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

    bucket_name = os.getenv("TENANT_ID", "").strip() or os.getenv("STORAGE_ACTIVITIES_BUCKET", "").strip()
    if not bucket_name:
        raise RuntimeError("Neither TENANT_ID nor STORAGE_ACTIVITIES_BUCKET is configured.")

    bucket_prefix = f"{bucket_name}/"
    object_key = raw[len(bucket_prefix) :] if raw.startswith(bucket_prefix) else raw
    object_key = object_key.lstrip("/")
    if not object_key:
        raise ValueError("Storage object key is required.")
    return bucket_name, object_key


def _download_storage_bytes(object_ref: str, project_root: Path) -> bytes:
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    src_path = str(project_root / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    from aetherion_sdk.config import apply_project_config
    from common_lib.storage.storage_client import RetrievalMode, storage

    apply_project_config(_detect_packages(project_root))
    storage.init_client()
    bucket_name, object_key = _split_storage_object_ref(object_ref)

    data = storage.retrieve(
        bucket_name=bucket_name,
        object_key=object_key,
        retrieval_mode=RetrievalMode.FULL_OBJECT,
    )
    if isinstance(data, bytes):
        return data

    client = getattr(storage, "client", None)
    if client is None:
        raise RuntimeError("Storage client is not initialized.")
    response = client.get_object(Bucket=bucket_name, Key=object_key)
    return response["Body"].read()


def _build_agent_command(
    *,
    aetherion_bin: str,
    agent_name: str,
    payload: dict[str, Any],
    run_id: str | None,
    task_queue: str | None,
) -> list[str]:
    command = [aetherion_bin, "agent", agent_name, json.dumps(payload), "--wait"]
    if run_id:
        command.extend(["--id", run_id])
    if task_queue:
        command.extend(["--task-queue", task_queue])
    return command


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the local test_runner agent and download the generated HTML report from MinIO."
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--payload-json", help="Inline JSON payload to send to the agent.")
    source_group.add_argument("--payload-file", help="Path to a JSON file containing the agent payload.")
    parser.add_argument("--agent-name", default="test_runner", help="Agent name to execute. Default: test_runner")
    parser.add_argument(
        "--aetherion-bin",
        default=str(PROJECT_ROOT / ".venv" / "bin" / "aetherion"),
        help="Path to the local aetherion CLI binary.",
    )
    parser.add_argument("--id", dest="run_id", help="Optional workflow/run id to pass to aetherion agent.")
    parser.add_argument("--task-queue", help="Optional task queue override to pass through.")
    parser.add_argument(
        "--output",
        help="Destination HTML path. Default: downloads/<test_suite_id>-<run_id>-report.html",
    )
    args = parser.parse_args()

    payload = _load_payload(payload_json=args.payload_json, payload_file=args.payload_file)
    command = _build_agent_command(
        aetherion_bin=args.aetherion_bin,
        agent_name=args.agent_name,
        payload=payload,
        run_id=args.run_id,
        task_queue=args.task_queue,
    )

    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    if completed.returncode != 0:
        if completed.stdout:
            print(completed.stdout, end="", file=sys.stdout)
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        return completed.returncode

    result = _extract_agent_result(completed.stdout)
    report_key = _find_report_key(result)
    report_bytes = _download_storage_bytes(report_key, PROJECT_ROOT)

    output_path = Path(args.output).expanduser() if args.output else _default_output_path(PROJECT_ROOT, payload, report_key)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(report_bytes)

    print(f"Downloaded HTML report to: {output_path}")
    print(f"Storage object: {report_key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
