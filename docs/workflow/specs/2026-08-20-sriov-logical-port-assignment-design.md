# SR-IOV logical-port assignment design

Issue: [#214](https://github.com/randomparity/hmc-mcp/issues/214)  
Decision: [ADR 0056](../../adr/0056-evidence-bounded-sriov-logical-port-assignment.md)

## Goal and evidence boundary

Expose inventory-driven assign/unassign with normalized identities. Same-family issue #214 captures
admit POWER9/HMC V10R3 M1060 reads, dynamic assign, and Not Activated profile unassign. Running
dynamic unassign, successful unclaimed dynamic removal, and adapter-mode mutation remain
capability-unavailable. Python 3.11 remains the floor; no dependency or migration is added.

## Inventory and models

SSH collectors read exact captured fields for adapter, `--level roce` physical port, `--level eth`
configured logical port, default unconfigured logical identity, LPAR state/RMC, and one profile's
Ethernet property. Strict CSV parsing rejects header/width faults. Key/value parsing requires
identities. Decimal capacity preserves precision. Public models use stable fields; internal
snapshots retain mode, state, counts, functional state, profile, and RMC needed by policy.

## Operation contracts

`assign_sriov_logical_port(hmc, system, lpar, adapter_id, physical_port_id, logical_port_id,
capacity_percent, *, ownership_override=False)` supports dynamic assignment for `Not Activated` or
`Running` with active RMC. Capacity is finite, 1–100, at most two decimals. Preflight requires a
healthy SR-IOV adapter, active physical port, remaining capacity, matching unconfigured port, and no
foreign owner. Same-owner/same-capacity returns unchanged; other ownership conflicts. Readback
requires effective owner/identity/capacity and unchanged profile state.

Remaining percentage is `100 - sum(capacity)` across one complete same-snapshot configured
`--level eth` inventory filtered to the selected adapter and physical port. Every contributing row
must carry unique logical identity, matching parent identity, and finite capacity; missing,
duplicate, malformed, or negative totals fail closed before mutation. Logical-port counts are not
used as percentage capacity.

`unassign_sriov_logical_port(hmc, system, lpar, profile_name, adapter_id, physical_port_id,
logical_port_id, *, ownership_override=False)` supports only `Not Activated` profile state. `none`
is unchanged. A single exact profile record changes to `none`; mismatched/multiple records fail.
Running/unsupported states fail before mutation. No dynamic remove or `--force` is emitted.

Both return immutable `SriovLogicalPortChangeResult` with operation/path/change flag, selector,
verified effective/profile before and after snapshots, and output. Once a mutation command is
dispatched, success, nonzero exit, timeout, and transport failure all trigger best-effort effective
and profile reconciliation. Any command or reconciliation failure raises
`SriovLogicalPortPartialError` carrying the verified before state, every after state that could be
read, and the original cause. Validation, policy, conflict, and capability errors precede writes.

`set_sriov_adapter_mode` and CLI `set-sriov-mode` remain the single names. Same-mode is unchanged;
an actual transition is unavailable. The raw SSH mutation and wrapper are removed.

## Presentation surfaces

Assign/unassign MCP tools use mutate effect and `target_kind="lpar"`; adapter mode remains
managed-system scoped. CLI adds `assign-sriov-logical-port` and `unassign-sriov-logical-port` while
replacing `set-sriov-mode` in place. The Python API exports operations, results/errors, and mode.
Adapters serialize dataclasses and contain no policy.

## Threat model and errors

Authenticated input can request HMC mutation. Controls are validation, shell quoting/record guards,
ADR 0011 ownership, immediate preflight inventory, foreign-owner refusal, and post-readback.
Operators are untrusted for selector freshness; HMC and configured credentials are trusted peers.
Errors expose selectors/public diagnostics, never credentials. A read/mutation race can occur;
readback detects divergence but cannot roll back. No `--force`, multi-record profile rewrite,
dynamic unassign, mode transition, RoCE logical mutation, or cross-family inference is in scope.

## Verification

Sanitized fixtures preserve provenance, commands, fields, stdout/stderr, and exit status. Tests
cover parsing, empty/malformed/failure, every precondition, silent-reassignment prevention,
duplicate no-op, supported writes, partial errors, unsupported cells, MCP security/schema, CLI,
API exports, and raw-mode removal. Run the required focused suite, smoke, and `just verify`.
