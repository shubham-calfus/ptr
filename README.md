# test_runner

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
- Set `PTR_BROWSER_PROVIDER=steel` to require Steel explicitly and fail fast if the API key is missing.
- Set `PTR_BROWSER_PROVIDER=local` to require local Chromium explicitly.
- If `PTR_BROWSER_PROVIDER` is unset, `PTR_IS_LOCAL_ENV=true` forces local Chromium for local development.
- If `PTR_BROWSER_PROVIDER` is unset and `PTR_IS_LOCAL_ENV` is not true, the runner uses Steel whenever `STEEL_API_KEY` is present; otherwise it falls back to local Chromium.
- Local Chromium launch honors `PTR_CHROMIUM_EXECUTABLE_PATH` first, then `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`, then Playwright's `channel="chromium"` fallback.
- `STEEL_SESSION_ID` optionally reuses an existing Steel session instead of creating a new one.
- `STEEL_CONNECT_URL` optionally overrides the Steel CDP endpoint.
- `PTR_STEEL_CONNECT_RETRIES` and `PTR_STEEL_SESSION_TIMEOUT_MS` tune Steel connection retries and session lifetime.

## AI failure summaries

Failed runs can optionally be summarized with OpenAI after execution. The runner
uses the captured logs, failure screenshot, and step screenshots, then stores an
`ai_failure_summary` object in the manifest and renders it in the HTML report.

Environment variables:

- `OPENAI_API_KEY` enables the feature.
- `OPENAI_FAILURE_SUMMARY_MODEL` optionally overrides the default model.
- `OPENAI_FAILURE_SUMMARY_ENABLED=false` disables the feature without removing the key.
- `OPENAI_BASE_URL` optionally points to a compatible Responses API base URL.

## Design docs

- [`RUNNER_EXPERIENCE_SYSTEM.md`](/Users/shubhammore/Documents/act-v2/test_runner/RUNNER_EXPERIENCE_SYSTEM.md)
  defines the planned experience-driven recovery system for the Oracle-focused
  runner.
- [`CODEX_CUSTOM_INSTRUCTIONS.md`](/Users/shubhammore/Documents/act-v2/test_runner/CODEX_CUSTOM_INSTRUCTIONS.md)
  contains a paste-ready Codex custom-instructions block aligned with the new
  runner direction.
# ptr
