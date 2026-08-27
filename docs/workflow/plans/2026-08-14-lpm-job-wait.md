# LPM job wait implementation plan

**Goal:** Add bounded optional waiting to LPM abort, recover, and remote-restart
while returning one stable job outcome from each public tool.

**Architecture:** Presentation-neutral LPM operations validate timing, submit,
optionally poll through the existing job helper, and normalize through the #141
outcome model. MCP and CLI layers expose matching parameters and serialize that
same result; migrate and migrate-validate retain their current raw-job behavior.

**Tech stack:** Python 3.13, FastMCP, Typer, pytest/respx, uv, ruff, ty.

## Global constraints

- Branch `feat/lpm-job-wait-150`, base `main`; final guardrail is `just verify`.
- Host architecture is `arm64`; no target architecture is declared.
- Each operation and MCP tool accepts `wait: bool = False`,
  `timeout_seconds: int = 300`, and `poll_interval: int = 5`.
- All three MCP tools return exactly `job_id: str`, `status: str | None`,
  `timed_out: bool`, `error: str | None`, and `job: dict[str, Any] | None`.
- `wait=False` returns a normalized submission with `timed_out=False`;
  `wait=True` normalizes the terminal or timeout result from shared polling.
- Do not add #151 validation sequencing, change migrate/migrate-validate output,
  alter dependencies or migrations, edit an ADR index, or broaden #145 docs.
- Validation happens before selector resolution, confirmation, or submission.

## Task 1: Normalize the three LPM operation results

**Files:** Modify `src/hmc_mcp/operations/lpm.py`; extend
`tests/lpar/test_lpm_tools.py`.

**Interfaces:** Import `JobOutcome`, `job_identifier`, and `job_outcome`. Widen
`LpmResult.job` to `dict[str, Any] | JobOutcome | None`. Add private
`async def _finish_job(hmc: HMCClient, job: dict[str, Any] | None, wait: bool,
timeout_seconds: int, poll_interval: int) -> JobOutcome`. Add the standard
keyword-only wait triple to `abort_lpar_migration`, `recover_lpar_migration`,
and `remote_restart_lpar`.

1. Add operation-level parameterized tests for abort, recover, and remote
   restart proving invalid active timing raises before the mocked resolver or
   client submission; immediate calls do not poll and return a five-field
   outcome with `timed_out is False`; waited calls poll to COMPLETED,
   FAILED/EXCEPTION, and a nonterminal timeout. Assert the same key set and field
   types across outcomes. Run
   `uv run pytest -q tests/lpar/test_lpm_tools.py`; expect only the new
   operation-level tests to fail because those three operation signatures do
   not accept the wait triple. Do not add MCP wrapper tests until Task 2.
2. Implement `_finish_job`: derive `submitted_id = job_identifier(job) or ""`;
   if `wait` is false, call `job_outcome(submitted_id, job)` and use
   `dataclasses.replace(..., timed_out=False)`; otherwise await
   `wait_for_submitted_job(hmc, job, True, timeout_seconds, poll_interval)` and
   return `job_outcome(submitted_id, completed_job)`. In each owned operation,
   call `validate_wait_timing` first, preserve existing selector/submission
   order, and return `_finish_job` in `LpmResult.job`.
3. Re-run `uv run pytest -q tests/lpar/test_lpm_tools.py`; expect all focused
   tests to pass. Commit as `feat: add stable waits to LPM operations`.

**Acceptance:** All three operations validate before remote work, use the shared
poller and normalizer, distinguish terminal failure and timeout, and retain raw
HMC data only in the stable outcome's `job` field.

## Task 2: Expose the stable MCP contract

**Files:** Modify `src/hmc_mcp/server_tools/lpm.py`; extend
`tests/lpar/test_lpm_tools.py` and `tests/app/test_capabilities.py`.

**Interfaces:** Each owned MCP function declares `-> JobOutcome`, accepts
`wait=False`, `timeout_seconds=300`, `poll_interval=5`, forwards them by keyword,
and returns the operation result's `JobOutcome`. Migrate and migrate-validate
signatures and returns remain unchanged.

1. Add schema tests asserting exactly the standard three input defaults and the
   five typed output properties for all three tools. Add tool tests for immediate,
   success, failure/exception, and timeout values. Run
   `uv run pytest -q tests/lpar/test_lpm_tools.py tests/app/test_capabilities.py`;
   expect failures from missing parameters and raw result annotations.
2. Import `JobOutcome`, add and forward the parameters, return the normalized
   outcome, and update each docstring to state that it always returns a job
   outcome, that `wait=False` returns after submission, and that `wait=True`
   blocks until terminal state or timeout.
3. Re-run the focused command; expect all tests to pass. Commit as
   `feat: expose stable LPM wait outcomes`.

**Acceptance:** Generated schemas and runtime values have one identical key/type
set across modes; no other MCP tool schema changes.

## Task 3: Mirror wait controls in the CLI

**Files:** Modify `src/hmc_mcp/cli_commands/lpars.py`; extend
`tests/app/test_cli_commands.py`.

**Interfaces:** Add `wait`, `timeout`, and `interval` Typer options with defaults
`False`, `300`, and `5` to `migrate-abort`, `migrate-recover`, and
`remote-restart`. Each command calls `validate_wait_timing(wait, timeout,
interval)` before `_lpm_run` and forwards the values to its operation. Update
`_lpm_run` to render `dataclasses.asdict(result.job)` only when the payload is a
`JobOutcome`; retain raw dictionaries for existing commands.

1. Extend command-runner cases to pass `--wait --timeout 60 --interval 1` and
   assert each fake client call receives the corresponding keyword arguments.
   Add an invalid active timing case proving no confirmation or operation call.
   Add one presentation test whose operation returns a `JobOutcome` with
   `job_id="job-uuid-999"`, `status="RUNNING"`, `timed_out=True`, `error=None`,
   and a raw job dictionary; assert the CLI emits those exact five JSON fields
   with their native types. Run `uv run pytest -q tests/app/test_cli_commands.py`;
   expect the new options to be rejected and the timeout outcome to serialize
   as a string rather than the required JSON object.
2. Add the three options and validation/forwarding exactly as specified. Import
   `asdict` and `JobOutcome`, and convert only that payload in `_lpm_run` before
   `_print_json`.
3. Re-run `uv run pytest -q tests/app/test_cli_commands.py`; expect all tests to
   pass. Commit as `feat: add LPM wait flags to CLI`.

**Acceptance:** All three CLI mirrors expose matching defaults, reject invalid
active timing before action, forward the values, and render the stable outcome.

## Task 4: Cross-surface verification

**Files:** Review every changed path; modify only focused tests or artifacts if
the reviewed contract exposes a gap.

**Interfaces:** No new interface. The three layers must agree on parameter names,
defaults, and the `JobOutcome` fields.

1. Run `uv run pytest -q tests/lpar/test_lpm_tools.py
   tests/app/test_cli_commands.py tests/app/test_capabilities.py`; expect all
   selected tests to pass.
2. Run `git diff --check main...HEAD` and inspect `git diff main...HEAD`; expect
   no whitespace errors, unrelated paths, #151 behavior, or other tool shapes.
3. Run `just verify`; expect lint, type checking, secret/workflow/env checks,
   1,082 or more passing tests, smoke import, and all CLI groups to pass. Commit
   any bounded review fix separately; never weaken a test or guardrail.

**Acceptance:** The focused suite and full repository guardrail pass, and the
diff remains inside the frozen surface. Rollback is a normal `git revert` of the
feature commits; no persisted data or external setup requires cleanup.
