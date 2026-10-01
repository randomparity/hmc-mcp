# ADR 0186: Stamp schema-version state on live maturity observations

## Status

Accepted on 2026-10-01 for issue #1090. Extends
[ADR 0127](0127-derived-live-verification-staleness.md)'s observation shape; its currency and
promotion rules are unchanged.

## Context

A live run may send `X-HMC-Schema-Version` or not, depending on `HMC_SCHEMA_VERSION`. The run
header and the results document's `run.schema_version` record the resolved value, with
`(not set)` when it is unset or empty (#890). The observations a maintainer copies into
`docs/capabilities/maturity.json` do not, so a promoted observation cannot be attributed to
either request environment. The observation shape is an exact key set, checked by
`just capability-inventory`, so a new field is a format change, and the 20 stored observations
were gathered without the value being recorded anywhere they could be recovered from.

## Decision

Maturity format 4 adds a required `schema_version` key to every evidence observation.

- **Value domain.** `V<n>_<n>` (the header's own token, e.g. `V1_0`), the literal
  `(not set)`, or the literal `unrecorded`. The first two are the strings the run header
  prints and `run.schema_version` records, so the two documents cannot disagree. The narrow
  grammar keeps this field, like `hmc_release` and `hardware_family`, unable to carry a
  hostname, serial or location code.
- **Emission.** The runner writes the value it resolved for the run header. It never writes
  `unrecorded`. A resolved value outside the first two forms writes no observations, with a
  message, exactly as an absent environment label or a dirty tree does; the results document
  still records it.
- **Existing observations.** Every observation stored before format 4 is migrated to
  `"schema_version": "unrecorded"`. That is the truthful value: nothing records which
  environment produced them. A fresh live run replaces one, as any re-validation does.
- **Confirmations.** `CONFIRMATION_KEYS` is unchanged. A confirmation is not evidence, and
  adding the field there would also require deciding whether gap reuse matches on it.
- **Currency and promotion.** Unchanged. `schema_version` is attribution, not a currency
  input: an observation under either environment, or `unrecorded`, promotes and goes stale
  exactly as ADR 0127 defines. The runtime projection carries no observation keys and keeps
  its format.

## Consequences

The catalog moves to format 4; a format-3 reader rejects it, and the validator rejects
format 3. A hand-copied observation missing the key fails the gate instead of passing
silently. The validator cannot tell an honest `unrecorded` from one typed onto a fresh
observation; catalog review stays the trust boundary, as ADR 0132 already states for
confirmations. A run whose `HMC_SCHEMA_VERSION` is outside the grammar loses its observations
and must be repeated with a conforming value or with it unset.

## Considered & rejected

- **Optional key, absent meaning unrecorded.** judgment: splits the one closed observation
  shape into two, and a copy that drops the key from a new observation passes unnoticed.
- **Backfill the stored observations with `V1_0`.** verified: `python3 -c` over
  `docs/capabilities/maturity.json` at `7e0476d0` lists 20 observations, all observed
  2026-09-30; neither they nor anything committed records the variable's state for that run.
- **Re-run live to replace them before landing.** judgment: cost — a hardware run to stamp
  a value the contract can mark honestly; excluded from this change's scope.
- **Record the raw variable value.** judgment: the variable admits any printable ASCII, which
  would make this the one free-text field in the observation.
- **Also stamp confirmations.** judgment: forces a gap-reuse matching rule nobody has asked
  for; no confirmation is stored (`0` at `7e0476d0`), so adding one later costs nothing.
- **Do nothing.** verified: `docs/compatibility.md` at `7e0476d0` states observations do not
  say which request environment produced them, which is the gap #1090 reports.
