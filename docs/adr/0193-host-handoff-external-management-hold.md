# 0193 — A host handoff places a hold that refuses hmcpctl's own partition mutations

## Status

Accepted (2026-10-01), issue #1216.

## Context

Epic #1215 hands partitions to an external consumer, kdive's adopt-only BYOH provider
(`docs/kdive-tier-a-contract.md`). While a partition is handed off:

- logical reconfigure, power and decommission must not silently conflict with the consumer;
- unknown consumer state refuses;
- release is explicit and never deletes the partition;
- nothing may claim to fence independent HMC writers.

kdive acts through hmcpctl's library (`power_lpar`) or through the MCP power and dump-restart
tools. No single guard covers every partition mutation today. Ownership enforcement has 21 call
sites, and power skips it when `authorize_power_operations` is off.

## Decision

1. **Prepare and release.** `hmc_prepare_host_handoff` returns one result shape for both
   actions: `{action, hold, document}`, where `document` is null for `release`.
   - `prepare` with `hold=true` records the partition's one hold in the ADR 0190 store, keyed
     by system UUID and partition UUID. The hold holds a `hold_id`, a consumer label of at most
     64 characters, and the placing agent id and connection.
   - A repeat from the same agent id with the same label returns the hold.
   - Any other repeat is refused, naming the label, agent id and creation time but never the
     `hold_id`.
   - `release` needs the exact `hold_id`, or a call from the placing agent id. It writes
     nothing to the HMC.
2. **Where the check runs.** It runs inside each tool's `authorized()` wrapper, so direct calls
   and `hmc_invoke_tool` both pass it (ADR 0189), and in the CLI's partition commands. It runs
   after the partition UUID is resolved and before the first HMC write. It covers every operation
   that changes a partition's configuration, power state or existence, whatever
   `authorize_power_operations` is set to. A registry-driven test enumerates those tools.
3. **Exemptions.**
   - `hmc_prepare_host_handoff` itself.
   - `hmc_capture_lpar_console`, because console leasing is kdive's.
   - A call presenting the matching `hold_id`, which lets the consumer drive power and
     dump-restart through MCP.
4. **No expiry.** Expiry would turn unknown consumer state into permission.
5. **Unknown state refuses.** A store that ADR 0190 Decision 9 refuses also refuses every
   guarded operation.
6. **Scope stated.** A hold binds this host's hmcpctl front ends only. These are not fenced, and
   the handoff document says so:
   - the library API, through which kdive acts;
   - other hosts;
   - the HMC GUI;
   - direct HMC commands.
7. **Recovery.** The CLI's operator command lists holds and releases one by partition. It
   answers to the credential holder.

## Consequences

- A consumer that never releases blocks guarded operations until someone releases the hold.
- A broken state directory stops specialist partition mutations too.
- Specialist partition-mutation tools gain an optional `hold_id` (#1231).
  `docs/kdive-tier-a-contract.md` must say so.
- `hold_id` is a capability, not a secret against the local operator.

## Considered & rejected

- **Record the hold in the partition description.** judgment: fit. Any writer can edit the
  description, and a visible marker implies a fence that does not exist (ADR 0011).
- **Check only in logical tools.** judgment: fit. A specialist called directly or through
  invoke would bypass the hold.
- **Check in the domain layer, library included.** judgment: fit. It would refuse kdive's own
  library calls, and kdive's host has no store.
- **Expire holds after a TTL.** judgment: fit. See Decision 4.
