# ADR 0130: The interrupt diagnostic window and the reap are separate bounds

## Status

Accepted

## Context

`_settle_interrupted` (`scripts/run_tests.py:21-31`) spends one constant,
`INTERRUPT_GRACE_SECONDS = 3`, on two different waits: one before `SIGTERM` and one before
`SIGKILL`. Issue #728 reports that the first truncates an interrupted pytest's
`KeyboardInterrupt` report on a loaded host; ADR 0129 named it as a residual it could not take.

ADR 0129 measured collection *through* that 3-second clamp and concluded it "is clamped by the
code under test rather than by the host". Measured again with the clamp lifted to 600 s — same
method, whole tree pinned to one core with `taskset` alongside N busy-loop spinners; x86_64,
48 cores, CPython 3.11.15; 3 repeats per row:

| Spinners | Readiness | Diagnostic fully written | Child exits |
|---|---|---|---|
| 0 | 0.29-0.88 s | already written | 0.26-0.28 s |
| 8 | 2.74-2.79 s | 1.56-1.59 s | 2.66-2.74 s |
| 40 | 13.0-13.6 s | 7.97-8.20 s | 12.5-12.7 s |
| 80 | 25.1-27.3 s | 15.3-16.9 s | 23.5-26.6 s |

Exit tracks readiness at 0.95x across a 94x readiness span. Collection does not stop racing host
latency below the clamp; the clamp is what hid that it never did. The grace is therefore a
latency budget sized on an idle machine — the shape ADR 0129 itself rejected for the test
budgets. The unmodified script loses the diagnostic between 16 and 40 spinners here, where
ADR 0129 lost it at 80 on its host 1 and 40 on its host 2: three hosts, three boundaries.

The second wait guards a different quantity. Against a child ignoring the signals,
`SIGTERM`-to-reaped took 3.6 ms, 121 ms and 224 ms at 0, 40 and 80 spinners, and
`SIGKILL`-to-reaped 2.9 ms, 84 ms and 345 ms — 0.22 s at the load where the diagnostic needs
16.9 s.

A second `Ctrl-C` inside the window is also unhandled: `_settle_interrupted` catches only
`subprocess.TimeoutExpired`, so a `KeyboardInterrupt` raised there escapes `main`, `_replay`
(`scripts/run_tests.py:66`) never runs, the captured pytest output is discarded entirely, the
status is `-2` rather than the specified `130`, and the child pytest is left orphaned.
Reproduced at 40 spinners against `313256d2b0f0ec39a4198e7cca498cc49541cf3c`.

## Decision

**Two constants, each sized to the quantity it guards, and a second `Ctrl-C` escalates.**

- `INTERRUPT_GRACE_SECONDS = 60` — the diagnostic window, before `SIGTERM`. Sized from a bound
  already in the system rather than a stopwatch: ADR 0129 set the readiness ceiling to 60 s and
  exit tracks readiness at 0.95x, so a host slow enough to lose the diagnostic inside a
  60-second window has already failed that ceiling and been reported as a hang. The two bounds
  are now consistent instead of independently chosen.
- `TERMINATE_GRACE_SECONDS = 3` — the reap, between `SIGTERM` and `SIGKILL`. Value unchanged,
  and roughly 9x the worst reap measured.
- Both waits also catch `KeyboardInterrupt`, so a second `Ctrl-C` escalates immediately. A
  generous window then costs an interactive developer only the wait they decline to cut short.
- The timeout arm escalates directly, skipping the window: nothing has asked that child to stop,
  so waiting cannot help it write anything, and without this split raising the shared constant
  would add 57 s to a path this change has no business slowing. The test's collection budget
  becomes the two bounds plus `_INTERRUPT_COLLECTION_SLACK_SECONDS`, keeping ADR 0129's coupling
  to the module under test.

## Consequences

- ADR 0129's residual is closed. Its decision stands unchanged; the Context finding that
  collection is host-independent is corrected by the table above. Its coupling widens, in that
  `TERMINATE_GRACE_SECONDS` now moves the test budget too.
- The real-interrupt test's collection budget rises from 16 s to 73 s and its overall worst case
  from 76 s to 133 s, against the `ci` job's 20-minute leg — paid only by a child that ignores
  `SIGINT`, which is the defect the test exists to report.
- A developer whose pytest is genuinely wedged waits 60 s rather than 6 s unless they press
  `Ctrl-C` again. Two rapid `Ctrl-C`s now truncate the diagnostic deliberately, where before they
  discarded it, misreported the status and orphaned the child.
- Every number here comes from one amd64 host on CPython 3.11.15, where ADR 0129 measured two
  hosts on 3.13. The eight `ci` legs, the four native `ubuntu-24.04-arm` ones especially, remain
  unmeasured, as they were for ADR 0129; both constants sit far above any plausible leg rather
  than being tuned per leg.

## Considered & rejected

- **Raise `INTERRUPT_GRACE_SECONDS` and change nothing else.** verified: it is spent on both
  waits (`scripts/run_tests.py:21-31` at `313256d2`), so raising it to 60 makes the reap wait
  60 s — 270x the worst reap measured — and adds 57 s to the timeout arm. judgment: issue #728
  and ADR 0129:126-130 reject a bare raise as reinstating the defect on a slower target;
  separating the constants is what makes the larger number answer to a bound in the system, and
  stops the smaller one paying for it.
- **Escalate on absence of output rather than wall clock** (issue #728's candidate 2).
  verified: polling the capture file every 20 ms at 80 spinners gives 383 bytes at t=0, silence
  until t=16.9 s, then the burst carrying `KeyboardInterrupt`; the silence is the child
  formatting its traceback under contention, not a pause between writes. judgment: an idle
  threshold would have to exceed 16.9 s here and roughly 50 s on ADR 0129's slower host purely to
  avoid killing the burst it exists to preserve — the same constant racing the same host speed,
  in more machinery.
- **Derive the window from a host measurement taken at run time** (issue #728's candidate 1).
  verified: `scripts/run_tests.py` observes no readiness marker, and the only timing it holds is
  how long pytest ran before the interrupt — set by the suite's length and the user's patience,
  not by the host. judgment: the derivation has no input, and a synthetic calibration loop is
  machinery this path does not need.
- **Do nothing.** verified: the diagnostic is lost at 40 spinners here, at 40 on ADR 0129's
  host 2, and at 80 on its host 1. judgment: a developer who interrupts a slow suite gets a
  truncated report with nothing indicating that it was truncated.
