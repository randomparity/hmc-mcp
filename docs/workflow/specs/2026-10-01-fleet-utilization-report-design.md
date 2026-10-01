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
  `open_client(profile)` client and one `hmc_timeout`-second deadline covering logon and every
  read. Closing the session after the deadline fires is not covered by it, so the client's own
  logoff can add up to that profile's request timeout (`HMC_TIMEOUT`, 180 s by default).
- Per profile: `list_managed_systems()`; then, per system, `list_logical_partitions(uuid)`,
  `list_vios(uuid)`, and, for each never-applied partition only,
  `list_child("LogicalPartition", uuid, "LogicalPartitionProfile")`.
- A profile whose client cannot be built, whose logon or `ManagedSystem` read fails, or which
  exceeds its deadline becomes a `ProfileFailure(profile, reason)`, and its partial readings
  are discarded. Any exception from that profile's work counts. Cancellation is not caught.
  The one exception: when every read finished and only closing the session fails (an error or
  the deadline), the profile keeps its readings and the failure is logged as a warning.
- A per-system read that raises `HMCError` is recorded as a gap. The gap names the feed and
  the error text, and the system keeps its other figures. The figures that depend on the failed
  feed are `None`, per ADR 0184 decision 2.
- A never-applied partition whose profile claim cannot be read records a gap naming why: no
  `AssociatedPartitionProfile` link, a linked profile absent from the partition's profile
  feed, or a profile without `HasDedicatedProcessors`.
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
- `rollup(readings) -> Rollup` with `systems`, per-figure sums, `cpu_util_pct`,
  `mem_util_pct`, and `unknown`. Each figure sums the readings that reported it, and is `None`
  only when none did. `unknown` lists `(group, figure, count)` for every figure that some
  readings lack. Each utilization percentage is `utilization_pct` over the readings that
  reported both configurable and free capacity (ADR 0184 decision 4).
- `utilization_pct(configurable, free) -> float | None` returns
  `round(100 * (configurable - free) / configurable, 1)`, or `None` when either figure is
  unknown or `configurable` is 0.

### CLI

`hmcpctl report utilization --csv PATH [--profile NAME ...] [--concurrency N]
[--hmc-timeout SECONDS]` lives in a new `report` group (`src/hmcpctl/cli_commands/report.py`).

- Without `--profile`, it surveys every profile `config_inventory()` lists, in file order. A
  named profile absent from the config is a usage error (exit 2) that lists the known names.
- An exported `HMC_HOST`, `HMC_USER`, `HMC_PASSWORD`, `HMC_PORT` or `HMC_VERIFY_SSL` (any value,
  empty included), or any root connection option given on the command line (`--host`, `--user`,
  `--password`, `--verify-ssl`, `--profile`), is a usage error (exit 2). Under the documented
  env-over-TOML precedence an exported value would send one HMC's host, credentials or TLS
  setting to every profile, and a root option would be silently ignored (operator decision
  after the security review, 2026-10-01).
- No configured profile is an error (exit 1) naming the config path.
- Each profile's client is `HMCClient(load_profile(name))`.
- Before surveying, it creates a temporary file beside `PATH`; failing to is a usage error
  (exit 2). After the survey it prints one stderr line per failed profile and writes the CSV to
  the temporary file. It then renames that file over `PATH`, so a failed write leaves no partial
  report and no temporary file. It prints a summary line, and it exits 0 once the CSV is in
  place, failures included.

CSV (stdlib `csv`, UTF-8, header row): the first column is `row_type`, one of `system`, `hmc`,
`fleet` or `failure`, and the columns that follow are fixed: `COLUMNS` in `src/hmcpctl/cli_commands/report.py` lists
them in order, and `tests/app/test_report_cli.py` pins that list. Rows
come in this order:

1. one `system` row per fleet system, sorted by name then serial;
2. one `hmc` roll-up per surveyed profile that did not fail;
3. one `fleet` roll-up over the fleet systems;
4. one `failure` row per failed profile.

