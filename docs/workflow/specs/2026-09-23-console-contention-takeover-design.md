# Console contention detail and forced takeover

Issue #975 (child 3 of #957). Decision record:
[ADR 0172](../../adr/0172-console-contention-and-forced-takeover.md), extending ADR 0170.

## Problem

Contention raises a generic `ConsoleHeldError`, no caller can take a vterm on purpose, and
the bounded capture leaks its own hold when console text quotes the P1 sentence after a
proven acquisition.

## Scope

All changes are in `src/hmcpctl/ssh/console.py`:

- `_acquire_capture_stream`: the contention message becomes
  `f"{command} found the console held by another session; the HMC reported: {report!r}"`,
  where `report = " ".join(bytes(data).decode("ascii", "replace").split())[:256]`.
- `_rmvterm(config, system_name, lpar_name) -> None`: issues `rmvterm` and logs an
  `HMCCLIError` as a warning. `_release_and_verify` calls it in place of its inline copy.
- `ConsoleSession.__init__(..., *, take_over: bool = False)`. In `open()`, inside the existing
  `try`, `take_over=True` logs a warning, awaits `_rmvterm`, and then acquires as today.
- `capture_lpar_console`: move the `HELD_SENTINEL` check after the `async with` block, so
  the session's normal `close()` runs first. Delete `ConsoleSession._disown`. The late
  message names the sentence, says it arrived after acquisition, and gives `released`.
- Update the docstrings for the class, `open()`, the capture and the module, plus a
  `CHANGELOG.md` "Changed" entry.

The capture's signature, `ConsoleCapture`, the MCP tool, the CLI and `hmcpctl.api` do not
change.

## Failure model

1. Actors and deployments: a library caller in a local Python process (kdive's collector,
   hmcpctl's own capture), and the MCP server, which runs only the capture.
2. Invariants: another client's hold is released only when the caller passed
   `take_over=True`. Every proven hold is released on `close()`. The capture's API and error
   type stay the same.
3. Accepted: a takeover that loses a race to a third client raises `ConsoleHeldError` with no
   retry (bounded, stated in ADR 0172). Console text that quotes the sentence still turns a
   capture into a raise (ADR 0072 assumption 2).
4. Covered elsewhere: reclaiming after a drop (#977), writes (#958), live proof (#879).

Threat model. The only boundary this change widens is the `rmvterm` command, which is built
from the system and partition names. Those names are `shlex.quote`d, as on every existing
path, and they come from a trusted library caller. The HMC output is untrusted. It reaches
the message only through the length bound and `repr`, which escapes control bytes. The MCP
and CLI actors cannot reach takeover. Out of scope: authorization for takeover, which is the
caller's decision.

## Success

1. With the default `take_over=False`, contention at open raises a `ConsoleHeldError` whose
   message contains `Only one open session is allowed`, and no `rmvterm` runs.
2. `take_over=True` runs `rmvterm` before `mkvterm` and then holds the vterm. If contention
   follows, `open()` raises `ConsoleHeldError` and `rmvterm` has run exactly once.
3. A failed takeover `rmvterm` still acquires when the slot is free.
4. A capture that sees the sentence after acquisition releases its hold (`rmvterm` plus the
   probe) and then raises `ConsoleHeldError`.
5. Capture contention at open carries the HMC detail.

## Validation

Each success item has a `focused-test` in `tests/unit/test_console_capture.py`:
`uv run --no-sync pytest tests/unit/test_console_capture.py`.
