from src.runtime.experience import append_episode, load_episodes, retrieve_recovery_candidates


def _episode(
    *,
    target_label: str = "Promote and Change Position",
    action_type: str = "click_link",
    control_family: str = "text_target",
    source: str = "ai_validated",
    status: str = "success",
    passed: bool = True,
    path_hint: str = "/fscmUI/faces/FuseWelcome",
    title: str = "Oracle Fusion Cloud Applications",
    guided_step: str = "",
    error_type: str = "RuntimeError",
) -> dict:
    return {
        "episode_id": "ep-1",
        "created_at": "2026-04-24T10:15:00Z",
        "action_type": action_type,
        "target_label": target_label,
        "target_label_normalized": target_label.lower(),
        "control_family": control_family,
        "page_signature": {
            "path_hint": path_hint,
            "title": title,
            "guided_step": guided_step,
            "surface_type": "redwood_home",
        },
        "failure_signature": {
            "error_type": error_type,
        },
        "recovery": {
            "source": source,
            "kind": "ai_locator_repair",
        },
        "postcondition": {
            "kind": "expected_target_visible",
            "passed": passed,
        },
        "outcome": {
            "status": status,
        },
    }


def test_append_episode_and_load_episodes_round_trip(tmp_path) -> None:
    path = tmp_path / "experience.jsonl"

    append_episode(path, _episode())

    loaded = load_episodes(path)

    assert len(loaded) == 1
    assert loaded[0]["target_label"] == "Promote and Change Position"


def test_retrieve_recovery_candidates_prefers_exact_match_and_filters_untrusted(tmp_path) -> None:
    path = tmp_path / "experience.jsonl"
    append_episode(path, _episode(source="oracle_handler"))
    append_episode(path, _episode(source="random_llm"))

    matches = retrieve_recovery_candidates(
        path,
        action_type="click_link",
        target_label="Promote and Change Position",
        control_family="text_target",
        page_signature={
            "path_hint": "/fscmUI/faces/FuseWelcome",
            "title": "Oracle Fusion Cloud Applications",
            "guided_step": "",
            "surface_type": "redwood_home",
        },
        failure_signature={"error_type": "RuntimeError"},
    )

    assert len(matches) == 1
    assert matches[0]["recovery"]["source"] == "oracle_handler"
    assert int(matches[0]["retrieval_score"]) >= 70


def test_retrieve_recovery_candidates_ignores_failed_or_unvalidated_episodes(tmp_path) -> None:
    path = tmp_path / "experience.jsonl"
    append_episode(path, _episode(status="failed"))
    append_episode(path, _episode(passed=False))

    matches = retrieve_recovery_candidates(
        path,
        action_type="click_link",
        target_label="Promote and Change Position",
        control_family="text_target",
        page_signature={
            "path_hint": "/fscmUI/faces/FuseWelcome",
            "title": "Oracle Fusion Cloud Applications",
            "guided_step": "",
            "surface_type": "redwood_home",
        },
        failure_signature={"error_type": "RuntimeError"},
    )

    assert matches == []
