# ADR 0129: The real-interrupt test derives its collection budget from observed readiness

## Status

Accepted

## Context

`tests/scripts/test_run_tests.py::test_real_interrupt_preserves_pytest_diagnostic` is the only
test that exercises `scripts/run_tests.py`'s `KeyboardInterrupt` path against a real child. It
spawns the script in a new session, waits for the grandchild pytest to touch a readiness
marker, sends `SIGINT` to the process group, and asserts `returncode == 130`, empty stdout,
and `KeyboardInterrupt` in stderr.

Two independent fixed 10-second constants guard that path: `_wait_for_process_marker`'s
`timeout_seconds` default and the `process.communicate(timeout=10)` that follows the signal.
Both were sized on an unloaded machine. Measured on this branch (x86_64 Linux, CPython 3.13,
`taskset` pinning the whole tree to one core):

| Contention on the core | Readiness | Post-`SIGINT` teardown | Teardown / readiness |
|---|---|---|---|
| none (48 idle cores) | 0.23–0.24 s | 0.22 s | 0.91 |
| 8 spinners | 0.84–0.86 s | 0.68–0.73 s | 0.85 |
| 40 spinners | 3.92–4.19 s | 3.34 s | 0.83 |

Two things follow. Teardown tracks readiness closely — 0.83 to 0.91 across a 17x span — so
readiness is a usable yardstick for the teardown budget. And the readiness wait is the tighter
of the two constants: at 40 spinners it retains 2.4x headroom while the collection budget still
has 3x. A constant sized against an idle machine therefore expires on load, and it expires as
`subprocess.TimeoutExpired`, which reads as a defect in the interrupt handling rather than as
the host being busy. The eight-leg `ci` matrix runs native `ubuntu-24.04-arm` alongside
`ubuntu-24.04`, so the slowest leg has the least headroom against a constant tuned elsewhere.

`scripts/run_tests.py` also bounds its own post-`SIGINT` behaviour: `_settle_interrupted`
(lines 21–31) waits `INTERRUPT_GRACE_SECONDS`, terminates, waits again, then kills. A
collection budget below `2 * INTERRUPT_GRACE_SECONDS` would expire inside a settle path the
code under test explicitly permits.

## Decision

**The readiness wait stays a constant and becomes a hang ceiling; the collection budget is
derived from the readiness the run actually observed.**

`_wait_for_process_marker` returns the elapsed time it already measures. The call site
computes

    2 * run_tests.INTERRUPT_GRACE_SECONDS + max(10.0, 5.0 * readiness_seconds)

and passes that to `communicate`. The first term is the code under test's own worst-case
settle bound, read from the module the test already imports. The second is a host-scaled
allowance, floored so that a suspiciously fast marker touch cannot produce a tight bound.

The readiness wait's default rises to 60 s. It is not a latency budget: the poll loop returns
the moment the marker appears, so its size is paid only by a child that never becomes ready.
That is why it stays a constant while the other budget does not — there is no earlier
observation to derive it from, and no passing run spends it.

`communicate` is wrapped: on `subprocess.TimeoutExpired` the test kills the process group,
drains, and calls `pytest.fail` with a message naming the observed readiness and the derived
budget. A timing failure is therefore never reported as an assertion on `returncode`,
`stdout`, or `stderr`.

## Consequences

- The multiple of 5 carries roughly 6x headroom over the measured 0.83–0.91 relationship. It
  is one number for every platform; no per-leg tuning is introduced.
- Worst case, readiness consumes its 60 s ceiling and the derived budget becomes 306 s,
  against the `ci` job's `timeout-minutes: 20`. In the ordinary case — readiness under a
  second — a child that ignores `SIGINT` fails the test in about 16 s.
- The test now reads `run_tests.INTERRUPT_GRACE_SECONDS`. Changing that constant moves the
  test's budget with it. The coupling is the point, but it means a `scripts/run_tests.py` edit
  can change a test budget without touching the test.
- Calibration rests on the measured relationship above, not on a mechanism argument: pytest's
  interrupt unwind is not the same work as its startup and collection. A future change that
  makes teardown expensive independently of startup — a large captured diagnostic, an
  `atexit` hook — would break the correlation, and the floor is what absorbs it.
- The readiness ceiling remains the hang detector. A child that never touches the marker is
  still killed and still fails the test.

## Considered & rejected

- **Raise both constants to a value with headroom on the slowest leg** (issue #721's own
  option 2). verified: readiness moved 0.23 s → 4.19 s across the measured contention range
  above, an 18x span reached on one desktop; nothing bounds the ratio, so every constant has a
  load at which it expires. judgment: the issue's own objection stands — it reinstates the
  defect the next time the matrix gains a slower target.
- **Measure the host with a separate probe** before spawning — time a bare interpreter start
  or a pytest import. verified: the readiness marker already measures interpreter start plus
  pytest import plus collection, inside the process actually under test. judgment: a second
  spawn per run to measure what the first spawn measures for free.
- **Drop the collection budget entirely** — plain `communicate()`. verified:
  `scripts/run_tests.py:21-31` bounds its own settle path and then `kill()`s, so an unbounded
  wait is reached only by a child surviving `SIGKILL`. judgment: the issue requires a bound so
  a hang fails rather than stalling the job, and an unlanded `kill()` is exactly the class
  worth catching.
- **Put a whole-test ceiling on it with `pytest-timeout`.** verified: `pyproject.toml`
  declares no `pytest-timeout` dependency and no `timeout` ini option, so this adds one.
  judgment: a whole-test ceiling cannot separate a slow readiness wait from a hung teardown,
  which is the distinction the issue asks for, and a dependency for one test is
  disproportionate.
- **Do nothing.** verified: the table above; the tighter constant retains 2.4x headroom at 40
  spinners on one core. judgment: an intermittent red that passes on re-run teaches readers to
  re-run, which is how a real interrupt-handling regression gets re-run away.
