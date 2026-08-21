# Spec: every served-process stderr writer goes through the bounded sink (#330)

Status: designed 2026-08-21 · Issue #330 · Branch `feat/bounded-sink-330` · BASE `main`

## Problem

ADR 0051 bound the `fastmcp` logger to ADR 0043's bounded stderr sink. Two unbounded
fd-2 writers survive it:

1. **uvicorn on `serve --http`.** `main_http` calls
   `.run(transport="streamable-http")`; FastMCP builds a `uvicorn.Config`, whose
   `__init__` runs `configure_logging()` unconditionally (`uvicorn/config.py:297`),
   landing a synchronous `StreamHandler(stderr)` on `uvicorn` *after* the sink install
   (and an unbounded stdout writer on `uvicorn.access`).
2. **The `mcp` namespace, both transports.** No handler anywhere on it, so WARNING+
   records reach `logging.lastResort` — raw, unformatted, synchronous, fd 2.

## Decision (operator-settled, 2026-08-21)

Take over all three loggers — `uvicorn`, `uvicorn.access`, `mcp` — onto ADR 0043's
bounded stderr sink at startup, on both transports. The residuals close; they are not
documented-away. ADR 0051's residual sections and ADR 0043's Consequences are amended
to match. The access log moves into the bounded sink: accepted.

## Levers, verified against installed `fastmcp-slim==3.4.7` + `uvicorn==0.52.1`

- `FastMCP.run(**transport_kwargs)` forwards unknown kwargs to `run_http_async`, which
  accepts `uvicorn_config: dict` merged into the `uvicorn.Config(app, host, port,
  **config_kwargs)` call (`fastmcp/server/mixins/transport.py:266,342-357`).
- `uvicorn.Config.__init__(..., log_config=None)` still runs `configure_logging()`,
  but that function skips `dictConfig` entirely when `log_config is None`
  (`uvicorn/config.py:384`) — no default handlers are ever attached, so nothing lands
  on fd 2 after the install and nothing needs re-installing. This is cleaner than
  re-running `dictConfig` after `Config` construction (unreachable from `main_http`)
  or supplying a replacement `LOGGING_CONFIG`.
- `dictConfig` also set levels and propagation, and skipping it loses those too:
  left alone, `uvicorn`/`uvicorn.access` sit at NOTSET (effective WARNING from root) —
  which would silently *disable* the INFO-level access log instead of moving it — and
  with `propagate=True` a parent+child binding pair renders every access record twice
  (`callHandlers` walks ancestors). The install therefore sets both uvicorn loggers to
  INFO and `propagate=False`, reproducing what uvicorn's own `LOGGING_CONFIG` would
  have produced; this is a documented exception to ADR 0051's "only the handlers"
  rule for the uvicorn pair specifically. `fastmcp` and `mcp` stay handlers-only:
  neither sits inside another bound namespace, so nothing double-renders through them.
- `temporary_log_level(log_level)` runs with `None` (neither entry point passes
  `log_level`) and reconfigures nothing — the survival argument ADR 0051 already made.

## Changes

1. **`src/hmc_mcp/server.py`** — generalize the install: one function binds each of
   `fastmcp`, `uvicorn`, `uvicorn.access`, `mcp` (remove-all-handlers, then add one
   `_AuditHandler` on the shared sink with `StreamSafeFormatter(_FASTMCP_LINE_FORMAT,
   f"{name}: ")`, where `_FASTMCP_LINE_FORMAT` stays `"%(levelname)s: %(message)s"`).
   Per-producer prefix keeps the column-0 forgery guard
   and names the producer. Renamed from `install_fastmcp_stderr_sink` — clean cutover,
   all callers/tests migrate. Called from `_serve_application` as today, so both
   transports get all four bindings unconditionally.
2. **`main_http`** passes `uvicorn_config={"log_config": None}` through `.run()`
   (`log_level` deliberately not passed: uvicorn's `configure_logging` applies its
   `log_level` block only to the `uvicorn.error`/`uvicorn.access`/`uvicorn.asgi`
   children and never to `uvicorn` itself, so levels belong to the install).
3. **Tests** (`tests/app/test_connection_authorization.py`, alongside the existing
   ADR 0051 sink tests):
   - after the serve-path install, each of the four loggers carries exactly one
     handler, and none targets `sys.stderr` directly;
   - constructing `uvicorn.Config` the way `run_http_async` does (with our
     `uvicorn_config`) attaches no default `StreamHandler` and leaves the sink
     binding intact — pinned through the real `main_http`/`.run()` path by stubbing
     `uvicorn.Server.serve`;
   - an access-format record on `uvicorn.access` is rendered exactly once through the
     sink at INFO (pins both the level fix and propagate=False against the two failure
     modes review found: silent level drop, parent+child double render);
   - a WARNING record on `mcp` reaches stderr through the sink with the `mcp: `
     prefix (stdio transport); WARNING is the floor because `mcp` stays handlers-only
     at NOTSET and inherits root's effective WARNING — records below it stay gated by
     the logger-level walk exactly as they were under `lastResort`, which is the
     status quo this binding preserves, not a regression it introduces;
   - idempotence of the generalized install.
3b. **`tests/conftest.py`** — extend the autouse `isolate_audit_logging` fixture to
   snapshot-and-reset handlers, level and propagate for `uvicorn`, `uvicorn.access`
   and `mcp`, exactly as `_PRISTINE_FASTMCP` does for `fastmcp`: the generalized
   install mutates process-global state on all four loggers, and without this the
   first serving test leaks a sink handler plus INFO/propagate=False onto every later
   module in the session.
4. **ADRs** — amend `docs/adr/0043-non-blocking-stderr-diagnostics.md` (Consequences:
   the "does not widen" clause becomes closed) and
   `docs/adr/0051-fastmcp-logging-through-the-bounded-sink.md` (the two residual
   sections fold into the decision; scope statements widen from `fastmcp` to the four
   loggers). No new ADR: this extends 0051's own decision by its own reasoning.

## Non-goals (still-open 0051 residuals, untouched)

Startup rich banner; `Handler.handleError`'s direct fd-2 writes; embedders who call
`configure_logging`/`log_level=` themselves and put the old handlers back.

## Acceptance

1. After `hmc-mcp serve --http` startup, `uvicorn`, `uvicorn.access`, `mcp`, and
   `fastmcp` all route through the bounded sink; no unbounded handler remains on fd 2.
2. On stdio, `mcp` routes through the bounded sink.
3. Tests pin the binding, including "a `uvicorn.Config` built via the serve path does
   not reintroduce a default fd-2 handler".
4. ADR 0043 Consequences and ADR 0051 residual sections reflect the closed residuals.
5. Guardrails green (`just test`, `just smoke`, `just verify`); PR green + mergeable;
   body references `Closes #330`.
