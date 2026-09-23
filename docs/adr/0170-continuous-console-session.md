# ADR 0170: Continuous read-only console session with proven release

## Status

Accepted (2026-09-23). Extends ADR 0072: its prototype evidence (P1-P8), contention,
proven-release, sealed-stdin, and byte-integrity rules stand. Only the rule that every
console hold is bounded changes.

## Context

ADR 0072 made console access a bounded capture: at most 3600 s and 1 MiB, then release.
kdive's continuous collector (#957) needs a hold with no cap. The acquire and release
helpers already existed, but only `capture_lpar_console` could reach them, so an unbounded
consumer would have needed a second release path. #975 (contention, takeover), #976
(pause, handover), #977 (reconnect) and #958 (writes) each extend whatever this record fixes.

## Decision

`hmcpctl.ssh.console.ConsoleSession(hmc, system_name, lpar_name)` holds one partition's
vterm from `open()` until `close()`. It is also an async context manager and an async
iterator of raw `bytes` chunks. Its core rules:

1. **One hold per session.** `open()` runs once. A second `open()`, or a `read()` outside
   the open state, raises `RuntimeError`. A session is never reused after `close()`.
2. **Acquisition is proven, contention never releases.** `open()` returns only after
   mkvterm prints `Open in progress`. The P1 sentinel raises `ConsoleHeldError` and issues no
   `rmvterm`, both at open and when it appears later in the stream (whole stream, across
   chunk boundaries, as the capture scanned its whole buffer). A session that saw
   contention owes no release.
3. **No cap.** `read()` returns the next raw chunk, the acquisition bytes first, and `b""`
   after the remote end closes. The session enforces no duration, byte, or idle bound.
   Consumers own their bounds, for example with `asyncio.wait_for` around `read()`. A
   timed-out read leaves the session open. A transport error propagates unwrapped, and the
   session still owes its release.
4. **Release belongs to `close()` alone.** `close()` runs `rmvterm`, proves release with the
   independent mkvterm probe (ADR 0072, P2), closes the session's connection and stdin, and
   returns `released`. `released` is `True` only on proof. It is `None` before `close()` and
   `False` for any unproven, contended, or never-acquired session. `close()` is idempotent:
   later and concurrent calls await the same single release. Cancellation of `close()` never
   interrupts the release. The cancellation is re-raised after the release completes. The
   context manager calls `close()` on every exit, including exceptions and
   `CancelledError`. Cancellation during `open()` after acquisition closes the session
   before `CancelledError` propagates, as in ADR 0072.
5. **Stdin is a construction choice.** The session builds its own stdin. Every session this
   record ships uses ADR 0072's `_SealedStdin`, and no attribute or method writes to the
   console. A write-capable session (#958) must be a separate, explicit construction.
   Acquisition and release take any stdin with the same private shape, so #958 changes
   neither.
6. **Process-exit contract.** The library installs no `atexit` hook and no signal handler.
   It releases the vterm on exits that unwind the coroutine that owns the session: a normal
   return, an exception, and task cancellation, including the main-task cancellation
   `asyncio.run` performs on SIGINT (Python 3.11+). It cannot release on exits that stop the
   interpreter without unwinding that coroutine: SIGKILL, `os._exit`, a crash, power loss,
   SIGTERM under its default action, an event loop stopped with the owning task unfinished,
   and a session that is dropped without being closed. An application that wants SIGTERM
   handled must turn it into task cancellation. P3 says the HMC never reclaims a leaked hold.
   The next `open()` for that partition then raises `ConsoleHeldError`, the same error
   another holder causes, and the operator recovers with
   `rmvterm -m <system> -p <partition>`. Telling a leftover hold apart from another holder
   is #975/#977's work.
7. **Bounded capture is a consumer.** `capture_lpar_console` opens a session, reads until its
   three ADR 0072 bounds fire, and closes it. The module keeps one release path, and the
   capture's signature, result, errors, and MCP tool are unchanged.
8. **Not a facade export.** The session is a pre-release domain-module API under ADR 0118.
   `hmcpctl.api` keeps its six names. #975-#977 and #958 still reshape the session, and a
   facade export would freeze it first.

## Consequences

- The continuous collector and the bounded capture share one acquisition path and one
  release path, so a later release fix reaches both.
- A consumer that forgets `close()` leaks the vterm with no warning. The context manager is
  the documented use.
- Scanning an unbounded stream for the contention sentence widens ADR 0072 assumption 2.
  Console text that quotes the sentence ends the session with `ConsoleHeldError` and no
  release attempt. #975 owns a narrower contention rule.
- Cancelling the bounded capture during its final release now raises `CancelledError` after
  the release instead of returning a result. That is ADR 0072's stated rule, "runs to
  completion before cancellation propagates".

## Considered & rejected

- **Raise the capture ceilings.** judgment: still a cap, and each raise moves a limit that
  exists to protect the MCP server process.
- **A separate module with its own release path.** judgment: two release paths drift, and
  #957 asks for one.
- **Release from `__del__` or `atexit`.** verified: a script that registers an `atexit`
  handler and then receives default-action SIGTERM, SIGKILL, or `os._exit(0)` never runs the
  handler (CPython 3.11.15 and 3.14.7, Linux x86_64; exit codes 143, 137, 0). The exits a
  hook would add are the ones it never sees, and a clean interpreter exit under
  `asyncio.run` already cancels and unwinds the owning task. `__del__` cannot await the
  release.
- **Install a SIGTERM handler in the library.** judgment: signal disposition belongs to the
  application. A library handler would override the MCP server's and the CLI's own.
- **Export the session through `hmcpctl.api`.** judgment: it would freeze an API that four
  approved issues are about to extend.
- **Leave late-contention detection in the capture only.** judgment: the capture would need
  a hook to skip the session's release, and contention would be a capture rule instead of a
  session rule that #975 can extend.
