# Runner Experience System

This document defines how `playwright_test_runner` should learn from past
failures and recoveries without turning into a generic fallback engine.

## Context

- `playwright-codegen` plus Phantom own recording generation.
- Phantom owns the Playwright desktop session, noVNC/browser lifecycle, and the
  raw generated recording script.
- `playwright_test_runner` owns execution.
- Once a recording reaches the runner, it must be treated as read-only
  execution input.

The purpose of this system is not to mutate generated scripts. The purpose is
to make the runner better at executing them over time.

## Goal

Build a deterministic experience-driven recovery layer for Oracle flows:

1. Strict execution first.
2. Minimal action-scoped waiting.
3. Oracle-specific control handler.
4. Experience retrieval.
5. AI self-repair.
6. Postcondition validation.
7. Clear failure if none of the above works.

## Non-goals

- Do not use free-form chat memory as execution logic.
- Do not automatically replay arbitrary LLM suggestions from old runs.
- Do not mark success from a click/fill that merely did not throw.
- Do not broaden global fallback chains.

## Core idea

Every failed or recovered action becomes a structured "episode". Future actions
can retrieve similar episodes and reuse only validated recoveries.

Over time:

- one-off successful recoveries stay as retrieved episodes
- repeated successful recoveries become deterministic Oracle handlers
- unstable or low-confidence recoveries stay out of the automatic path

## Runner pipeline

For every action:

1. Execute the raw recorded Playwright locator in strict mode.
2. Apply the smallest required action-scoped wait/delay.
3. If the action targets a known Oracle control family, invoke that specific
   Oracle handler.
4. If strict execution still fails, build a failure signature and retrieve
   similar past episodes.
5. Reuse only recoveries that match the target semantically and have validated
   postconditions.
6. If retrieval does not produce a trusted recovery, call AI self-repair as the
   final recovery layer.
7. Validate the result using a postcondition.
8. Record a new episode with full context and outcome.

## Episode schema

Each episode should be stored as structured JSON.

```json
{
  "episode_id": "uuid",
  "created_at": "2026-04-24T10:15:00Z",
  "runner_version": "ptr-v2",
  "recording_id": "HCM_Promote_and_change_position",
  "recording_name": "HCM_Promote_and_change_position.py",
  "app_family": "oracle",
  "ui_family": "adf",
  "action_type": "click_link",
  "target_label": "Promote and Change Position",
  "target_label_normalized": "promote and change position",
  "control_family": "quick_action",
  "page_signature": {
    "host": "eqjz.ds-fa.oraclepdemos.com",
    "path_hint": "/fscmUI/faces/FuseWelcome",
    "title": "Oracle Fusion Cloud Applications",
    "guided_step": "",
    "url_pattern": "FuseWelcome",
    "visible_anchor_ids": [
      "showmore_groupNode_workforce_management",
      "itemNode_workforce_management_hiring_redwood_0"
    ]
  },
  "failure_signature": {
    "error_type": "TimeoutError",
    "error_hint": "Unable to click text target",
    "ready_state": "complete",
    "busy_indicator_count": 0,
    "target_ready": true
  },
  "recovery": {
    "source": "oracle_handler",
    "kind": "quick_action_expand",
    "handler_name": "oracle_quick_actions_expand",
    "details": {
      "trigger": "Show more quick actions"
    }
  },
  "postcondition": {
    "kind": "visible_target_appeared",
    "details": {
      "target_label": "Promote and Change Position"
    },
    "passed": true
  },
  "outcome": {
    "status": "success",
    "confidence": "high"
  }
}
```

## Minimum fields that matter

These fields must exist for retrieval to be useful:

- `app_family`
- `ui_family`
- `action_type`
- `target_label_normalized`
- `control_family`
- `page_signature`
- `failure_signature.error_type`
- `recovery.kind`
- `postcondition.kind`
- `postcondition.passed`
- `outcome.status`

## Page signature

The page signature should be stable enough for retrieval but not so detailed
that every run becomes unique.

Recommended fields:

- host
- normalized path or path hint
- document title
- guided process step title, if present
- control family visible on the page
- a small list of visible stable ids or label-hints
- Oracle surface type:
  - `redwood_home`
  - `guided_process`
  - `adf_form`
  - `adf_popup`
  - `table_selection`

Avoid storing raw full HTML as the primary signature.

## Failure signature

The failure signature is what the retriever should match against.

