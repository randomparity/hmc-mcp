# Scratch create processing units (#947)

## Problem

ST8's `hmc_create_lpar` sends `desired_vcpus`/`max_vcpus` (3/6 in the example) but no
processing units. On the `mksyscfg` fallback, #938's guard refuses more than one virtual
processor without explicit units, and the HMC rejected the old 0.1 default (HSCL0622).

## Scope

- `LiveTestConfig` gains required `scratch_create_desired_procs` and
  `scratch_create_max_procs` (floats), read from `LIVE_TEST_SCRATCH_CREATE_DESIRED_PROCS` and
  `LIVE_TEST_SCRATCH_CREATE_MAX_PROCS`. Required, not derived: #938 records the per-processor
  minimum as platform-specific, so no default is computed from the vCPU count. "Required"
  means required in `.env`; the dataclass defaults are float literals 0.3 / 0.6, matching
  `.env.example`, because `_decode_saved_config` and preflight's `_env_text` read them.
- `from_env_file` parses keys ending `_PROCS` as float, rejects a non-finite value or one
  `<= 0`, and reports `desired > max` as `inconsistent resource limits`, as for the vCPUs.
- `_create_and_confirm_scratch_lpar` adds `desired_procs` and `max_procs` to `resources`.
  Minimum units stay at the SSH default (0.1 for the default one minimum processor).
- `.env.example` samples 0.3 / 0.6 (0.1 per virtual processor); `docs/live-testing.md` names the
  keys; one CHANGELOG `Fixed` entry notes the new required keys.
- No ownership transition: the runner already owns scenario settings.

### Failure model

1. Actors and deployments: a local operator running the live harness from a checkout, editing
   their git-ignored `.env`.
2. Invariants: an existing `.env` without the keys fails at config load, naming both keys,
   before any HMC call; no partition is created with guessed units.
3. Accepted: units too small for the platform's per-processor minimum, or above the vCPU
   count, are refused by the HMC, not pre-validated (platform-specific, #938). A results
   document saved before this change no longer restores artifacts (warning, run continues).
4. Covered elsewhere: live proof of the create (#879); other arms' sizing (operator).

Threat model: the added boundary is two `.env` values from the local operator, who is trusted
with the whole file. Control: float parse plus finiteness/positivity/ordering; the tool schema
validates types again. Out of scope: hostile `.env` authors (they already choose the target).

## Success

1. The create call's `resources` carries `desired_procs`/`max_procs` equal to the config.
2. The checked-in `.env.example` loads, and yields 0.3 / 0.6.
3. A file missing either key, with a value that is non-positive, non-finite, or not a float,
   or with desired above max, raises `ValueError` from `from_env_file`.

## Validation

- Create call carries units (S1): focused-test,
  `test_lpar_lifecycle_captures_jobs_and_clears_scratch_identity`; red before the lpar.py edit.
- Example loads (S2): focused-test,
  `test_live_config_reads_the_complete_example_and_ignores_exports` asserts the two floats.
- Rejections (S3): focused-test, parametrized `from_env_file` cases via `_example_env_with`.
- Docs and CHANGELOG: task-test-not-applicable; prose read by operators, no executable consumer.
