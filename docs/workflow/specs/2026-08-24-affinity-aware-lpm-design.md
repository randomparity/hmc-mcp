# Affinity-aware LPM preflight design

Issue: #320  
Decision: [ADR 0091](../../../adr/0091-compose-affinity-preflight-before-lpm.md)

## Outcome

Expose an opt-in affinity-aware LPM operation that evaluates explicit source and destination
evidence before the canonical HMC validation-first sequence, without changing `JobOutcome` or the
existing migration operation.

## Contract

`LpmAffinityPreflightRequest` names the source/current score, destination estimated score,
destination check basis, configured minimum, platform capability, response (`warn` or `fail`), and
a bounded preflight timeout. `LpmAffinityPreflightOutcome` always reports status, reason, every
input fact, and `proceed`. `LpmAffinityMigrationResult` always contains that outcome and a nullable
job.

The separate MCP and CLI surfaces use this contract. The existing surfaces remain unchanged.

## Data flow and errors

Caller-controlled values are validated before HMC traffic. Preflight evaluation then runs under
its timeout. Complete supported evidence at or above the threshold passes. Evidence below the
threshold is adverse. Unsupported capability, absent evidence, or timeout is unavailable. Warning
intent proceeds for adverse and unavailable outcomes; fail intent stops for both. Only `proceed`
calls the existing `migrate_lpar`, which retains ADR 0018 validation and submission ordering.

Malformed scores, thresholds, response values, bases, capabilities, and timeouts raise actionable
`ValueError` before HMC traffic. Runtime preflight failures are represented as unavailable so the
explicit response controls the exact stop decision.

## Tests

Contract tests prove stable fields and public exports. Operation and tool tests cover passing,
warning, failure, timeout, unsupported capability, malformed input, and exact HMC call counts.
Regression tests prove the legacy operation still returns only `JobOutcome` and keeps its default
validation-first behavior.

