# TrafficMonitor Seven Optimizations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the seven previously identified optimizations for the TrafficMonitor Codex Usage plugin and verify the packaged plugin in the local TrafficMonitor environment.

**Architecture:** Keep the TrafficMonitor DLL responsible for UI, settings, and refresh process orchestration, while the Python collector remains responsible for Codex data discovery and snapshot generation. Add behavior tests before each production change, using Python unittest for collector behavior and lightweight source/CI assertions where direct DLL runtime tests are impractical.

**Tech Stack:** C++17 Win32 DLL, PowerShell wrapper, Python 3 unittest collector tests, GitHub Actions on Windows, TrafficMonitor local plugin deployment.

---

### Task 1: Correct Rollout Token Baselines Across Midnight

**Files:**
- Modify: `scripts/collect_codex_usage.py`
- Test: `tests/test_rollout_token_breakdown.py`

- [ ] Add a failing test proving a thread created before today uses the last pre-midnight `token_count` as baseline instead of the first in-day event.
- [ ] Run `python -m unittest tests.test_rollout_token_breakdown -v` and confirm the new test fails with the current collector.
- [ ] Update `scan_rollout_token_usage` to retain the latest totals before `start_ms` when the thread predates the current day.
- [ ] Re-run `python -m unittest tests.test_rollout_token_breakdown -v` and confirm it passes.

### Task 2: Prevent Overlapping Collector Launches

**Files:**
- Modify: `src/CodexUsagePlugin.cpp`
- Test: `tests/test_refresh_interval_feature.py`

- [ ] Add a failing source-level test requiring an in-flight collector handle check and cleanup before launching another collector.
- [ ] Run `python -m unittest tests.test_refresh_interval_feature -v` and confirm failure.
- [ ] Add a stored process handle, close completed collector handles, and return early for forced/manual refresh while a collector is still active.
- [ ] Re-run `python -m unittest tests.test_refresh_interval_feature -v` and confirm it passes.

### Task 3: Make Reset-Credit Fetching Explicitly Configurable And Cached

**Files:**
- Modify: `src/CodexUsagePlugin.cpp`
- Modify: `scripts/update_codex_usage.ps1`
- Modify: `scripts/collect_codex_usage.py`
- Modify: `README.md`
- Modify: `docs/data-format.md`
- Test: `tests/test_reset_credits.py`
- Test: `tests/test_options_dialog_layout.py`

- [ ] Add failing collector tests proving reset credits are skipped when disabled and reused from a fresh cache without another network call.
- [ ] Add failing plugin/source tests proving the options dialog exposes a reset-credit checkbox and passes the setting to the wrapper.
- [ ] Run the relevant tests and confirm failure.
- [ ] Add `--reset-credits enabled|disabled`, a cache JSON path, and cache TTL to the Python collector.
- [ ] Wire `-ResetCreditsMode` through the PowerShell wrapper.
- [ ] Persist `reset_credits_enabled` in `codex_usage_plugin.ini`, expose it in the options dialog, and pass it to the collector command.
- [ ] Update docs to describe the explicit setting and cache behavior.
- [ ] Re-run reset-credit and options tests.

### Task 4: Harden DLL Snapshot JSON Parsing

**Files:**
- Modify: `src/CodexUsagePlugin.cpp`
- Test: `tests/test_reset_display.py`

- [ ] Add failing source-level tests requiring escaped Unicode handling and top-level-key parsing rather than naive substring matching.
- [ ] Run `python -m unittest tests.test_reset_display -v` and confirm failure.
- [ ] Replace the current key lookup/string decoding helpers with a bounded scanner that matches top-level keys and decodes `\uXXXX` escapes, including surrogate pairs.
- [ ] Re-run the parser-related tests.

### Task 5: Add A Real C++ Smoke Test Target

**Files:**
- Modify: `src/CodexUsagePlugin.cpp`
- Create: `tests/cpp/json_parser_smoke.cpp`
- Modify: `CMakeLists.txt`
- Test: `tests/test_cpp_smoke_target.py`

- [ ] Add a failing Python test requiring a CMake-enabled C++ smoke executable.
- [ ] Run `python -m unittest tests.test_cpp_smoke_target -v` and confirm failure.
- [ ] Gate the plugin `main` export only for DLL builds if needed, add a smoke executable that exercises JSON parsing and tooltip-safe values, and register it with CTest.
- [ ] Build/run the smoke executable through CMake or `ctest`.

### Task 6: Improve Collector Scan Performance

**Files:**
- Modify: `scripts/collect_codex_usage.py`
- Test: `tests/test_session_rate_limits.py`

- [ ] Add failing tests requiring session scanning to stop after the newest plausible rate-limit event and avoid opening older files unnecessarily.
- [ ] Run `python -m unittest tests.test_session_rate_limits -v` and confirm failure.
- [ ] Sort candidate session files once with cached stat info and stop scanning files older than the best found event.
- [ ] Re-run session rate-limit tests.

### Task 7: Add CI Validation For Pull Requests And Pushes

**Files:**
- Modify: `.github/workflows/release.yml`
- Test: `tests/test_ci_workflow.py`

- [ ] Add a failing workflow test requiring `pull_request` and `push` validation paths in addition to tag release.
- [ ] Run `python -m unittest tests.test_ci_workflow -v` and confirm failure.
- [ ] Update the workflow triggers so non-tag pushes and PRs run tests/build without publishing a release.
- [ ] Run the workflow test and full unittest suite.

### Final Verification

- [ ] Run `python -m unittest discover -s tests -v`.
- [ ] Run `.\tools\build.ps1 -Platform x64 -Configuration Release -SkipSign`.
- [ ] Build and run the C++ smoke test.
- [ ] Close local TrafficMonitor if running, copy the new DLL and scripts into the local TrafficMonitor plugin directory, restart TrafficMonitor, and verify the status/log paths and plugin load behavior.
- [ ] Confirm `git status --short` only contains intentional changes.
