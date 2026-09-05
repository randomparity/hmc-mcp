# Single-Read Permission Configuration Implementation Plan

Goal: make one effective-permissions report use one invocation-scoped configuration document.
The configuration layer owns snapshot creation and `build_config` consumption; the permission
layer owns one snapshot per report and existing unresolved classification. The stack is Python,
pytest, pydantic-settings, and stdlib TOML parsing.

## Global Constraints

- Python versions and target architectures remain those declared by the repository and CI:
  Python 3.11 through 3.14 on amd64 and arm64 Ubuntu runners.
- Add no dependency, cache, public API, schema, migration, or external service.
- Preserve `HMC_HOST` environment-only behavior, environment-over-profile precedence,
  fresh-per-call behavior, and existing exception classifications.
- Run `just verify` and `UV_NO_SYNC=1 uv run prek run --all-files`; CI separately gates generated
  tool documentation and document freshness.

## Task 1: Pin the single-read report contract

Files: modify `tests/app/test_power_guard_report.py`.

Interfaces: consume `resolve_power_guards(policy)` and spy on
`hmc_mcp.config._read_config_document(path: Path) -> dict[str, Any]`; later tasks must make the
existing call return the same `PowerOwnershipGuard` rows with one reader invocation.

1. Add a test that writes two profiles with different guard values, grants both connections,
   wraps `_read_config_document` with a counting spy, calls `resolve_power_guards`, and asserts the
   two existing values plus a count of one.
2. Add zero-read assertions for ambient `HMC_HOST` (including a case variant) and for a policy with
   no reachable connection tokens.
3. Add a malformed-document/two-connection test asserting one read attempt, two ordered unresolved
   `ConfigError` rows, and the existing connection-scoped warning for each row.
4. Add a path-resolution-failure test that asserts the existing generic exception detail remains
   unchanged rather than becoming `ConfigError`.
5. Add a fresh-per-call test that writes one guard value, resolves a report, rewrites the file with
   the opposite value, resolves a second report, and asserts the second invocation observes the
   new value while each invocation performs no more than one document read.
6. Run `uv run --no-sync pytest tests/app/test_power_guard_report.py -q`; expect the new tests to
   fail because the count is two.

Acceptance: the test fails only on the repeated-read assertion and continues to pin both values.

## Task 2: Add and consume an invocation snapshot

Files: modify `src/hmc_mcp/config.py`, `src/hmc_mcp/server_tools/permissions.py`, and focused
`tests/unit/test_config.py` coverage if configuration behavior is not already pinned by the app
suite.

Interfaces: define internal frozen `_ConfigDocument(path: Path | None, data: dict[str, Any])`,
`load_config_document() -> _ConfigDocument`, beginning with `resolve_config_path`, and extend
`build_config(profile: str | None = None, *, document: _ConfigDocument | None = None,
**overrides: Any) -> HMCConfig`. `_power_guard` consumes a snapshot or captured exception;
`resolve_power_guards` produces at most one snapshot and passes it to every guard resolution.

1. Add `_ConfigDocument` and `load_config_document`; call `resolve_config_path` directly, then
   `_read_config_document` once, or use an empty mapping when no path exists. Do not route through
   `_selected_config_path`, whose exception normalization would change the generic detail that
   `_power_guard` currently reports for path-resolution failures.
2. Extend `build_config` so its existing profile branch loads a profile from `document.data` and
   `document.path` when supplied, while its no-document path retains `load_profile` unchanged.
3. Extend `_power_guard` to accept the snapshot outcome and retain the existing exception-to-detail
   branches.
4. Make `resolve_power_guards` create the snapshot once after the `HMC_HOST` collapse, capture a
   creation exception once, and pass the outcome through the ordered loop. Do not create a snapshot
   for environment-only resolution or an empty connection set.
5. Add focused `tests/unit/test_config.py` coverage proving a supplied document bypasses path and
   file resolution while retaining profile selection and environment precedence.
6. Run `uv run --no-sync pytest tests/app/test_power_guard_report.py tests/unit/test_config.py -q`;
   expect all focused tests to pass.

Acceptance: one report has at most one read/parse; all existing report cases remain unchanged;
separate calls read afresh; exception detail remains `ConfigError` or the existing closed generic
classification. Rollback is a normal revert because no data or external state changes.

## Task 3: Verify the branch

Files: no planned source changes; regenerate only artifacts whose repository recipe reports stale.

Interfaces: consume the completed branch and provide green guardrail evidence to delivery.

1. Run `just verify`; expect exit 0.
2. Run `UV_NO_SYNC=1 uv run prek run --all-files`; expect exit 0.
3. Review `git diff $(git merge-base HEAD origin/main)` for scope, naming, and stale documentation.

Acceptance: both required commands pass with no warnings and the diff contains only issue #536's
design, implementation, tests, and recipe-required generated artifacts.
