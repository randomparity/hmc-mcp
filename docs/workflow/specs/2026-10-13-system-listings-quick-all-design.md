# System listings and full-entry readers use quick/All (#1501, part 4)

## Problem

The last readers of the full `ManagedSystem` feed, the `SystemName` and `State` searches, and a
`No Connection` system's `ManagedSystem/{uuid}` all stall on V11R2 SP1120 (#1290):

- `hmc_list_systems` / `systems list` (full feed, or the `State` search);
- `hmc_get_system` / `systems show` (the name search or the entry);
- the logical inventory (`hmc_inventory`) and the LPAR plan (`hmc_plan_lpar`): they enumerate
  with the full feed, and resolve selectors with the name search or the entry;
- the system summary, snapshot capture, the VIOS-backup MTMS lookup and the memory bound, which
  each read one system's entry.

## Decision (operator, 2026-10-13: B, C, sparse entry)

1. Every system read starts from `ManagedSystem/quick/All`. `summary_matching`
   (`resource_identity.py`, extracted from `resolve_system_uuid`) selects a summary. A UUID
   matches case-insensitively, and a name matches `SystemName` exactly. A name shared by
   distinct systems raises the existing ambiguity `ValueError`.
2. `system_entry` (`operations/systems/fleet.py`) returns an operating system's full entry from
   `ManagedSystem/{uuid}` and any other system's **sparse entry**. The sparse entry has the
   parsed envelope (`UUID`, `title` and `link` null, `ResourceType: "ManagedSystem"`), and its
   `Resource` holds only `SystemName`, `State` and `StateDetail`. An operating system whose
   entry reads back empty also gets the sparse entry.
3. `hmc_list_systems` keeps `list[dict]`: one entry per `quick/All` row. `state` filters the
   summaries' `State` exactly, locally; the `State` search is gone. `limit` is unchanged.
   `hmc_get_system` returns the same entries, or None when nothing matches. ADR 0197 is amended
   to say why sparse entries suit system listings.
4. The logical inventory and the plan:
   - Both take candidates from `quick/All`.
   - The inventory reads a candidate's entry when it reads that candidate's page, and not after
     a transport stall.
   - The plan reads entries for the 16 candidates it keeps. Its existing
     `system_not_operating` blocker reads the sparse entry's `State`.
5. Readers that cannot work from a sparse entry refuse a non-operating system promptly through
   `operating_system_entry`: "Cannot read <what> of managed system '<name>' (<uuid>): it is in
   State '<state>'; retry when it reconnects". These are the system summary, snapshot capture
   and the VIOS-backup MTMS lookup. A system `quick/All` does not list reads as not found, as a
   missing entry did.
6. The memory bound reads `read_system_entry`, so a non-operating system's sparse entry leaves
   the bound unchecked. That is its documented behaviour for an entry without the figure.
7. `list_managed_systems` has no caller left and is removed with its ADR 0138 fallback.
   `get_managed_system` keeps its fallback, now reached only for operating systems (ADR 0138
   amendment).
8. C: the capacity report and placement are unchanged from part 3.

### Failure model

1. Actors: an agent or CLI user listing, showing, inventorying, planning, summarizing,
   snapshotting or backing up while a system has no connection.
2. Invariants: none of these reads the full `ManagedSystem` feed, the `SystemName` or `State`
   search, or a non-operating system's `ManagedSystem/{uuid}`. A listed system is never dropped
   from `hmc_list_systems`.
3. Accepted failure classes:
   - An operating system's own entry can still time out; that read fails as before.
   - `hmc_list_systems` now makes one entry read per operating system where it made one feed
     read.
   - A sparse entry lacks every detail field. Callers that read `MachineTypeModelAndSerialNumber`
     or IP addresses see them missing for exactly the systems the HMC cannot read.
   - The `state` filter is exact string equality on `quick/All`'s `State`.
   - The plan reads its candidates' entries in one step, so an error on one operating
     system's entry fails the enumeration, as a failed feed read did before.

## Success

Driven by `rest-ms-quick-all-no-connection-v11r2`, with the full feed, every search and a
disconnected system's entry stalled:

- `list_systems` returns eleven entries, and the two disconnected ones are sparse. Filtering on
  `no connection` reads no entry.
- `get_system` returns the sparse entry by name or upper-case UUID, and the full entry for an
  operating system.
- The summary and the VIOS-backup lookup refuse with the state.
- The memory bound skips a disconnected system and still refuses an oversize request on an
  operating one.
- The inventory and the plan enumerate all eleven systems, reading only the operating systems'
  entries.
