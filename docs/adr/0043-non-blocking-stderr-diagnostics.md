# 0043 — A bounded queue and one writer thread for every stderr diagnostic

## Status

Accepted (2026-08-19)

> **Amended (2026-08-26, issue #534):** the `hmc_mcp` logger namespace joins this sink. It had
> never been on it — only the reserved `hmc_mcp.audit` logger and (through ADR 0051) the
> third-party set were — so this record's "every stderr write this package makes" was false for
> every other `hmc_mcp.*` logger. The Consequences clause that overstated it is corrected in
> place and the amendment section at the end records what changed; the rest is unchanged history.

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
one drop counter, one shutdown — for the shipped sink. An operator who attaches their own handler
to `hmc_mcp.audit` splits it back into two: their handler takes the records, this sink still takes
`_warn`'s lines.

The sink object and its `atexit` hook are created at **import**, and only the writer thread is
lazy. That is the same narrowness ADR 0040 claimed for setting `propagate = False` at import: no
handler is attached, nothing is written, no thread is started, and the hook is a no-op on a process
that never submits a line. The in-process composition path ADR 0040 protects is unaffected.

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
**successfully** writes. `count` is lines lost since the previous marker — from a full queue, or
from an absent, broken, or closed stream — so a reader learns that N lines are missing *before this
point in the stream*, in the same one-line ASCII JSON grammar as every other record.

Two limits on that, both real. **The marker needs a destination that recovers.** A stream that
never accepts another write carries no marker; the count accumulates in memory and is reported when
and if a write lands. Counting is unconditional, reporting is not. And **`count` is lines, not
records**: `server._warn`'s prose shares this queue, so a dropped startup warning counts the same
as a dropped audit record. In practice the two cannot mix — `_warn` runs once, before `.run()`,
against an empty queue — but an operator reconciling a trail should know the field is not a record
count by construction.

Adding an event value follows ADR 0040's precedent rather than the letter of its stability rule,
which enumerates fields and reason codes: that rule's principle — additive, never renamed, never
repurposed, consumers ignore what they do not know — is what `EVENTS` being derived from the
`Literal` already encodes for events.

**Shutdown is bounded, and the bound is the only thing between the two failures.** An `atexit` hook
registered by this module stops the sink: a sentinel goes on the queue behind whatever is already
there, into a slot reserved for it and refused to `submit`, and the thread is joined for at most
`_DRAIN_TIMEOUT` (2.0 s). Nothing is evicted to make room, so a destination being read drains
**completely** — including a queue that was full when shutdown began. A destination that is not
being read cannot be drained by any mechanism, so the join times out, the daemon thread is
abandoned, and the process exits.

`atexit` is LIFO and `logging` registers its own `shutdown` hook when it is first imported, so this
module's hook — registered later — runs **first**, and it is the one that drains at exit.
`_AuditHandler.flush()` is the same bounded wait, and it covers the other caller: a program that
invokes `logging.shutdown()` itself, before exit.

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
- **Two ADR 0040 residuals close, one of them partly.** "An undrained stderr blocks the write, and
  therefore the call" is fixed outright. "A dropped record is silent" is narrowed rather than
  removed: every drop reason is now counted, and the count is reported as soon as any write lands —
  but a destination that never accepts another write reports nothing, and lines submitted after
  `atexit` has stopped the sink are counted and never reportable. ADR 0040's Residuals section is
  amended in place to point here; nothing else in that record changes and it is not superseded —
  its record schema, logger reservation, propagation rule, and installation point all still govern.
- **ADR 0040's logger reservation now bounds the logger, not the stream.** The `records-dropped`
  line is written by the sink and never becomes a `LogRecord`, because it describes this sink's
  queue rather than a decision. Two things follow: it is not filtered by the level an operator sets
  on `hmc_mcp.audit` — the only volume lever ADR 0040 offers — and a reader of the *stream* now
  sees a line that no emitter on the reserved logger produced. ADR 0040's instruction to skip a
  line that does not parse is unaffected; this one parses, and `EVENTS` names it.
- **One daemon thread per served process**, started on the first submitted line and never on the
  in-process composition path, since nothing there submits one.
- **The guarantee is the default sink's, not the logger's.** An operator who attaches their own
  handler to `hmc_mcp.audit` before `main_stdio` / `main_http` still gets the deferral ADR 0040
  chose — and their handler's blocking behaviour, which this record cannot fix from here.
- **The bound was on this package's contribution, not on the stream — widened by ADR 0051.**
  FastMCP's own `RichHandler`s write to fd 2 and were not on this queue, so a consumer that
  stopped reading could still wedge the server through them. #323 tracked that and ADR 0051
  brings the `fastmcp` logger onto this sink as a third producer. Nothing in this record is
  superseded: the queue, the capacity, the drop rule, the marker grammar and the bounded
  shutdown all still govern, and they now govern more of the stream. Two clauses above read
  differently under it. **"`count` is lines, not records"** was hedged with "in practice the
  two cannot mix" because `_warn` runs once before `.run()`; FastMCP's records mix freely for
  the life of the process, so the hedge is gone and the field's stated meaning is the only one.
  And **the queue holds 1024 lines** now means 1024 *items*, one of which may be a rendered
  traceback of no fixed length — the 0.5 MiB figure is a typical case rather than a ceiling,
  and the bound on outstanding writes is the part that keeps the server answering. #330's
  amendment to ADR 0051 widens it again: `uvicorn`, `uvicorn.access` and `mcp` join the
  `fastmcp` logger on this queue, on both transports. That still left this package's *own*
  non-audit loggers off the sink, which is #534 and the amendment below; with it, a served
  process with no operator handler of its own puts every logger it writes through —
  third-party or `hmc_mcp.*` — on this queue. Neither record claims fd 2 has a *single*
  writer, and neither claims the guarantee survives an operator's own handler: on
  `hmc_mcp` as on `hmc_mcp.audit`, an attached handler takes the records and its
  blocking behaviour is the operator's, per the clause above. rich's startup banner and
  `Handler.handleError` still write directly, both recorded as residuals in ADR 0051, and a
  namespace outside the bound set with no handler of its own — `asyncio`, say — still walks to
  `logging.lastResort`.
- **A process that never installs the sink is unchanged.** `logging.lastResort` writes
  synchronously at `WARNING`, so a CLI ownership-override record still blocks on an undrained
  stderr. No dispatch path exists in such a process, and `install_audit_sink` runs on every serve
  path, so the exposure #269 describes is closed where it exists.

## Amendment (#534): the `hmc_mcp` namespace joins the sink

**`server.install_package_stderr_sink` binds the `hmc_mcp` logger to this queue, and
`_serve_application` calls it beside `install_audit_sink`.** The record's own reasoning already
covered these writes; only the wiring was missing. `install_audit_sink` bound `hmc_mcp.audit`
alone, ADR 0051 bound the third-party set, and a record on any other `hmc_mcp.*` logger found
zero handlers in its `callHandlers` walk and went to `logging.lastResort` — a `StreamHandler` on
fd 2, synchronous and unbounded, without ADR 0051's prefix or its escaping. Twenty-one
`WARNING`-or-above call sites across six modules were on that route — `config`,
`server_permissions`, `operations_jobs`, `operations_lpar`, `operations_templates` and
`console_capture`. #534 names two of them, and they are the two that are rate-limited:
`HMCConfig._warn_audit_memento_override` fires once per config construction, and
`_log_unresolved` was deduplicated by #470 to one line per distinct failure *because* the route
was unbounded. The other nineteen are not rate-limited, which is what the queue-pressure clause
below is sized from.

One binding on the namespace covers every producer in it, present and future, because
`callHandlers` reaches a parent's handler. Three choices inside it:

- **Prefix `hmc_mcp: `, through the same `StreamSafeFormatter`** the third-party bindings use.
  A rendered `str(ConfigError)` carries the config path and the profile inventory, and a TOML
  quoted key can put a newline in either, so this package's own text needs the marking and the
  control-character escaping as much as a foreign package's does.
- **No handler is displaced, and no level is set.** ADR 0051's wholesale removal answers a
  problem that does not exist here — nothing but an operator attaches a handler to `hmc_mcp` —
  so the sink goes on only when the logger is bare, which also makes a second call add nothing.
  That makes `hmc_mcp` a second operator attachment point, carrying the two unenforced
  constraints ADR 0040 wrote down for the audit one: a handler attached here must not write to
  `sys.stdout`, which under stdio is the JSON-RPC stream, and it is called on the dispatch
  path — the `_log_unresolved` line runs inside a tool call — so one that blocks there blocks
  the call, which is the failure this record exists to remove and which it cannot fix from here.
- **`propagate` is left alone**, which is where this parts company with
  `install_audit_sink`. ADR 0040 clears the flag on the reserved logger because a record goes
  out there on *every authorized call*, so one `StreamHandler(sys.stdout)` above it corrupts
  the protocol stream on every call. Extending that to this namespace was considered and
  rejected: it would take all twenty-one diagnostic sites out of an operator's own centralized
  logging the moment they serve — silently, and under `--http` too, where stdout carries no
  protocol at all. The defect #534 names needs a handler on the walk and nothing else, and a
  duplicate rendering into a destination the operator chose is a far smaller cost than losing
  the records there. The stdout hazard for these records is unchanged from before this
  amendment, is the operator's own act, and is already documented in
  `docs/authorization-audit.md`.
- **The level is left at `NOTSET`, and this is not volume-neutral for the queue.** The floor is
  unchanged at the shipped default — root at `WARNING`, the level `logging.lastResort`
  enforced. What changes is how much reaches *this sink*: a record that previously found an
  operator's root handler never consulted `lastResort` and never entered the queue at all, and
  now enters it as well. With root below `WARNING` that additionally includes records
  `lastResort` discarded outright, and `_log_unresolved`'s repeat branch is `DEBUG` on every
  call. Accepted rather than pinned at `WARNING` on the handler: a hard floor with no lever
  would put these records out of reach of an operator who deliberately lowered the level.

`hmc_mcp.audit` is unaffected — its own `propagate = False` keeps it off the parent handler, so
the audit stream stays bare one-line JSON with no prefix and no second rendering. The tests are
in `tests/app/test_connection_authorization.py`, and `tests/conftest.py` clears the handler
between tests so a serving test cannot take a later test's `hmc_mcp.*` records onto the sink.

**More producers on a shared bound.** ADR 0051 recorded that its added producers reach the
1024-slot capacity sooner and narrow the security-observability window; this adds the rest of
the `hmc_mcp` namespace on the same terms, and it is the larger of the two additions —
twenty-one call sites, nineteen of them unrated, against a queue whose other occupant is the
authorization trail. Against a destination that has stopped draining, package diagnostics can
displace audit records. Accepted on this record's own trade — a droppable trail that keeps
serving over a complete one that stops — and bounded by the same precondition, a destination
nobody is draining, which under stdio is the client harming itself. The `records-dropped`
marker already counts lines rather than records, so the accounting is unchanged and a reader
still learns how many lines are missing.

**Which channel a non-audit `hmc_mcp.*` record uses.** This sink carries three grammars, not
two, and the third is the one a caller is most likely to reach for by mistake:

1. **Audit records** — one line of ASCII JSON on the reserved `hmc_mcp.audit` logger, ADR 0040's
   schema. A record belongs here only if it is an authorization decision in that schema:
   machine-parsed, and carrying the stability rule the schema carries.
2. **Prefixed prose** — any `hmc_mcp.*` module logger, which since this amendment lands on the
   queue marked `hmc_mcp: ` and control-character-escaped.
3. **Bare prose** — `server._warn`, which submits through `audit.write_diagnostic` with no
   formatter at all, so its lines carry neither the marker nor the escaping.

**A new non-audit record uses grammar 2, the module logger.** Grammar 3 is not a general
diagnostic channel: `_warn` exists for the fixed startup lines this package writes in full,
before `.run()`, and an unmarked line at column 0 is exactly what `StreamSafeFormatter` was
added to stop. Anything that interpolates a value — a setting, a path, a name from a config
file — needs the marking and the escaping, so it goes on a module logger even when it is
emitted at startup beside `_warn`'s lines. That is the answer for #533's startup announcement
of the effective `authorize_power_operations`: a module logger, not `_warn`, and not the audit
stream.

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
state it does not own. Duplicating the descriptor first does not help: `dup` shares the open file
description, so `O_NONBLOCK` set on the copy is set on the original.

**Probe writability instead of mutating the descriptor** — `select.select([], [2], [], 0)` before
each write, drop when it says no. It answers option 3's objection completely: no thread, no queue,
no asynchronous delivery, no loss on `SIGKILL`, and nothing about the descriptor changed. Rejected
because writability is not the guarantee needed. `select` reports a pipe writable when `PIPE_BUF`
bytes will fit — 512 on this host, measured with `getconf PIPE_BUF /tmp` — and a record is 386 to
563 bytes. A record above 512 bytes can therefore find the pipe "writable" and still block partway
through. The probe narrows the window; it does not close it, and a residual liveness dependency on
the record's own length is worse than a thread.

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
