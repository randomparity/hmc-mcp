# Continuous console session

Issue #974 (child 1 of #957). Decision record: [ADR 0170](../../adr/0170-continuous-console-session.md),
extending [ADR 0072](../../adr/0072-bounded-lpar-console-capture.md).

## Problem

`capture_lpar_console` is the only way to hold a partition vterm, and it is capped at
3600 s and 1 MiB. kdive's continuous collector needs an uncapped hold with the same proven
release, and the capture must keep working unchanged on top of that hold.

## Scope

In `src/hmcpctl/ssh/console.py`:

- Add `ConsoleSession(hmc: HMCClient, system_name: str, lpar_name: str)`. The constructor
  does no I/O. Members: `async open() -> None`, `async read() -> bytes`, `async close() ->
  bool`, a `released: bool | None` property, `__aenter__`/`__aexit__`, and
  `__aiter__`/`__anext__`, which yield chunks until `read()` returns `b""`. ADR 0170's rules
  1-6 are the behavior contract.
- `open()` reuses `_await_acquisition`, `_acquire_capture_stream` and `_open_capture_stream`
  unchanged, with a `_SealedStdin` the session constructs. `close()` reuses
  `_release_and_verify` and `_probe_released` unchanged through one shielded task per session.
  `_release_uncancellable` is deleted: its callers were all in the capture, and the session's
  `close()` replaces it.
- `read()` does not scan for the sentinel (ADR 0170 rule 2). `close()` makes the session
  terminal, and `close()` during an in-flight `open()` raises `RuntimeError` (rule 1).
- Rebuild `capture_lpar_console` as `async with ConsoleSession(...)` around
  `_collect_output(session, ...)`. `_collect_output` reads through `session.read()` and
  keeps its outcomes. Inside the block, the existing whole-buffer `HELD_SENTINEL` check calls
  the private `session._disown()` (the session then owes no release) and raises
  `ConsoleHeldError` with today's message, so no `rmvterm` runs, as today. `released` comes
  from `session.released is True`. The signature, `ConsoleCapture`, error types and
  messages, and the MCP tool are unchanged.
- One capture difference conforms to ADR 0072's documented rule: cancellation during the
  final release raises `CancelledError` after the release instead of returning a result.
- Update the module docstring's opening paragraph to name the session as the hold owner.
- ADR 0170, and one `CHANGELOG.md` "Added" entry (repository convention for a new domain API).

No change to `hmcpctl.api`, the MCP or CLI surfaces, the transport, or the bounds and ceilings.

## Failure model

1. **Actors and deployments**
   - A library consumer on Python 3.11-3.14 (kdive's collector) holding one session per task.
   - The MCP server and the live bare-cec arm through `capture_lpar_console`.
2. **Invariants and assets at stake**
   - The partition's single vterm. A leaked hold locks every operator out until a manual
     `rmvterm` (P3).
   - Another holder's session. The code never issues `rmvterm` on a contention path.
   - `released` is `True` only on probe proof.
   - The console is write-sealed. The session has no write surface.
   - The capture's public contract, which #959 wraps.
3. **Accepted failure classes**
   - The process-exit leaks named in ADR 0170 rule 6 (SIGKILL, `os._exit`, default SIGTERM, a
     stopped loop, an unclosed session). They cannot be handled in-process, and the next
     `open()` reports them as `ConsoleHeldError`.
   - Console text that quotes the contention sentence makes a bounded capture raise
     `ConsoleHeldError` without release, leaking its own hold (ADR 0072 assumption 2, kept by
     #974's criterion 6; #975 owns the rule). A session treats it as data.
   - Loop-wide shutdown or a second SIGINT during a release (ADR 0170 rule 6). No in-process
     remedy exists; rule 6 tells applications to close sessions before the loop ends.
   - Acquisition timeout without a sentinel leaves ownership unknown and issues no `rmvterm`.
     This behavior is unchanged from ADR 0072.
4. **Covered elsewhere**
   - Dead-channel detection and reconnect: #977.
   - Typed contention and forced takeover: #975.
   - Pause and handover: #976.
   - Writes: #958.
   - Live proof: the #879 live window.

### Threat model

- **Boundaries.** None are added. The existing boundary, caller-supplied names reaching
  mkvterm and rmvterm command lines, gains a second entry point (the session constructor).
- **Actors.** An authenticated library caller, who is trusted like any `HMCClient` user. The
  MCP path stays behind the existing `lpar.capture_console` authorization.
- **Controls.** Every name is `shlex.quote`d at each command, as today. Console bytes are
  returned raw and never interpreted.
- **Out of scope.** Authorization for direct library callers: domain modules carry none
  (ADR 0118).

## Success

1. A session opens, yields more than `MAX_CAPTURE_BYTES` across chunks and survives a
   timed-out read, then closes with `released=True` after `rmvterm` and a clean probe.
2. `close()` on an unproven, contended, or never-opened session returns `False`. The
   contended and never-opened cases issue no `rmvterm`. `open()` after `close()` and
   `close()` during `open()` raise `RuntimeError` without acquiring or releasing.
3. Cancellation inside `async with`, and cancellation of a caller awaiting `close()`, both
   complete the release before `CancelledError` propagates. A repeated `close()` does not
   repeat the release.
4. Contention at open raises `ConsoleHeldError` with no `rmvterm`. A session yields a later
   sentinel as data. A capture that sees one raises `ConsoleHeldError` with no `rmvterm`.
5. Every existing capture test passes. Seams are retargeted where a private helper was
   removed, and the `_release_uncancellable` cancellation test is replaced by a close test.
6. The session exposes no public attribute named `write*` or `send*`.

## Validation

All tests are in `tests/unit/test_console_capture.py` and use the existing fakes. The focused
command is `uv run --no-sync pytest tests/unit/test_console_capture.py -q`.

| Contract | Mode | Evidence |
|---|---|---|
| Uncapped stream, timed-out read keeps session (S1) | focused-test | `test_session_streams_past_capture_ceiling_and_releases` |
| Unproven/contended/unopened close (S2) | focused-test | `test_session_close_reports_unproven_release`, `test_session_contention_at_open_never_releases` |
| Cancellation and idempotent close (S3) | focused-test | `test_session_cancellation_releases_before_propagating`, `test_session_close_survives_cancellation_and_is_idempotent`, `test_session_close_propagates_cancelled_release` |
| Late sentinel is data; capture releases then raises (S4) | focused-test | `test_session_yields_late_sentinel_as_data`, `test_contention_is_detected_when_it_arrives_midstream` (now also asserts no `rmvterm`) |
| Capture cancelled in final release (S3, the one difference) | focused-test | `test_capture_cancelled_during_release_raises_after_release` |
| Transport error leaves release owed (rule 3) | focused-test | `test_session_read_error_propagates_and_close_still_releases` |
| Terminal close and misuse (rule 1, S2) | focused-test | `test_session_rejects_reopen_and_read_when_not_open`, `test_session_close_during_open_is_refused` |
| Capture unchanged (S5) | focused-test | existing capture tests; `_release_uncancellable` seams retargeted to `_release_and_verify` |
| No write surface (S6) | focused-test | `test_session_has_no_write_surface` |
| Process-exit contract, facade decision | task-test-not-applicable | A written contract in ADR 0170 and the docstring. No in-process test can observe SIGKILL, and the facade membership is already pinned by `tests/unit/test_public_api.py`. |
