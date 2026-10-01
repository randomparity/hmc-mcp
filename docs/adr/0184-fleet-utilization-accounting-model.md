# ADR 0184: Fleet utilization accounts allocation from current configurations

## Status

Accepted on 2026-10-01 for issue #1252 (slice 1). #1253 and #1254 extend the same survey.

## Context

`hmcpctl report utilization` must show leadership how much CPU and memory each managed system
has allocated, to whom, and how much sits idle in partitions that are defined but not running.
`capacity.py` reports only configurable minus available, which folds the hypervisor, the VIOS
and idle partitions into one "assigned" figure.

A read-only capture on 2026-10-01 over the configured HMC profiles that answered (V10R3 and
V11R2, POWER9 to POWER11; private evidence, public-safe summary here) established:

- The `LogicalPartition` feed omits VIOS partitions; `VirtualIOServer` carries them with the same
  `PartitionMemoryConfiguration` and `PartitionProcessorConfiguration` fields.
- A not-activated partition with an applied configuration reports its size in `CurrentMemory`
  and `CurrentProcessors` or `CurrentProcessingUnits`, and only 0 or 256 in its `Runtime*`
  figures. A never-applied one reports 0 everywhere, and its size exists only in its profile.
- On every reachable system, the sum of all partitions' `CurrentMemory`, VIOS included, equals
  the system's `CurrentAssignedMemoryToPartitions`. Configurable minus available processor units
  equals the partitions' summed current units on all but two systems, which differ by 1 and 2
  units. So the hypervisor reserves the current configuration of a not-activated partition.
- Configurable minus available minus `MemoryUsedByHypervisor` minus assigned-to-partitions is
  non-zero on some systems (up to 200704 MiB). That remainder is reserved by something the
  partition feeds do not name.
- Per-HMC failures happen: the `VirtualIOServer` feed returns HTTP 500 ("Error occurred while
  querying for ViosStorage") on two HMCs, and the `ManagedSystem` feed timed out at 180 s on two
  others.

## Decision

1. Allocation is read from current configurations: `CurrentMemory`, plus `CurrentProcessors`
   for a partition whose `CurrentHasDedicatedProcessors` is `true`, otherwise
   `CurrentProcessingUnits`. `Runtime*` figures are not used.
2. Each system's configurable capacity splits into: VIOS; active client (any client state
   other than `not activated`); idle reserved (`not activated` with non-zero current
   configuration); hypervisor (memory only, `MemoryUsedByHypervisor`); other reserved (the
   remainder); and free (`CurrentAvailable*`). Memory's other reserved is computed against
   `CurrentAssignedMemoryToPartitions`, so a failed VIOS feed leaves only the VIOS figure
   unknown. CPU's remainder needs every partition's units, so it is unknown when either feed
   fails.
3. A never-applied partition's profile claim is read from its `LogicalPartitionProfile` feed,
   selecting the profile its `AssociatedPartitionProfile` link names. It is reported beside the
   reserved figures and never added to them, because the hypervisor holds nothing for it.
4. A figure the HMC did not report, or that a failed read prevented, is unknown, never 0. A
   roll-up counts only systems whose figures are all known, and says how many systems it counted.
5. Fleet totals count a managed system once per machine type-model-serial. The reading with the
   fewest unknown figures wins; ties go to the first profile name. Each HMC roll-up still counts
   every system its HMC manages.

## Consequences

- Idle reserved capacity is real hypervisor allocation, so it shows as allocated, not free.
- A system whose VIOS feed fails still contributes memory figures but drops out of the CPU and
  fleet-complete counts; the CSV names the failed read.
- The two systems whose CPU remainder is non-zero show it as other reserved rather than hiding it.

## Considered & rejected

- **Read `Runtime*` figures.** verified: on 2026-10-01 not-activated partitions reported
  `RuntimeMemory` 256 while the system counted their full `CurrentMemory` as assigned, so runtime
  sums under-report allocation (one system: 1344256 MiB runtime vs 7634944 MiB assigned).
- **Keep configurable minus available as one "assigned" figure.** judgment: fit; it is the
  figure #1252 says cannot separate firmware, VIOS and idle partitions.
- **Add profile claims into the idle figure.** verified: on 2026-10-01 never-applied partitions
  reported 0 current memory and the system's assigned total matched without them, so the
  hypervisor reserves nothing for them.
- **Sum known values and ignore unknown systems silently.** judgment: fit; #1252 forbids
  under-reporting through dropped systems.
- **First-profile-wins dedup.** judgment: fit; the operator chose the most complete reading.
