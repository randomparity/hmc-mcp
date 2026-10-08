# Invocation-local capability fingerprints (#1433)

## Problem

Capability derivation repeats import walks and hashes for operations sharing a
handler module, and report invocations derive both runtime and report states.
Issue #1433 requires equivalent results with less repeated work.

## Scope

Keep derivation in `scripts/check_capability_inventory.py`. Give each standalone
`derive_states` call a fresh fingerprint dictionary. `main` supplies one dictionary
through runtime projection and report derivation, keyed by repository root and
handler module. Compute entries only when a live observation needs them; cache
successful fingerprint values only. Keep the closure walker, digest algorithm,
validation and state evaluation unchanged. No ownership transition is needed.
Use no global or persistent cache. Parsed-import caching is omitted unless later
profiling warrants a separately reviewed design change. No ADR is needed: the
existing ADR 0127 fingerprint/freshness decision remains unchanged.

### Failure model

- Actors and deployments: local developers and CI invoking the offline gate,
  report or direct derivation functions against one stable repository snapshot.
- Invariants and assets: projection bytes, report diagnostics/status, freshness,
  root isolation, and the existing missing/unreadable path behavior.
- Accepted failure classes: concurrent source edits during a single invocation
  have no snapshot guarantee in the existing walker; callers rerun after editing.
- Covered elsewhere: baseline #1430, CI #1431/#1435, parallelism #1434;
  persistent caches, evidence promotion and product/live/security/coverage/target
  changes remain excluded under campaign ownership and #1429.

## Success

A shared module is fingerprinted once per derivation invocation, including both
CLI report phases. Separate invocations observe source and import-edge changes;
separate roots cannot share a result. The fixture corpus and current repository
retain derived states, projection bytes, diagnostics and exit statuses, including
report-only age expiry. Serial gate/report measurements show attributable work
reduction without assuming a minimum timing improvement.

## Validation

- `focused-test`: instrument real fingerprint calls for shared modules and CLI
  phases; current code must fail the once-per-module assertion. Green command:
  `uv run --no-sync pytest tests/scripts/test_check_capability_inventory.py -q --no-cov`.
- `focused-test`: the same module name in distinct roots; source edits and changed
  import edges between derivations; missing modules and unreadable resolved files;
  compare states and errors with uncached fingerprint behavior. Controlled stale
  cache and swallowed-read faults must fail these tests. Same green command.
- `focused-test`: retain the projection/report age distinction and compare exact
  JSON plus CLI diagnostics/status; controlled projection-age and stale-exit faults
  must fail. Same green command, followed by the actual gate/report recipes.
- `task-test-not-applicable`: measurement prose has no executable consumer. Record
  three serial before/after gate and report samples, commit/blob, environment and
  cache context; compare their output bytes/status. Final `just verify`, hooks and
  native CI remain campaign-scheduled requirements.