Recommended fields:

- normalized error type
- normalized error hint
- action type
- target label
- control family
- ready state
- busy indicator count
- target-ready flag
- page-closed flag
- popup-open flag

## Recovery kinds

Start with a small controlled vocabulary:

- `quick_action_expand`
- `guided_step_auto_advance`
- `guided_step_wait`
- `oracle_select_open_via_toggle`
- `oracle_select_choose_option`
- `adf_select_one_choice_js`
- `adf_popup_open`
- `adf_menu_panel_select`
- `oracle_table_row_refind`
- `oracle_cell_select`
- `target_page_refind`
- `ai_locator_repair`

Do not store arbitrary prose as the primary recovery record.

## Postconditions

Every automatic recovery must have a postcondition.

Allowed postcondition kinds:

- `url_changed`
- `guided_step_changed`
- `popup_opened`
- `menu_opened`
- `dialog_opened`
- `row_selected`
- `cell_selected`
- `field_value_changed`
- `combobox_value_changed`
- `expected_target_visible`
- `oracle_control_state_changed`

If `postcondition.passed` is false, the episode must not be eligible for
automatic reuse.

## Retrieval policy

Use exact filters first:

- same `app_family`
- same `ui_family` when available
- same `action_type`
- same normalized target label or approved alias

Then score similarity on:

- page path/title similarity
- control family match
- guided step match
- error type match
- Oracle surface type match

Suggested weighting:

- target label exact or alias match: 35
- action type match: 20
- control family match: 15
- page signature similarity: 15
- error type match: 10
- guided step match: 5

Reject reuse unless:

- outcome is `success`
- postcondition passed
- recovery source is trusted
- semantic match is strong enough

## Trust model

Automatic reuse is allowed only for:

- deterministic Oracle handlers
- previously validated experience entries with strong signature match
- AI recoveries that were later validated and repeated successfully

Automatic reuse is not allowed for:

- one-off AI recoveries with weak semantic match
- recoveries that clicked a different label than requested
- recoveries with missing or failed postconditions

## Promotion pipeline

Episodes should graduate through stages:

1. Logged
   - captured after a run
2. Candidate
   - succeeded with a valid postcondition
3. Reusable
   - same recovery succeeds multiple times with strong signature match
4. Promoted
   - implemented as deterministic runner logic

Promotion rule suggestion:

- same recovery kind
- same control family
- same page signature cluster
- at least 3 successful runs
- no recent semantic mismatch

Promoted behavior should move out of retrieval memory and into code.

## Storage options

Start simple:

- JSONL file or object-storage JSON documents keyed by episode id

Then move to:

- SQLite or Postgres table for retrieval and promotion analytics

The first version does not need vectors.

Start with structured exact/fuzzy retrieval over normalized fields.

## Integration points in the runner

The experience system should plug into the runner at these points:

1. After strict execution fails but before AI self-repair
2. After a recovery attempt completes
3. After postcondition validation
4. During report generation for debugging visibility

The runner should record:

- strict failure
- recovery chosen
- whether recovery came from handler, experience store, or AI
- postcondition result
- final episode record

## Reporting requirements

Reports should show:

- failing layer
- chosen recovery source
- episode match summary if retrieval was used
- postcondition used
- whether the recovery was promoted, reusable, or rejected

## Instruction update required

Yes. Codex instructions should be updated so future work stays aligned with this
design.

Add these rules:

- Treat runner learning as structured experience retrieval, not chat memory.
- Reuse only validated experience entries with passed postconditions.
- Promote repeated successful recoveries into deterministic Oracle handlers.
- Never auto-reuse AI suggestions that do not semantically match the target.
- Do not broaden generic fallback logic when an experience-backed Oracle handler
  would be more precise.

## First implementation slice

Phase 1:

- Add episode schema
- Log structured episodes for every failed and recovered action
- Add basic retrieval scaffolding with exact-match filters

Phase 2:

- Use retrieval before AI self-repair
- Enforce postcondition-backed reuse only
- Add report visibility for retrieved episodes

Phase 3:

- Add promotion metrics
- Convert repeated recoveries into deterministic Oracle handlers
- Shrink the live AI recovery surface over time

## Design principle

The runner should not become "smarter" by growing more global fallback logic.

It should become more robust by:

- staying strict first
- remembering validated Oracle-specific recoveries
- promoting stable recoveries into code
- rejecting weak or semantically incorrect recoveries
