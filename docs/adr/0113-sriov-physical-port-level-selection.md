# ADR 0113: SR-IOV physical-port level selection

## Status

Accepted on 2026-09-01. Supersedes ADR 0056 only for physical-port read level selection.

## Context

ADR 0056 admits one captured POWER9/HMC V10R3 M1060 family and hard-codes
`--level roce` for SR-IOV physical-port inventory. Issue #557 reports a second
successful physical-port state query using `--level ethc`. IBM's `lshwres`
manual defines `roce` and `ethc` as physical-port type levels. Issue #573's
sanitized live evidence covers six HMC profiles, about 68 systems, HMC V10R3
M1060 and V11R2 SP1120, and thirteen machine types. Every numeric adapter ID
returned rows at exactly one of the two levels; the other level returned the
literal empty-result sentinel at exit zero. Mixed live port states establish
`1` as up and `0` as down. The evidence does not support the speed threshold
suggested in #557.

The adapter inventory admitted by ADR 0056 does not expose a type field that can
select the level before the physical-port read. An empty result from one valid
level is not evidence that the adapter has no physical ports.

## Decision

For an environment already admitted by ADR 0056, require the requested adapter
ID to be a positive decimal integer, then query the exact physical-port
projection at both `roce` and `ethc`. Accept the one non-empty result. If both
are empty, preserve an empty row result for existing internal consumers and
have physical-port inventory raise `SriovLogicalPortCapabilityError`; if both
contain rows, reject the result as ambiguous rather than selecting by order.
Require every returned row to belong to the requested adapter. Treat
`phys_port_type` as HMC data rather than a selector: the captured `roce` result
reports `eth`, so equality with the query level is not an evidenced invariant.

Normalize the HMC `state` value in inventory: `1` becomes `up` and `0` becomes
`down`. Any other value is malformed input and fails closed. Do not change the
HMC release or managed-system model admission check, logical-port commands,
mutation matrix, or captured fixture.

## Consequences

Physical-port state inventory works for the two evidence-supported adapter-type
levels while retaining the exact version/model admission pair. Each read performs two
read-only commands so unexpected dual-level output can fail closed. Operators
get an explicit failure for an invalid adapter selector, unsupported or
ambiguous adapter type, mismatched adapter identity, or unknown state.

## Considered & rejected

- **Select `ethc` above a connection-speed threshold.** verified: IBM's Power11
  `lshwres` manual defines `ethc` as converged Ethernet and `roce` as RDMA over
  Converged Ethernet, while issue #214's captured 100,000 Mbps adapter succeeds
  only at `roce`; no authoritative speed threshold supports the issue #557 claim.
- **Stop after the first non-empty level.** judgment: this saves one read-only
  command but cannot detect the dual-nonempty condition that the frozen scope
  requires to fail closed.
- **Return available-empty when neither level has rows.** judgment: absence at
  both admitted levels does not distinguish an empty adapter from an unsupported
  type such as plain `eth`.
- **Remove the family admission check because the read is non-mutating.**
  judgment: the new evidence supports the existing pair but #557 does not ask
  to redesign the shared capability boundary.
- **Probe non-positive or symbolic adapter IDs.** verified: issue #573 records
  exit 1 and the HMC invalid-filter diagnostic for `null` and `unavailable`, so
  those inventory values must be rejected before command construction.
