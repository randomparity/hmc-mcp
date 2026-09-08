# Interrupt grace bounds in `scripts/run_tests.py`

Issue: [#728](https://github.com/randomparity/hmc-mcp/issues/728). The decision, measurements and
rejected alternatives are in [ADR 0130](../../adr/0130-interrupt-diagnostic-window-and-reap.md),
extending [ADR 0129](../../adr/0129-interrupt-test-budget-bounds.md); the execution and its
verification inventory are in `../plans/2026-09-07-interrupt-grace-bounds.md`.

## Problem

`INTERRUPT_GRACE_SECONDS = 3` is spent on both of `_settle_interrupted`'s waits — a diagnostic
window that must cover host latency, and a reap that need not. Measured unclamped, those needed
16.9 s and 0.22 s at the same 80-way contention, so one constant cannot serve both and 3 s
truncates the `KeyboardInterrupt` report on a loaded host. The same function catches only
`subprocess.TimeoutExpired`, so a second `Ctrl-C` inside the window escapes `main`, skips
`_replay`, discards the captured output, returns `-2` instead of `130`, and orphans the child;
widening the window widens that exposure, so it is fixed here rather than left.

## Scope

In: the two waits and their constants in `scripts/run_tests.py`; the timeout arm's use of the
same helper; `tests/scripts/test_run_tests.py`'s collection budget and tests for the paths
above; ADR 0130; a `CHANGELOG.md` entry.

Out: `TEST_TIMEOUT_SECONDS` and the `ci` job's 20-minute budget; recalibrating the readiness and
collection budgets ADR 0129 set; a bare raise of the existing constant as the whole fix.

## Success

1. The window cannot fire before ADR 0129's budgets apply: a host slow enough to truncate the
   diagnostic has already exhausted the 60-second readiness ceiling.
2. A child ignoring `SIGINT` is still escalated and reaped, so the real-interrupt test fails on
   a bounded 73-second budget rather than stalling.
3. A second `Ctrl-C` arriving while the parent waits on the child escalates to `SIGTERM` at once
   and a third to `SIGKILL`, with the run still returning `130`, replaying the captured output
   and leaving no orphan. Interrupts arriving during `_replay` are not caught, and ADR 0130
   records that.
4. The timeout arm is not slowed by the widened window.
5. `just verify` green; `CHANGELOG.md` carries an `## [Unreleased]` entry.
