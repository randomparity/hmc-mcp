# Observation schema-version stamp — design

Issue #1090 (parent #890). Decision: [ADR 0186](../../adr/0186-observation-schema-version-stamp.md),
extending the observation record in
[the live-verification staleness design](2026-09-06-live-verification-staleness-design.md#observation-record).

## Problem

Live maturity observations do not record whether the run sent `X-HMC-Schema-Version`, so an
observation promoted into `docs/capabilities/maturity.json` cannot be attributed to a request
environment. The run header and `run.schema_version` already carry the value.

## Design

**Shape.** `check_capability_inventory.ATTEMPTED_KEYS` gains `schema_version`;
`MATURITY_FORMAT_VERSION` becomes 4. Two module constants:

- `SCHEMA_VERSION = re.compile(r"V\d+_\d+|\(not set\)")` — what a run may emit.
- `UNRECORDED_SCHEMA_VERSION = "unrecorded"` — the legacy value, accepted by the validator only.

`_validate_observation` reports `"<label>: schema_version must be V<n>_<n>, (not set) or
unrecorded"` when the value is neither a `SCHEMA_VERSION` full match nor the legacy string.
`CONFIRMATION_KEYS`, `gap_is_current`, currency derivation and the runtime projection are
untouched.

**Emission.** `main` already resolves `schema_version = env_var_value("HMC_SCHEMA_VERSION") or
"(not set)"`. It passes that string to `_emit_observations(state, path, environment,
schema_version, repo_root)`, which, after the environment check and before any other guard,
prints `HMC_SCHEMA_VERSION is not V<n>_<n> or unset — observations not written` and returns
`False` when `SCHEMA_VERSION` does not fully match. Otherwise every emitted observation gets
`observation["schema_version"] = schema_version` beside `tested_commit`. `missing_scope` rows
are unchanged. `record_verified` is unchanged: the value is a run fact filled at emission,
like `tested_commit`.

**Data.** Each of the 20 stored observations gains `"schema_version": "unrecorded"` after
`hardware_family`; `format_version` becomes 4. No other catalog content changes.

**Docs.** `docs/compatibility.md` replaces the sentence saying observations do not record the
value with one saying they do, as `schema_version`, and that pre-format-4 observations read
`unrecorded`. The runner's module docstring and `docs/capabilities/README.md`'s observation
example and prose say the same. ADR 0127's Status gains an "Extended by ADR 0186" line, and the
staleness design's observation-record section gains a one-line pointer here.

## Failure model

1. **Actors and deployments**
   - a maintainer running the live runner from a clean checkout on an operator host;
   - a maintainer hand-copying runner output into `maturity.json`;
   - CI and the prek hook running `just capability-inventory` offline.
2. **Invariants and assets at stake**
   - `maturity.json` is a published, gate-checked contract: every stored observation passes the
     format-4 validator after migration;
   - no observation field can carry a hostname, serial or location code;
   - an emitted value equals the run header's and `run.schema_version`'s string for that run.
3. **Accepted failure classes**
   - a hand-typed `unrecorded` on a fresh observation passes the gate — catalog review is the
     trust boundary (ADR 0132), and the value under-claims rather than misattributes;
   - a run with a non-conforming `HMC_SCHEMA_VERSION` writes no observations — bounded: the
     emission message names the cause and the results document keeps the raw value.
4. **Covered elsewhere**
   - re-running live tests to replace `unrecorded` observations — unowned, excluded by scope;
   - the results document's shape — #890's run-provenance child (done);
   - live-scenario dispatch drift — #1094 (`just scenario-gap`).

## Validation

| Contract | Mode | Evidence |
|---|---|---|
| Validator requires the key and its domain | focused-test | `tests/scripts/test_check_capability_inventory.py`: missing key fails `expected exactly`; `V1_0`, `(not set)`, `unrecorded` accepted; `v1_0`, `V1_0 `, `hmc01.lab.example.com`, `` rejected |
| Format 4 required | focused-test | existing `format_version must be integer` assertions move to 4 |
| Emission stamps the run's value | focused-test | `tests/test_live_runner.py`: emitted observation carries `(not set)` and `V1_0` when passed, and validates against the catalog shape |
| Emission refuses a non-conforming value | focused-test | `tests/test_live_runner.py`: `V1_0;x` returns `False`, prints the message, writes nothing |
| Stored catalog migrated | focused-test | `just capability-inventory` exits 0 on the migrated catalog |
| Docs sentences | task-test-not-applicable | prose with no executable consumer |
