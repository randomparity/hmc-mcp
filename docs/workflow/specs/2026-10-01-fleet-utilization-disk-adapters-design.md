# Fleet utilization disk and adapter columns design (#1253)

## Problem

`hmcpctl report utilization` (#1252 slice 1, [design](2026-10-01-fleet-utilization-report-design.md))
reports CPU, memory and partitions only. #1253 asks for VIOS disk capacity and I/O slot and
SR-IOV occupancy per system and in the HMC and fleet roll-ups.

## Scope

Governing decisions: [ADR 0184](../../adr/0184-fleet-utilization-accounting-model.md) (unknown
never 0, per-figure roll-ups, MTMS dedup) and
[ADR 0185](../../adr/0185-fleet-utilization-disk-and-adapter-accounting.md) (field sources and
classification). No new HMC read, SSH call or MCP tool.

Out of scope, with owners: measured consumption (#1242); the agent-facing inventory tool (#1220);
MCP exposure (maintainer); committing generated reports (never); HTML output (#1254); new HMC
reads or command families (out of scope).

### Survey operation (`src/hmcpctl/operations/inventory/utilization.py`)

- `DiskFigures` (MiB, `int`): `internal_total`, `internal_assigned`, `internal_free`,
  `san_total`, `san_assigned`, `san_free`.
- `AdapterFigures` (`int` counts): `slots_assigned`, `slots_unassigned`, `slots_sriov`,
  `slots_empty`, `sriov_adapters`, `sriov_logical_ports`, `sriov_logical_ports_free`.
- `SystemReading` and `Rollup` gain `disk` and `adapters`; `Rollup` gains `disk_util_pct`,
  `slots_util_pct` and `sriov_util_pct`. `FIGURE_GROUPS` maps each group name (`cpu`, `memory`,
  `partitions`, `disk`, `adapters`) to its dataclass; `unknown_figures`, `rollup` and the CSV
  iterate it, so #1254 renders the same groups.
- `capacity_pairs(reading)` returns `{"cpu", "mem", "disk", "slots", "sriov"}` →
  `(capacity, free)`: disk total and free (internal plus SAN), occupied slots and unassigned
  slots, SR-IOV logical ports and free ports. Each `*_util_pct` is `utilization_pct` over the
  pair, pooled in a roll-up over the readings reporting both, as CPU and memory are.
- A container is present when its key is in the parsed parent mapping; a present container
  with no entries (a self-closed element parses as `''`) has zero items.
- Disk figures: per ADR 0185 decision 4, from each VIOS entry's `PhysicalVolumes`. A failed VIOS
  feed leaves them unknown with the existing gap. Each VIOS without `PhysicalVolumes` adds gap
  `PhysicalVolumes: VIOS <name> (<state>) reported no storage` and makes them unknown. A volume without
  `UniqueDeviceID` counts per VIOS, with gap `PhysicalVolumes: VIOS <name> lists a volume without
  UniqueDeviceID; it is not deduplicated`. A volume missing `VolumeCapacity`, the backing flags or
  `AvailableForUsage` makes the figures it feeds unknown. A system with no VIOS reports 0.
- Adapter figures: per ADR 0185 decisions 2, 3 and 5, from the system's
  `AssociatedSystemIOConfiguration`. Without `IOSlots`, the four slot figures are unknown; without
  `SRIOVAdapters`, the three SR-IOV figures and `slots_sriov` and `slots_unassigned` are unknown.
  Each missing container adds a gap naming it. An `Sriov`-mode adapter whose
  `UnconfiguredLogicalPorts` is absent has 0 free ports when its capacity is 0, else unknown.

### CSV (`src/hmcpctl/cli_commands/report.py`)

`COLUMNS` keeps its order and appends, after `notes`: `disk_internal_total_mib`,
`disk_internal_assigned_mib`, `disk_internal_free_mib`, `disk_san_total_mib`,
`disk_san_assigned_mib`, `disk_san_free_mib`, `disk_util_pct`, `slots_assigned`,
`slots_unassigned`, `slots_sriov`, `slots_empty`, `slots_util_pct`, `sriov_adapters`,
`sriov_logical_ports`, `sriov_logical_ports_free`, `sriov_util_pct`. System and roll-up rows fill
them; failure rows leave them empty. A roll-up's shortfall note names these columns as it does
the others.

### Documentation

`docs/cli.md` describes each new column, including that disk figures cover only VIOS-reported
volumes, and its summary lines and the command help name disk and adapter occupancy.
`CHANGELOG.md` records the addition.

## Failure model

1. **Actors and deployments**: the operator running the command on a workstation that reaches the
   configured HMCs. No MCP surface, no CI job.
2. **Invariants and assets at stake**:
   - Read-only: the survey's request list is unchanged (GETs only).
   - No unknown figure renders 0; every gap that makes a figure unknown is named in `notes`.
   - A disk seen through two VIOS of one system counts once; a system two HMCs manage counts once
     in the fleet (ADR 0184 decision 5).
   - Existing CSV columns keep their names and positions.
3. **Accepted failure classes**:
   - A LUN zoned to VIOS of two systems counts once per system, so HMC and fleet SAN totals
     over-state SAN shared across systems (ADR 0185 consequences).
   - Disk figures cover only VIOS-reported volumes; NPIV and client-owned disks are absent.
   - "Assigned" disk is the VIOS's own unavailable-for-use judgment (ADR 0185 consequences).
   - Slot and SR-IOV shapes were captured on operating and initializing systems only; other
     states take the missing-container path.
4. **Covered elsewhere**: credentials and TLS (`load_profile`, ADR 0096); keeping reports private
   (operator, doc warning); REST read paths (`just live-vocabulary`).

## Success

- A fixture system with two VIOS sharing one SAN LUN reports that LUN once, assigned when one VIOS
  says `AvailableForUsage` `false`.
- A VIOS without `PhysicalVolumes`, a system without `IOSlots`, and a failed VIOS feed each leave
  only their figures `unknown`, with a named gap; an empty or self-closed container reports 0.
- A system two profiles read contributes its disk and adapter figures once to the fleet row.
- Slot classes and SR-IOV figures match ADR 0185 for a fixture with each slot class.
- The CSV header equals the old column list followed by the sixteen new columns; roll-up rows sum
  the new figures and name their shortfalls.

## Validation

Offline tests with synthetic names; XML nesting follows the 2026-09-30 captures cited in ADR 0185.
`tests/system/test_utilization_survey.py` covers the operation, `tests/app/test_report_cli.py`
the CSV. Guardrails: `just test`, `just verify`, `uv run --no-sync prek run --all-files`. The
end-to-end proof is a read-only live run of the command at the branch head on the live-test
host; its output stays private. It must show disk and adapter columns filled for operating
systems with running VIOS, and gaps, not zeros, where a VIOS is down.
