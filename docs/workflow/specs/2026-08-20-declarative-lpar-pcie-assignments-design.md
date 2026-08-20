# Declarative LPAR PCIe assignments design

Issue #216 adds one typed assignment collection to LPAR create, provision, and modify.
The collection contains dedicated-slot, direct SR-IOV logical-port, and vNIC requests.  A shared
presentation-neutral coordinator validates every request before create and applies requests in
the stable order dedicated slot, direct SR-IOV, then vNIC.  Existing operations from ADRs 0055,
0056, and 0057 remain the only mutation implementations.

Provision uses the complete order `create → network → vscsi → storage → dedicated → direct
SR-IOV → vNIC → power_on`. Modify uses `resources → dedicated → direct SR-IOV → vNIC`. The first
failure skips every later step and retains every earlier external change.

Create and provision preflight the complete collection before creating an LPAR.  Dedicated-slot
requests therefore currently fail with the ADR 0055 capability-unavailable error.  SR-IOV and
vNIC requests validate selector shape, duplicate identities, conflicting capacity, adapter and
physical-port availability, and aggregate capacity per `(system, adapter_id, physical_port_id)`.
Existing direct and vNIC observations deduplicate by complete `(system, adapter_id,
logical_port_id)` identity only when parent and capacity agree; disagreement fails closed. Every
direct and vNIC request contributes to the prospective total. A stale or
unavailable inventory fails closed.  Prevalidation never claims to reserve capacity.

After creation, each request produces an ordered step record.  A failed mutation leaves the LPAR
and earlier successful assignments intact, marks later steps skipped, and returns a recoverable
partial result.  There is no automatic rollback.  Modify uses the same coordinator and requires
the managed-system selector so ADR 0011 ownership authorization remains inside the composed
operations.  Dry-run returns the same ordered step names with `dry_run` status.

The create, provision, and modify result types expose workflow completion, LPAR identity, and step
records.  The MCP, Python, and CLI surfaces consume the same dataclasses; no alternate raw-string
format is retained.
