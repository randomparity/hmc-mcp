# 0043 — A bounded queue and one writer thread for every stderr diagnostic

## Status

Accepted (2026-08-19)

## Context

ADR 0040 put one audit record on `sys.stderr` per authorization decision, written synchronously
from inside `dispatch_scope.authorize` — ahead of the denial and ahead of the handler. Its sink
guards three conditions: `sys.stderr is None`, `OSError`, and `ValueError`. A pipe that is open,
healthy, and not being read is none of them; `write()` blocks. ADR 0040 recorded that as a
residual, shipped option 1 — the deployment keeps fd 2 drained — and filed #269 so the other two
options were decided rather than never considered. This record is that decision.

Three facts moved the question since:

- **The precondition binds a party who never reads it.** Under the stdio transport the MCP client
  spawns the server and owns fd 2. "Drain the pipe" is not a choice the deploying operator makes.
- **ADR 0041 made a policy mandatory**, so every deployment now emits records rather than only
  those passing `--access-policy`.
- **The arithmetic is small.** Measured on `feat/fail-closed-startup-225`: 386 bytes for a denial
  carrying no targets, 563 for a permit carrying two. A 64 KiB pipe buffer fills after roughly
  120–170 calls. Because the record precedes the denial, an **ungranted** caller reaches that
  count by calling tools it does not hold.

The failure is total: the server stops answering JSON-RPC, with no timeout, no diagnostic, and no
exception for any guard to catch. `server._warn` has the same dependency for its startup lines —
bounded to four lines once per start, but the same missing case.

## Decision

**Every stderr write this package makes goes onto a bounded in-memory queue drained by one daemon
thread. A full queue drops the line and counts it. The count is written out as a record.** This is
#269's option 2 in shape, with three choices inside it.

**One mechanism, carrying rendered lines rather than `LogRecord`s.** The queue is a private
`_StderrSink` in `hmc_mcp.audit`. `_AuditHandler.emit` renders its record and submits the line;
`server._warn` submits its startup lines to the same sink. A queue of `LogRecord`s could not carry
`_warn`'s lines, and leaving `_warn` on a second, blocking mechanism keeps one writer in the
process that can still wedge a start — the condition #269 names first. One destination, one bound,
one drop counter, one shutdown.

**Not `logging.handlers.QueueHandler` and `QueueListener` as they stand.** Read at 3.11.15, both
reintroduce the failure this record exists to remove:

- `QueueHandler.emit` calls `put_nowait` and routes `queue.Full` to `Handler.handleError`, which
  writes a multi-line traceback **synchronously to `sys.stderr`** — a larger write than the record
  it was dropping, on the descriptor that is already blocked, from the dispatch path.
- `QueueListener.stop()` calls `self._thread.join()` with no timeout. The thread is blocked in
  `write()` in exactly the case that matters, so shutdown hangs.

Subclassing both to fix both leaves nothing of the stdlib pair but the name.

**A drop is a record.** `Event` gains a third value, `records-dropped`, and the writer emits
`{"time": …, "event": "records-dropped", "count": N}` immediately before the next line it
successfully writes. `count` is the number of lines lost since the previous marker — from a full
queue, or from an absent, broken, or closed stream. A reader therefore learns that N lines are
missing *before this point in the stream*, in the same one-line ASCII JSON grammar as every other
record. Adding an event value is what ADR 0040's stability rule permits: a field may be added, a
reason code may be added, a consumer ignores what it does not know.

**Shutdown is bounded, and the bound is the only thing between the two failures.** `atexit` stops
the sink: a sentinel goes on the queue behind whatever is already there, and the thread is joined
for at most `_DRAIN_TIMEOUT` (2.0 s). A destination that is being read drains completely, because
the sentinel is last. A destination that is not being read cannot be drained by any mechanism, so
the join times out, the daemon thread is abandoned, and the process exits. `_AuditHandler.flush()`
is the same bounded wait, which is what `logging.shutdown` already calls.

The queue holds 1024 lines — about 0.5 MiB at the measured record sizes, and roughly six times the
pipe buffer it is standing in for.

