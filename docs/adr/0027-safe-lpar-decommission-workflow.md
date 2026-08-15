# ADR 0027: Safe LPAR Decommission Workflow

## Status

Accepted

## Context

Removing an LPAR currently requires callers to coordinate resolution, ownership
authorization, power-off job completion, adapter removal, and final deletion.
Splitting those safety checks across public calls makes it easy to target an
ambiguous name, ignore a foreign ownership token, or continue after a failed step.
Issue #149 requires one destructive inverse of `hmc_provision_lpar`, including a
side-effect-free blast-radius preview. The HMC API available to this project can
enumerate storage mappings but this change has no supported mapping-deletion contract.

## Decision

Add one destructive `hmc_decommission_lpar` workflow. It requires a managed-system
selector and proves that either an LPAR name or UUID identifies exactly one child of
that resolved system. A UUID is not passed through under ADR-0015's compatibility rule:
a missing, duplicate, or cross-system target fails before ownership reads, inventory, or
mutation. The workflow reads and enforces the ADR-0011 ownership token even for dry runs
and accepts `ownership_override=True` only as an explicit caller decision.

Every call inventories the current power state, all four supported client-adapter
types, and VIOS storage-detail mappings associated with the resolved LPAR. Storage
mappings are reported as blast-radius observations only. The workflow never deletes
or edits storage mappings or backing storage. Mappings whose client identity is too
sparse to classify are counted separately and produce an incomplete-inventory warning;
they are never silently presented as a complete negative result.

A dry run returns the inventory with `dry_run` step statuses and performs no writes.
The stable step identifiers are `power_off`, `detach_adapters`, and `delete_lpar`. In dry
run all three are `dry_run`. During execution an already inactive LPAR records
`power_off` as `ok` with `already_off: true`; otherwise it waits for a successful
terminal power-off outcome. Adapters are ordered by type as `ClientNetworkAdapter`,
`VirtualSCSIClientAdapter`, `VirtualFibreChannelClientAdapter`, then
`VirtualNICDedicated`, and by UUID within a type. The `detach_adapters` result contains
one `{type, uuid}` record for each deleted instance. The first failed adapter deletion
marks the whole phase `error`, stops further adapter deletion, and makes `delete_lpar`
`skipped`. Any failed phase makes every later phase `skipped`; earlier successes remain
`ok`. Public results expose identifiers and summary fields, not raw sub-operation
payloads, and no rollback is attempted.

## Consequences

- Target and ownership checks become mandatory workflow preconditions.
- Dry-run and execution apply the same inventory algorithm, but each call takes an
  independent current snapshot. A preview is informational; execution re-inventories and
  may report or affect a different set if HMC state changed between calls.
- Partial teardown remains possible and is reported for manual recovery.
- Storage mappings may cease to reference the LPAR as a consequence of adapter or LPAR
  deletion by the HMC, but this workflow does not claim or request that behavior.
- The workflow is a new destructive MCP/CLI contract and needs registry, schema, and
  mocked orchestration tests.

## Considered & rejected

- **Keep composing low-level tools.** This leaves resolution, authorization, ordering,
  and partial-failure truthfulness to every caller.
- **Delete storage mappings explicitly.** No supported deletion primitive or agreed
  ownership semantics exists; inventing one would expand the destructive surface.
- **Delete the LPAR and let the HMC handle everything.** This cannot provide the required
  ordered adapter steps or precise partial-failure record.
- **Automatically roll back after failure.** Recreating adapters or restoring an LPAR is
  not reliably reversible and conflicts with ADR-0005's manual-recovery model.
