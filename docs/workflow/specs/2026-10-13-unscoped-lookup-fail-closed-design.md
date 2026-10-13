# Unscoped name lookups fail closed on unreadable systems (#1309)

## Problem

Without a system selector, `find_partition_by_name` and `find_vios_by_name` resolve a name through
`LogicalPartition/search/(PartitionName==…)` and `VirtualIOServer/search/(PartitionName==…)`.
Issue #1309 asked whether those searches stall while a managed system is `No Connection`, as the
HMC-wide feeds did in #1293.

A read-only capture on 2026-10-13 answered it. It covered two HMCs on V11R2 SP1120, each with one
managed system in `No Connection`:

- Both searches answer in 0.1–2.8 s. A name on an operating system gets 200 with one entry. A
  name that exists nowhere gets 204. A VIOS name on the `No Connection` system **also** gets 204
  from both searches, and the HMC-wide `LogicalPartition` feed leaves that system's partitions
  out. The search does not stall, but it is silently incomplete: a "not found" can be wrong, and
  a duplicate can be missed.
- The unscoped `GET ManagedSystem` feed timed out at 600, 300 and 60 s on both HMCs, and
  `ManagedSystem/{uuid}` timed out for the `No Connection` system and, on one HMC, for an
  operating one. `read_fleet` reads that feed through `inventory_managed_systems()`, so the
  ADR 0197 unscoped listings hang on these HMCs.
- `ManagedSystem/quick/All` answered fast on both and lists every system with its `State`
  (`no connection` for the disconnected one).

`hmc_create_lpar`'s duplicate-name check and read-back, and `hmc_provision_lpar`'s duplicate-name
check, call the unscoped lookup even though both already know the target system.

## Decision (operator, 2026-10-13: "Search + quick/All")

1. **Name lookups.** The unscoped lookups keep the HMC-wide search. A hit is returned as before.
   On a miss they read `quick/All`; when any system is not `operating`, they raise `HMCError`
   naming those systems ("not found on the readable managed systems … supply a managed-system
   selector, or retry when they reconnect") instead of reporting not-found. When every system
   operates, not-found is unchanged.
2. **Create-time duplicate checks.** Create and provision resolve the target system first and
   check the name with the scoped lookup on that system, and create's read-back is scoped the
   same way. The scoped feed already refuses an empty answer from a non-operating system (#1301),
   so the check cannot pass on an unread system, and it fails before any write. A same-named
   partition on a different system no longer blocks the create; the HMC allows it. An unscoped
   lookup of that name reports the ambiguity while both systems are readable, but returns the
   readable twin when the other's system is not.
3. **Fleet inventory.** `read_fleet` takes its system list from `quick/All`
   (`list_system_summaries()`), not the full `ManagedSystem` feed. A system whose `State` is not
   `operating`, or whose `UUID` is not a UUID, is recorded in `unreadable_systems`; `detailed_state`
   comes from `quick/All`'s `StateDetail` when it is not blank. The re-read after a failed scoped
   read also uses `quick/All`. The null-property fallback no longer applies to listings, because
   `quick/All` does not trip it.

### Field evidence

The `quick/All` keys used — `UUID`, `SystemName`, `State`, `StateDetail` — are in the committed
capture `tests/fixtures/live/rest-ms-quick-all.json` (V10R3). Its systems are all `operating`,
with `StateDetail` empty. The `no connection` value is in the captured `SystemState` vocabulary
(`tests/fixtures/live/vocabulary/enums-*.json`). No committed fixture shows `quick/All` for a
non-operating system, and none shows a non-empty `StateDetail`, so the mapping of `StateDetail` to
`detailed_state` is by name only. The tests use synthetic fixtures that mirror the evidence.

### Failure model

1. Actors and deployments: an agent or CLI user resolving a partition or VIOS by name, or
   creating one, against an HMC where some managed system is not operating.
2. Invariants and assets: a "not found" is only reported when every system was readable; a
   create never proceeds on an unproven duplicate check; listings never read the full
   `ManagedSystem` feed.
3. Accepted failure classes: a name lookup that misses while any system is down now fails instead
   of answering, even when the name exists nowhere; the error says how to scope it. A system
   that leaves `operating` between the `quick/All` read and the search is not detected. A search
   hit is returned without reading `quick/All` (decision 1), so a same-named partition on an
   unreadable system is not reported as an ambiguity. The ambiguity path of the unscoped lookups,
   and the non-operating refusal of an empty scoped feed (`require_operating_system`, which reads
   `ManagedSystem/{uuid}`), still read the endpoints the capture saw time out; both fail closed
   and are reported as follow-ups.
4. Covered elsewhere: the scoped feeds' non-operating refusal (#1301, `require_operating_system`).

## Success

1. An unscoped LPAR or VIOS lookup that the search finds returns the entry without reading
   `quick/All`.
2. A 204 search plus a `quick/All` listing with one `no connection` system raises an error naming
   that system; an all-operating `quick/All` returns not-found as before.
3. Create and provision with an unreadable target system refuse before any write; their name
   checks read the target system's scoped feed, not the HMC-wide search.
4. `read_fleet` reads `quick/All` and never the full `ManagedSystem` feed, and records
   non-operating systems as before.
5. `just verify` and `prek run --all-files` pass.

## Validation

- Success 1–2: focused-test, `tests/unit/test_unscoped_lookup_fail_closed.py` (respx).
- Success 3: focused-test, same module, asserting no PUT or CLI create was issued.
- Success 4: focused-test, `tests/unit/test_fleet_listing.py`.
- ADR 0197 consequence text and the changelog: task-test-not-applicable, prose gated by
  `just adr-numbering` and review.
