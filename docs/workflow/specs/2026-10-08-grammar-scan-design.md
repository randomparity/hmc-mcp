# Invocation-local grammar scans (#1440)

## Problem

The grammar recurrence tests repeatedly parse identical roots and traverse each
selected subtree four times to exclude docstrings, f-string fragments and labels.
Issue #1440 authorizes reducing verified duplicate work with measured evidence.

## Scope

Only `tests/unit/test_i_record_grammar.py` changes executable behavior.
Keep the scanner as owner; pass its parsed modules explicitly to selection
consumers, supplied by a function-scoped pytest fixture. The known-site test
reuses that snapshot across its three categories. Build one node list per
literal-selection call and pass it to the three existing exclusion helpers.
Remove their redundant traversals; preserve selection order and identity.
No ownership transition or compatibility path is needed.

A module/session fixture would share across test invocations; it is excluded.
Retaining the current scanner remains the fallback if comparable measurements
show no improvement. No production/API/grammar/live-hardware changes,
persistent caches, dependency additions, root/category reduction or sibling
optimization work; owners remain those recorded in the frozen #1440 scope.
ADR 0045 and ADR 0061 retain their existing bounded guard contracts.

### Failure model

- Actors/deployments: local and CI pytest runs, amd64/arm64, Python 3.11–3.14.
- Invariants/assets: both roots, three categories, exact known sites, builder
  coupling, hoist rejection, prose exclusion; read/parse failures propagate.
- Accepted classes: edits during one test's snapshot are not observed until its
  next invocation; existing literal-selection bounds remain per ADR 0045/0061.
- Covered elsewhere: production grammar and hardware behavior, campaign owner;
  unrelated verification optimizations, #1431–#1435.

## Success

The existing guard assertions and module tests pass without narrowing inventory.
A snapshot is local to one test invocation, observes fresh fixture roots and
changed files on later invocations, and never caches failed reads/parses.
Comparable focused-module durations and read/parse/walk counts demonstrate
improvement; report observations without a promised multiplier.

## Validation

- Mode: focused-test — recurrence and coverage: synthetic source roots exercise
  the real guard assertions for builder bypass, uninspected and hoisted literals,
  missing known sites, and excluded prose; faults raise the existing assertions.
- Mode: focused-test — lifetime/errors: temporary roots change between scans;
  changed source is visible and unreadable/malformed files raise. Count reads,
  parses and AST walks; duplicate traversal is the expected pre-change failure.
- Green command for both: `uv run --no-sync pytest tests/unit/test_i_record_grammar.py -q --no-cov`.
- Run comparable before/after module timings with identical settings on this
  branch base and candidate, serially without competing full verification.
  Retain commands, source identities, repetitions and counts in workflow evidence.
- Run `just verify` and `uv run --no-sync prek run --all-files`; hosted CI covers
  the declared Python/architecture matrix. No live hardware operation applies.
- Mode: task-test-not-applicable — this spec and measurement prose have no
  executable consumer; validate their claims against actual results and review.
