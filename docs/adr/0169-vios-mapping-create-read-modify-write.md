# ADR 0169: Create VIOS mappings by read-modify-write of the ViosSCSIMapping group

## Status

Accepted (2026-09-23)

## Context

`storage map` and `mount-optical-media` POST a `VirtualIOServer` document. Its
`VirtualSCSIMappings` collection (`kb="CUD"`) carries only the new mapping (#962). If the HMC
reads that collection as the complete set, one create drops every other mapping on the VIOS,
including mappings of partitions this run does not own. In the #879 window (V10R3 M1060), the
following sequence created a mapping and left the VIOS's existing mapping byte-identical:
GET `VirtualIOServer/<vios>?group=ViosSCSIMapping`, append one `VirtualSCSIMapping`, POST
back to the same URL with `If-Match: <etag>`, `Accept: */*` and a typed `Content-Type`.

## Decision

Both mapping creates use that sequence verbatim through one private client helper. The builder
still renders the new mapping, and the helper appends that element to the fetched collection.
The helper refuses before any POST when the GET lacks an `ETag`, names another VIOS, or has no
`VirtualSCSIMappings` collection. A 412 is reported as a concurrent change and is not retried.

## Consequences

Every create costs one extra GET and fails on a concurrent VIOS change instead of overwriting
it. A VIOS whose grouped GET omits an empty collection cannot receive its first mapping until
#879 shows what the HMC returns there. `delete_storage_mapping` keeps its own RMW (full GET, a
system-scoped POST without `If-Match`), so the two paths differ until a follow-up aligns them.
The HMC creates a new client/server adapter pair for each mapping, and a vSCSI adapter added
beforehand is left without a server adapter. The `docs/cli.md` bootable-disk recipe no longer
adds one. The provision and attach-disk workflows still do, which is left to a follow-up.

## Considered & rejected

- **Keep the sparse POST.** verified: the issue #962 body records the `kb="CUD"` replacement
  risk and #779's destructive V10R3 storage write; nothing shows that the HMC merges the
  collection.
- **Reuse the detach path's full GET and system-scoped POST.** judgment: that sequence has not
  been live-proven for a create, and the grouped form has been.
- **Create the collection when it is absent.** judgment: a response missing the collection
  cannot be told apart from an unexpected document, and posting into it recreates the sparse
  write this record removes.
- **POST without `If-Match` when the GET has no `ETag`.** judgment: a lost update would silently
  drop a mapping added concurrently, which is the harm this record prevents.
- **Share one RMW helper with the VolumeGroup path.** judgment: this is left to #936, which
  owns that path.
- **Pass a pre-added `ClientAdapter` into the mapping.** judgment: it is not live-proven, and
  the recipe no longer creates such an adapter.
