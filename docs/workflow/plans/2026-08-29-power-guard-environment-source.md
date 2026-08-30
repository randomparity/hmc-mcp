# Power guard environment source implementation plan

## Goal

Report every case-insensitive spelling of `HMC_AUTHORIZE_POWER_OPERATIONS` as environment-sourced
and remove the obsolete `ambiguous` contract from the MCP and startup-audit schema vocabulary.

The existing effective-config resolver remains authoritative for the boolean. The reporting layer
only detects whether the environment supplied the field, and both public channels continue to share
one `PowerOwnershipGuard` object. The implementation is Python with pytest documentation and static
guardrails.

## Global Constraints

- Target architectures are amd64 and arm64; the x86_64 host is included.
- Never use bare `uv run` or `uv sync`; use `uv run --no-sync` and repository `just` recipes.
- Environment lookup reuses `config.env_var_value`, whose `str.lower()` and last-match semantics
  mirror pydantic-settings.
- The only source values after the change are `environment`, `profile`, `default`, and
  `unresolved`; an environment result has `detail=None`.
- No migration, dependency, authorization-decision, or persisted-data change is permitted.

## Task 1: Pin the corrected report behavior

Files: modify `tests/app/test_power_guard_report.py`.

Interfaces: tests consume `resolve_power_guards(policy) -> tuple[PowerOwnershipGuard, ...]` and
assert `PowerOwnershipGuard.authorize_power_operations`, `.source`, and `.detail`. Later source and
documentation work relies on these assertions defining the narrowed vocabulary.

1. Change the variant-without-profile test to expect `source == "environment"` and
   `detail is None`.
2. Change the variant-over-profile test to expect `source == "environment"` and
   `detail is None`, preserving the assertion that the environment boolean wins.
3. Add a parameterized test that inserts exact and variant spellings with opposite boolean values
   in both orders. Assert the last inserted value is the effective boolean and the source remains
   `environment` with no detail in both cases.
4. Run `uv run --no-sync pytest tests/app/test_power_guard_report.py -q`; expect the changed variant
   assertions to fail against `ambiguous` before implementation.

Acceptance: exact-case, variant, absent/profile, default, and multiple-spelling behavior are all
executable contracts and the new assertions demonstrate red before source changes.

## Task 2: Collapse environment source detection

Files: modify `src/hmc_mcp/server_tools/permissions.py`; test
`tests/app/test_power_guard_report.py`.

Interfaces: `_guard_source(config: HMCConfig) -> tuple[str, str | None]` calls the existing
`env_var_value(name: str) -> str | None` and returns `("environment", None)` on presence, otherwise uses
`config.model_fields_set` for `default` versus `profile`.

1. Remove `_guard_env_spelling` and use `env_var_value(POWER_GUARD_ENV_VAR)` directly.
2. Remove `_CASE_VARIANT_DETAIL` and all stale divergence rationale.
3. Keep unresolved handling untouched.
4. Run `uv run --no-sync pytest tests/app/test_power_guard_report.py -q`; expect all tests to pass.

Acceptance: no runtime path emits `ambiguous`; effective values still come from `HMCConfig`; no
additional abstraction or dependency is added.

## Task 3: Align startup audit and public contract records

Files: modify `tests/app/test_authorization_audit.py`, `docs/environment-variables.md`,
`CHANGELOG.md`; retain ADR 0110, this plan, and the linked specification.

Interfaces: startup calls the same `resolve_power_guards` result and submits its source/detail
fields; documentation and the Facade manifest enumerate the same source vocabulary.

1. Extend the startup-audit test fixture with a case-variant environment spelling and assert the
   emitted record contains `source: environment` and `detail: null`.
2. Remove the obsolete `ambiguous` paragraph and workaround from environment documentation.
3. Add an `[Unreleased]` Facade manifest bullet explicitly recording removal of the `ambiguous`
   literal alternative from `PowerOwnershipGuard.source` while noting no `__all__` change.
4. Run `uv run --no-sync pytest tests/app/test_power_guard_report.py tests/app/test_authorization_audit.py tests/unit/test_changelog.py tests/test_authorization_audit_doc.py -q`; expect pass.
5. Run `just verify`, `just tool-docs-check`, `just doc-freshness`, and
   `UV_NO_SYNC=1 uv run --no-sync prek run --all-files`; expect zero exit status.

Acceptance: MCP, audit, tests, public docs, changelog, and ADR vocabulary agree. Rollback is a
single git revert; there is no cleanup or migration.
