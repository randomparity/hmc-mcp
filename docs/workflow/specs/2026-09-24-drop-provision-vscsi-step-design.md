# Drop the vSCSI step from provision and attach-disk

Issue #1030. Governing decision: ADR 0169 (mapping create makes the HMC create its own
client/server adapter pair). This amends ADR 0005 §2's `vscsi` step and ADR 0062's
`adapters.vios_partition_id` nested selector; ADR 0169's Consequences records both.

## Problem

`provision_lpar` and `attach_disk_to_lpar` share `_run_storage_leg`, which runs
`create_disk` (attach-disk only), then `vscsi` (`_add_vscsi` → `HMCClient.add_vscsi_adapter`),
then `storage` (`_map_storage` → `map_storage_to_lpar`). Since ADR 0169 the mapping create
makes the HMC add its own client/server pair, so the `vscsi` step's client adapter is never
used and is left with no server adapter. Every successful run leaves one unpaired
`VirtualSCSIClientAdapter` and one used virtual slot on the LPAR.

## Design

1. `_run_storage_leg` runs `create_disk` (when a capacity is given) then `storage`. `_add_vscsi`
   is deleted. `attach_disk_to_lpar`'s step names become `["create_disk", "storage"]`;
   `_provision_step_names` drops `"vscsi"`. Dry-run and live results therefore list no
   `vscsi` step.
2. Inputs used only by the removed step are removed (operator decision, pre-release surface,
   no shim):
   - `attach_disk_to_lpar(..., vios_partition_id, vios_slot)` keyword arguments;
     `hmc_attach_disk_to_lpar`'s `vios_partition_id` and `vios_slot` parameters;
     `storage attach-disk --vios-id/--vios-slot`.
   - `ProvisionAdapters.vios_partition_id` and `.vios_slot` (the dataclass keeps
     `port_vlan_id`, still used by the network leg); `lpars provision
     --vios-partition-id/--vios-slot`; `hmc_provision_lpar`'s nested
     `("vios", "adapters.vios_partition_id")` extra target.
   Verified other uses: `rg -n "vios_partition_id|vios_slot" src/hmcpctl/operations/lpar
   src/hmcpctl/server_tools/storage/resources.py src/hmcpctl/server_tools/lpar
   src/hmcpctl/cli_commands/lpar src/hmcpctl/cli_commands/storage` shows they reach only
   `_add_vscsi` and the target-selector declarations. The mapping takes `vios_uuid`,
   `kind`, `storage_name`, `lpar_uuid`. The standalone `hmc_add_vscsi_adapter` /
   `adapters add-vscsi` keep their inputs. Removed top-level tool arguments and CLI options
   are rejected; removed nested `adapters` keys are dropped by MCP argument validation
   (stdlib-dataclass schema without `additionalProperties: false`) and have no effect.
   `hmc_provision_lpar`'s `adapters` Args line becomes "Virtual Ethernet attachment settings."
   `docs/capabilities/operations.json` stores attach-disk's `inspect.signature` string, checked
   by `just capability-inventory` with no regenerator; its row is edited to the new signature.
