# Warn that a type-3 profile restore unconfigures partitions

## Problem

Issue #1322: the `hmc_restore_lpar_profiles` MCP description says `restore_type=3`
merges current and backup data with the current data winning conflicts, and its
WARNING mentions only profile overwrite. The live run in #627 showed that a type-3
merge also resets `resource_config` from 1 to 0 on every Not Activated partition,
on a V10R3 HMC with a POWER9 system, even when merging a backup taken moments
earlier. `CHANGELOG.md` and `docs/live-testing.md` record this; an MCP caller
reading the tool description is not told.

## Scope

- The `hmc_restore_lpar_profiles` docstring in
  `src/hmcpctl/server_tools/lpar/profiles.py`: the WARNING paragraph states the
  side effect, where it was observed, and the re-apply command
  `chsyscfg -r lpar -m <system> -o apply -p <lpar> -n <profile>`; the
  `restore_type` argument text points type 3 at that WARNING.
- One assertion in `tests/lpar/test_lpar_profile.py` over the served MCP
  description, the pattern `tests/app/test_lifecycle_schema_descriptions.py` uses.
- Generated `docs/tools/` via `just tool-docs`.
- Re-run the `profiles` live arm at the final code head and recopy only the
  `lpar_profile.backup`, `lpar_profile.sync` and `lpar_profile.restore`
  observations into `docs/capabilities/maturity.json` (ADR 0126's record, with
  ADR 0127's closure-fingerprint staleness), then regenerate
  `src/hmcpctl/_operation_maturity.json` with `just capability-metadata`.
- Out of scope: runtime behaviour, other restore types, any other stale
  observation, and the `pcie.*` dedicated-slot observations (bare-cec arm).

## Success

- The served description names `resource_config`, Not Activated partitions,
  the observed environment, and the exact re-apply command, using the wording
  of `docs/live-testing.md`.
- `just verify` and `prek run --all-files` pass.
- The three observations carry the final head's `tested_commit` and closure
  fingerprint; `lpar_profile.restore` stays `failed`.

## Validation

- Served description — `focused-test`:
  `tests/lpar/test_lpar_profile.py::test_restore_tool_description_warns_merge_unconfigures_partitions`;
  red on the old docstring (`resource_config` absent); green with
  `uv run --no-sync pytest tests/lpar/test_lpar_profile.py -q`.
- `docs/tools/` — `focused-test`: it renders only the description's summary
  line and each operation's live state, so `just tool-docs-check` goes red after
  the observation recopy and green after `just tool-docs`.
- Recopied observations — `focused-test`: `just verification-report` shows the
  three operations stale after the docstring edit and current after the recopy;
  `just capability-inventory` validates the catalog.
- The profiles arm runs per `docs/live-testing.md` (preflight, arm, recovery);
  an independent read-only partition baseline before and after the run matches.
