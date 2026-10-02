# 0190 — Logical operations are recorded in a local SQLite store and resumed only on request

## Status

Accepted (2026-10-01), issue #1216.

## Context

Provision, reconfigure, power and decommission span several HMC writes and can outlive one MCP
call. The server persists nothing today: the HMC session is process-local (ADR 0028), and the
only operation identity is the HMC JobID (ADRs 0081, 0093). Epic #1215 requires:

- durable identity that binds requests, effects and created resources;
- explicit resume;
- reconciliation after a timeout or restart, never blind replay.

The documented MCP transport is stdio (`docs/mcp-server.md`), so each client spawns its own
server process. The operator chose a local SQLite store on 2026-10-01.

## Decision

1. **Location and lock.** The store is one stdlib `sqlite3` file in a private state
   directory: the directory is `0700` and the file `0600`. It is created on first use. The
   directory is set by `hmcpctl serve --state-dir`, and defaults to the platform state
   directory: `$XDG_STATE_HOME/hmcpctl`, or `~/.local/state/hmcpctl` on Linux, and
   `~/Library/Application Support/hmcpctl` on macOS. CLI commands that mutate partitions open
   the same default. At most one process holds an OS writer lock for its whole lifetime.
   - **The holder** serves the logical mutating tools.
   - **Any other process** opens the store read-only. It enforces ADR 0193 holds and serves
     status, but refuses logical mutations with a reason that names the lock holder's process
     id.
2. **Identity.** Every mutating logical call carries a caller `request_id` of 1–64 characters
   from `[A-Za-z0-9._-]`. The server mints an `operation_id` and stores the SHA-256 of the
   canonical request beside it. The key is (`HMC_AGENT_ID`, `request_id`); the connection is
   recorded as provenance.
   - A repeat with the same digest returns the existing operation.
   - A repeat with a different digest is refused as a conflict.
   - `continuation` and `wait_seconds` are excluded from the digest.
3. **Effects before and after.** Before each HMC write, the store records an *intent*. After
   the write, it records the *outcome* and the created identities: partition UUID, adapter
   UUIDs and MACs, VIOS, volume-group and disk names, media name, and HMC JobID. A write whose
   outcome was never recorded is `uncertain`.
4. **Execution.** A call runs in the lock holder's process and returns within `wait_seconds`
   (default 60, maximum 600) with the current phase. Work continues while that process lives.
   A caller timeout does not mean execution stopped. When a process acquires the lock, it marks
   every non-terminal record `interrupted`; nothing restarts on its own.
5. **Continuation.** The original tool, called again with the same `request_id`, takes
   `continuation`:
   - `resume` re-authorizes as a fresh call (ADR 0189), reconciles each `uncertain` effect
     against live HMC state, and continues only if every effect is classified. Anything still
     ambiguous stops in `needs_attention`.
   - `abandon` ends an interrupted or `needs_attention` operation as `abandoned`. It makes no
     HMC write and leaves its effects recorded.
   - `boot` is ADR 0191's deferred boot.

   Installer power-on is never repeated after `boot_started` is recorded.
6. **One active operation per partition.** The guard is keyed by system UUID and partition UUID,
   never by connection. A second mutating logical operation on that partition is refused and
   names the holder's `operation_id`. An operation paused at `ready_to_boot` releases the guard.
7. **Bounds.**
   - The canonical request is at most 64 KiB, and an operation holds at most 256 effects.
   - A status page holds at most 50 operations or 200 events.
   - Terminal operations are pruned after 30 days, oldest first.
   - When non-terminal operations plus holds reach 10,000, new operations and holds are refused
     with `store_full` until some are abandoned, completed or released.
8. **Failure.** If the store is unreadable, corrupt or not private, every logical mutating
   call and every continuation is refused with the reason. The store is never rebuilt silently.

## Consequences

- hmcpctl gains durable state, so backup and loss now matter. Losing the store loses
  provenance, and cleanup falls back to ADR 0192's retain path.
- Under stdio, a second concurrent client session gets status and holds but no logical
  mutations. Supporting both together means running one long-lived server.
- A process that exits mid-operation, such as a stdio client closing, stops execution between
  intent and outcome. The next lock holder marks the operation `interrupted`, and `resume`
  reconciles it.
- Isolation rests on `HMC_AGENT_ID`. MCP carries no authenticated principal.
- Ownership stamps (ADR 0011) remain advisory. The store fences nothing outside this host.

## Considered & rejected

- **Progress in the LPAR description stamp only.** judgment: fit. The stamp cannot hold effects
  from before the partition exists or after it is deleted.
- **Per-operation JSON files.** judgment: complexity. Duplicate-key, active-partition and hold
  queries would need hand-written locking and indexing.
- **Synchronous calls with no continuation.** judgment: fit. The epic requires that a caller
  timeout not imply execution stopped, and synchronous calls make the two coincide.
- **Several processes writing the store concurrently.** judgment: complexity. Background
  execution would need cross-process ownership of in-flight work.
- **Automatic resume at startup.** judgment: fit. It replays effects without a caller decision,
  which the epic forbids.
