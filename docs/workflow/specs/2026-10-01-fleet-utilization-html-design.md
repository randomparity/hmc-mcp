# Fleet utilization HTML report design (#1254)

## Problem

`hmcpctl report utilization` (#1252 slice 1, [design](2026-10-01-fleet-utilization-report-design.md);
#1253, [design](2026-10-01-fleet-utilization-disk-adapters-design.md)) writes CSV only. #1254 asks
for a single self-contained HTML page over the same survey that leadership can read offline.

## Scope

Governing decisions: [ADR 0184](../../adr/0184-fleet-utilization-accounting-model.md) and
[ADR 0185](../../adr/0185-fleet-utilization-disk-and-adapter-accounting.md). No new HMC read, MCP
tool or dependency; no ADR (the renderer has no decision with a viable alternative the issue left
open).

Out of scope, with owners: measured consumption (#1242); the agent-facing inventory tool (#1220);
MCP exposure (maintainer); committing generated reports (never); a new HTML or chart dependency
(out of scope); disk and adapter computation (#1253, merged); the CSV `notes` formula guard
(campaign root).

### Command (`src/hmcpctl/cli_commands/report.py`)

- `--csv PATH` becomes optional and `--html PATH` is added. Neither given is a usage error naming
  both; both naming the same path after `resolve()` and `casefold()` is a usage error (the
  case fold covers case-insensitive file systems; over-refusing on a case-sensitive one is
  harmless). One survey feeds both outputs.
- `_scratch_file(path)` is unchanged in shape and is called once per requested output before the
  survey, so an unwritable directory still fails before any HMC is contacted. Each scratch file is
  mkstemp's owner-only file. After the survey every requested output is written, then each
  scratch is renamed onto its path (CSV first); `finally` unlinks any scratch left behind.
- The HTML is stamped with the UTC time the survey finished.
- The summary line names every path written.

### Renderer (new `src/hmcpctl/cli_commands/report_html.py`)

`render_html(rows, profiles, generated) -> str`, where `rows` is `report_rows(survey)` (the CSV's
string dicts, so dedup, roll-ups, shortfall notes and failure rows have one owner), `profiles` is
`survey.profiles`, and `generated` an aware `datetime`. It imports nothing from `report.py`.

Page sections, in order:

1. Title, `Generated <YYYY-MM-DD HH:MM> UTC`, the profiles surveyed (every name in `profiles`),
   and a warning that the page holds internal hostnames and serials.
2. Fleet tiles from the `fleet` row, each showing that row's existing columns as they stand (the
   renderer adds no sums; `*_mib` shows as GiB) and, where it has one, its `*_util_pct` as text and an inline SVG
   bar: CPU (`allocated`, `free`), memory (`allocated`, `free`), disk (internal and SAN
   `assigned`, `free`), I/O slots (`assigned`, `sriov`, `unassigned`, `empty`), SR-IOV
   (`logical_ports`, `logical_ports_free`), and idle (`partitions_not_activated`,
   `cpu_idle_reserved`, `mem_idle_reserved_mib`). When the fleet row's `systems` is `0` (every
   profile failed, or none answered with a system), the tiles are replaced by "No systems were
   surveyed" and no figure is shown.
3. Fleet notes: the fleet row's `notes` once, verbatim, or nothing when empty.
4. Per-HMC table (one row per `hmc` row) with utilization bars as inline SVG.
5. Sortable per-system table (`system` rows); only this table carries class `sortable`.
6. Failed profiles, each with its reason, or "none". Failure rows appear only here.

Table columns are one `(csv column, label)` tuple shared by both tables: system, profiles, machine
type, model, serial, state, CPU configurable/allocated/util, memory configurable/allocated/util, idle
CPU and memory, partitions running/not activated, disk/slots/SR-IOV util, notes. The HMC table
drops the identity columns and adds `systems`. `*_mib` values display as GiB to one decimal.

Rendering rules:

- A cell whose value is `unknown` renders the word `unknown` (class `unknown`), never 0; an
  unknown utilization draws no bar. A bar's width is the percentage clamped to 0–100.
- Every value from `rows` and `profiles` passes through `html.escape(value, quote=True)` at the
  single interpolation helper; no other path writes row text.
- Sorting: inline script; clicking a header sorts by each cell's `data-sort` (numeric when every
  known cell parses as a number), unknown cells always last.
- A `<meta http-equiv="Content-Security-Policy">` sets `default-src 'none'`, `style-src
  'unsafe-inline'`, and `script-src 'sha256-<hash of the inline script>'`.
- No `src=`, `href=`, `url(` or `@import` appears in the template. A `@media print` block drops
  the pointer cursor and keeps tiles and rows from splitting across pages.

### Documentation

`docs/cli.md` describes `--html`, its sections and the never-commit/never-post warning; command help
names both formats; `CHANGELOG.md` records the addition.

## Success

- `report utilization --html a.html` alone, and with `--csv b.csv`, exits 0 and writes each file
  owner-only from one survey call; neither option is a usage error.
- The page contains each section above, every surveyed profile, each failed profile with its
  reason, the fleet notes, and the UTC stamp.
- A survey in which every profile failed writes both files, exit 0; the page names each failure
  and shows no fleet figure.
- For the template and the fixtures in the tests, the page contains no `src=`, `href=`, `url(` or
  `@import`.
- A reading whose every text field is `<script>alert(1)</script>"'&` renders only escaped.
- A figure the survey reports as unknown renders `unknown` in tiles and tables, never 0.

## Failure model

1. **Actors and deployments**: an operator running `hmcpctl` on a workstation that reaches the
   configured HMCs, then opening the file in a browser. No MCP surface, no CI job.
2. **Invariants and assets at stake**:
   - HMC- and config-sourced text cannot become markup or script in the page.
   - The page loads nothing from the network.
   - Unknown is never rendered 0 (ADR 0184 decision 4).
   - The CSV's columns and rows are unchanged.
3. **Accepted failure classes**:
   - With both outputs, a failure renaming the HTML after the CSV was renamed leaves a new CSV
     and the old HTML; the command exits non-zero naming the error. Bounded: rerun.
   - The page holds hostnames and serials; it is owner-only on disk and documented never to be
     committed or posted. Handling after it is written is the operator's.
   - Displayed GiB values are rounded; the CSV carries exact MiB.
4. **Covered elsewhere**: figure correctness, dedup and roll-up arithmetic (ADR 0184/0185,
   `tests/system/test_utilization_survey.py`, `tests/app/test_report_cli.py`); CSV formula guard
   for `notes` (campaign root).

### Threat model

- **Boundaries**: added — HMC-reported strings (system name, type, model, serial, firmware,
  state, gap notes naming VIOS) and operator-config strings (profile names, failure reasons that
  quote config errors) into an HTML document. Widened — none.
- **Actors**: whoever controls an HMC's reported names (an HMC administrator or a compromised
  HMC), and the page's reader, whose browser executes it. The operator is trusted.
- **Controls**: escaping at one helper with `quote=True` (covers element and attribute contexts;
  no row text is placed in script, style or URL contexts); the CSP hash blocks any inline script
  but the renderer's own and every network fetch; owner-only file mode from mkstemp.
- **Out of scope**: a reader's browser ignoring CSP (escaping still holds); disclosure through the
  operator sharing the file (documented warning).
