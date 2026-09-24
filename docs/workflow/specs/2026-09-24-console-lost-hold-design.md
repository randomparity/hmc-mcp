# Console lost-hold detection (#1004)

Decision record: the #1004 amendments in the Status sections of ADR 0172 and ADR 0174.

## Problem

When another client runs `rmvterm` against a vterm that a live `ConsoleSession` holds (a manual
`rmvterm`, or a `take_over=True` session), the session never notices. Its `close()` then issues
`rmvterm` and ends the new holder's session (ADR 0172 Consequences).

Live evidence (HMC V10R3 M1060, 2026-09-24, three two-client runs; redacted transcript in
`tests/fixtures/console/lost-hold-transcript.json`):

- About 1.5 s after the other client's `rmvterm`, the holder's stream received one 98-byte chunk:
  `\r\n Connection has closed \r\n\r\n\r\n This session is no longer connected. Please close this window.\n\n\n `.
- After that the `mkvterm` channel stayed open and silent. There was no EOF, no exit status, and no
  channel close within 40 s. The SSH connection stayed open and could run another command.
- Closing the old holder's connection after the loss left the taker's hold intact.

This refutes ADR 0174's assumption that a lost hold ends `mkvterm` as a remote close. A lost hold and
a dropped connection are distinguishable: a drop closes the connection, and a lost hold does not.

## Design

- `LOST_HOLD_SENTINEL` is the recorded chunk without its trailing space. The exact bytes include the
  bare `\n\n\n`, which a guest tty's `\n`→`\r\n` translation would not reproduce.
- `ConsoleSession` keeps the last `len(LOST_HOLD_SENTINEL) - 1` stream bytes per `mkvterm` stream.
  `_acquire` resets them. Every channel read goes through `_read_for` (collector, handover, raw
  channel). When the new chunk plus that tail contains the sentinel while the state is `held`, the
  state becomes `lost`. That read still returns its chunk, so no console byte is withheld.
- In state `lost`, `_read_for` raises `ConsoleHoldLostError`, a new `HMCError` subclass, for every
  reader. Because it is not an `asyncssh.Error` or `OSError`, it never starts a reconnect.
- `_teardown` issues `rmvterm` only in state `held`. So a `lost` session releases nothing, closes
  its own connection, and `close()` returns `False`, which reads as unproven, like a dropped session.
- `hand_over`, `suspend`, `raw_mode`, and writes already require `held`, so after a loss they raise
  `RuntimeError`.
- `capture_lpar_console` needs no change: its collector turns the error into `stop_reason="error"`
  with the error text, and `released=False`.

## Failure model

1. **Actors and deployments:** library callers, the MCP capture tool, and the CLI, all driving one
   `ConsoleSession` against a V10 HMC. Untrusted party: the partition's console output. A second
   console client can be another tool or a person.
2. **Invariants and assets:** ADR 0170's release guarantee for a hold the session still owns, and
   another client's live hold.
3. **Accepted failure classes:**
   - Partition output that byte-for-byte reproduces the sentinel makes the session skip `rmvterm`
     and leak its own hold. The cost is bounded: `close()` reports `False`, and `take_over=True`
     recovers the vterm (ADR 0172 amendment).
   - A loss whose sentinel arrives while `suspend()` is already releasing is not detected.
     `suspend()` has already started its `rmvterm`.
   - A loss during a suspension or a reconnect gap is not seen, because no stream exists.
   - The HMC may word the message differently on another release. In that case detection silently
     fails and the old behavior (`rmvterm` on close) returns.
4. **Covered elsewhere:** an ownership-query primitive belongs to the operator (excluded).
   Release through stdin EOF is a follow-up candidate.

## Success

1. A session that receives the sentinel (whole or split across reads) raises `ConsoleHoldLostError`
   on the next read and issues no `rmvterm` on `close()`, which returns `False`.
2. A held session whose stream ends any other way (remote close, transport error, or the sentinel
   with `\r\n` line ends) still issues `rmvterm` and probes on `close()`.
3. A `reconnect=True` session does not reconnect after a loss.
4. ADR 0172 and ADR 0174 each carry a `#1004` Status amendment that records the live evidence and
   corrects the refuted premise.

## Validation

| Contract | Mode | Evidence |
|---|---|---|
| Success 1 | focused-test | whole and split sentinel from the fixture: `read` raises, `run_hmc_command` never awaited, `close()` is `False` |
| Success 2 | focused-test | CRLF-translated sentinel: `close()` awaits `rmvterm`; existing drop and remote-close tests stay green |
| Success 3 | focused-test | `reconnect=True`: one `open_hmc_connection` call, `ConsoleHoldLostError` raised |
| capture path | focused-test | `capture_lpar_console`: `stop_reason == "error"`, `released is False`, no `rmvterm` |
| Success 4 | task-test-not-applicable | ADR prose; no test reads ADR bodies (`just adr-numbering` checks names only) |

## Considered & rejected

- **Do nothing (ADR 0172's accepted consequence).** judgment: fit. #1004 asks that a takeover
  survive the earlier session's close.
- **Use the end of the `mkvterm` channel, or its exit status, as the signal.** verified: in all
  three fixture runs no EOF, exit status, or channel close arrived within 40 s of `rmvterm`.
- **Probe before running `rmvterm` at close.** verified: in fixture run 3 the probe answered
  "held" while the taker held the vterm. That is the same answer the session's own hold gives
  (ADR 0174).
- **Match only the sentence "This session is no longer connected."** judgment: fit. Any guest
  or relayed session that prints the sentence would then match, which widens the spoof class
  and gains nothing.
- **Release through stdin EOF instead of `rmvterm`.** judgment: cost. ADR 0072 P5 records that
  EOF ends the vterm, but nobody has verified that it ends only this session's hold. Settling
  that needs its own live evidence (follow-up candidate).
