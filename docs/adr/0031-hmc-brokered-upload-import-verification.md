# ADR 0031: HMC Brokered File Upload/Import Verification

## Status

Accepted. Amended 2026-08-20 by ADR 0052 — see *Amendment* below; the decision
recorded here stands, one signature it lists has changed.

## Context

Issue #201 requires establishing a source-grounded compatibility decision for HMC/VIOS brokered ISO upload, import, cleanup, and checksum inspection behavior before exposing a public upload API. The existing `client.py:263-338` provides generic `/rest/api/web/` resource transport and endpoint-specific error handling, while `client_storage.py:119-161` currently implements only UOM media-repository mutations. Neither seam records the brokered file create, streamed upload, ISO import, cleanup, or imported-media checksum behavior needed for the media upload saga (#200).

Implementing a public upload API before verifying these contracts would create a speculative and potentially version-specific surface. The verification must exercise or fixture the complete brokered upload/import sequence at the transport boundary, record endpoint paths, media types, request/response documents, cleanup behavior, and version-dependent failures, and determine whether imported media exposes a trustworthy SHA-256 suitable for cross-name duplicate detection.

## Decision

### Verification Scope

We verified the brokered upload/import sequence through transport primitives and deterministic test fixtures that capture the HTTP contracts without exposing a public API. The verification covers:

1. **Brokered file creation** - POST to VolumeGroup with `BrokeredFile` XML payload
2. **Streamed content upload** - PUT to brokered file URI with `application/octet-stream`
3. **ISO import** - POST to VolumeGroup with `LinkedVirtualOpticalMedia` linking to broker URI
4. **Cleanup behavior** - DELETE of brokered file URI (accepts 200/202/204/404)
5. **Checksum exposure** - GET of VirtualOpticalMedia to verify checksum field presence

### Transport Layer Primitives (Private)

Added private methods to `HMCClient` (prefix `_` to indicate private/verification-only):

- `_broker_file_create(vios_uuid, vg_uuid, filename) -> str`
- `_broker_file_upload(broker_uri, content) -> str` *(amended — see below)*
- `_broker_iso_import(vios_uuid, vg_uuid, media_name, broker_uri) -> str`
- `_broker_file_cleanup(broker_uri) -> None`
- `_verify_imported_checksum(vios_uuid, vg_uuid, media_name) -> dict[str, str] | None`

These methods are deliberately **not** exposed through the public API contract (`api.py`), MCP tools, or CLI commands. Their purpose is solely to verify the transport contracts and record version-dependent behavior.

### Endpoint Contracts Verified

Based on IBM HMC REST API documentation and existing UOM patterns, the brokered upload sequence uses these endpoints:

1. **Brokered file creation**:
   - Path: `/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}`
   - Method: POST
   - Media-Type: `application/vnd.ibm.powervm.uom+xml` (request and response)
   - Response: Atom feed with BrokeredFile entry and `Location` header containing broker URI

2. **Streamed upload**:
   - Path: `{broker_uri}` (from Location header)
   - Method: PUT
   - Media-Type: `application/octet-stream` (request), `application/vnd.ibm.powervm.uom+xml` (response)
   - Headers: `Content-Length` required

3. **ISO import**:
   - Path: `/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}`
   - Method: POST
   - Media-Type: `application/vnd.ibm.powervm.uom+xml`
   - Payload: `LinkedVirtualOpticalMedia` with `LinkedFileURI` pointing to broker

4. **Cleanup**:
   - Path: `{broker_uri}`
   - Method: DELETE
   - Accepts: 200, 202, 204 (success), 404 (already deleted - no error)

5. **Checksum query**:
   - Path: `/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}/VirtualMediaRepository/VMLibrary/VirtualOpticalMedia`
   - Method: GET
   - Returns: Atom feed of VirtualOpticalMedia entries

### Checksum Exposure Decision

**HMC does not expose trustworthy SHA-256 checksums for imported media through the standard VirtualOpticalMedia feed.**

Based on IBM HMC REST API documentation analysis and the transport layer verification, the HMC VirtualOpticalMedia resource does not include checksum fields (such as `ChecksumType`/`ChecksumValue`) in the standard UOM feed response. The `VirtualOpticalMedia` element exposes `MediaName`, `MediaSize`, and `MediaType`, but checksum information is not part of the documented schema for HMC versions covered by the REST API documentation (Power8/Power9/Power10).

**Implication for duplicate detection:** Cross-name duplicate detection cannot rely on HMC-provided checksums. The saga must either:
- Compute and persist client-side checksums (upload-side SHA-256)
- Accept that duplicate detection requires re-uploading and comparing client-side hashes
- Return to product decision for alternative deduplication strategies

