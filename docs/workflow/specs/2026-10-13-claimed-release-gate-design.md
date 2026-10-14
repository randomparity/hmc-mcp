# Claimed release evidence gate

## Problem and scope

Issue #1478 needs an executable release gate for the claimed set under accepted
[ADR 0212](../../adr/0212-release-scoped-live-verification.md). Extend the existing
inventory command and consume #1511's `load_release_claims`; no new schema,
owner, compatibility layer or ADR is needed. Development runbooks belong to
#1514, snapshot creation to #1508 and window execution to #1513.

## Contract

`uv run --no-sync python scripts/check_capability_inventory.py --require-current
 docs/capabilities/releases/v0.1.0.json` validates the inventory and frozen
projection as usual, loads that claimed file against discovered operation IDs,
and reports only claimed operations. The option implies the verification report;
explicit `--verification-report` is also accepted. Its argument is one existing
versioned JSON file, using the existing loader and route validation unchanged.

The gate exits 0 exactly when each claimed operation derives `current` from
candidate source closures and catalog evidence at the current UTC instant.
Unclaimed stale, failed, unevidenced and unrecorded states do not fail the gate.
An empty validated set succeeds vacuously and reports zero operations: the
selection of release scope remains the operator's responsibility. Missing or
malformed files and unknown operations fail through the inventory diagnostic.
Every non-current claim is printed by operation ID, state and optional reason,
and returns 1. CLI usage errors return 2.

Reuse `derive_states` with age checks enabled: exactly 90 days passes, a greater
age fails, changed closures fail, missing records remain `unrecorded`, missing
live evidence remains `unevidenced`, and failed observations remain `failed`.
The existing validator rejects malformed timestamps; future observation dates
retain the existing derive_states behavior (no upper-age violation). Claims identify
operation evidence; their validated arm/scenario routes plan execution and do
not change the existing operation-keyed currency calculation.

`--fail-on-stale` and `--require-current` are mutually exclusive because they
select different failure sets. The existing report without either option stays
informational. `--release` remains a separate opt-in snapshot/closure equality
check and may accompany the claimed gate; ordinary checks stay frozen. No gate
writes catalog, snapshots or projections unless the existing explicit
`--write-runtime-projection` option is supplied.

## Success and validation

Focused tests cover mixed claimed/unclaimed states, all non-current states,
empty sets, named diagnostics, malformed/missing/unknown claims, CLI combinations,
and exact 90-day plus one-microsecond boundaries. A fixture-backed `main` test
uses actual claim loading, derivation and projection checks; process execution
against the committed release file proves CLI wiring without live hardware.
Run focused tests, lint/type checks, then final `just verify` and pinned hooks.
Metadata acceptance proves only that recorded evidence meets this policy.

## Failure model

- Actors/deployments: release operators and CI in a trusted source checkout.
- Invariants/assets: only selected operations determine currency failure;
  malformed input cannot silently pass; installed snapshot behavior is preserved.
- Accepted: a trusted operator may select an empty set; recorded evidence cannot
  independently prove hardware success; existing future-observation behavior and
  checkout mutation races are unchanged by this read-only extension.
- Other owners: claim relationships #1511, live authorization/recovery #1513,
  snapshot promotion #1512 and operational release instructions #1514.

## Threat model

The supplied local path enters the existing JSON loader and validator. IDs are
validated against the registry, never executed or used as destinations. No new
network, credentials, shell interpolation or permissions are introduced.
Repository Python and filesystem integrity retain existing trust assumptions.
