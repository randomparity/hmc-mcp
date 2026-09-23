# ADR 0171: Virtual-disk VolumeGroup writes use read-modify-write with If-Match

## Status

Accepted (2026-09-23).

## Context

`create_virtual_disk` and `delete_virtual_disk` POSTed a sparse `VolumeGroup` document holding
only the disk being changed. On V10R3 both were rejected at schema validation, and a sparse
document omits the group's other disks and physical volumes; an earlier VolumeGroup write
(#779) destroyed physical-volume metadata. In the #879 live window (2026-09-23, V10R3 M1060) a
read-modify-write succeeded: GET the group, insert the disk, POST the whole element back with
`If-Match` set to the GET's `etag` header. That GET returned an `etag`; ETag presence was
observed on V10R3 M1060 only.

## Decision

Both writes GET the whole `VolumeGroup`, add or remove exactly one `VirtualDisk`, and POST the
whole element back through the existing media-repository helper (`Accept: */*`, typed
`Content-Type`) with `If-Match` set to the GET's ETag. A GET without an ETag refuses the write
before any POST. A 412 is reported as a concurrent change with nothing written. Delete refuses
zero or several matching disks; create refuses a name the group already holds.

## Consequences

- An HMC that sends no ETag on the VolumeGroup GET cannot create or delete virtual disks
  through this path; the error names the missing ETag.
- That the HMC enforces a mismatched `If-Match`, and deletes a disk omitted from the POST, is
  not yet live-verified (#879).
- The media-repository operations still POST the whole group without `If-Match`, so one racing
  a virtual-disk write can undo it; that is reported, not changed here.

## Considered & rejected

- **Fix the sparse document's attributes.** verified: the #936 owner comment (2026-09-23,
  V10R3) records the sparse document rejected at schema validation and read-modify-write
  succeeding.
- **Send `If-Match` only when an ETag is present.** judgment: silently falls back to an
  unconditional whole-group write on the path that holds guest data.
- **POST through `_post` / `_write_uom`.** verified: `_post` in `src/hmcpctl/client/core.py`
  takes no extra headers, so it cannot carry `If-Match` without a `core.py` change (#935).
