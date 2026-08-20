# ADR 0058: Declarative LPAR PCIe assignment orchestration

## Status

Accepted on 2026-08-20.

## Context

Issue #216 requires create, provision, and modify to share one PCIe assignment vocabulary while
preserving ADR 0005 observable partial failures, ADR 0011 ownership checks, and the evidence
ceilings in ADRs 0053, 0055, 0056, and 0057.

## Decision

Use one typed assignment collection containing dedicated-slot, direct SR-IOV logical-port, and
vNIC requests.  Validate the complete collection before LPAR creation.  Reject malformed or
duplicate selectors, conflicting capacities, unavailable inventory, unhealthy topology, and
aggregate capacity exhaustion.  Do not treat prevalidation as a reservation.

Reconcile capacity per `(system, adapter_id, physical_port_id)`. Deduplicate existing direct and
vNIC observations by complete `(system, adapter_id, logical_port_id)` identity only when their
parent physical port and capacity agree; disagreement fails closed. Include every direct and vNIC
request in the prospective total even though HMC allocates vNIC logical-port IDs.

Apply assignments in the stable order dedicated slot, direct SR-IOV, then vNIC by composing the
presentation-neutral operations accepted by ADRs 0055, 0056, and 0057.  Keep their capability,
ownership, idempotency, and readback rules intact.  In particular, dedicated assignment remains
unavailable until ADR 0053 admits profile readback.

Return stable per-step `ok`, `error`, `skipped`, or `dry_run` outcomes.  After creation, stop on
the first failure, retain the created LPAR identity and earlier outcomes, and perform no general
rollback.  Existing-LPAR assignment uses the same vocabulary and ordering.

Provision runs `create → network → vscsi → storage → dedicated → direct SR-IOV → vNIC →
power_on`. Modify runs `resources → dedicated → direct SR-IOV → vNIC`. The first failure records
`error`, marks later steps `skipped`, and retains earlier external changes. All three workflows
return an envelope carrying stable ordered outcomes.

## Consequences

Callers can declare mixed assignments once and can safely inspect partial state for recovery.
Prevalidation narrows avoidable failures but cannot prevent a concurrent operator from consuming
capacity; the composed operations' readback and partial errors remain authoritative.  A request
containing a dedicated slot currently fails before create instead of exposing a phantom feature.
Because a vNIC request names a nested VIOS that MCP target extraction cannot enumerate, create,
modify, and provision are non-exhaustive target tools and require an `all-targets` policy grant.

## Considered & rejected

- Embed assignments in the REST LPAR XML. Rejected because the accepted evidence admits only the
  composed SSH operations and their readbacks.
- Roll back the LPAR after an assignment failure. Rejected because ADR 0005 requires observable
  partial state and no general automatic rollback.
- Keep separate create, provision, and modify schemas. Rejected because issue #216 requires one
  vocabulary and separate formats would drift.
