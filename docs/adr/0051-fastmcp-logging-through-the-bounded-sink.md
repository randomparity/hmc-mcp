# 0051 — FastMCP's logger writes through the bounded stderr sink

## Status

Accepted (2026-08-20)

## Context

ADR 0043 put every stderr write *this package* makes onto one bounded queue drained by one
daemon thread, because a synchronous `write()` to a descriptor nobody is reading blocks, raises
nothing, and — sitting inside `dispatch_scope.authorize` — stops the server answering JSON-RPC.

FastMCP writes to the same descriptor and was not on that queue. `fastmcp/__init__.py` calls
`configure_logging` once at import, gated on `settings.log_enabled`, and that function attaches
two `rich.logging.RichHandler`s to the `fastmcp` logger, both rendering through a
`Console(stderr=True)`. So fd 2 had two writers with different failure behaviour:

| Writer | Under a stderr nobody drains |
|---|---|
| `hmc_mcp`'s sink (ADR 0043) | bounded queue, drops counted, reported in band as `records-dropped` |
| FastMCP's `RichHandler`s | unbounded; a blocked `write()` blocks the thread that logged |

ADR 0043's guarantee was therefore a bound on this package's *contribution*, not on the stream.
#323 records that, and ADR 0046 recorded it before that as a residual it did not undertake.

**A handler attached to the `fastmcp` logger survives.** #323's triage said the opposite — that
`configure_logging` removes and re-adds the handlers on every call, so a handler attached there
would be discarded. That is true of the *function* and false of this application's paths, and it
is the fact everything below rests on, so it was checked against the installed
`fastmcp-slim==3.4.7` rather than reasoned about:

- `configure_logging` has exactly two callers in the package — `fastmcp/__init__.py:23`, once at
  import, and `temporary_log_level`, which takes a bare `yield` and reconfigures nothing when its
  level is falsy;
- `temporary_log_level` in turn has exactly two call sites,
  `fastmcp/server/mixins/transport.py:237` and `:354`, both passing `.run()`'s `log_level`;
- neither `main_stdio` nor `main_http` passes `log_level` to `.run()`.

A `NullHandler` attached to the `fastmcp` logger and driven through `temporary_log_level(None)`
was still attached afterwards.

**The severity is P3 and the reasoning belongs here, because it bounds what this record claims.**
Wedging the server needs *both* a caller generating log lines and a consumer that has stopped
draining fd 2. A caller controls only the first. Under stdio the client spawns the server and
owns both ends of that pipe, so a client that stops reading is harming itself. Under
streamable-HTTP the stall has to come from the operator's own log pipeline. This is not a
caller-triggerable denial of service end to end, and nothing here should be read as saying it is.
What it is, is a liveness dependency on a party outside the deployment's control — the same one
ADR 0043 removed for this package's own writes, left standing on the larger writer.

## Decision

**`hmc_mcp.server.install_fastmcp_stderr_sink` replaces the `fastmcp` logger's handlers with one
handler feeding ADR 0043's sink, and `_serve_application` calls it beside `install_audit_sink`
and `install_denial_log_filter`.** Four choices inside that.

**One shared sink, not a second one.** `audit.sink_handler()` returns an `_AuditHandler` bound to
the same `_StderrSink` the audit records and `server._warn` already use. ADR 0043 gave the reason
when it refused to leave `_warn` on its own mechanism: a second mechanism on one descriptor is a
second failure mode on it, with its own bound, its own counter and its own shutdown to keep in
agreement with the first.

**The handler carries a `logging.Formatter`, and that is load-bearing.** `_AuditHandler` renders
`record.getMessage()` when no formatter is installed and `self.format(record)` when one is —
deliberately not `logging.Handler.format`'s fallback to a shared default formatter, because the
two callers want opposite things. The audit logger installs none: ADR 0040's grammar is that the
message *is* the record, one physical line of ASCII JSON, and a formatter is the one thing that
could put something else on that line. The `fastmcp` logger installs
`logging.Formatter("%(levelname)s: %(message)s")` — the format `configure_logging`'s own non-rich
branch uses — because `logging.Formatter.format` is what appends `exc_info`'s traceback. Without
it this change would quietly undo ADR 0046's guarantee that a genuine handler bug keeps its
traceback, which is the debuggability regression #267 rejected and #323 was filed not to
reintroduce. `test_a_handler_bug_keeps_its_traceback_through_the_sink` fails if the formatter
goes.

**`_DenialFilter` stays.** It and this handler solve different problems and neither subsumes the
other: the handler decides *where* a FastMCP record goes, ADR 0046's filter decides *what* a
denial record says. Removing the filter reddens
`test_a_denial_writes_one_line_and_leaves_the_client_message_alone` and
`test_a_denial_is_one_line_through_the_sink`; removing the handler reddens
`test_the_served_path_takes_fastmcps_handlers_off_fd_2`. Three separate breaks, three separate
tests.

