# Verification timing diagnostics

## Problem

Issue #1430 requires attributable verification costs before the optimizations in
epic #1429. Successful pytest output currently hides its timing evidence.

## Scope

Extend the existing runner and recipe graph; no ownership transition is needed.
Keep `main()` callable without arguments. A closed CLI accepts only `--timings`;
`HMCPCTL_TEST_TIMINGS=1` enables the same mode through the composed graph.
The runner adds pytest `--durations=30 --durations-min=0`, replays successful
output in that mode, and retains its existing lifecycle and environment filtering.
`test-timings` selects the CLI flag; `verify-timings` sets the switch and invokes
`just --time verify`, reusing native recipe timings without another gate list.
Quiet recipes remain unchanged. ADR 0204 records this opt-in extension.

Measure three serial warm full `verify-timings` runs on one implementation commit;
warm means existing venv, dependency cache and OS caches, after setup and focused
checks, without cache eviction or an additional full warm-up. Measure one initial
fresh-venv `just setup` separately with existing dependency/OS caches. Record
commit, Python, OS, architecture, available CPU/memory, load and cache conditions.
Measure dirty-metadata regression and capability gate/report independently once.
Report three-run variability and every native leg of successful CI run 37795020469,
its distinct commit, queue delay, end-to-end elapsed and summed runner time.
Report final branch CI separately; do not extrapolate hosted timings locally.
Exclude optimizations (#1431–#1435) and campaign-owned product API, live hardware,
weaker coverage/security, target/Python removal and new hardware floors.

### Failure model

- Prevent: diagnostic inputs bypassing configured 90.5% branch coverage or changing selection.
- Recover: existing subprocess failure, timeout and interrupt paths replay diagnostics unchanged.
- Accept: timings vary with shared-host load and retained caches; record variability, no speed target.
- Deployments: existing local development and native amd64/arm64 Python 3.11–3.14 CI.

## Success

Diagnostics retain the slowest 30 pytest setup/call/teardown entries and recipe
wall times; ordinary successful output remains compact. Unknown CLI inputs fail
before launch. Coverage settings, environment sanitation, timeout, signal status
and interruption escalation remain unchanged. The report contains the bounded
observations above, with machine identifiers omitted and failures disclosed.

## Validation

- Mode: focused-test. Runner tests in `tests/scripts/test_run_tests.py` cover
  opt-in output/arguments, rejected overrides and existing failure/interrupt paths;
  expected red: timing option unsupported. Green: `uv run --no-sync pytest --no-cov
  tests/scripts/test_run_tests.py -q`.
- Mode: focused-test. Recipe tests in `tests/test_ci_pipeline.py` enforce the
  closed diagnostic invocation and single verification graph; expected red:
  recipes missing. Green: `uv run --no-sync pytest --no-cov tests/test_ci_pipeline.py -q`.
- Mode: task-test-not-applicable. ADR, operator guidance and timing report are
  human-readable evidence with no executable consumer; inspect captured results.
- Run focused checks, static hooks, final `just verify`, and native branch CI;
  diagnostic full runs also exercise real pytest and its configured coverage gate.
