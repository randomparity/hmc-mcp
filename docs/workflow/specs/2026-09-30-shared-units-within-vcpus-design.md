# Shared processing units within virtual processors (#1034)

## Problem

A virtual processor uses at most 1.0 processing unit. #938 and #949 refuse *defaulted*
processing units that break this on the `mksyscfg` path; explicit units are never checked. The
REST create document (`build_lpar_document`) renders them unchecked, and the `mksyscfg`
fallback builds the same record unchecked, so `max_procs=4, max_vcpus=2` or `desired_procs=1.5`
with desired vcpus defaulted to 1 reach the HMC and fail there.

## Scope

- `documents/lpar.py` gains `shared_vcpu_defaults(resources) -> tuple[int, int, int]` — the
  existing `mksyscfg` defaults for omitted counts (min 1, desired 1, max `max(desired, 2)`),
  moved here unchanged — and `shared_units_over_vcpus(resources) -> str | None`, which returns
  a refusal message when any *explicit* unit level (min, desired, max) exceeds that level's
  effective virtual processors, else `None`. The message names each violating
  `<level>_procs=<value>` and `<level>_vcpus=<count>` pair and the CLI options to change
  (`--min-procs/--min-vcpus`, `--procs/--vcpus`, `--max-procs/--max-vcpus`).
- REST: `_processor_config` raises `ValueError(message)` for a non-dedicated resource set
  before rendering, so `build_lpar_document` (create, provision, VIOS create) refuses before
  the PUT. `units == vcpus` is accepted.
- `ssh/lpar.py`: `_shared_processors` (inside `complete_create_resources`, which #1164 made
  the defaulting step of both the LPAR REST create and `mksyscfg`) takes its vcpu defaults from
  `shared_vcpu_defaults` (the duplicated literals are removed) and raises
  `HMCCLIError(message)` after the existing #938/#949 guards, before any command or PUT. LPAR
  create, provision and the 406 fallback therefore refuse with `HMCCLIError` there; the
  `_processor_config` `ValueError` is what refuses VIOS create and direct document builds.
- Omitted vcpus on REST are validated as the `mksyscfg` defaults, per the issue's "effective
  vcpu count after defaults"; both paths therefore refuse the same input, and a REST 406 never
  reaches the fallback with a violating record. Omitted units are not checked on REST (the HMC
  chooses them). Rejected: checking only explicit vcpus on REST — `desired_procs=1.5` with no
  vcpus would still reach the HMC, failing the issue's expected behaviour.
- No ADR: the rule is fixed by the issue; the only alternative is recorded above.
- CHANGELOG `[Unreleased]` → `### Fixed` entry.

### Failure model

1. Actors and deployments: an MCP client, CLI operator, or library caller creating,
   provisioning, or creating a VIOS partition on the REST path or its `mksyscfg` fallback.
2. Invariants: no create request (REST PUT or `mksyscfg`) is sent for a shared request whose
   explicit units exceed the level's effective vcpus; dedicated requests and modify are unchanged.
3. Accepted: a REST create whose HMC would have defaulted vcpus above 1 is now refused unless
   the caller passes vcpus (bounded: the message names them). Read-only lookups
   (`find_partition_by_name`, system UUID resolution) still precede the refusal, as for the
   existing document validation. `provision_lpar(dry_run=True)` does not build the document and
   does not refuse (provision preflight is out of this surface). A non-positive explicit vcpu
   count is validated as its `mksyscfg` default (`or` semantics, as `_shared_processors`
   sends it); REST renders it verbatim and leaves it to the HMC to reject (defaulting rules are
   excluded).
4. Covered elsewhere: the per-vcpu minimum (#938, platform-specific); modify validation,
   dedicated validation, and defaulting rules (operator exclusions, 2026-09-29).

## Success

1. `build_lpar_document` raises `ValueError` naming `max_procs`/`max_vcpus` and
   `--max-procs` for `max_procs=4, max_vcpus=2`, and naming `desired_procs`/`desired_vcpus` and
   `--vcpus` for `desired_procs=1.5` with vcpus omitted; the tests match the unit field, the
   vcpu field, and the option together.
2. `create_lpar_via_cli` raises `HMCCLIError` for the same two inputs without running a command.
3. `units == vcpus` at each level is accepted on both paths; a dedicated request with units
   above its vcpu fields is accepted.
4. Existing #938/#949 tests and the rendered records for fitting input are unchanged.

## Validation

- REST refusal (S1): focused-test, parametrized case in `tests/unit/test_documents.py`; red
  before the `_processor_config` edit (no exception raised).
- SSH refusal (S2): focused-test, parametrized case in `tests/lpar/test_lpar_http406.py`
  asserting `run.assert_not_awaited()`; red before the `_shared_processors` edit.
- Boundary and dedicated (S3): focused-test, same modules, `units == vcpus` renders/sends.
- Unchanged behaviour (S4): focused-test, the existing #938/#949 and document tests.
- CHANGELOG: task-test-not-applicable; prose read by humans, no executable consumer.