**Every handler goes, and the install runs unconditionally.** Not "the two `RichHandler`s":
deciding a handler's destination means reading `rich`'s `Console.file`, and when
`settings.log_enabled` is false there is no handler to recognize in the first place. Taking the
list wholesale is what makes "no handler on this logger writes to fd 2" a property a test asserts
rather than infers, and removing-then-adding is what makes the install idempotent without a type
check. The logger's other configuration — its level, its `propagate` flag, its filters — is left
exactly as it is.

Running it when `settings.log_enabled` is false is a choice and it closes a case rather than
being a no-op. In that state FastMCP configures no handler at all, so a record walks
`fastmcp.server.server` → `fastmcp` → root and, finding nothing, reaches `logging.lastResort` —
a `StreamHandler` on fd 2 that writes synchronously and unbounded. That is the same writer this
record exists to remove, wearing a different name.
`test_the_sink_is_installed_even_when_fastmcp_logging_is_disabled` pins it.

## Consequences

- **stderr loses rich formatting and wrapping.** This is operator-visible and it is a real cost,
  not a detail. A FastMCP line is now `LEVEL: message` with no colour, no level column, no
  hard-wrap to the console width. A traceback is a plain CPython traceback rather than a
  `rich` panel, which means it is also *longer*: `configure_logging`'s traceback handler
  suppressed `fastmcp`, `mcp` and `pydantic` frames and capped the render at three, and none of
  that survives. More frames and less decoration; whether that reads better is a matter of taste,
  but it reads differently and an operator will notice on the first handler bug.
- **This package takes ownership of another package's logger.** It removes handlers it did not
  attach, on a logger it does not own, on the strength of source read at one version.
  `fastmcp-slim` is pinned exactly at `==3.4.7`, which bounds the drift to a deliberate bump and
  makes the bump the place to re-read `fastmcp/utilities/logging.py` — but it does not remove it.
  The wholesale removal also displaces a handler an operator attached to `fastmcp` themselves;
  `hmc_mcp.audit` remains the attachment point this package documents, and it still defers to
  what the operator put there (ADR 0040).
- **A drop can now lose a traceback that a synchronous write would have delivered.** ADR 0043
  traded a complete trail for a serving one, and this widens what that trade covers: under
  overflow a rendered handler traceback is droppable exactly as an audit record is. It is
  dropped whole — one `submit`, one queue item, one `write` — so it can never land torn or
  half-written, and the loss is counted and reported like any other. But #267's guarantee is now
  precisely "the traceback is preserved *through the sink*", subject to ADR 0043's drop rule,
  and not "the traceback is always delivered".
- **The `records-dropped` accounting still reads correctly, and it already had to.** `count` is
  items lost, not records lost — ADR 0043 said so when `server._warn` became the second producer
  sharing the queue, noting that in practice the two could not mix because `_warn` runs once
  before `.run()` against an empty queue. FastMCP's records are the third producer and they do
  mix, freely, for the life of the process. The field's meaning is unchanged and the conservation
  law still holds; what changes is that a reader reconciling a trail can no longer assume a
  non-zero `count` refers to audit records.
  `test_two_producers_share_one_bound_and_the_count_still_adds_back` drives both producers into a
  real overflow and asserts that every item is on the stream or inside a marker's count.
- **The bound is on items, and an item is no longer a fixed size.** 1024 slots was about 0.5 MiB
  at the measured record sizes. A rendered traceback is larger and has no fixed length, so the
  byte figure is now a typical case rather than a ceiling. The queue still bounds the number of
  outstanding writes, which is the property that keeps the server answering; it bounds memory
  only loosely.
- **The bound is reached sooner, and a denied caller reaches it twice as fast.** A denied call
  now submits two items where it submitted one: ADR 0040's audit record, and ADR 0046's concise
  line, which used to bypass the queue. Against a destination that has stopped draining, the
  1024 slots therefore hold about half as many audit records as before, and the `records-dropped`
  count reaches a given number in half the calls. This is a real narrowing of the
  security-observability window and it is accepted on the same terms ADR 0043 accepted the drop
  rule: a droppable trail that keeps serving, over a complete one that stops. It is also bounded
  by the same precondition — a destination nobody is draining, which under stdio is the client
  harming itself.
- **A denial's two stderr lines are now ordered.** The audit record and ADR 0046's concise line
  go onto the same FIFO from the same call, so the record precedes the line. That was not true
  before — the record went to the sink while the line went straight through FastMCP's handler —
  and `_DENIAL_LINE`'s text still asserts no ordering, because the ordering is the sink's
  property rather than something the string should claim.
- **ADR 0046's "Two writers still share fd 2" residual closes**, and ADR 0043's decision widens
  from "every stderr write this package makes" to "every stderr write the served process makes
  through a logger this package installs". Both records are amended in place to point here.
  Neither is superseded: ADR 0043's queue, drop rule, marker grammar and shutdown all still
  govern, and ADR 0046's filter is untouched.

### Residual: an embedder can still put the `RichHandler`s back

`configure_logging` really does remove and re-add the `fastmcp` logger's handlers, and this
record's handler is not exempt. Two named triggers put it back:

- calling `application.run(log_level=...)` — `run_stdio_async` and the HTTP transport wrap the
  server in `temporary_log_level(log_level)` (`fastmcp/server/mixins/transport.py:237` and
  `:354`), which reconfigures on entry *and again on exit*, so both ends of the `with` wipe the
  handler;
- calling `fastmcp.utilities.logging.configure_logging` directly.

Neither is reachable through `hmc-mcp serve`; both are reachable by an embedder composing with
`create_mcp` and driving `.run()` itself. In that process fd 2 has an unbounded writer again and
nothing reports it.

This is an argument for keeping ADR 0046's logger-level filter *as well as* the handler, not
against the handler: a filter on the emitting logger survives exactly the reconfiguration that
discards a handler, so a denial stays one line even in the process where this record's guarantee
has lapsed. Detecting the lapse and reinstalling — a watchdog, or a re-check on every dispatch —
is not undertaken here: it adds a mechanism whose failure mode is the one being fixed, for a path
no shipped entry point takes.

### Residual: the startup banner is not a log record and is not on the sink

`FastMCP.run` calls `log_server_banner`, which builds its own `Console(stderr=True)` and
`print`s to it (`fastmcp/utilities/cli.py:246`, `:268`). Nothing about it goes through the
`fastmcp` logger, so nothing here touches it: fd 2 still takes one unbounded `rich` write per
start. Observed on a real `hmc-mcp serve` subprocess at this branch's HEAD, where the banner
rendered above the first record.

Its exposure is the one ADR 0043 declined to hand-wave for `server._warn`: a fixed-size write
before `.run()` reaches the transport, so a start that blocks there is a start nobody reaches.
The difference from `_warn` is that this one is not ours to move — it is a direct console write
inside the dependency, and the levers are FastMCP's own (`FASTMCP_SHOW_SERVER_BANNER=false`, the
`--no-banner` flag, or `show_banner=False` to `.run()`). Choosing one is a change to what an
operator sees at startup and belongs to whoever wants that, not to this record.

### Residual: `Handler.handleError` still writes straight to fd 2

`_AuditHandler.emit` wraps its body and routes a failure to `logging.Handler.handleError`, which
is what every stdlib handler does and what makes a logging call safe to place anywhere. With
`logging.raiseExceptions` true — the default — that method writes a multi-line traceback
**synchronously to `sys.stderr`**, which is the behaviour ADR 0043 cited when it rejected
`QueueHandler`. Confirmed by driving a record whose `msg % args` raises through the handler at
this branch's HEAD: `--- Logging error ---` and a traceback reached the stream directly.

This is not new — the arm predates this record and ADR 0040 accepted it for the malformed
foreign record it was written for — but routing FastMCP's records through the same handler makes
it reachable by more traffic. It needs a record that fails to render, which FastMCP's own tool
error path cannot produce (it logs f-strings with no `args`), so no shipped call reaches it. Left
as it is rather than rerouted through the sink, because replacing the stdlib error contract on
the handler an operator may attach to `hmc_mcp.audit` is a decision about ADR 0040's surface, not
about where FastMCP's records go.

## Considered & rejected

**Accept it and document the operational requirement.** #323's other option: record that a
consumer must drain stderr, and stop. Rejected for the reason ADR 0043 rejected the same option
for the audit records — under stdio the party the precondition binds is the client, which spawns
the server and does not read this repository's operator documentation. ADR 0041 had already been
reduced to restating it as "choose a client that drains its child's stderr", which is advice, not
a bound.

**Remove only the handlers whose destination is fd 2, and leave the rest.** The narrower,
better-mannered version, and it was written before it was rejected. Deciding a destination means
reading `logging.StreamHandler.stream` for one shape and `rich`'s `Console.file` for the other,
comparing against a `sys.stderr` that a test harness or an embedder may have replaced since the
handler was constructed — a predicate that returns the wrong answer quietly, in the direction of
leaving an unbounded writer attached. It also answers nothing when `settings.log_enabled` is
false and there are no handlers to classify. The blunt version is testable; the careful one is
plausible.

**Keep the `RichHandler`s and add the sink handler alongside.** Preserves the rendering and
satisfies nothing: every FastMCP record would reach stderr twice, and the unbounded writer — the
whole subject of this record — would still be attached.

**Set `propagate = False` on the `fastmcp` logger while installing.** `configure_logging` sets it
when it runs, so this would only bite when `settings.log_enabled` is false, where it would stop
FastMCP records reaching an operator's root handlers. That is the operator's own configuration
and their own descriptor, and the stdio hazard it would close — a root `StreamHandler(sys.stdout)`
putting a FastMCP line into the JSON-RPC stream — is unchanged by this record and pre-dates it.
Closing it belongs to whoever decides that ADR 0040's propagation rule should bind a logger this
package does not own.

**Wrap FastMCP's handlers in `logging.handlers.QueueHandler`.** Rejected on ADR 0043's reading of
that pair, which has not changed: `QueueHandler.emit` routes `queue.Full` to `handleError`, which
writes a multi-line traceback synchronously to the descriptor that is already blocked, and
`QueueListener.stop()` joins with no timeout.
