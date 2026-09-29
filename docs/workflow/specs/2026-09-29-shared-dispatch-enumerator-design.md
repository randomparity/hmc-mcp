# Spec: One dispatch enumerator for the argument guard and the gap report

**Issue:** #1120
**Branch:** refactor/shared-dispatch-enumerator-1120
**BASE_BRANCH:** main
**ADR:** none — a test-tooling seam with no public, persisted, or security contract.

## Problem

Two AST walks enumerate the live scenarios' `call(...)` dispatch sites:

- `tests/test_live_runner.py` `_dispatched_calls` — feeds the argument guard
  (`_dispatch_argument_report`, `test_every_dispatched_argument_matches_the_served_schema`)
  and the tool-name guard (`_dispatched_tool_names`). Walks every `ast.Call` in the
  module, raises `AssertionError` on a non-literal tool, yields `None` for a `**`
  splat, excludes `{"expected", "reuse_gaps"}` inline.
- `scripts/scenario_gap_report.py` `scan_source` — walks every top-level statement,
  lists a dispatch outside a top-level definition, a non-literal tool, or a splat as
  `unreadable`, excludes `_RUNNER_KEYWORDS`.

Their rules can drift so that the guard and the report disagree while both pass.

## Design

`scripts/scenario_gap_report.py` gains the one enumerator:

```python
@dataclass(frozen=True)
class DispatchSite:
    lineno: int
    function: str | None          # enclosing top-level def or class; None outside one
    tool: str | None              # None unless a string literal
    arguments: tuple[tuple[str | None, ast.expr], ...]  # tool keywords; None name = ** splat
    unreadable: str | None        # why the site cannot be read statically, else None

def dispatch_sites(tree: ast.Module) -> list[DispatchSite]
```

It visits every `ast.Call` whose `func` is an `ast.Attribute` named `call` inside any
top-level statement — which is every such call in the module — in source order.
`arguments` drops `_RUNNER_KEYWORDS`, the single runner-keyword set. `unreadable` is,
first match wins: `"call outside a top-level function"` when `function` is `None`;
`"dispatch with a non-literal tool or a ** splat"` when `tool` is `None` or a name is
`None`; else `None`. These are today's report strings, unchanged.

Consumers, each keeping its own unreadable policy:

- `scan_source` keeps its single source-ordered loop over attribute calls; for each
  `call` it asks the enumerator's per-node helper for the `DispatchSite`, then builds
  `Dispatch` (names only) or lists `f"{label}:{lineno} {unreadable}"`. So dispatch and
  `record_verified` unreadable lines stay interleaved in source order, as today.
  `record_verified` handling is otherwise untouched.
- The test module imports `scenario_gap_report` (already on `sys.path`) and deletes
  `_dispatched_calls`. `_dispatch_argument_report` iterates `dispatch_sites` and turns
  each unreadable site into one problem containing `cannot read`, then continues —
  the guard fails (`problems == []` is asserted). `_dispatched_tool_names` and the
  argument-resolution tests read through a helper that asserts no site is unreadable,
  message containing `cannot read`.

### Scope decision

The guard adopts the report's site classification; the enumerator still walks every
top-level statement. Consequence: a dispatch outside a top-level definition, which the
guard used to check normally, now fails the guard. Nothing is dropped — every
`.call` in the module still reaches a consumer, readable or unreadable. On the real
tree no such site exists (`live_test_runner.py` has zero `.call` sites; the report's
`--fail-on-dispatch` run is clean over the scenario package).

The tool-name guard also moves onto the shared classification, so it now refuses a
`**` splat and an outside-top-level dispatch as well as a non-literal tool (today it
accepts both). This is re-pointing, a consequence of criterion 4; on the real tree
neither shape exists, so the tool-name set is unchanged.

Rejected:

- **Enumerator walks the whole module and ignores enclosing function.** judgment: the
  report needs the enclosing top-level name to decide reachability, so a second,
  function-aware pass would remain — the drift this issue removes.
- **Guard consumes `scan_source` directly.** verified: `Dispatch.arguments` holds names
  only (`scripts/scenario_gap_report.py`, `class Dispatch`), and `_resolved_argument`
  needs the expression node.

## Failure model

1. Actors and deployments: a developer or CI running `just test` / the gap-report
   script offline. Nothing reaches an HMC.
2. Invariants at stake: the guard's fail-closed coverage — every `.call` site in the
   guarded sources is either checked or fails; `checked`/`total` on the real tree stay
   at the pre-change 253/470; the tool-name set stays at 87.
3. Accepted: a `*` positional splat or extra positional after the tool is read as a
   clean site — `RunState.call` is keyword-only after `tool`, so it raises
   `TypeError` before any request; unchanged from today, follow-up candidate.
   Accepted: a dispatch spelled other than `<expr>.call(...)` (e.g. a bare `call(...)`
   name) is not enumerated — held by the approved non-goal that what counts as a
   dispatch does not change.
4. Covered elsewhere: `record_verified` scanning (non-goal); served-schema composition
   (#1119).

## Success

1. `_dispatched_calls` and its inline keyword set are gone from `tests/test_live_runner.py`;
   the runner-keyword set exists only as `_RUNNER_KEYWORDS`.
2. `_dispatch_argument_report` on the real tree reports 0 problems, `checked` 253,
   `total` 470; tool-name set size 87 — measured before and after.
3. The guard fails on each of: a non-literal tool, a `**` splat, a dispatch outside a
   top-level function — asserted by tests in `tests/test_live_runner.py`.
4. Existing `tests/scripts/test_scenario_gap_report.py` cases pass unchanged; a new case
   asserts `dispatch_sites` returns expression nodes and drops runner keywords, and one
   asserts a `record_verified` unreadable line before a dispatch one keeps source order.
5. `uv run --no-sync python scripts/scenario_gap_report.py --fail-on-dispatch` exits 0 and
   its stdout is byte-identical before and after (`diff` exits 0; baseline ends
   `summary: 221 dispatches; ... 0 unreadable`).

## Validation

- `focused-test`: guard unreadable policy — parametrized
  `test_argument_guard_refuses_a_dispatch_it_cannot_read` and
  `test_dispatch_guard_refuses_a_tool_name_it_cannot_read` over the three fault shapes;
  red on the outside-top-level case before the change (the old walk checks it);
  `uv run --no-sync pytest tests/test_live_runner.py -k "cannot_read" --no-cov -q`.
- `focused-test`: enumerator contract — `test_dispatch_sites_carry_argument_nodes`;
  red before `dispatch_sites` exists;
  `uv run --no-sync pytest tests/scripts/test_scenario_gap_report.py --no-cov -q`.
- `focused-test`: coverage counts — before and after, call
  `_dispatch_argument_report(sources, schemas)` over `LIVE_WORKFLOW_MODULES` plus the
  runner (as the schema test builds `sources`) and take the union of
  `_dispatched_tool_names` over the same sources; expect 0 problems, 253, 470, 87.
  Numbers go in the PR body; the `checked >= 230` floor test stays.
- `focused-test`: report output — capture stdout before (done, scratch), rerun after,
  `diff` exits 0.
