# ADR 0027: Safe LPAR Decommission Workflow

## Status

Accepted

> **Partially superseded by [0190](0190-durable-logical-operation-store.md) and [0192](0192-decommission-owned-storage-cleanup.md)** (2026-10-01)

## Context

Removing an LPAR currently requires callers to coordinate resolution, ownership
authorization, power-off job completion, adapter removal, and final deletion.
Splitting those safety checks across public calls makes it easy to target an
ambiguous name, ignore a foreign ownership token, or continue after a failed step.
Issue #149 requires one destructive inverse of `hmc_provision_lpar`, including a
side-effect-free blast-radius preview. The original workflow had no supported mapping-deletion contract. ADRs 0168 and
0169 now provide exact vSCSI mapping identity and guarded read-modify-write deletion.
Issue #1387 requires using that primitive while the client partition still exists.

## Decision

Add one destructive `hmc_decommission_lpar` workflow. It requires a managed-system
selector and proves that either an LPAR name or UUID identifies exactly one child of
that resolved system. A UUID is not passed through under ADR-0015's compatibility rule:
a missing, duplicate, or cross-system target fails before ownership reads, inventory, or
mutation. The workflow reads and enforces the ADR-0011 ownership token even for dry runs
and accepts `ownership_override=True` only as an explicit caller decision. The owner
reported in the blast radius comes from that same authorizing description read. Before
execution performs its first mutation, it repeats the ownership read and enforcement so
an ownership change during inventory fails closed; this remains an advisory check rather
than a cross-process lock under ADR-0011.

Every call inventories the current power state, all four supported client-adapter
types, and VIOS storage-detail mappings associated with the resolved LPAR. Execution
requires complete vSCSI inventory before its first mutation: each listed VIOS has
readable, structurally valid storage detail; each vSCSI mapping has a valid linked
client UUID; each mapping linked to the target has one exact, unambiguous
`<server adapter>/<target device>` identity. Numeric client IDs and a curated mapping
UUID alone cannot authorize deletion. Unavailable or unclassifiable vSCSI inventory
stops execution before power-off, adapter deletion or partition deletion. Dry-run
still returns the inventory and warnings without writes. Incomplete vFC observations
remain warnings; this change adds no VIOS-side vFC deletion.

After a successful power-off and fresh `not activated` state read, the workflow
removes the inventoried target vSCSI mappings through the existing grouped
read-modify-write primitive. Each write freshly verifies the exact mapping identity
and authorized target-client UUID and requires the GET's ETag. A missing, duplicate,
retargeted mapping, missing ETag, concurrent-change refusal or HMC failure stops
teardown. No failed or ambiguous write is retried or promoted to success from
readback; earlier successful detach records remain visible for manual recovery.
Foreign mappings and backing storage are retained. Future explicit owned backing
storage cleanup remains governed by ADR 0192 and issue #1229.

A dry run returns the inventory with `dry_run` step statuses and performs no writes.
The ordered step identifiers are `power_off`, `detach_storage_mappings`,
`detach_adapters`, and `delete_lpar`. In dry run these four are `dry_run`; the new
mapping step reports exact VIOS/mapping identities. An incomplete execution records
`power_off: skipped`, `detach_storage_mappings: error`, and later steps `skipped`.
During execution an already inactive LPAR records
`power_off` as `ok` with `already_off: true`; otherwise it waits for a successful
terminal power-off outcome. Immediately before mapping deletion and again before
adapter deletion, the workflow re-reads
`PartitionState` and fails the detach phase unless the value is exactly `not activated`.
Adapters are ordered by type as `ClientNetworkAdapter`, `VirtualSCSIClientAdapter`,
`VirtualFibreChannelClientAdapter`, then `VirtualNICDedicated`, and by UUID within a type.
The `detach_adapters` result contains one `{type, uuid}` record for each deleted instance.
The first failed adapter deletion marks the whole phase `error`, stops further adapter
deletion, and makes `delete_lpar` `skipped`. Any failed phase makes every later phase
`skipped`; earlier successes remain `ok`. Public results expose identifiers and summary
fields, not raw sub-operation payloads, and no rollback is attempted.

## Consequences

- Target and ownership checks become mandatory workflow preconditions.
- Dry-run and execution apply the same inventory algorithm, but each call takes an
  independent current snapshot. A preview is informational; execution re-inventories and
  may report or affect a different set if HMC state changed between calls.
- Partial teardown remains possible and is reported for manual recovery.
- Target vSCSI mappings are explicitly removed while the authorized client still exists;
  backing storage is retained. Unknown vSCSI inventory now blocks execution rather than
  merely warning. The read-only inspection helper retains its observation contract.
- A preview does not reserve its mappings; an out-of-band mapping addition after inventory
  is outside this advisory snapshot. The existing exclusive live-writer window is required
  for the native proof; there is no new lock or reconciliation policy.
- The workflow is a new destructive MCP/CLI contract and needs registry, schema, and
  mocked orchestration tests.

## Considered & rejected

- **Keep composing low-level tools.** judgment: fit. Callers would still coordinate the
  ordering and could delete the authorized client before removing its mapping.
- **Leave mappings as observations.** verified: `decommission_lpar` previously called only
  adapter and partition deletes; `detach_storage_mapping` authorizes its existing mapped
  client before calling the guarded primitive. Deleting that client first leaves no
  supported authorization path (issue #1387).
- **Authorize orphan mapping deletion.** judgment: fit. This would introduce a new
  authorization exception instead of reusing the target partition’s current guard.
- **Proceed with incomplete vSCSI inventory.** judgment: fit. The workflow cannot establish
  that the target mappings were removed before their authorized client disappears.
- **Delete the LPAR and let the HMC handle everything.** judgment: fit. This cannot provide the required
  ordered adapter steps or precise partial-failure record.
- **Automatically roll back after failure.** judgment: fit. Recreating adapters or restoring an LPAR is
  not reliably reversible and conflicts with ADR-0005's manual-recovery model.
