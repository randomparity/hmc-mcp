# ADR 0177: ISO upload goes through the HMC web File API

## Status

Accepted (2026-09-24). Supersedes [ADR 0031](0031-hmc-brokered-upload-import-verification.md)'s
brokered-file request shape. ADR 0052's streaming decision stands and now applies to the File
contents upload.

## Context

ADR 0031 derived `upload_iso`'s requests from documentation: a `BrokeredFile` POST to the
VolumeGroup, a PUT to the returned `Location`, a `LinkedVirtualOpticalMedia` import POST, and a
DELETE of the handle. No HMC ever accepted it. V10R3 answers the first request with
`400 REST0001 ... Cannot find the declaration of element 'BrokeredFile'` (#978).

The documented alternative, the `AddOpticalMedia` VirtualIOServer job, needs HMC 10.3.1061 or
later. The HMC this release is verified against runs `V10R3 M1060`.

In the #879 window on 2026-09-23 (`V10R3 M1060`, `8375-42A`), an out-of-tree probe uploaded a
1,160,095,744-byte ISO through the web File API:

1. `PUT /rest/api/web/File`, `Content-Type: application/vnd.ibm.powervm.web+xml; type=File`,
   `Accept: */*`. The body is a web-namespace `File` with `schemaVersion="V1_0"`, an empty
   `Metadata/Atom`, then `Filename`, `InternetMediaType` (`application/octet-stream`),
   `ExpectedFileSizeInBytes`, `FileEnumType` (`BROKERED_MEDIA_ISO`) and
   `TargetVirtualIOServerUUID`, in that order. The HMC answered 200 with an Atom `entry` whose
   `content` holds `File:File` in the web namespace as the default namespace; its `FileUUID`
   child is therefore in that namespace, and equals the entry's `id`.
2. `PUT /rest/api/web/File/contents/<FileUUID>`, `Content-Type: application/octet-stream`,
   `Accept: */*`, the bytes streamed with a `Content-Length`: 204.
3. The VIOS repository's `VolumeGroup` listed a `MediaName` equal to the `Filename` sent, on the
   first poll.
4. `DELETE /rest/api/web/File/<FileUUID>`, `Accept: */*`: 204.

The HMC echoed the elements in its own order: `Filename`, `DateModified`, `InternetMediaType`,
`FileUUID`, `ExpectedFileSizeInBytes`, `FileEnumType`, `TargetVirtualIOServerUUID`. The
request sends the proven five, in the order above. `DateModified` and `FileUUID` are read-only
(`kb="ROR"`).

## Decision

`upload_iso` uses the web File flow above. It sends that exact element order and those exact
headers. The optional `X-HMC-Schema-Version` header is added the way every other
`/rest/api/web/` request adds it. The client owns three requests:

- `_web_file_create(vios_uuid, filename, size_bytes) -> str` returns the `FileUUID`. The
  response must hold exactly one `FileUUID`, and it must be a UUID.
- `_web_file_upload(file_uuid, content, content_length) -> None` streams the async iterator
  from ADR 0052.
- `_web_file_delete(file_uuid) -> None` accepts 200, 202, 204 and 404.

Before downloading anything, the operation refuses a volume group that holds no media
repository. It creates the File, streams the staged ISO into it, and then polls the target
volume group's repository. It reports `uploaded` only when the media name is listed there, and
fails if it is not listed within a bounded number of polls. It deletes the File handle in a
`finally` on every outcome after a successful create. A delete failure is raised after a
successful upload. After a failed upload, the delete failure is only logged, so it does not
hide the upload failure. The client-side download, its size bound, and the SHA-256 stay as
they are.

The `BrokeredFile` and `LinkedVirtualOpticalMedia` builders and the four `_broker_*` methods
are removed.

## Consequences

- There is no separate import request. The HMC puts the uploaded ISO into the VIOS's single
  media repository. The volume-group argument now picks where visibility is checked, not
  where the ISO goes. A volume group with no repository is refused before the transfer. The
  errors raised after the HMC accepted the bytes say so, and they tell the operator to check
  `list-optical-media` before retrying.
- When the media does not appear in time, the `finally` deletes the File before the media is
  visible. Nobody has tested whether that cancels an import that is still running, and the
  error says the media may or may not land.
- `media` in the result is never `None`. The HMC's inventory not listing the media is now an
  error, not a success with no entry.
- A SHA-256 element on the `File` stays unverified, so the digest is still computed and
  reported only by the client.
- The contents PUT runs under the configured `HMC_TIMEOUT`. The 2026-09-23 probe used a
  one-hour timeout, so whether the HMC answers a large upload promptly after the last byte
  was not measured.

## Considered & rejected

- **Keep the `BrokeredFile` shape and fix its headers.** verified: the #879 window run of
  2026-09-23 on `V10R3 M1060` got `400 REST0001 Cannot find the declaration of element
  'BrokeredFile'` once the #935 header was corrected (issue #978 body).
- **The `AddOpticalMedia` VirtualIOServer job.** verified:
  `docs/refs/hmc-rest-api-p10/jobs/virtualioserver-jobs/122-addopticalmedia_virtualioserver-job.md`
  says "With HMC 10.3.1061.0, or later", and the verified HMC runs M1060.
- **Report success without checking the repository**, as the old path did when the media was
  missing. judgment: fit. The issue requires the ISO to be visible before it is reported
  uploaded, and a 204 from the contents PUT alone does not show that.
- **Delete the File handle before the visibility check.** judgment: cost. The probe deleted it
  after the media was visible. Nobody tested whether an earlier delete cancels an import that
  is still running.
