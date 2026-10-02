# Durable logical-operation engine and `hmc_operation_status`

Issue #1218 (H2 of epic #1215). Governing decisions: [ADR 0190](../../adr/0190-durable-logical-operation-store.md)
(store, lock, identity, effects, states, guard, ledger, bounds, failure) and
[ADR 0195](../../adr/0195-logical-operations-replay-an-effect-journal.md) (execution model).
Contract context: [the H1 spec](2026-10-01-logical-lpar-workflows-design.md), *Primary tools*,
*Operation fields* and *Operations, resume and restart*.

## Problem

Provision, reconfigure, power and decommission will each span several HMC writes and outlive one
MCP call. Nothing in hmcpctl persists an operation today, so an interrupted workflow cannot be
told apart from a fresh request, and a repeat cannot be told apart from a conflicting one. The
epic's consumer issues (#1223, #1225, #1226, #1227, #1229, #1231) all compose one engine. This
change builds that engine and the read-only status tool. It wires no consumer.

## Scope

In scope (WORK:SCOPE criteria 1–11):

- `src/hmcpctl/operations/logical/store.py` — state-directory resolution, privacy checks,
  `store-id` sentinel, SQLite schema, the execution lock, bounds, pruning, and status queries.
- `src/hmcpctl/operations/logical/engine.py` — `submit()`, the continuation table, the
  per-operation worker thread, `OperationContext`, and the reconciliation classifier registry.
- `src/hmcpctl/operations/lpar/workflow_contract.py` — the shared operation record types.
- `src/hmcpctl/server_tools/operations.py` — `hmc_operation_status`.
- Registry, capability metadata, generated tool docs, `HMCPCTL_STATE_DIR` documentation, a
  state section in `docs/mcp-server.md`, and the CHANGELOG.

