# SR-IOV logical-port assignment design

Issue: [#214](https://github.com/randomparity/hmc-mcp/issues/214)  
Decision: [ADR 0056](../../adr/0056-fail-closed-sriov-logical-port-assignment.md)

## Goal and constraints

Expose assign/unassign logical-port and adapter-mode operations keyed by issue #212's normalized
identities. Before any mutation, validate adapter mode, physical/logical-port identity, capacity,
availability, ownership, and ADR 0053 LPAR state/capability; retries must be idempotent and results
must carry verified before/after state. ADR 0053 currently admits none of the SR-IOV read projections
needed to prove those conditions, so the only evidence-backed implementation is capability-
unavailable before mutation. No dependency or migration is added. Python 3.11 remains the minimum.

## Public contracts

`assign_sriov_logical_port` and `unassign_sriov_logical_port` accept an HMC client, system and LPAR
name-or-UUID selectors, `adapter_id`, `physical_port_id`, `logical_port_id`, `capacity_percent`, and
an optional `ownership_override`. The operations validate selectors and a percentage in `(0, 100]`
with at most two decimal places, resolve the LPAR, enforce ADR 0011 ownership, then raise
`SriovLogicalPortCapabilityUnavailableError` with a stable ADR 0053 reason. They issue no SR-IOV
inventory or mutation command while the capability matrix is unavailable.

`set_sriov_adapter_mode` keeps its established Python name but changes from a raw SSH mutation into
a fail-closed operation accepting the normalized system selector, adapter ID, and `sriov` or
`dedicated`. Its MCP/CLI contract remains available under the existing name, but now returns the
same explicit capability error before SSH. This is replacement, not a compatibility shim.

The immutable `SriovLogicalPortAssignmentResult` defines the eventual successful shape: system,
LPAR, adapter/physical/logical-port identities, requested capacity, and separate profile/effective
before and after logical-port records. Current operations cannot construct it because no field may
be inferred. MCP tools use `target_kind="lpar"`; CLI commands mirror their parameters; all four
operations are re-exported from the supported Python API.

## Transport boundary

`ssh_commands.py` removes the conflicting executable adapter helper and adds no replacement
mutation builder. Documented command grammar remains in ADR 0053 until a supported operation can
validate and verify it. System/LPAR resolution and ADR 0011 ownership may perform their established
reads; the new path issues no SR-IOV inventory or mutation command.

## Errors and idempotency

Blank selectors, unknown modes, non-finite capacity, capacity outside `(0, 100]`, or more than two
decimal places fail before resolution. Missing/ambiguous system or LPAR selectors retain existing
resolver errors. Foreign/malformed ownership fails before capability reporting unless an audited
override is supplied. All otherwise-valid calls terminate at the same capability error and perform
no SSH mutation, making retries side-effect-free. No before/after result is returned until every
field can be populated from dedicated readback.

## Threat model

Added boundaries are authenticated MCP/CLI/Python arguments that can request external HMC mutation;
the actor is an authenticated local operator or automation identity. Selector/capacity validation,
ADR 0011 ownership authorization, shell quoting, attribute-record validation, and capability
fail-closed behavior control this boundary. Existing SSH credentials and transport remain trusted.
Failure text exposes only operation, selector category, and the public ADR reason. Compromised HMC
credentials, HMC-side authorization policy, and races after a future preflight are out of scope;
this version sends no mutation and therefore creates no such race.

## Testing

Focused tests prove input validation, ownership ordering and override forwarding, zero SR-IOV
inventory/mutation commands, stable capability errors, MCP security metadata and schemas, CLI
registration, supported API exports, and removal of the old raw helper behavior. Existing fixtures
continue to prove selectors and percentage units. `just verify` is the final guardrail.
