# ACT Agent

Runs Playwright-generated Python scripts as a test suite and produces an HTML report.

## Intended flow

1. User selects a test suite in Sombrero.
2. Agent receives `test_suite_id` and ordered `recordings`.
3. Each recording script is downloaded from storage and executed.
4. Results are stored as manifests in object storage.
5. A final HTML report is generated and uploaded.

## Current status

This is a first runnable scaffold that mirrors the ACT parent/child/report pattern.
Before publishing, validate it locally with:

- a Playwright-capable runtime
- real recording `.py` files already stored in `STORAGE_ACTIVITIES_BUCKET`
- working object storage credentials

## Execution assumptions

- Recording files are Python Playwright scripts.
- The worker runtime has Python and Playwright installed.
- The runner executes scripts directly in its own worker process; it does not proxy execution through Phantom.
- Script execution uses `python3` by default.
- You can override the interpreter with `PLAYWRIGHT_TEST_PYTHON_BIN`.
- The runner is pinned to local Chromium for replay; it does not select Steel for execution.
- Local Chromium launch checks `PTR_CHROMIUM_EXECUTABLE_PATH`, `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`, `/usr/bin/chromium`, `/usr/bin/chromium-browser`, `/usr/lib/chromium/chrome`, and Playwright cache paths.
- If Chromium is missing, the runner attempts `python -m playwright install chromium` once by default before failing.
- Set `PTR_AUTO_INSTALL_CHROMIUM=false` to disable that install attempt and fail immediately.

## AI failure summaries

Failed runs can optionally be summarized with OpenAI after execution. The runner
uses the captured logs, failure screenshot, and step screenshots, then stores an
`ai_failure_summary` object in the manifest and renders it in the HTML report.

Environment variables:

- `OPENAI_API_KEY` enables the feature.
- `OPENAI_FAILURE_SUMMARY_MODEL` optionally overrides the default model.
- `OPENAI_FAILURE_SUMMARY_ENABLED=false` disables the feature without removing the key.
- `OPENAI_BASE_URL` optionally points to a compatible Responses API base URL.

## AI extraction in recordings (`ai_extract`)

Playwright recordings can read a value straight off the rendered page with a
vision-LLM call, for cases where no stable locator or parameter exists (e.g. the
transaction number of the first row in a freshly loaded table).

In the recording, call it as a top-level statement:

```python
ai_extract("transaction_number", "extract the transaction number from the first row of the table")
```

- First argument is the **name** to store the value under; second is the
  **prompt** describing what to read. (Order mirrors `api_helpers.extract(name, value)`.)
- At that point in the run, the runner settles the page, screenshots it, sends
  the screenshot + prompt to the vision model, and stores the returned value.
- **Do not import `ai_extract`** — it is rewritten by the AST pipeline, not a
  module function.

Using the extracted value:

- **Same recording** — reference `{{transaction_number}}` later in the *same*
  script (e.g. inside a locator name or fill value). It resolves at runtime:

  ```python
  page.get_by_role("link", name="{{transaction_number}}").click()
  ```

- **Another recording** — the value is published to the run's extracted outputs /
  Flow Context, so a later recording in a **sequential** suite can consume it the
  same way it consumes any upstream output.

Requirements:

- `OPENAI_API_KEY` must be set, and `PTR_AI_SELF_REPAIR_ENABLED=true` (the AI gate
  this feature shares). If either is missing, the step fails rather than silently
  skipping.
- If a slow page (e.g. an Oracle work area) is captured before it finishes
  rendering, raise the recording's `after_action_wait_ms` (or
  `PTR_AFTER_ACTION_WAIT_MS`) so the screenshot matches the visible step.

Troubleshooting:

- `NameError: name 'ai_extract' is not defined` in a report means the run executed
  your **raw recording** because AST preparation was skipped — almost always a
  **stale worker** running code from before `ai_extract` existed. Restart the
  worker (or republish the image) from current source. It is never a missing import.

## Design docs

- [`RUNNER_EXPERIENCE_SYSTEM.md`](/Users/shubhammore/Documents/act-v2/act_agent/RUNNER_EXPERIENCE_SYSTEM.md)
  defines the planned experience-driven recovery system for the Oracle-focused
  runner.
- [`CODEX_CUSTOM_INSTRUCTIONS.md`](/Users/shubhammore/Documents/act-v2/act_agent/CODEX_CUSTOM_INSTRUCTIONS.md)
  contains a paste-ready Codex custom-instructions block aligned with the new
  runner direction.
# ptr
