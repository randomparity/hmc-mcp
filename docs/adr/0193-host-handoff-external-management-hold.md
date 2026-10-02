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

kdive drives the partition through hmcpctl's library (`power_lpar`) or through the MCP power
and dump-restart tools. No single guard covers every partition mutation today. Ownership
enforcement has 21 call sites (`resolve_and_authorize_lpar_mutation`), and power skips it when
`authorize_power_operations` is off.

## Decision

1. **Prepare and release.** `hmc_prepare_host_handoff` has two actions, both effect `mutate`
   on local state.
   - `prepare` returns the handoff document. With `hold=true` it records the partition's one
     hold in the ADR 0190 store, keyed by system UUID and partition UUID. The hold carries a
     `hold_id`, a consumer label of at most 64 characters, and the connection as provenance. A
     repeated `prepare` with the same label returns the existing hold; with a different label it
     is refused, naming the existing hold's label.
   - `release` removes the hold matching an exact `hold_id`. It writes nothing to the HMC.
2. **Guarded set.** The check runs in hmcpctl's two front ends, the MCP dispatch and the CLI
   commands, after the partition UUID is resolved and before the first HMC write. It covers
   every operation that changes a partition's configuration, power state or existence, logical
   or specialist, whatever `authorize_power_operations` is set to. A registry-driven test
   enumerates those tools.
3. **Exemptions.**
   - `hmc_prepare_host_handoff` itself.
   - `hmc_capture_lpar_console`: a read-only capture, and kdive owns console leasing.
   - Any call that presents the matching `hold_id`. Through this, the consumer drives power and
     dump-restart through MCP.
4. **No expiry.** A hold lasts until it is released. Expiry would turn unknown consumer state
   into permission.
5. **Unknown state refuses.** If the store cannot be opened or read, every guarded operation is
   refused.
6. **Scope stated.** A hold binds this host's hmcpctl front ends only. The library API, which is
   how kdive itself acts, other hosts, the HMC GUI and direct HMC commands are not fenced. The
   handoff document says so.

## Consequences

- A consumer that never releases blocks guarded operations until an operator calls `release`.
  That is the intended direction of failure.
- A broken state directory stops specialist partition mutations too.
- Specialist partition-mutation tools gain an optional `hold_id` argument (#1231).

## Considered & rejected

- **Record the hold in the partition description.** judgment: fit. Any writer can edit the
  description, and a visible marker implies a fence that does not exist (ADR 0011).
- **Check only in logical tools.** judgment: fit. A specialist called directly or through
  invoke would bypass the hold.
- **Check in the domain layer, including the library.** judgment: fit. It would refuse kdive's
  own library calls, and kdive has no store on its host.
- **Expire holds after a TTL.** judgment: fit. See Decision 4.