3. Authorization: removing `vios_partition_id` removes a `vios` selector from
   `hmc_attach_disk_to_lpar` (a top-level argument named in `tool_registry`'s selector map)
   and from `hmc_provision_lpar` (its nested extra target). `storage.vios_uuid` /
   `vios_uuid` remain the `vios` selectors. `exhaustive_targets` stays `False` on both tools:
   flipping it would let a policy `targets` table grant them, a widening this change is not
   authorized to make. The rationale comments that cite the removed argument for these two
   tools — the two decorator comments, `tool_registry.py`'s "three live tools" count, the
   `_NOT_EXHAUSTIVE` grouping and provision-selector docstring in `test_tool_security.py`, and
   `docs/mcp-server.md`'s partition-ID list — say instead that the flag is kept pending a
   separate decision (follow-up candidate).
4. Live harness: ST13 and ST14 stop sending `vios_partition_id`/`vios_slot`; ST14's
   pre-flight no longer requires them. The now-unused `dry_run_vios_slot` /
   `dry_run_vios_partition_id` settings (and `LIVE_TEST_DRY_RUN_VIOS_*` in `.env.example`)
   and ST0's `_capture_vscsi_identifiers` (it fed only those two baseline keys) are removed.
   `_decode_saved_config` drops the two removed keys from an older results document before
   comparing, mirroring the `test_user_uuid` precedent in `_decode_artifacts`, so a subset
   re-run can still restore artifacts. `artifacts.vios_partition_id` stays (diagnostic prints;
   it is part of the persisted artifact format).
   ST14 therefore needs no VIOS slot, which makes #1031's slot fallback unnecessary.
5. Docs: regenerate `docs/tools/`; `docs/cli.md`'s storage-model note says provision and
   attach-disk rely on the mapping's pair; `docs/mcp-server.md`'s count of partition-ID tools
   drops attach-disk; ADR 0169's Consequences replaces the "left to a follow-up" sentence with
   one saying #1030 removed ADR 0005's `vscsi` step and provision's nested partition-ID
   selector; CHANGELOG `[Unreleased]` records the removal under `Removed`/`Fixed`.

## Failure model

1. Actors and deployments: a local operator via `hmcpctl`; an MCP client via the server; the
   live-test runner operator on the authorized lab.
2. Invariants and assets: VIOS mappings of other partitions (untouched — only the step before
   the mapping is removed); the access-policy selector set (must only narrow); the tool/CLI
   input contract (pre-release, changed by operator decision).
3. Accepted failure classes: a caller still passing `vios_partition_id`/`vios_slot` gets an
   argument error as a top-level tool argument or CLI option, and silently has them ignored as
   nested `adapters` keys (pre-release surface, no shim, values unused, CHANGELOG says so);
   provision's reliance on the mapping-created pair is proven offline and by ADR 0169's
   evidence, not by a live provision (ST14 deletes and re-creates the authorized LPAR, which
   the live authorization does not cover); unpaired adapters left
   by earlier runs are not cleaned up (not asked; `adapters delete` exists).
4. Covered elsewhere: rollback after a failed storage step → none (approved exclusion); mapping
   delete `If-Match` → #1037; UOM write headers → #935/ADR 0178.

### Threat model

- Boundaries: tool/CLI arguments into `attach_disk_to_lpar` / `provision_lpar` (narrowed —
  two arguments fewer); target-selector extraction for the access policy (one selector fewer
  per tool). No boundary is added.
- Actor: an MCP client constrained by the server access policy.
- Control: the policy's `vios` selectors now come only from `vios_uuid`; `exhaustive_targets`
  stays `False`, so the grant rule (`targets = "all-targets"` only) is unchanged.
- Out of scope: making either tool table-grantable (follow-up candidate).

## Success

- `attach_disk_to_lpar` and `provision_lpar` never call `add_vscsi_adapter` (dry run, success,
  failure paths); their `steps` contain no `vscsi` entry.
- The removed top-level tool arguments and CLI options are rejected; `just verify` is green.
- Live: one attach-disk on the authorized LPAR adds the mapping's adapter pair and no other
  `VirtualSCSIClientAdapter`; the created disk and mapping are removed afterwards.

## Validation

- `focused-test`: `tests/lpar/test_attach_disk.py` — step order `["create_disk", "storage"]`,
  dry-run step list, `add_vscsi_adapter` never awaited. Red: current order includes `vscsi`.
- `focused-test`: `tests/lpar/test_provision_tool.py` — no `VirtualSCSIClientAdapter` route
  hit, steps without `vscsi`, storage failure skips assignments/power-on.
- `focused-test`: `tests/app/test_cli_commands.py`, `tests/app/test_application_boundaries.py`
  — CLI builds requests without the removed options; `tests/app/test_tool_security.py` —
  selector inventories without the removed selectors; `just capability-inventory`.
- `focused-test`: `tests/test_live_runner.py`, `tests/scripts/test_inventory.py` — ST13/ST14
  requests and pre-flight without slot/partition ID.
- `task-test-not-applicable`: ADR 0169, `docs/cli.md`, `docs/mcp-server.md`, CHANGELOG prose —
  no executable consumer reads these sentences.
- Generated: `just tool-docs-check`, `just doc-freshness`, `just env-vars`.
- Live: attach-disk once on the authorized LPAR via `hmcpctl storage attach-disk`, bracketed by
  preflight, read-only adapter/mapping baselines, cleanup and `live_test_recovery.py`; ST13
  (dry run, no mutation) through the runner when its context allows.
