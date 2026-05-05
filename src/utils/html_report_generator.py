from __future__ import annotations

import base64
import json
import os
import re
from functools import lru_cache
from html import escape
from typing import Any

from common_lib.storage.storage_client import RetrievalMode, storage

_AETHERION_HEADER_ICON = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' width='174' height='30' viewBox='0 0 174 30' fill='none'>"
    "<rect x='1' y='1' width='28' height='28' rx='8' fill='%23F4F7FF' stroke='%23D7E0F4'/>"
    "<path d='M9 10.5 15 7l6 3.5v7L15 21l-6-3.5v-7Z' stroke='%23172233' stroke-width='1.7' stroke-linejoin='round' fill='none'/>"
    "<path d='M15 11.2v7.1M9.7 10.8 15 14l5.3-3.2' stroke='%23172233' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'/>"
    "<text x='40' y='20' font-family='Syne, Arial, sans-serif' font-size='12' font-weight='700' fill='%23172233'>Aetherion</text>"
    "</svg>"
)


def _get_bucket_name() -> str:
    bucket_name = os.getenv("STORAGE_ACTIVITIES_BUCKET", "").strip()
    if not bucket_name:
        raise RuntimeError("STORAGE_ACTIVITIES_BUCKET is not configured.")
    return bucket_name


def _load_bytes(object_key: str | None) -> bytes | None:
    key = str(object_key or "").strip()
    if not key:
        return None
    try:
        storage.init_client()
        if hasattr(storage, "retrieve"):
            data = storage.retrieve(
                bucket_name=_get_bucket_name(),
                object_key=key,
                retrieval_mode=RetrievalMode.FULL_OBJECT,
            )
            if isinstance(data, bytes):
                return data
        client = getattr(storage, "client", None)
        if client is None:
            return None
        response = client.get_object(Bucket=_get_bucket_name(), Key=key)
        return response["Body"].read()
    except Exception:
        return None


@lru_cache(maxsize=512)
def _to_data_uri(object_key: str | None) -> str | None:
    image_bytes = _load_bytes(object_key)
    if not image_bytes:
        return None
    return f"data:image/png;base64,{base64.b64encode(image_bytes).decode('utf-8')}"


def _format_duration_minutes(duration_seconds: Any) -> str:
    try:
        seconds = max(0.0, float(duration_seconds or 0))
    except Exception:
        seconds = 0.0
    minutes = round(seconds / 60.0, 1)
    if float(minutes).is_integer():
        value = str(int(minutes))
    else:
        value = f"{minutes:.1f}".rstrip("0").rstrip(".")
    unit = "min" if value == "1" else "mins"
    return f"{value} {unit}"


