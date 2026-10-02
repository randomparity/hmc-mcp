# Non-operating-system check in the per-system feeds — design (#1301)

Part of #1297. Charter: the `WORK:SCOPE` annotation on #1301 (token `q1301-6c506499`).

## Problem

A managed system in `recovery` or `no connection` answers
`ManagedSystem/{uuid}/LogicalPartition` and `.../VirtualIOServer` with HTTP 204
and no body (#1289). `HMCClient.list_logical_partitions(system_uuid)` and
`HMCClient.list_vios(system_uuid)` read that as "no partitions". PR #1295 added
`require_operating_system` (`client/client_resolution.py`) at four call sites
only: `operations/lpar/core.py:list_lpars`, `operations/vios/core.py:list_vios`,
`find_partition_by_name`, and `find_vios_by_name`. Every other scoped caller
still trusts the empty feed.

## Design

### Ownership move (criteria 1, 2)

The empty-feed state policy moves from four callers to its one owner: the two
client feed methods. When `system_uuid` is given and the parsed feed is empty,
each method calls `require_operating_system(self.get_managed_system,
system_uuid, "LPARs" | "VIOSes")` and returns the empty list only if the system
is operating. A non-empty feed returns without the system GET. The four #1295
call sites and the now-unused imports are deleted; their callers inherit the
check through the feed method, so an empty feed from an operating system costs
exactly one system GET on every path. The unscoped branch (`list_uom`) is
unchanged. No compatibility path is retained: these are internal call sites.

"Empty" means the parsed list is empty — a 204 and a 200 feed with no entries
are treated alike, matching the #1295 checks being replaced.

### Fleet paths (criterion 3)

"Not operating" on a fleet path is read from the `State` of the fleet's own
`list_managed_systems` entry, case-insensitively, as `health._system_issue`
already does. Only an `HMCError` from the LPAR feed of a system that is not
operating by that reading is absorbed; the same error from an operating
system keeps today's behaviour.

- **Fleet health** (`operations/systems/health.py`). A new
  `_lpars_or_warning(hmc, system_uuid, system_name, operating)` mirrors
  `_vios_or_warning`: for a non-operating system it returns no LPARs and the
  warning `"LPAR inventory for system <name> is unavailable: <error>"`,
  truncated to `_MAX_WARNING_LENGTH`; for an operating system it re-raises.
  `_system_inventory` takes `operating: bool` and returns its warnings as a
  tuple. The system stays in `systems` exactly once (from `_system_issue`); the
  warning records that its partitions were not inspected.
- **Capacity report** (`operations/inventory/capacity.py`).
  `fetch_capacity_report` leaves a non-operating system whose LPAR feed raises
  `HMCError` out of the returned list and logs a `logging` warning naming its
  UUID and the error. `CapacitySummary` has no warning or unreadable field, and
  adding one changes the MCP output schema, so omission is the representation
  the shape carries without a contract change (the issue delegates this
  choice). `find_placement` inherits it: a system that cannot run a partition
  is not a placement candidate. When systems exist but every one is omitted,
  the report raises the first omitted system's `HMCError` instead of returning
  an empty list, so no reader shows "No managed systems found" for an estate
  whose systems cannot be read. The `hmc_capacity_report` and
  `hmc_find_placement` tool descriptions state the omission. `docs/tools/`
  carries only each description's first line, so it does not change.
- **Composite system summary** (`operations/inventory/composite.py`): no code
  change. `_inventory_or_warning` already turns the `HMCError` into the
  warning `"LPAR inventory is unavailable: …"` with `lpar_count` `None`. This
  assumes the non-operating `ManagedSystem` document still carries the four
  figures `system_capacity` reads; no capture shows it either way, so the
  test's figures are assumed, and the operator's live verification confirms it.
- **Utilization survey** (`operations/inventory/utilization.py` `read_system`):
  no code change. `_read_feed` already records an `HMCError` as a gap, so a
  non-operating system's partition and VIOS figures read unknown, not zero.
- **Owning-system discovery** (`operations/lpar/ownership.py:139`): no code
  change. The existing `except HMCError` records the system in
  `skipped.unreadable` and the walk continues.

Considered and rejected:

- **A dedicated `SystemNotOperatingError(HMCError)` subclass** so fleet paths
  catch it precisely. judgment: a new error type in `hmcpctl.errors` for two
  call sites that can already distinguish the case from the fleet entry's
  `State`.
- **Skip the LPAR read for every non-operating fleet entry.** judgment: a
  `standby` system can still serve a non-empty partition feed; skipping would
  drop inventory that is readable today.
- **Absorb every LPAR-feed `HMCError` on health and capacity.** judgment: the
  health docstring and `test_core_inventory_error_propagates_without_partial_result`
  keep an operating system's feed failure fatal; the issue keeps that unless
  review decides otherwise.

## Failure model

1. **Actors and deployments**
   - An MCP client, CLI operator, or library caller against one HMC whose
     estate may include a system in `recovery` or `no connection`.
2. **Invariants and assets at stake**
   - A non-operating system's empty scoped feed is never returned as `[]`
     from either client feed method.
   - A fleet result (health, capacity, composite, discovery) is not aborted by
     a system that the fleet entry reports as not operating.
   - An operating system's feed failure on fleet health and capacity stays
     fatal (published behaviour).
3. **Accepted failure classes**
   - State race: the fleet entry says `operating` but the feed check sees
     `no connection`; health and capacity fail for that call. Bounded: one
     call, the error names the system and its state, and a retry reads fresh
     state.
   - One extra system GET per empty scoped feed from an operating system
     (stated in the issue's Expected section). If that GET fails, health and
     capacity fail for the call, the same as a failed feed read today.
   - `ambiguous_parent_details` (unscoped ambiguous-name diagnosis) now raises
     the not-operating `HMCError` instead of a `ValueError` naming each
     candidate's parent when a fleet system is not operating. The lookup fails
     either way. The one reader that branches on the type,
     `operations/lpar/core.py` `_create_and_read_back`, then takes its
     read-back-error path instead of propagating the `ValueError`.
   - Capacity omission is visible only in the log and the tool description;
     a structured warning needs a schema change outside this charter.
4. **Covered elsewhere**
   - Unscoped HMC-wide feeds while a system is `no connection`: #1293.
   - 204 shape and the full non-operating sweep: #1290.
   - Per-system I/O and SR-IOV feeds: out of scope.
   - Single-system fail-closed paths (ownership, decommission, templates):
     #1302. They inherit the client check as a consequence of criterion 1;
     their user-facing handling stays #1302's.
   - Live verification: operator (`verification:live-hmc`).

## Validation

Regression tests use an empty scoped feed (HTTP 204) and a `no connection` /
`Unknown` system document named `sys-R1`, values checked against
`tests/fixtures/live/vocabulary/v11r2-p10-9080-hex.json`, through a real
`HMCClient` over respx.

| Contract | Mode | Evidence |
| --- | --- | --- |
| Both client feed methods raise on an empty feed from a non-operating system | focused-test | `test_empty_feed_from_a_system_not_operating_raises` gains client-method reads |
| Empty feed from an operating system: one system GET on every path | focused-test | `test_empty_feed_from_an_operating_system_is_empty` asserts `call_count == 1` |
| Non-empty feed: no system GET | focused-test | `test_non_empty_feed_does_not_read_the_system`, extended to both client methods |
| Health warns and continues for a non-operating system | focused-test | new HTTP test plus a two-system test in `tests/system/test_fleet_health.py` |
| Health: operating-system feed error stays fatal | focused-test | existing `test_core_inventory_error_propagates_without_partial_result` |
| Capacity omits the non-operating system, keeps the rest | focused-test | new HTTP test |
| Capacity raises when every system is omitted | focused-test | new test |
| Capacity: operating-system feed error stays fatal | focused-test | new test |
| Composite surfaces an LPAR warning | focused-test | new HTTP test |
| Discovery records the non-operating system as unreadable | focused-test | new HTTP test: not-found error names `1 could not be read: <sys-R1 UUID>` |
| Tool descriptions name the capacity omission | task-test-not-applicable | prose on later description lines; `docs/tools/` renders only the first line, and no consumer validates the rest |
| Utilization survey records feed gaps, not zero partitions | focused-test | new HTTP test |
