# Canonical HMC release token for live evidence

## Problem

`docs/capabilities/maturity.json` records one V10R3 HMC under two `hmc_release`
values, `V10R3` and `V10R3M1060`, because the value is whatever the operator typed into
`LIVE_TEST_ENV_HMC_RELEASE` and the runner accepts both. Each catalog refresh can move
rows between the two, so release-grouped evidence shows two environments where there is
one (#1335).

## Scope

- The canonical form is `V<version>R<release>M<level>`, for example `V10R3M1060`: it
  matches the V11 rows' `V11R2M1120` and carries the maintenance level.
- `scripts/live_test_runner.py` gets a runner-only pattern requiring the `M<level>` part
  and applies it in `_read_environment` in place of the catalog's `HMC_RELEASE`. A
  value outside a key's grammar is rejected with a message naming that key's expected
  form, appended to the existing "does not match its grammar" text; the value itself is
  never echoed, because the check exists to keep hostnames and serials off disk.
- `check_capability_inventory.HMC_RELEASE` is unchanged and keeps admitting the bare
  `V10R3` rows already in the catalog.
- `docs/capabilities/README.md` "Recording an observation" states the canonical form,
  its JSON examples use it, and its grammar note says the catalog keeps `M` optional
  for older rows while the runner requires it.
- `.env.example`'s placeholder becomes `V10R3M1060`, so copying the example still
  starts a run.
- Out of scope: rewriting existing rows, whose maintenance level no tracked evidence
  proves; #1334 re-observes them with the canonical token.

## Success

- A `.env` carrying `LIVE_TEST_ENV_HMC_RELEASE=V10R3` fails at startup with a message
  naming `V10R3M1060` as the expected shape; `V10R3M1060` is read unchanged.
- The catalog validator still accepts every committed row.
- No confirmed gap in `maturity.json` carries a release, so reuse through
  `gap_is_current` is unaffected today.

## Validation

- Offline tests in `tests/test_live_runner.py`: a bare release is rejected with the
  expected-form message, a canonical release is read (the existing
  `test_both_environment_keys_are_read` moves to `V10R3M1060`), and `.env.example`'s
  environment pair passes `_read_environment`. The rejection test is shown to fail when
  the runner pattern is reverted to the catalog's.
- `just verify` and `uv run --no-sync prek run --all-files`. No live run is needed.
