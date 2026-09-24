# Console disconnect detection and opt-in reconnect

Issue #977 (child 2 of #957). Decision record:
[ADR 0174](../../adr/0174-console-keepalive-and-reconnect.md), extending ADR 0170 within ADR
0172 and ADR 0173.

## Problem

A `ConsoleSession`'s SSH connection sends no keepalives, so a dead but idle channel is never
noticed (P8). A drop ends the session: `read()` raises, and the vterm stays held because the
HMC does not release it after an abrupt disconnect (P3). A continuous collector needs to see the
drop, get the console back, and know where output may be missing.

## Scope

- `src/hmcpctl/ssh/transport.py`: `open_hmc_connection` passes `keepalive_interval=15` and
  `keepalive_count_max=3` to `asyncssh.connect`. That connection hosts only console streams
  and release probes.
- `src/hmcpctl/ssh/console.py`:
  - `ConsoleSession(..., take_over=False, reconnect=False)`. `read()` and iteration yield
    `bytes | ConsoleGap`. A gap appears only when `reconnect=True`.
  - `ConsoleGap`, a frozen dataclass with `error: str` (the bounded drop detail) and
    `took_over: bool` (whether `rmvterm` ran before re-acquisition).
  - `ConsoleHeldAfterDropError(ConsoleHeldError)`.
  - A drop is one of two things seen by the collector's read while it owns the channel and
    before `close()` begins: an `asyncssh.Error` or `OSError`, or `b""` while the session's
    connection reports `is_closed()`. The first `b""` on an open connection is a remote close
    and is latched, so later reads never count as a drop. Without `reconnect`, and once
    `close()` has begun, reads behave exactly as they do today.
  - With `reconnect`, a drop starts one reconnect task. The task closes the dead channel and
    makes one `_acquire` call with the session's `take_over` value. Held contention raises
    `ConsoleHeldAfterDropError` and issues no `rmvterm` unless `take_over=True`. A failed
    connect or acquisition raises `HMCCLIError`. Both failures leave the state `"dropped"`. On
    success `read()` returns a `ConsoleGap`, and the next read returns the new banner.
  - `read()` awaits that task through a shield, so a consumer timeout leaves the reconnect
    running, and the next `read()` gets its outcome once. `close()` cancels the task, awaits
    its completion (a task cancelled before it starts completes too), and then releases a hold
    only if the state is `"held"`. `hand_over()` and `suspend()` refuse while the state is
    `"reconnecting"` or `"dropped"`.
  - `capture_lpar_console` constructs its session without `reconnect`.
- ADR 0174, a `CHANGELOG.md` "Added" entry, and docstrings.

Pauses (ADR 0173): a handover's reader receives a drop as the same exception or `b""`, and
the session does not reconnect under it. After the block exits, the collector's next read
detects the drop and reconnects. A suspended session holds no connection, so it cannot drop;
`resume()` stays the only re-acquisition and raises plain `ConsoleHeldError`.

Lost hold (#1004): another client's `rmvterm` ends `mkvterm` but not the SSH connection, so
this design treats it as remote close (`b""`, no gap), latched so a later connection close is
not reclaimed. Until #879 records how `mkvterm` ends under `rmvterm`, the two cases are told
apart only by whether the connection is closed when the stream ends.

## Failure model

1. Actors and deployments: a library caller in one asyncio process, such as kdive's collector.
   The MCP server and the CLI reach only the bounded capture, which never reconnects.
2. Invariants: without `take_over=True`, no `rmvterm` targets a hold that hmcpctl did not
   prove in the current connection (ADR 0172 rule 3). A gap marker precedes the stream that
   follows a reconnect's re-acquisition; `resume()` emits none, since its caller made the
   pause. A reconnect that acquires is released by `close()`, and no reconnect starts after
   `close()` begins. The capture's result for a read failure is still `stop_reason="error"`.
3. Accepted:
   - Detection takes up to about 60 s (4 × 15 s) on an idle dead channel. That cost is bounded.
   - The capture can now end with `"error"` on a dead channel before its idle or duration bound
     fires. The read-failure mapping is unchanged; only detection is faster.
   - A lost hold whose SSH connection also closes is reported as a drop, and a server that
     closes the channel before the connection reads as a remote close. Both are unverified
     until #879.
   - Under P3 the default reconnect usually ends in `ConsoleHeldAfterDropError`; a gap after a
     real drop needs `take_over=True` (ADR 0174 Consequences).
   - `ConsoleHeldAfterDropError` cannot tell the session's leftover hold from another holder.
     The typed error states the leftover is likely, and reclaiming is the caller's call.
4. Covered elsewhere: the takeover mechanism (#975, ADR 0172), `close()` after a lost hold
   (#1004), and live proof (#879).

Threat model: no new boundary. The keepalive is an SSH protocol request. `rmvterm` and
`mkvterm` stay `shlex.quote`d from caller-supplied names.

## Success

1. `open_hmc_connection` calls `asyncssh.connect` with `keepalive_interval=15.0` and
   `keepalive_count_max=3`.
2. With `reconnect=True`, a read that raises a transport error returns a `ConsoleGap`
   (`took_over=False`) and then the new banner. It closes the old connection and issues no
   `rmvterm`. The same happens for `b""` on a connection that reports closed.
3. With `reconnect=True`, `b""` on an open connection returns `b""` and no gap, and a later
   read returns `b""` even after the connection closes.
4. A reconnect that meets contention raises `ConsoleHeldAfterDropError`, a `ConsoleHeldError`,
   with no `rmvterm`. After that, `read()` raises `RuntimeError` and `close()` returns `False`
   with no `rmvterm`. With `take_over=True` it issues `rmvterm` and returns a gap with
   `took_over=True`.
5. A failed reconnect connect makes `read()` raise `HMCCLIError`; `close()` then returns
   `False` with no `rmvterm`.
6. A `read()` that times out during a reconnect loses no gap. `close()` during a reconnect
   releases a hold that the reconnect acquired, and the waiting reader gets `b""`. A drop
   raised to a pending read after `close()` began starts no reconnect.
7. Pauses: a drop inside `hand_over()` reaches the handover reader and is reconnected after the
   block. `resume()` on a reconnect session raises plain `ConsoleHeldError`.
8. With `reconnect=False` (the default, and the capture's setting), existing tests pass
   unchanged.

## Validation

Item 1 is a `focused-test` in `tests/unit/test_ssh.py`. Items 2-7 are `focused-test`s in
`tests/unit/test_console_capture.py`, using its fakes (`FakeConnection` gains `is_closed()`).
Item 8 is the existing suite. Tests that pass before the reconnect logic lands (items 3, 7's
resume case, 8) show their bite by a controlled fault, such as counting every `b""` as a drop.
Command:
`uv run --no-sync pytest tests/unit/test_ssh.py tests/unit/test_console_capture.py`.
ADR, CHANGELOG and docstrings are `task-test-not-applicable`: prose that no executable
consumer reads.
