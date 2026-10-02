# Unscoped partition, VIOS and ownership listings read system by system

Issue #1293. Decision record: [ADR 0197](../../adr/0197-unscoped-listings-name-unreadable-systems.md).

## Problem

Without a system, `list_lpars`, `list_vios` and `list_lpar_ownership` read the HMC-wide
`LogicalPartition` / `VirtualIOServer` feeds, or `VirtualIOServer/search/(PartitionState==…)`.
On an HMC where one system was `No Connection`, every one of those reads timed out after 180 s.
The same system's scoped feeds answered within a second.

## Design

**Fleet reader.** A new module, `src/hmcpctl/operations/systems/fleet.py`, owns the unscoped
read:

- `UnreadableSystem(system_name, system_uuid, state, detailed_state)`, a frozen dataclass. Every
  field is `str | None`, taken from the `ManagedSystem` entry's `Resource`/`UUID`; a value that
  is not a string becomes `None`.
- `FleetListing(entries: list[dict[str, Any]], unreadable_systems: list[UnreadableSystem])`, a
  frozen dataclass.
- `async read_fleet(hmc, read_system, resources) -> FleetListing` works in four steps:
  1. Call `hmc.list_managed_systems()`.
  2. If it returns more than `MAX_PARENT_DISCOVERY_SYSTEMS` systems, raise `ValueError("Cannot
     list {resources} across managed systems: discovery exceeds 100 managed systems; supply
     managed-system scope")`.
  3. In feed order, record a system as unreadable when its UUID is not a non-empty string or its
     `State`, stripped and case-folded, is not `operating`.
  4. Otherwise `await read_system(uuid)` and extend `entries` with the result. An exception from
     that read propagates unchanged.

**Operations.** Each operation resolves the selector exactly as it does today.

- *Scoped:* `FleetListing(<scoped read>, [])`.
- *Unscoped:* `read_fleet` with `hmc.list_logical_partitions` or `hmc.list_vios`.
- `list_lpars` and `list_vios` then filter `entries` by `state` locally. `list_vios` loses its
  `search_uom` branch.
- `list_lpar_ownership` maps `lpar_ownership_entry` over `entries`.

**Client.** `list_logical_partitions` and `list_vios` take a required `system_uuid: str`. The
unscoped `list_uom` branch goes, and the two protocol declarations in `client_contracts.py` and
`SurveyClient` (`operations/inventory/utilization.py`) change with it. Every caller already
passes a UUID; `ty` proves it.

**Tools.** `hmc_list_lpars`, `hmc_list_vios` and `hmc_list_lpar_ownership` return `FleetListing`,
which FastMCP serializes and describes in its output schema, as it does for `hmc_capacity_report`.
`limit` is checked as `run_limited_collection` checks it (a negative value raises `ValueError`)
and then applied with `dataclasses.replace(listing, entries=listing.entries[:limit])` in a
private helper in `server_tools/systems/core.py`. Docstrings name the `unreadable_systems`
field. The `hmc_list_vios` docstring and the CLI `--state` help stop saying "server-side".

**CLI.** `lpar list` and `vios list` build their tables from `entries`. `--json` prints
`dataclasses.asdict(listing)`. A new `output.report_unreadable(listing)` prints one yellow
stderr line per unreadable system, in both modes:
`Skipped managed system <name> (<uuid>): State <state>`.

**Docs.** Regenerate `docs/tools/` (`just tool-docs`). Add a CHANGELOG `### Changed` entry. Point
the kdive Tier A page's ownership row at `FleetListing.entries`.

## Failure model

1. **Actors and deployments**
   - an MCP agent or CLI operator listing partitions on one HMC, unattended or interactive
   - kdive importing `list_lpar_ownership` (pre-release, pinned commit)
2. **Invariants and assets at stake**
   - a listing never presents a skipped system's absence as "no partitions": every skipped
     system appears in `unreadable_systems`
   - the published tool and operations return shape (ADR 0197)
3. **Accepted failure classes**
   - Slow `ManagedSystem` feed (about 160 s live): the cost is bounded by `HMC_TIMEOUT`, and
     speed is excluded (untracked, operator 2026-10-02).
   - A system that stops operating between the feed read and its scoped read: the call fails,
     naming the state (#1301), and a retry lists the system as unreadable.
   - An operating system whose scoped read raises (transport timeout, 500): the call fails, as
     it did before. Degrading those errors is outside the criteria (ADR 0197 rejected list).
4. **Covered elsewhere**
   - whether the HMC-wide feed is ever a safe fast path: #1290
   - the unscoped `search_uom` in `find_partition_by_name` / `find_vios_by_name`: adjacent note,
     untracked
   - non-operating single-system reads: #1302; the scoped-feed refusal: #1301

## Success

1. An unscoped listing from `hmc_list_lpars`, `hmc_list_vios` (with or without `state`) or
   `hmc_list_lpar_ownership` sends no request to `/rest/api/uom/LogicalPartition`,
   `/rest/api/uom/VirtualIOServer`, or a path under `/rest/api/uom/VirtualIOServer/search/`.
2. With sys-R1 non-operating and sys-E2 operating, the result holds sys-E2's entries, and
   `unreadable_systems` names sys-R1 with its UUID, State and DetailedState.
3. A fleet of 101 systems raises the bound error before any scoped read.
4. `limit` truncates `entries` only. A scoped call returns `unreadable_systems == []`.

## Validation

| Contract | Mode | Evidence |
|---|---|---|
| `read_fleet` skip, bound, propagate | focused-test | new `tests/unit/test_fleet_listing.py` |
| unscoped tool requests and envelope (S1, S2) | focused-test | respx routes for the HMC-wide paths assert zero calls; `tests/system/test_system_tools.py`, `tests/lpar/test_lpar_ownership_bulk.py` |
| `limit` and scoped shape (S4) | focused-test | `tests/app/test_collection_limits.py` |
| CLI JSON envelope and stderr line | focused-test | `tests/app/test_cli_commands.py` |
| client signature narrowing | focused-test | `just typecheck` plus the updated client tests |
| generated tool docs | focused-test | `just tool-docs-check` |
| CHANGELOG and kdive page prose | task-test-not-applicable | prose, and `test_kdive_tier_a_contract` resolves the page's names |

Live verification on a `No Connection` system is pending (label `verification:live-hmc`). It is
not run here.
