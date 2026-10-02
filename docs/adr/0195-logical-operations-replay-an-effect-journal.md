# 0195 — Logical operations run in a worker thread and resume by replaying an effect journal

## Status

Accepted (2026-10-02), issue #1218. Implements ADR 0190 Decisions 4–6; ADR 0190 otherwise stands.

## Context

ADR 0190 requires each HMC write to be recorded as an intent and an outcome, requires a call to
return within `wait_seconds` while work continues, and requires `resume` to reconcile `uncertain`
effects before anything else is written. Two facts shape how:

- Every sync tool handler runs its own `asyncio.run` (`src/hmcpctl/_app.py`, `run_sync`). A task
  started inside a call is cancelled when the call returns.
- Six consumer issues (#1223, #1225, #1226, #1227, #1229, #1231) will each add a multi-write
  workflow. Resume rules written once in the engine are written once; rules written per tool are
  written six times.

## Decision

1. **One worker thread per run.** `submit` starts a daemon thread that runs the operation body
   under its own `asyncio.run`, then joins it for at most `wait_seconds`. The thread, not the
   call, owns the work, so it survives the call and dies with the process.
2. **The body is replayed against a journal.** A body performs each HMC write through
   `ctx.effect(key, kind, target, write)`, with a key stable across runs of the same request.
   - An effect already `applied` returns its recorded identity and is not written again.
   - An effect `not_applied` or not yet recorded is written.
   - `resume` and `boot` re-run the body from the start once every `uncertain` effect has been
     classified by the live read registered for its kind.
   There is no stored program counter and no per-tool resume code.
3. **Only `EffectNotApplied` means "not applied".** A `write` that raises it is recorded
   `not_applied`; any other exception leaves the effect `uncertain`. Consumers raise it only
   where the HMC's answer proves the write did not happen.
4. **No classifier means `needs_attention`.** A kind without a registered classifier, a
   classifier that fails, and a classifier that reports an owner change all leave the effect
   `uncertain` and pause the operation. DLPAR effects register none (H1 spec *Resume*).

## Consequences

- A body must be deterministic in its effect keys for a given request. Keys derived from live
  reads, such as a name chosen from current inventory, must be recorded through an effect or the
  ledger first.
- A precondition that the operation's own applied effect invalidates, such as provision's
  name-uniqueness check (ADR 0005) after its partition exists, must run inside that effect's
  `write` or be skipped when `ctx.recorded(key)` shows it applied; otherwise replay refuses the
  operation's own work.
- Effect keys and kinds are a persisted contract. A consumer release must not change them while
  an operation of its tool can be non-terminal, or `resume` writes again.
- Reads between writes run again on every replay. That costs HMC calls and is the point:
  replay revalidates live state before the next write.
- A long-blocking store call holds only its own worker's loop, never another operation's.
- A consumer that reports a partial write as `EffectNotApplied` gets a duplicate write on
  replay. That classification is each consumer's to test.

## Considered & rejected

- **Run the body as a task on the calling event loop.** verified: `run_sync` is
  `asyncio.run(fn())` (`src/hmcpctl/_app.py`, main `b7123903`). A probe that creates a
  0.2-second task inside `asyncio.run` and returns at once printed `work finished: False` after
  a 0.4-second wait (CPython 3.11.15, Linux): the task is cancelled with the call.
- **A stored phase pointer with per-tool resume code.** judgment: complexity. Each of six
  consumers would hand-write resume points, and a missed one replays a write.
- **One shared background event loop for every operation.** judgment: fit. One body blocking in
  a synchronous store or SSH call would stall every other operation, and the HMC client's
  per-call lifecycle (`client/core.py`) would need a cross-thread owner.
- **Treat an HTTP 4xx `HMCError` as not applied in the engine.** judgment: fit. Whether a 4xx
  proves nothing changed depends on the write: a job submission can fail after side effects. The
  consumer knows; the engine does not.
