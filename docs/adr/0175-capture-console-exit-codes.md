# ADR 0175: Exit codes for `hmcpctl lpars capture-console`

## Status

Accepted (2026-09-23). Applies ADR 0072 and ADR 0170's capture outcome (`stop_reason`,
`released`) to the CLI command #959 adds. PR #777's review deferred this contract to #959.

## Context

A bounded capture ends in one of three ways a script must tell apart: it finished and the
vterm is proven free; it failed, before or during the capture; or the release was not proven,
so the partition's single console slot may still be held and needs `rmvterm` from an operator.
The command's stdout is the raw console stream, so stdout cannot carry the outcome.
`hmcpctl` already uses `1` for runtime errors and `2` for usage errors.

## Decision

| Code | Meaning |
|---:|---|
| `0` | Capture stopped on `duration`, `max_bytes`, `idle` or `remote-close`, and `released` is true. |
| `1` | No capture: selector lookup, SSH or HMC failure, `ConsoleHeldError` contention, invalid bounds, or an existing `--output` file. Also a capture whose `stop_reason` is `error`. |
| `2` | Usage error, including stdout that is a terminal. |
| `3` | `released` is false. Outranks `1`. |

Bytes captured before a non-zero exit are still written. One stderr line always reports the
stop reason, byte count, `released`, and any error, so the code never has to carry detail.

## Consequences

- A wrapper retries on `1`, and on `3` releases the console before any further capture.
- Contention shares `1` with other failures; its message names it. A script that must retry only
  on contention reads stderr.
- A later `--follow` mode (#957) must map its own ending onto this table or record a new one.

## Considered & rejected

- **Always exit 0, outcome on stderr only.** judgment: a script would have to parse prose to
  learn that the console may still be held, the one outcome that needs action.
- **A distinct code for contention.** judgment: contention needs the same response as any other
  failed attempt, waiting and retrying; a code of its own adds a branch no caller needs today.
- **Exit 1 when the release is unproven.** judgment: it merges the one state that leaves shared
  HMC state held with ordinary failures a retry clears.
