# Ownership identity helpers design

## Problem

`src/hmcpctl/operations/lpar/ownership.py` reads partition and system names
from REST documents by hand in three places, instead of the validated helpers
`resource_identity.lpar_name_from_uuid`/`system_name_from_uuid` (#1002 / PR
#1024). The hand-rolled reads accept values the helpers reject: `str(lpar_name)`
coerces a non-string name to text, and `if not lpar_name` lets a
whitespace-only name through.

## Scope

Replace each read with the matching helper, translating a `None` result into
the site's existing message/exception:

- `_partition_name` (~296-317): call `lpar_name_from_uuid`; keep the
  `ValueError("No LPAR … found …")` message on `None`. The GET's 404
  (`HMCError`) propagates unchanged — the helper calls the same client method
  and adds no handling.
- The inline read in `resolve_lpar_ownership_names` (~555-559): call
  `lpar_name_from_uuid`; keep `ValueError(f"LPAR {lpar_uuid!r} has no partition name")` on `None`.
- `_resolve_system_name` (~587-600): call `system_name_from_uuid` inside its
  existing `try`/`except HMCError`; keep the SSH fallback and logging. `None`
  falls through to SSH exactly as today's falsy-name check does.
Excluded (operator 2026-09-24 "Approve all"): adding or widening any
`resource_identity` helper. `_partition_name`'s 404 propagation is unchanged.

### Failure model

1. Actors: hmcpctl operator/automation calling an LPAR mutation or
   ownership-inspection tool against a live HMC.
2. Invariants: none new — this only narrows accepted name shapes, so a
   malformed name can only become today's existing "not found" error, never
   the reverse.
3. Accepted: a whitespace-only/non-string name is treated as absent, per the
   helpers' contract from #1002, adopted here rather than invented.
4. Covered elsewhere: helper validation/tests are owned by
   `resource_identity.py` (#1002 / PR #1024).

## Success

- All three reads call `lpar_name_from_uuid` or `system_name_from_uuid`.
- Each site's message, exception type, and (for `_resolve_system_name`)
  fallback/logging are unchanged.
- `_partition_name`'s 404 propagation is unchanged.
- `just verify` passes with no `HMC_*` exported.

## Validation

- Contract: `_partition_name` raises `ValueError` on a missing/whitespace-only
  name, 404 propagates. Mode: focused-test — `tests/unit/test_ownership.py`,
  new whitespace-only case; existing 404 case retained.
- Contract: the inline read in `resolve_lpar_ownership_names` raises
  `ValueError` on a missing/whitespace-only name. Mode: focused-test —
  `tests/unit/test_ownership.py`, new whitespace-only case.
- Contract: `_resolve_system_name` returns the REST name when usable, else
  SSH, else the caller's selector. Mode: focused-test —
  `tests/unit/test_ownership.py`, new whitespace-only case exercising the SSH
  fallback; existing `HMCError`/SSH-failure cases retained.
