# Interrupt grace bounds in `scripts/run_tests.py`

Issue: [#728](https://github.com/randomparity/hmc-mcp/issues/728). Decision:
[ADR 0130](../../adr/0130-interrupt-diagnostic-window-and-reap.md), which carries the
measurements and extends [ADR 0129](../../adr/0129-interrupt-test-budget-bounds.md). Plan and
verification inventory: `../plans/2026-09-07-interrupt-grace-bounds.md`.

## Problem

`INTERRUPT_GRACE_SECONDS = 3` is spent on both of `_settle_interrupted`'s waits. The first is a
diagnostic window that must cover host latency; the second only reaps a signalled process.
Measured unclamped, the diagnostic needs up to 16.9 s at 80-way single-core contention while the
reap needs 0.22 s at the same load, so one constant cannot serve both, and 3 s truncates the
`KeyboardInterrupt` report on any sufficiently loaded host.

A second defect sits in the same function: it catches only `subprocess.TimeoutExpired`, so a
second `Ctrl-C` inside the window escapes `main`, skips `_replay`, discards the captured output,
returns `-2` instead of `130`, and orphans the child. Widening the window widens that exposure,
so it is fixed here rather than left.

## Scope

In: the two waits and their constants in `scripts/run_tests.py`; the timeout arm's use of the
same helper; `tests/scripts/test_run_tests.py`'s collection budget and tests for the paths
above; ADR 0130; a `CHANGELOG.md` entry.

Out: `TEST_TIMEOUT_SECONDS` and the `ci` job's 20-minute budget; recalibrating the readiness and
collection budgets ADR 0129 set; a bare raise of the existing constant as the whole fix.

## Design

`INTERRUPT_GRACE_SECONDS` becomes 60 and governs only the diagnostic window. A new
`TERMINATE_GRACE_SECONDS = 3` governs the reap between `SIGTERM` and `SIGKILL`. The escalation
becomes its own function, `_stop`, so the timeout arm — where no signal has yet asked the child
to stop, and a window therefore buys nothing — calls it directly instead of paying a window
first. `_settle_interrupted` keeps its name and the interrupt arm, waits the window, and falls
through to `_stop`. Both waits catch `KeyboardInterrupt` beside `subprocess.TimeoutExpired`, so
a second `Ctrl-C` escalates at once and a third kills.

ADR 0130 derives the 60 rather than picking it: ADR 0129's readiness ceiling is 60 s and child
exit tracks readiness at 0.95x, so no host that clears that ceiling can lose the diagnostic
inside a 60-second window. The test's collection budget follows the new structure, still reading
its bounds from the module under test as ADR 0129 requires.

## Success

1. The window cannot fire before ADR 0129's budgets apply: a host slow enough to truncate the
   diagnostic has already exhausted the 60-second readiness ceiling.
2. A child that ignores `SIGINT` is still escalated and reaped, so the real-interrupt test fails
   on a bounded 73-second budget rather than stalling.
3. A second `Ctrl-C` escalates immediately, returns `130`, replays the captured output, and
   leaves no orphan; a third kills.
4. The timeout arm is not slowed by the widened window.
5. `just verify` green; `CHANGELOG.md` carries an `## [Unreleased]` entry.
