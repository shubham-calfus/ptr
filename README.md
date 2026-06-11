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

## Design docs

- [`RUNNER_EXPERIENCE_SYSTEM.md`](/Users/shubhammore/Documents/act-v2/test_runner/RUNNER_EXPERIENCE_SYSTEM.md)
  defines the planned experience-driven recovery system for the Oracle-focused
  runner.
- [`CODEX_CUSTOM_INSTRUCTIONS.md`](/Users/shubhammore/Documents/act-v2/test_runner/CODEX_CUSTOM_INSTRUCTIONS.md)
  contains a paste-ready Codex custom-instructions block aligned with the new
  runner direction.
# ptr
