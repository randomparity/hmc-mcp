# V10R3 write-document schema conformance (#961)

## Problem

V10R3 validates UOM write bodies against its schema. Once #935 removes the 406, the builders in
`src/hmcpctl/documents/` fail that validation with HTTP 400 `REST0001`. The failures are wrong
`kb` values (every element writes `CUD`), wrong wrapper attributes, and wrong structure or child
order. For LPAR create, a 400 also means the `mksyscfg` fallback, which only a 406 triggers,
never runs.

## Evidence

Two sources, both observed on a V10R3 M1060 HMC. The design uses no documentation-derived
values.

- **F**: `tests/storage/vscsi_mapping_v10r3.xml`, a redacted live `VirtualSCSIMappings` response
  (#940).
- **I**: the live values and 400 messages recorded in the #961 body.

| Builder element | Change | Source |
|---|---|---|
| `AdapterType` | `kb="ROR"` | F, I |
| `VirtualSlotNumber` (vSCSI, vFC, network) | `kb="COD"` | F, I |
| `RemoteLogicalPartitionID`, `RemoteSlotNumber` | `kb="CUR"`, `kb="CUA"` | F, I |
| `PortVLANID`, `MACAddress` | `kb="CUR"` | I |
| `VirtualSwitchID`, `VswitchID`, `OperatingSystemType` | `kb="ROR"` | I |
| `NetworkVLANID`, `TaggedNetwork` | `kb="COD"` | I |
| `VolumeName`, `DiskName`, `DiskCapacity`, `MediaName` | `kb="CUR"` | F (disk), I |
| `PendingBootString` (`boot.py`) | `kb="UOO"` | I |
| `VirtualDisk`, `PhysicalVolume`, `VirtualOpticalMedia`, `VirtualSCSIMapping` | only `schemaVersion="V1_0"` (no `kb`/`kxe`) | F, I |
| `Storage` | `kb="CUR" kxe="false"`; no `schemaVersion`, no `Metadata` child | F, I |
| `DiskCapacity` / `DiskName` | `DiskCapacity` first | F, I |
| `TargetDevice` | `kb="CUR" kxe="false"`, wrapping `<LogicalVolume\|PhysicalVolume>VirtualTargetDevice` or `VirtualOpticalTargetDevice` with `Metadata` and `TargetName kb="CUR"`. It no longer carries the name as text | F for the LogicalVolume case; the PhysicalVolume and optical element names are inferred from F's shape and the names used in the repo's test feeds |
| `AssociatedLogicalPartition` | UOM namespace (not Atom), `kb="CUR" kxe="false"`, first child of the mapping | F |
| `PartitionMemoryConfiguration` | add `schemaVersion="V1_0"` | I |

**Invariant (F):** every element in F that has a `Metadata` child carries `schemaVersion`, and
`Storage`/`TargetDevice`, which have no `Metadata`, do not. The memory 400 is this rule broken.
The three processor-configuration wrappers break it too, but no evidence records their
requirement, so they stay unchanged. They are listed as unverified, and the invariant test
exempts them by name.

## Design

- Edit the literal attributes in place in `adapters.py`, `storage.py`, `lpar.py` and `boot.py`.
  Add no per-resource table in `src`, because each value is written once. The tests hold the
  expected table.
- `_adapter_document` takes the `kb` for each remote field from its caller. vSCSI passes
  `CUR`/`CUA`. vFC keeps `CUD` for `ConnectingPartitionID`/`ConnectingVirtualSlotNumber`,
  because no live value is recorded for them.
- The two mapping builders share one private `_mapping_document(storage_xml, target_xml,
  lpar_link)`. It renders the child order `Metadata`, `AssociatedLogicalPartition`, `Storage`,
  `TargetDevice`. A private `_target_device(element, name)` renders the typed target.
- `build_virtual_disk_delete_document`'s `VirtualDisk` gets the same attribute correction.
- Elements with no recorded live value keep their current attributes. Examples:
  `IsTaggedVLAN`, `GroupName`, `NetworkName`, the vFC `Connecting*` fields, the LPAR resource
  fields, the processor-configuration wrappers, and `AssociatedSwitch`. They are listed in the
  PR as unverified. An element written today without `kb`, such as `MediaName` and `GroupName`
  in the delete documents, stays without it.
- The 400 path already works: `_write_uom` raises `HMCError(..., resp.text)`, and `HMCError`
  reports the `<Message>` text. A test pins that behavior. No `src` change is needed.

Rejected: **emitting no `kb`/`kxe` at all**. judgment: the attributes appear to be optional,
but only the HMC-valued documents are proven accepted (the 200 in #961), and the charter asks
for the V10R3 values.

## Failure model

1. Actors and deployments: an operator or MCP client drives `hmcpctl` against a V10R3 HMC,
   and CI runs the unit tests offline.
2. Invariants: a write the HMC accepts must not change meaning. Structure only changes; no
   value the caller supplies is dropped or reinterpreted. The existing escaping decorator and
   the `storage_kind` allowlist still guard every element name and value.
3. Accepted:
   - Values evidenced only by I, and the PhysicalVolume/optical target-device element names
     inferred from F, are unproven by a 200 until #879 runs. The cost is bounded: the result is a 400 with the
     HMC's message, as today after #935.
   - Elements with no recorded value stay unchanged and may still draw a 400. The PR lists
     them.
   - Whether V10R3 puts the schema detail in `<Message>` is unobserved. The test uses the
     repo's existing `HttpErrorResponse` shape.
4. Covered elsewhere: header negotiation (#935); live proof (#879); sparse-document
   read-modify-write for mappings (#962); disk-name length (#964).

## Success

1. Each builder element in the Evidence table carries exactly the listed attributes and
   order. Unit tests read F directly for every element F contains, and use a
   table transcribed from I for the rest.
2. Every element that has a `Metadata` child carries `schemaVersion`, for each builder call in
   the invariant test's parametrization (every public builder in the four files, at least once),
   except the three named processor-configuration wrappers.
3. A PUT answered with 400 `REST0001` raises an `HMCError` whose text contains the HMC
   `<Message>`.
4. Existing tests that asserted the old `CUD` literals are updated. `just verify` passes.
