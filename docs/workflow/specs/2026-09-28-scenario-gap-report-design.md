# Scenario gap report (#1091)

## Problem

Nothing joins the capability ledger (`docs/capabilities/rows.json`, `operations.json`) to
the live scenario registry (`SUBTASKS` in `scripts/live_test_runner.py`), so no one can say
which operations or rows have a scenario, or which scenario names an operation the registry
no longer has. `tests/test_live_runner.py` already fails `just test` on a dispatch the
served schema rejects; the report surfaces that class rather than adding its first detector.

## Scope

New `scripts/scenario_gap_report.py`, tested by `tests/scripts/test_scenario_gap_report.py`,
plus one pointer sentence in `docs/capabilities/README.md`. No just recipe, CI job, schema,
generator, maturity record, or scenario fix (#1092-#1094, #625-#634).

- **Scenario modules**: the modules defining a `SUBTASKS` value, as the runner's validator.
- **Scan** (AST per module): each `<x>.call(client, "<tool>", ...)` with line, tool and
  keyword names (`expected`, `reuse_gaps` dropped), and each `record_verified` literal
  `operation=`. A non-literal tool or operation, or a `**` splat, is `unreadable`.
- **Served schemas**: the runner's composition — legacy policy with the arbitrary command,
  `create_mcp`, `configure_arbitrary_command_tool`, in-process `list_tools()`.
- **Join**: a tool maps to the `operations.json` entry with that `tool`. An operation is
  exercised when dispatched or named by `record_verified`; a row is exercised when an
  exercised operation lists it in `row_ids`. Arguments are checked by the runner's
  `_dispatch_problems(tool, names, schemas)`.
- **Output**: one line per finding — `uncovered-operation:`, `uncovered-row:`,
  `unregistered:` (tool not served or operation not in `operations.json`, with
  `file:line`), `dispatch-mismatch:` (`file:line` and problem), `unreadable:` — then a
  `summary:` line of counts. Exit 0; with `--fail-on-dispatch`, exit 1 when any
  `unregistered`, `dispatch-mismatch` or `unreadable` line exists.

### Failure model

- Actors and deployments: an operator or agent at a workstation; later a CI step (#1094).
- Invariants and assets at stake: read-only — writes nothing, reaches no HMC; exit 0
  without the flag, so wiring cannot start red.
- Accepted failure classes: a helper in a scenario module that no registered function
  reaches still counts (module granularity, as the runner's validator); argument types
  are unchecked — `tests/test_live_runner.py` owns type resolution.
- Covered elsewhere: CI enforcement (#1094); fixing mismatches (#625-#634).

## Success

1. Each operation in `operations.json` and row in `rows.json` not exercised prints one
   `uncovered-*` line.
2. Each dispatched tool not served, and each `record_verified` operation absent from
   `operations.json`, prints one `unregistered:` line with `file:line`.
3. Each dispatch passing an argument absent from the served schema, or omitting a required
   one, prints `dispatch-mismatch:` with `file:line`.
4. Exit is 0 without the flag; with it, 1 exactly when a 2, 3 or `unreadable` line exists.

## Validation

- Scan: Mode: focused-test — synthetic source yields line, tool, keyword names, operation,
  and `unreadable` for a splat and a non-literal tool.
- Criteria 1-3: Mode: focused-test — synthetic ledger, schemas and scan give each line.
- Criterion 4: Mode: focused-test — exit decision, clean and failing, flag on and off.
- Real tree: Mode: focused-test — `main(["--fail-on-dispatch"])` exits 0 with `summary:`.
- README pointer: Mode: task-test-not-applicable — prose with no executable consumer.
