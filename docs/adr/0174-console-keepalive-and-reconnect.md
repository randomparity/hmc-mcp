# ADR 0174: Console keepalive, drop detection, and opt-in reconnect

## Status

Accepted (2026-09-23). Extends ADR 0170 within ADR 0172 and ADR 0173. It amends ADR 0170 rule 3
in part: a transport error still propagates unwrapped unless the caller opted into reconnect.
It amends ADR 0072's P8 rule in part: the HMC still sends no keepalives, but hmcpctl now sends
SSH keepalives on every console connection. It settles the reconnect-while-paused interaction
that ADR 0173 assigned to #977, and the leftover-hold question that ADR 0170 rule 6 and ADR 0172
left to #977: hmcpctl cannot tell its own leftover hold from another holder, so a drop that
meets a held vterm raises a typed error, and only `take_over=True` reclaims it.

> **Amended by #1004** (2026-09-24): decision 2's premise is refuted. Another client's `rmvterm`
> leaves the holder's channel and connection open, so a lost hold is neither a drop nor a remote
> close, and the Consequences bullet on a lost hold whose connection also closes did not occur in
> three runs. See ADR 0172's amendment.

## Context

A continuous collector (#957) must survive a dropped connection, including an idle channel whose
peer is gone, and must learn where output may be missing. The console connection sent no
keepalives, so an idle dead channel stayed silent forever (P8). After an abrupt disconnect the
HMC keeps the vterm held (P3), so a re-acquisition meets that leftover hold. hmcpctl cannot tell
the leftover hold from another client's hold.

## Decision

1. **Keepalive.** `open_hmc_connection` sends an OpenSSH keepalive request every 15 s. After 3
   unanswered requests, asyncssh closes the connection with `ConnectionLost`. A pending read
   raises it, and a later read returns `b""`. Any reply counts as alive, including a request
   failure. The console stream, the acquisition, and the release probe all use this connection.
2. **What a drop is.** The session's collector sees a drop while it owns the channel in one of
   two ways: a read raises `asyncssh.Error` or `OSError`, or a read returns `b""` while the
   session's connection reports `is_closed()`. `b""` on an open connection is a remote close,
   and it is latched for that stream: later reads return `b""` and never reconnect, until
   `resume()` acquires a new stream. A lost hold (another
   client's `rmvterm`, #1004) ends `mkvterm` but not the connection, as far as anyone knows, so
   it is a remote close. #879's live evidence is what can confirm that. No drop counts once
   `close()` has begun: the read then behaves as it does without reconnect.
3. **Reconnect is opt-in.** `ConsoleSession(..., reconnect=False)` is the default. Without it, a
   drop behaves exactly as ADR 0170 rule 3 says. `capture_lpar_console` never sets it, so the
   capture still ends with `stop_reason="error"` on a read failure.
4. **Reconnect.** With `reconnect=True`, a drop starts one session-owned task. The task closes
   the dead channel and makes one acquisition through the `open()` path. On success, `read()`
   returns a `ConsoleGap(error, took_over)` marker before the new stream's first bytes. `read()`
   awaits the task through a shield, so a consumer's read timeout neither cancels the reconnect
   nor loses its outcome. `close()` cancels the task, waits for it to finish, and releases a
   hold that the task acquired.
5. **Default after a drop: typed error, no reclaim.** When a re-acquisition meets a held vterm,
   the session raises `ConsoleHeldAfterDropError`, a `ConsoleHeldError`. It issues no `rmvterm`,
   because hmcpctl cannot prove the hold is its own (ADR 0172 rule 3). A failed connect or
   acquisition raises `HMCCLIError`. Either failure leaves the session dropped: `read()`,
   `hand_over()` and `suspend()` raise `RuntimeError`, and `close()` returns `False` with no
   `rmvterm`. Reclaiming uses #975's option: with `take_over=True`, the reconnect issues
   `rmvterm` before `mkvterm`, and the gap reports `took_over=True`. After the typed error, the
   consumer's recovery is `close()` (`released=False`) and a new session opened with
   `take_over=True`.
6. **Reconnect while paused.** Mode (a): the handover's reader receives a drop as an exception
   or `b""`, and the session does not reconnect under an in-process holder. When the block
   exits, the collector's next read finds the closed connection and reconnects. Mode (b): a
   suspended session holds no connection and cannot drop. `resume()` stays the only
   re-acquisition, never issues `rmvterm`, and raises plain `ConsoleHeldError`. A reconnect in
   progress, a reconnect outcome not yet read, or a dropped session refuses both modes. A drop
   that the collector's read reports after a pause began starts no reconnect; the pause's holder
   or `resume()` meets the dead connection.

## Consequences

- Under P3 a real drop leaves the old hold in place, so `reconnect=True` without
  `take_over=True` usually ends in `ConsoleHeldAfterDropError`, not a gap. What it adds over
  doing nothing is detection and a typed error. Collecting across drops without a manual step
  needs both options.
- A dead console channel is detected within about 60 s. The bounded capture can therefore end
  with `"error"` before its idle or duration bound fires. Its result mapping is unchanged.
- Consumers that enable reconnect must handle `ConsoleGap` items in the stream. The session's
  `read()` type is now `bytes | ConsoleGap`, including for sessions that never emit one.
- A session with `take_over=True` and `reconnect=True` ends whatever holds the vterm after every
  drop. That includes a client that acquired during the outage.
- A lost hold whose SSH connection also closes would be treated as a drop. With `take_over=True`
  this would end the new holder's session. That stays unverified until #879.
- A server that closes the `mkvterm` channel before the connection, for example an HMC
  shutting down, reads as a remote close: the stream ends with no gap. This is unverified
  until #879.
- A drop seen during `suspend()`'s release or `open()`'s acquisition keeps its existing
  behavior. Reconnect covers only the collector's reads.

## Considered & rejected

- **Do nothing; the consumer reopens a session after a read error.** judgment: fit. #977 asks
  for detection, a typed signal for the leftover hold, and a gap marker in one iterated
  stream when the caller opts into reclaiming.
- **Reclaim the leftover hold by default.** judgment: fit. #957 and ADR 0172 rule 3 reserve
  `rmvterm` of an unproven hold for an explicit `take_over=True`.
- **Signal the gap with an exception the consumer catches before reading on.** judgment: fit.
  #977 asks for a marker in the iterated stream, and an exception ends an `async for`.
- **A sentinel byte string as the marker.** judgment: fit. Any byte string can occur in raw
  console output, so a consumer could not tell the marker from data.
- **Reconnect inside the reader's own task.** judgment: fit. The handshake is already
  shielded (`_await_acquisition`), so a timed-out read would block past its timeout until
  acquisition finished, against ADR 0170 rule 3's usable-after-timeout contract.
- **Retry inside the session.** judgment: a keepalive drop is declared only after about 60 s
  of silence, no recorded outage says a few more seconds would outlast it, and contention, the
  likely outcome, is not retryable. The consumer owns any retry policy.
- **Classify a drop by the channel's missing exit status.** judgment: complexity. No recorded
  evidence shows that `mkvterm` reports an exit status when it ends. `is_closed()` is a
  connection fact: asyncssh's `_cleanup` sets it in the same synchronous step that queues the
  channel's error, so the reader resumes after it is set (`asyncssh/connection.py`, 2.24.0).
