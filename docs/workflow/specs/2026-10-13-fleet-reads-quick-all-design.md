# Fleet-wide reads list systems from quick/All (#1501, part 3)

## Problem

Four fleet-wide reads start from the full `ManagedSystem` feed (`list_managed_systems`), which
timed out at 60–600 s on V11R2 SP1120 while a system was `No Connection` (#1290):
fleet health (`operations/systems/health.py`), owning-system discovery
(`operations/lpar/ownership.py`, ADR 0094), the capacity report and placement
(`operations/inventory/capacity.py`), and the utilization survey
(`operations/inventory/utilization.py`, ADR 0184). `ManagedSystem/quick/All` answered fast with
every system's `UUID`, `SystemName` and `State`.

## Decision

1. **Identity and state only — fleet health and owning-system discovery** read `quick/All`.
   Health takes each system's name and `State` from its summary and keeps its existing
   behaviour otherwise: a non-operating system is an issue, and its refused scoped feeds become
   warnings. Discovery takes the CLI name from the summary, and skips a system whose `State` is
   not `operating` before reading its partition feed, counting it, with its state, among the
   frames it could not read (ADR 0094 amendment).
2. **Full entry — capacity and utilization** list the systems from `quick/All` and read
   `ManagedSystem/{uuid}` and the scoped feeds only for a system that is operating and has a
   valid UUID (`unreadable_system`, made public from `fleet.py`, ADR 0197's rule). A system the
   summary lists but whose entry is absent fails the read.
   - Capacity leaves a non-operating system out with a logged warning, as it did when its
     partition feed refused. When every system is left out it raises an `HMCError` naming them.
     Its return type, `list[CapacitySummary]`, has nowhere to name the skipped systems in a
     partial report; adding that is a public-contract change and is decided with part 4.
   - Utilization keeps every system in the survey: a non-operating one gets a `SystemReading`
     with its name and `State`, every figure unknown, and the gap
     `ManagedSystem: not read, because the system is in State '<state>'` (ADR 0184 update).
3. Only reachability is keyed on `State`, case-insensitively; `ReferenceCode` is not read.

### Failure model

1. Actors: an agent or CLI user running fleet health, capacity, placement, the utilization
   report, or a selector-less DLPAR/power operation while a system has no connection.
2. Invariants: none of these reads the full `ManagedSystem` feed; none reads a non-operating
   system's `ManagedSystem/{uuid}`; discovery never reads a non-operating system's partition
   feed and still returns only on a positive UUID match.
3. Accepted failure classes: an operating system's own entry can still time out (one capture saw
   this); that fails the read as before. A system leaving `operating` between `quick/All` and its
   read fails capacity and utilization rather than being re-checked as ADR 0197 decision 4 does.
   A partial capacity report names its skipped systems only in the log.

## Success

Driven by `rest-ms-quick-all-no-connection-v11r2` with the full feed, the name search and a
disconnected system's entry stalled: health reports the two disconnected systems; discovery finds
an owner on an operating system, and names both disconnected systems when nothing owns the
partition, without reading their feeds; capacity reports exactly the nine operating systems; the
survey returns eleven readings, two of them unread with the state gap.
