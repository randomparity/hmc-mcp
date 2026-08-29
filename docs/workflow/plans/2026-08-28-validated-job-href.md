# Validated job-href implementation plan

Goal: reject URL-parser-deleted controls before job-link parsing so accepted and echoed handles
retain one consistent cleaned spelling. The operations layer owns cleaning and delegates only an
accepted link to the existing client path guard. Python 3.11–3.14 on amd64 and arm64 remains the
supported CI matrix; no dependency or schema changes are needed.

## Global constraints

- Reject TAB (`U+0009`), LF (`U+000A`), and CR (`U+000D`) before URL parsing.
- Preserve valid relative/absolute spelling after existing surrounding-whitespace trimming.
- Host, query, and fragment remain unchecked and unrequested.
- Preserve #529 stale-link path equivalence and #532 confirming-read scheduling unchanged.
- Use only the Python standard library and existing repository dependencies.
- Required guardrails: `just verify` and
  `UV_NO_SYNC=1 uv run --no-sync prek run --all-files`; regenerate tool docs with
  `just tool-docs` before freshness checks.

## Task 1: Prove and implement operations-boundary rejection

Files:

- Modify `tests/unit/test_operations_jobs.py`.
- Modify `tests/app/test_server_tools.py`.
- Modify `src/hmc_mcp/operations/jobs.py`.

Interfaces:

- Existing input: `_clean_job_href(job_href: str | None) -> str | None`.
- Existing consumers: `get_job(...) -> JobOutcome` and `wait_for_job(...) -> JobOutcome`.
- Later tasks rely on `ValueError` being raised before `HMCClient.get_job` is awaited.

Steps:

1. Add parameterized tests over `"\t"`, `"\r"`, and `"\n"` embedded in an otherwise valid job
   link. Call each operations entry point and the served `hmc_wait_for_job`, expect `ValueError`
   matching a fixed `job_href` control-character message, and assert the client mock was not
   awaited.
2. Run
   `uv run --no-sync pytest tests/unit/test_operations_jobs.py tests/app/test_server_tools.py -q`;
   expect the new cases to fail because `_clean_job_href` currently returns the control-bearing
   string.
3. In `_clean_job_href`, return `None` for the existing null/blank cases, reject when any of
   `"\t\r\n"` occurs in the original non-null string, and otherwise return `job_href.strip()`.
   Keep the error message fixed; do not interpolate the hostile input.
4. Replace the obsolete mismatch explanation in `_clean_job_href`'s docstring with the
   reject-before-parse invariant.
5. Re-run both focused modules; expect all tests to pass.
6. Run the existing #529 and #532 focused tests in that module by their test names; expect all to
   pass without changed assertions.
7. Commit the production change and unit proof with subject
   `fix: reject parser-deleted job href controls`.

Acceptance: all three controls fail before a client read; valid and blank links retain their prior
behavior; no stale-link or timing logic changes.

## Task 2: Prove the served-tool contract and update its documentation

Files:

- Modify `src/hmc_mcp/server_tools/jobs.py`.
- Modify `CHANGELOG.md`.
- Regenerate `docs/tools/hmc_wait_for_job.md` and any other output selected by `just tool-docs`.

Interfaces:

- Existing served entry point:
  `hmc_wait_for_job(job_id, timeout_seconds=300, poll_interval=5, job_href=None, profile=None)`.
- It delegates to `operations.jobs.wait_for_job` and exposes `JobOutcome` unchanged.
- Generated documentation consumes the served function's docstring.

Steps:

1. Simplify the served docstring: an echoed link is the cleaned caller input whose parsed path was
   validated and requested; host, query, and fragment remain unattested. Remove the obsolete claim
   that TAB/CR/LF can survive unchecked.
2. Add an `[Unreleased]` changelog bullet stating that `hmc_get_job` and `hmc_wait_for_job` reject
   TAB/CR/LF-bearing links before any HMC read.
3. Run `just tool-docs`, then `just tool-docs-check` and `just doc-freshness`; expect generated
   output to match and both checks to pass.
4. Run the focused app module; expect all tests to pass, including the valid exact-echo case.
5. Commit the served contract, changelog, and generated docs with subject
   `docs: describe validated job href echo`.

Acceptance: the served tool rejects all three parser-deleted controls, its documentation states the
narrow validation boundary, and generated docs are fresh.

## Task 3: Whole-change verification

Files: no planned source changes; any evidence-backed correction stays within the frozen surface.

Interfaces: Tasks 1 and 2 together satisfy ADR 0093's #537 amendment.

Steps:

1. Run `git diff --check`; expect exit 0.
2. Run `just verify`; expect static checks, tests with exact coverage, smoke, build, artifact
   verification, and CLI group loading to pass.
3. Run `UV_NO_SYNC=1 uv run --no-sync prek run --all-files`; expect every hook to pass.
4. Inspect the merge-base diff and confirm no behavior in `_select_persisted_job_href`, `_read_job`,
   or the wait scheduler changed.

Acceptance: both required guardrails are green and the diff remains within the frozen surface.
Rollback is ordinary `git revert` of the branch commits; no data migration or external cleanup is
required.
