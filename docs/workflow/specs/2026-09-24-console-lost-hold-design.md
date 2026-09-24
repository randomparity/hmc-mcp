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

- `LOST_HOLD_SENTINEL` is the recorded chunk minus its trailing space. Its bare `\n\n\n` is not
  reproduced by text relayed through a guest tty, which translates `\n` to `\r\n`.
- Every channel read (collector, handover, raw channel) passes through `_read_for`.
  - Match the sentinel against that chunk plus the last `len(LOST_HOLD_SENTINEL) - 1` bytes of the
    same stream. `_acquire` resets those bytes.
  - A match while `held` sets the state to `lost`, and that read still returns its chunk.
  - Later `_read_for` calls raise `ConsoleHoldLostError(HMCError)`. That is not an
    `asyncssh.Error`, so it never triggers a reconnect.
- `_teardown` already issues `rmvterm` only when the state is `held`. A `lost` session therefore
  closes only its own connection, and `close()` returns `False` (unproven, like a dropped session).
- Unchanged code covers the rest. `hand_over`, `suspend`, `raw_mode`, and writes require `held`, so
  they raise `RuntimeError` after a loss. `capture_lpar_console` reports the loss as
  `stop_reason="error"` with `released=False`.

## Failure model

1. **Actors and deployments:** library callers, the MCP capture tool, and the CLI, each driving one
   session against a V10 HMC. Untrusted: the partition's console output and other console clients.
2. **Invariants and assets:** ADR 0170's release guarantee for a hold the session still owns, and
   another client's live hold.
3. **Accepted failure classes:**
   - Console output that reproduces the sentinel byte for byte leaks the session's own hold. The
     cost is bounded: `close()` returns `False`, and `take_over=True` recovers the hold.
   - A loss during `suspend()`'s release or a suspension/reconnect gap goes undetected (as today).
   - An HMC release that rewords the message disables detection. `rmvterm` behaves as before.
4. **Covered elsewhere:** vterm ownership query → operator (excluded); stdin-EOF release → follow-up.

## Success

1. When a `held` session's stream carries the sentinel, whole or split across reads, `read` returns
   that chunk and then raises `ConsoleHoldLostError`, and `close()` returns `False` with no `rmvterm`.
2. After a remote close, a transport error, or a `\r\n` sentinel, `close()` still runs `rmvterm`.
3. A `reconnect=True` session does not reconnect after a loss.
4. ADR 0172 and ADR 0174 each carry a `#1004` amendment with the evidence and refuted premise.

## Considered & rejected

- **Do nothing.** judgment: fit. #1004 asks that a takeover survive the earlier session's close.
- **Use the channel's end or exit status as the signal.** verified: none of the three fixture runs
  saw EOF, an exit status, or a channel close within 40 s of the `rmvterm`.
- **Probe before `rmvterm` at close.** verified: fixture run 3's probe answered "held" while the
  taker held the vterm. That is the same answer the session's own hold gives (ADR 0174).
- **Match only "This session is no longer connected."** judgment: fit. It widens the spoof class
  to any guest or relayed session that prints the sentence.
- **Release through stdin EOF instead.** judgment: cost. ADR 0072 P5 records that EOF ends the
  vterm, but whether it ends only this session's hold needs its own live evidence.
