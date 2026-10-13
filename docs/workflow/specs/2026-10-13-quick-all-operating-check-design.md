# Empty-feed operating check reads quick/All (#1500)

## Problem

Since #1309, create and provision check a new partition's name on the target system's scoped
`LogicalPartition` feed. A system that is not operating answers that feed with 204, so
`require_operating_system` (`client/client_resolution.py`) refuses the empty answer unless the
system's `State` is `operating`. It reads that `State` from `ManagedSystem/{uuid}`. The 2026-10-13
capture (#1290, V11R2 SP1120) saw that read time out for a `No Connection` system, so the refusal
arrives only after `HMC_TIMEOUT` and names a timeout instead of the system state.
`ManagedSystem/quick/All` answered fast on the same HMCs and carries every system's `State`.

## Decision

1. `require_operating_system(list_system_summaries, system_uuid, resources)` reads `State` from
   the `quick/All` summary whose `UUID` matches `system_uuid` case-insensitively, through the
   existing `list_system_summaries()` (#1309). It never reads `ManagedSystem/{uuid}`.
2. The decision keys on `State` alone, compared case-insensitively with `operating`, as #1309's
   `system_operating` does. `ReferenceCode` is not consulted (blank for a disconnected system on
   one captured HMC).
3. A system missing from `quick/All`, or a summary that is not an object, reads `State`
   `unknown` and is refused, as a missing `ManagedSystem/{uuid}` document was. A failed `quick/All`
   read propagates its `HMCError`, as a failed `ManagedSystem/{uuid}` read did.
4. The error names the system (`SystemName` and UUID), its `State`, its `StateDetail` when
   non-blank, and ends "retry when it reconnects".
5. **Scope of the change: the shared function, not only create/provision.** Its two callers are
   `list_logical_partitions` and `list_vios`, and create/provision reach it through the former.
   The narrower alternative, a `quick/All` pre-check in the create and provision operations ahead
   of their name check, would duplicate the `State` decision in two places, add a `quick/All` read
   even when the scoped feed is non-empty, and leave the stalling read in every other caller.
   For every caller the change keeps the refuse/accept outcome for every captured `State` and
   replaces a read that can stall with one that answered fast. The trade: the message carried
   `DetailedState`, and `quick/All` has only `StateDetail`, blank in every captured row, so that
   detail text is lost in practice. Fleet health, utilization, capacity, ownership discovery and
   decommission inherit the faster refusal through these client methods without code changes;
   their own `ManagedSystem` feed and per-system reads stay with #1501, whose inventory can drop
   `require_operating_system`.

### Failure model

1. Actors: an agent or CLI user creating, provisioning, listing or resolving partitions or VIOSes
   on a system whose scoped feed is empty.
2. Invariants: an empty scoped feed is trusted only when `quick/All` reports that system's
   `State` as `operating`; create and provision perform no write before that check.
3. Accepted failure classes: a system that changes state between the feed read and the `quick/All`
   read is judged on the later state (as before, with a different second read). A fleet walk over
   N systems with empty feeds reads `quick/All` up to N times, where it read N per-system entries.
   A `quick/All` row whose `UUID` is not a string is skipped, so its system reads `unknown` and
   is refused.

## Success

1. Create and provision against a `no connection` target in the committed
   `rest-ms-quick-all-no-connection-v11r2` capture refuse with the system's name, UUID and
   `State 'no connection'`, issue no `ManagedSystem/{uuid}` read, and issue no write.
2. The client LPAR and VIOS listings refuse an empty feed for a non-operating system, accept it
   for `operating` in any letter case, and refuse a system absent from `quick/All` as `unknown`.
3. The tests in 1–2 fail when the check reads `ManagedSystem/{uuid}` again (controlled fault).
4. `just verify` and `prek run --all-files` pass.

## Validation

- Success 1: focused-test, `tests/unit/test_unscoped_lookup_fail_closed.py` (respx), asserting
  the `ManagedSystem/{uuid}` route was never called and no mutating request was sent.
- Success 2: focused-test, `tests/unit/test_empty_feed_system_state.py`.
- Success 3: controlled fault, recorded in the PR.
- Changelog: task-test-not-applicable, prose reviewed.
