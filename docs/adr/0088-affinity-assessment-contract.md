# ADR 0088: Affinity assessment is an evidence-first read-only contract

## Status

Accepted

## Context

ADR 0083 separates observed affinity scores from potential predictions and records IBM's warning
that a score of 100 may be unattainable. Portable snapshots now preserve captured scores and a
POWER11 configured minimum when supported. Operators need one stable assessment that compares
those facts without turning a generic score into an invented universal policy.

## Decision

Add one pure assessment contract whose inputs are captured, current, predicted, and optional
configured-minimum scores plus explicit caller thresholds and freshness evidence. The result is a
frozen, presentation-neutral value containing exactly one classification, normalized evidence,
an explanation, and non-mutating recommended actions.

Classification precedence is `unsupported-data`, `policy-violation`, `regression`,
`optimization-opportunity`, then `none`. Unsupported, missing, stale, or contradictory evidence is
returned as `unsupported-data`, never silently ignored. A configured minimum governs policy
violations. Without that policy, callers must provide both a maximum acceptable regression and a
minimum worthwhile optimization gain; the package defines no universal values. Predictions remain
potential outcomes and never become guarantees or policy.

Expose the same contract through the Python facade, MCP, and CLI. Snapshot assessment reads the
captured score and capture time from the validated version-1 snapshot, while current and predicted
scores and policy are explicit inputs. The operation performs no HMC mutation or network I/O.

## Consequences

Every verdict is reproducible from returned evidence. Callers can tune thresholds to their own
workload and policy. Stale or internally inconsistent evidence yields an actionable unsupported
result rather than a false recommendation. The contract does not promise that a predicted score,
including 100, can be achieved.

## Considered & rejected

- **Use fixed score thresholds.** judgment: one threshold cannot represent every workload or
  operator policy and would contradict the requested caller-owned policy boundary.
- **Treat predicted score as desired state.** verified: ADR 0083 and IBM's `lsmemopt` guidance
  describe calculated scores as potential, not guaranteed, and warn that 100 may be unattainable.
- **Apply optimization automatically.** verified: issue #317 requires recommendations without
  mutations, and ADR 0083 excludes `optmem` from the affinity planning contract.
- **Return several simultaneous classifications.** judgment: one precedence-ordered verdict is
  easier to automate while the evidence and explanation retain every comparison.
