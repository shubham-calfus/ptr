# Claude Code Custom Instructions

Paste the block below into Claude Code project instructions or custom
instructions if you want it aligned with the current Oracle-focused runner
direction in this repository.

```text
You are working in a repository with two separate Playwright systems:

1. playwright-codegen
- This is the recording/code generation side.
- Phantom owns the real Playwright desktop session, noVNC/browser lifecycle, and the raw generated recording scripts.

2. playwright_test_runner
- This is the execution side.
- The runner downloads stored Python Playwright recordings and executes them directly in its own worker process.
- The runner does not execute recordings through Phantom.

Ownership rules:
- Once Phantom generates a Playwright script and hands it to the runner, treat it as read-only execution input.
- Do not solve runner failures by editing or rewriting generated scripts unless I explicitly ask.
- If the issue is generation quality, YAML/backfill, or recording authoring, that belongs to Phantom/codegen.
- If the issue is replay, parameter loading, flow context, manifests, diagnostics, reports, fallback behavior, or postconditions, that belongs to the runner.

Primary goal:
- Keep the Oracle-focused runner small, deterministic, and debuggable.
- Prefer deleting broad fallback behavior over stacking more heuristics.
- Do not preserve old and new execution models in parallel.

Execution order:
strict execution -> minimal scoped wait/delay -> Oracle-specific control handler -> experience retrieval -> AI self-repair -> postcondition validation -> fail clearly

Mandatory execution rules:
- Start with the exact recorded Playwright locator and the exact recorded action.
- For normal input/select/search flows, try the raw recorded locator pair first.
- Do not replace strict execution with broad role/text/label scanning unless strict execution fails.
- Do not add generic "try everything" fallback chains.
- Do not let global readiness heuristics block an action if the actual target locator is already actionable.
- Prefer action-scoped waits over global page settle gates.
- Avoid hard dependencies on networkidle unless truly required.
- A non-throwing click, fill, or selection is not success by itself.

Postcondition rules:
- Every action must have a real postcondition.
- Valid postconditions include:
  - field value changed
  - selected option or control value changed
  - expected row or cell selected
  - popup, menu, or dialog opened
  - guided step changed
  - Oracle control state changed
  - expected content appeared
  - URL or title changed
  - Continue/Submit advanced the flow
- If the postcondition does not pass, the action failed.

Oracle support rules:
- Support Oracle ADF / Redwood intentionally, not generically.
- Build narrow deterministic handlers for known Oracle patterns such as:
  - quick actions / Show More quick actions
  - guided process navigation / step transitions
  - oj-select-single / oj-c-select-single
  - oj-input-text / oj-c-input-text
  - oj-input-number / oj-c-input-number
  - oj-input-date / oj-c-input-date
  - oj-text-area / oj-c-text-area
  - LOV / listbox / popup / dropdown
  - split buttons / menu panels / ADF popup menus
  - action cards / switches / checkboxes
  - table / row / cell selection
  - live inline table editors
  - dialog and popup interaction patterns
- If you add a recovery rule, explain exactly which Oracle pattern it covers.
- Do not introduce broad global behavior that can affect unrelated flows.
- When repeated labels exist, prefer row-scoped or control-scoped behavior over broad label scans.

Experience system rules:
- Treat runner learning as structured experience retrieval, not chat memory.
- Store failures and recoveries as structured episodes with page signature, failure signature, recovery, postcondition, and outcome.
- Reuse only trusted successful episodes whose postcondition passed.
- Promote repeated successful Oracle-specific recoveries into deterministic handlers.
- Never auto-reuse a recovery that previously clicked or selected the wrong semantic target.

AI self-repair rules:
- AI self-repair is last resort only.
- AI is a recovery layer, not the primary execution path.
- Send AI only the minimum useful DOM/context needed for the failed action.
- AI-generated locators must be semantically validated against the requested label/control before use.
- Reject AI suggestions that do not clearly match the intended target.
- Never mark success just because an AI click or fill did not throw.

Script preparation rules:
- The runner executes Python Playwright recordings only.
- Keep generated recordings read-only.
- Put resilience in the AST pipeline, optimizer, script generator, and runtime helpers, not in stored scripts.
- If a single parsed action lacks AST helper coverage, allow one explicit inline raw-step fallback at that exact point and keep the rest of the recording on the AST/helper path.
- If preparation fails before an action-level fallback is possible, allow one explicit fallback to the substituted raw recording and label that run clearly instead of reviving broad legacy fallback chains.

Suite/data rules:
- Parameter values can come from script defaults, workbook/CSV params files, and inline overrides.
- Flow Context inputs must be resolved before execution starts.
- If unresolved placeholders remain, fail before execution instead of guessing.
- resume_from_run_id is supported only in sequential mode.
- resume_from_run_id rebuilds context only from earlier recordings in the same sequential suite payload, not from an arbitrary unrelated upstream run.
- If a child depends on a parent output, the suite must run sequentially or the concrete value must be passed directly.

Debugging workflow:
- Identify the failing layer before changing code:
  1. strict locator/action
  2. minimal wait/delay
  3. Oracle handler
  4. experience retrieval
  5. AI self-repair
  6. postcondition validation
- Use the actual traceback, report step, script snippet, locator string, active element, and visible page state.
- Explain failures using the exact runtime path that fired.
- Separate runner-code issues from published runtime/image freshness issues.
- If local code is fixed but reports still show old behavior, check whether the live worker/image is updated.

Reporting and diagnostics rules:
- Preserve and improve observability.
- Keep action logs, step screenshots, failure screenshots, diagnostics snapshots, flow-context results, and HTML report details accurate.
- When changing execution behavior, make sure the failure report still shows enough evidence to identify the failing layer.

Working style:
- Optimize for correctness, determinism, and debuggability over recovery rate.
- Keep fixes narrow.
- Prefer simplifying or replacing bad helpers over adding more branches on top.
- Avoid unrelated changes and dead code.
- When changing runner behavior, update or add targeted tests so the intended architecture is enforced.
- If a proposed change increases global complexity or regression risk, say so explicitly.
- If the issue belongs to another layer, say that before editing runner code.
```
