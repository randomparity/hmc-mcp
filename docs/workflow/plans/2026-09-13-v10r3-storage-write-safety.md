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
`capacity_gib: int | float | None`, `free_space_gib: int | float | None`, and
`free_space_diagnostic: str | None`. Its sole new diagnostic value is
`"free_space_exceeds_capacity"`.

Verification:

- Mode: focused-test. Contract: 1024 MiB emits `DiskCapacity` 1; invalid values
  fail before XML. Red observation: existing test expects raw MiB XML. Green:
  `uv run --no-sync pytest tests/storage/test_storage.py tests/storage/test_storage_results.py -q`.
- Mode: focused-test. Contract: GiB fields replace MiB fields and impossible free
  space is null with `free_space_exceeds_capacity`. Red observation: current projection
  exposes MiB keys and no diagnostic. Green: same command.

Steps:

1. Add a local conversion guard before the envelope is built:

   ```python
   if capacity_mib <= 0 or capacity_mib % 1024:
       raise ValueError("capacity_mib must be a positive multiple of 1024")
   capacity_gib = capacity_mib // 1024
   ```

2. Use `capacity_gib` for `DiskCapacity`; rename the VolumeGroup fields and make
   `_volume_group` null the free-space value and set
   `free_space_diagnostic="free_space_exceeds_capacity"` when both numeric values are
   present and `free_space_gib > capacity_gib`.
3. Write red tests for conversion, invalid inputs, fractional GiB inventory, and
   impossible free space; implement until the focused command passes.

Acceptance: no conversion rounds a request; VolumeGroup field names state GiB and the
sole impossible-free-space diagnostic is serialized with the null value.

## Task 2 — reconcile the bounded mutation set

Files: `src/hmc_mcp/client/core.py`, `src/hmc_mcp/client/client_storage.py`,
`tests/unit/test_transport_contracts.py`, `tests/unit/test_client.py`,
`tests/unit/test_client_domain_mixins.py`.

Interfaces: add an explicit private typed-UOM fallback argument to `_post`/`_put`;
add a private `StorageClient` helper that receives snapshot, dispatch, and readback
callables and returns the dispatch result or raises `HMCError` chained from a 5xx. The
following bounded matrix is the complete task inventory:

| Method/path | Snapshot and readback | 406 behavior | 5xx test |
| --- | --- | --- | --- |
| `create_volume_group` | `list_volume_groups` | typed → generic UOM once | list before/after |
| `create_virtual_disk`, `delete_virtual_disk`, `_broker_iso_import` | `get_volume_group` | typed → generic UOM once | group before/after |
| `create_media_repository`, `create_optical_media`, `delete_media_repository`, `delete_optical_media` via `_post_vg_xml` | raw VolumeGroup GET | already generic `Accept`; no retry | raw group before/after |
| `map_storage_to_lpar`, `create_optical_mapping` | VIOS mapping inventory | typed → generic UOM once | mapping before/after |
| `delete_storage_mapping` | its existing VIOS document GET | already generic `Accept`; no retry | VIOS document before/after |

Verification:

- Mode: focused-test. Contract: the selected calls send typed headers first and
  retry exactly once with generic UOM `Accept` only after 406. Red observation:
  current helper sends one request. Green:
  `uv run --no-sync pytest tests/unit/test_transport_contracts.py tests/unit/test_client.py tests/unit/test_client_domain_mixins.py -q`.
- Mode: focused-test. Contract: each matrix row's 5xx performs one readback and says
  possible side effect/do not retry, while no 5xx retries. Red observation:
  current errors omit readback. Green: same command.

Steps:

1. Add `fallback_to_generic_uom_on_406: bool = False` to `_post` and `_put`.
   Rebuild only `Accept` for the retry; preserve `Content-Type`, body, path,
   schema-version choice, and UUID validation.
2. Apply the 406 fallback exactly to the typed rows in the matrix. Assert that
   `_post_vg_xml`, `delete_storage_mapping`, storage-broker, job, and web paths do not
   opt in.
3. Route every matrix row, including `_post_vg_xml` and `delete_storage_mapping` direct
   requests, through reconciliation. On status 500–599, read back exactly once and
   raise a chained actionable error containing normalized before/after state or the
   readback failure.
4. Add a 5xx request-count/readback test for each matrix row, then make the focused
   command green.

Acceptance: no matrix row retries a 5xx or attempts rollback; tests pin the complete
bounded inventory and direct-request paths.

## Task 3 — migrate consumers and public documentation

Files: `src/hmc_mcp/cli_commands/storage/resources.py`,
`src/hmc_mcp/server_tools/storage/resources.py`,
`src/hmc_mcp/operations/lpar/provision.py`, `tests/storage/test_storage_tools.py`,
`tests/app/test_cli_commands.py`, `tests/lpar/test_attach_disk.py`,
`docs/operations.md`, `CHANGELOG.md`, generated `docs/tools/storage.md` if changed.

Interfaces: create/attach tool and CLI parameters remain `capacity_mib`; list-vgs
JSON and table fields/labels use `capacity_gib` and `free_space_gib`; impossible
inventory also serializes and renders `free_space_diagnostic`.

Verification:

- Mode: focused-test. Contract: storage tools and CLI expose GiB inventory keys
  while create/attach retain MiB argument names. Red observation: current tests
  expect MiB inventory keys. Green:
  `uv run --no-sync pytest tests/storage/test_storage_tools.py tests/app/test_cli_commands.py tests/lpar/test_attach_disk.py -q`.
- Mode: focused-test. Contract: served schema still names `capacity_mib` for
  create/attach. Red observation: a renamed parameter breaks capability tests.
  Green: `uv run --no-sync pytest tests/app/test_capabilities.py -q`.

Steps:

1. Replace list-vgs presentation references and headings with the GiB result fields
   and render `free_space_diagnostic`; leave create/attach input and attachment-result
   `capacity_mib` intact.
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
