# Console input: SysRq injection and exclusive raw mode

Issue #958. Decision record:
[ADR 0176](../../adr/0176-console-input-and-exclusive-raw-mode.md). It extends ADR 0170 within
ADR 0172, ADR 0173 and ADR 0174.

## Problem

kdive's console plane has to write to a partition console. It injects SysRq while its collector
keeps reading, and it runs KGDB's remote protocol over an exclusive channel. hmcpctl seals
mkvterm's stdin on every path (ADR 0072, P5/P7), so no caller can send a byte.

## Scope

- `src/hmcpctl/ssh/console.py`:
  - `_SealedStdin` gains the two-method stdin contract that `_open_capture_stream` uses:
    `source` (what `create_process(stdin=...)` receives, here the pipe's read end) and
    `adopt(process)` (here it records that asyncssh owns the read end). It still has no write
    method.
  - `_ConsoleStdin`, private. Its `source` is `asyncssh.PIPE`, `adopt(process)` keeps
    `process.stdin`, and `async write(data)` calls `writer.write(data)` then `await
    writer.drain()`. `close()` drops the writer reference and never calls `write_eof()`.
  - `ConsoleSession._new_stdin()` returns `_SealedStdin()`, and `_acquire` calls it.
  - `WritableConsoleSession(ConsoleSession)` overrides `_new_stdin()` to return a
    `_ConsoleStdin`. It adds `async write(data: bytes) -> None`, `async send_sysrq(key: str, *,
    prefix: bytes) -> None`, and the async context manager `raw_mode()`, which yields a
    `ConsoleRawChannel`.
  - `ConsoleRawChannel(ConsoleHandover)` adds `async write(data: bytes) -> None`.
    `hand_over()` and `raw_mode()` share one private context manager that takes the handover
    object.
  - `_write_for(owner, data, *, mode, input_kind)` is the one write path. It validates `data`,
    requires state `"held"`, no `close()`, no reconnect task, and `owner is self._owner`. It
    then emits the audit record, then writes.
  - Validation: `data` must be non-empty `bytes`, otherwise `TypeError` or `ValueError`.
    `key` must be one printable ASCII character (`0x21`-`0x7e`). `prefix` must be non-empty
    `bytes`.
- `src/hmcpctl/audit/records.py`: the `console-write` event and `record_console_write(*,
  system, lpar, host, mode, input_kind, length, agent_id)`, emitted at `WARNING`. The
  `agent_id` claim falls back to `hmcpctl`, as the install records do.
- `docs/authorization-audit.md`: the `console-write` section.
- ADR 0176, a `CHANGELOG.md` "Added" entry, and docstrings. The `ConsoleSession` docstring's
  sealed-stdin paragraph names the writable subclass as the one exception.

Unchanged: `capture_lpar_console`, `hmc_capture_lpar_console`, the release probe, and the
behavior of `ConsoleSession`. No MCP or CLI surface is added.

## Failure model

1. Actors and deployments: an in-process library caller, such as kdive's console plane, holds
   HMC credentials and constructs the session. The MCP server and the CLI build only
   `ConsoleSession` through the capture.
2. Invariants: no read-only path (`ConsoleSession`, `ConsoleHandover`, the capture, the release
   probe) can send a byte or EOF. Exactly one `console-write` record is emitted before each
   writer call, and no record carries written content.
   Writes in raw mode are exclusive, and leaving the block restores collection. The release
   guarantees of ADR 0170, 0172, 0173 and 0174 are unchanged.
3. Accepted:
   - The SysRq prefix is unverified. The caller supplies it, and #879 verifies it.
   - `~.` in written bytes may end the vterm, which reads as a remote close (ADR 0176
     Consequences).
   - Cancelling a write during its drain does not withdraw it, because asyncssh has already
     queued the whole buffer. A retried write is sent twice.
   - A record is emitted for a write that then fails, so records can overstate delivery.
   - Off the serve path no sink is installed. Records go synchronously through the embedder's
     logging, one per raw-mode write, and the embedder's configuration can suppress them with
     no drop count (ADR 0176 Consequences).
   - Writes bypass the ADR 0011 ownership guard (ADR 0176 Consequences).
4. Covered elsewhere: leasing between writers (kdive ADR-0539), a lost hold versus a drop
   (#1004), live SysRq proof (#879), and a served write surface (not requested).

Threat model:

- Boundary added: library caller to partition console input. Its control is the
  `WritableConsoleSession` type (the gate), the owner and state checks in `_write_for`, and
  audit before write. On refusal nothing is sent; the error leaks nothing beyond the method
  name.
- Boundary unchanged: MCP and CLI callers reach only sealed sessions.
- Actor: the library caller is trusted with the credentials it already holds, and hmcpctl adds
  attribution, not authentication.
- Out of scope: arbitration among writers in one process (kdive), and content filtering (raw
  must pass KGDB bytes unchanged).

## Success

1. `ConsoleSession`, `ConsoleHandover` and `_SealedStdin` expose no public `write` or `send`
   name. The capture still passes an integer pipe read end as `stdin`.
2. `WritableConsoleSession` passes `asyncssh.PIPE` (`-1`) as `stdin`. `write(b"x")` reaches the
   process's stdin writer and drains, and no `write_eof` is called on any path, including
   `close()`.
3. `send_sysrq("c", prefix=b"\x0f")` makes one writer call with `b"\x0fc"`. A bad `key` or an
   empty `prefix` raises `ValueError` and emits no record.
4. Every write emits one `console-write` record before the writer call, with `mode`,
   `input_kind` and `length`. The record contains none of the written bytes.
5. Inside `raw_mode()`, the channel reads and writes, and the session's `read()` waits. The
   session's `write` and `send_sysrq` raise `RuntimeError`. After the block the collector reads
   again, and no `rmvterm` or `mkvterm` ran. A raw write after the block raises `RuntimeError`.
6. `write` raises `RuntimeError` with no record while suspended, while dropped or reconnecting,
   after `close()`, and inside `hand_over()`. It also refuses once a reconnect has re-acquired
   but before `read()` has returned its gap. After the gap is read, writes reach the new
   process's writer.
7. With `reconnect=True`, a drop inside `raw_mode()` reaches the channel's read, and the raw
   write's transport error propagates unwrapped with no reconnect started. After the block,
   `read()` yields a `ConsoleGap`, a session write reaches the new process, and the stale raw
   channel's write raises `RuntimeError`.
8. The `console-write` event appears in `EVENTS` and in `docs/authorization-audit.md`.

## Validation

Items 1-7 are `focused-test`s in `tests/unit/test_console_capture.py`, using its fakes
(`FakeProcess` gains a recording `stdin`). Item 4 also asserts the record through `caplog` on
the `hmcpctl.audit` logger. Item 8 is covered by the existing drift tests in
`tests/test_authorization_audit_doc.py`, plus a record-shape case in `tests/unit/test_audit.py`.
Tests that already pass (item 1) show their bite by a controlled fault, such as giving
`_SealedStdin` a `write` method. Command:
`uv run --no-sync pytest tests/unit/test_console_capture.py tests/unit/test_audit.py tests/test_authorization_audit_doc.py`.
ADR, CHANGELOG and docstrings are `task-test-not-applicable`: prose that no executable consumer
reads.
