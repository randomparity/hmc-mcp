# Console session suspension: handover, suspend and resume

Issue #976 (child 4 of #957). Decision record:
[ADR 0173](../../adr/0173-console-session-suspension.md), extending ADR 0170 within ADR 0172.

## Problem

`ConsoleSession` holds a vterm from `open()` to `close()` with no way to pause. A preempting
hold must either take the channel while hmcpctl keeps the vterm (#958), or have the vterm
released for an outside client and re-acquired afterwards.

## Scope

All code changes are in `src/hmcpctl/ssh/console.py`:

- `ConsoleHandover`, a new public class. `read() -> bytes` returns the next raw chunk, or `b""`
  at remote close, and raises `RuntimeError` once the handover has ended.
- `ConsoleSession.hand_over()`, an async context manager yielding a `ConsoleHandover` (mode a).
- `ConsoleSession.suspend() -> bool` and `ConsoleSession.resume() -> None` (mode b).
- Channel ownership. `_owner` is the session itself (collecting), a `ConsoleHandover`, or
  `None`. `_collecting` is an `asyncio.Event` that is set only while the session owns the
  channel. `_read_for(owner)` makes every stream read a tracked task. `_give_channel(owner)`
  cancels the in-flight read. A cancelled read re-raises `CancelledError` only when its own
  task is being cancelled (`Task.cancelling() > 0`). Otherwise it returns `None`, and the
  reader re-checks ownership: the collector waits or reads again, and a handover raises once it
  has ended.
- States gain `"suspending"`, `"suspended"` and `"resuming"`. `open()` and `resume()` share one
  `_acquire(fallback, take_over)` helper. `suspend()` and `close()` share `_release_hold()` and
  `_await_uninterrupted(task)`, which is the shield loop taken out of `close()`.
- `close()` of a suspended session returns the stored `suspend()` proof and issues no `rmvterm`.
  `close()` during `suspend()` or `resume()` waits on a `_settled` event, which the transition
  sets when it ends, and then tears down whatever state the transition left. `resume()` raises
  `RuntimeError` if `close()` began while it ran. `close()` still refuses `"opening"`. It sets
  `_collecting` so that a waiting collector returns `b""`.
- Docstrings for the module, the session and the new methods, plus a `CHANGELOG.md` "Added"
  entry.

`capture_lpar_console`, `ConsoleCapture`, `ConsoleHeldError`, the MCP tool, the CLI and
`hmcpctl.api` do not change.

## Failure model

1. Actors and deployments: a library caller in a local asyncio process, such as kdive's
   collector or #958's raw mode. The collector and the preempting holder can run as different
   tasks on one event loop. The MCP server and the CLI reach only the bounded capture.
2. Invariants: in mode (a) the vterm is never released between `hand_over()` entry and exit. No
   stream byte goes to two readers or is dropped when the channel changes hands. A session that
   proved a hold releases it on `close()` or `suspend()`. A suspended session never issues
   `rmvterm`.
3. Accepted:
   - A chunk that the stream delivered before `_give_channel` ran goes to the previous owner.
     Those bytes arrived before the handover.
   - `resume()` can lose the race to the external holder. It raises and does not retry (ADR
     0173 Consequences).
   - A task that swallowed its own cancellation without `uncancel()` keeps `cancelling() > 0`,
     so a preempted read in that task re-raises `CancelledError`. The misuse is the caller's.
   - Multi-threaded use is outside the model, because the session is single-loop asyncio.
   - "No byte dropped" rests on asyncssh 2.x `SSHStreamSession.read`, which blocks only before
     consuming data (verified in 2.24.0). The fakes cannot model that, so a bump of the
     `asyncssh>=2.24,<3` range is checked against the source.
4. Covered elsewhere: reconnect while paused (#977), writes and raw mode (#958), live proof
   (#879), and lost-hold detection in a held session (ADR 0172 Consequences, #879).

Threat model: no new boundary. `suspend()` and `resume()` reuse the existing `rmvterm` and
`mkvterm` commands, built from `shlex.quote`d names that a trusted library caller supplies.

## Success

1. Mode (a): inside `hand_over()`, `handover.read()` returns the stream bytes that follow the
   collector's last chunk. A collector read that was waiting on the stream returns nothing
   during the handover and returns the next bytes after it. From `open()` to `close()`,
   `run_hmc_command` runs only for `close()` (rmvterm plus the probe's teardown), and
   `create_process` runs once on the session's own connection.
2. After `hand_over()` exits, `handover.read()` raises `RuntimeError`. `hand_over()`,
   `suspend()` and `resume()` raise `RuntimeError` outside their valid states: `hand_over()`
   and `suspend()` need a held session with no active pause, and `resume()` needs a suspended
   session.
3. Mode (b): `suspend()` returns `True` after `rmvterm` and a clean probe. `resume()` then
   acquires on a new connection. A collector `read()` that was pending across both calls returns
   the new banner.
4. `resume()` against a held slot raises `ConsoleHeldError`, issues no `rmvterm`, and leaves
   the session suspended. A later `close()` issues no `rmvterm`, returns the `suspend()` proof,
   and makes a waiting collector `read()` return `b""`.
5. Cancellation while paused: cancelling the owner inside a handover releases on `close()`.
   Cancelling a `suspend()` caller still completes the release. `CancelledError` then
   propagates, and `close()` issues no second `rmvterm`. A `close()` from another task during
   `resume()` releases the hold that `resume()` acquired.
6. A handover that exits without yielding leaves the collector's pending read to return the next
   chunk, not `CancelledError`.
7. Bounded capture tests pass unchanged.

## Validation

Items 1-6 each have a `focused-test` in the session section of
`tests/unit/test_console_capture.py`, using its `FakeConnection`, `FakeProcess` and
`_session_patches` fakes. Item 7 is the existing capture tests. The command is
`uv run --no-sync pytest tests/unit/test_console_capture.py`.
The CHANGELOG and docstrings are `task-test-not-applicable`: they are prose, and no executable
consumer reads them.
