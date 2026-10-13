# System identity resolution reads quick/All (#1501, part 1)

## Problem

The 2026-10-13 capture (#1290, V11R2 SP1120) saw the full `ManagedSystem` feed, the
`ManagedSystem/search/(SystemName==…)` search for a `No Connection` system, and that system's
`ManagedSystem/{uuid}` entry time out. `ManagedSystem/quick/All` answered fast and carries every
system's `UUID`, `SystemName` and `State`. Three identity reads still use the stalling endpoints:

- `resolve_system_uuid` (`resource_identity.py`) resolves a system name through the
  `SystemName` search. Most tools that accept a system name reach it; `get_system`,
  logical inventory and the LPAR plan call `find_system_by_name` directly, because they need the
  full entry, and move with the other full-entry readers in a later part.
- The unscoped ambiguous-name path of `find_partition_by_name` and `find_vios_by_name` reads the
  full `ManagedSystem` feed to find each candidate's parent system.
- The scoped ambiguous-name path of the same two finders reads `ManagedSystem/{uuid}` only to
  name the system in its error.

## Decision

1. `resolve_system_uuid` matches the name exactly against `quick/All`'s `SystemName`. One match
   returns its `UUID`. None raises the existing `ResourceNotFoundError`. More than one raises
   `ValueError("Ambiguous managed-system name …")` listing each distinct UUID, as
   `find_system_by_name` does; a row repeated with the same UUID is one match. A matching row
   whose `UUID` is not a non-empty string is refused as incomplete metadata.
   It does not consult `State`: resolution is identity only, and the caller's own read refuses a
   system that is not operating (#1500).
2. `ambiguous_parent_details` takes `quick/All` summaries. It skips a summary whose `State` is not
   `operating` (case-insensitive, `system_operating`) instead of reading its scoped feed. When a
   candidate is then left without exactly one parent, the error names the skipped systems the
   way `require_absence_provable` does. A summary that is not an object, or lacks a usable `UUID`
   or `SystemName`, is still refused as incomplete inventory metadata.
3. The scoped ambiguity path takes the system's `SystemName` from its `quick/All` summary
   (`summary_for`, extracted from `require_operating_system`).
4. `find_system_by_name`, `get_managed_system`, `list_managed_systems` and
   `system_name_from_uuid` are unchanged here; their callers are later parts of #1501.

### Failure model

1. Actors: an agent or CLI user naming a managed system, LPAR or VIOS while some system on the
   HMC has no connection.
2. Invariants: a system name resolves to exactly one UUID or fails; an ambiguous partition or
   VIOS name never resolves to one candidate; no path here reads the full `ManagedSystem` feed,
   the `SystemName` search, or `ManagedSystem/{uuid}`.
3. Accepted failure classes: a `quick/All` read failure propagates its `HMCError`, where the
   search's failure did before. The exact-match comparison is done locally, so its case and
   whitespace semantics are Python string equality; the HMC search was documented as exact match.
   A candidate partition on a system that is not operating, `Standby` and `Power Off` included, is
   reported as unplaced with that system named as not read, rather than placed, as the fleet
   listings (ADR 0197) treat such a system.

## Success

1. A name in the committed `rest-ms-quick-all-no-connection-v11r2` capture resolves to its UUID,
   for an operating and a `no connection` system, with every stalling route unread.
2. An unscoped ambiguous LPAR or VIOS name reads only the operating systems' scoped feeds and
   names each candidate's parent; a candidate left unplaced names the two `no connection`
   systems.
3. A scoped ambiguous name names its system from `quick/All` without `ManagedSystem/{uuid}`.
