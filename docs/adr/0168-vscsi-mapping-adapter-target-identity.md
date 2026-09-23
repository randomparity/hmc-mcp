# ADR 0168: Identify a vSCSI mapping by its server adapter and target device

## Status

Accepted (2026-09-23). Supersedes ADR 0079's selector decision; its parent-VIOS
read-modify-write, fail-closed, and caller-serialization rules stand.

## Context

ADR 0079 kept a mapping `UUID` as the public detach selector and rejected name-based
selection because "the inventory already exposes a stable UUID". A read-only probe of a
V10R3 VIOS (issue #940) shows the premise is false: a `VirtualSCSIMapping` carries
`AssociatedLogicalPartition`, `ClientAdapter`, `ServerAdapter`, `Storage`, and
`TargetDevice`, and no `UUID`. Inventory therefore fails on the first real mapping, and
neither detach nor optical unmount can select one. The same probe shows the client-LPAR
link is absolute and system-scoped (`.../ManagedSystem/<system>/LogicalPartition/<lpar>`),
which the code's `/rest/api/uom/LogicalPartition/<lpar>` prefix check never matches.

## Decision

A mapping's identity is `<ServerAdapter/AdapterName>/<TargetName>`, where `TargetName` is
the sole child of `TargetDevice` (for example `vhost0/vtscsi0` or `vhost1/vtopt0`). The
VIOS assigns both device names and each is unique on that VIOS. One client function
derives it; inventory, detach lookup, the parent-document remover, and optical unmount
all call that function. A mapping missing either name, or holding more than one target,
has no identity: inventory lists it with `id: null` and nothing can detach it.

The public selector becomes `mapping_id` (MCP parameter, CLI argument, Python operation)
and `StorageMapping.uuid` becomes `id`, with no alias. The remover requires exactly one
mapping in the fetched VIOS document whose identity equals the selector; zero or several
fail without a POST. Mappings without an identity are ignored, not fatal.

The client-LPAR UUID is the final path segment after `/LogicalPartition/` in the href, with
scheme, host, and any `ManagedSystem/<system>` prefix ignored. A missing, empty, or
slash-containing segment has no LPAR, which fails detach closed and matches no LPAR filter.

## Consequences

MCP and CLI callers pass `mapping_id` and read `id`; pre-release callers holding UUIDs from
synthetic data have nothing to migrate because a real HMC never produced one. The identity
depends on device names the VIOS reports only while it is running: a mapping whose adapter
name is absent is listable but not detachable until the VIOS reports it. Renaming a target
device between list and detach makes the old identity not found, which fails closed.

## Considered & rejected

- **Keep `mapping_uuid` and fall back to a derived value.** verified: the V10R3 probe
  recorded in issue #940 returned no `UUID` on any mapping, so the UUID branch is dead code
  and the name would misdescribe the value.
- **Key on the server `VirtualSlotNumber` instead of `AdapterName`.** judgment: the slot is
  always populated but operators recognise `vhost0` from `lsmap`; operator chose adapter names.
- **Separate `server_adapter` and `target_device` parameters.** judgment: a wider signature
  for the same identity, and list output could no longer feed detach as one value.
- **Key on client LPAR plus backing-storage name.** judgment: needs a storage-kind-specific
  rule and can be ambiguous when one backing device is mapped twice.