## Consequences

- **The delivery guarantee changes, and it is weaker.** Before: a record was written, or silently
  dropped when the stream was absent, broken, or closed. Now: a record is written, or dropped and
  counted, and a burst above 1024 undelivered lines drops. That is a security-observability
  contract change, and it is the trade #269 asks to be taken deliberately: a droppable audit trail
  that keeps serving, over a complete one that stops. `docs/authorization-audit.md` and README say
  so in those terms.
- **Records are no longer synchronous with the call.** A record may reach stderr after the tool
  result reaches the client. Order among records is preserved — one FIFO queue, one writer.
- **A hard kill loses what is queued.** `SIGKILL` or `os._exit` skips `atexit`; up to 1024 lines a
  synchronous write would have delivered are lost, and no marker reports them. A synchronous
  writer lost nothing here, so this is a real regression, accepted for the same reason as above.
- **Two ADR 0040 residuals close.** "An undrained stderr blocks the write, and therefore the call"
  is fixed. "A dropped record is silent" is fixed for the shipped sink: every drop reason now has a
  count and a marker. ADR 0040's Residuals section is amended in place to point here; nothing else
  in that record changes and it is not superseded — its record schema, logger reservation,
  propagation rule, and installation point all still govern.
- **One daemon thread per served process**, started on the first submitted line and never on the
  in-process composition path, since nothing there submits one.
- **The guarantee is the default sink's, not the logger's.** An operator who attaches their own
  handler to `hmc_mcp.audit` before `main_stdio` / `main_http` still gets the deferral ADR 0040
  chose — and their handler's blocking behaviour, which this record cannot fix from here.
- **A process that never installs the sink is unchanged.** `logging.lastResort` writes
  synchronously at `WARNING`, so a CLI ownership-override record still blocks on an undrained
  stderr. No dispatch path exists in such a process, and `install_audit_sink` runs on every serve
  path, so the exposure #269 describes is closed where it exists.

## Considered & rejected

**Option 1 — keep fd 2 drained, and say so.** What ships today. Rejected because the party it
binds does not read the operator documentation: under stdio the client owns the descriptor. A
precondition nobody in the deployment can satisfy is not a mitigation, and ADR 0041 already had to
restate it as "choose a client that drains its child's stderr".

**Option 3 — put fd 2 in non-blocking mode and treat `EAGAIN` as a drop.** No thread, and it fails
worse. `O_NONBLOCK` is a property of the open file description, not of the descriptor, so setting
it mutates the same object the parent process holds and every other writer in this process shares
— FastMCP's `RichHandler`, `server._warn`, and any traceback Python prints on the way out. A
buffered text stream meeting `EAGAIN` mid-write raises `BlockingIOError` with `characters_written`
set, which is a partial line on a line-oriented stream, and it raises it inside whatever unrelated
code was printing. Under stdio the descriptor belongs to the client, so this is a server mutating
state it does not own.

**Do nothing about `server._warn`.** Its exposure is four lines once, before `.run()`, and #269
calls a start that hangs there "a start nobody reaches". Rejected because it leaves two mechanisms
writing to one destination with two different failure modes, and because the fresh pipe assumption
is the client's to break, not ours to rely on.

**Drop the oldest queued record instead of the newest.** A ring buffer keeps the most recent
records, which is usually what an operator wants. Rejected because it loses the *first* records of
an incident — the ones naming what an ungranted caller tried before the flood — and because
`queue.Queue` gives the newest-drops behaviour for free while a ring needs its own eviction and
its own accounting.

**Report drops out of band** — a counter on a health endpoint, or a log line on another logger.
There is no health endpoint under stdio, and another logger is another destination to configure.
An in-band marker in the stream's own grammar reaches every reader the records already reach.

**Block with a timeout instead of a queue** — attempt the write, give up after N milliseconds.
Rejected because there is no portable way to bound a write to a blocking descriptor from the
calling thread; achieving it needs a second thread, at which point the queue is the honest
structure and the timeout is a worse bound than a capacity.
