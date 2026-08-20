# Non-blocking stderr diagnostics — design

Issue: [#269](https://github.com/randomparity/hmc-mcp/issues/269). Decision record:
[ADR 0043](../../adr/0043-non-blocking-stderr-diagnostics.md), which owns every choice with a
viable alternative — the queue over the non-blocking descriptor, the hand-rolled sink over
`QueueHandler`/`QueueListener`, the in-band drop marker, and the bounded shutdown. This document
specifies what gets built and how it is proven.

## Goal

No write this package makes to `sys.stderr` can block the thread that made it. An MCP client that
spawns the server and never reads its child's stderr may lose audit records; it may not stop the
server from answering JSON-RPC. Every line lost that way is counted, and the count is written into
the same stream as a record, so an operator reading the stream can tell a quiet server from a
truncated trail.

## Non-goals

- Any new environment variable, CLI flag, or configuration key for the sink's destination, level,
  volume, or queue size — issue #270 owns that gap and this design does not pre-empt it.
- Adding `connection` to the ownership-override record (#271).
- Durable retention, sequencing, integrity protection, or export of records (ADR 0040's residual,
  unchanged).
- Suppressing FastMCP's traceback rendering of a routine denial (#267).
- Changing the record schema: no field is renamed, removed, or retyped, and no reason code changes.
- Making an operator-attached handler on `hmc_mcp.audit` non-blocking. The server defers to a
  pre-attached handler (ADR 0040) and cannot fix its behaviour from here.
- Fixing `logging.lastResort`, which a process that never installs the sink still uses.

## Architecture

Two source files change; no file is added.

| file | responsibility after this change |
|---|---|
| `src/hmc_mcp/audit.py` | unchanged record vocabulary and rendering. Gains `_StderrSink` — a bounded queue, one daemon writer thread, a drop counter, and a bounded shutdown — plus the module-level `write_diagnostic` entry point. `_AuditHandler.emit` submits to the sink instead of writing; `_AuditHandler.flush` is the sink's bounded drain. |
| `src/hmc_mcp/server.py` | `_warn` submits its startup lines through `audit.write_diagnostic` instead of `print(file=sys.stderr)`. The three guards it carried move to the sink, which is now their only owner. |
| `tests/unit/test_audit.py` | sink behaviour, including the real-pipe proofs; existing delivery assertions gain an explicit flush, because delivery is now asynchronous. |
| `tests/app/test_authorization_audit.py` | end-to-end proof that a dispatch through the tool wrapper does not block on a full stderr pipe. |
| `docs/authorization-audit.md`, `README.md` | state the new delivery guarantee in place of the fd-2 deployment requirement. |

### Data flow

```
record_authorization / record_ownership_override
  -> audit._emit            (renders JSON, catches everything)
  -> logging 'hmc_mcp.audit'
  -> _AuditHandler.emit     (renders message + "\n")
                     \
server._warn ---------+---> _StderrSink.submit(line)   [never blocks, never raises]
                            |
                            | queue.Queue(maxsize=1024)   full -> drop, count += 1
                            v
                      writer daemon thread
                            |  drop marker first, if count > 0
                            v
                      sys.stderr.write(line); flush()   [may block here, and only here]
```

## Components

### `_StderrSink`

One instance, module-private, created at import; its thread starts on the first submitted line, so
importing `hmc_mcp.audit` still starts nothing.

```python
class _StderrSink:
    def __init__(self, capacity: int, drain_timeout: float) -> None: ...
    def submit(self, line: str) -> None: ...
    def drain(self, timeout: float) -> bool: ...
    def close(self) -> None: ...
```

- **`submit(line)`** — enqueue one already-terminated line. Starts the writer thread if it is not
  running. On a full queue, or after `close()`, increments the drop counter and returns. Never
  blocks and never raises; a failure to submit is a drop like any other.
- **`drain(timeout)`** — block the calling thread until every submitted line has been written or
  dropped, or *timeout* seconds elapse. Returns whether it drained. Returns immediately once
  closed. This is what `_AuditHandler.flush` calls, and what makes the tests deterministic.
- **`close()`** — put a sentinel behind whatever is queued and join the writer for at most
  `drain_timeout`. Idempotent. Registered with `atexit` at module import.

Writer loop, per item: take the item; if the drop counter is non-zero, write the marker first and
zero the counter; write the item. A write that fails restores what it was owed, so nothing is
double-counted and nothing is forgotten.

Writing is where the three ADR 0040 guards live now, and they are unchanged in meaning:
`sys.stderr` is resolved per write, `None` returns, and `OSError` and `ValueError` are caught. A
failed or skipped write counts a drop, which is the change: those three cases were silent before.

### The drop marker

```json
{"time": "2026-08-19T22:14:03.881271+00:00", "event": "records-dropped", "count": 37}
```

`Event` gains `"records-dropped"`, so `EVENTS` — derived from the `Literal` — gains it too. The
marker is written by the sink, not through the logger, because it describes this sink's queue
rather than an authorization decision; an operator who attached their own handler has no such
queue and gets no such marker.

`count` is lines, not records: a dropped `server._warn` line counts the same as a dropped
authorization record. The marker appears **before** the next successfully written line, so it reads
as "N lines are missing above this point".

### `server._warn`

```python
def _warn(lines: tuple[str, ...]) -> None:
    for line in lines:
        write_diagnostic(line)
```

`write_diagnostic(line)` appends the newline and submits. `_warn` keeps its docstring's promise —
never stdout, never aborts a start — and gains the fourth case: never waits on a reader.

## Constants

| name | value | why |
|---|---|---|
| `_QUEUE_CAPACITY` | `1024` lines | ≈0.5 MiB at ADR 0043's measured 386–563 bytes per record; roughly six times the 64 KiB pipe buffer it stands in for. |
| `_DRAIN_TIMEOUT` | `2.0` seconds | The whole of what shutdown will wait on a destination nobody is reading. A drained destination finishes in microseconds. |

Neither is configurable; see Non-goals.

## Error handling

| condition | behaviour |
|---|---|
| queue full at `submit` | drop, count, return |
| `sys.stderr is None` at write | drop, count, continue |
| `OSError` / `ValueError` at write | drop, count, continue |
| any other exception at write | drop, count, continue — a writer thread that dies stops the trail permanently, so the loop catches broadly and keeps running |
| write blocks forever | the writer thread blocks; producers keep enqueuing until full, then drop and count |
| marker write itself fails | the count is restored, so the next successful write reports it |
| `close()` with the writer blocked | join times out, the daemon thread is abandoned, the process exits |
| `SIGKILL` / `os._exit` | queued lines are lost with no marker; stated in ADR 0043 |

## Threat model

The change is security-relevant: it alters the delivery guarantee of a security-observability
channel, and the blocking condition it removes is reachable by an unauthenticated caller.

- **Boundary inventory.** This design adds no boundary and widens none. It changes the behaviour
  behind one existing boundary — the MCP dispatch boundary, where `dispatch_scope.authorize` emits
  a record before denying. Data crossing outward is unchanged: the same record fields, the same
  truncation, the same JSON escaping, on the same descriptor.
- **Actor model.** The untrusted party is the MCP client and any agent driving it. Under stdio that
  client also owns fd 2, so it is simultaneously the reader of the audit stream and the party able
  to stop reading it. The design places no trust in it: it is assumed to be able to withhold reads
  indefinitely and to call denied tools at will. The operator's supervisor or journal is trusted
  under `--http`.
- **Control per boundary.** Availability is bounded by `_QUEUE_CAPACITY` — an unauthenticated
  caller can cost the process at most 1024 buffered lines and one thread, and cannot cost it a
  request. Integrity of the stream is bounded by the marker: a caller who forces drops cannot make
  the loss invisible, because the count is written by the sink and not derived from anything the
  caller supplies. Confidentiality is unchanged; no new value is rendered. The marker carries no
  caller-supplied value at all, so it needs no truncation or escaping beyond `json.dumps`.
- **Explicitly out of scope.** A caller who *can* make records drop still degrades the trail —
  that is the accepted trade, made visible rather than prevented. Rate limiting the caller is #218's
  rejected surface and stays rejected. Forging a `records-dropped` line into the stream is possible
  for any process writing to fd 2, exactly as it already is for every other record; ADR 0040's
  "skip a line that does not parse" and "this stream is not signed" both still hold.

## Testing

Every test that proves the blocking case uses a **real pipe filled to its buffer limit**, not a
mock. The fixture opens `os.pipe()`, sets the write end non-blocking, writes until `BlockingIOError`,
then restores blocking mode — `O_NONBLOCK` is a property of the open file description, so clearing
it leaves a genuinely blocking, genuinely full pipe. Teardown closes the read end, which wakes a
blocked writer with `EPIPE`, then joins the sink.

Each proof that involves a wedged destination drives its work on a **thread the test can
abandon**, and asserts that the thread returned within a bound. Run in the main thread, the
pre-change behaviour would hang the whole suite rather than fail one test; run this way, it
reddens. Confirmed by reverting the enqueue to a synchronous write and watching each of them
fail rather than stall.

| # | proves | shape |
|---|---|---|
| 1 | a full pipe does not block `submit` | fill the pipe, point `sys.stderr` at it, submit `capacity + 200` lines on a worker, assert it returned inside 10 s |
| 2 | a full pipe does not block a **tool dispatch** | same pipe, a policy-gated authorizer, 400 denied calls through `dispatch_authorizer`, assert the worker returned and every call still raised its ADR 0038 error |
| 3 | drops are visible and the arithmetic closes | after test 1's overflow, free the pipe and submit one more line; assert `sum(marker counts) + lines written == lines submitted`, that a marker precedes what follows it, and that the queue did overflow. The conservation law rather than a fixed count, because how many land before the writer is first scheduled is not deterministic |
| 4 | an absent, broken, closed, or otherwise unwritable stream counts a drop | parametrized over `None`, `ValueError`, `OSError`, and an unforeseen exception; assert the next successful write is preceded by a `count: 1` marker — which also proves the writer thread survived |
| 5 | shutdown loses nothing when the destination is read | fill the queue to capacity, `close()`, assert every line arrived in order |
| 6 | shutdown does not hang when it is not | full pipe, submit, `close()`, assert it returned |
| 7 | `server._warn` does not block | full pipe, `_warn` with three lines on a worker, assert it returned |
| 8 | a closed sink writes nothing more and still counts | `close()`, submit, assert nothing further was written and the loss was counted; `close()` twice is safe |
| 9 | a failed marker write is still owed | two drops against a hostile stream, then a working one; assert one marker carrying `count: 2` |
| 10 | the marker is one physical line of ASCII JSON | assert it parses, is ASCII, and carries exactly `time`, `event`, `count` |
| 11 | `EVENTS` still equals the `Literal`, now with three values | existing test extended |

Existing tests that read `capsys` immediately after emitting a record now wait for the sink
first — through a module-level `_flush()`, or through `_AuditHandler.flush()` where the point is
that `logging.shutdown`'s call is bounded. That is not a weakened assertion; it is the assertion
the asynchronous contract permits, and a drain that times out fails the test.
`tests/app/test_capability_ceiling.py`'s unusable-stderr start proof is repointed: it patched
`server.sys`, and `_warn` no longer resolves the stream at all.

`tests/conftest.py` gains the filled-pipe fixture and extends the audit-isolation fixture to
settle the sink against a throwaway stream between tests — a writer thread scheduled after a
test's redirection is undone would otherwise land in the next test's captured output.

One trap is worth recording, because it cost a review cycle: a `sys.stderr` redirection installed
by a *fixture* does not reliably survive to the writer. The writer thread may not be scheduled
until after the test body returns, and fixture finalizers run in an order that can undo the patch
first. Every wedged-pipe test therefore owns the redirection and the sink's `close()` in its own
frame, closing before restoring.

## Acceptance criteria

1. With `sys.stderr` on a full, undrained pipe, `capacity + 200` authorization decisions dispatched
   through the tool wrapper all return, and none blocks.
2. Lines dropped for any reason — full queue, absent stream, broken stream, closed stream — are
   counted, and the count reaches the stream as a `records-dropped` record ahead of the next
   successful write.
3. `close()` on a drained destination writes every queued line before returning; `close()` on an
   undrained one returns within `_DRAIN_TIMEOUT` plus scheduling slack.
4. `server._warn` cannot block a start.
5. No record field is renamed, removed, or retyped, and `EVENTS` still equals `get_args(Event)`.
6. `docs/authorization-audit.md` and `README.md` state the droppable-with-a-count guarantee and no
   longer state the fd-2 drain requirement as the mitigation.
7. `just verify` and `uv run prek run --all-files` are green.
