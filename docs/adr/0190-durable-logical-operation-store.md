# 0190 — Logical operations are recorded in a local SQLite store and resumed only on request

## Status

Accepted (2026-10-01), issue #1216. Partially supersedes ADR 0005 and ADR 0027:
`hmc_provision_lpar`, `hmc_decommission_lpar` and their CLI mirrors now require `request_id`
(Decision 3). ADR 0005's steps and no-rollback rule, and ADR 0027's rules, otherwise stand.

## Context

Provision, reconfigure, power and decommission span several HMC writes and can outlive one MCP
call. The server persists nothing today: the HMC session is process-local (ADR 0028), and the
only operation identity is the HMC JobID (ADRs 0081, 0093). Epic #1215 requires:

- durable identity binding requests, effects and created resources;
- explicit resume;
- reconciliation after timeout or restart, never blind replay.

The documented MCP transport is stdio (`docs/mcp-server.md`), so each client spawns its own
server process, and the CLI acts on the same partitions. The operator chose a local SQLite store
on 2026-10-01.

## Decision

1. **Location.** The store is one stdlib `sqlite3` file under a private state directory
   (directory `0700`, file `0600`).
   - The directory is `HMCPCTL_STATE_DIR` if set. Otherwise it is the platform state directory:
     `$XDG_STATE_HOME/hmcpctl` or `~/.local/state/hmcpctl` on Linux, and
     `~/Library/Application Support/hmcpctl-state` on macOS, which stays apart from the config
     directory `hmcpctl config init` creates there. Other platforms must set
     `HMCPCTL_STATE_DIR`.
   - The server and the CLI read the same setting.
   - The first logical mutation creates the directory, the store and a `store-id` sentinel. A
     directory whose sentinel exists while the store is missing counts as a lost store: it is
     refused and never recreated. With neither sentinel nor store, the ADR 0193 hold check
     finds no hold and passes; only the Decision 9 conditions refuse.
2. **Locks.** Any process may run short SQLite transactions, such as placing or releasing
   holds and reading status.
   - A separate OS *execution lock* gives one process the right to run logical operations.
     A process acquires it at its first logical mutation.
   - A server keeps it until the server exits.
   - A CLI command keeps it only while the command runs.
   - A process that cannot acquire it refuses the logical mutation, naming the holder's process
     id, and tries again on its next call.
   - When a process acquires the lock, it marks every `running` record `interrupted`.
3. **Identity.** The key is (`HMC_AGENT_ID`, `request_id`), where `request_id` is 1–64
   characters from `[A-Za-z0-9._-]`.
   - The server mints an `operation_id` (32 lower-case hex digits) and records the connection
     and the SHA-256 of the canonical request, which excludes `continuation`, `wait_seconds`
     and `hold_id`.
   - A repeat with the same digest returns the operation.
   - A repeat with a different digest is a conflict.
   - A continuation call needs only `request_id` and `continuation`, plus `hold_id` when a
     hold now covers the partition; any other inputs it gives must match the digest.
   - A continuation from a connection other than the recorded one is refused.
4. **Effects.** Each HMC write is recorded twice: an *intent* before it, and the *outcome* and
   created identities after it. A write with no recorded outcome is `uncertain`.
5. **Execution.** A call returns within `wait_seconds` (default 60, maximum 600). Work continues
   while the lock holder lives. A caller timeout does not mean execution stopped.
6. **States and continuation.**

   | State | Partition guard | Continuations accepted |
   | --- | --- | --- |
   | `running` | held | none (refused as running) |
   | `interrupted`, or `paused` with outcome `needs_attention` | held | `resume`, `abandon` |
   | `paused` with outcome `ready_to_boot` | held | `boot`, `abandon` |
   | `terminal` (`completed`, `configured`, `boot_started`, `failed`, `abandoned`) | released | none (the record is returned) |

   `resume` re-authorizes as a fresh call (ADR 0189) and reconciles each `uncertain` effect
   against live state. It continues only if every effect is classified. `abandon` writes nothing
   to the HMC. Installer power-on happens at most once per operation: `resume` after a boot
   wait that ended `needs_attention` re-polls refcodes against the recorded baseline.
7. **Partition guard and ledger.**
   - The guard allows one non-terminal operation per partition, keyed by system UUID and
     partition UUID.
   - A separate *resource ledger*, keyed the same way, holds each created partition, disk and
     media identity, plus the declared install facts.
   - Pruning never touches the ledger. Decommission removes ledger entries as it deletes or
     retains each resource.
8. **Bounds.**
   - A canonical request is at most 64 KiB, and an operation has at most 256 effects.
   - A status page holds at most 50 operations, each with at most its newest 200 events.
   - Terminal operations are pruned after 30 days.
   - Once non-terminal operations, holds and ledger entries together reach 10,000, new
     operations and holds are refused with `store_full`.
9. **Failure.** A store that is unreadable, corrupt, not private, or lost per Decision 1 refuses
   logical mutations, continuations and ADR 0193's guarded operations, and reports the reason.

## Consequences

- hmcpctl gains durable state that needs backing up. Deleting the whole state directory removes
  every hold and ledger entry, which is an operator action like `release`.
- A `request_id` reused after its operation was pruned starts a new operation. Duplicate
  creation is still refused by provision's name-uniqueness precondition (ADR 0005).
- A long-lived server keeps the execution lock, so on its host the CLI and other stdio sessions
  serve status and holds but refuse logical mutations until that server exits.
- A process exit mid-write leaves the effect `uncertain` for `resume`.
- Isolation rests on `HMC_AGENT_ID`, because MCP carries no principal. The CLI's operator
  commands list and abandon operations under any agent id. The CLI answers to the credential
  holder, as ADR 0188 notes.

## Considered & rejected

- **Progress in the LPAR description stamp only.** judgment: fit. It cannot hold effects from
  before the partition exists or after it is deleted.
- **Per-operation JSON files.** judgment: complexity. The duplicate-key, guard, ledger and hold
  queries would need hand-written locking.
- **Synchronous calls with no continuation.** judgment: fit. The epic requires that a caller
  timeout not imply execution stopped.
- **Prune the ledger with its operation.** judgment: fit. ADR 0192 would retain owned storage
  forever after 30 days.
- **Automatic resume at startup.** judgment: fit. It replays effects without a caller decision.
