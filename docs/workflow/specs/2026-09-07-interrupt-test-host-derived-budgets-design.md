# The real-interrupt test's collection budget is derived from the host

Issue [#721](https://github.com/randomparity/hmc-mcp/issues/721).
Decision: [ADR 0129](../../adr/0129-host-derived-interrupt-test-budgets.md).

## Problem

`test_real_interrupt_preserves_pytest_diagnostic` spawns a real `scripts/run_tests.py`, waits
for the child's readiness marker under a fixed 10 s budget, sends `SIGINT` to its process
group, then collects output under a second, independent fixed 10 s budget. Both constants were
sized on an unloaded machine. Measured on this branch (x86_64, CPython 3.13), readiness costs
0.24 s idle and 4.2 s under 41-way contention on one core, leaving the readiness wait 2.4x
headroom. Either budget can expire because the host was busy, and the failure then
arrives as `subprocess.TimeoutExpired`, indistinguishable from a regression under test.

## Scope

`tests/scripts/test_run_tests.py` only.

- `_wait_for_process_marker` returns the readiness duration it already measures instead of
  discarding it; its default becomes `_READINESS_TIMEOUT_SECONDS = 60.0`.
- `test_real_interrupt_preserves_pytest_diagnostic` derives its collection budget from that
  observation: `2 * run_tests.INTERRUPT_GRACE_SECONDS + max(10.0, 5.0 * readiness)`.
- The collection call is wrapped so `subprocess.TimeoutExpired` kills the group and fails with
  a message naming both numbers, distinct from the `returncode`/`stdout`/`stderr` assertions.

Out of scope per the frozen charter: `tests/test_ci_pipeline.py`'s `timeout=180` whole-command
budgets (owner: a separate issue if they ever flake) and `tests/vios/test_vios_backup.py`'s
`timeout=300.0` mock assertions (owner: none; no wall-clock race exists there). No
`CHANGELOG.md` entry — nothing outside `tests/` changes. No deferrals carried.

## Success

1. The post-interrupt collection budget scales with the host's measured readiness latency.
2. The readiness wait is a hang ceiling, not a latency budget: it costs nothing when the
   marker appears, and 60 s is 14x the slowest readiness measured under contention.
3. Both budgets stay finite, so a child that never becomes ready and a child that never exits
   after `SIGINT` each fail the test rather than stalling the job.
4. A collection timeout fails with its own message, never as an assertion on `returncode`,
   `stdout`, or `stderr`.
5. The existing assertions still hold: `returncode == 130`, empty stdout, `KeyboardInterrupt`
   in stderr.
6. `just verify` and `uv run --no-sync prek run --all-files` are green.

## Validation

- **Derived budget and distinguishable timeout** (success 1, 3, 4). Mode: focused-test. Case
  `tests/scripts/test_run_tests.py::test_real_interrupt_preserves_pytest_diagnostic`. Red:
  setting the floor and multiple to 0.001 makes the run fail with the new teardown message
  naming the observation, not with an assertion error. Green:
  `uv run --no-sync pytest tests/scripts/test_run_tests.py`.
- **Readiness ceiling still bounds a hang** (success 2, 3). Mode: focused-test. Same case.
  Red: a `test_slow.py` that never touches the marker fails through
  `_wait_for_process_marker`'s existing "did not create readiness marker" path after the
  ceiling, rather than stalling.
- **Existing interrupt assertions** (success 5). Mode: focused-test. Same case, assertions
  unchanged; red is any change to `scripts/run_tests.py`'s `KeyboardInterrupt` path.
- **Guardrails** (success 6). Mode: task-test-not-applicable. `just verify` and
  `prek run --all-files` are the observation; no test of this change can fail on them.
