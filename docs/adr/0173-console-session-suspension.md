# ADR 0173: Mid-session suspension of a console session

## Status

Accepted (2026-09-23). Extends ADR 0170 and stays within ADR 0172. ADR 0170 rule 1 (one hold
per session, `close()` terminal) gains the two suspension modes below. Rule 4 (release belongs to
`close()`) gains one exception: `suspend()` also releases, with the same proof.

## Context

A continuous collector (#957) must yield the console to a preempting hold, such as kdive's KGDB
hold, and then continue. #958's exclusive raw mode needs the vterm to stay held by hmcpctl while
it takes over. The HMC never auto-releases a vterm (P3), so any release and re-acquire leaves a
gap that another client can use. Other consumers do not need that guarantee and want the slot
released.

## Decision

`ConsoleSession` names two modes.

1. **In-process handover (mode a).** `async with session.hand_over() as handover:` moves the
   session's channel to `handover`, whose `read()` returns raw chunks. The vterm stays held: no
   `rmvterm` and no `mkvterm` run on entry or exit. While the handover is active, the session's
   own `read()` waits and delivers nothing. A collector `read()` that is already waiting on the
   stream is cancelled internally, and it waits again without losing a byte (asyncssh 2.24
   blocks only before it has consumed data). Leaving the block gives the channel back to the
   collector, and a handover read still pending then raises `RuntimeError`. Only one
   pause, of either mode, can be active at a time.
2. **Release for an external holder (mode b).** `await session.suspend()` releases exactly as
   `close()` does: `rmvterm`, the independent probe, then connection and stdin teardown. It
   returns the proof as a `bool`. `await session.resume()` acquires again through the same path
   as `open()`. It never issues `rmvterm`, even when the session was built with
   `take_over=True`, because that would end the holder the session made way for. If the slot
   was taken, `resume()` raises `ConsoleHeldError` and the session stays suspended. The caller
   can call `resume()` again later or call `close()`. The collector's `read()` waits while the
   session is suspended.
3. **The release guarantee spans both modes.** `close()` of a held session, with or without an
   active handover, releases as ADR 0170 rule 4 requires. `close()` of a suspended session
   issues no `rmvterm`, because the slot may now belong to the external holder (ADR 0172 rule
   3), and returns the proof from `suspend()`. `suspend()` runs to completion when its caller
   is cancelled, as `close()` does. Cancellation during `resume()` after acquisition releases
   the new hold before `CancelledError` propagates, as `open()` does. The preempting holder
   usually runs in a different task from the session's owner, so `close()` during `suspend()`
   or `resume()` is not refused. It waits for the transition and then tears down: it releases
   a hold that `resume()` acquired, and `resume()` then raises `RuntimeError`. `close()` still
   raises `RuntimeError` during `open()` (ADR 0170 rule 1). A collector that waits through a
   pause when `close()` starts gets `b""`.
4. **Bounded capture does not change.** `capture_lpar_console` uses neither mode.

## Consequences

- #958 builds its write-capable session on mode (a). This record adds no write surface.
- Reconnect while paused belongs to #977, which lands after this record. Neither mode detects a
  dropped channel.
- A suspended session whose `suspend()` could not prove release reports `released=False` from
  `close()` and issues no second `rmvterm`. The operator recovers as ADR 0170 rule 6 describes.
- `resume()` and the external holder race for the slot. The loser sees contention and does not
  retry.
- A held session whose vterm another client took with `rmvterm` still issues `rmvterm` on
  `close()` (ADR 0172 Consequences). This record avoids that only for mode (b), where the
  session knows it gave the slot up.

## Considered & rejected

- **Only mode (b).** judgment: fit. #958 needs a handover with no gap, and #957 names both
  modes.
- **One `pause(mode=...)` method.** judgment: the two modes return different things (a channel
  or a proof) and end differently (block exit or `resume()`), so one signature would blur them.
- **Mode (b) through `close()` and a fresh session.** judgment: fit. The collector would have to
  rebuild its iterator and state, and #976 asks for resume on the same session.
- **The collector's `read()` raises while paused.** judgment: complexity. Every collector would
  need a retry loop, and its own read timeout already bounds the wait.
- **Do nothing: the caller closes the session and opens a new one, and #958 builds its own
  session.** judgment: fit. The close-to-open gap is the one mode (a) exists to remove (P3),
  and #976 asks for resume on the same session.
- **A cooperative handover that waits for the collector's pending read to return.**
  judgment: a silent console never returns that read (P8), so the preempting hold would wait
  for an unbounded time. Cancelling the pending read is what makes the handover immediate.
- **`resume()` honours `take_over`.** judgment: it would end the holder the session made way
  for, and ADR 0172 rule 3 limits `rmvterm` of an unproven hold to an explicit request at open.
