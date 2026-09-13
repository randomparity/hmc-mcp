# V10R3 storage write safety implementation

Make the audited HMC storage write paths report possible post-dispatch effects,
preserve MiB user input while emitting GiB protocol values, and correct
VolumeGroup inventory units. The client owns HMC transport/reconciliation;
documents own XML; operations own public result projection; presentation layers
only render those result types.

## Global Constraints

- Base branch: `main`; branch: `feat/v10r3-storage-safety-779`.
- No new dependencies or public compatibility aliases.
- Preserve `capacity_mib` inputs; require positive multiples of 1024 MiB.
- The only header fallback is the audited VolumeGroup/VirtualIOServer typed-UOM
  storage write set, on 406 only. Never retry a 5xx.
- Do not modify `docs/recipes/lpar-iso-install.md`; PR #777 owns it.
- Run `just setup` in this worktree before test commands and use `uv run --no-sync`.

Expected implementation size: 450–750 changed lines (L) — derived from the client,
document, operation, presentation, tests, and generated-document file map.

## Task 1 — encode protocol units and inventory truth

Files: `src/hmc_mcp/documents/storage.py`,
`src/hmc_mcp/operations/storage/resources.py`, `tests/storage/test_storage.py`,
`tests/storage/test_storage_results.py`.

Interfaces: `build_virtual_disk_document(disk_name: str, capacity_mib: int) -> str`
continues to accept MiB and emits integral GiB. `VolumeGroup` exposes
`capacity_gib: int | float | None`, `free_space_gib: int | float | None`, and a
diagnostic field or equivalent bounded result diagnostic.

Verification:

- Mode: focused-test. Contract: 1024 MiB emits `DiskCapacity` 1; invalid values
  fail before XML. Red observation: existing test expects raw MiB XML. Green:
  `uv run --no-sync pytest tests/storage/test_storage.py tests/storage/test_storage_results.py -q`.
- Mode: focused-test. Contract: GiB fields replace MiB fields and impossible free
  space is null/diagnostic. Red observation: current projection exposes MiB keys.
  Green: same command.

Steps:

1. Add a local conversion guard before the envelope is built:

   ```python
   if capacity_mib <= 0 or capacity_mib % 1024:
       raise ValueError("capacity_mib must be a positive multiple of 1024")
   capacity_gib = capacity_mib // 1024
   ```

2. Use `capacity_gib` for `DiskCapacity`; rename the VolumeGroup fields and make
   `_volume_group` null the free-space value when both numeric values are present
   and `free_space_gib > capacity_gib`.
3. Write red tests for conversion, invalid inputs, fractional GiB inventory, and
   impossible free space; implement until the focused command passes.

Acceptance: no conversion rounds a request; all exposed VolumeGroup fields state GiB.

## Task 2 — reconcile the bounded mutation set

Files: `src/hmc_mcp/client/core.py`, `src/hmc_mcp/client/client_storage.py`,
`tests/unit/test_transport_contracts.py`, `tests/unit/test_client.py`,
`tests/unit/test_client_domain_mixins.py`.

Interfaces: add an explicit private typed-UOM fallback argument to `_post`/`_put`;
add a private `StorageClient` helper that receives snapshot, dispatch, and readback
callables and returns the dispatch result or raises `HMCError` chained from a 5xx.

Verification:

- Mode: focused-test. Contract: the selected calls send typed headers first and
  retry exactly once with generic UOM `Accept` only after 406. Red observation:
  current helper sends one request. Green:
  `uv run --no-sync pytest tests/unit/test_transport_contracts.py tests/unit/test_client.py tests/unit/test_client_domain_mixins.py -q`.
- Mode: focused-test. Contract: each audited 5xx performs one readback and says
  possible side effect/do not retry, while no 5xx retries. Red observation:
  current errors omit readback. Green: same command.

Steps:

1. Add `fallback_to_generic_uom_on_406: bool = False` to `_post` and `_put`.
   Rebuild only `Accept` for the retry; preserve `Content-Type`, body, path,
   schema-version choice, and UUID validation.
2. Audit each storage POST/PUT call. Pass the flag only to the documented
   VolumeGroup/VirtualIOServer typed-UOM set and add tests proving job/web calls
   do not opt in.
3. Add a reconciliation helper around the VolumeGroup mutation call sites. Capture
   the named group where available, otherwise the VIOS group list; on status 500–599,
   read back once and raise a chained actionable error containing normalized before/
   after state or the readback failure.
4. Add per-shape 5xx tests, then make their focused command green.

Acceptance: no code path retries a 5xx or attempts rollback; tests pin the bounded set.

## Task 3 — migrate consumers and public documentation

Files: `src/hmc_mcp/cli_commands/storage/resources.py`,
`src/hmc_mcp/server_tools/storage/resources.py`,
`src/hmc_mcp/operations/lpar/provision.py`, `tests/storage/test_storage_tools.py`,
`tests/app/test_cli_commands.py`, `tests/lpar/test_attach_disk.py`,
`docs/operations.md`, `CHANGELOG.md`, generated `docs/tools/storage.md` if changed.

Interfaces: create/attach tool and CLI parameters remain `capacity_mib`; list-vgs
JSON and table fields/labels use `capacity_gib` and `free_space_gib`.

Verification:

- Mode: focused-test. Contract: storage tools and CLI expose GiB inventory keys
  while create/attach retain MiB argument names. Red observation: current tests
  expect MiB inventory keys. Green:
  `uv run --no-sync pytest tests/storage/test_storage_tools.py tests/app/test_cli_commands.py tests/lpar/test_attach_disk.py -q`.
- Mode: focused-test. Contract: served schema still names `capacity_mib` for
  create/attach. Red observation: a renamed parameter breaks capability tests.
  Green: `uv run --no-sync pytest tests/app/test_capabilities.py -q`.

Steps:

1. Replace list-vgs presentation references and headings with the GiB result
   fields; leave create/attach input and attachment-result `capacity_mib` intact.
2. Update direct tests and fixtures for converted XML and result keys.
3. Update `docs/operations.md` and the unreleased changelog with the conversion,
   replacement result keys, and no-retry safety behavior. Run `just tool-docs`
   only if tool registry output changes.

Acceptance: no stale VolumeGroup MiB label remains; the excluded recipe is untouched.

## Task 4 — verify the delivered branch

Files: all task files plus generated documentation if regeneration changes it.

Interfaces: no new interface; this task proves the prior contracts together.

Verification:

- Mode: focused-test. Contract: every changed behavior above is covered by the
  preceding focused suites. Green: their listed commands pass.
- Mode: task-test-not-applicable. Surface: live HMC behavior. Reason: completion
  criteria reserve live re-validation for an approved disposable environment.

Steps:

1. Run the focused suites after the final migration.
2. Run `just static`, `just test`, `just smoke`, and `just verify`; regenerate
   tool docs before the static check if Task 3 changed the registry output.
3. Run `uv run --no-sync prek run --all-files`; inspect the merge-base diff and
   commit the implementation in logical conventional commits.

Acceptance: repository guardrails are green and the branch contains no recipe edit.
