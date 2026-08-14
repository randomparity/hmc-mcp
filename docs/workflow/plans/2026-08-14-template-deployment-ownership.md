# Template deployment ownership implementation plan

**Goal:** Implement issue #135 so a successfully awaited template deployment stamps the
one safely identifiable new LPAR and reports every inconclusive or failed attempt without
changing deployment success.

**Architecture:** `operations_templates` captures and compares system-scoped LPAR
snapshots around the existing deployment job. It delegates the actual best-effort stamp
to the ownership operation already used by direct creation. UUID comparison is strict:
one malformed entry invalidates a snapshot, and only one new UUID authorizes stamping.

**Tech stack:** Python 3.12+, async `HMCClient`, pytest/pytest-asyncio, Ruff, ty, prek.

## Global constraints

- Python remains `>=3.12`; no new dependency is introduced.
- No tool inputs, token format, hard enforcement, persistence, dependency, job behavior,
  or other create workflow changes are included.
- Existing validation, submission, and polling exceptions continue to raise without a
  deployment result.
- Snapshot and stamp failures are best-effort warnings and never reverse a successful
  deployment.
- Stamp only after `wait=True`, exact job status `COMPLETED`, two wholly valid snapshots,
  and exactly one new UUID.
- Every normally returned result has `job`, `ownership_stamped`, and `warnings`.
- Guardrails are `just verify` and the separately CI-gated
  `uv run prek run --all-files`.

## File map

- `tests/unit/test_template_ownership.py` — pure snapshot-selection and async orchestration
  tests, including call order and degraded dependencies.
- `tests/app/test_template_tools.py` — public MCP result-shape and non-wait regression tests.
- `src/hmc_mcp/operations_lpar.py` — expose the existing shared stamp operation.
- `src/hmc_mcp/operations_templates.py` — list-diff inference and deploy orchestration.
- `src/hmc_mcp/server_templates.py` — public behavior documentation.
- `src/hmc_mcp/_app.py` — MCP-wide ownership instructions.
- `docs/adr/0011-multi-agent-lpar-ownership.md` — replace the now-resolved known-gap text.

## Task 1: Prove snapshot inference and orchestration behavior

**Files:** create `tests/unit/test_template_ownership.py`; modify
`tests/app/test_template_tools.py`.

**Interfaces**

- Tests consume
  `deploy_partition_template(hmc, draft_template_uuid, target_system_name_or_uuid, *,
  wait, timeout_seconds, poll_interval) -> dict[str, Any]`.
- Tests require a pure helper
  `_new_lpar_from_snapshots(before: list[dict[str, Any]], after:
  list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]`.
- Later implementation must call
  `stamp_created_lpar_ownership(hmc, system_uuid, system_fallback, created_lpar)
  -> tuple[bool | None, list[str]]`.

1. Add pure-helper tests with entries shaped as
   `{"UUID": "lpar-1", "Resource": {"PartitionName": "aix1"}}`. Assert one new UUID
   returns that entry and no reason; zero returns no entry and a no-new-LPAR reason;
   two returns no entry and a multiple-candidate reason; any blank, non-string, or absent
   UUID in either snapshot invalidates the whole comparison. Include a post snapshot with
   one valid new entry plus one malformed entry and assert no target is returned.
2. Run
   `uv run pytest -q --no-cov tests/unit/test_template_ownership.py`; expect collection or
   import failure because `_new_lpar_from_snapshots` does not exist. This is the required
   red proof.
3. Add async orchestration tests using an `AsyncMock` client and monkeypatched
   `resolve_system_uuid`, `wait_for_submitted_job`, and
   `stamp_created_lpar_ownership`. Append labels to an `events` list from each side effect
   and assert the success order is exactly `baseline`, `submit`, `wait`, `post`, `stamp`.
   Assert the stamp receives the new entry's `PartitionName`, the resolved UUID, and the
   original system selector.
4. Add cases asserting stamp return `(True, [])`, `(False, [warning])`, and no-attempt
   `None` paths. Cover zero/multiple candidates, malformed mixed snapshots, baseline
   `HMCError`, post-list `HMCError`, a returned non-`COMPLETED` job, and `wait=False`.
   Assert non-completed and non-wait paths never post-list or stamp.
5. Update the app tests so non-wait results expect
   `{"job", "ownership_stamped", "warnings"}` and `ownership_stamped is None`. Replace
   the obsolete completed/manual-advisory test with a public success case that supplies
   before/after system-scoped feeds and patches the shared stamp operation.
6. Run
   `uv run pytest -q --no-cov tests/unit/test_template_ownership.py tests/app/test_template_tools.py`;
   expect failures in result shape, missing list calls, and no stamp call. Confirm the
   tests bite by preserving this output before implementation.

**Acceptance criteria:** Every frozen edge has a failing test; the ordering assertion would
fail if baseline capture moved after submission; malformed-plus-valid input cannot select a
target.

## Task 2: Implement conservative inference and shared stamping

**Files:** modify `src/hmc_mcp/operations_lpar.py` and
`src/hmc_mcp/operations_templates.py`; test the files from Task 1.

