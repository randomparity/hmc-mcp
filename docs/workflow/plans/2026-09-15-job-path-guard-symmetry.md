# Plan: guard the job request path wherever it is built

**Goal.** `HMCClient.get_job_entry` and `HMCClient.delete_job` build one job path
expression and refuse it the same way, and that refusal no longer admits `?` or
`#` in the identifier segment.

**Architecture.** `src/hmc_mcp/client/core.py` holds the pattern `_JOB_PATH` and
the guard `_reject_non_job_path(path)`, which raises `HMCError` when the path does
not address a job; two client methods call it. This plan tightens the pattern,
adds an argument-name parameter to the guard's message, and makes `get_job_entry`
call the guard on the finished path the way `delete_job` already does. One task;
nothing is created, moved or removed; no caller migrates.

**Tech stack.** Python 3.11+, httpx, pytest + pytest-asyncio + respx, `just`.

**Global Constraints.**

- Design record: [ADR 0149](../../adr/0149-one-job-path-expression-guarded-once.md);
  spec: [job path guard symmetry](../specs/2026-09-15-job-path-guard-symmetry-design.md).
- Edit only `src/hmc_mcp/client/core.py` and `tests/unit/test_client.py`.
  `tests/unit/test_request_path_safety.py` and
  `tests/unit/test_operations_jobs_disappearance.py` must keep passing
  **unedited**: the first calls `_reject_non_job_path(path)` with one argument and
  matches `does not address a job`, the second matches `job_href refused`.
- Guardrails: `just verify` and `uv run --no-sync prek run --all-files`, both bare,
  both exit 0. A focused `pytest -k` run needs `--no-cov` to report a meaningful
  exit status; the 90.5% `fail-under` gate otherwise exits 1 on a green selection.
- No deferrals are carried into this plan.

Expected implementation size: 55-75 changed lines (S) — ~14 changed lines in
`src/hmc_mcp/client/core.py` and ~50 added lines in `tests/unit/test_client.py`.

## Task 1 — one guarded job path expression

**Interfaces.** Consumes nothing from an earlier task. The only signature change
is `_reject_non_job_path(path: str, argument: str = "job_href") -> None`;
`get_job_entry` and `delete_job` keep `(self, job_id: str, *, job_href: str | None
= None)`.

**Verification.** The green command for the first three entries is
`uv run --no-sync pytest tests/unit/test_client.py -k job_path --no-cov`, expecting
`passed` and exit 0.

- *A `job_id` that leaves the job path class, or carries `?`/`#`, is refused by
  both methods before any request.* Mode: `focused-test` — new
  `test_a_job_id_that_leaves_the_job_path_is_refused`, parametrized over
  `get_job_entry` and `delete_job` and over `""`, `a/b`, `a%2Fb`, `j?x=1`, `j#f`,
  `j%3Fx=1`, `j%23f`. Expected red: `Failed: DID NOT RAISE <class
  'hmc_mcp.errors.HMCError'>` for `get_job_entry` on every value and for
  `delete_job` on the `?`/`#` values.
- *The refusal names the argument the path was built from.* Mode: `focused-test` —
  new `test_a_non_job_href_is_refused_naming_the_href` asserts `^job_href refused`
  and the case above asserts `^job_id refused`. Expected red: the `job_id` match
  fails with `DID NOT RAISE` or `Regex pattern did not match`.
- *A `job_href` carrying a query still reaches the wire at its path.* Mode:
  `focused-test` — new `test_a_job_href_query_is_dropped_not_refused` asserts the
  route for `/rest/api/uom/jobs/j` was called. Expected red: none; it passes before
  and after, pinning that the tightening is `job_id`-only.
- *Every job path accepted today still reaches the wire.* Mode: `focused-test` —
  the existing `test_get_job_uses_href_when_provided`,
  `test_get_job_falls_back_to_global_path_when_no_href`, `test_delete_job_*` and
  `test_wait_for_job_*` cases in `tests/unit/test_client.py`, plus
  `test_a_job_link_is_accepted` in `tests/unit/test_request_path_safety.py`.
  Expected red: none; these are the regression pins, and a `_JOB_PATH` tightened
  past the accepted shapes turns them red. Green: the step 7 command.
- *ADR 0149 is a well-formed record.* Mode: `focused-test` — `just adr-numbering`,
  exit 0 with no error output. Expected red: before the file exists, nothing runs.

**Steps.**

1. In `tests/unit/test_client.py`, after `test_delete_job_propagates_http_error`,
   add the three cases above. Each registers
   `sent = mock_hmc.route(method__in=("GET", "DELETE")).mock(...)` before entering
   `async with HMCClient(make_config()) as hmc:`; the two refusal cases assert
   `not sent.called` after the `pytest.raises` block.
2. Run the green command. Expect the failures named in Verification.
3. In `src/hmc_mcp/client/core.py`, replace `_JOB_PATH`'s character classes:

   ```python
   _JOB_PATH = re.compile(r"^(?:/[^/?#]+)*/(?:Job|jobs)/[^/?#]+$")
   ```

4. Give `_reject_non_job_path` the parameter and the message:

   ```python
   def _reject_non_job_path(path: str, argument: str = "job_href") -> None:
       raise HMCError(
           f"{argument} refused: it does not address a job resource. Pass a "
           "job identifier, or the SELF link returned when the job was submitted."
       )
   ```

   Extend its docstring to say the guard covers the `job_id` branch too, why the
   identifier segment excludes `?` and `#`, and why the match stays on the decoded
   form only. Keep the existing ADR 0039 residual paragraph.
5. Replace `get_job_entry`'s five-line `if job_href:` block, and `delete_job`'s two
   guard lines, with the identical pair, and name the refusal in one added
   sentence of `get_job_entry`'s docstring:

   ```python
   path = urlparse(job_href).path if job_href else f"/rest/api/uom/jobs/{job_id}"
   _reject_non_job_path(path, "job_href" if job_href else "job_id")
   ```

6. Re-run the green command. Expect `passed`, exit 0.
7. Run `uv run --no-sync pytest tests/unit/test_client.py
   tests/unit/test_request_path_safety.py
   tests/unit/test_operations_jobs_disappearance.py
   tests/unit/test_operations_jobs.py --no-cov`. Expect `passed`, exit 0.
8. Run `just verify`, then `uv run --no-sync prek run --all-files`. Both bare, both
   exit 0.
9. Commit as `fix(client): guard the job request path on both branches`.

**Acceptance criteria.** All five Success criteria of the spec hold;
`src/hmc_mcp/client/core.py` and `tests/unit/test_client.py` are the only source
files in the diff; both guardrail commands exit 0.

**Rollback.** Revert the commit; nothing persists outside the working tree.
