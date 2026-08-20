# ADR 0057: Evidence-bounded vNIC backing selection

## Status

Accepted on 2026-08-20.

## Context

Issue #215 replaces an opaque `backing_devices` string. A live POWER9 8375-42A capture under HMC
V10R3 M1060 proves that add requires
`sriov/<vios-name>/<vios-id>/<adapter-id>/<physical-port-id>/<capacity>`, uses `-p`, and lets HMC
allocate the logical port. `lshwres` vNIC and `vnicbkdev` rows then correlate the target slot,
VIOS, adapter, physical port, allocated logical port, capacity, and Operational state. Remove uses
`-p <lpar> -s <slot>`. Two add/remove cycles returned to the clean baseline. ADR 0056 admits the
same-family SR-IOV inventory needed for preflight.

## Decision

Replace the old add arguments with one typed backing selector containing VIOS name and ID,
adapter ID, physical-port ID, and decimal capacity, plus the target VLAN. Do not accept caller
syntax, a caller-selected logical-port ID, top-level vNIC capacity, or virtual-switch name. HMC
allocates the logical port; the result exposes it only after correlated readback.

Before add, require the ADR 0056 environment, a healthy SR-IOV adapter, an available matching
physical port, a matching VIOS identity, and enough capacity across admitted logical-port and vNIC
backing inventories. An exact existing target-LPAR vNIC is an unchanged retry; ambiguous matches
fail closed. Add uses the captured `-p` grammar. After dispatch, exactly one new vNIC slot must
match the selector and VLAN, and exactly one Operational backing row must match its logical port.

Remove accepts the stable `slot_num` read identity. An absent target slot is unchanged. Otherwise
capture its backing logical ports, use the captured `-p ... -s ...` grammar, and require both the
slot and those backing rows to disappear. Every exception after dispatch triggers best-effort
reconciliation and raises a structured partial error carrying verified before/after state. There
is no rollback promise.

These guarantees apply only to POWER9 8375-42A under HMC V10R3 M1060. Other environments fail
before mutation. Preserve exact strings and decimal percentages; reject malformed, duplicate, or
ambiguous rows.

## Consequences

MCP, CLI, and Python callers receive one typed contract and stable dataclass results instead of
raw command output. The public add signature is intentionally breaking because the project is
pre-release and two old command forms are live-proven invalid. Capacity validation may still race
another operator; post-readback detects divergence but cannot undo a successful HMC allocation.
Failover and optional priority/max-capacity inputs remain unavailable.

## Considered & rejected

- **Retain the opaque string behind a typed wrapper.** judgment: callers could still smuggle
  unvalidated topology through the public boundary.
- **Let callers select a logical-port ID.** verified: issue #215 hardware Finding 4 shows the HMC
  allocates the logical port and reports it only in vNIC readback.
- **Reuse ADR 0056 direct logical-port assignment before vNIC add.** verified: issue #215 hardware
  Finding 3 records no logical-port field in add grammar, so preassignment cannot bind the vNIC.
- **Keep `--filter` add and `vnic_id` remove.** verified: issue #215 Findings 6 and 7 record their
  exact failures and the successful `-p` and `-s` forms.
- **Expose failover, priority, and maximum capacity now.** judgment: the issue requires the
  smallest typed replacement and the live run characterized only one backing device with default
  optional values.
- **Trust command success without readback.** verified: issue #215 Findings 4 and 5 provide the
  vNIC and per-backing-device projections needed to verify identity and Operational state.

