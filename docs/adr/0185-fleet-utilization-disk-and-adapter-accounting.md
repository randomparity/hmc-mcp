# ADR 0185: Fleet utilization reads disk and adapter occupancy from documents it already fetches

## Status

Accepted on 2026-10-01 for issue #1253 (#1252 slice 2). Extends ADR 0184; its unknown-never-zero,
per-figure roll-up and MTMS dedup rules apply unchanged to the figures added here.

## Context

#1253 adds VIOS physical-volume capacity (internal vs SAN; assigned vs free) and I/O slot and
SR-IOV occupancy to `hmcpctl report utilization`. The survey reads only REST feeds, and the SSH
`lshwres` listings that report slots and SR-IOV ports need a second credential path per HMC.

The read-only sweep of 2026-09-30 (V10R3 with POWER9; V11R2 with POWER9, POWER10 and POWER11;
private evidence, public-safe counts here) established:

- The `ManagedSystem` entry the survey already reads carries
  `AssociatedSystemIOConfiguration/IOSlots/IOSlot` and `SRIOVAdapters/IOAdapterChoice/SRIOVAdapter`.
  On all five systems captured both ways, its slot counts (empty by `Description` `Empty slot`;
  assigned by a present `PartitionID`) equal `lshwres -r io --rsubtype slot` exactly. The seven
  `initializing` system entries carried neither container.
- For every one of 386 captured `Sriov`-mode adapter entries, `MaximumLogicalPortsSupported` minus the
  count of `UnconfiguredLogicalPorts/SRIOVUnconfiguredLogicalPort` equals the sum of the
  physical ports' `Configured*LogicalPorts`. An SR-IOV adapter's `AdapterID` equals its slot's
  `SlotDynamicReconfigurationConnectorIndex`; that slot lists no `PartitionID`.
- In 943 `PhysicalVolume` entries, `AvailableForUsage` `true` (71) never named a disk a vSCSI
  mapping uses; `false` covered every mapped disk and 746 that no vSCSI mapping used. In reads without
  a `group` filter, a running, RMC-active VIOS always carried `PhysicalVolumes`; a not-activated
  or RMC-inactive one never did.
- Every disk listed twice in one system was an FC LUN seen by two VIOS with one
  `UniqueDeviceID` and one capacity; `AvailableForUsage` disagreed in 12 of 30 such pairs.

## Decision

1. Adapter figures come from the `ManagedSystem` entry and disk figures from the
   `VirtualIOServer` feed the survey already reads. The survey adds no HMC read and no SSH.
2. A slot is empty when its `Description` is `Empty slot`; otherwise assigned when it lists a
   `PartitionID`, SR-IOV when an `Sriov`-mode adapter's `AdapterID` is its DRC index, else
   unassigned. Slot utilization is assigned plus SR-IOV over occupied slots.
3. SR-IOV figures count `Sriov`-mode adapters, their `MaximumLogicalPortsSupported` (capacity)
   and their unconfigured logical ports (free). Utilization is configured over capacity.
4. A physical volume counts once per system by `UniqueDeviceID`; it is SAN when
   `IsFibreChannelBacked` or `IsISCSIBacked` is `true`, otherwise internal; free when every VIOS
   listing it reports `AvailableForUsage` `true`, assigned when any reports `false`. Capacity is
   `VolumeCapacity` in MiB. Disk utilization is assigned over total.
5. A missing container is unknown with a named gap: no `PhysicalVolumes` on a VIOS makes the
   system's disk figures unknown; no `IOSlots` or no `SRIOVAdapters` makes the figures that need
   it unknown. A present, empty container is a known 0.

## Consequences

- One stopped VIOS hides its system's disk figures; the roll-up names the shortfall.
- An empty slot still assigned to a partition counts as empty: the issue counts empty slots
  whatever their assignment.
- "Assigned" disk is whatever the VIOS reports unavailable for use, not only mapped disks.

## Considered & rejected

- **Read slots and SR-IOV with SSH `lshwres`.** verified: on the five systems captured both ways
  the REST counts equal `lshwres`; SSH would add a second credential path and per-system logins.
- **Classify assigned disks by vSCSI mappings.** verified: 746 of 943 captured volumes were
  unavailable without any vSCSI mapping on their VIOS, so mappings under-report use.
- **Sum the VIOS that reported storage.** judgment: fit; it under-reports a system silently,
  which ADR 0184 decision 4 forbids.
- **Configured SR-IOV ports from `Configured*LogicalPorts`.** judgment: complexity; three
  port-type containers for a count that capacity minus unconfigured already gives.
