# ADR 0079: Delete virtual SCSI mappings through the parent VIOS

## Status

Accepted

## Context

`VirtualSCSIMapping` is a detailed object in the HMC UOM API, not a directly
addressable child resource. The existing detach path nevertheless sends DELETE to a
fabricated child URI. Mapping inventory exposes a mapping UUID, and callers already use
that value as the public selector.

## Decision

Keep the mapping UUID as the public selector, expose the parsed `UUID` consistently in
human-readable inventory, and treat it only as an identity within the fetched parent
document. Fetch the complete `VirtualIOServer`, require exactly one
`VirtualSCSIMapping` whose direct `UUID` child equals the selector, remove only that
element, and POST the modified `VirtualIOServer` to its managed-system parent URI.

Missing, duplicate, malformed, or empty mapping identity fails without a POST. Network
and HMC failures propagate. Unrelated mappings and all other VIOS content from the fetched
snapshot are retained. HMC UOM exposes no revision precondition in the documented flow,
so callers must serialize concurrent VIOS mapping changes; the method cannot prevent a
different writer from changing the parent between GET and POST.

## Consequences

The CLI, MCP tool, and Python operation retain their existing signatures. The CLI inventory
uses `UUID`, matching the transport parser and detach selector, rather than the currently
empty `ElementID` lookup. Detach is no
longer idempotent for a missing mapping: absence is reported because silently accepting
an unverifiable destructive selector would hide stale inventory or operator mistakes.
The client must parse and serialize the parent XML without rebuilding it from the lossy
inventory dictionary.

## Considered & rejected

- **Select by LPAR and backing-storage name.** judgment: this widens the public contract,
  requires storage-kind-specific identity rules, and can still be ambiguous while the
  inventory already exposes a stable UUID for exact in-document matching.
- **DELETE a child URI derived from the mapping UUID.** verified: issue #403 records the
  IBM UOM contract that detailed `VirtualSCSIMapping` objects do not support direct
  GET/PUT/POST/DELETE operations.
- **Silently succeed when no mapping matches.** judgment: destructive operations should
  fail closed when the requested object cannot be proven unique.
- **Add client-side locking.** judgment: a process-local lock cannot coordinate other HMC
  users or services and would imply concurrency safety the external API cannot enforce.
