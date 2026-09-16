# Plan: guard the job request path wherever it is built

**Goal.** `HMCClient.get_job_entry` and `HMCClient.delete_job` build one job path
expression and refuse it the same way, and that refusal inspects the path httpx
will actually send.

**Architecture.** `src/hmc_mcp/client/core.py` holds the pattern `_JOB_PATH` and
the guard `_reject_non_job_path(path)`, which raises `HMCError` when the path does
not address a job; two client methods call it. This plan tightens the identifier
segment, makes the guard require a match on the raw path as well as the decoded
one, names the refused argument in its message, and makes `get_job_entry` call it
on the finished path the way `delete_job` does. One task; nothing is created,
moved or removed; no caller migrates.

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

Expected implementation size: 55-75 changed lines (S) — ~18 changed lines in
`src/hmc_mcp/client/core.py` and ~45 added lines in `tests/unit/test_client.py`.

## Task 1 — one guarded job path expression

**Interfaces.** Consumes nothing from an earlier task. The only signature change
is `_reject_non_job_path(path: str, argument: str = "job_href") -> None`;
`get_job_entry` and `delete_job` keep `(self, job_id: str, *, job_href: str | None
= None)`.

**Verification.** The green command for the first three entries is
`uv run --no-sync pytest tests/unit/test_client.py -k "job_path or job_href" --no-cov`,
expecting `passed` and exit 0.

- *A `job_id` that leaves the job path class, or carries `?`/`#`, is refused by
  both methods before any request.* Mode: `focused-test` — new
  `test_a_job_id_that_leaves_the_job_path_is_refused`, parametrized over
  `get_job_entry` and `delete_job` and over `""`, `a/b`, `a%2Fb`, `j?x=1`, `j#f`,
  `j%3Fx=1`, `j%23f`. Expected red: `Failed: DID NOT RAISE <class
  'hmc_mcp.errors.HMCError'>` for `get_job_entry` on every value and for
  `delete_job` on the `?`/`#` values. The same case asserts `match=r"^job_id
  refused"`, so a guard that does not thread the argument through fails with
  `Regex pattern did not match`.
- *A `job_href` that is a job path only after decoding is refused, and the
  refusal names `job_href`.* Mode: `focused-test` — new
  `test_a_job_href_that_only_decodes_to_a_job_path_is_refused`, over
  `get_job_entry` and `delete_job` with
  `job_href="/rest/api/uom/HmcUser/root%2FJob%2Fx"`, asserting `^job_href
  refused`. Expected red: `DID NOT RAISE`, because `_JOB_PATH` matches the
  decoded form today.
- *Every job path accepted today still reaches the wire.* Mode: `focused-test` —
  the existing `test_get_job_*`, `test_delete_job_*` and `test_wait_for_job_*`
  cases in `tests/unit/test_client.py` plus `test_a_job_link_is_accepted` in
  `tests/unit/test_request_path_safety.py`. Expected red: none; they are the
  regression pins, and a `_JOB_PATH` tightened past the accepted shapes turns
  them red. Green: the step 7 command.
- *ADR 0149 is a well-formed record.* Mode: `focused-test` — `just adr-numbering`,
  exit 0 with no error output.

**Steps.**

1. In `tests/unit/test_client.py`, after `test_delete_job_propagates_http_error`,
   add the two new cases above. Each registers
   `sent = mock_hmc.route(method__in=("GET", "DELETE")).mock(...)` before entering
   `async with HMCClient(make_config()) as hmc:`, and asserts `not sent.called`
   after the `pytest.raises` block.
2. Run the green command. Expect the failures named in Verification.
3. In `src/hmc_mcp/client/core.py`, tighten `_JOB_PATH`'s identifier segment only:

   ```python
   _JOB_PATH = re.compile(r"^(?:/[^/]+)*/(?:Job|jobs)/[^/?#]+$")
   ```

4. Make `_reject_non_job_path` require both forms and name the argument:

   ```python
   def _reject_non_job_path(path: str, argument: str = "job_href") -> None:
       for form in (path, unquote(path)):
           if not _JOB_PATH.match(form):
               raise HMCError(
                   f"{argument} refused: it does not address a job resource. "
                   "Pass the UUID or JobID as job_id, or the SELF link returned "
                   "when the job was submitted as job_href."
               )
   ```

   Keep the docstring's existing ADR 0039 residual paragraph, and extend it to
   say the guard covers the `job_id` branch too, that a decode can manufacture a
   `/Job/{id}` tail the raw path does not have, and why only the identifier
   segment excludes `?` and `#`.
5. Replace `get_job_entry`'s five-line `if job_href:` block, and `delete_job`'s two
   guard lines, with the identical pair, and name the refusal in one added
   sentence of `get_job_entry`'s docstring:

   ```python
   path = urlparse(job_href).path if job_href else f"/rest/api/uom/jobs/{job_id}"
   _reject_non_job_path(path, "job_href" if job_href else "job_id")
   ```

6. Re-run the green command. Expect `passed`, exit 0.
7. Run `uv run --no-sync pytest tests/unit/test_client.py tests/unit/test_request_path_safety.py tests/unit/test_operations_jobs_disappearance.py tests/unit/test_operations_jobs.py --no-cov`.
   Expect `passed`, exit 0.
8. Run `just verify`, then `uv run --no-sync prek run --all-files`. Both bare, both
   exit 0.
9. Commit as `fix(client): guard the job request path on both branches`.

**Acceptance criteria.** All six Success criteria of the spec hold;
`src/hmc_mcp/client/core.py` and `tests/unit/test_client.py` are the only source
files in the diff; both guardrail commands exit 0.

**Rollback.** Revert the commit; nothing persists outside the working tree.
