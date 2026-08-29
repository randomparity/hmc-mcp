# ADR 0005: hmc_provision_lpar Composite Workflow Tool

## Status

Accepted; module ownership superseded by ADR 0013

## Context

Provisioning a new LPAR currently requires six separate MCP tool calls in a
precise order: `hmc_create_lpar`, `hmc_add_network_adapter`,
`hmc_add_vscsi_adapter`, `hmc_map_storage_to_lpar`, `hmc_power_on_lpar`. An
agent or operator must orchestrate this sequence manually, handle intermediate
UUIDs, and decide how to respond to a partial failure at any step. The README
documents this as a six-step example.

Issue #67 requests a single `hmc_provision_lpar` tool that executes the full
workflow with:
- precondition validation (name uniqueness, VLAN existence, volume-group
  existence) optionally as a dry run only, with no side effects;
- a per-step result list so partial failures are observable;
- no auto-rollback (by explicit design: rollback is the operator's
  responsibility when state was mutated before the failure).

The tool is untagged (state-changing but not destructive): the LPAR is created
powered off and can be deleted; none of the individual steps is irreversible in
isolation (no data destruction).

## Decision

Add `hmc_provision_lpar` in a new `server_tools/provision.py` module (following the
domain-module pattern of `server_tools/power.py`, `server_tools/storage.py`, etc.). The
tool composes the already-implemented client methods:

1. **Preconditions** (always, including dry-run):
   - `find_partition_by_name` — name uniqueness
   - `list_virtual_networks` — VLAN `port_vlan_id` exists on the system
   - `list_volume_groups` — volume group `vg_uuid` exists on the VIOS

2. **Execution steps** (skipped on dry-run):
   - `create_logical_partition` — step "create"
   - `add_network_adapter` — step "network"
   - `add_vscsi_adapter` — step "vscsi"
   - `map_storage_to_lpar` — step "storage"
   - `submit_job` (PowerOn) — step "power_on"

   Each step is attempted only if the preceding step succeeded. On step
   failure, remaining steps are skipped and recorded as `{"status": "skipped"}`.

3. **Result shape** (matches issue specification):
   ```json
   {
     "created": true,
     "dry_run": false,
     "steps": [
       {"step": "create", "status": "ok", "result": {...}},
       {"step": "network", "status": "ok", "result": {...}},
       ...
     ],
     "warnings": []
   }
   ```

`lpars provision` CLI command in `cli_commands/lpars.py` wraps the tool (thin adapter,
same arguments, `--dry-run` flag).

## Capability annotation

Untagged: the tool creates resources (not purely read-only) and does not
destroy them (not destructive). The default treatment (state-changing lifecycle
operation) is correct.

## Consequences

- One new source file: `src/hmc_mcp/server_tools/provision.py`.
- `server.py` gains one re-export: `hmc_provision_lpar`.
- `_app.py` READ_ONLY_TOOLS and DESTRUCTIVE_TOOLS sets are **not** modified
  (the tool is intentionally untagged).
- `cli_commands/lpars.py` gains the `lpars provision` sub-command.
- Tests in `tests/lpar/test_provision_tool.py` cover the full workflow, dry-run
  path, precondition failures, and partial step failures.
- README: new row in the **Mutating / lifecycle** table; six-step example
  replaced with a one-call `hmc_provision_lpar` usage note.

## Considered & Rejected

**Keep the six-step sequence as documentation only.** Does not reduce the
burden on agents and operators who must maintain intermediate state.

**Auto-rollback on partial failure.** The issue body explicitly excludes this:
partial-failure state on a real system is complex and operator-specific; manual
rollback is the correct contract.

**Implement in `server_tools/power.py`.** That module covers power-lifecycle
operations (create/modify/power-on/off/delete). Provisioning spans adapters,
storage, and power — a new module keeps concerns separated and matches the
file-scope hint in the issue.

**Blocking issues (#56, #57, #64).** The issue lists these as upstream blockers
but all three capabilities (wait_for_job, find_partition_by_name / find_system,
precondition guard patterns) are already implemented in main. The blocks are
resolved.
