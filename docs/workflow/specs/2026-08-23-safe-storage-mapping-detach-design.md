# Safe storage mapping detach design

Issue: #403  
Decision: [ADR 0079](../../adr/0079-delete-vscsi-mapping-through-parent-vios.md)

## Outcome

Replace the unsupported child-resource DELETE with the documented parent
`VirtualIOServer` read-modify-write operation while retaining the existing mapping UUID
selector across the Python, MCP, and CLI surfaces.

## Design

The storage client GETs `/rest/api/uom/VirtualIOServer/{vios_uuid}` without a schema
version, validates the XML, locates the `VirtualIOServer` and its
`VirtualSCSIMappings`, and compares the selector only with each mapping's direct `UUID`
child text. Exactly one match is required. The client removes that element from the
existing tree and POSTs the serialized VIOS to
`/rest/api/uom/ManagedSystem/{system_uuid}/VirtualIOServer/{vios_uuid}` using the
VirtualIOServer media type.

The current public `mapping_uuid` contract remains appropriate: although the mapping is
not independently addressable, its parsed `UUID` is present in mapping inventory and
provides a storage-kind-independent exact identity. The CLI's human-readable inventory
must display `UUID`, not its currently mismatched `ElementID` lookup, so list output can be
fed directly into detach. No substring matching is permitted.

## Failure contract

Empty GET responses, malformed XML, missing VIOS content, missing or duplicate mapping
UUIDs, absent selectors, and unavailable managed-system identity raise `HMCError` before
POST. GET, POST, and HMC status failures propagate. The implementation never falls back
to a broader selector and never removes more than one mapping. Preservation applies to
the fetched snapshot. Because the documented API provides no revision precondition for
this parent POST, operators must serialize concurrent mapping writes to a VIOS; this change
does not claim lost-update protection against another writer between GET and POST.

## Testing

Transport tests exercise a realistic Atom VIOS document and assert the request sequence,
POST URI and media type, target removal, unrelated-mapping preservation, and preservation
of unrelated parent content in that snapshot. Separate cases prove exact rather than substring matching,
not-found and duplicate failures without POST, malformed XML failure, and POST failure.
Operations and server-tool tests assert the selector is passed unchanged. A CLI boundary
test uses realistic parsed inventory to prove the displayed UUID is the detach selector.

## Scope

Changes are limited to storage detach client behavior, directly affected storage-layer
tests, and this design record. Live HMC validation is excluded because no authorized live
target is available. No new dependency, migration, or configuration is introduced.
