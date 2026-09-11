# Cleanup knowledge v1 checklist

This checklist maps each enablement gate from "Acceptance and rollout gates" in
`docs/cleanup-knowledge-design.md` to its v1 status, the test that covers it, and
the command that verifies it. v1 ships read-only advice plus a resolver for uv,
npm, and go. Native execution is not enabled.

| Design gate | v1 status | Covering test | Verify command |
| --- | --- | --- | --- |
| CI installs the toolchain | Deferred to phase 2. v1 never executes native cleanup, so CI does not need the toolchains yet. | none | `uv run pytest -q` |
| Real subprocess integration tests | Passed | `tests/test_resolver.py::test_fake_executable_end_to_end` | `uv run pytest tests/test_resolver.py -q` |
| Preview target equals execute target | Passed | `tests/test_cleanup_e2e.py::test_preview_target_equals_reresolved_target` | `uv run pytest tests/test_cleanup_e2e.py -q` |
| Timeout, partial failure, tool missing, and Trash fallback coverage | Passed | `tests/test_resolver.py` (timeout and nonzero exit), `tests/test_trash.py` (partial failure), `tests/test_cleanup_e2e.py::test_tool_missing_reports_adapter_missing_with_trash_fallback` | `uv run pytest tests/test_resolver.py tests/test_trash.py tests/test_cleanup_e2e.py -q` |
| Windows reports `adapter_missing` where unsupported | Passed | `tests/test_cleanup_e2e.py::test_unsupported_platform_reports_adapter_missing` | `uv run pytest tests/test_cleanup_e2e.py -q` |
| Outcome-based acceptance | Passed | `tests/test_cleanup_e2e.py::test_scan_advice_action_state_change`, `tests/test_cleanup_e2e.py::test_disk_state_after_trash_move` | `uv run pytest tests/test_cleanup_e2e.py -q` |
| Native execution enabled adapter by adapter | Not enabled in v1 (phase 2). The `adapter_allowlist` defaults to empty, which is the read-only kill switch. | none | `uv run space-scout config list` (prints `adapter_allowlist: read-only`) |

## Scope note

The v1 resolver supports uv, npm, and go only. Bun, Cargo, pyenv, rustup, and
editor caches remain advisory entries without a native adapter; their advice
carries an actionable Trash path and a regeneration note, and `method` is never
empty.