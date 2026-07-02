"""Tests for the advisory "fix your recording" suggestion subsystem.

Tier 1 is deterministic and offline (must never touch the network); Tier 2 is gated by
ACT_AI_SUGGESTIONS_ENABLED + OPENAI_API_KEY and must fail soft. These tests pin both, plus the
report integration (the card renders, in both views, off action["recording_suggestion"]).
"""

import src.utils.recording_suggestions as rs
from src.utils.html_report_generator import generate_html_report_content


def _quantity_like_action() -> dict:
    """A step that failed the recorded locator (30s visibility timeout) but recovered via
    experience reuse -- the AR_Credit_Memo Quantity case (71s, status success)."""
    return {
        "action": "click_title",
        "label": "Quantity",
        "status": "success",
        "duration_ms": 71_000,
        "strategy": "experience_css_1",
        "fallback_strategy_count": 4,
        "fallback_strategies": ["direct", "oracle_quick_actions_expand", "experience_css_1"],
        "script_data": {"raw": 'page.get_by_title("Quantity").first.click()'},
        "recovery": {
            "source": "experience_reuse",
            "kind": "css",
            "handler_name": "experience_reuse",
            "details": {
                "locator_strategy": {
                    "steps": [
                        {"method": "get_by_role", "args": ["row"], "kwargs": {"name": "1 Line"}},
                        {"method": "get_by_label", "args": ["Quantity"]},
                    ]
                }
            },
        },
        "debug": {
            "click_with_candidates": {
                "direct_attempt": {
                    "status": "failed",
                    "error": (
                        "Locator.wait_for: Timeout 30000ms exceeded. waiting for "
                        'get_by_title("Quantity").first to be visible'
                    ),
                },
                "resolved_by": "experience_reuse",
            }
        },
    }


def test_tier1_flags_fragile_recovered_step_and_echoes_recovered_locator() -> None:
    suggestion = rs.build_deterministic_suggestion(_quantity_like_action())
    assert suggestion is not None
    assert suggestion["severity"] == "warning"
    assert suggestion["ai"] is None
    # The runner already found the working locator -- echo it as the fix, reconstructed readably.
    assert suggestion["recovered_locator"] == (
        "page.get_by_role('row', name='1 Line').get_by_label('Quantity')"
    )
    assert "experience_reuse" in suggestion["diagnosis"]
    assert "71s" in suggestion["diagnosis"]
    assert suggestion["recovered_locator"] in suggestion["recommended"]


def test_tier1_flags_hard_failure_with_visible_candidates() -> None:
    action = {
        "action": "click_title",
        "label": "Business Unit",
        "status": "failed",
        "duration_ms": 60_000,
        "strategy": "direct",
        "fallback_strategy_count": 3,
        "script_data": {"raw": 'page.get_by_role("textbox", name="Business Unit").click()'},
        "error": "Unable to open combobox",
        "debug": {
            "click_combobox": {"direct_attempt": {"status": "failed", "error": "to be visible"}}
        },
        "failure_context": {
            "dom_context": {
                "candidates": [
                    {"head": "Invoice Credit memo", "id": "ap1:transactionClass::content"},
                ]
            }
        },
    }
    suggestion = rs.build_deterministic_suggestion(action)
    assert suggestion is not None
    assert suggestion["severity"] == "error"
    assert "Re-record" in suggestion["recommended"]
    assert "ap1:transactionClass::content" in suggestion["recommended"]


def test_tier1_disabled_target_failure_gives_dependency_guidance() -> None:
    """The disabled-select fast-fail (dependent LOV) gets a tailored card, not a generic
    'corrected locator' one -- a locator fix can't help a disabled field."""
    action = {
        "action": "select_option",
        "label": "Requisitioning BU",
        "status": "failed",
        "duration_ms": 8_000,
        "strategy": "direct",
        "fallback_strategy_count": 1,
        "script_data": {
            "raw": 'page.get_by_label("Requisitioning BU").select_option("105 Tacoma BU")'
        },
        "error": (
            'Select target "Requisitioning BU" is disabled. The dependent field did not become '
            "enabled within the wait window."
        ),
        "debug": {"select_option_target": {"status": "disabled_fast_fail"}},
    }
    suggestion = rs.build_deterministic_suggestion(action)
    assert suggestion is not None
    assert suggestion["severity"] == "error"
    assert "disabled" in suggestion["title"].lower()
    assert "controlling field" in suggestion["recommended"] or "auto-derived" in suggestion[
        "recommended"
    ]


def test_tier1_flags_slow_strict_success_as_info() -> None:
    action = {
        "action": "fill_textbox",
        "label": "Description",
        "status": "success",
        "duration_ms": 30_000,
        "strategy": "direct",
        "fallback_strategy_count": 1,
        "script_data": {"raw": 'page.get_by_label("Description").fill("Test")'},
    }
    suggestion = rs.build_deterministic_suggestion(action)
    assert suggestion is not None
    assert suggestion["severity"] == "info"


