# Fleet utilization report design (#1252, slice 1)

## Problem

There is no single view of how the lab's Power capacity is allocated across every configured HMC.
`systems capacity` reads one profile, folds the hypervisor, VIOS and idle partitions into one
"assigned" figure, and renders a terminal table. #1252 asks for a fleet survey that leadership
can read. This slice delivers the survey operation and its CSV. #1253 (disk and adapters) and
#1254 (HTML) extend it.

## Scope

Governing decision: [ADR 0184](../../adr/0184-fleet-utilization-accounting-model.md) defines how
allocation is accounted, how unknowns propagate, and how dual-managed systems deduplicate.

Out of scope, with owners: measured consumption (#1242); the agent-facing `hmc_inventory` tool
(#1220); exposing the report as an MCP tool (maintainer); committing or publishing generated
reports (never); VIOS disk, I/O slot and SR-IOV columns (#1253); HTML output (#1254).

### Survey operation

New module `src/hmcpctl/operations/inventory/utilization.py`. It is presentation-neutral and
read-only: it issues only the GETs below.

- `survey_fleet(profiles, open_client, *, concurrency=4, hmc_timeout=300.0) -> FleetSurvey`
  surveys each named profile at most `concurrency` at a time. Each profile gets one
  `open_client(profile)` client and one `hmc_timeout`-second deadline over its whole survey.
- Per profile: `list_managed_systems()`; then, per system, `list_logical_partitions(uuid)`,
  `list_vios(uuid)`, and, for each never-applied partition only,
  `list_child("LogicalPartition", uuid, "LogicalPartitionProfile")`.
- A profile whose client cannot be built, whose logon or `ManagedSystem` read fails, or which
  exceeds its deadline becomes a `ProfileFailure(profile, reason)`, and its partial readings
  are discarded. Any exception from that profile's work counts. Cancellation is not caught.
- A per-system read that raises `HMCError` is recorded as a gap. The gap names the feed and
  the error text, and the system keeps its other figures. The figures that depend on the failed
  feed are `None`, per ADR 0184 decision 2.
- A figure missing from, or not numeric in, an HMC answer is `None`. No figure defaults to 0.

Records (frozen dataclasses; figures are `None` when unknown):

- `SystemReading`: `profile`, `name`, `machine_type`, `model`, `serial`, `firmware`, `state`,
  `cpu: CpuFigures`, `memory: MemoryFigures`, `partitions: PartitionFigures`,
  `shared_pools: tuple[int, ...]`, `gaps: tuple[str, ...]`.
- `CpuFigures` (processor units, `float`): `installed`, `configurable`, `vios`,
  `client_active`, `idle_reserved`, `other_reserved`, `free`, `dedicated`, `shared`.
- `MemoryFigures` (MiB, `int`): `installed`, `configurable`, `hypervisor`, `vios`,
  `client_active`, `idle_reserved`, `other_reserved`, `free`.
- `PartitionFigures`: `running`, `not_activated`, `other` (counts of client partitions), and
  `profile_claims`, `profile_claim_memory`, `profile_claim_units` for never-applied
  partitions.
- `FleetSurvey`: `profiles` (surveyed, in request order), `readings`, `failures`.

Field sources follow ADR 0184. A never-applied partition is `not activated` with a current memory
of 0 and current units of 0. `dedicated`/`shared` sum the current units of partitions whose
`CurrentHasDedicatedProcessors` is `true`/`false`. `shared_pools` holds the sorted distinct
`CurrentSharedProcessorPoolID` values of shared partitions with non-zero units.

Roll-ups (pure functions):

- `fleet_systems(readings) -> tuple[FleetSystem, ...]` groups readings by machine
  type-model-serial. A reading with no MTMS is its own group. Each `FleetSystem` carries the
  chosen reading (fewest `None` figures; ties go to the first profile name) and the sorted
  managing profiles.
- `rollup(readings) -> Rollup` with `systems`, `systems_counted`, and figure sums over the
  counted readings. A reading is counted when none of its figures is `None`.
- `utilization_pct(configurable, free) -> float | None` returns
  `round(100 * (configurable - free) / configurable, 1)`, or `None` when either figure is
  unknown or `configurable` is 0.

### CLI

`hmcpctl report utilization --csv PATH [--profile NAME ...] [--concurrency N]
[--hmc-timeout SECONDS]` lives in a new `report` group (`src/hmcpctl/cli_commands/report.py`).

- Without `--profile`, it surveys every profile `config_inventory()` lists, in file order. A
  named profile absent from the config is a usage error (exit 2) that lists the known names.
- An exported `HMC_HOST`, or any root connection option given on the command line (`--host`,
  `--user`, `--password`, `--verify-ssl`, `--profile`), is a usage error (exit 2). `HMC_HOST`
  would retarget every profile under the documented env-over-TOML precedence, and a root option
  would be silently ignored.
- No configured profile is an error (exit 1) naming the config path.
- Each profile's client is `HMCClient(load_profile(name))`.
- It writes the CSV, then prints one stderr line per failed profile and a summary line. It
  exits 0 once the CSV is written, failures included.

CSV (stdlib `csv`, UTF-8, header row): the first column is `row_type`, one of `system`, `hmc`,
`fleet` or `failure`, and the columns that follow are fixed (the plan lists them in order). Rows
come in this order:

1. one `system` row per fleet system, sorted by name then serial;
2. one `hmc` roll-up per surveyed profile that did not fail;
3. one `fleet` roll-up over the fleet systems;
4. one `failure` row per failed profile.

A field that does not apply to a row type is empty. An unknown figure is `unknown`. Utilization
columns are `cpu_util_pct` and `mem_util_pct`.

### Documentation

`docs/cli.md` gains the command, the meaning of each figure, and a warning: a generated report
holds internal hostnames, system names and serials, and must never be committed or posted
publicly. `CHANGELOG.md` records the addition.

## Failure model

1. **Actors and deployments**: an operator at a terminal on a workstation that holds the
   `config.toml` profiles and can reach the HMCs. There is no MCP surface and no CI job.
2. **Invariants and assets at stake**:
   - The read-only contract: the survey issues GETs only.
   - The figures leadership acts on must not under-report: no figure reads 0 when unknown,
     and no failed profile is dropped.
   - Internal identifiers in the output file.
3. **Accepted failure classes**:
   - Readings are not a point-in-time snapshot. They are taken seconds to minutes apart, and
     partitions may change state between reads. The report is for allocation planning, not
     audit.
   - A system a profile's HMC omits from its `ManagedSystem` feed (`list_managed_systems`
     skips a system it cannot read and logs a warning) is absent from the report.
   - The CPU other-reserved remainder is unexplained. It is reported, not attributed (ADR 0184).
   - `HMC_USER`, `HMC_PASSWORD` and other `HMC_*` overrides still apply to every profile, per
     `load_profile`'s documented precedence. A wrong value fails that profile's logon, so the
     effect shows as named failure rows rather than wrong figures.
4. **Covered elsewhere**:
   - Credential storage and TLS verification: `load_profile` and `HMCClient` (ADR 0096 and the
     TLS warning path).
   - Keeping generated reports out of git and out of public places: the operator, per the doc
     warning.
   - HMC REST read paths: the `just live-vocabulary` gate (the `LogicalPartitionProfile` child
     feed is captured).

## Success

- `hmcpctl report utilization --csv out.csv` over a fixture fleet writes system, hmc, fleet and
  failure rows. Each figure matches the ADR 0184 arithmetic for its fixture.
- A dual-managed fixture system appears once in `system` rows and once in the fleet total, but in
  both `hmc` roll-ups.
- A failed profile (logon error, deadline, client build) appears as a `failure` row, and its
  readings are absent.
- An HTTP 500 VIOS feed leaves `cpu_vios`, `mem_vios` and `cpu_other_reserved` `unknown` on that
  system only. `systems_counted` excludes the system.
- The header row equals the plan's column list exactly.

## Validation

Offline tests use respx and synthetic names. Their XML follows the 2026-10-01 captured shapes:
`tests/system/test_utilization_survey.py` covers the operation, and
`tests/app/test_report_cli.py` covers the CLI. Guardrails: `just test`, `just verify`, and
`uv run --no-sync prek run --all-files`. A read-only live run of the command against the
configured profiles, on minimus, is the end-to-end proof; its output stays private.
