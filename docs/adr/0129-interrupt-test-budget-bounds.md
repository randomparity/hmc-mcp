# ADR 0129: The real-interrupt test's budgets are sized from bounds in the system

## Status

Accepted

## Context

`tests/scripts/test_run_tests.py::test_real_interrupt_preserves_pytest_diagnostic` is the only
test that drives `scripts/run_tests.py`'s `KeyboardInterrupt` path with a real child. It spawns
the script in a new session, waits for the grandchild pytest to touch a readiness marker, sends
`SIGINT` to the process group, and asserts `returncode == 130`, empty stdout, and
`KeyboardInterrupt` in stderr.

Two independent fixed 10-second constants guard that path: `_wait_for_process_marker`'s
`timeout_seconds` default and the `process.communicate(timeout=10)` after the signal. Both were
sized on an unloaded machine. Measured on one host — x86_64 Linux, CPython 3.13, the whole
process tree pinned to a single core with `taskset` and N busy-loop processes on that core:

| Spinners on the core | Readiness | Post-`SIGINT` collection | `KeyboardInterrupt` in stderr |
|---|---|---|---|
| 0 (48 idle cores) | 0.23–0.74 s | 0.22 s | yes |
| 8 | 0.84–0.86 s | 0.68–0.73 s | yes |
| 40 | 3.92–4.19 s | 3.34 s | yes |
| 80 | 7.28–8.77 s | 3.36–3.42 s | **no** |

Three things follow.

**Only the readiness wait races host latency.** It reached 8.77 s against its 10 s budget.
Collection does not: `_settle_interrupted` (`scripts/run_tests.py:21-31`) waits
`INTERRUPT_GRACE_SECONDS` (3), terminates, waits again, then kills — so collection is clamped
by the code under test rather than by the host, and it stayed under 3.42 s across a 38x
readiness span.

**The failure host load actually produces is not a timeout.** At 80 spinners the grandchild
pytest could not finish its interrupt report inside the 3-second grace, `_settle_interrupted`
sent `SIGTERM`, and `KeyboardInterrupt` never reached stderr. The test fails there as an
`AssertionError` on `tests/scripts/test_run_tests.py:288`, with both 10-second budgets
unexhausted. No change to either budget addresses it; the governing constant is
`INTERRUPT_GRACE_SECONDS`, in a file this change may not touch.

**A budget multiplied out of the readiness observation would price the wrong quantity.**
Readiness and collection track each other only below the clamp.

## Decision

**Both budgets stay constants, and each is sized against a bound that exists in the system
rather than against a stopwatch on an idle machine.**

- `_wait_for_process_marker`'s default becomes `_READINESS_TIMEOUT_SECONDS = 60.0`. It is a
  hang ceiling, not a latency budget: the poll loop returns the moment the marker appears, so
  its size is paid only by a child that never becomes ready. Raising it trades away no
  headroom, and no slower future target consumes it.
- The collection budget becomes
  `2 * run_tests.INTERRUPT_GRACE_SECONDS + _INTERRUPT_COLLECTION_SLACK_SECONDS`, reading the
  first term from the module the test already imports. That term is `_settle_interrupted`'s
  bounded portion, up to the `kill()`; the 10-second second term covers the unbounded post-kill
  reap, `_replay`, and the parent's own teardown, which the first term does not bound.
- `communicate` is wrapped: on `subprocess.TimeoutExpired` the test kills the group, drains,
  and calls `pytest.fail` with a message naming the budget. A teardown timeout is therefore
  never reported as an assertion on `returncode`, `stdout`, or `stderr`.
- The test times the interval from `SIGINT` to exit. A missing `KeyboardInterrupt` after an
  interval that reached `INTERRUPT_GRACE_SECONDS` fails with its own message naming the
  truncation, because reaching the grace is exactly the condition under which
  `_settle_interrupted` escalates to `SIGTERM`. Every other missing diagnostic still falls
  through to the existing assertion. Without this branch the raised readiness ceiling would
  make things worse where readiness lands between 10 s and 60 s: the old code failed there with
  `_wait_for_process_marker`'s clear marker message, and the new code would instead reach the
  stderr assertion and report a host artifact in the shape of a regression.

