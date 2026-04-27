from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def append_episode(path: str | Path, episode: dict[str, Any]) -> None:
    target = Path(path)
    if not episode or not isinstance(episode, dict):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(episode, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def load_episodes(path: str | Path, *, max_entries: int = 4000) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []

    episodes: list[dict[str, Any]] = []
    try:
        with target.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except Exception:
                    continue
                if isinstance(parsed, dict):
                    episodes.append(parsed)
    except Exception:
        return []

    if max_entries > 0 and len(episodes) > max_entries:
        return episodes[-max_entries:]
    return episodes


def _is_successful_episode(episode: dict[str, Any]) -> bool:
    return (
        str(((episode.get("outcome") or {}).get("status") or "")).strip().lower() == "success"
        and bool(((episode.get("postcondition") or {}).get("passed")))
    )


def _is_trusted_episode(episode: dict[str, Any]) -> bool:
    recovery = episode.get("recovery") or {}
    source = str(recovery.get("source") or "").strip().lower()
    return source in {"oracle_handler", "ai_validated", "experience_reuse"}


def _score_episode(
    episode: dict[str, Any],
    *,
    action_type: str,
    target_label_normalized: str,
    control_family: str,
    page_signature: dict[str, Any],
    failure_signature: dict[str, Any],
) -> int:
    score = 0

    if normalize_text(episode.get("target_label_normalized")) == target_label_normalized:
        score += 35

    if normalize_text(episode.get("action_type")) == normalize_text(action_type):
        score += 20

    if normalize_text(episode.get("control_family")) == normalize_text(control_family):
        score += 15

    candidate_page = episode.get("page_signature") or {}
    query_path = normalize_text(page_signature.get("path_hint"))
    candidate_path = normalize_text(candidate_page.get("path_hint"))
    if query_path and candidate_path and query_path == candidate_path:
        score += 8

    query_title = normalize_text(page_signature.get("title"))
    candidate_title = normalize_text(candidate_page.get("title"))
    if query_title and candidate_title and query_title == candidate_title:
        score += 4

    query_surface = normalize_text(page_signature.get("surface_type"))
    candidate_surface = normalize_text(candidate_page.get("surface_type"))
    if query_surface and candidate_surface and query_surface == candidate_surface:
        score += 3

    query_step = normalize_text(page_signature.get("guided_step"))
    candidate_step = normalize_text(candidate_page.get("guided_step"))
    if query_step and candidate_step and query_step == candidate_step:
        score += 5

    candidate_failure = episode.get("failure_signature") or {}
    query_error_type = normalize_text(failure_signature.get("error_type"))
    candidate_error_type = normalize_text(candidate_failure.get("error_type"))
    if query_error_type and candidate_error_type and query_error_type == candidate_error_type:
        score += 10

    return score


def retrieve_recovery_candidates(
    path: str | Path,
    *,
    action_type: str,
    target_label: str,
    control_family: str,
    page_signature: dict[str, Any],
    failure_signature: dict[str, Any],
    limit: int = 3,
    min_score: int = 80,
) -> list[dict[str, Any]]:
    normalized_target = normalize_text(target_label)
    episodes = load_episodes(path)
    scored: list[dict[str, Any]] = []

    for episode in episodes:
        if not _is_successful_episode(episode):
            continue
        if not _is_trusted_episode(episode):
            continue
        if normalize_text(episode.get("action_type")) != normalize_text(action_type):
            continue
        if normalize_text(episode.get("target_label_normalized")) != normalized_target:
            continue
        score = _score_episode(
            episode,
            action_type=action_type,
            target_label_normalized=normalized_target,
            control_family=control_family,
            page_signature=page_signature,
            failure_signature=failure_signature,
        )
        if score < min_score:
            continue
        enriched = dict(episode)
        enriched["retrieval_score"] = score
        scored.append(enriched)

    scored.sort(
        key=lambda item: (
            int(item.get("retrieval_score") or 0),
            str(item.get("created_at") or ""),
        ),
        reverse=True,
    )
    return scored[: max(0, limit)]
