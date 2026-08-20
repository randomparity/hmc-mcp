# 0046 — A policy denial is one line on stderr, and only a policy denial

## Status

Accepted (2026-08-20)

## Context

A routine authorization denial reached the operator as a `rich`-boxed traceback panel.
Reproduced against `main` at `1bf6458` with `fastmcp-slim==3.4.7`, in-process through
`fastmcp.Client` against `hmc_mcp.server.create_mcp(policy)`, with **no** logging
configuration applied by the probe: a policy granting `hmc_power_on_lpar` on connection
`lab`, called with `profile="other"`, wrote **44 lines** to stderr — the ADR 0040 audit
record, then `Error calling tool 'hmc_power_on_lpar'`, then a panel of frames ending in
`ConnectionScopeError`. A `targets = { lpar = ["scratch-01"] }` grant called on `lp-1`
wrote the same 44 lines ending in `TargetScopeError`. The two are not merely alike: they
reach the same `except Exception` arm of `FastMCP._call_tool` and are rendered by the
same handler, so nothing distinguishes them at the boundary. PR #307 made the target
dimension bind tools declaring no connection argument, which puts more calls on the
second shape than when #267 was filed, and does not change this.

A server enforcing its policy exactly as configured therefore looks, to the operator
reading fd 2, like a server crashing on every denied call.

The client is not affected and must not become affected. It receives ADR 0038's closed
denial template inside a `ToolError`, and that string is the contract ADR 0038 and
ADR 0039 fixed.

The audit stream settles what the server-side line has to carry. Since ADR 0040 every
decision writes one structured record naming the policy, the tool, the effect, the
decision, the reason code, the connection (selector and resolved), the targets, and the
attribution. That record is present in both reproductions above, one line before the
panel. Everything the traceback could tell an operator about *this* denial, the audit
record already tells them in a form they can filter. The traceback is the redundant one,
exactly as #267's own notes predicted it would be once #224 landed.

## Decision

**`hmc_mcp.server` installs one `logging.Filter` on the logger FastMCP renders tool
errors through. A record whose exception is a `ConnectionScopeError` or a
`TargetScopeError` loses its traceback, drops to `WARNING`, and carries a fixed line.
Every other record passes untouched.**

The filter is installed by `install_denial_log_filter()`, called from
`_serve_application` beside `install_audit_sink()` and for the same reason it is there:
composing an application must not mutate global logging state (ADR 0040). It is
idempotent.

Two facts about FastMCP make it work, and #267 asked for exactly this to be established
rather than assumed:

- `FastMCP._call_tool` logs an unhandled handler exception through
  `fastmcp.server.server.logger`. The filter imports that **logger object by reference**,
  not by name, so a FastMCP that moves or renames it fails at import — loudly — rather
  than silently rendering panels again.
- `fastmcp.utilities.logging.configure_logging` attaches two `RichHandler`s to the
  `fastmcp` logger and routes between them on `record.exc_info is not None`. Clearing
  `exc_info` is how a record asks for the plain one-line handler. That is FastMCP's own
  filter predicate, not an internal we reach around.

The dependency is bounded by an exact pin. `pyproject.toml` requires
`fastmcp-slim[server]==3.4.7` and `fastmcp-slim[client]==3.4.7` — not a floor, a pin — so
an upgrade is a deliberate edit, and `tests/app/test_connection_authorization.py` asserts
on captured stderr in that edit's test run. #267 recorded this direction as needing "a
FastMCP-level hook whose stability across versions has not been established here". It is
established here, and the answer is that the hook is a stdlib `logging.Filter` over a
pinned dependency.

A **logger** filter rather than a **handler** filter: `configure_logging` removes and
re-adds the `fastmcp` logger's handlers on every call, so a filter attached to a handler
is discarded by the next reconfiguration.

The line reads `authorization denied; the authorization audit record carries the
decision`. Nothing is interpolated into it. The audit record carries the particulars, and
a fixed string cannot be a disclosure surface. It deliberately does not say which record
is "the" one: the audit record goes onto ADR 0043's async sink while this line is written
straight through by FastMCP's handler, so their order on stderr is not fixed and the text
does not claim one.

Measured after the change, on the same two probes: **44 lines to 4** (the audit record,
plus the one warning `RichHandler` wraps to three at an 80-column console). The client's
`ToolError` string is byte-for-byte what it was, pinned by two tests — one with the filter
installed and one without, so the pin cannot be satisfied by editing the expected string.

## Consequences

**A handler bug keeps its panel.** This is the whole reason the filter tests the exception
type instead of the log level or the message. #267 rejected suppressing FastMCP's
tool-error rendering because it would take genuine handler bugs with it; the rejection was
right and this record does not reverse it. A tool raising anything that is not a scope
error is rendered exactly as before, and
`test_an_unexpected_handler_error_still_renders_its_traceback` fails if that stops being
true.

**The `configuration-unreadable` denial is quieted too.** It is a `ConnectionScopeError`
and it is genuinely an operator-facing condition — the config file cannot be read at all.
It is not silenced: it keeps its audit record with `reason: "configuration-unreadable"`,
and the client still receives the full template naming the tool. What it loses is the
frame list, which pointed at `dispatch_scope.authorize` rather than at the unreadable
file, and never named the file (deliberately — ADR 0038 chains the `ConfigError` as
`__cause__` and interpolates nothing from it). Nothing that identified the fault is lost.

**Two writers still share fd 2.** ADR 0043 routes everything this package writes through
one bounded queue and one daemon thread, because a blocked `write()` on fd 2 wedges the
server. FastMCP's `RichHandler` is not on that queue and never was; this record makes its
denial output much smaller — a per-denial panel was the largest recurring contributor —
but does not bring it under the sink. Doing so means taking over FastMCP's handlers, which
is the blanket re-levelling #267 rejected, and is not undertaken here.

**A test that reads stderr must drain the sink first.** The audit record and the FastMCP
line arrive by different routes, and only the audit route is asynchronous. The tests use
`audit._SINK.drain(audit._DRAIN_TIMEOUT)` and normalize whitespace before asserting,
since `RichHandler` hard-wraps to the console width.

## Alternatives considered

**Convert the scope errors to a FastMCP `ToolError` at the boundary.** `FastMCP._call_tool`
has an `except FastMCPError` arm that logs at `e.log_level` with `exc_info=False` — a
genuine, concise, no-traceback path, and the most tempting option in the file. It was
rejected on what that arm does next: it re-raises the error itself rather than wrapping it,
so the client would stop receiving `Error calling tool 'x': <template>` and start receiving
`<template>`. That is #267's second rejected direction, and reading the source confirmed
rather than overturned it.

**Suppress or re-level FastMCP's tool-error rendering wholesale.** Rejected as filed: it
takes handler bugs with it. The filter above is the narrow form of the same idea and is
narrow because it inspects the exception.

**A `fastmcp` middleware.** Middleware wraps `_call_tool` from the outside; the
`logger.exception` call is inside it. The panel is already written by the time any
middleware sees the exception.
