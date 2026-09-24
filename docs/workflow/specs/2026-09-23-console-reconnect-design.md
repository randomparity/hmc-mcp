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
  - A drop is one of two things seen by the collector's read while it owns the channel: an
    `asyncssh.Error` or `OSError`, or `b""` while the session's connection reports
    `is_closed()`. Without `reconnect`, both behave exactly as they do today.
  - With `reconnect`, a drop starts one reconnect task. The task closes the dead channel and
    runs `_acquire` with the session's `take_over` value. It retries `HMCCLIError` up to 3
    attempts, 5 s apart. Held contention raises `ConsoleHeldAfterDropError` and issues no
    `rmvterm` unless `take_over=True`. Exhausted retries raise `HMCCLIError`. Both failures
    leave the state `"dropped"`. On success `read()` returns a `ConsoleGap`, and the next read
    returns the new banner.
  - `read()` awaits that task through a shield, so a consumer timeout leaves the reconnect
    running, and the next `read()` gets its outcome once. `close()` cancels the task, waits for
    it to settle, and then releases a hold only if the state is `"held"`. `hand_over()` and
    `suspend()` refuse while the state is `"reconnecting"` or `"dropped"`.
  - `capture_lpar_console` constructs its session without `reconnect`.
- ADR 0174, a `CHANGELOG.md` "Added" entry, and docstrings.

Pauses (ADR 0173): a handover's reader receives a drop as the same exception or `b""`, and
the session does not reconnect under it. After the block exits, the collector's next read
detects the drop and reconnects. A suspended session holds no connection, so it cannot drop;
`resume()` stays the only re-acquisition and raises plain `ConsoleHeldError`.

Lost hold (#1004): another client's `rmvterm` ends `mkvterm` but not the SSH connection, so
this design treats it as remote close (`b""`, no gap). Until #879 records how `mkvterm` ends
under `rmvterm`, the two cases are told apart only by whether the connection is closed.

## Failure model

1. Actors and deployments: a library caller in one asyncio process, such as kdive's collector.
   The MCP server and the CLI reach only the bounded capture, which never reconnects.
2. Invariants: without `take_over=True`, no `rmvterm` targets a hold that hmcpctl did not
   prove in the current connection (ADR 0172 rule 3). A gap marker precedes every stretch that
   follows a re-acquisition. A reconnect that acquires is released by `close()`. The capture's
   result for a read failure is still `stop_reason="error"`.
3. Accepted:
   - Detection takes up to about 60 s (4 × 15 s) on an idle dead channel. That cost is bounded.
   - The capture can now end with `"error"` on a dead channel before its idle or duration bound
     fires. The read-failure mapping is unchanged; only detection is faster.
   - A lost hold whose SSH connection also closes is reported as a drop. It is unverified until
     #879.
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
3. With `reconnect=True`, `b""` on an open connection returns `b""` and no gap.
4. A reconnect that meets contention raises `ConsoleHeldAfterDropError`, a `ConsoleHeldError`,
   with no `rmvterm`. After that, `read()` raises `RuntimeError` and `close()` returns `False`
   with no `rmvterm`. With `take_over=True` it issues `rmvterm` and returns a gap with
   `took_over=True`.
5. A failed connect is retried; after 3 failures `read()` raises `HMCCLIError`.
6. A `read()` that times out during a reconnect loses no gap. `close()` during a reconnect
   releases a hold that the reconnect acquired, and the waiting reader gets `b""`.
7. Pauses: a drop inside `hand_over()` reaches the handover reader and is reconnected after the
   block. `resume()` on a reconnect session raises plain `ConsoleHeldError`.
8. With `reconnect=False` (the default, and the capture's setting), existing tests pass
   unchanged.

## Validation

Item 1 is a `focused-test` in `tests/unit/test_ssh.py`. Items 2-7 are `focused-test`s in
`tests/unit/test_console_capture.py`, using its fakes (`FakeConnection` gains `is_closed()`).
Item 8 is the existing suite. Command:
`uv run --no-sync pytest tests/unit/test_ssh.py tests/unit/test_console_capture.py`.
ADR, CHANGELOG and docstrings are `task-test-not-applicable`: prose that no executable
consumer reads.
