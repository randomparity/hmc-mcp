# Scenario gap report (#1091)

## Problem

Nothing joins the ledger (`docs/capabilities/rows.json`, `operations.json`) to the live
scenario registry (`SUBTASKS` in `scripts/live_test_runner.py`). `tests/test_live_runner.py`
already fails `just test` on a dispatch the served schema rejects; the report surfaces that
class rather than adding its first detector.

## Scope

New `scripts/scenario_gap_report.py`, its `tests/scripts/` module, and a pointer in
`docs/capabilities/README.md`. No recipe, CI job, schema, generator, maturity record, or
scenario fix (#1092-#1094, #625-#634).

- **Scan** (AST, each `scripts/live_test/*.py`): each `<x>.call(client, "<tool>", ...)`
  with line, enclosing top-level function, tool and keywords (`expected`, `reuse_gaps`
  dropped); each literal `record_verified` `operation=`; a non-literal tool or operation,
  a `**` splat, or a site outside a top-level def is `unreadable`. A function is registered
  when a `SUBTASKS` value reaches it by name (same module, `from .` or `live_test` import).
- **Served schemas**: the runner's composition — legacy policy with the arbitrary command,
  `create_mcp`, `configure_arbitrary_command_tool`, in-process `list_tools()`.
- **Join**: a tool maps to the `operations.json` entry with that `tool`. An operation is
  exercised when dispatched or named by `record_verified`; a row is exercised when an
  exercised operation lists it in `row_ids`. Arguments are checked by the runner's
  `_dispatch_problems(tool, names, schemas)`.
- **Output**: one line per finding — `uncovered-operation:`, `uncovered-row:`, `departed:`
  (unregistered function's site), `unregistered:` (tool or operation not in the ledger, or
  tool not served; no further check), `dispatch-mismatch:`, `unreadable:`, each site with
  `file:line` — then `summary:` counts. Loaded inputs exit 0, or with `--fail-on-dispatch`
  1 on an `unregistered`, `dispatch-mismatch` or `unreadable` line. A failed load raises.

### Failure model

- Actors and deployments: an operator or agent at a workstation; later a CI step (#1094).
- Invariants and assets at stake: read-only (no writes, no HMC); flagless exit 0.
- Accepted failure classes: a function reached only dynamically (`getattr`, a mapping)
  reads as departed; argument types are unchecked (`tests/test_live_runner.py` owns them).
- Covered elsewhere: CI enforcement (#1094); fixing mismatches (#625-#634).

## Success

1. Each operation in `operations.json` and row in `rows.json` not exercised prints one
   `uncovered-*` line.
2. Each dispatch or `record_verified` in an unregistered function prints `departed:`;
   in a registered one, a tool not served or not in the ledger, or an operation absent
   from it, prints `unregistered:`; both with `file:line`.
3. Each dispatch passing an argument absent from the served schema, or omitting a required
   one, prints `dispatch-mismatch:` with `file:line`.
4. With inputs loaded: exit 0 without the flag; with it, 1 iff a 2, 3 or `unreadable` line.

## Validation

- Scan: Mode: focused-test — synthetic source yields sites, tools, keywords, operations,
  `unreadable` for a splat and a non-literal tool.
- Criteria 1-3: Mode: focused-test — synthetic ledger, schemas and scan give each line.
- Criterion 4: Mode: focused-test — exit decision, flag on and off (`departed` excluded).
- Real tree: Mode: focused-test — `--fail-on-dispatch` exits 0, non-zero counts read.
- README pointer: Mode: task-test-not-applicable — prose with no executable consumer.