def _format_action_duration(duration_ms: Any) -> str:
    try:
        total_ms = max(0, int(duration_ms or 0))
    except Exception:
        total_ms = 0

    if total_ms <= 0:
        return "0:00"
    if total_ms < 1000:
        return "<0:01"

    rounded_seconds = int(round(total_ms / 1000.0))
    hours, remainder = divmod(rounded_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _format_duration_seconds(value: Any) -> str:
    try:
        total = max(0.0, float(value or 0))
    except Exception:
        total = 0.0
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    seconds = int(total % 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {seconds:02d}s"


def _format_duration_ms(value: Any) -> str:
    try:
        total_ms = max(0, int(value or 0))
    except Exception:
        total_ms = 0
    return _format_duration_seconds(total_ms / 1000.0)


def _duration_markup(value: Any) -> str:
    text = _format_duration_seconds(value)
    parts = re.findall(r"(\d+)([hms])", text)
    if not parts:
        return escape(text)

    chunks: list[str] = []
    for index, (number, unit) in enumerate(parts):
        if index:
            chunks.append('<span class="dur-gap"></span>')
        chunks.append(
            f'<span class="dur-part"><span class="dur-num">{escape(number)}</span>'
            f'<span class="dur-unit">{escape(unit)}</span></span>'
        )
    return "".join(chunks)


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


_SENSITIVE_NAME_RE = re.compile(
    r"\b(password|passcode|secret|api[\s_-]?key|access[\s_-]?key|secret[\s_-]?key|auth[\s_-]?token|token)\b",
    re.IGNORECASE,
)
_SECRET_MASK = "*****"


def _is_sensitive_name(value: Any) -> bool:
    return bool(_SENSITIVE_NAME_RE.search(_safe_text(value)))


def _action_is_sensitive(action: dict[str, Any]) -> bool:
    parsed_action = ((action.get("script_data") or {}).get("parsed_action") or {})
    markers = [
        action.get("label"),
        parsed_action.get("name"),
        parsed_action.get("label"),
        parsed_action.get("placeholder"),
        parsed_action.get("aria_label"),
    ]
    return any(_is_sensitive_name(marker) for marker in markers)


def _result_sensitive_literals(result: dict[str, Any]) -> set[str]:
    literals: set[str] = set()

    for action in result.get("action_log") or []:
        parsed_action = ((action.get("script_data") or {}).get("parsed_action") or {})
        if not _action_is_sensitive(action):
            continue
        value = _safe_text(parsed_action.get("value"))
        if value and not value.startswith("{{"):
            literals.add(value)

    for item in (result.get("flow_input_status") or {}).values():
        if not any(_is_sensitive_name(item.get(field)) for field in ("name", "label")):
            continue
        value = _safe_text(item.get("value"))
        if value and not value.startswith("{{"):
            literals.add(value)

    for item in result.get("flow_output_results") or []:
        if not any(_is_sensitive_name(item.get(field)) for field in ("name", "label")):
            continue
        value = _safe_text(item.get("value"))
        if value and not value.startswith("{{"):
            literals.add(value)

    return literals


def _redact_sensitive_literals(html: str, literals: set[str]) -> str:
    redacted = html
    for literal in sorted({item for item in literals if item}, key=len, reverse=True):
        for variant in {literal, escape(literal)}:
            if variant and variant != _SECRET_MASK:
                redacted = redacted.replace(variant, _SECRET_MASK)
    return redacted


def _result_name(result: dict[str, Any]) -> str:
    preferred = [
        _safe_text(result.get("recording_name")),
        _safe_text(result.get("recording_id")),
        _safe_text(result.get("file_key")),
    ]
    raw_name = next((value for value in preferred if value), "Recording")
    leaf = raw_name.rsplit("/", 1)[-1]
    if leaf.endswith(".py"):
        leaf = leaf[:-3]
    return leaf or "Recording"


def _result_title(result: dict[str, Any]) -> str:
    return _result_name(result).replace("_", " ")


def _result_status(result: dict[str, Any]) -> str:
    raw = _safe_text(result.get("status")).lower()
    return "passed" if raw in {"passed", "success", "completed"} else "failed"


def _is_result_failed(result: dict[str, Any]) -> bool:
    return _result_status(result) == "failed"


def _status_chip_html(status: str) -> str:
    normalized = _safe_text(status).lower()
    tone = "status-passed" if normalized in {"passed", "success", "completed"} else "status-failed"
    return f'<span class="status-chip {tone}">{escape(status)}</span>'


def _artifact_map(result: dict[str, Any]) -> dict[int, dict[str, Any]]:
    artifacts: dict[int, dict[str, Any]] = {}
    for artifact in result.get("step_artifacts") or []:
        try:
            index = int(artifact.get("index") or 0)
        except Exception:
            continue
        if index > 0:
            artifacts[index] = artifact
    return artifacts


def _step_image_data_uri(result: dict[str, Any], action: dict[str, Any]) -> str | None:
    artifact = _artifact_map(result).get(int(action.get("step") or 0))
    if not artifact:
        return None
    return _to_data_uri(str(artifact.get("screenshot_s3_key") or ""))


def _failure_image_data_uri(result: dict[str, Any]) -> str | None:
    return _to_data_uri(str(result.get("screenshot_s3_key") or ""))


def _action_tone(action: str) -> str:
    value = _safe_text(action).lower()
    if value in {"goto", "navigation_button"}:
        return "goto"
    if value in {"select_combobox", "search_and_select", "date_pick", "click_combobox"}:
        return "select"
    return "default"


def _strategy_label(strategy: Any) -> str:
    raw = str(strategy or "").strip()
    mapping = {
        "direct": "Raw Locator",
        "experience_lookup": "Experience",
        "ai_self_repair_lookup": "AI Repair",
        "oracle_select_single_arrowdown": "Oracle Select Handler",
        "oracle_quick_action_exact_role": "Oracle Quick Action",
        "raw_option": "Raw Option",
        "role_option": "Role Option",
        "role_cell": "Role Cell",
        "role_gridcell": "Role Gridcell",
        "day_select": "Day Select",
    }
    if raw in mapping:
        return mapping[raw]
    if raw.startswith("ai_"):
        parts = raw.split("_")
        if len(parts) >= 2:
            label = " ".join(
                part.upper() if part.isalpha() and len(part) <= 5 else part.title()
                for part in parts[1:]
            )
            return f"AI {label}"
        return "AI Repair"
    return raw.replace("_", " ").title()


def _strategy_tone(strategy: Any, index: int, last_index: int, status: str) -> str:
    raw = str(strategy or "")
    if index == last_index:
        return "failed" if status == "failed" else "success"
    if raw == "direct":
        return "direct"
    if raw.startswith("ai_") or raw == "ai_self_repair_lookup":
        return "ai"
    if raw.startswith("oracle_"):
        return "oracle"
    return "fallback"


def _chain_icon(strategy: Any, tone: str) -> str:
    raw = str(strategy or "")

    if raw == "direct":
        return (
            '<svg viewBox="0 0 24 24" aria-hidden="true">'
            '<circle cx="12" cy="12" r="7" stroke="currentColor" stroke-width="1.9" fill="none"/>'
            '<path d="M12 3v4M12 17v4M3 12h4M17 12h4" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/>'
            '<circle cx="12" cy="12" r="1.8" fill="currentColor"/>'
            "</svg>"
        )
    if raw == "experience_lookup":
        return (
            '<svg viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M7 7h7v7" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
            '<path d="M17 17H10V10" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
            '<path d="M14 7h3v3" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
            "</svg>"
        )
    if raw == "ai_self_repair_lookup":
        return (
            '<svg viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M12 3l1.7 4.7L18.5 9.5l-4.8 1.8L12 16l-1.7-4.7-4.8-1.8 4.8-1.8L12 3z" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linejoin="round"/>'
            '<path d="M18 4l.7 1.9L20.6 6.6l-1.9.7-.7 1.9-.7-1.9-1.9-.7 1.9-.7L18 4z" fill="currentColor"/>'
            "</svg>"
        )
    if raw.startswith("ai_css"):
        return (
            '<svg viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M8 7L4.5 12 8 17" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
            '<path d="M16 7l3.5 5-3.5 5" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
            '<path d="M11 18l2-12" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/>'
            "</svg>"
        )
    if raw.startswith("ai_xpath"):
        return (
            '<svg viewBox="0 0 24 24" aria-hidden="true">'
            '<circle cx="6" cy="6" r="2" fill="currentColor"/>'
            '<circle cx="18" cy="6" r="2" fill="currentColor"/>'
            '<circle cx="12" cy="18" r="2" fill="currentColor"/>'
            '<path d="M8 6h8M7.5 7.5l3.2 8M16.5 7.5l-3.2 8" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
            "</svg>"
        )
    if raw.startswith("ai_text"):
        return (
            '<svg viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M6 8h12M6 12h8M6 16h10" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/>'
            '<path d="M4.5 8h0M4.5 12h0M4.5 16h0" stroke="currentColor" stroke-width="2.8" stroke-linecap="round"/>'
            "</svg>"
        )
    if raw.startswith("oracle_"):
        return (
            '<svg viewBox="0 0 24 24" aria-hidden="true">'
            '<rect x="4.5" y="5" width="15" height="14" rx="3" stroke="currentColor" stroke-width="1.9" fill="none"/>'
            '<path d="M8 10h8M8 14h5" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/>'
            '<path d="M15 12l2 2 2-2" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
            "</svg>"
        )
    if tone == "failed":
        return (
            '<svg viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M12 4.5L20 18.5H4L12 4.5z" stroke="currentColor" stroke-width="1.9" fill="none" stroke-linejoin="round"/>'
            '<path d="M12 9v4.5" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/>'
            '<circle cx="12" cy="16.5" r="1.1" fill="currentColor"/>'
            "</svg>"
        )
    if tone == "success":
        return (
            '<svg viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M6.5 12.5l3.4 3.4 7.6-8.1" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
            "</svg>"
        )
    if tone == "ai":
        return (
            '<svg viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M12 4l1.6 4.4L18 10l-4.4 1.6L12 16l-1.6-4.4L6 10l4.4-1.6L12 4z" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linejoin="round"/>'
            "</svg>"
        )
    if tone == "oracle":
        return (
            '<svg viewBox="0 0 24 24" aria-hidden="true">'
            '<rect x="5" y="5" width="14" height="14" rx="3" stroke="currentColor" stroke-width="2" fill="none"/>'
            '<path d="M8 12h8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
            "</svg>"
        )
    if tone == "fallback":
        return (
            '<svg viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M7 7h8M7 7l3-3M7 7l3 3M17 17H9M17 17l-3-3M17 17l-3 3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
            "</svg>"
        )
    return (
        '<svg viewBox="0 0 24 24" aria-hidden="true">'
        '<circle cx="12" cy="12" r="6" stroke="currentColor" stroke-width="2" fill="none"/>'
        "</svg>"
    )


def _extract_ai_error_text(interaction: dict[str, Any]) -> str:
    explicit = _safe_text(
        interaction.get("sent_error")
        or interaction.get("last_error")
        or interaction.get("error")
    )
    if explicit:
        return explicit

    prompt = _safe_text(interaction.get("user_prompt"))
    if not prompt:
        return ""

    match = re.search(
        r"- Last error:\s*(.*?)(?:\nRecorded script data JSON:|\nRecorded target context JSON:|\nDOM candidates JSON:|\Z)",
        prompt,
        re.S,
    )
    if match:
        return match.group(1).strip()
    return ""


def _extract_prompt_json_section(prompt: str, heading: str) -> Any:
    prompt_text = _safe_text(prompt)
    if not prompt_text:
        return None

    marker = f"{heading}:\n"
    start = prompt_text.find(marker)
    if start == -1:
        return None
    start += len(marker)

    tail = prompt_text[start:]
    end = len(tail)
    for next_marker in [
        "\nRecorded script data JSON:\n",
        "\nRecorded target context JSON:\n",
        "\nDOM candidates JSON:\n",
    ]:
        idx = tail.find(next_marker)
        if idx != -1 and idx < end:
            end = idx

    section = tail[:end].strip()
    if not section:
        return None
    try:
        return json.loads(section)
    except Exception:
        return section


def _highlight_json(raw: str) -> str:
    clean = _safe_text(raw)
    if not clean:
        return ""

    try:
        parsed = json.loads(clean)
    except Exception:
        return f'<pre class="path-ai-pre">{escape(clean)}</pre>'

    pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
    token_re = re.compile(
        r'("(?:[^"\\\\]|\\\\.)*")(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?'
    )

    def repl(match: re.Match[str]) -> str:
        string_token = match.group(1)
        has_colon = match.group(2)
        literal = match.group(0)
        if string_token is not None:
            if has_colon:
                return f'<span class="json-key">{escape(string_token)}</span>{escape(has_colon)}'
            return f'<span class="json-string">{escape(string_token)}</span>'
        if literal in {"true", "false"}:
            return f'<span class="json-bool">{escape(literal)}</span>'
        if literal == "null":
            return f'<span class="json-null">{escape(literal)}</span>'
        return f'<span class="json-number">{escape(literal)}</span>'

    parts: list[str] = []
    cursor = 0
    for match in token_re.finditer(pretty):
        parts.append(escape(pretty[cursor:match.start()]))
        parts.append(repl(match))
        cursor = match.end()
    parts.append(escape(pretty[cursor:]))
    return f'<pre class="path-ai-pre json-pre">{"".join(parts)}</pre>'


def _ai_block(
    title: str,
    body: str,
    *,
    icon: str,
    tone: str = "violet",
    open_by_default: bool = False,
    render_json: bool = False,
) -> str:
    clean = _safe_text(body)
    if not clean:
        return ""

    open_attr = " open" if open_by_default else ""
    markup = _highlight_json(clean) if render_json else f'<pre class="path-ai-pre">{escape(clean)}</pre>'
    return (
        f'<details class="path-ai-panel tone-{escape(tone)}"{open_attr}>'
        '<summary class="path-ai-panel-summary">'
        f'<span class="path-ai-panel-icon">{icon}</span>'
        '<span class="path-ai-panel-copy">'
        f'<span class="path-ai-panel-title">{escape(title)}</span>'
        "</span>"
        '<span class="path-ai-panel-chevron" aria-hidden="true">'
        '<svg viewBox="0 0 18 18">'
        '<path d="M4 7l5 5 5-5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
        "</svg>"
        "</span>"
        "</summary>"
        '<div class="path-ai-panel-body">'
        f"{markup}"
        "</div>"
        "</details>"
    )


def _ai_request_payload_json(interaction: dict[str, Any]) -> str:
    payload: dict[str, Any] = {}

    for field in ("model", "feature", "helper", "label", "max_output_tokens"):
        value = interaction.get(field)
        if value not in (None, "", []):
            payload[field] = value

    system_prompt = _safe_text(interaction.get("system_prompt"))
    user_prompt = _safe_text(interaction.get("user_prompt") or interaction.get("user_prompt_excerpt"))
    sent_error = _extract_ai_error_text(interaction)

    recorded_script_data = interaction.get("recorded_script_data")
    if recorded_script_data in (None, {}, []):
        recorded_script_data = _extract_prompt_json_section(user_prompt, "Recorded script data JSON")

    recorded_target_context = interaction.get("recorded_target_context")
    if recorded_target_context in (None, {}, []):
        recorded_target_context = _extract_prompt_json_section(user_prompt, "Recorded target context JSON")

    dom_candidates = interaction.get("dom_candidates")
    if dom_candidates in (None, [], {}):
        dom_json = _extract_prompt_json_section(user_prompt, "DOM candidates JSON")
        if isinstance(dom_json, dict):
            dom_candidates = dom_json
        elif dom_json is not None:
            dom_candidates = dom_json

    if system_prompt:
        payload["system_prompt"] = system_prompt
    if user_prompt:
        payload["user_prompt"] = user_prompt
    if sent_error:
        payload["last_error"] = sent_error
    if recorded_script_data not in (None, {}, []):
        payload["recorded_script_data"] = recorded_script_data
    if recorded_target_context not in (None, {}, []):
        payload["recorded_target_context"] = recorded_target_context
    if dom_candidates not in (None, {}, []):
        payload["dom_candidates"] = dom_candidates
    elif interaction.get("dom_candidate_count") not in (None, "", 0):
        payload["dom_candidate_count"] = interaction.get("dom_candidate_count")

    return json.dumps(payload, indent=2, ensure_ascii=False)


def _ai_strategy_rows(
    interaction: dict[str, Any],
    parsed_strategies: list[dict[str, Any]],
    status: str,
) -> str:
    if not parsed_strategies:
        return ""

    aliases = list(interaction.get("locator_strategies") or [])
    validated = _safe_text(interaction.get("validated_locator_strategy"))
    last_used = _safe_text(interaction.get("last_locator_strategy"))

    rows: list[str] = []
    for index, strategy in enumerate(parsed_strategies):
        alias = aliases[index] if index < len(aliases) else f"strategy_{index + 1}"
        if validated and alias == validated:
            row_state = "Validated"
            row_tone = "success"
        elif last_used and alias == last_used:
            row_state = "Tried" if status == "failed" else "Selected"
            row_tone = "warn"
        else:
            row_state = "Suggested"
            row_tone = "soft"

        rows.append(
            '<div class="path-ai-strategy-row">'
            f'<span class="path-ai-kind">{escape(str(strategy.get("kind") or "strategy").upper())}</span>'
            '<span class="path-ai-strategy-copy">'
            f'<span class="path-ai-reason">{escape(_safe_text(strategy.get("reason")) or "No reason provided")}</span>'
            f'<span class="path-ai-alias">{escape(alias)}</span>'
            "</span>"
            f'<span class="path-ai-state {row_tone}">{escape(row_state)}</span>'
            "</div>"
        )
    return f'<div class="path-ai-strategy-list">{"".join(rows)}</div>'


def _candidate_cards(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return ""

    items: list[str] = []
    for candidate in candidates:
        label = (
            candidate.get("labelledby_text")
            or candidate.get("text")
            or candidate.get("title")
            or candidate.get("aria_label")
            or candidate.get("id")
            or candidate.get("tag")
            or "candidate"
        )

        chips: list[str] = []
        tag = _safe_text(candidate.get("tag"))
        role = _safe_text(candidate.get("role"))
        data_field = _safe_text(candidate.get("data_oj_field") or candidate.get("oracle_host_data_oj_field"))
        if tag:
            chips.append(f'<span class="cand-chip tone-blue">{escape(tag)}</span>')
        if role:
            chips.append(f'<span class="cand-chip tone-violet">{escape(role)}</span>')
        if data_field:
            chips.append(f'<span class="cand-chip tone-green">{escape(data_field)}</span>')

        fields: list[str] = []
        for key, value, tone in [
            ("ID", candidate.get("id"), "plain"),
            ("Title", candidate.get("title"), "plain"),
            ("Text", candidate.get("text"), "plain"),
            ("ARIA", candidate.get("aria_label"), "plain"),
            ("Label", candidate.get("labelledby_text"), "plain"),
            ("Placeholder", candidate.get("placeholder"), "plain"),
            ("Oracle Host", candidate.get("oracle_host_text"), "soft"),
        ]:
            text = _safe_text(value)
            if not text:
                continue
            fields.append(
                '<div class="cand-field">'
                f'<span class="cand-field-k">{escape(key)}</span>'
                f'<span class="cand-field-v {tone}">{escape(text)}</span>'
                "</div>"
            )

        fields_html = f'<div class="cand-fields">{"".join(fields)}</div>' if fields else ""

        items.append(
            '<div class="cand-item">'
            '<div class="cand-top">'
            f'<div class="cand-head">{escape(str(label))}</div>'
            f'<div class="cand-chips">{"".join(chips)}</div>'
            "</div>"
            f"{fields_html}"
            "</div>"
        )
    return f'<div class="cand-list">{"".join(items)}</div>'


def _failure_context_block(action: dict[str, Any]) -> str:
    context = action.get("failure_context") or {}
    if not context:
        return ""

    rows: list[str] = []
    for key in ("helper", "page_title", "ready_state", "busy_indicator_count"):
        value = context.get(key)
        if value in (None, "", [], {}):
            continue
        rows.append(
            '<div class="kv">'
            f'<span class="kk">{escape(key.replace("_", " "))}</span>'
            f'<span class="kv2">{escape(str(value))}</span>'
            "</div>"
        )

    active_html = ""
    active = context.get("active_element") or {}
    if active:
        active_text = ", ".join(
            f"{key}={value}" for key, value in active.items() if value not in (None, "", [], {})
        )
        active_html = (
            '<div class="detail-card">'
            '<div class="dc-title">Active Element</div>'
            f'<div class="dc-body mono-text">{escape(active_text)}</div>'
            "</div>"
        )

    candidates_html = ""
    candidates = ((context.get("dom_context") or {}).get("candidates") or [])[:8]
    if candidates:
        candidates_html = (
            '<div class="detail-card">'
            '<div class="dc-title">DOM Candidates</div>'
            f"{_candidate_cards(candidates)}"
            "</div>"
        )

    if not rows and not active_html and not candidates_html:
        return ""

    return (
        '<div class="detail-card failure-context-card">'
        '<div class="failure-context-head">'
        '<div class="dc-title">Failure Context</div>'
        f'<div class="kv-row">{"".join(rows)}</div>'
        "</div>"
        f"{active_html}{candidates_html}"
        "</div>"
    )


def _script_block(action: dict[str, Any]) -> str:
    raw = _safe_text((action.get("script_data") or {}).get("raw"))
    if not raw:
        return ""
    return (
        '<div class="detail-card">'
        '<div class="dc-title">Recorded Script</div>'
        f'<pre class="code-block">{escape(raw)}</pre>'
        "</div>"
    )


def _flow_context_request_payload_json(interaction: dict[str, Any]) -> str:
    payload: dict[str, Any] = {}
    for field in ("model", "feature", "status", "reason"):
        value = interaction.get(field)
        if value not in (None, "", [], {}):
            payload[field] = value
    for field in ("system_prompt", "user_prompt"):
        value = _safe_text(interaction.get(field))
        if value:
            payload[field] = value
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _flow_context_block(result: dict[str, Any]) -> str:
    input_status = result.get("flow_input_status") or {}
    output_results = result.get("flow_output_results") or []
    if not input_status and not output_results:
        return ""

    input_rows = ""
    if input_status:
        input_rows = "".join(
            '<div class="ctx-row">'
            f'<span class="ctx-name">{escape(str(item.get("label") or item.get("name") or ""))}</span>'
            f'<span class="ctx-state {"ok" if item.get("status") == "available" else "fail"}">{escape(str(item.get("status") or ""))}</span>'
            f'<span class="ctx-meta">{escape(str(item.get("value") or item.get("error") or ""))}</span>'
            "</div>"
            for item in input_status.values()
        )
        input_rows = (
            '<div class="ctx-section">'
            '<div class="dc-title">Inputs</div>'
            f'<div class="ctx-list">{input_rows}</div>'
            "</div>"
        )

    output_cards: list[str] = []
    for item in output_results:
        attempts = item.get("attempts") or []
        attempt_html = ""
        if attempts:
            attempt_html = (
                '<div class="ctx-attempts">'
                + "".join(
                    '<span class="ctx-attempt">'
                    f'<span class="ctx-attempt-source">{escape(str(attempt.get("source") or ""))}</span>'
                    f'<span class="ctx-attempt-status">{escape(str(attempt.get("status") or ""))}</span>'
                    f'<span class="ctx-attempt-detail">{escape(_safe_text(attempt.get("detail")) or "")}</span>'
                    "</span>"
                    for attempt in attempts
                )
                + "</div>"
            )

        ai_interaction = item.get("ai_interaction") or {}
        ai_blocks = ""
        if ai_interaction:
            response_text = _safe_text(ai_interaction.get("response_text"))
            if not response_text and ai_interaction.get("parsed_response"):
                response_text = json.dumps(ai_interaction.get("parsed_response"), indent=2, ensure_ascii=False)
            ai_blocks = (
                '<div class="ctx-ai-block">'
                + _ai_block(
                    "Request Sent to AI",
                    _flow_context_request_payload_json(ai_interaction),
                    icon=(
                        '<svg viewBox="0 0 24 24" aria-hidden="true">'
                        '<path d="M4 12h11" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'
                        '<path d="M11 5l7 7-7 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
                        "</svg>"
                    ),
                    tone="blue",
                    render_json=True,
                )
                + _ai_block(
                    "Model Output",
                    response_text,
                    icon=(
                        '<svg viewBox="0 0 24 24" aria-hidden="true">'
                        '<path d="M7 7h10v10H7z" fill="none" stroke="currentColor" stroke-width="1.8"/>'
                        '<path d="M9.5 11.5h5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'
                        '<path d="M9.5 14.5h3" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'
                        "</svg>"
                    ),
                    tone="green",
                    render_json=True,
                )
                + "</div>"
            )

        output_cards.append(
            '<div class="ctx-output-card">'
            '<div class="ctx-output-top">'
            f'<span class="ctx-name">{escape(str(item.get("label") or item.get("name") or ""))}</span>'
            f'<span class="ctx-state {"ok" if item.get("status") == "extracted" else "fail"}">{escape(str(item.get("status") or ""))}</span>'
            "</div>"
            f'<div class="ctx-output-value">{escape(str(item.get("value") or item.get("error") or ""))}</div>'
            f'<div class="ctx-output-source">{escape(str(item.get("source") or ""))}</div>'
            f"{attempt_html}"
            f"{ai_blocks}"
            "</div>"
        )

    output_section = ""
    if output_cards:
        output_section = (
            '<div class="ctx-section">'
            '<div class="dc-title">Extracted Outputs</div>'
            f'<div class="ctx-output-grid">{"".join(output_cards)}</div>'
            "</div>"
        )

    return (
        '<div class="recording-params-block">'
        '<div class="trace-head">'
        '<div class="trace-title">Flow Context</div>'
        '<div class="trace-subtitle">Workbook-defined parent inputs and extracted outputs for this recording run.</div>'
        "</div>"
        f"{input_rows}{output_section}"
        "</div>"
    )


def _execution_path_block(action: dict[str, Any]) -> str:
    status = "failed" if action.get("status") == "failed" else "success"
    strategies = list(action.get("fallback_strategies") or [])
    if not strategies:
        strategies = [action.get("strategy") or "direct"]

    nodes: list[str] = []
    last_index = len(strategies) - 1
    for index, strategy in enumerate(strategies):
        tone = _strategy_tone(strategy, index, last_index, status)
        if index:
            nodes.append(
                '<div class="chain-arrow" aria-hidden="true">'
                '<svg viewBox="0 0 48 14">'
                '<path d="M2 7h38" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>'
                '<path d="M34 2l10 5-10 5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
                "</svg>"
                "</div>"
            )
        nodes.append(
            f'<div class="chain-node cn-{tone}">'
            f'<span class="chain-node-icon">{_chain_icon(strategy, tone)}</span>'
            '<div class="chain-node-copy">'
            f'<div class="chain-node-label">{escape(_strategy_label(strategy))}</div>'
            f'<div class="chain-node-meta">{escape(str(strategy))}</div>'
            "</div>"
            "</div>"
        )

    recovery_html = ""
    recovery = action.get("recovery") or {}
    if recovery:
        handler = recovery.get("handler_name") or recovery.get("kind") or "recovery"
        recovery_html = (
            '<div class="path-recovery">'
            '<span class="path-recovery-icon">'
            '<svg viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M5 12a7 7 0 111.5 4.4" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/>'
            '<path d="M5 7v5h5" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
            "</svg>"
            "</span>"
            '<div class="path-recovery-copy">'
            '<div class="path-recovery-title">Recovery confirmed</div>'
            f'<div class="path-recovery-value">{escape(str(handler))}</div>'
            "</div>"
            "</div>"
        )

    ai_blocks: list[str] = []
    interactions = action.get("ai_interactions") or []
    for index, interaction in enumerate(interactions, start=1):
        parsed_strategies = ((interaction.get("parsed_response") or {}).get("strategies") or [])[:5]
        usage = interaction.get("usage") or {}
        response_text = _safe_text(interaction.get("response_text"))
        if not response_text and interaction.get("parsed_response"):
            response_text = json.dumps(interaction.get("parsed_response"), indent=2, ensure_ascii=False)

        tokens = ""
        if any(usage.get(key) is not None for key in ("input_tokens", "output_tokens", "total_tokens")):
            tokens = (
                '<div class="path-ai-tokens">'
                f'<span><strong>in</strong> {escape(str(usage.get("input_tokens") or 0))}</span>'
                f'<span><strong>out</strong> {escape(str(usage.get("output_tokens") or 0))}</span>'
                f'<span><strong>total</strong> {escape(str(usage.get("total_tokens") or 0))}</span>'
                "</div>"
            )

        request_payload = _ai_request_payload_json(interaction)
        request_block = _ai_block(
            "Request Sent to AI",
            request_payload,
            icon=(
                '<svg viewBox="0 0 24 24" aria-hidden="true">'
                '<path d="M4 12h11" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'
                '<path d="M11 5l7 7-7 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
                "</svg>"
            ),
            tone="blue",
            open_by_default=True,
            render_json=True,
        )
        response_block = _ai_block(
            "Model Output",
            response_text,
            icon=(
                '<svg viewBox="0 0 24 24" aria-hidden="true">'
                '<path d="M7 7h10v10H7z" fill="none" stroke="currentColor" stroke-width="1.8"/>'
                '<path d="M9.5 11.5h5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'
                '<path d="M9.5 14.5h3" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'
                "</svg>"
            ),
            tone="green",
            render_json=True,
        )

        attempt_badge = (
            f'<span class="path-ai-model">attempt {index}</span>' if len(interactions) > 1 else ""
        )
        ai_blocks.append(
            '<div class="path-ai-inline">'
            '<div class="path-ai-head">'
            '<span class="path-ai-icon">'
            '<svg viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M12 4l1.6 4.4L18 10l-4.4 1.6L12 16l-1.6-4.4L6 10l4.4-1.6L12 4z" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linejoin="round"/>'
            "</svg>"
            "</span>"
            '<div class="path-ai-head-copy">'
            '<div class="path-ai-title">AI self-repair details</div>'
            f'{attempt_badge}<div class="path-ai-model">{escape(_safe_text(interaction.get("model")) or "unknown")}</div>'
            "</div>"
            "</div>"
            f'<div class="path-ai-message">{escape(_safe_text(interaction.get("repair_error") or interaction.get("repair_outcome") or interaction.get("status") or "unknown"))}</div>'
            f'{_ai_strategy_rows(interaction, parsed_strategies, status)}'
            f"{tokens}"
            f"{request_block}"
            f"{response_block}"
            "</div>"
        )

    return (
        '<div class="detail-card path-card">'
        '<div class="path-head">'
        '<div class="dc-title">Execution Path</div>'
        '<div class="path-meta">'
        f'<div class="path-stat"><span class="kk">Strategy</span><span class="kv2">{escape(str(action.get("strategy") or strategies[-1]))}</span></div>'
        f'<div class="path-stat"><span class="kk">Duration</span><span class="kv2">{escape(_format_duration_ms(action.get("duration_ms")))}</span></div>'
        f'<div class="path-stat"><span class="kk">Attempts</span><span class="kv2">{escape(str(action.get("fallback_attempt_count") or 1))}</span></div>'
        f'<div class="path-stat"><span class="kk">Status</span><span class="kv2 {"r" if status == "failed" else "g"}">{escape(status)}</span></div>'
        "</div>"
        "</div>"
        f'<div class="chain-flow">{"".join(nodes)}</div>'
        f"{recovery_html}"
        f'{"".join(ai_blocks)}'
        "</div>"
    )


def _step_item(action: dict[str, Any], action_index: int, result: dict[str, Any], result_index: int) -> str:
    step_number = int(action.get("step") or 0)
    action_name = _safe_text(action.get("action"))
    label = _safe_text(action.get("label"))
    value = _safe_text(((action.get("script_data") or {}).get("parsed_action") or {}).get("value"))
    status = "failed" if action.get("status") == "failed" else "success"
    has_fallback = (action.get("fallback_strategy_count") or 1) > 1
    has_ai = bool(action.get("ai_interactions"))
    has_recovery = bool(action.get("recovery"))
    step_image = _step_image_data_uri(result, action)
    step_image_title = f"Step {step_number}: {label}"
    dom_id = f"r{result_index}-step{action_index}"

    thumb_html = (
        '<div class="thumb-box {tone}" onclick="openLbFromImage(this, {title});event.stopPropagation()">'
        '<img src="{src}" alt="{alt}" loading="lazy" onerror="this.parentElement.innerHTML=\'<div class=&quot;thumb-ph&quot;>—</div>\'">'
        "</div>"
    ).format(
        tone="fail-thumb" if status == "failed" else "",
        title=escape(json.dumps(step_image_title), quote=True),
        src=escape(step_image or "", quote=True),
        alt=escape(step_image_title, quote=True),
    ) if step_image else '<div class="thumb-box"><div class="thumb-ph">—</div></div>'

    expand_image_html = (
        '<img src="{src}" alt="{alt}" onclick="openLbFromImage(this, {title})" '
        'onerror="this.outerHTML=\'<div class=&quot;ex-ph&quot;>Image not found</div>\'">'
    ).format(
        src=escape(step_image or "", quote=True),
        alt=escape(step_image_title, quote=True),
        title=escape(json.dumps(step_image_title), quote=True),
    ) if step_image else '<div class="ex-ph">No screenshot</div>'

    pills: list[str] = []
    if has_ai:
        pills.append('<span class="spill spill-ai">AI</span>')
    if has_fallback and not has_ai:
        pills.append('<span class="spill spill-fb">Fallback</span>')
    if has_recovery:
        pills.append('<span class="spill spill-rec">Recovery</span>')

    value_html = f'<span class="sr-val">→ {escape(value)}</span>' if value else ""
    error_html = (
        f'<div class="sr-err">{escape(_safe_text(action.get("error")))}</div>'
        if status == "failed" and action.get("error")
        else ""
    )

    stacktrace = _safe_text(result.get("stderr") or result.get("error"))
    stacktrace_html = (
        '<div class="detail-card">'
        '<div class="dc-title">Traceback</div>'
        f'<div class="strace">{escape(stacktrace)}</div>'
        "</div>"
    ) if stacktrace and status == "failed" else ""

    detail_blocks = "".join(
        block
        for block in [
            _execution_path_block(action),
            _script_block(action),
            _failure_context_block(action),
            stacktrace_html,
        ]
        if block
    )

    return (
        f'<div class="step-item" data-failed="{str(status == "failed").lower()}" '
        f'data-fb="{str(has_fallback or has_ai).lower()}">'
        f'<div class="step-row {"is-fail" if status == "failed" else ""}" onclick="tog(\'{dom_id}\')">'
        f'<div class="sr-cell sr-num">{step_number:02d}</div>'
        f'<div class="sr-cell sr-action"><span class="atag at-{_action_tone(action_name)}">{escape(action_name.replace("_", " "))}</span></div>'
        '<div class="sr-cell sr-label">'
        f'<div class="sr-name">{escape(label)}{value_html}</div>'
        f"{error_html}"
        f'<div class="sr-pills">{"".join(pills)}</div>'
        "</div>"
        f'<div class="sr-cell sr-thumb">{thumb_html}</div>'
        f'<div class="sr-cell sr-dur">{escape(_format_duration_ms(action.get("duration_ms")))}</div>'
        f'<div class="sr-cell sr-status">{_status_chip_html(status)}</div>'
        f'<div class="sr-cell sr-chev"><div class="chev{" open" if status == "failed" else ""}" id="chv-{dom_id}"></div></div>'
        "</div>"
        f'<div class="step-expand{" open" if status == "failed" else ""}" id="exp-{dom_id}">'
        '<div class="expand-inner">'
        f'<div class="ex-shot">{expand_image_html}</div>'
        f'<div class="ex-body">{detail_blocks}</div>'
        "</div>"
        "</div>"
        "</div>"
    )


def _result_callout(result: dict[str, Any], result_index: int, summary_only: bool = False) -> str:
    summary = result.get("ai_failure_summary") or {}
    if not summary and not _is_result_failed(result):
        return ""

    failure_image = _failure_image_data_uri(result)
    failure_step = next((item for item in result.get("action_log") or [] if item.get("status") == "failed"), {})
    screenshot_block = (
        '<div class="fc-screenshot-wrap" onclick="openLbFromImage(this, {title})">'
        '<img src="{src}" alt="failure screenshot" onerror="this.parentElement.outerHTML=\'<div class=&quot;fc-screenshot-ph&quot;>Image not found</div>\'">'
        "</div>"
    ).format(
        title=escape(json.dumps("Failure Screenshot"), quote=True),
        src=escape(failure_image or "", quote=True),
    ) if failure_image else '<div class="fc-screenshot-ph">No failure screenshot</div>'

    suite_prefix = (
        f'<span class="chip chip-default">{escape(_result_name(result))}</span>' if summary_only else ""
    )
    headline = _safe_text(summary.get("headline") or result.get("error") or "Run failed")
    body = _safe_text(summary.get("summary") or result.get("error"))
    next_action = _safe_text(
        summary.get("next_action")
        or "Inspect the failed control and rerun after updating the deterministic handler."
    )

    return (
        f'<div class="failure-card {"suite-callout" if summary_only else "recording-callout"}" style="display:block">'
        '<div class="fc-strip"></div>'
        '<div class="fc-inner">'
        f'<div class="fc-left">{screenshot_block}</div>'
        '<div class="fc-right">'
        '<div class="fc-badge-row">'
        f'{suite_prefix}<span class="chip chip-fail">{escape(_safe_text(summary.get("failure_category")) or "Failure")}</span>'
        f'<span class="chip chip-default">Step {escape(str(failure_step.get("step") or "-"))} · {escape(_safe_text(failure_step.get("action")))}</span>'
        "</div>"
        f'<div class="fc-title">{escape(headline)}</div>'
        f'<div class="fc-summary">{escape(body)}</div>'
        f'<div class="fc-hint"><strong>Next action:</strong> {escape(next_action)}</div>'
        "</div>"
        "</div>"
        "</div>"
    )


def _recording_item(result: dict[str, Any], result_index: int) -> str:
    actions = list(result.get("action_log") or [])
    name = _result_name(result)
    status = _result_status(result)
    parameters = [str(item) for item in (result.get("resolved_parameter_keys") or [])]
    passed_actions = sum(1 for action in actions if action.get("status") == "success")
    failed_actions = len(actions) - passed_actions
    ai_repairs = sum(len(action.get("ai_interactions") or []) for action in actions)
    fallback_actions = sum(1 for action in actions if (action.get("fallback_strategy_count") or 1) > 1)
    open_attr = " open" if status == "failed" else ""

    meta_cards = [
        ("Status", escape(status), "r" if status == "failed" else "g", False),
        ("Duration", _duration_markup(result.get("duration_seconds")), "", True),
        ("Logged Actions", escape(str(len(actions))), "", False),
        ("AI Repairs", escape(str(ai_repairs)), "", False),
        ("Fallback Steps", escape(str(fallback_actions)), "", False),
    ]

    params_block = ""
    if parameters:
        params_html = "".join(f'<div class="param">{escape(param)}</div>' for param in parameters)
        params_block = (
            '<div class="recording-params-block">'
            '<div class="trace-head">'
            '<div class="trace-title">Parameters</div>'
            '<div class="trace-subtitle">Resolved parameter keys used for this recording run.</div>'
            "</div>"
            f'<div class="params">{params_html}</div>'
            "</div>"
        )

    flow_context_block = _flow_context_block(result)

    trace_body = (
        "".join(_step_item(action, action_index, result, result_index) for action_index, action in enumerate(actions))
        if actions
        else '<div class="empty-trace">No action log captured for this recording.</div>'
    )

    return (
        f'<details class="recording-item{open_attr}" id="recording-{result_index}" '
        f'data-failed="{str(status == "failed").lower()}" '
        f'data-fb="{str(fallback_actions > 0 or ai_repairs > 0).lower()}">'
        '<summary class="recording-summary">'
        '<div class="recording-summary-main">'
        f'<div class="recording-summary-title">{escape(name)}</div>'
        f'<div class="recording-summary-subtitle">{len(actions)} actions · {passed_actions} passed · {failed_actions} failed</div>'
        "</div>"
        '<div class="recording-summary-side">'
        f'<span class="recording-duration">{escape(_format_duration_seconds(result.get("duration_seconds")))}</span>'
        f'{_status_chip_html(status)}'
        '<span class="recording-chevron"></span>'
        "</div>"
        "</summary>"
        '<div class="recording-panel">'
        '<div class="recording-panel-inner">'
        f"{_result_callout(result, result_index)}"
        '<div class="recording-meta-grid">'
        + "".join(
            f'<div class="meta-tile"><span class="label">{escape(label)}</span><span class="value {cls}{" duration-value" if is_duration else ""}">{value}</span></div>'
            for label, value, cls, is_duration in meta_cards
            if value
        )
        + "</div>"
        + params_block
        + flow_context_block
        + '<div class="trace-section">'
        '<div class="trace-head">'
        '<div class="trace-title">Execution Trace</div>'
        '<div class="trace-subtitle">Expand any action to inspect screenshots, script snippets, fallback chains, and failure context.</div>'
        "</div>"
        f'<div class="steps-outer"><div class="steps-list">{trace_body}</div></div>'
        "</div>"
        "</div>"
        "</div>"
        "</details>"
    )


def generate_html_report_content(
    test_suite_id: str,
    parent_run_id: str,
    results: list[dict[str, Any]],
) -> str:
    normalized_results = list(results or [])
    sensitive_literals: set[str] = set()
    for result in normalized_results:
        sensitive_literals.update(_result_sensitive_literals(result))
    total_runs = len(normalized_results)
    passed_runs = sum(1 for result in normalized_results if _result_status(result) == "passed")
    failed_runs = total_runs - passed_runs
    total_actions = sum(len(result.get("action_log") or []) for result in normalized_results)
    total_ai_repairs = sum(
        len(action.get("ai_interactions") or [])
        for result in normalized_results
        for action in (result.get("action_log") or [])
    )
    total_fallbacks = sum(
        1
        for result in normalized_results
        for action in (result.get("action_log") or [])
        if (action.get("fallback_strategy_count") or 1) > 1
    )
    total_duration = sum(float(result.get("duration_seconds") or 0) for result in normalized_results)
    suite_status = "failed" if failed_runs else "passed"
    first_failed_entry = next(
        ((index, result) for index, result in enumerate(normalized_results) if _is_result_failed(result)),
        None,
    )
    first_failed = first_failed_entry[1] if first_failed_entry else None
    first_failed_index = first_failed_entry[0] if first_failed_entry else 0

    rail_rows = [
        ("Status", suite_status, "r" if suite_status == "failed" else "g"),
        ("Run ID", parent_run_id or "run", ""),
        ("Recordings", str(total_runs), ""),
        ("Logged Actions", str(total_actions), ""),
        ("Passed", str(passed_runs), "g"),
        ("Failed", str(failed_runs), "r" if failed_runs else "g"),
        ("Duration", _format_duration_seconds(total_duration), ""),
        ("AI Repairs", str(total_ai_repairs), ""),
        ("Fallback Steps", str(total_fallbacks), ""),
    ]

    styles = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#f4f6fb;
  --s1:#ffffff;
  --s2:#f8f9fd;
  --s3:#eef1f7;
  --border:#d9dfeb;
  --border2:#c6cfe0;
  --text:#172033;
  --text-mid:#4b5877;
  --text-dim:#7f8aa4;
  --blue:#2f6fff;
  --blue-bg:rgba(47,111,255,.08);
  --green:#0e9f6e;
  --green-bg:rgba(14,159,110,.08);
  --red:#e03c4b;
  --red-bg:rgba(224,60,75,.08);
  --amber:#d48a19;
  --amber-bg:rgba(212,138,25,.1);
  --violet:#8b5cf6;
  --violet-bg:rgba(139,92,246,.08);
  --r:8px;
  --r2:12px;
  --r3:16px;
}
html{scroll-behavior:smooth}
body{
  font-family:'Syne',sans-serif;
  background:var(--bg);
  color:var(--text);
  font-size:15px;
  line-height:1.6;
  min-height:100vh;
  overflow-x:hidden;
}
body::before{
  content:'';
  position:fixed;
  inset:0;
  z-index:0;
  pointer-events:none;
  background:
    radial-gradient(ellipse 60% 40% at 85% 0%, rgba(47,111,255,.06) 0%, transparent 70%),
    radial-gradient(ellipse 40% 30% at 10% 90%, rgba(139,92,246,.05) 0%, transparent 60%);
}
.mono,.mono-text,.hero-sub,.nav-run-name,.rc-v,.stat-hint,.sr-num,.atag,.sr-val,.sr-err,.sr-dur,.kk,.kv2,.dc-title,.code-block,.recording-summary-subtitle,.recording-duration,.run-link-meta,.param,.path-ai-model,.path-ai-state,.path-ai-kind,.path-ai-alias,.path-ai-tokens,.status-chip,.recording-summary-subtitle{
  font-family:'JetBrains Mono',monospace;
}
.nav{
  position:sticky;
  top:0;
  z-index:100;
  height:52px;
  background:rgba(244,246,251,.88);
  backdrop-filter:blur(16px) saturate(180%);
  border-bottom:1px solid var(--border);
  display:flex;
  align-items:center;
  gap:14px;
  padding:0 24px;
}
.nav-logo{display:flex;align-items:center;gap:12px;font-weight:700;font-size:14px}
.brand-mark{height:28px;width:auto;display:block}
.nav-divider{width:1px;height:16px;background:var(--border2)}
.nav-run-name{
  font-size:12px;
  color:var(--text-dim);
  max-width:360px;
  overflow:hidden;
  text-overflow:ellipsis;
  white-space:nowrap;
}
.page{
  position:relative;
  z-index:1;
  display:grid;
  grid-template-columns:300px 1fr;
  min-height:calc(100vh - 52px);
}
.main{
  min-width:0;
  padding:28px 28px 80px;
  border-left:1px solid var(--border);
}
.rail{
  position:sticky;
  top:52px;
  height:calc(100vh - 52px);
  overflow-y:auto;
  padding:24px 20px;
  background:rgba(244,246,251,.55);
}
.hero{margin-bottom:24px}
.hero-title-row{
  display:flex;
  align-items:center;
  gap:12px;
  flex-wrap:wrap;
  margin-bottom:6px;
}
.hero-title{
  font-size:35px;
  font-weight:800;
  letter-spacing:-.03em;
  line-height:1.1;
  color:var(--text);
}
.hero-status-pill{
  display:inline-flex;
  align-items:center;
  padding:5px 12px;
  border-radius:999px;
  border:1px solid rgba(224,60,75,.26);
  background:var(--red-bg);
  color:var(--red);
  font-size:11px;
  font-weight:700;
  letter-spacing:.06em;
  text-transform:uppercase;
}
.hero-status-pill.ok{
  border-color:rgba(14,159,110,.24);
  background:var(--green-bg);
  color:var(--green);
}
.hero-sub{font-size:11px;color:var(--text-dim);line-height:1.7}
.stat-row{
  display:grid;
  grid-template-columns:repeat(4,1fr);
  gap:10px;
  margin-bottom:20px;
}
.stat-card{
  position:relative;
  overflow:hidden;
  background:var(--s1);
  border:1px solid var(--border);
  border-radius:var(--r2);
  padding:16px 18px;
  box-shadow:0 8px 22px rgba(23,32,51,.05);
}
.stat-card::before{
  content:'';
  position:absolute;
  top:0;
  left:0;
  right:0;
  height:3px;
  border-radius:var(--r2) var(--r2) 0 0;
  background:var(--border2);
}
.sc-pass::before{background:var(--green)}
.sc-fail::before{background:var(--red)}
.sc-dur::before{background:var(--blue)}
.stat-label{
  font-size:10px;
  font-weight:700;
  letter-spacing:.08em;
  text-transform:uppercase;
  color:var(--text-dim);
  margin-bottom:8px;
}
.stat-val{
  font-size:36px;
  line-height:1;
  font-weight:800;
  letter-spacing:-.04em;
}
.dur-part{display:inline-flex;align-items:flex-start;gap:1px}
.dur-num{display:inline-block}
.dur-unit{
  display:inline-block;
  font-size:.46em;
  line-height:1;
  font-weight:700;
  text-transform:lowercase;
  transform:translateY(.15em);
}
.dur-gap{display:inline-block;width:.2em}
.duration-value{white-space:nowrap}
.stat-val.g{color:var(--green)}
.stat-val.r{color:var(--red)}
.stat-val.b{color:var(--blue)}
.stat-val.w{color:var(--text)}
.stat-hint{margin-top:6px;color:var(--text-dim);font-size:10px}
.failure-card{
  display:none;
  background:var(--s1);
  border:1px solid rgba(224,60,75,.25);
  border-radius:var(--r3);
  overflow:hidden;
  box-shadow:0 16px 34px rgba(224,60,75,.06);
  margin-bottom:24px;
}
.fc-strip{height:3px;background:linear-gradient(90deg,var(--red),rgba(224,60,75,.2))}
.fc-inner{display:flex;gap:18px;padding:18px 20px}
.fc-left{flex:0 0 210px;width:210px}
.fc-screenshot-wrap{
  width:210px;
  overflow:hidden;
  border-radius:var(--r);
  border:1px solid var(--border2);
  background:var(--s2);
  cursor:pointer;
}
.fc-screenshot-wrap img{display:block;width:100%}
.fc-screenshot-ph{
  width:210px;
  height:130px;
  border-radius:var(--r);
  border:1px dashed var(--border2);
  background:var(--s2);
  display:flex;
  flex-direction:column;
  align-items:center;
  justify-content:center;
  gap:8px;
  color:var(--text-dim);
  font-size:11px;
}
.fc-right{flex:1;min-width:0;display:flex;flex-direction:column;gap:10px}
.fc-badge-row{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.fc-title{font-size:15px;font-weight:700;color:var(--red);letter-spacing:-.01em}
.fc-summary{font-size:13px;color:var(--text-mid);line-height:1.85}
.fc-hint{
  padding:10px 12px;
  border:1px solid var(--border);
  border-radius:var(--r);
  background:var(--s2);
  color:var(--text-mid);
  font-size:13px;
  line-height:1.8;
}
.fc-hint strong{color:var(--text)}
.chip{
  display:inline-flex;
  align-items:center;
  padding:4px 11px;
  border-radius:6px;
  border:1px solid;
  font-size:11px;
  font-weight:700;
  letter-spacing:.06em;
  text-transform:uppercase;
}
.chip-default{color:var(--text-dim);background:var(--s2);border-color:var(--border2)}
.chip-fail{color:var(--red);background:var(--red-bg);border-color:rgba(224,60,75,.26)}
.chip-pass{color:var(--green);background:var(--green-bg);border-color:rgba(14,159,110,.25)}
.section-head{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
  margin-bottom:14px;
}
.section-title{
  font-size:13px;
  font-weight:800;
  letter-spacing:.04em;
  text-transform:uppercase;
  color:var(--text-dim);
}
.filter-tabs{
  display:flex;
  gap:4px;
  padding:5px;
  border-radius:var(--r);
  border:1px solid var(--border);
  background:var(--s2);
}
.ftab{
  border:none;
  background:none;
  padding:7px 16px;
  border-radius:8px;
  font-family:'Syne',sans-serif;
  font-size:12px;
  font-weight:700;
  color:var(--text-dim);
  cursor:pointer;
}
.ftab.on{background:var(--s3);color:var(--text)}
.recording-list{display:flex;flex-direction:column;gap:12px}
.recording-item{
  background:var(--s1);
  border:1px solid var(--border);
  border-radius:var(--r2);
  overflow:hidden;
  box-shadow:0 8px 22px rgba(23,32,51,.05);
}
.recording-item.hidden{display:none!important}
.recording-item[open]{border-color:var(--border2)}
.recording-summary{
  list-style:none;
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:16px;
  padding:16px 18px;
  cursor:pointer;
}
.recording-summary::-webkit-details-marker{display:none}
.recording-summary-main{min-width:0}
.recording-summary-title{
  font-size:20px;
  font-weight:800;
  line-height:1.15;
  letter-spacing:-.01em;
}
.recording-summary-subtitle{
  margin-top:5px;
  font-size:12px;
  color:var(--text-dim);
}
.recording-summary-side{
  display:flex;
  align-items:center;
  gap:10px;
  margin-left:auto;
  flex-shrink:0;
}
.recording-duration{font-size:11px;color:var(--text-dim)}
.recording-chevron{
  width:34px;
  height:34px;
  border-radius:999px;
  border:1px solid var(--border2);
  background:var(--s2);
  display:inline-flex;
  align-items:center;
  justify-content:center;
}
.recording-chevron::before{
  content:'';
  width:10px;
  height:10px;
  border-right:1.5px solid var(--text-dim);
  border-bottom:1.5px solid var(--text-dim);
  transform:rotate(45deg) translateY(-1px);
  transition:transform .2s ease;
}
.recording-item[open] .recording-chevron::before{transform:rotate(225deg) translateX(-1px)}
.status-chip{
  display:inline-flex;
  align-items:center;
  justify-content:center;
  min-width:94px;
  padding:6px 12px;
  border-radius:999px;
  border:1px solid;
  font-size:11px;
  font-weight:700;
  letter-spacing:.06em;
  text-transform:uppercase;
}
.status-passed{color:var(--green);background:var(--green-bg);border-color:rgba(14,159,110,.24)}
.status-failed{color:var(--red);background:var(--red-bg);border-color:rgba(224,60,75,.28)}
.recording-panel{display:grid;grid-template-rows:0fr;transition:grid-template-rows .24s ease}
.recording-panel-inner{min-height:0;overflow:hidden;display:grid;gap:16px;padding:0 18px 0}
.recording-item[open] .recording-panel{grid-template-rows:1fr}
.recording-item[open] .recording-panel-inner{padding:4px 18px 18px}
.recording-meta-grid{
  display:grid;
  grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
  gap:10px;
}
.meta-tile{
  background:linear-gradient(180deg,#ffffff 0%, #f7f9fd 100%);
  border:1px solid var(--border2);
  border-radius:14px;
  padding:18px 18px 20px;
  min-height:132px;
  box-shadow:0 10px 24px rgba(23,32,51,.05);
}
.meta-tile .label{
  display:block;
  color:var(--text-dim);
  font-size:12px;
  text-transform:uppercase;
  letter-spacing:.1em;
}
.meta-tile .value{
  display:block;
  margin-top:14px;
  font-size:34px;
  font-weight:800;
  line-height:1.08;
  letter-spacing:-.04em;
  word-break:break-word;
  color:var(--text-mid);
}
.meta-tile .value.r{color:var(--red)}
.meta-tile .value.g{color:var(--green)}
.trace-section{display:grid;gap:10px}
.trace-head{display:grid;gap:6px}
.trace-title{font-size:22px;font-weight:800;letter-spacing:-.02em}
.trace-subtitle{font-size:14px;color:var(--text-dim);line-height:1.8}
.steps-outer{
  background:var(--s1);
  border:1px solid var(--border);
  border-radius:var(--r2);
  overflow:hidden;
}
.steps-list{display:flex;flex-direction:column}
.empty-trace{padding:18px;color:var(--text-dim);font-size:14px}
.step-item{border-bottom:1px solid var(--border)}
.step-item:last-child{border-bottom:none}
.step-row{
  display:grid;
  grid-template-columns:38px 148px minmax(0,1fr) 72px 80px 110px 26px;
  align-items:center;
  cursor:pointer;
  transition:background .12s;
}
.step-row:hover{background:var(--s2)}
.step-row.is-fail{background:rgba(224,60,75,.05)}
.step-row.is-fail:hover{background:rgba(224,60,75,.08)}
.sr-cell{padding:10px 6px}
.sr-num{text-align:center;font-size:11px;color:var(--text-dim);font-weight:600}
.sr-action{display:flex;align-items:center}
.atag{
  display:inline-block;
  padding:3px 9px;
  border-radius:4px;
  border:1px solid;
  font-size:10px;
  font-weight:600;
  text-transform:lowercase;
  white-space:nowrap;
}
.at-goto{color:var(--blue);border-color:rgba(47,111,255,.24);background:var(--blue-bg)}
.at-default{color:var(--green);border-color:rgba(14,159,110,.24);background:var(--green-bg)}
.at-select{color:var(--amber);border-color:rgba(212,138,25,.28);background:var(--amber-bg)}
.sr-label{padding-left:10px;min-width:0}
.sr-name{
  font-size:15px;
  font-weight:600;
  color:var(--text);
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}
.sr-val{margin-left:6px;font-size:12px;color:var(--text-dim);font-weight:500}
.sr-err{
  margin-top:1px;
  font-size:12px;
  color:var(--red);
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}
.sr-pills{display:flex;gap:4px;margin-top:3px}
.spill{
  padding:2px 8px;
  border-radius:3px;
  border:1px solid;
  font-size:10px;
  font-weight:600;
}
.spill-ai{color:var(--violet);border-color:rgba(139,92,246,.26);background:var(--violet-bg)}
.spill-fb{color:var(--amber);border-color:rgba(212,138,25,.28);background:var(--amber-bg)}
.spill-rec{color:var(--green);border-color:rgba(14,159,110,.24);background:var(--green-bg)}
.thumb-box{
  width:64px;
  height:40px;
  overflow:hidden;
  border-radius:5px;
  border:1px solid var(--border);
  background:var(--s2);
  cursor:pointer;
  display:flex;
  align-items:center;
  justify-content:center;
}
.thumb-box:hover{border-color:var(--blue)}
.thumb-box img{display:block;width:100%;height:100%;object-fit:cover}
.thumb-box.fail-thumb{border-color:rgba(224,60,75,.28)}
.thumb-ph{font-size:9px;color:var(--text-dim)}
.sr-dur{text-align:right;padding-right:6px;font-size:12px;color:var(--text-dim)}
.sr-status{display:flex;justify-content:flex-start}
.sr-chev{display:flex;align-items:center;justify-content:center;padding-right:10px}
.chev{
  width:10px;
  height:10px;
  border-right:1.5px solid var(--text-dim);
  border-bottom:1.5px solid var(--text-dim);
  transform:rotate(45deg);
  transition:transform .18s;
}
.chev.open{transform:rotate(-135deg)}
.step-expand{display:none;border-top:1px solid var(--border)}
.step-expand.open{display:block}
.expand-inner{
  display:grid;
  grid-template-columns:200px 1fr;
  gap:18px;
  padding:16px 16px 16px 44px;
}
.ex-shot img{
  width:100%;
  display:block;
  border-radius:var(--r);
  border:1px solid var(--border2);
  background:var(--s2);
  cursor:pointer;
}
.ex-shot img:hover{border-color:var(--blue)}
.ex-ph{
  width:100%;
  height:120px;
  border-radius:var(--r);
  border:1px dashed var(--border2);
  background:var(--s2);
  display:flex;
  align-items:center;
  justify-content:center;
  color:var(--text-dim);
  font-size:10px;
}
.ex-body{display:flex;flex-direction:column;gap:12px;min-width:0}
.detail-card{
  background:var(--s2);
  border:1px solid var(--border);
  border-radius:var(--r);
  padding:14px 15px;
}
.failure-context-card{display:grid;gap:12px}
.failure-context-head{display:grid;gap:10px}
.dc-title{
  font-size:12px;
  font-weight:700;
  text-transform:uppercase;
  letter-spacing:.08em;
  color:var(--text-dim);
  margin-bottom:10px;
}
.failure-context-head .dc-title{margin-bottom:0}
.dc-body{font-size:14px;color:var(--text-mid);line-height:1.9}
.kv-row{display:flex;flex-wrap:wrap;gap:16px;row-gap:12px}
.kv{display:flex;flex-direction:column;gap:2px}
.kk{
  font-size:10px;
  font-weight:700;
  text-transform:uppercase;
  letter-spacing:.09em;
  color:var(--text-dim);
}
.kv2{font-size:14px;color:var(--text-mid);line-height:1.85}
.kv2.r{color:var(--red)}
.kv2.g{color:var(--green)}
.code-block{
  background:#fff;
  border:1px solid var(--border2);
  border-radius:var(--r);
  padding:10px 12px;
  font-size:14px;
  color:var(--text-mid);
  line-height:1.85;
  white-space:pre-wrap;
  word-break:break-all;
}
.path-card{display:grid;gap:14px}
.path-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap}
.path-meta{display:flex;flex-wrap:wrap;gap:16px;row-gap:10px}
.path-stat{display:flex;flex-direction:column;gap:2px}
.chain-flow{display:flex;flex-wrap:wrap;align-items:center;gap:10px 0}
.chain-node{
  min-width:156px;
  max-width:220px;
  display:flex;
  align-items:center;
  gap:12px;
  padding:10px 12px;
  border-radius:10px;
  border:1px solid var(--border2);
  background:var(--s1);
}
.chain-node-icon{
  width:30px;
  height:30px;
  flex-shrink:0;
  display:flex;
  align-items:center;
  justify-content:center;
  border-radius:8px;
  border:1px solid currentColor;
  background:linear-gradient(180deg,#ffffff 0%, #f5f8ff 100%);
  box-shadow:0 1px 0 rgba(255,255,255,.7) inset;
  line-height:0;
}
.chain-node-icon svg{width:19px;height:19px;display:block}
.chain-node-copy{min-width:0}
.chain-node-label{font-size:13px;font-weight:700;color:var(--text);line-height:1.4}
.chain-node-meta{margin-top:2px;font-size:10px;color:var(--text-dim);word-break:break-word}
.cn-direct{color:var(--blue);background:var(--blue-bg);border-color:rgba(47,111,255,.24)}
.cn-fallback{color:var(--amber);background:var(--amber-bg);border-color:rgba(212,138,25,.28)}
.cn-oracle{color:var(--green);background:var(--green-bg);border-color:rgba(14,159,110,.24)}
.cn-ai{color:var(--violet);background:var(--violet-bg);border-color:rgba(139,92,246,.26)}
.cn-success{color:var(--green);background:var(--green-bg);border-color:rgba(14,159,110,.28)}
.cn-failed{color:var(--red);background:var(--red-bg);border-color:rgba(224,60,75,.28)}
.chain-arrow{width:54px;display:flex;align-items:center;justify-content:center;color:var(--text-dim)}
.chain-arrow svg{width:48px;height:18px;display:block}
.cn-direct .chain-node-icon{background:linear-gradient(180deg,#f7faff 0%, #eef4ff 100%)}
.cn-fallback .chain-node-icon{background:linear-gradient(180deg,#fffaf1 0%, #fff3de 100%)}
.cn-oracle .chain-node-icon,.cn-success .chain-node-icon{background:linear-gradient(180deg,#f3fcf7 0%, #e6f8ef 100%)}
.cn-ai .chain-node-icon{background:linear-gradient(180deg,#faf7ff 0%, #f0eaff 100%)}
.cn-failed .chain-node-icon{background:linear-gradient(180deg,#fff6f8 0%, #ffecee 100%)}
.path-recovery{
  display:flex;
  align-items:center;
  gap:12px;
  padding:12px 14px;
  border-radius:10px;
  border:1px solid rgba(14,159,110,.24);
  background:var(--green-bg);
}
.path-recovery-icon,.path-ai-icon,.path-ai-panel-icon{
  width:24px;
  height:24px;
  display:flex;
  align-items:center;
  justify-content:center;
  border-radius:7px;
  background:#fff;
  flex-shrink:0;
  line-height:0;
}
.path-recovery-icon{
  border:1px solid rgba(14,159,110,.28);
  color:var(--green);
}
.path-recovery-icon svg,.path-ai-icon svg,.path-ai-panel-icon svg{width:15px;height:15px;display:block}
.path-recovery-copy{min-width:0}
.path-recovery-title{
  font-size:11px;
  font-weight:700;
  letter-spacing:.08em;
  text-transform:uppercase;
  color:var(--green);
}
.path-recovery-value{
  margin-top:2px;
  font-size:13px;
  font-weight:600;
  color:var(--text);
  word-break:break-word;
}
.path-ai-inline{
  display:grid;
  gap:12px;
  padding:14px 15px;
  border-radius:10px;
  border:1px solid rgba(139,92,246,.22);
  background:#fbf8ff;
}
.path-ai-head{display:flex;align-items:center;gap:12px}
.path-ai-icon{
  border:1px solid rgba(139,92,246,.26);
  color:var(--violet);
}
.path-ai-head-copy{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.path-ai-title{font-size:13px;font-weight:800;color:var(--violet)}
.path-ai-model{
  padding:2px 8px;
  border-radius:999px;
  border:1px solid rgba(139,92,246,.24);
  background:#fff;
  color:var(--text-dim);
  font-size:10px;
  font-weight:700;
}
.path-ai-message{font-size:14px;color:var(--text);line-height:1.75}
.path-ai-strategy-list{display:grid;gap:8px}
.path-ai-strategy-row{
  display:grid;
  grid-template-columns:auto 1fr auto;
  gap:10px;
  align-items:flex-start;
  padding:10px 11px;
  border-radius:8px;
  border:1px solid rgba(139,92,246,.16);
  background:#fff;
}
.path-ai-kind{
  padding:2px 8px;
  border-radius:999px;
  background:var(--blue-bg);
  border:1px solid rgba(47,111,255,.24);
  color:var(--blue);
  font-size:10px;
  font-weight:700;
}
.path-ai-strategy-copy{min-width:0;display:flex;flex-direction:column;gap:4px}
.path-ai-reason{font-size:12px;color:var(--text-mid);line-height:1.7}
.path-ai-alias{font-size:10px;color:var(--text-dim);line-height:1.5}
.path-ai-state{
  padding:2px 8px;
  border-radius:999px;
  border:1px solid var(--border2);
  font-size:10px;
  font-weight:700;
  white-space:nowrap;
}
.path-ai-state.soft{color:var(--text-dim);background:var(--s1)}
.path-ai-state.success{color:var(--green);border-color:rgba(14,159,110,.28);background:var(--green-bg)}
.path-ai-state.warn{color:var(--amber);border-color:rgba(212,138,25,.28);background:var(--amber-bg)}
.path-ai-tokens{display:flex;flex-wrap:wrap;gap:12px;font-size:11px;color:var(--text-dim)}
.path-ai-tokens strong{color:var(--text-mid)}
.path-ai-panel{
  border:1px solid rgba(139,92,246,.16);
  border-radius:10px;
  background:#fff;
  overflow:hidden;
}
.path-ai-panel + .path-ai-panel{margin-top:2px}
.path-ai-panel.tone-blue{border-color:rgba(47,111,255,.18)}
.path-ai-panel.tone-green{border-color:rgba(14,159,110,.2)}
.path-ai-panel-summary{
  list-style:none;
  display:flex;
  align-items:center;
  gap:10px;
  padding:11px 12px;
  cursor:pointer;
}
.path-ai-panel-summary::-webkit-details-marker{display:none}
.path-ai-panel-copy{min-width:0;display:flex;align-items:center;gap:8px;flex:1}
.path-ai-panel-title{font-size:12px;font-weight:700;color:var(--text)}
.path-ai-panel-chevron{
  width:18px;
  height:18px;
  color:var(--text-dim);
  display:flex;
  align-items:center;
  justify-content:center;
  flex-shrink:0;
  transition:transform .16s ease;
}
.path-ai-panel[open] .path-ai-panel-chevron{transform:rotate(180deg)}
.path-ai-panel.tone-blue .path-ai-panel-icon{color:var(--blue);border:1px solid rgba(47,111,255,.22);background:var(--blue-bg)}
.path-ai-panel.tone-green .path-ai-panel-icon{color:var(--green);border:1px solid rgba(14,159,110,.22);background:var(--green-bg)}
.path-ai-panel-body{
  padding:0 12px 12px;
  max-height:360px;
  overflow:auto;
  overscroll-behavior:contain;
}
.path-ai-pre{
  background:#f9fbff;
  border:1px solid var(--border);
  border-radius:8px;
  padding:12px 13px;
  font-size:12px;
  color:var(--text-mid);
  line-height:1.8;
  white-space:pre-wrap;
  word-break:break-word;
  overflow:auto;
}
.json-pre{background:linear-gradient(180deg,#f8fbff 0%, #f2f7ff 100%);color:#334155}
.json-pre .json-key{color:#2563eb;font-weight:700}
.json-pre .json-string{color:#0f9f6e}
.json-pre .json-number{color:#d97706;font-weight:600}
.json-pre .json-bool{color:#7c3aed;font-weight:600}
.json-pre .json-null{color:#ef4444;font-weight:600}
.strace{
  background:#fff;
  border:1px solid var(--border);
  border-radius:var(--r);
  padding:12px 14px;
  font-size:12px;
  color:#b42332;
  line-height:1.95;
  overflow-x:auto;
  white-space:pre-wrap;
  word-break:break-all;
}
.cand-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:8px}
.cand-item{
  background:var(--s1);
  border:1px solid var(--border);
  border-radius:var(--r);
  padding:10px 11px;
  display:flex;
  flex-direction:column;
  gap:10px;
}
.cand-top{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}
.cand-chips{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:4px}
.cand-chip{
  display:inline-flex;
  align-items:center;
  padding:2px 8px;
  border-radius:999px;
  border:1px solid var(--border2);
  font-size:10px;
  font-weight:700;
  line-height:1.2;
  white-space:nowrap;
}
.cand-chip.tone-blue{color:var(--blue);border-color:rgba(47,111,255,.24);background:var(--blue-bg)}
.cand-chip.tone-violet{color:var(--violet);border-color:rgba(139,92,246,.24);background:var(--violet-bg)}
.cand-chip.tone-green{color:var(--green);border-color:rgba(14,159,110,.24);background:var(--green-bg)}
.cand-fields{display:grid;gap:6px}
.cand-field{
  display:grid;
  grid-template-columns:84px minmax(0,1fr);
  gap:8px;
  align-items:flex-start;
  padding:6px 8px;
  border-radius:8px;
  background:#fff;
  border:1px solid rgba(120,140,180,.14);
}
.cand-field-k{
  font-size:10px;
  font-weight:800;
  letter-spacing:.06em;
  text-transform:uppercase;
  color:var(--text-dim);
}
.cand-field-v{
  min-width:0;
  font-size:12px;
  line-height:1.65;
  color:var(--text-mid);
  word-break:break-word;
}
.cand-field-v.soft{color:var(--text-dim)}
.cand-head{
  font-size:14px;
  font-weight:700;
  color:var(--text);
  line-height:1.45;
  flex:1;
  min-width:0;
}
.rail-section{margin-bottom:24px}
.rail-title{
  margin-bottom:10px;
  font-size:11px;
  font-weight:800;
  text-transform:uppercase;
  letter-spacing:.1em;
  color:var(--text-dim);
}
.rail-card{
  background:var(--s1);
  border:1px solid var(--border);
  border-radius:var(--r2);
  overflow:hidden;
}
.rc-row{
  display:flex;
  justify-content:space-between;
  align-items:flex-start;
  gap:8px;
  padding:10px 14px;
  border-bottom:1px solid var(--border);
}
.rc-row:last-child{border-bottom:none}
.rc-k{font-size:11px;color:var(--text-dim)}
.rc-v{
  max-width:170px;
  text-align:right;
  font-size:11px;
  color:var(--text-mid);
  word-break:break-all;
}
.rc-v.r{color:var(--red);font-weight:600}
.rc-v.g{color:var(--green);font-weight:600}
.recording-params-block{
  display:grid;
  gap:8px;
  padding:16px 18px;
  background:linear-gradient(180deg,#ffffff 0%, #f8faff 100%);
  border:1px solid var(--border);
  border-radius:14px;
  box-shadow:0 10px 24px rgba(23,32,51,.04);
}
.ctx-section{display:grid;gap:10px}
.ctx-list,.ctx-output-grid{display:grid;gap:8px}
.ctx-row,.ctx-output-card{
  display:grid;
  gap:8px;
  padding:10px 12px;
  border-radius:10px;
  border:1px solid var(--border);
  background:#fff;
}
.ctx-row{grid-template-columns:minmax(0,1fr) auto;align-items:start}
.ctx-name{font-size:13px;font-weight:700;color:var(--text)}
.ctx-state{
  display:inline-flex;
  align-items:center;
  justify-content:center;
  padding:3px 10px;
  border-radius:999px;
  font-size:10px;
  font-weight:800;
  letter-spacing:.06em;
  text-transform:uppercase;
  border:1px solid var(--border2);
}
.ctx-state.ok{color:var(--green);border-color:rgba(14,159,110,.24);background:var(--green-bg)}
.ctx-state.fail{color:var(--red);border-color:rgba(224,60,75,.24);background:var(--red-bg)}
.ctx-meta,.ctx-output-value,.ctx-output-source,.ctx-attempt-detail{
  font-family:'JetBrains Mono',monospace;
  font-size:12px;
  color:var(--text-mid);
  line-height:1.7;
  word-break:break-word;
}
.ctx-output-top{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}
.ctx-output-source{color:var(--text-dim)}
.ctx-attempts{
  display:grid;
  gap:6px;
  padding-top:4px;
}
.ctx-attempt{
  display:grid;
  gap:4px;
  padding:8px 10px;
  border-radius:8px;
  background:var(--s2);
  border:1px solid var(--border);
}
.ctx-attempt-source{
  font-family:'JetBrains Mono',monospace;
  font-size:11px;
  font-weight:800;
  color:var(--text);
  text-transform:uppercase;
}
.ctx-attempt-status{
  font-family:'JetBrains Mono',monospace;
  font-size:10px;
  font-weight:700;
  color:var(--text-dim);
  text-transform:uppercase;
}
.ctx-ai-block{display:grid;gap:8px;padding-top:4px}
.params{display:flex;flex-wrap:wrap;gap:4px}
.param{
  padding:4px 10px;
  border-radius:4px;
  border:1px solid var(--border);
  background:var(--s2);
  color:var(--text-dim);
  font-size:11px;
}
.run-links{display:flex;flex-direction:column;gap:8px}
.run-link{
  display:flex;
  flex-direction:column;
  gap:2px;
  min-width:0;
  padding:10px 12px;
  border-radius:var(--r);
  border:1px solid var(--border);
  background:var(--s1);
  color:inherit;
  text-decoration:none;
}
.run-link:hover{border-color:var(--border2);background:var(--s2)}
.run-link.fail-link{border-color:rgba(224,60,75,.22)}
.run-link-name{
  font-size:14px;
  font-weight:700;
  color:var(--text);
  line-height:1.45;
  overflow-wrap:anywhere;
  word-break:break-word;
}
.run-link-meta{font-size:11px;color:var(--text-dim)}
#lb{
  display:none;
  position:fixed;
  inset:0;
  z-index:300;
  background:rgba(18,24,38,.6);
  backdrop-filter:blur(6px);
  align-items:center;
  justify-content:center;
  padding:32px;
}
#lb.open{display:flex}
.lb-card{
  width:100%;
  max-width:1100px;
  max-height:90vh;
  display:flex;
  flex-direction:column;
  overflow:hidden;
  border-radius:var(--r3);
  border:1px solid var(--border2);
  background:var(--s1);
  box-shadow:0 24px 60px rgba(23,32,51,.22);
}
.lb-top{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid var(--border)}
.lb-ttl{font-size:13px;font-weight:700}
.lb-close{
  width:28px;
  height:28px;
  border-radius:var(--r);
  border:1px solid var(--border2);
  background:var(--s2);
  cursor:pointer;
  display:flex;
  align-items:center;
  justify-content:center;
  font-size:14px;
  color:var(--text-mid);
}
.lb-img{
  flex:1;
  overflow:auto;
  display:flex;
  align-items:flex-start;
  justify-content:center;
  padding:20px;
  background:var(--s2);
}
.lb-img img{max-width:100%;height:auto;border-radius:var(--r)}
@media(max-width:1024px){
  .page{grid-template-columns:1fr}
  .main{border-left:none}
  .rail{position:static;height:auto;border-bottom:1px solid var(--border)}
}
@media(max-width:700px){
  .stat-row{grid-template-columns:repeat(2,1fr)}
  .step-row{grid-template-columns:34px 1fr 58px 80px 22px}
  .step-row>.sr-action,.step-row>.sr-thumb{display:none}
  .expand-inner{grid-template-columns:1fr}
  .fc-inner{flex-direction:column}
  .fc-left{width:100%;flex:0 0 auto}
  .fc-screenshot-wrap,.fc-screenshot-ph{width:100%}
}
"""

    title_text = test_suite_id.replace("_", " ").strip() or "Test Suite"
    callout_html = _result_callout(first_failed, first_failed_index, summary_only=True) if first_failed else ""
    recordings_html = "".join(
        _recording_item(result, index) for index, result in enumerate(normalized_results)
    ) or '<div class="empty-trace">No recording results were provided.</div>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title_text)} — Agentic Test Report</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{styles}</style>
</head>
<body>
<nav class="nav">
  <div class="nav-logo">
    <img src="{escape(_AETHERION_HEADER_ICON, quote=True)}" alt="Aetherion" class="brand-mark" />
  </div>
  <div class="nav-divider"></div>
  <div class="nav-run-name">{escape(test_suite_id)}</div>
</nav>

<div class="page">
  <aside class="rail">
    <div class="rail-section">
      <div class="rail-title">Run Details</div>
      <div class="rail-card">
        {"".join(
            f'<div class="rc-row"><span class="rc-k">{escape(label)}</span><span class="rc-v {cls}">{escape(value)}</span></div>'
            for label, value, cls in rail_rows
        )}
      </div>
    </div>
    <div class="rail-section">
      <div class="rail-title">Runs</div>
      <div class="run-links">
        {"".join(
            f'<a class="run-link {"fail-link" if _is_result_failed(result) else ""}" href="#recording-{index}">'
            f'<span class="run-link-name">{escape(_result_name(result))}</span>'
            f'<span class="run-link-meta">{escape(_result_status(result))} · {escape(_format_duration_seconds(result.get("duration_seconds")))}</span>'
            f'</a>'
            for index, result in enumerate(normalized_results)
        )}
      </div>
    </div>
  </aside>

  <main class="main">
    <div class="hero">
      <div class="hero-title-row">
        <div class="hero-title">{escape(title_text)}</div>
        <span class="hero-status-pill {"ok" if suite_status == "passed" else ""}">{escape(suite_status)}</span>
      </div>
      <div class="hero-sub">Run ID: {escape(parent_run_id or "run")} · {total_runs} recording{"s" if total_runs != 1 else ""} · {total_actions} logged actions</div>
    </div>

    <div class="stat-row">
      <div class="stat-card sc-total">
        <div class="stat-label">Recordings</div>
        <div class="stat-val w">{total_runs}</div>
        <div class="stat-hint">{total_actions} logged actions across the suite</div>
      </div>
      <div class="stat-card sc-pass">
        <div class="stat-label">Passed</div>
        <div class="stat-val g">{passed_runs}</div>
        <div class="stat-hint">{total_fallbacks} fallback steps across all runs</div>
      </div>
      <div class="stat-card sc-fail">
        <div class="stat-label">Failed</div>
        <div class="stat-val {"r" if failed_runs else "g"}">{failed_runs}</div>
        <div class="stat-hint">{escape(_result_name(first_failed)) if first_failed else "all clear"}</div>
      </div>
      <div class="stat-card sc-dur">
        <div class="stat-label">Duration</div>
        <div class="stat-val b duration-value">{_duration_markup(total_duration)}</div>
        <div class="stat-hint">{total_ai_repairs} AI repair attempts</div>
      </div>
    </div>

    {callout_html}

    <div class="section-head">
      <div class="section-title">Suite Runs</div>
      <div class="filter-tabs">
        <button class="ftab on" id="ft-all" onclick="setFilter('all')">All</button>
        <button class="ftab" id="ft-failed" onclick="setFilter('failed')">Failed</button>
        <button class="ftab" id="ft-fallback" onclick="setFilter('fallback')">Fallbacks</button>
      </div>
    </div>

    <div class="recording-list" id="recordingList">
      {recordings_html}
    </div>
  </main>
</div>

<div id="lb" onclick="closeLb(event)">
  <div class="lb-card" onclick="event.stopPropagation()">
    <div class="lb-top">
      <div><div class="lb-ttl" id="lbT"></div></div>
      <div class="lb-close" onclick="closeLb()">✕</div>
    </div>
    <div class="lb-img"><img id="lbImg" src="" alt="screenshot"></div>
  </div>
</div>

<script>
function tog(domId) {{
  const panel = document.getElementById('exp-' + domId);
  const chev = document.getElementById('chv-' + domId);
  if (!panel) return;
  const open = panel.classList.toggle('open');
  if (chev) chev.classList.toggle('open', open);
}}

function setFilter(filterName) {{
  document.querySelectorAll('.ftab').forEach(button => button.classList.remove('on'));
  document.getElementById('ft-' + filterName)?.classList.add('on');
  document.querySelectorAll('.recording-item').forEach(item => {{
    const isFailed = item.dataset.failed === 'true';
    const hasFallback = item.dataset.fb === 'true';
    let visible = true;
    if (filterName === 'failed' && !isFailed) visible = false;
    if (filterName === 'fallback' && !hasFallback) visible = false;
    item.classList.toggle('hidden', !visible);
  }});
}}

function openLbFromImage(node, title) {{
  if (!node) return;
  const img = node.tagName === 'IMG' ? node : node.querySelector('img');
  if (!img || !img.src) return;
  document.getElementById('lbImg').src = img.src;
  document.getElementById('lbT').textContent = title || 'Screenshot';
  document.getElementById('lb').classList.add('open');
}}

function closeLb(event) {{
  const root = document.getElementById('lb');
  if (!event || event.target === root) {{
    root.classList.remove('open');
  }}
}}

document.addEventListener('keydown', event => {{
  if (event.key === 'Escape') closeLb();
}});
</script>
</body>
</html>
"""
    return _redact_sensitive_literals(html, sensitive_literals)
