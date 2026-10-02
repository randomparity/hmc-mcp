# 0193 — A host handoff places a local hold that refuses this deployment's partition mutations

## Status

Accepted (2026-10-01), issue #1216.

## Context

Epic #1215 hands partitions to an external consumer, kdive's adopt-only BYOH provider
(`docs/kdive-tier-a-contract.md`). While it is handed off, logical reconfigure, power and
decommission must not silently conflict with that consumer. If the consumer's state is
unknown, they refuse. Release must be explicit and must not delete the partition, and nothing
may claim to fence independent HMC writers.

No single guard covers every partition mutation today. Ownership enforcement
(`resolve_and_authorize_lpar_mutation`, ADR 0011) has 21 call sites, and power skips it when
`authorize_power_operations` is off (`config.py`; kdive contract, Ownership).

## Decision

1. **Prepare and release.** `hmc_prepare_host_handoff` has two actions, both effect `mutate`
   on local state:
   - `prepare` returns the handoff document. With `hold=true` it also records a hold in the
     ADR 0190 store, keyed by connection, system UUID and partition UUID, carrying a
     `hold_id`, a consumer label (≤ 64 characters) and a creation time.
   - `release` removes a hold. It needs the exact `hold_id`, and touches nothing on the HMC.
2. **Enforcement point.** Every served tool whose effect is `mutate` or `destructive` and that
   resolves a partition target checks the store after resolving the partition UUID and before
   its first HMC write. This covers logical and specialist tools alike, whatever
   `authorize_power_operations` is set to. A registry-driven test enumerates those tools and
   fails for any that cannot reach the check. A held partition refuses with the `hold_id` and
   consumer label.
3. **No expiry.** A hold lasts until it is released. The consumer's state is unknown to
   hmcpctl, and a hold that lapsed would end the refusal without the consumer agreeing. Holds
   count toward the store's bounds, but pruning never removes one.
4. **Unknown state refuses.** When the store cannot be opened or read, every partition
   mutation the check guards is refused, specialist tools included.
5. **Scope stated.** A hold binds this deployment only. Other deployments, the HMC GUI and
   direct HMC commands are not fenced. The handoff document says so.

## Consequences

- A consumer that never releases blocks this deployment's mutations of that partition until an
  operator calls `release`. That is the intended direction of failure.
- A broken state directory stops specialist partition mutations too, not only logical ones.
  That coupling is the cost of refusing on unknown consumer state.
- Read tools, inspection and the handoff document itself remain available while held.
- Console acquisition is not a partition mutation and is not blocked. kdive owns console
  leasing.

## Considered & rejected

- **Record the hold in the partition description.** judgment: fit. Other writers can edit
  the description, and a visible marker suggests a fence that does not exist (ADR 0011).
- **Check the hold only in logical tools.** judgment: fit. `hmc_power_off_lpar` through invoke
  or a direct call would bypass it.
- **Expire holds after a TTL.** judgment: fit. Expiry turns unknown consumer state into
  permission.