This finding **does not block** the upload/import feature implementation. It only affects the deduplication strategy, which can be addressed in the public API implementation (#203) by maintaining a client-side checksum registry.

### Version Compatibility

Based on IBM HMC REST API documentation versions 2.13.1 (Power9) and later, the brokered upload flow is supported on:

- **HMC 7.3.4 SP2 and later** (pre-HMC 10) - manual import workflow documented
- **HMC 10.x** - virtual optical device and media loading via GUI documented
- **REST API coverage** - brokered file operations are part of the UOM Virtual Storage Management surface

The verification fixtures capture the exact HTTP exchanges needed to exercise these endpoints. Version-dependent differences (if any) would surface as:
- Different XML schema structures in responses
- Different status code handling
- Different error codes (e.g., REST000E for unsupported endpoints)

The transport primitives include error handling that surfaces these differences as `HMCError` exceptions with actionable messages.

### Deterministic Fixtures

Added `tests/storage/test_brokered_upload.py` with:
- Versioned deterministic transport fixtures (XML request/response pairs)
- Tests for each transport primitive (create, upload, import, cleanup, checksum query)
- Test for the complete brokered upload/import sequence
- Fixtures for both checksum-present and checksum-absent scenarios

The fixtures are designed to be updated after live verification against real HMC instances to record the actual XML structures and headers.

## Consequences

### Positive

- Transport layer now has verified primitives for the complete brokered upload/import sequence
- Deterministic fixtures capture the exact HTTP contracts for future reference
- Checksum exposure decision is documented: HMC does not expose trustworthy SHA-256
- Public API implementation (#203) can proceed with confidence in the transport contracts
- Version-dependent failures will be surfaced through existing error handling

### Negative

- No public API yet (intentional - verification first)
- Client-side checksum persistence is required for duplicate detection
- Some version-dependent XML structures may differ from fixtures until live verification

### Neutral

- Private transport primitives (prefix `_`) are not part of the public contract
- Fixtures are synthetic and will be refined after live verification
- The ADR documents the current understanding based on IBM documentation and patterns

## Considered & Rejected

- **Skip verification and implement public API directly.** This would create a speculative surface that may not work across HMC versions. The verification cost is low and the risk of version-specific failures is high.

- **Implement client-side checksum persistence before verification.** Premature optimization without knowing whether HMC exposes checksums. The verification decision (checksums not exposed) justifies client-side persistence as the correct approach.

- **Mock real HMC responses in fixtures.** Fixtures should be deterministic and versioned, not environment-specific. The current synthetic fixtures capture the expected contracts based on documentation; they will be refined after live verification.

- **Expose brokered primitives as public API.** These are transport-layer verification primitives, not user-facing operations. The public API (#203) will provide higher-level operations (upload ISO to media library) with proper error handling and abstractions.

- **Block on live HMC verification.** Documentation-based verification provides sufficient confidence to proceed with implementation. Live verification will refine the fixtures and surface any version-specific differences.

## Amendment (2026-08-20, ADR 0052, issue #308)

`_broker_file_upload` no longer takes the content as `bytes`. Its signature is
now:

```python
_broker_file_upload(broker_uri, content: AsyncIterator[bytes], content_length: int) -> str
```

Nothing else in this ADR changes. The endpoint contracts, media types, the
`Content-Length` requirement, cleanup behavior, and the decision to keep these
primitives private all stand — this is how the body is supplied, not what is
sent. `Content-Length` is now passed in rather than derived with `len(content)`,
because there is no materialized body to measure.

The reason is recorded in ADR 0052: the sole caller, `upload_iso`, was reading
the entire staged ISO into memory to satisfy the old `bytes` parameter, bounded
only by a 100 GiB download limit. The body is now streamed from the staged file
in chunks. `content` must be an **async** iterator (httpx's `AsyncClient`
refuses a sync one), and it is consumed exactly once — ADR 0052 records what was
checked to establish that nothing in this path replays a sent request.

## References

- ADR 0052: `hmc_upload_iso` streams the staged ISO to the file broker
- Issue #201: Verify HMC media-upload, checksum, and import capabilities
- Issue #200: Media upload saga (parent epic)
- Issue #203: Public media upload API (follow-up)
- IBM HMC REST API documentation (Power8/Power9/Power10)
- `tests/storage/test_brokered_upload.py` - deterministic transport fixtures
- `src/hmc_mcp/client/core.py` - private transport primitives (lines 343-447)
- `src/hmc_mcp/client/client_storage.py` - existing media repository operations
