# Console lost-hold detection (#1004)

Decision record: the `#1004` amendments in the Status sections of ADR 0172 and ADR 0174.

## Problem

A live `ConsoleSession` never notices when another client's `rmvterm` ends its hold. That client can
be a manual `rmvterm` or a `take_over=True` session. The session's `close()` then issues `rmvterm`,
which ends the new holder's session (ADR 0172 Consequences).

The live capture (V10R3 M1060, 2026-09-24, three two-client runs) is recorded redacted in
`tests/fixtures/console/lost-hold-transcript.json`:

- About 1.5 s after the `rmvterm`, the holder receives one 98-byte chunk:
  `\r\n Connection has closed \r\n\r\n\r\n This session is no longer connected. Please close this window.\n\n\n `.
- The `mkvterm` channel then stays open and silent, with no EOF, exit status, or channel close for
  40 s. The SSH connection stays usable.
- Closing the old holder's connection leaves the taker's hold intact.

A dropped connection closes the connection, and a lost hold does not, so the two are
distinguishable.

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
- `hand_over`, `suspend`, `raw_mode`, and writes already require `held`, so they raise
  `RuntimeError` after a loss.
- `capture_lpar_console` is unchanged. Its collector reports the loss as `stop_reason="error"`
  with `released=False`.

## Failure model

1. **Actors and deployments:** library callers, the MCP capture tool, and the CLI, each driving one
   session against a V10 HMC. Untrusted: the partition's console output and other console clients.
2. **Invariants and assets:** ADR 0170's release guarantee for a hold the session still owns, and
   another client's live hold.
3. **Accepted failure classes:**
   - Console output that reproduces the sentinel byte for byte leaks the session's own hold. The
     cost is bounded: `close()` returns `False`, and `take_over=True` recovers the hold.
   - A loss that arrives while `suspend()` is releasing, or during a suspension or reconnect gap,
     is not detected. `rmvterm` behaves as before.
   - An HMC release that words the message differently disables detection. `rmvterm` behaves as
     before.
4. **Covered elsewhere:** vterm ownership query → operator (excluded). Stdin-EOF release →
   follow-up candidate.

## Success

1. A `held` session whose stream carries the sentinel, whole or split across reads, meets these
   conditions:
   - `read` returns that chunk, then raises `ConsoleHoldLostError`.
   - `close()` issues no `rmvterm` and returns `False`.
2. A session whose stream ends by remote close or transport error still issues `rmvterm` and
   probes on `close()`. So does a session whose stream carries the sentinel with `\r\n` line
   ends.
3. A `reconnect=True` session does not reconnect after a loss.
4. ADR 0172 and ADR 0174 each carry a `#1004` amendment recording the evidence and the refuted
   premise.

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
