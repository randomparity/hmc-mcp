# ADR 0091: Compose explicit affinity preflight before LPM

## Status

Accepted

## Context

ADR 0018 requires canonical HMC validation before migration submission. The accepted affinity
policy and assessment contracts can also identify an adverse or unavailable destination estimate,
but `JobOutcome` cannot gain fields only for calls that request affinity checks.

## Decision

Add a separate affinity-aware migration operation with its own stable result. Its request carries
the source/current score, destination estimate, the estimate or check basis, configured minimum,
capability state, and an explicit `warn` or `fail` response. Its result always pairs an affinity
preflight outcome with the migration `JobOutcome`, or `null` when fail-closed preflight prevents
submission.

The preflight runs before ADR 0018 validation and migration. Supported, complete evidence passes
when the destination estimate meets the configured minimum. An estimate below the minimum warns or
stops according to the explicit response. Missing evidence, unavailable capability, malformed
values, and preflight timeout are unavailable results: `warn` proceeds and `fail` stops. A stop
does not submit either HMC validation or migration. The existing migration operation and its
`JobOutcome` return remain unchanged.

## Consequences

Default callers retain their traffic, result, and validation-first behavior. Affinity-aware callers
receive a deterministic companion that distinguishes evidence, policy, capability, and the exact
proceed decision. The destination score remains an estimate, never a migration guarantee.

## Considered & rejected

- **Conditionally extend `JobOutcome`.** judgment: an input-dependent result shape makes every
  existing caller branch on a field unrelated to job execution.
- **Always wrap existing migration results.** judgment: changing the established public return
  contract would violate the requirement to preserve default behavior.
- **Infer fail-closed from the configured policy action.** verified: issues #316 and #320 require an
  explicit caller decision; configured policy is evidence, not authorization to stop a migration.

