# ADR 0130: The interrupt diagnostic window and the reap are separate bounds

## Status

Accepted

Extends [ADR 0129](0129-interrupt-test-budget-bounds.md), closing the residual it recorded. Its
two test budgets stand unchanged; this record corrects one of its Context findings and sets the
production bounds that residual named.

## Context

`_settle_interrupted` (`scripts/run_tests.py:21-31`) spends one constant,
`INTERRUPT_GRACE_SECONDS = 3`, on two different waits: one before `SIGTERM`, one before `SIGKILL`.
Issue #728 reports that the first truncates an interrupted pytest's `KeyboardInterrupt` report on
a loaded host; ADR 0129 named it as a residual it could not take. ADR 0129 measured collection
*through* that 3-second clamp and concluded it "is clamped by the code under test rather than by
the host". Measured again with the clamp lifted to 600 s — same method, whole tree pinned to one
core with `taskset` alongside N busy-loop spinners; x86_64, 48 cores, CPython 3.11.15; 3 repeats:

| Spinners | Readiness | Diagnostic fully written | Child exits |
|---|---|---|---|
| 0 | 0.29-0.88 s | already written | 0.26-0.28 s |
| 40 | 13.0-13.6 s | 7.97-8.20 s | 12.5-12.7 s |
| 80 | 25.1-27.3 s | 15.3-16.9 s | 23.5-26.6 s |

Collection does not stop racing host latency below the clamp; the clamp is what hid that it never
did. The grace is therefore a latency budget sized on an idle machine — the shape ADR 0129 itself
rejected for the test budgets. Two loss boundaries have been observed, not three: the unmodified
script loses the diagnostic at 40 spinners here and ADR 0129's host 2 also lost it at 40, while
its host 1 held to 80. This host matches host 2 within about 8% under contention and diverges
about 3x from host 1, yet all three agree at idle. That divergence is unexplained: interpreter
version does not account for it, since holding the plugin set constant across CPython 3.11.15,
3.12.13, 3.13.14 and 3.14.7 on this host at 80 spinners moved the diagnostic only 15.1-17.3 s,
with no ordering by version — measured during review of this change.

**That fixture is not the workload the constant guards.** The rows above run one `test_slow` in a
bare directory. `just test` wraps the repository suite: 4699 items under
`addopts = "--cov=hmc_mcp --cov-report="` (`pyproject.toml:101`). Measured the same way, with the
interrupt sent 5 s after collection finished:

| Spinners | Diagnostic fully written | Child exits |
|---|---|---|
| 0 | 0.68 s | 1.6 s |
| 8 | 8.41 s | 15.7 s |
| 40 | 36.8 s | 68.5 s |
| 80 | 72.9 s | 133.2 s |

The real suite costs 5.2-6.3x the fixture at every load, and the 80-spinner row was reproduced
end-to-end against an earlier draft of this change that used a 60-second window: the window
expired, `SIGTERM` arrived 12.9 s before pytest finished, and the replay stopped mid-line with
`KeyboardInterrupt` absent — issue #728 verbatim. The 80-spinner figures come from review of this
change; the other three rows were taken directly.

The diagnostic's share of readiness is not a stable ratio either. On this host at 80 spinners it
was 0.62-0.73x with the repository's plugin set, 0.82-0.84x with a plugin-matched scratch venv,
and 1.25-1.48x with a pytest-only venv — the diagnostic outlasting readiness there. Readiness is
dominated by interpreter start, plugin import and collection, which warm caches make cheap; the
diagnostic is traceback formatting, which they do not. The ratio follows the installed plugin set
and cache state rather than the host.

The second wait guards a different quantity. Against a child ignoring the signals,
`SIGTERM`-to-reaped took 3.6 ms, 121 ms and 224 ms at 0, 40 and 80 spinners, and
`SIGKILL`-to-reaped 2.9 ms, 84 ms and 345 ms — 0.22 s at the load where the diagnostic needs 16.9 s.

A second `Ctrl-C` inside the window is also unhandled: `_settle_interrupted` catches only
`subprocess.TimeoutExpired`, so a `KeyboardInterrupt` raised there escapes `main`, `_replay`
(`scripts/run_tests.py:66`) never runs, the captured output is discarded entirely, the status is
`-2` not `130`, and the child is orphaned. Reproduced at 40 spinners against `313256d2`.

## Decision

**Two constants, each sized to the quantity it guards, and a second `Ctrl-C` escalates.**

- `INTERRUPT_GRACE_SECONDS = 300` — the diagnostic window, before `SIGTERM`. It is sized against
  the repository suite, not the fixture: 4.1x the worst diagnostic write measured (72.9 s at
  80-way single-core contention) and 2.3x the worst child exit (133.2 s), at a load no CI leg or
  developer machine approaches. It stays above `_READINESS_TIMEOUT_SECONDS` so the script and its
  test are not guessed apart, but that floor is a coupling rather than a proof — the ratio above
  moves with the plugin set, so a window over the ceiling does not by itself put truncation out of
  reach. What makes a number this large affordable is that the wait returns the moment the child
  exits, so only a child that has not exited pays it, and the escape hatch below bounds the
  interactive case whatever the number is.
- `TERMINATE_GRACE_SECONDS = 3` — the reap, between `SIGTERM` and `SIGKILL`. Value unchanged, and
  roughly 9x the worst reap measured.
- Every wait catches `KeyboardInterrupt`, so a second `Ctrl-C` escalates at once and a third
  kills. A generous window then costs an interactive developer only the wait they decline to cut
  short.
