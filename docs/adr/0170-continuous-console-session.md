# ADR 0170: Continuous read-only console session with proven release

## Status

Accepted (2026-09-23). Extends ADR 0072: its prototype evidence (P1-P8), contention,
proven-release, sealed-stdin, and byte-integrity rules stand. Only the rule that every
console hold is bounded changes. The mechanism ADR 0072 names for its rule 1,
`_release_uncancellable`, is replaced by `ConsoleSession.close()`.

> **Amended by #1058** (2026-09-24): rule 4's release sends stdin EOF first, and `close()` runs
> `rmvterm` only when the stream does not end. See ADR 0072's `#1058` amendment.

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

1. **One hold per session.** `open()` runs only on a new session. `open()` after `close()`,
   a second `open()`, `close()` while `open()` is in flight (cancel the opening task
   instead), and `read()` outside the open state all raise `RuntimeError`. `close()` is
   terminal: a closed session is never reopened.
2. **Acquisition is proven, contention at open never releases.** `open()` returns only after
   mkvterm prints `Open in progress`. The P1 sentinel before that point raises
   `ConsoleHeldError` and issues no `rmvterm`. After acquisition the session scans nothing:
   P1's contention text replaces the banner and is never sent to a holder, so the sentence
   later in the stream is console content, and the session's proven hold is still released
   on `close()`.
3. **No cap.** `read()` returns the next raw chunk, the acquisition bytes first, and `b""`
   after the remote end closes. The session enforces no duration, byte, or idle bound.
   Consumers own their bounds, for example with `asyncio.wait_for` around `read()`. A
   timed-out read leaves the session open. A transport error propagates unwrapped, and the
   session still owes its release.
4. **Release belongs to `close()` alone.** `close()` runs `rmvterm`, proves release with the
   independent mkvterm probe (ADR 0072, P2), closes the session's connection and stdin, and
   returns `released`. `released` is `True` only on proof. It is `None` before `close()` and
   `False` for any unproven, contended, or never-acquired session. `close()` is idempotent:
   later and concurrent calls await the same single release. Cancelling a caller that awaits
   `close()` never interrupts the release; the cancellation is re-raised after the release
   completes. Cancelling the release task itself, as a loop-wide shutdown does, can. The
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
   SIGTERM under its default action, a second SIGINT under `asyncio.run` (it raises
   `KeyboardInterrupt` instead of cancelling), an event loop stopped with the owning task
   unfinished, `asyncio.run` returning while a release is in flight in a task the main
   coroutine does not await (its shutdown cancels every task), and a session that is
   dropped without being closed. An application that wants SIGTERM handled must turn it
   into task cancellation, and must await its sessions' `close()` before its main coroutine
   returns. P3 says the HMC never reclaims a leaked hold.
   The next `open()` for that partition then raises `ConsoleHeldError`, the same error
   another holder causes, and the operator recovers with
   `rmvterm -m <system> -p <partition>`. Telling a leftover hold apart from another holder
   is #975/#977's work.
7. **Bounded capture is a consumer.** `capture_lpar_console` opens a session, reads until its
   three ADR 0072 bounds fire, and closes it. The module keeps one release path, and the
   capture's signature, result, error types, and MCP tool are unchanged. The capture keeps
   its own whole-buffer check for the contention sentence. On a match it disowns the hold
   through a module-private session hook, so `close()` issues no `rmvterm`, and raises
   `ConsoleHeldError` exactly as before (#974 keeps contention's behavior unchanged).
8. **Not a facade export.** The session is a pre-release domain-module API under ADR 0118.
   `hmcpctl.api` keeps its six names. #975-#977 and #958 still reshape the session, and a
   facade export would freeze it first.

## Consequences

- The continuous collector and the bounded capture share one acquisition path and one
  release path, so a later release fix reaches both.
- A consumer that forgets `close()` leaks the vterm with no warning. The context manager is
  the documented use.
- The capture still skips `rmvterm` when console text quotes the contention sentence after
  a proven acquisition, which leaks its own hold (ADR 0072 assumption 2). The session does
  not inherit this; changing the capture's contention rule belongs to #975.
- Cancelling the capture during its final release now raises `CancelledError` after the
  release instead of returning a result. The old code swallowed that cancellation, against
  ADR 0072's documented rule that the release "runs to completion before cancellation
  propagates"; the session implements the documented rule.

## Considered & rejected

- **Raise the capture ceilings.** judgment: still a cap, and each raise moves a limit that
  exists to protect the MCP server process.
- **A separate module with its own release path.** judgment: two release paths drift, and
  #957 asks for one.
- **Release from `__del__` or `atexit`.** verified: a script that registers an `atexit`
  handler and then receives default-action SIGTERM, SIGKILL, or `os._exit(0)` never runs the
  handler (CPython 3.11.15 and 3.14.7, Linux x86_64; exit codes 143, 137, 0). The exits a
  hook would add are the ones it never sees. The exits that do run it, a normal return
  and a first SIGINT under `asyncio.run`, already unwind the session's owning task.
  `__del__` cannot await the release.
- **Install a SIGTERM handler in the library.** judgment: signal disposition belongs to the
  application, and a library handler would replace whatever handler the application set.
- **Export the session through `hmcpctl.api`.** judgment: it would freeze an API that four
  approved issues are about to extend.
- **Scan the whole session stream for the contention sentence and skip release on a match.**
  verified: `_acquire_capture_stream` returns only after `Open in progress` with no sentinel
  (`src/hmcpctl/ssh/console.py:493-499` at main 80b8bb1d), and ADR 0072's P1 record shows the
  contention text in place of the banner. A later match is console content, so skipping
  release would leak the session's own hold and report it as another holder.
- **Release first, then raise, in the capture's late-contention path.** judgment: it fixes
  the capture's leak but changes a contention side effect #974 must keep; #975 owns it.