For the collection path this is issue #721's option 2, taken deliberately rather than for
simplicity. Its stated objection — that a constant reinstates the defect on the next slower
target — applies to a budget racing host latency. This one races a constant inside
`scripts/run_tests.py`, so a slower target moves the readiness wait and leaves it alone.

## Consequences

- The test now reads `run_tests.INTERRUPT_GRACE_SECONDS`. Raising that constant raises the
  test's budget with it, and lowering it tightens it. The coupling is the point, but it means a
  `scripts/run_tests.py` edit moves a test budget.
- Worst case is 16 s, fixed, against the `ci` job's `timeout-minutes: 20`. A child that ignores
  `SIGINT` fails the test in 16 s, and one that never becomes ready in 60 s.
- The readiness wait becomes the binding constraint on every host: collection is clamped near
  3.4 s, so a host slow enough to spend 16 s there would have exhausted the 60 s readiness
  ceiling first and reported a hang.
- **Residual, named but not closed: the truncated diagnostic.** The branch above reports it
  accurately; it does not stop it happening. `INTERRUPT_GRACE_SECONDS` is in
  `scripts/run_tests.py`, outside this change's frozen surface, and moving it is a decision
  about that script's production contract rather than about a test budget. Owner: a follow-up
  candidate returned to this run's caller, which is where filing authority sits — an unattended
  run cannot obtain the confirmation `$bounty` requires. Weakening the `KeyboardInterrupt`
  assertion is not the remedy: it is the behaviour the test exists to prove.
- Every number here comes from one amd64 host on CPython 3.13. The eight `ci` legs, including
  the four native `ubuntu-24.04-arm` ones, are unmeasured. Both constants are sized to sit far
  above any plausible leg rather than tuned per leg, which is what makes that acceptable.

## Considered & rejected

- **Derive the collection budget from the observed readiness latency** (issue #721's preferred
  option 1). verified: the table above — readiness moved 0.23 s → 8.77 s while collection
  stayed under 3.42 s, because `scripts/run_tests.py:21-31` clamps it. judgment: a multiplier
  over readiness prices a quantity readiness does not predict, and a generous multiple over the
  60 s ceiling would put the worst case past 300 s against a 20-minute leg.
- **Leave both constants at 10 s.** verified: readiness reached 8.77 s at 80-way contention on
  one core, 1.14x from the budget. judgment: the tighter of the two is one step of load from
  expiring, and it expires as `TimeoutExpired`, which reads as a defect in the interrupt
  handling.
- **Make `INTERRUPT_GRACE_SECONDS` host-derived, or raise it.** verified: it governs the only
  outright test failure reproduced above (80 spinners, `KeyboardInterrupt` absent). judgment:
  correct, and outside this change's frozen surface — it changes `scripts/run_tests.py`'s
  production behaviour rather than a test budget. Recorded as the residual above rather than
  taken silently.
- **Bound collection by observed progress instead of wall clock** — reset a deadline on each
  chunk read from the child's pipes. verified: `scripts/run_tests.py:66` replays the captured
  file in one `copyfileobj` after the grandchild has already exited, so no incremental output
  precedes the exit the budget is waiting for. judgment: also more machinery than a clamped
  path needs.
- **Give the whole test a ceiling with `pytest-timeout`.** verified: `pyproject.toml` declares
  no `pytest-timeout` dependency and no `timeout` ini option, so this adds one. judgment: a
  whole-test ceiling cannot separate a slow readiness wait from a hung teardown, which is the
  distinction the issue asks for.
- **Skip the test instead of failing it when the diagnostic is truncated.** verified: ADR 0128
  rejected the same shape for L5 — "a green run that silently proved nothing is a worse failure
  mode than the misleading red it replaces". judgment: the same reasoning holds here, and the
  named red costs a reader one line to understand.
- **Do nothing.** verified: readiness reached 8.77 s against its 10 s budget at 80-way
  contention, and issue #721 reports the wait expiring on a loaded host during review of
  PR #720. judgment: a constant one step of load from expiring produces an intermittent red
  that passes on re-run, which teaches readers to re-run — which is how a real
  interrupt-handling regression gets re-run away.