- The timeout arm escalates directly, skipping the window: nothing has asked that child to stop,
  so waiting cannot help it write anything, and without this split raising the shared constant
  would add 57 s to a path this change has no business slowing. The test's collection budget
  becomes the two bounds plus `_INTERRUPT_COLLECTION_SLACK_SECONDS`, keeping ADR 0129's coupling.

## Consequences

- **The window is a diagnostic window only when something outside the wrapper delivered `SIGINT`
  to the child too** — terminal `Ctrl-C` and the test's `killpg`, both process-group delivery.
  `scripts/run_tests.py` never signals its own child before the wait, so a `SIGINT` aimed at the
  wrapper's pid alone leaves the child running, and the wait cannot preserve a report the child
  was never asked to write. This change lengthens that dead wait: reproduced at 3.26 s before and
  60.26 s after. Its bound is the escape hatch, a second signal escalating at once, and it costs
  a wait rather than a diagnostic, because on that path there is none to lose.
- ADR 0129's residual is closed. Its decision stands; its Context finding that collection is
  host-independent is corrected by the table above, as are the Consequences at ADR 0129:92-93
  resting on the same clamp. Its rejection of a readiness-derived collection budget
  (ADR 0129:117-121) survives on its second ground — a generous multiple over the 60 s ceiling
  puts the worst case past 300 s — not on the clamp. The coupling widens and reverses:
  `TERMINATE_GRACE_SECONDS` now moves the test budget too, and since the test asserts the window
  covers the readiness ceiling, raising `_READINESS_TIMEOUT_SECONDS` forces
  `INTERRUPT_GRACE_SECONDS` up with it, so the property ADR 0129's *Decision* claims for that
  ceiling — "raising it trades away no headroom" — no longer holds. The test's collection budget rises from 16 s to 73 s and its overall worst case
  from 76 s to 373 s against the `ci` job's 20-minute leg, paid only by a child that does not exit.
- The ladder is bounded, not total: the post-`SIGKILL` reap suppresses a further
  `KeyboardInterrupt`, but `_replay` does not, so an interrupt during the replay still discards the
  remaining output. A developer whose pytest is wedged waits 5 minutes rather than 6 s unless they
  interrupt again, and two rapid `Ctrl-C`s now truncate the diagnostic deliberately, where before
  they discarded it, misreported the status and orphaned the child.
- Every number comes from one amd64 host, where ADR 0129 measured two hosts on 3.13. The
  interpreter axis is settled by the four-version table above and is flat, so the unmeasured
  residual is architecture alone: the four native `ubuntu-24.04-arm` legs. Both constants sit far
  above any plausible leg rather than being tuned per leg, which is what that flat axis supports.

## Considered & rejected

- **Raise `INTERRUPT_GRACE_SECONDS` and change nothing else.** verified: it is spent on both waits
  (`scripts/run_tests.py:21-31` at `313256d2`), so raising it to 300 makes the reap wait 300 s —
  over 800x the worst reap measured — and adds the same to the timeout arm. judgment: issue #728 and
  ADR 0129:126-130 reject a bare raise as reinstating the defect on a slower target; separating
  the constants is what makes the larger number answer to measurement, and stops the smaller one
  paying for it.
- **Forward `SIGINT` to the child before the wait**, so the window means the same on every
  delivery path. verified: reproduced at 40 spinners with a wrapper sending
  `process.send_signal(SIGINT)` on `KeyboardInterrupt` — on the process-group path the child takes
  a second `SIGINT` while formatting the first one's report and emits 17,253 bytes of
  `_pytest/main.py` internal traceback, ending inside `inspect.getmodule`, in place of the clean
  663-byte `test_slow.py:6: KeyboardInterrupt` summary. judgment: it cures the dead wait above by
  destroying the diagnostic on the path this change exists to protect, and `KeyboardInterrupt`
  survives in that dump, so no assertion here would catch the loss.
- **Remove the window; wait unbounded with the `KeyboardInterrupt` escape as the only bound.**
  verified: the real-interrupt test supplies its own ceiling through `communicate(timeout=budget)`
  (`tests/scripts/test_run_tests.py:365`), so the suite fails rather than hangs. judgment: a
  non-interactive `SIGINT` sender has no escape hatch to press, so the wrapper waits forever on
  exactly the wrapper-only path priced above; the split buys a bound for three lines.
- **Escalate on absence of output rather than wall clock** (issue #728's candidate 2). verified:
  polling the capture file every 20 ms at 80 spinners gives 383 bytes at t=0, silence until
  t=16.9 s, then the burst carrying `KeyboardInterrupt`; the silence is the child formatting its
  traceback under contention, not a pause between writes. judgment: an idle threshold would have
  to exceed that silence — 16.9 s for the fixture here, and longer for the repository suite or on
  a slower host — purely to avoid killing the burst it exists to preserve, which is the same
  constant racing the same host speed, in more machinery.
- **Derive the window from a host measurement taken at run time** (issue #728's candidate 1).
  verified: `scripts/run_tests.py` observes no readiness marker, and the only timing it holds is
  how long pytest ran before the interrupt — set by the suite's length and the user's patience,
  not by the host. judgment: the derivation has no input, and a synthetic calibration loop is
  machinery this path does not need.
- **Do nothing.** verified: the diagnostic is lost at 40 spinners here, at 40 on ADR 0129's host 2
  and at 80 on its host 1. judgment: a developer interrupting a slow suite gets a truncated report
  with nothing indicating it was truncated.