Out of scope, with owners (operator-approved 2026-10-02): `request_id` and classifiers on
provision (#1225) and media (#1227); reconfigure (#1226); power (#1223); decommission and ledger
removal (#1229); holds, `hold_id` and handoff (#1231); search/invoke and primary-tool metadata
(#1219); CLI list/abandon (#1286); live runs; any `hmcpctl.api` change.

## Store

**Location.** `HMCPCTL_STATE_DIR` if set and non-empty; else `$XDG_STATE_HOME/hmcpctl` or
`~/.local/state/hmcpctl` on Linux, `~/Library/Application Support/hmcpctl-state` on macOS. On
any other platform, or when `Path.home()` fails, the store refuses with `state_dir_unresolved`.
The directory holds `operations.sqlite3`, `store-id` and `execution.lock`.

**Privacy.** At every open the directory must be a real directory (not a symlink) owned by the
effective uid with no group or other permission bits; `operations.sqlite3` and `store-id` must be
regular files owned by the effective uid with mode bits `& 0o077 == 0`. Anything else refuses
with `store_not_private`, naming the path and the observed mode, never contents. Creation uses
`mkdir(mode=0o700)` followed by `chmod(0o700)` and `os.open(..., O_CREAT | O_EXCL, 0o600)`.

**Opening.** `open_store(create: bool)` returns a connection or `None`:

| On disk | `create=False` (status) | `create=True` (mutation) |
| --- | --- | --- |
| no directory, or directory without DB and sentinel | `None` | create directory, DB, schema, then sentinel |
| sentinel present, DB missing | `store_lost` | `store_lost` |
| DB present, sentinel missing | open; rewrite sentinel only when `create` | open and write the sentinel from the DB's `store_id` |
| sentinel text ≠ DB `store_id` | `store_corrupt` | `store_corrupt` |
| `schema_version` ≠ `1`, or a `sqlite3.DatabaseError` other than `OperationalError` | `store_corrupt` | `store_corrupt` |
| `OSError`, or `sqlite3.OperationalError` (busy past the timeout, I/O error) | `store_unreadable` | `store_unreadable` |

The sentinel is written after the schema commits, so a crash mid-creation leaves a DB without a
sentinel, which the third row recovers. Connections use `timeout=5`, autocommit
(`isolation_level=None`) with explicit `BEGIN IMMEDIATE` for writes, and
`PRAGMA foreign_keys=ON`. One connection per transaction, so worker threads never share one.

**Schema (version 1).** Tables `meta` (`store_id`, `schema_version`), `operations` (one row per
operation: identity, `connection`, `host`, `digest`, `request_json`, `state`, `outcome`, `phase`,
the guarded `system_uuid`/`partition_uuid`, `result_json`, `warnings_json`, timestamps;
`UNIQUE (agent_id, request_id)`), `effects` and `events` (both `ON DELETE CASCADE` from
`operations`), and `ledger` (keyed by `(operation_id, key)`, with **no** foreign key, so pruning an
operation never removes its ledger rows, ADR 0190 D7). The partition guard is a partial unique
index on `operations (system_uuid, partition_uuid) WHERE state != 'terminal' AND partition_uuid IS
NOT NULL`. The plan carries the exact DDL.

**Execution lock.** `acquire_execution_lock(state_dir)` opens `execution.lock` (`0600`), takes
`fcntl.flock(LOCK_EX | LOCK_NB)`, writes the pid, and keeps the descriptor in a process-global
map until exit. On `BlockingIOError` it reads the recorded pid and refuses with
`execution_lock_held: held by process <pid>; retry after it exits`. The first successful
acquisition in a process runs `PRAGMA quick_check` (anything but `ok` is `store_corrupt`), then
in one transaction sets every `running` operation to `interrupted` and every `intended` effect to
`uncertain`. A platform without `fcntl` refuses with `state_dir_unresolved`. On every later call
the holder compares its descriptor's `(st_dev, st_ino)` with `execution.lock` on disk; a missing or
replaced file means the lock file or the directory was removed under it, so it drops the stale
descriptor and acquires again; that recovery skips operations whose worker is alive in this
process. Removing `execution.lock` alone while a holder runs lets a second process take a fresh
lock and recover the holder's live operations; that is outside the model, and the operator
documentation forbids it. Refusal
texts that tell an operator to delete the directory say to stop every hmcpctl process first.

**Bounds and retention** (ADR 0190 D8):

- Canonical request: at most 65,536 UTF-8 bytes, else `request_too_large`.
- Effects per operation: at most 256. The 257th `ctx.effect` key raises `OperationFailed`
  before any intent is recorded.
- Body `result`: at most 65,536 bytes as JSON; an effect identity at most 4,096 bytes. Over
  either bound is a code defect, raised as `ValueError`; the run ends `needs_attention`.
- Events per operation: the newest 1,024 are kept; older ones are deleted on insert.
- Retention: each new-operation transaction first deletes terminal operations whose
  `terminal_at` is more than 30 days old (effects and events cascade).
- `store_full`: a new operation is refused when non-terminal operations plus ledger rows reach
  10,000. #1231 adds holds to that count.

## Identity

`OperationRequest(tool, agent_id, connection, host, request_id, arguments)`. `request_id` must
match `^[A-Za-z0-9._-]{1,64}$` (`invalid_request_id`). `connection` is
`store.connection_label(profile, tool=...)`: the connection the access policy authorized, as
`connection_scope.selected_connection` resolves it (a nickname becomes its profile key, and
`HMC_HOST` makes every profile `"<default>"`); an unconfigured profile is refused. `host` is the HMC host that connection resolved to
for this call (`HMCConfig.host`), because `<default>` binds late (ADR 0038). Both are recorded and
both must match on every later call. The canonical request is
`json.dumps({"tool": tool, "arguments": arguments}, sort_keys=True, separators=(",", ":"),
ensure_ascii=False)` after removing the keys `continuation`, `wait_seconds` and `hold_id` from
`arguments`; the digest is its SHA-256 hex. `operation_id` is `secrets.token_hex(16)`.

## Engine

```python
def submit(request: OperationRequest, body: Body, *, continuation: str = "none",
           wait_seconds: int = 60) -> OperationRecord
```

`Body = Callable[[OperationContext], Awaitable[BodyResult]]`. The consumer tool has already
passed `authorized()`, so each continuation is authorized as a fresh call (ADR 0189). `submit`
validates `wait_seconds` (integer 0–600, `invalid_wait_seconds`) and `continuation` (`none`,
`resume`, `abandon`, `boot`; `invalid_continuation`), opens the store with `create=True`,
acquires the lock, and then:

| Record for (agent, request_id) | `continuation` | Result |
| --- | --- | --- |
| absent | `none` | prune, check `store_full`, insert `running`, start the worker |
| absent | other | `not_found` |
| present, other connection or host | any | `connection_mismatch`, naming neither |
| present | `none`, digest differs | `request_conflict` |
| present | `none`, digest equal | return the record (wait on a live worker first) |
| present | other, a supplied argument ≠ stored | `request_conflict` |
| `terminal` | other | return the record |
| `running`, no live worker in this process | any | first set `interrupted` (its `intended` effects `uncertain`), then apply this table |
| `running`, live worker | other | `running` |
| `interrupted`, or `paused`/`needs_attention` | `resume` | set `running`, start the worker |
| `paused`/`ready_to_boot` | `boot` | set `running`, start the worker |
| `interrupted` or `paused` | `abandon` | `terminal`/`abandoned`; no HMC write; guard released |
| otherwise | other | `continuation_not_accepted`, naming the accepted set |

The lock holder is the only process that can have a live worker, so a `running` record without one
in the holder is orphaned: a worker that could not record its end (a store fault) leaves exactly
that, and this row recovers it without a restart. A record under another agent id is never visible: absent and other-agent records both give
`not_found`. "Supplied argument" means a key present in the continuation call's `arguments`;
continuation calls pass only what the caller supplied.

**Worker.** A daemon `threading.Thread` per run executes `asyncio.run(_run(...))`. A process map
of live workers keyed by `operation_id` prevents a second worker for one operation. `submit`
joins the worker for up to `wait_seconds`, then returns the record as stored. A caller timeout
leaves the worker running; process exit leaves the record `running` until the next lock
acquisition marks it `interrupted`.

**`_run`.** First, every `uncertain` effect goes to `CLASSIFIERS.get(effect.kind)`:

- `Classification("applied", identity)` → `applied`, with the live identity.
- `Classification("not_applied")` → `not_applied`.
- `Classification("needs_attention", reason=...)`, no registered classifier, or a classifier
  exception → the effect stays `uncertain` and gets a warning naming the key and the reason.

DLPAR effects and owner changes therefore reach `needs_attention`, because their consumers either
register no classifier or return it (spec *Resume*). If any effect is still `uncertain`, the run
ends `paused`/`needs_attention` without calling the body. Otherwise the body runs with a fresh
context. Its exit maps as follows:

| Body exit | State / outcome |
| --- | --- |
| returns while any effect is `uncertain` (the body caught the exception) | `paused`/`needs_attention`, warning naming the keys |
| returns `completed`, `configured` or `boot_started` | `terminal` with that outcome; guard released |
| returns `ready_to_boot` or `needs_attention` | `paused` with that outcome |
| raises `OperationFailed` or `EffectNotApplied`, no effect `uncertain` | `terminal`/`failed`, warning = message |
| raises anything else, or any effect `uncertain` | `paused`/`needs_attention`, warning = exception type and message |

The body's `result` mapping is stored as `result_json`.

**`OperationContext`:**

- `operation_id`, `agent_id`, `connection`, `continuation`, and `arguments` (the stored request
  arguments).
- `phase(name)` updates `phase` and records a `phase` event when it changes.
- `guard(system_uuid, partition_uuid)` sets the operation's partition once. Repeating the same
  pair is a no-op; a different pair is a `ValueError`. Another non-terminal operation on the
  partition raises `OperationFailed("partition_busy: operation <id> holds this partition")`.
- `effect(key, kind, target, write)` journals one HMC write. `key` matches
  `^[a-z0-9_.:-]{1,128}$`, `kind` matches `^[a-z0-9_.]{1,64}$`, `target` is at most 256
  characters.
  - A key already `applied` returns its identity without calling `write`.
  - Otherwise it records `intended` plus an `intent` event, awaits `write()`, and records
    `applied` with the returned identity mapping plus an `outcome` event.
  - `EffectNotApplied` from `write` records `not_applied` and re-raises.
  - Any other exception records `uncertain` and re-raises.
- `ledger_add(key, kind, system_uuid, partition_uuid, identity)` inserts one ledger row,
  idempotent on `(operation_id, key)`.
- `recorded(key)` returns the journaled `EffectRecord` or `None`, so a body can skip a
  precondition its own applied effect invalidates (ADR 0195).

A body opens its own HMC client inside its worker loop with the existing
`client_from_env(profile)`, where `profile` is `None` for `"<default>"`; the engine binds the
connection and host but owns no client.

`register_classifier(kind, classifier)` fills the module-level `CLASSIFIERS`, where
`Classifier = Callable[[OperationContext, EffectRecord], Awaitable[Classification]]`.
Registering a kind twice is a `ValueError`.

## Status tool

```python
@tool(effect="read", operation="operation.status", target_kind="console",
      exhaustive_targets=False)
def hmc_operation_status(operation_id: str | None = None, request_id: str | None = None,
    state: OperationState | None = None, outcome: OperationOutcome | None = None,
    limit: int = 50, cursor: str | None = None, profile: str | None = None) -> OperationPage
```

- The agent id is `build_config(profile=profile).agent_id or "hmcpctl"`, the same default
  ownership stamping uses. Records are also filtered to this call's connection,
  `store.connection_label(profile, tool="hmc_operation_status")`, and its HMC host
  (`HMCConfig.host`): the (connection, host) pair `submit` binds. A policy that grants one
  connection never lists another connection's records, and a `<default>` record made against
  another `HMC_HOST` is not listed.
- `target_kind="console"`, as `hmc_get_console_info` uses, keeps `profile` under the policy's
  connection scope without a target selector; `profile` chooses the agent id, so it must stay
  policed.
- The tool reads with `create=False` inside one read transaction: no store means an empty page,
  and a failed store raises its `OperationRefused` reason. It needs no lock and makes no HMC call.
- Filters are ANDed. `operation_id` must be 32 lower-case hex digits. `limit` is 1–50.
  Results are ordered newest first by (`created_at`, `operation_id`).
- `cursor` is URL-safe base64 of `[created_at, operation_id]`. A malformed cursor raises
  `ValueError("invalid_cursor: ...")`. A cursor narrows only within the caller's own records, so
  it carries no signature.
- Each record carries its effects (at most 256), its newest 200 events, and `events_truncated`.
- The page carries `limit`, `truncated` and `next_cursor`.
- The stored request is never returned.

## Records

In `workflow_contract.py`, frozen dataclasses with `metadata={"description": ...}` on every
field:

- `EffectRecord(key, kind, target, status, identity)`.
- `OperationEvent(seq, at, kind, detail)`; `at` is ISO 8601 UTC with a `Z` suffix.
- `OperationRecord(operation_id, request_id, tool, connection, system_uuid, partition_uuid,
  state, phase, outcome, effects, events, events_truncated, warnings, next_actions, result,
  created_at, updated_at)`; the two UUIDs name the guarded partition, or are null.
  `next_actions` is the accepted continuation set for the state, from the table above
  (`[]` for `running` and `terminal`).
- `OperationPage(operations, limit, truncated, next_cursor)`.

`OperationRefused(ValueError)` carries `.reason` and renders `"<reason>: <action>"`.
`OperationFailed` and `EffectNotApplied` are plain `Exception` subclasses.

## Failure model

**Actors and deployments**

- An MCP client agent calling consumer tools (future) and `hmc_operation_status` through one
  stdio or HTTP server process. Isolation between agents rests on `HMC_AGENT_ID`.
- Several hmcpctl processes on one host sharing one state directory: stdio sessions, an HTTP
  server, CLI commands. Only the execution-lock holder runs operations.
- A local operator who owns the state directory.

**Invariants and assets at stake**

- At most one worker per operation, and one non-terminal operation per partition.
- A recorded `applied` effect is never written again. An `uncertain` effect is never treated as
  applied or as not applied without a classifier's live read.
- The request digest binds a `request_id`, so a conflicting reuse never runs.
- Another agent id's records are invisible.
- The ledger survives pruning.

**Accepted failure classes**

- *Store calls block the event loop briefly.* Calls are synchronous with a 5-second busy timeout.
  The bound is stated here; stores hold at most about 10,000 live rows.
- *An operation whose worker process exits stays `running` until another process takes the lock*
  (ADR 0190 D2). `hmc_operation_status` shows it as `running` meanwhile; any continuation from a
  process that can take the lock recovers it, and `docs/mcp-server.md` says so.
- *A consumer release that changes an effect key or kind while an operation of its tool is
  non-terminal* replays that write. ADR 0195 makes keys a persisted contract; each consumer owns
  keeping it.
- *An effect the consumer's `write` performed but reported wrongly* (it returned an identity
  for a write that failed, or raised `EffectNotApplied` after a partial write). The engine trusts
  its consumer's classification. Each consumer's tests own it.
- *A caller that loses track of its `request_id`* reads it back through `hmc_operation_status`.
- *Unbounded resume count:* each resume adds at most one event per phase. The 1,024-event cap
  bounds storage.

**Covered elsewhere**

- Per-effect-kind classifiers, phases and continuations for real tools: #1223, #1225, #1226,
  #1227, #1229.
- Holds and their store-full contribution: #1231.
- CLI operator list/abandon: #1286.
- Independent HMC writers: ADR 0190 and the H1 spec *Failure model* (not fenced).

## Threat model

**Boundaries added**

1. The store file → resume and abandon decisions, and status output.
2. The `execution.lock` pid → refusal text.
3. `HMCPCTL_STATE_DIR`, `XDG_STATE_HOME` and `HOME` → the store location.

**Boundary widened:** none. The status tool reads only local state through the existing
`authorized()` wrapper.

**Actors**

- An MCP client with a policy grant for `hmc_operation_status`.
- Another local user on the host.
- A second hmcpctl process run by the same user.

**Controls**

1. The `0700` directory and `0600` files are checked at every open, along with ownership and
   symlinks; a failure refuses with the path and mode only. The store is created with
   `O_EXCL`. Schema version and `store-id` are checked.
2. Status filters on the caller's agent id, its policy-checked connection and that
   connection's HMC host, inside the SQL `WHERE` clause. Other agents' records
   read as `not_found` to `submit`.
3. Continuations require the recorded connection, and the consumer's `authorized()` call runs
   first.
4. Status never returns `request_json`. Errors name the failed check without echoing arguments;
   `connection_mismatch` names neither the recorded connection nor its host.
5. The pid is parsed as an integer and rendered only as an integer.
6. The state path must be absolute; the store directory's own ownership, mode and symlink status
   are checked (control 1). Its ancestor directories are trusted as operator-owned.

**Out of scope**

- A same-uid process tampering with the store: it already holds the user's HMC credentials.
- Root on the host.
- Two clients sharing one agent id (accepted by ADR 0190).

## Success

Each item maps to a test in `tests/unit/test_logical_store.py`,
`tests/unit/test_logical_engine.py` or `tests/app/test_operation_status_tool.py`:

1. Location: env override, Linux XDG and default, macOS, an unsupported platform, and an
   unresolvable home. Privacy: group/other bits on the directory, the DB or the sentinel; a
   symlinked directory. Sentinel: present-without-DB is `store_lost`, DB-without-sentinel
   recovers, and a mismatch is `store_corrupt`. A garbage DB is `store_corrupt`; a wrong
   `schema_version` is `store_corrupt`.
2. Lock: a second descriptor is refused naming the pid. Acquisition marks `running` records
   `interrupted` and `intended` effects `uncertain`. A lock file replaced under the holder is
   re-acquired with recovery, and a second process then sees the holder.
3. Identity: a repeat with the same digest returns the record without a second body run; a
   different digest is `request_conflict`; another connection or host is `connection_mismatch`; another
   agent gets `not_found`; an excluded key does not change the digest; invalid `request_id`
   values are refused.
4. Effects: intent precedes the write; an applied effect is not rewritten on replay;
   `EffectNotApplied` gives `not_applied`; another exception gives `uncertain` and
   `needs_attention`.
5. `wait_seconds=0` returns `running` while the worker finishes later; a worker past the wait
   still completes; 601 and -1 are refused.
6. Crash windows. A store that has an intent with no outcome, under a fresh lock:
   - becomes `uncertain`;
   - `resume` with a classifier answering `applied` continues without rewriting;
   - `not_applied` rewrites the effect once;
   - `needs_attention`, a changed owner or an unregistered kind pauses with the effect still
     `uncertain`;
   - `abandon` makes no `write` call and releases the guard;
   - continuations outside the state's set are refused.
   A crash before any effect replays the body from the start. A worker whose end write fails
   leaves a record the next continuation recovers in the same process. A body that swallows an
   effect's exception ends `needs_attention`.
7. The guard refuses a second non-terminal operation on the same partition and frees it on
   terminal. Ledger rows survive pruning.
8. Bounds: a request over 65,536 bytes; a 257th effect; a status page of 50 with `truncated`;
   newest 200 events with `events_truncated`; pruning after 30 days (by clock injection); and
   `store_full` at 10,000.
9. Each store failure reason refuses `submit` and status (status with no store returns an
   empty page); a store busy past the timeout is `store_unreadable`.
10. The tool: registered as `read` with operation `operation.status`; returns only the caller's
    agent records; paginates through `next_cursor`; refuses an invalid `operation_id`, cursor or
    limit; and makes no HMC request.
11. The generated tool docs, capability metadata, the env-var row and the CHANGELOG are updated.
    `just verify` is green.
