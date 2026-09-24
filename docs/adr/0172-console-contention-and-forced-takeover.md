# ADR 0172: Console contention detail and explicit forced takeover

## Status

Accepted (2026-09-23). Extends ADR 0170, amends ADR 0170 rule 7 in part (the capture's
disown hook), and amends ADR 0072 in part (the clause "no `rmvterm` is ever issued on a
contention path"). `rmvterm` is withheld on contention only while hmcpctl cannot prove the
hold is its own.

> **Amended by #1004** (2026-09-24): a live session no longer undoes a takeover. When another
> client runs `rmvterm`, the HMC sends the holder one fixed in-band message
> (`LOST_HOLD_SENTINEL`); the `mkvterm` channel then stays open and silent, with no EOF and no
> exit status (redacted capture: `tests/fixtures/console/lost-hold-transcript.json`). A `held`
> session that reads that exact message becomes `lost`: later reads raise `ConsoleHoldLostError`,
> and `close()` issues no `rmvterm` and returns `False`. Rule 3's "`close()` always issues
> `rmvterm` for a session that proved its hold" now excludes a lost hold. Partition output that
> reproduces the message byte for byte leaks the session's own hold, reported as `False` and
> recoverable with `take_over=True`. Design and alternatives:
> `docs/workflow/specs/2026-09-24-console-lost-hold-design.md`.

## Context

ADR 0072 and ADR 0170 rule 2 never issue `rmvterm` on contention, because it would close the
other holder's session. #957 still needs a way to take a vterm on purpose, for example after a
leaked hold (ADR 0170 rule 6). The contention error also dropped the HMC's own text. The bounded
capture raised `ConsoleHeldError` and skipped `rmvterm` when console output quoted the P1
sentence after a proven acquisition. That leaked the capture's own hold (ADR 0170,
Consequences).

## Decision

1. **Detail.** `ConsoleHeldError` from acquisition names the `mkvterm` command and quotes the
   HMC output read up to the contention sentence. The output is whitespace-collapsed, cut to
   `_ERROR_DETAIL_MAX_CHARS` (256) characters, and then quoted with `repr`, which escapes
   control bytes, so the quote can run longer than 256 characters. The type is unchanged.
2. **Takeover is explicit.** `ConsoleSession(hmc, system, lpar, *, take_over=False)`. With
   `take_over=True`, `open()` issues `rmvterm` and then acquires as usual. `open()` returns
   only after `Open in progress`. A failed `rmvterm` is logged, because its exit code proves
   nothing (P2), and acquisition decides. If contention appears after the `rmvterm`, `open()`
   raises `ConsoleHeldError` with no second `rmvterm` and no retry. The default stays
   `False`. `capture_lpar_console` and the MCP tool never set it.
3. **`rmvterm` rule.** hmcpctl issues `rmvterm` against a hold it cannot prove is its own
   only when the caller passed `take_over=True`. A hold proven by `Open in progress` is its
   own, because the HMC allows one session per partition (P1), until another client's
   `rmvterm` ends it. `close()` always issues `rmvterm` for a session that proved its hold.
4. **Late sentence in the capture.** The capture still raises `ConsoleHeldError` when its
   collected bytes contain the sentence, but it now does so after the session's normal
   `close()`, so it issues `rmvterm` for its own proven hold first. The message says the
   sentence came after acquisition and gives `released`.

## Consequences

- A consumer can recover a leaked or unwanted hold without shelling out to `rmvterm`. Doing so
  ends whatever session held the console, and that holder gets no warning from hmcpctl.
- Taking over a vterm that a live hmcpctl session holds is undone when that session closes:
  its `close()` issues `rmvterm`, which ends the taker's hold. Takeover is meant for leaked or
  foreign holds. A session that could detect that it lost its hold needs live evidence of how
  `mkvterm` ends under `rmvterm` (#879).
- Takeover does not tell a leftover hold from another holder. #977 owns that distinction and
  the reclaim policy.
- Takeover races another client. If a third client acquires between the `rmvterm` and the
  `mkvterm`, `open()` reports contention and does not try again.
- The capture no longer leaks its hold on a late sentence, but it still raises contention for
  a stream it held. #957 keeps the capture's error behavior unchanged.

## Considered & rejected

- **Do nothing and keep manual `rmvterm` recovery (ADR 0170 rule 6).** judgment: fit. #975
  and #957 ask for takeover inside the library, so consumers do not have to shell out.
- **Try to attach first, and `rmvterm` only on contention.** judgment: complexity. The
  caller has already chosen takeover, and this adds a second acquisition path.
- **Retry acquisition after a failed takeover.** judgment: an unbounded fight against another
  client; one attempt keeps the result honest.
- **Return the late-sentence capture as data.** judgment: it changes the capture's public
  behavior, which #957 rules out. Releasing first fixes the leak and keeps the error.
- **A new `ConsoleContentionError` subclass carrying the HMC text as a field.** judgment: no
  consumer asks for the field, and the message already names it.
- **Expose takeover on the MCP tool or CLI.** judgment: no caller asked for it, and it would
  let an agent close an operator's console session.
