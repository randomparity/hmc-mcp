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
`VnicChangeResult`. Its ordered fields are `operation`, `mutation_dispatched`, `changed`,
`selector`, `slot_num`, `vnic_before`, `backing_before`, `vnic_after`, `backing_after`,
`vnic_after_read_succeeded`, `backing_after_read_succeeded`, `output`, and `errors`.
`changed: bool | None`; selector and slot are nullable; each before/after field is an immutable
tuple of matching typed snapshots (empty means verified absence when its read succeeded); read
flags are bool; output is str; errors is an ordered tuple of public-safe strings. `changed=None`
means a
dispatched mutation could not be reconciled; it never means unchanged. A successful after-read
that proves absence uses a true read flag with an absent snapshot, while a failed read uses a false
flag, so those states cannot collapse. Multiple ambiguous matches remain in the tuple rather than
being discarded. `VnicPartialError` carries that complete result. Local
validation and admitted environment/inventory capability failures occur before writes; any
post-dispatch failure is partial. An HMC-only rejection such as the captured VLAN-restriction
diagnostic is partial because no admitted projection can prove that restriction before dispatch.

Result requiredness is operation-specific:

| Outcome | `selector` | `slot_num` | Evidence retained |
|---|---|---|---|
| Add unchanged | requested, required | matched existing slot, required | exact before pair in both before and after tuples |
| Add changed | requested, required | newly verified slot, required | baseline matches plus verified new pair |
| Add partial | requested, required | verified observed slot when known, otherwise `None` | every successful before/after projection, including ambiguous rows |
| Remove unchanged because absent | `None` | requested slot, required | successful empty before/after tuples |
| Remove changed | captured selector, required | requested slot, required | exact before pair and verified empty after tuples |
| Remove partial | captured selector when preflight found the slot, otherwise `None` | requested slot, required | every successful before/after projection, including ambiguous rows |

Pre-dispatch validation/capability exceptions do not manufacture a change result. Once dispatch
occurs, requested/captured selector and slot evidence are never cleared by a later failed read.

The MCP add tool exposes scalar fields for the selector because JSON tool schemas should not
require callers to encode Python dataclasses. CLI exposes corresponding named options. Both adapt
to the presentation-neutral operation. The Python API exports the dataclasses, errors, and
operations. `backing_devices`, generic top-level capacity, virtual-switch name, and `vnic_id` are
removed rather than aliased.

## Reads, validation, and data flow

SSH adds strict collectors for version-labelled vNIC and `vnicbkdev` key/value rows and a VIOS
identity projection. `No results were found.` is available-empty only for the admitted read. A
malformed identity or decimal fails closed. Duplicate vNIC slots fail. Logical ports conflict only
on complete `(system, adapter_id, logical_port_id)` identity. Repeated observations of that
identity deduplicate only when parent and compared capacity agree.
Before serialization, fields embedded in ADR 0057's slash-delimited backing value reject `/`.
Every caller field entering ADR 0045's HMC attribute record rejects ASCII controls, comma, equals,
and double quote. Other shell metacharacters are permitted and remain data because the completed
record and each standalone argument are shell-quoted for the remote shell. Nested backing grammar,
HMC attribute-record grammar, and remote-shell quoting are three separate controls.

Add authorizes the target LPAR, checks the environment, requires nonblank selector components,
finite one-to-100 capacity with at most two decimals, and VLAN 0–4094. It verifies exact VIOS
name/ID/type, healthy adapter, active physical port, and remaining percentage. Capacity reconciles
on the complete admitted identity `(system, adapter_id, logical_port_id)` and sums only identities
whose physical parent is the selected port. Direct and backing observations deduplicate only on
that key. A repeated identity must have the same physical parent and percentage: direct-row
`capacity` must equal backing-row `desired_capacity`; otherwise preflight fails. The VIOS is a
backing attribute, never a replacement identity component. Within one backing row, `capacity` and
`desired_capacity` may differ, and the reserved percentage uses `desired_capacity`. Add is
ensure-one: an exact vNIC match on target LPAR, VLAN, VIOS, adapter, physical
port, and capacity is unchanged only when exactly one active, Operational backing row correlates
to its logical port. Multiple matches or incomplete/degraded correlation fail closed without a
second add. The contract deliberately cannot create a second identical vNIC. Mutation serializes
the captured single-backing grammar and uses `-p`.

After add, compare target vNIC snapshots before/after. Require one new slot matching the request,
one allocated logical-port ID, and one matching `vnicbkdev` row with `is_active=1` and
`status=Operational`. Remove first resolves one target-LPAR slot and requires exactly one
correlated active, Operational backing row; zero, multiple, or degraded correlation fails before
mutation. Absence is unchanged. Mutation uses `-p` and `-s`. Verification requires the slot and
captured backing identity absent. Read/mutation races yield partial errors; no rollback is
attempted. An authorized operator can reuse the slot between preflight and dispatch, in which case
the replacement can be removed and cannot be restored by this workflow.

After dispatch, vNIC and backing reconciliation run independently even if the command raises,
times out, or one read fails. The result retains every failure. If both reads fail, both flags are
false, both after snapshots are absent, `changed` is unknown, and the original mutation cause plus
both read causes remain ordered in `errors`. No field fabricates an after state.

Reconciliation uses this total decision table. “Final” means add has one matching vNIC plus one
correlated active Operational backing, or remove has neither the captured slot nor its backing.
“Before” means add has neither newly matching object, or remove still has the exact captured pair.
Every other successful-read combination is contradictory or degraded.

| Dispatch/read evidence | `changed` | Result |
|---|---:|---|
| Both reads succeed and prove Final; command succeeded | `True` | return result |
| Both reads succeed and prove Final; command raised | `True` | partial error |
| Both reads succeed and prove Before | `False` | partial error |
| Both reads succeed but contradict or show degraded/ambiguous state | `None` | partial error |
| Exactly one read succeeds, whatever it shows | `None` | partial error |
| Neither read succeeds | `None` | partial error |

Each successful projection populates its matching `vnic_after` or `backing_after` tuple;
successful absence is its read flag true and an empty tuple. Each failed projection also leaves an
empty tuple but its flag is false. Errors contain only present causes, ordered as dispatch cause,
vNIC read cause, backing read cause, then one verification-mismatch description. When all commands
and reads succeed but verification mismatches, that mismatch is the first and only error. A dispatched
command returns normally only in the first row; every other row raises `VnicPartialError`, even if
one projection suggests the requested transition occurred.

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
remove verification, command timeout with one failed after-read, and command failure with both
after-reads failed and every cause retained. Table-driven tests cover every reconciliation row for
both operations, including contradictory successful reads and the captured HMC-only VLAN
restriction diagnostic. Parser/orchestration tests retain multiple ambiguous rows, reject
conflicting cross-projection capacity and every HMC delimiter including double quote, and
cover the same bare logical-port ID on two adapters without conflation. Result tests enforce every
row of the requiredness/evidence-retention table. Delimiter tests separately prove other shell
metacharacters remain quoted data. MCP/schema,
CLI, and public API tests prove replacement
and absence of the old names. System contract tests enforce evidence metadata and family boundary.
README documents typed inputs and verified outputs. Run focused tests, verify they fail before the
implementation and pass after it, then run `just verify` bare.
