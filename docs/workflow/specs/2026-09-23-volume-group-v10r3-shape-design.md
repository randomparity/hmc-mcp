# VolumeGroup create document matches V10R3 (#1001)

## Problem

`create_volume_group` PUTs the document from `build_volume_group_document`. Its shape was
never compared with a live V10R3 `VolumeGroup`. A read-only V10R3 capture (2026-09-23) shows
`GroupName` carries `kb="CUR"`; the builder sends `kb="CUD"`. The other elements the builder
emits (`VolumeGroup`, `Metadata`, `GroupName` before `PhysicalVolumes`, `PhysicalVolumes`,
`PhysicalVolume`, `VolumeName`) already match the capture in order and attributes.

## Scope

- `src/hmcpctl/documents/storage.py`: `GroupName` `kb` becomes `CUR`. No other builder change.
- `tests/storage/volume_group_v10r3.xml`: one redacted live `VolumeGroup` entry, following
  the `vscsi_mapping_v10r3.xml` precedent (#940).
- `tests/unit/test_documents_v10r3.py`: a structural test against that fixture.
- `tests/storage/test_storage_tools.py`: the existing create-body assertion pins
  `GroupName kb="CUD"`; it changes to `CUR` (a reader of the changed wire field).
- `CHANGELOG.md`: one `Fixed` entry (the wire document changes).
- No ownership transition: the builder stays the single owner of the create document.

### Failure model

1. Actors and deployments: an operator or agent calling `hmc_create_volume_group` against a
   V10R3 HMC.
2. Invariants and assets at stake: the VIOS disks named in the create; a rejected PUT writes
   nothing, so the cost of a wrong shape is a failed create, not data loss.
3. Accepted failure classes: V10R3 may reject the create for a reason a read cannot show
   (a required create-only element absent from GET responses, or a GET-visible `kb="CUR"`
   element the builder omits, such as `GroupCapacity` or `VolumeCapacity`); accepted because the
   charter scopes shape to emitted elements and only a live create shows what it requires (#879).
4. Covered elsewhere: live acceptance on a disposable disk → #879; If-Match → #996 (not
   applicable to a create).

## Success

1. Every element the builder emits appears in the fixture's `VolumeGroup` in the same relative
   order (child-name subsequence at each level the builder emits; repeated `PhysicalVolume`
   siblings collapse to one before comparing, and each is checked against the fixture's one).
2. Each emitted element's `kb`, `kxe` and `schemaVersion` equal the fixture element's.
3. The fixture holds no lab identifiers (UUIDs, names, serials, location codes, device IDs).

## Validation

- Success 1-2, `Mode: focused-test`: `test_volume_group_create_matches_fixture` in
  `tests/unit/test_documents_v10r3.py`; red before the fix on `GroupName` (`CUD` != `CUR`);
  built with two physical volumes; green with
  `uv run --no-sync pytest tests/unit/test_documents_v10r3.py tests/storage/test_storage_tools.py -q`.
- Success 3, `Mode: task-test-not-applicable`: redaction is a property of committed data, not
  code behaviour; checked before commit by an `rg` scan of the fixture for UUID, 32-hex,
  U-code, FQDN and private-address shapes, and by reading it.
