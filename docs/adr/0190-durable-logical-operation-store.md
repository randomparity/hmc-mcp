# 0190 — Logical operations are recorded in a local SQLite store and resumed only on request

## Status

Accepted (2026-10-01), issue #1216.

## Context

Provision, reconfigure, power and decommission span several HMC writes and can outlive one
MCP call. The server persists nothing today: the HMC session is process-local (ADR 0028) and the
only operation identity is the HMC JobID (ADRs 0081, 0093). Epic #1215 requires durable
operation identity that binds requests, effects and created resources, explicit resume, and
reconciliation after timeout or restart without blind replay. The operator chose a local SQLite
store on 2026-10-01.

## Decision

1. **One store per deployment**: a stdlib `sqlite3` file in a private state directory
   (directory `0700`, file `0600`), created at startup when absent. One process holds the
   writer lock. A second process opening the same store reads it, so it still enforces ADR 0193
   holds, but serves no logical mutating tool.
2. **Identity.** Every mutating logical call carries a caller `request_id` (1–64 characters,
   `[A-Za-z0-9._-]`). The server mints an `operation_id` and stores the SHA-256 of the
   canonical request beside it. The key is (connection, `HMC_AGENT_ID`, `request_id`). Repeating
   a key with the same digest returns the existing operation. Repeating it with a different
   digest is refused as a conflict.
3. **Effects before and after.** Before each HMC write the store records an *intent*. After it,
   the store records the *outcome* and the identities of anything created (partition UUID,
   adapter UUIDs and MACs, VIOS/VG/disk names, media name, HMC JobID). A write whose outcome was
   not recorded is `uncertain`.
4. **Execution.** A call runs its operation in the server process and returns after at most
   `wait_seconds` (default 60, maximum 600) with the current phase. Work continues while the
   process lives. A caller timeout says nothing about whether execution stopped. At startup every
   non-terminal record becomes `interrupted`, and nothing restarts on its own.
5. **Resume** is the original tool called again with the same `request_id` and `resume=true`.
   It re-authorizes as a fresh call (ADR 0189), reconciles each `uncertain` effect against live
   HMC state, and continues only when every effect is classified as applied or not applied.
   Anything still ambiguous stops in `needs_attention`. Installer power-on is never replayed
   after `boot_started` has been recorded.
6. **One active operation per partition.** A second mutating logical operation on a partition
   with a non-terminal operation is refused and names the holder's `operation_id`.
7. **Bounds.** Canonical request ≤ 64 KiB; ≤ 256 effects per operation; status pages ≤ 50
   operations or 200 events. Terminal operations are pruned after 30 days or beyond 10,000
   records, oldest first. Non-terminal operations and handoff holds are never pruned.
8. **Failure.** A store that is unreadable, corrupt or not private refuses every logical
   mutating call and every resume, and reports the reason. It is never rebuilt silently.

## Consequences

- hmcpctl gains its first durable state, with backup and loss to consider. Loss of the store
  loses provenance, so later cleanup falls back to the retain-by-default path of ADR 0192.
- Isolation rests on `HMC_AGENT_ID` and the connection. The MCP transport carries no
  authenticated principal, so two clients of one server sharing an agent id share operations.
- Ownership stamps (ADR 0011) remain advisory evidence. The store is not a distributed lock,
  and two deployments that share one HMC do not see each other's operations.

## Considered & rejected

- **Encode progress in the LPAR description stamp only.** judgment: fit. It cannot hold
  effects recorded before the partition exists or after it is deleted, and the description is
  short.
- **Per-operation JSON journal files.** judgment: complexity. Duplicate-key, active-partition
  and hold queries would need hand-written locking and indexing that SQLite already provides.
- **Fully synchronous calls with no background continuation.** judgment: fit. A boot wait
  exceeds common client timeouts, which would force a resume on every provision.
- **Automatic resume at startup.** judgment: fit. It replays effects with no caller decision,
  which the epic forbids for installation and destructive cleanup.