**Interfaces**

- Rename `_stamp_ownership` to
  `stamp_created_lpar_ownership(hmc: HMCClient, system_uuid: str,
  system_fallback: str, created_lpar: dict[str, Any])
  -> tuple[bool | None, list[str]]`; `create_and_stamp_lpar` and template deployment both
  consume it.
- `_new_lpar_from_snapshots` returns `(entry, None)` only for exactly one new UUID;
  otherwise `(None, reason)`.
- `deploy_partition_template` keeps its existing signature and adds
  `ownership_stamped` to normally returned dictionaries.

1. Rename the existing helper in `operations_lpar.py`, add its concrete `HMCClient` type,
   and update `create_and_stamp_lpar` to call the renamed function. Do not change its
   system-name resolution, SSH call, or warning contract.
2. In `operations_templates.py`, import `logging` and the shared stamp operation, define a
   module logger, and replace the unconditional manual-warning constant with reason-specific
   constants for non-wait, non-completed job, unavailable baseline/post snapshot, malformed
   snapshot, zero candidate, and multiple candidates.
3. Implement `_snapshot_uuids(entries)` so it returns a UUID set only when every entry's
   top-level `UUID` is a non-empty string. Implement `_new_lpar_from_snapshots` using those
   sets and a UUID-to-entry map. Return no candidate unless the difference has cardinality
   one and maps to exactly one post entry.
4. Refactor `deploy_partition_template` in this exact order: validate and resolve; if
   waiting, try the baseline list and retain failure as state; submit; wait; initialize
   `ownership_stamped=None`; short-circuit non-wait and non-completed normal results with
   warnings; on completed jobs require a usable baseline, try the post list, infer a sole
   candidate, then await `stamp_created_lpar_ownership`. Catch only snapshot `HMCError`s;
   log operation context without raw response bodies.
5. Return `{"job": selected_job, "ownership_stamped": ownership_stamped,
   "warnings": warnings}` on every normal path. Do not catch errors from timing validation,
   system resolution, submission, or `wait_for_submitted_job`.
6. Run the focused command from Task 1; expect all focused tests green. Temporarily change
   the cardinality guard from `len(new_uuids) == 1` to accepting multiple candidates, run
   the multiple-candidate test and confirm it fails, then restore the guard and rerun green.
7. Run `uv run ruff check src/hmc_mcp/operations_lpar.py
   src/hmc_mcp/operations_templates.py tests/unit/test_template_ownership.py
   tests/app/test_template_tools.py` and `uv run ty check`; expect zero warnings/errors.
8. Commit the code and tests with `feat: stamp template-deployed LPAR ownership`.

**Acceptance criteria:** One unambiguous completed deployment stamps through the existing
operation; every unsafe/degraded path preserves job success and explains non-attempt;
existing job exceptions and direct-create stamping remain unchanged.

## Task 3: Align ownership-facing documentation and verify the branch

**Files:** modify `src/hmc_mcp/server_templates.py`, `src/hmc_mcp/_app.py`, and
`docs/adr/0011-multi-agent-lpar-ownership.md`; test relevant app and smoke suites.

**Interfaces**

- The tool docstring and MCP instructions describe the implemented result contract; they
  introduce no callable API.
- ADR 0011 links the resolved gap to issue #135 and ADR 0014 while retaining its original
  decision and history.

1. Replace the tool docstring's manual-only claim with: automatic ownership inference is
   attempted only for `wait=True` after `COMPLETED`, and ambiguous or failed inference is
   reported in `warnings` with `ownership_stamped=None`.
2. Replace `_app.py`'s manual-only instructions with the same condition. Keep guidance to
   list and stamp manually when the result reports no attempt.
3. Replace ADR 0011's known-gap consequence with a resolved-gap note linking issue #135 and
   ADR 0014. Do not alter its accepted decision or unrelated text.
4. Run
   `uv run pytest -q --no-cov tests/unit/test_template_ownership.py tests/app/test_template_tools.py tests/app/test_capabilities.py tests/scripts/test_smoke_mcp.py`;
   expect all selected tests green.
5. Run `just verify` bare; expect all static checks, the full pytest suite, MCP handshake,
   and CLI group imports green. If collection fails, run
   `uv run python scripts/smoke_mcp.py` explicitly before diagnosing further.
6. Run `uv run prek run --all-files` bare; expect every hook green.
7. Review `git diff main...HEAD` for names, complexity, warning wording, accidental public
   surface, and generated artifacts. Commit documentation separately as
   `docs: document template ownership stamping` if it is not already part of the task-2
   behavior commit.

**Acceptance criteria:** No statement claims template deployment is always manual; all local
and CI-equivalent guardrails pass; branch diff stays within the frozen surface.

## Rollback and cleanup

The change has no persisted local data or migration. A git revert restores manual-only
template ownership behavior; remotely deployed LPARs already stamped remain valid ADR 0011
resources and require no cleanup. Review artifacts live only under `/tmp` and are removed
after the quest handoff.
