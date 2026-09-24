# Console lost-hold detection (#1004)

Decision record: the `#1004` amendments in the Status sections of ADR 0172 and ADR 0174.

## Problem

A live `ConsoleSession` never notices when another client's `rmvterm` ends its hold. That client can
be a manual `rmvterm` or a `take_over=True` session. The session's `close()` then issues `rmvterm`,
which ends the new holder's session (ADR 0172 Consequences).

Live capture (V10R3 M1060, 2026-09-24, three runs; `tests/fixtures/console/lost-hold-transcript.json`):

- About 1.5 s after the `rmvterm`, the holder receives one 98-byte chunk:
  `\r\n Connection has closed \r\n\r\n\r\n This session is no longer connected. Please close this window.\n\n\n `.
- The `mkvterm` channel then stays open and silent, with no EOF, exit status, or channel close for
  40 s. The SSH connection stays usable.
- Closing the old holder's connection leaves the taker's hold intact.

A dropped connection closes the connection, and a lost hold does not, so the two differ.

## Design

- `LOST_HOLD_SENTINEL` is the recorded chunk minus its trailing space (judgment: its bare
  `\n\n\n` makes text relayed through a guest tty with `onlcr` unlikely to match).
- Every channel read (collector, handover, raw channel) passes through `_read_for`.
  - Match the sentinel against that chunk plus the last `len(LOST_HOLD_SENTINEL) - 1` bytes of the
    same stream. `_acquire` resets those bytes.
  - A match while `held` sets the state to `lost`, and that read still returns its chunk.
  - Later `_read_for` calls raise `ConsoleHoldLostError(HMCError)`. That is not an
    `asyncssh.Error`, so it never triggers a reconnect.
- `_release_hold` (behind `close()` and `suspend()`) first cancels and awaits any read in flight,
  then scans bytes already received but unread (reads of at most 0.1 s, 1 s in total). A message
  the caller never read still latches `lost`. Any scan error falls through to `rmvterm`. A `lost`
  hold drops only its own connection, logs a warning, issues no `rmvterm`, and returns `False`
  (unproven, like a dropped session); after `suspend()`, `resume()` then meets the taker's hold.
- Unchanged code covers the rest. `hand_over`, `suspend`, `raw_mode`, and writes require `held`, so
  they raise `RuntimeError` after a loss. `capture_lpar_console` reports the loss as
  `stop_reason="error"` with `released=False`.

## Failure model

1. **Actors and deployments:** library callers, the MCP capture tool, and the CLI, each driving one
   session against a V10 HMC. Untrusted: the partition's console output and other console clients.
2. **Invariants and assets:** ADR 0170's release guarantee for a hold the session still owns, and
   another client's live hold.
3. **Accepted failure classes:**
   - Console output that reproduces the sentinel byte for byte leaks the session's own hold, and
     `close()` returns `False`. Library callers recover with `take_over=True`. MCP-tool and CLI
     users need a manual `rmvterm` (ADR 0170 rule 6); until then their captures raise
     `ConsoleHeldError`.
   - A loss whose message has not arrived when `close()` runs (about 1.5 s of delivery latency)
     still issues `rmvterm`. Only the excluded ownership query could close that window.
   - A loss during a suspension or reconnect gap, when no stream exists, goes undetected.
   - An HMC release that rewords the message disables detection. `rmvterm` behaves as before.
4. **Covered elsewhere:** vterm ownership query → operator (excluded).

## Success

1. A `held` session whose received stream carries the sentinel, whole or split across reads,
   meets two conditions. `read` returns that chunk, then raises `ConsoleHoldLostError`.
   `close()` returns `False` with no `rmvterm`, including when the chunk was never read.
2. After a remote close, a transport error, or a `\r\n` sentinel, `close()` still runs `rmvterm`.
3. A `reconnect=True` session does not reconnect after a loss.
4. ADR 0172 and ADR 0174 each carry a `#1004` amendment with the evidence and refuted premise.

## Considered & rejected

- **Do nothing.** judgment: fit. #1004 asks that a takeover survive the earlier session's close.
- **Use the channel's end or exit status as the signal.** verified: none of the three fixture runs
  saw EOF, an exit status, or a channel close within 40 s of the `rmvterm`.
- **Probe before `rmvterm` at close.** verified: fixture run 3's probe did not prove release while
  the taker held the vterm, the same answer the session's own hold gives (ADR 0174).
- **Match only "This session is no longer connected."** judgment: fit. It widens the spoof class
  to any guest or relayed session that prints the sentence.
- **Release through stdin EOF instead.** judgment: cost. ADR 0072 P5 records that EOF ends the
  vterm, but whether it ends only this session's hold needs its own live evidence.
