# vNIC inventory selector design

Issue: [#215](https://github.com/randomparity/hmc-mcp/issues/215)  
Decision: [ADR 0057](../../adr/0057-evidence-bounded-vnic-backing-selection.md)

## Goal and constraints

Replace opaque vNIC backing syntax across SSH, MCP, CLI, and Python API. Python 3.11 remains the
floor; no dependency, migration, alias, dual format, failover surface, or cross-family behavior is
added. Host verification is arm64; declared targets are amd64, arm64, and ppc64le; the host is
included. The final guardrail is `just verify`.

Only POWER9 8375-42A / HMC V10R3 M1060 is admitted. The issue's live capture supplies add/remove
grammar, vNIC and `vnicbkdev` readback, diagnostics, and two cleaned-up cycles. ADR 0056 supplies
adapter, physical-port, logical-port, environment, ownership, and capacity rules.

## Public models and interfaces

`VnicBackingSelector` is immutable and contains `vios_name`, `vios_lpar_id`, `adapter_id`,
`physical_port_id`, and `capacity_percent: Decimal`. `VnicSnapshot` contains target LPAR,
`slot_num`, VLAN, and the normalized backing snapshots. `VnicBackingSnapshot` contains VIOS,
adapter, physical/logical port, desired/current capacity, activity, and status.

`add_vnic(hmc, system, lpar, selector, port_vlan_id, *, ownership_override=False)` and
`remove_vnic(hmc, system, lpar, slot_num, *, ownership_override=False)` return immutable
`VnicChangeResult`. The result records operation, changed, selector or slot, verified before and
after snapshots, and raw mutation output. `VnicPartialError` carries that result. Validation and
capability failures occur before writes; any post-dispatch failure is partial.

The MCP add tool exposes scalar fields for the selector because JSON tool schemas should not
require callers to encode Python dataclasses. CLI exposes corresponding named options. Both adapt
to the presentation-neutral operation. The Python API exports the dataclasses, errors, and
operations. `backing_devices`, generic top-level capacity, virtual-switch name, and `vnic_id` are
removed rather than aliased.

## Reads, validation, and data flow

SSH adds strict collectors for version-labelled vNIC and `vnicbkdev` key/value rows and a VIOS
identity projection. `No results were found.` is available-empty only for the admitted read. A
malformed identity, decimal, duplicated slot/logical port, or conflicting parent fails closed.

Add authorizes the target LPAR, checks the environment, requires nonblank selector components,
finite one-to-100 capacity with at most two decimals, and VLAN 0–4094. It verifies exact VIOS
name/ID/type, healthy adapter, active physical port, and remaining percentage. Capacity sums unique
logical-port IDs from direct SR-IOV rows and vNIC backing rows so an identity observed in both is
counted once. Add is ensure-one: an exact vNIC match on target LPAR, VLAN, VIOS, adapter, physical
port, and capacity is unchanged only when exactly one active, Operational backing row correlates
to its logical port. Multiple matches or incomplete/degraded correlation fail closed without a
second add. The contract deliberately cannot create a second identical vNIC. Mutation serializes
the captured single-backing grammar and uses `-p`.

After add, compare target vNIC snapshots before/after. Require one new slot matching the request,
one allocated logical-port ID, and one matching `vnicbkdev` row with `is_active=1` and
`status=Operational`. Remove first resolves one target-LPAR slot and captures its backing logical
ports. Absence is unchanged. Mutation uses `-p` and `-s`. Verification requires the slot and all
captured backing identities absent. Read/mutation races yield partial errors; no rollback is
attempted.

## Error handling and threat model

Authenticated MCP/CLI/API callers can cause HMC mutation and control every selector string. The
added trust boundaries are public scalar input into validation and then shell command composition;
the widened boundaries are vNIC add/remove and system-wide backing inventory. Controls are strict
value bounds, exact inventory membership, target-LPAR authorization, VIOS identity/type check,
shell quoting of the complete attribute payload and each selector argument, ambiguity rejection,
and post-readback. HMC and configured credentials are trusted peers; callers and inventory
freshness are not. Errors contain public selectors and HMC diagnostics but no credentials.

Out of scope: protection from a separately authorized HMC operator racing the request, rollback of
a successful external mutation, multi-backing failover policy, unsupported HMC families, and
authorization policy changes. Existing ADR 0011 ownership remains the authorization control.

## Verification

Add sanitized version-labelled fixtures for baseline/add/remove vNIC and backing rows plus the
captured failure diagnostics. Unit tests prove strict parsing, command grammar and quoting,
validation, exact retries, capacity aggregation, relationship failures, successful correlation,
remove verification, and partial errors. MCP/schema, CLI, and public API tests prove replacement
and absence of the old names. System contract tests enforce evidence metadata and family boundary.
README documents typed inputs and verified outputs. Run focused tests, verify they fail before the
implementation and pass after it, then run `just verify` bare.
