# LPAR decommission workflow design

**Issue:** #149  
**Decision:** [ADR 0027](../../adr/0027-safe-lpar-decommission-workflow.md)  
**Branch:** `feat/decommission-lpar-149` from `main`  
**Guardrails:** `just verify`; `UV_NO_SYNC=1 uv run prek run --all-files`

## Outcome and boundaries

Add `hmc_decommission_lpar`, a public destructive MCP tool and `hmc-mcp lpars
decommission` mirror. It is one orchestrated safety workflow around existing HMC client
primitives. It does not add dependencies, migrations, rollback, live-HMC tests, storage
deletion, or an ADR-index edit.

The public inputs are `system_name_or_uuid`, `lpar_name_or_uuid`, `dry_run=False`,
`ownership_override=False`, `immediate=False`, `timeout_seconds=300`,
`poll_interval=5`, and the MCP-only optional `profile`. The system selector is required
so name resolution is system-scoped and unambiguous. The CLI requires `--system`, accepts
the same controls, asks once before non-dry-run execution unless `--yes` is supplied, and
supports `--json`.

## Architecture and data flow

Presentation-neutral orchestration lives in `operations/decommission.py`. The MCP wrapper
lives with LPAR lifecycle tools in `server_tools/lpars.py`; the CLI wrapper lives in
`cli_commands/lpars.py`. Both construct one client and call the same coroutine.

Preconditions always execute before a result is returned:

1. Resolve the managed system, list its LPAR children, and require the supplied name or
   UUID to match exactly one child. UUID selectors do not use the shared pass-through
   behavior: a UUID absent from that child collection fails before any further read.
2. Resolve the managed-system and LPAR names required by the existing ownership reader.
3. Read the description once, enforce ADR-0011, and retain the parsed owner from that
   authorizing snapshot. A valid foreign owner or malformed hmc-mcp token raises
   `PermissionError` unless `ownership_override=True`.
4. Read the LPAR resource and current `PartitionState`.
5. List `ClientNetworkAdapter`, `VirtualSCSIClientAdapter`,
   `VirtualFibreChannelClientAdapter`, and `VirtualNICDedicated` children.
6. List VIOS partitions for the selected system, fetch each VIOS storage-detail entry,
   and retain only vSCSI/vFC mappings whose client-partition identity matches the target
   LPAR UUID or numeric partition ID. If a mapping lacks enough client identity to prove a
   match, do not report it as affected; increment `unresolved_storage_mapping_count` and
   add a warning that the storage blast radius may be incomplete. If a listed VIOS lacks
   a UUID or returns no storage detail, increment `unavailable_storage_source_count` and
   emit one warning for that source without increasing the unresolved mapping count.

Each call takes a fresh inventory; a prior dry-run does not bind a later execution. The
inventory is bounded by the selected system and contains curated values: LPAR UUID,
name, state, parsed ownership holder or null, adapters as `{type, uuid}`, and storage
mappings as `{vios_uuid, type, uuid, backing_device}` when those fields exist, plus the
unresolved mapping and unavailable storage-source counts. Raw HMC entries and credentials
never enter the public result. An unavailable source is a non-fatal inventory warning, so
execution continues under the same warning rather than claiming a mapping count that the
workflow could not observe.

Adapters are ordered by type as `ClientNetworkAdapter`, `VirtualSCSIClientAdapter`,
`VirtualFibreChannelClientAdapter`, and `VirtualNICDedicated`, then by UUID within each
type.

For `dry_run=True`, the three execution steps (`power_off`, `detach_adapters`,
`delete_lpar`) have `dry_run` status and summary results; no submit, delete, modify, or
SSH write method is called. Reads, including the SSH-backed ownership description read,
are allowed and required.

For execution, the workflow repeats the ownership check after inventory and immediately
before its first mutation. An already `not activated` LPAR records `power_off` as `ok`
with `already_off=True`. Otherwise the workflow submits immediate or graceful power-off
with `wait=True`, validates the normalized terminal outcome, and treats timeout, missing
terminal status, or a failed terminal status as an error. After either the successful job
or the initially-inactive shortcut, it re-reads `PartitionState` immediately before
adapter deletion. Any value other than exactly `not activated`, or a failed state read,
marks `detach_adapters` as `error` and skips all deletion. Only then does it delete every
inventoried adapter in deterministic type/UUID order. The adapter step is `ok` only when
all deletes succeed; the first failure stops remaining adapter deletes and skips LPAR
deletion. Finally it calls the client's LPAR delete directly against the frozen UUID. Any
expected `HMCError`, `PermissionError`, or `ValueError` before mutation propagates;
expected HMC failures during execution become an `error` step and short-circuit the
remainder.

## Result contract

`DecommissionResult` follows the stable provisioning envelope:

- `resource_deleted: bool`
- `workflow_completed: bool`
- `lpar_uuid: str`
- `dry_run: bool`
- `steps: tuple[dict[str, Any], ...]`
- `warnings: tuple[str, ...]`
- `blast_radius: dict[str, Any]`

Each step contains `step`, one of `ok`, `error`, `skipped`, or `dry_run`, and an optional
curated `result`. Errors are actionable strings. There is no rollback field or hidden
retry. `workflow_completed` is true for a successful execution and for a fully evaluated
dry run; `resource_deleted` is true only after the final HMC delete succeeds.
The CLI renders this full result before returning; an incomplete non-dry execution exits
with status 1 in both human and JSON modes.

Mocked tests include sparse storage-detail records and prove they produce a non-zero
unresolved count and an incomplete-inventory warning rather than disappearing silently.
Dry-run tests cover both a UUID-less listed VIOS and a VIOS returning no storage detail;
an execution test proves the latter retains its warning and source count while power-off,
adapter deletion, and LPAR deletion continue in order to success.

## Threat model

The new boundaries are an MCP/CLI caller selecting a destructive target and requesting an
ownership override. The authenticated local operator or MCP gateway is trusted to approve
the call; partition names, UUIDs, parsed HMC records, and ownership descriptions are not
trusted. Existing system-scoped resolvers validate identity; the existing ADR-0011 parser
authorizes ownership; explicit booleans carry override and immediate-shutdown intent;
bounded timeout and positive poll interval validation precede mutation. Errors expose only
resource identifiers and HMC error text already used by sibling tools, never credentials.

Existing boundaries widened are HMC job submission and delete endpoints, reached only
after the checks above. The tool annotation and destructive registry force clients to treat
the entry point as destructive. Out of scope are authentication of remotely exposed MCP
transport (covered by existing server deployment controls), protecting an operator who
explicitly overrides ownership, HMC-side races after inventory, and storage/backing-device
deletion. The workflow reduces races by retaining resolved UUIDs but cannot make separate
HMC calls transactional.

## Acceptance proof

Mocked tests prove dry-run zero writes and full inventory, happy-path ordering and curated
results, foreign-owner refusal and explicit override, already-off behavior, failed/timed-out
power-off short-circuit, adapter-delete partial failure, schema descriptions and destructive
registration, and CLI confirmation/JSON/options. Tests assert storage mappings are reported
but never deleted. The full branch passes both recorded guardrails. No live HMC is available
in this run, so no live destructive exercise is attempted.