def test_tier1_returns_none_for_healthy_fast_strict_step() -> None:
    action = {
        "action": "fill_textbox",
        "label": "Username",
        "status": "success",
        "duration_ms": 800,
        "strategy": "direct",
        "fallback_strategy_count": 1,
        "script_data": {"raw": 'page.get_by_role("textbox", name="Username").fill("SVC")'},
    }
    assert rs.build_deterministic_suggestion(action) is None


def test_locator_strategy_to_text_handles_string_dict_and_steps() -> None:
    assert rs._locator_strategy_to_text("css=.foo") == "css=.foo"
    assert rs._locator_strategy_to_text({"selector": "#bu"}) == "#bu"
    assert rs._locator_strategy_to_text(
        {"steps": [{"method": "get_by_label", "args": ["Quantity"]}]}
    ) == "page.get_by_label('Quantity')"


def test_attach_skips_ai_when_disabled_and_never_calls_network(monkeypatch) -> None:
    """With ACT_AI_SUGGESTIONS_ENABLED off, the pass attaches Tier 1 only and must not invoke the
    LLM enrichment at all (no network from report generation by default)."""
    monkeypatch.delenv("ACT_AI_SUGGESTIONS_ENABLED", raising=False)

    def _boom(*_a, **_k):
        raise AssertionError("enrich_with_ai must not be called when suggestions AI is disabled")

    monkeypatch.setattr(rs, "enrich_with_ai", _boom)
    result = {"action_log": [_quantity_like_action()]}
    rs.attach_recording_suggestions([result])
    suggestion = result["action_log"][0]["recording_suggestion"]
    assert suggestion["severity"] == "warning"
    assert suggestion["ai"] is None


def test_attach_uses_ai_within_budget_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("ACT_AI_SUGGESTIONS_ENABLED", "true")
    monkeypatch.setenv("ACT_AI_SUGGESTIONS_MAX", "1")
    calls: list[int] = []

    def _fake_ai(_action, _suggestion):
        calls.append(1)
        return {"root_cause": "hidden first match", "suggested_edit": "use row-scoped locator",
                "confidence": 0.8, "model": "test-model"}

    monkeypatch.setattr(rs, "enrich_with_ai", _fake_ai)
    # Two warning-severity steps, budget of 1 -> only the first gets an AI block.
    results = [{"action_log": [_quantity_like_action(), _quantity_like_action()]}]
    rs.attach_recording_suggestions(results)
    logged = results[0]["action_log"]
    assert sum(1 for a in logged if (a["recording_suggestion"].get("ai"))) == 1
    assert len(calls) == 1


def test_enrich_with_ai_returns_none_without_api_key(monkeypatch) -> None:
    from src.runtime import helpers_v2 as facade

    # Mock the key getter (the real act_agent/.env carries a key) so no network call happens.
    monkeypatch.setattr(facade, "get_runner_env_value", lambda name: "")
    out = rs.enrich_with_ai(_quantity_like_action(), {"recorded": "x", "diagnosis": "y"})
    assert out is None


def test_enrich_with_ai_fails_soft_on_transport_error(monkeypatch) -> None:
    from src.runtime import helpers_v2 as facade

    monkeypatch.setattr(facade, "get_runner_env_value", lambda name: "sk-test-not-real")

    def _boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr(facade, "urlopen", _boom)
    out = rs.enrich_with_ai(_quantity_like_action(), {"recorded": "x", "diagnosis": "y"})
    assert out is None


def test_report_renders_recording_suggestion_card_in_both_views() -> None:
    action = _quantity_like_action()
    action["step"] = 1
    result = {
        "recording_id": "rec-1",
        "recording_name": "AR_Credit_Memo",
        "status": "passed",
        "duration_seconds": 212,
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "error": "",
        "step_artifacts": [],
        "resolved_parameter_keys": [],
        "action_log": [action],
    }
    html = generate_html_report_content("AR_Credit_Memo", "run-xyz", [result])
    assert 'detail-card reco-card reco-warn"' in html
    assert "Suggested fix:" in html
    # The recovered locator is echoed (single quotes are HTML-escaped, so match quote-free).
    assert "get_by_role(" in html
    assert "get_by_label(" in html
    # The advisory card is NOT dev-only -- it must show in the End User view too.
    assert "reco-card dev-only" not in html


def test_report_has_no_suggestion_card_for_clean_run() -> None:
    action = {
        "step": 1,
        "action": "fill_textbox",
        "label": "Username",
        "status": "success",
        "duration_ms": 800,
        "strategy": "direct",
        "fallback_strategy_count": 1,
        "fallback_strategies": ["direct"],
        "ai_interactions": [],
        "script_data": {"raw": 'page.get_by_role("textbox", name="Username").fill("SVC")'},
    }
    html = generate_html_report_content(
        "Clean", "run-1",
        [{"recording_name": "Clean", "status": "passed", "duration_seconds": 5, "exit_code": 0,
          "stdout": "", "stderr": "", "error": "", "step_artifacts": [],
          "resolved_parameter_keys": [], "action_log": [action]}],
    )
    # The CSS class `.reco-card` is always in the stylesheet; assert no rendered card element.
    assert "detail-card reco-card" not in html