A field that does not apply to a row type is empty. An unknown figure is `unknown`. Utilization
columns are `cpu_util_pct` and `mem_util_pct`, beside `cpu_allocated` and `mem_allocated_mib`
(configurable minus free, over the same systems as the percentage). A system row's `notes` holds its gaps. A roll-up
row's `notes` names each column some of its systems lack, as
`<column>: <n> of <systems> systems unknown`. When profiles failed, the `fleet` row's `notes`
begins `<n> of <m> profiles failed (<names>); systems only they manage are absent`. A text
cell (`profiles`, `system`, identity, `firmware`, `state`) starting with `=`, `+`, `-`, `@`,
tab or carriage return is written with a leading `'`, so a spreadsheet does not evaluate it.

### Documentation

`docs/cli.md` gains the command, the meaning of each figure, and a warning: a generated report
holds internal hostnames, system names and serials, and must never be committed or posted
publicly. `CHANGELOG.md` records the addition.

## Failure model

1. **Actors and deployments**: an operator at a terminal on a workstation that holds the
   `config.toml` profiles and can reach the HMCs. There is no MCP surface and no CI job.
2. **Invariants and assets at stake**:
   - The read-only contract: the survey issues GETs only.
   - For the systems each HMC's `ManagedSystem` feed returns, the figures leadership acts on
     must not under-report: no figure reads 0 when unknown, no failed profile is dropped, and
     every roll-up shortfall is named.
   - Internal identifiers in the output file.
3. **Accepted failure classes**:
   - Readings are not a point-in-time snapshot. They are taken seconds to minutes apart, and
     partitions may change state between reads. The report is for allocation planning, not
     audit.
   - A system a profile's HMC omits from its `ManagedSystem` feed is absent from the report.
     This includes a system `list_managed_systems` skips in its firmware fallback, which it
     reports only as a warning on stderr; the docs say so.
   - System figures are reported as the HMC gives them, whatever the state. The 2026-10-01
     capture checked them for `operating`, `standby` and `initializing` systems, where they
     reconciled with their partitions. A `no connection` system lacked
     `CurrentAssignedMemoryToPartitions`. Other states (`power off`, `error`) were not checked.
     The `state` column shows each system's state, and dedup may prefer such a reading.
   - The CPU other-reserved remainder is unexplained. It is reported, not attributed (ADR 0184).
   - `HMC_TIMEOUT` and `HMC_SCHEMA_VERSION` still apply to every profile, per `load_profile`'s
     documented precedence; they change no host, credential or TLS setting. The connection
     overrides are refused (CLI section).
4. **Covered elsewhere**:
   - Credential storage and TLS verification: `load_profile` and `HMCClient` (ADR 0096 and the
     TLS warning path).
   - Keeping generated reports out of git and out of public places: the operator, per the doc
     warning.
   - HMC REST read paths: the `just live-vocabulary` gate (the `LogicalPartitionProfile` child
     feed is captured). Element nesting is not covered by that gate; the live run under
     Validation covers it.

## Success

- `hmcpctl report utilization --csv out.csv` over a fixture fleet writes system, hmc, fleet and
  failure rows. Each figure matches the ADR 0184 arithmetic for its fixture.
- A dual-managed fixture system appears once in `system` rows and once in the fleet total, but in
  both `hmc` roll-ups.
- A failed profile (logon error, deadline, client build) appears as a `failure` row, and its
  readings are absent.
- An HTTP 500 VIOS feed leaves `cpu_vios`, `mem_vios`, `cpu_other_reserved`, `cpu_dedicated`,
  `cpu_shared` and `shared_pools` `unknown` on that system only (the last three include VIOS
  units). The roll-ups still sum its other figures, and their notes name the VIOS columns'
  shortfall.
- The header row equals the plan's column list exactly.

## Validation

Offline tests use respx and synthetic names. Their XML follows the 2026-10-01 captured shapes:
`tests/system/test_utilization_survey.py` covers the operation, and
`tests/app/test_report_cli.py` covers the CLI. Guardrails: `just test`, `just verify`, and
`uv run --no-sync prek run --all-files`. The end-to-end proof is a read-only live run of the
command against the configured profiles on the live-test host, at the branch head; its output
stays private. It must show:
- profile-claim columns populated on systems known to hold never-applied partitions;
- gaps rather than failure rows on the HMCs whose VIOS feed answers 500;
- for each fully read system, client, idle and VIOS memory summing to the system's
  `CurrentAssignedMemoryToPartitions`, checked by hand against the HMC.
