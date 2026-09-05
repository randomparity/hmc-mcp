# ADR 0110: Classify case-insensitive power guard variables as environment

## Status

Accepted

## Context

`power_ownership_guards` is returned by `hmc_effective_permissions` and emitted in the
`power-ownership-guard` startup audit event. Its `source` vocabulary currently includes
`ambiguous` for a case variant of `HMC_AUTHORIZE_POWER_OPERATIONS`. That label recorded an old
loader divergence: variants won in environment-only construction but could lose to a TOML profile.
The profile loader now removes the TOML value whenever any case-insensitive spelling is present, so
the environment wins on both resolution paths. Keeping `ambiguous` now contradicts the effective
value and gives operators a stale detail sentence.

## Decision

Detect `HMC_AUTHORIZE_POWER_OPERATIONS` through the existing `config.env_var_value` helper, whose
`str.lower()` and last-match semantics mirror pydantic-settings. When any matching spelling exists,
report `source: environment` and `detail: null`. Retain `profile`, `default`, and
`unresolved` with their existing meanings. Multiple matching spellings remain environment-sourced;
the effective boolean continues to come from the existing pydantic-settings resolution rather than
from the reporting probe.

The same `PowerOwnershipGuard` value feeds the MCP report and startup audit event, so both channels
use this vocabulary without a second conversion layer.

Configuration construction and source classification assume the process environment is stable for
the duration of one report resolution. They remain separate reads, matching the existing resolver;
this change does not add environment snapshot plumbing.

## Consequences

The public report and audit schema remove the `ambiguous` literal alternative and its explanatory
detail. A case variant is no longer treated as a spelling warning; it is a supported
case-insensitive environment name. Consumers must accept the narrower source vocabulary. No
configuration precedence, authorization decision, persisted data, or migration changes.
Concurrent in-process mutation of `os.environ` between config construction and source
classification can still make the reported source disagree with the effective value. Normal
process launch environments are stable, tests that mutate them are serialized, and closing that
pre-existing race would require a broader resolver snapshot design.

## Considered & rejected

- **Keep `ambiguous` as a spelling warning.** verified: issue #547 and
  `config.env_var_value`, which `_load_profile_from_document` uses to suppress the profile key,
  show that the same case-insensitive environment match governs both construction paths.
- **Add a separate spelling-warning field.** judgment: no acceptance criterion asks for spelling
  diagnostics, and case-insensitive matching makes a variant supported rather than malformed.
- **Return no source for a case variant.** verified: `config.env_var_value` both detects the
  environment value and controls profile-key filtering, so null attribution would discard known
  provenance without resolving a remaining uncertainty.
- **Choose a value directly from matching environment keys in the reporter.** judgment: that would
  duplicate pydantic-settings precedence and risk reporting a value different from the effective
  `HMCConfig`; the reporter needs only classify the already-resolved value's source.
