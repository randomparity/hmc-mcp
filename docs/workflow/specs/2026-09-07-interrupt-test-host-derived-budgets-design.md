# The real-interrupt test's budgets are sized from bounds in the system

Issue [#721](https://github.com/randomparity/hmc-mcp/issues/721).
Decision: [ADR 0129](../../adr/0129-interrupt-test-budget-bounds.md).

## Problem

`test_real_interrupt_preserves_pytest_diagnostic` waits for a real `scripts/run_tests.py`
child's readiness marker under a fixed 10 s budget, sends `SIGINT`, then collects output under a
second fixed 10 s budget, both sized on an idle machine. Measured on one amd64 host, readiness
rises from 0.23 s idle to 8.77 s at 80-way single-core contention — 1.14x from expiring — while
collection stays under 3.42 s, clamped by `_settle_interrupted` (`scripts/run_tests.py:21-31`).
At that load the grandchild also misses the 3 s grace, is `SIGTERM`ed, and its
`KeyboardInterrupt` never reaches stderr.

## Scope

`tests/scripts/test_run_tests.py` only.

- `_wait_for_process_marker`'s default becomes `_READINESS_TIMEOUT_SECONDS = 60.0`, a hang
  ceiling paid only by a child that never becomes ready. Signature and body are unchanged.
- The collection budget becomes
  `2 * run_tests.INTERRUPT_GRACE_SECONDS + _INTERRUPT_COLLECTION_SLACK_SECONDS` (10.0), and the
  call is wrapped so `subprocess.TimeoutExpired` kills the group, drains, and fails naming it.
- The test times `SIGINT` to exit. A missing `KeyboardInterrupt` after an interval reaching
  `INTERRUPT_GRACE_SECONDS` fails with its own truncation message, the existing assertion
  standing behind it otherwise. Without it the raised ceiling would turn that band's clear
  marker failure into an `AssertionError` shaped like a regression.

Deferral carried: the truncation is governed by `INTERRUPT_GRACE_SECONDS` in
`scripts/run_tests.py`, outside this surface, so this change names it rather than fixing it.
Owner: a follow-up candidate returned to this run's caller.

Out of scope per the frozen charter: `tests/test_ci_pipeline.py`'s `timeout=180` budgets and
`tests/vios/test_vios_backup.py`'s `timeout=300.0` mock assertions. No `CHANGELOG.md` entry.

## Success

1. The ceiling is sized to a hang and the collection budget to `run_tests.py`'s settle bound.
2. Raising `INTERRUPT_GRACE_SECONDS` raises the collection budget without editing the test.
3. Both stay finite: 60 s for a child that never becomes ready, 16 s for one that never exits.
4. Neither a teardown timeout nor a grace-truncated diagnostic reads as an assertion failure.
5. The assertions hold: `returncode == 130`, empty stdout, `KeyboardInterrupt` in stderr.
6. `just verify` and `uv run --no-sync prek run --all-files` are green.

## Validation

- **Teardown timeout and budget coupling** (success 1–4). Mode: focused-test. Case
  `…test_run_tests.py::test_real_interrupt_preserves_pytest_diagnostic`. Red: setting
  `run_tests.INTERRUPT_GRACE_SECONDS` and the slack to 0.001 puts the budget under the 0.22 s
  idle collection, so the run fails with the new message, not an `AssertionError`. Green:
  `uv run --no-sync pytest tests/scripts/test_run_tests.py`.
- **Truncation is named, not misread** (success 4, 5). Mode: focused-test. Same case. Red: run
  it under the 80-way single-core contention that reproduced the truncation above; it fails
  with the truncation message rather than on `KeyboardInterrupt`.
- **Readiness ceiling still bounds a hang** (success 3). Mode: focused-test. Same case. Red: a
  `test_slow.py` that never touches the marker and sleeps past 60 s, so `poll()` cannot report
  an exit first, fails through the existing marker path.
- **Guardrails** (success 6). Mode: task-test-not-applicable. `just verify` and
  `prek run --all-files` are themselves the observation.
