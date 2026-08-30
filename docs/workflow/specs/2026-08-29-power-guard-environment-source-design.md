# Power guard environment source design

## Scope and outcome

Issue #547 requires the effective power-ownership guard report to describe all case-insensitive
spellings of `HMC_AUTHORIZE_POWER_OPERATIONS` as environment-sourced. The change removes the stale
`ambiguous` alternative and detail from the shared value used by the MCP response and startup audit
record. It does not change configuration precedence or authorization behavior.

This design follows [ADR 0110](../../adr/0110-case-insensitive-power-guard-source.md).

## Architecture and data flow

`resolve_power_guards` continues to build the effective `HMCConfig` first. `_guard_source` then
calls `config.env_var_value("HMC_AUTHORIZE_POWER_OPERATIONS")`, reusing the `str.lower()` and
last-match semantics that mirror pydantic-settings. A match returns
`("environment", None)`; without a match, `model_fields_set` distinguishes `profile` from
`default`. Existing exception handling remains the only producer of `unresolved` and its closed
detail.

`PowerOwnershipGuard` remains the shared schema object. The MCP report serializes it directly, and
serve startup submits the same fields to the audit sink, preventing channel-specific vocabulary
drift.

The existing resolver reads the process environment during config construction and again during
source classification. This design assumes `os.environ` remains stable for one report resolution;
concurrent mutation can produce a stale source label and is a pre-existing residual, not new scope
for snapshot plumbing.

## Behavioral contract

- The exact uppercase spelling reports `environment` and no detail.
- Any supported case variant reports `environment` and no detail, both with and without a profile.
- With no matching environment spelling, a supplied profile value reports `profile` and an absent
  value reports `default`.
- If multiple case-insensitive spellings are simultaneously present, `env_var_value` and
  pydantic-settings use the last match in `os.environ` order; the report classifies that effective
  value as `environment` and does not invent a second winner.
- Configuration failures continue to report `unresolved` with the existing closed detail.
- The MCP report and startup audit record expose the same narrowed source vocabulary:
  `environment`, `profile`, `default`, or `unresolved`.

## Errors, compatibility, and documentation

No new error path is introduced. Removing a documented literal alternative is a pre-1.0 public
contract change under ADR 0029, so the `[Unreleased]` Facade manifest records the removal. Public
environment documentation removes the obsolete workaround and names case-insensitive environment
matching as deterministic. ADR 0107 remains valid because it delegates record values to the shared
guard resolver; ADR 0110 governs the narrowed vocabulary.

## Testing

Focused report tests cover exact case, a variant without a profile, a variant overriding the
opposite profile value, and absent environment with both profile and default outcomes. A
parameterized simultaneous-spellings test inserts exact and variant keys with opposite boolean
values in both orders, then asserts both that the last inserted value is the effective
`authorize_power_operations` value and that its source is `environment` with no detail.
Startup-audit coverage asserts that the shared environment classification reaches the operator
channel. Existing unresolved tests protect error behavior. The full repository guardrail and hook
suites cover schema serialization, documentation coupling, and supported Python versions.

## Security model

This change touches authorization observability but neither widens nor weakens authorization. The
local process environment is operator-controlled input crossing into configuration; existing
pydantic validation parses the boolean and existing config precedence selects its value. The new
probe reads key names only and does not expose values. MCP clients and the trusted startup audit
sink receive the same fields they already receive, with one alternative removed. Credential
handling, access-policy disclosure, and the underlying power-operation guard are out of scope and
unchanged.
