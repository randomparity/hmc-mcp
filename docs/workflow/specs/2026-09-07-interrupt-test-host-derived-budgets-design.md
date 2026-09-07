# The real-interrupt test's budgets are sized from bounds in the system

Issue [#721](https://github.com/randomparity/hmc-mcp/issues/721).
Decision: [ADR 0129](../../adr/0129-interrupt-test-budget-bounds.md).

## Problem

`test_real_interrupt_preserves_pytest_diagnostic` waits for a real `scripts/run_tests.py`
child's readiness marker under a fixed 10 s budget, sends `SIGINT`, then collects output under
a second, independent fixed 10 s budget. Both were sized on an idle machine. Measured on one
amd64 host (CPython 3.13, tree pinned to one core under N busy loops), readiness rises from
0.23 s idle to 8.77 s at 80-way contention — 1.14x from expiring — while collection stays under
3.42 s, clamped by `_settle_interrupted` (`scripts/run_tests.py:21-31`). Either expiry arrives
as `subprocess.TimeoutExpired`, indistinguishable from a regression.

## Scope

`tests/scripts/test_run_tests.py` only.

- `_wait_for_process_marker`'s default becomes `_READINESS_TIMEOUT_SECONDS = 60.0`, a hang
  ceiling paid only by a child that never becomes ready. Signature and body are unchanged.
- The collection budget becomes
  `2 * run_tests.INTERRUPT_GRACE_SECONDS + _INTERRUPT_COLLECTION_SLACK_SECONDS` (10.0).
- The collection call is wrapped so `subprocess.TimeoutExpired` kills the group, drains, and
  fails with a message naming the budget, distinct from the assertions.

Deferral carried: at 80-way contention the grandchild misses `INTERRUPT_GRACE_SECONDS`, is
`SIGTERM`ed, and `KeyboardInterrupt` never reaches stderr — the real host-load red, unreachable
from this surface. Owner: a follow-up issue, reported to this run's caller.

Out of scope per the frozen charter: `tests/test_ci_pipeline.py`'s `timeout=180` budgets and
`tests/vios/test_vios_backup.py`'s `timeout=300.0` mock assertions. No `CHANGELOG.md` entry.

## Success

1. Neither budget is sized against an idle machine: the readiness ceiling is sized to a hang,
   the collection budget to `scripts/run_tests.py`'s own settle bound.
2. Raising `INTERRUPT_GRACE_SECONDS` raises the collection budget without editing the test.
3. Both budgets stay finite: a child that never becomes ready fails in 60 s, one that never
   exits after `SIGINT` in 16 s.
4. A collection timeout fails with its own message, never as an assertion on `returncode`,
   `stdout`, or `stderr`.
5. The assertions hold: `returncode == 130`, empty stdout, `KeyboardInterrupt` in stderr.
6. `just verify` and `uv run --no-sync prek run --all-files` are green.

## Validation

- **Distinguishable teardown timeout and budget coupling** (success 1–4). Mode: focused-test.
  Case `…test_run_tests.py::test_real_interrupt_preserves_pytest_diagnostic`. Red: setting
  `run_tests.INTERRUPT_GRACE_SECONDS` and the slack constant to 0.001 puts the budget under the
  0.22 s idle collection measured above; the run then fails with the new message, not an
  `AssertionError`, and moving it only through those two names is success 2. Green:
  `uv run --no-sync pytest tests/scripts/test_run_tests.py`.
- **Readiness ceiling still bounds a hang** (success 3). Mode: focused-test. Same case. Red: a
  `test_slow.py` that never touches the marker and sleeps past 60 s — it must outlive the
  ceiling, or `poll()` reports the exit first — fails through the existing "did not create
  readiness marker" path.
- **Existing interrupt assertions** (success 5). Mode: focused-test. Same case, unchanged.
- **Guardrails** (success 6). Mode: task-test-not-applicable. `just verify` and
  `prek run --all-files` are themselves the observation.
