# Dirty metadata regression scope

## Problem

Issue #1432 observes that a fresh-project no-sync regression repeats every
static hook inside pytest. Keep its dirty editable-install proof while removing
redundant hook execution, subject to measured and fault-injected evidence.

## Scope

One test-only unit: `tests/test_ci_pipeline.py` retains the real copied project,
locked app sync, dirty `pyproject.toml`, `just lint`, and actual `prek run lint`.
The existing recipe/hook structural guards cover the complete configured hook
set: delegation to the matching static recipe, system execution, no file
narrowing, and no-sync protection for recipe `uv run` calls. Strengthen only gaps
needed for this proof. Record the choice in [ADR 0205](../../adr/0205-representative-dirty-metadata-hook.md).
No ownership transition: recipes and production hooks keep their current owners.
Separate required `prek run --all-files` and the CI matrix retain execution of
the complete hook set. Baselines (#1430), production orchestration (#1431),
other optimizations (#1433–#1435), product APIs and live hardware are excluded.

### Failure model

- Actors and deployments: local contributors and native Linux CI; amd64/arm64,
  Python 3.11–3.14, pinned project tools.
- Invariants and assets: dirty metadata must not trigger editable rebuilds;
  unselected hooks must remain structurally protected and independently executed.
- Accepted failure classes: none for the changed no-sync regression contract.
- Covered elsewhere: real complete hook execution by required prek checks;
  full test/coverage, generated docs and artifacts by `just verify` and CI.

## Success

Real dirty-project commands pass without rebuilding the installed editable
metadata. Removing recipe `--no-sync` or the outer `UV_NO_SYNC` protection is
detected; non-representative hook bypasses fail the exhaustive structural guard.
A rebuild hidden inside successful hook output is also detected. Report repeated
before/after focused-test timing samples with commit, resources and cache context.
If a per-hook behavior defeats the representative proof, retain integration
coverage and report a measured no-go. No production hook suppression is added.

## Validation

- `focused-test`: dirty-project regression; controlled real rebuild, missing
  outer environment protection, and missing recipe flag must fail its rebuild
  assertion. Inspect both command output streams, including successful hook
  output; isolate inherited no-sync settings so they cannot mask controls.
- `focused-test`: existing complete hook/recipe guards; mutate an unselected
  hook entry and recipe protection and observe assertion failures, then restore.
- Green command: `uv run --no-sync pytest tests/test_ci_pipeline.py --no-cov -q`.
  Time at least two serial old/new focused-regression samples on the same host,
  using fresh temporary projects and the same warmed dependency cache.
- Integration: `just verify`; `uv run --no-sync prek run --all-files`; CI's
  unchanged eight native verification and wheel-smoke legs.
- `task-test-not-applicable`: ADR/spec and measured report are human-readable
  rationale/evidence without an executable consumer; review their claims against
  retained command results rather than asserting prose.
