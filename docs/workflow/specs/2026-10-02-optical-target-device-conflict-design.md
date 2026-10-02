# Optical mount target-device conflict (#1283)

## Problem

`create_optical_mapping` with an explicit `target_device` that a mapping on the VIOS already
uses fails on the HMC with HTTP 500 (`VTD <name> name is already used in another mapping`,
observed once on V10R3 in #1280). The client reports it only as a possible side effect, so the
operator learns neither the cause nor the remedy. The read-modify-write GET (ADR 0169) already
holds every mapping's `VirtualOpticalTargetDevice` `TargetName`
(`tests/fixtures/live/rest-ms-vios-feed-media.json`), so the collision is visible before POST.

## Design

1. **Pre-POST refusal.** `create_optical_mapping`'s mutate callback, before appending, refuses
   when `target_device` is given and equals the text of a
   `VirtualSCSIMapping/TargetDevice/VirtualOpticalTargetDevice/TargetName` in the fetched
   `VirtualSCSIMappings`. It raises `HMCError(<conflict message>, 409)`; nothing is posted.
   Comparison is exact (AIX device names are case-sensitive), as `_disks_named` does for
   `create_virtual_disk`. With no `target_device` the HMC picks the name; no check runs.
2. **5xx guidance.** `_reconcile_storage_mutation` (and `_rmw_vios_mapping`, which forwards
   it) accepts `note: str | Callable[[HMCError], str]`; a callable receives the failed
   dispatch's `HMCError`. `create_optical_mapping` passes a callable that returns
   `_ADAPTER_SIDE_EFFECT` alone, or, when the error body contains the substring
   `name is already used in another mapping`, the conflict message followed by
   `_ADAPTER_SIDE_EFFECT`. Readback, the "may have a possible side effect. Do not retry until
   state is verified" text, the 5xx status, and the body are unchanged. String notes keep their
   behaviour, so every other storage write is unaffected. The `client_contracts.py` protocol
   declaration mirrors the widened type.
3. **Conflict message** (one helper, both paths): names the device (or "the target device" when
   the HMC chose it), says it is already mapped on the VIOS, and gives the remedies available
   today: unmount the media mapped to it first (`unmount-optical-media`), or name a different
   `target_device`; loading media into an existing device is not supported yet. No command that
   does not exist is named (#1285 is unbuilt).

No ADR: this extends ADR 0169's RMW and #1237's side-effect note without a new decision.

## Failure model

1. **Actors and deployments** — an operator or agent calling `mount-optical-media` /
   `hmc_mount_optical_media` against an HMC (V10R3 observed).
2. **Invariants and assets at stake**
   - a 5xx that may have left an orphan VIOS server adapter (#1085/#1237) must still report the
     readback and the adapter listing; augmentation never removes them.
   - the pre-POST refusal must not block a mount the HMC would accept: it fires only on an exact
     `TargetName` match among optical target devices.
3. **Accepted failure classes**
   - a non-optical target device (e.g. a `vtscsi`) with the same name is not pre-checked; the HMC
     still refuses it, and the 5xx path still adds the guidance when the body matches. Bounded:
     one failed write with the existing side-effect report; the issue scopes the check to
     optical devices.
   - different HMC wording for this 500 falls back to today's message. Bounded: one observation
     only; no capture exists (#1284).
   - a mapping created between the GET and the POST is not seen by the pre-check; the If-Match
     POST answers 412 or the HMC 500 path above applies.
4. **Covered elsewhere** — retry after 5xx (ADR 0169); concurrent VIOS writes (ADR 0079);
   physical optical devices; load/unload (#1285); live capture (#1284).

## Success

- Explicit `target_device` matching an existing optical `TargetName` → 409, no POST, message
  names the device and the remedies.
- 5xx with the collision body text → status unchanged, "possible side effect" text, readback,
  `_ADAPTER_SIDE_EFFECT`, plus the conflict message.
- 5xx without that text, and every other caller's 5xx → message unchanged.

## Validation

Unit tests in `tests/storage/test_mapping_rmw.py` with respx: the 409 refusal (POST route not
called), no refusal for a non-matching name and for `target_device=None`, the 5xx collision
body, and the existing 5xx tests unchanged.
