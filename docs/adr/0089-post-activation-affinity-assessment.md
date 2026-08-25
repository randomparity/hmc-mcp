# ADR 0089: Post-activation affinity assessment is explicit and terminal

## Status

Accepted

## Context

Provisioning may submit a PowerOn job, but job submission alone does not prove that the partition
activated or that a subsequently observed affinity score was achieved. ADR 0088 defines the stable
evidence-first assessment contract, while ADR 0087 makes minimum-affinity actions explicit.

## Decision

Add an optional provisioning assessment request containing captured score and policy evidence bound
to the requested managed-system and LPAR identities, freshness and caller thresholds required by ADR
0088, an explicit `warn` or `fail` response, and bounded power-job wait settings. Identity mismatch
is rejected before HMC traffic; anonymous captured evidence is not accepted. When requested,
provisioning waits for PowerOn to reach a successful terminal state, then reads the current and
calculated LPAR scores and current policy and passes those facts to the ADR 0088 assessment contract.

An explicitly applied `minimum_affinity_policy` is the current configured policy for assessment. The
captured policy remains historical evidence: an equal configured value is comparable, a different
configured value is deliberately returned by ADR 0088 as contradictory `unsupported-data`, and an
absent or unsupported captured state retains that state. Provisioning never rewrites historical
policy evidence to make the new policy appear unchanged.

Record `power_on` and `affinity_assessment` as separate stable steps. The assessment result labels its
current score as achieved and retains the calculated score as a non-guaranteed prediction. An
unsupported or adverse verdict is a warning under `warn`; under `fail` it makes
`workflow_completed=false` without undoing the already-created, activated partition. Power-off,
dry-run, failed activation, and activation timeout never claim an achieved score and never perform
the assessment.

## Consequences

Default provisioning remains asynchronous and unchanged. Opt-in assessment may block for the chosen
timeout and may report a partial failure after successful creation and activation. No rollback is
attempted, and every returned score remains traceable to whether it was observed or calculated.

## Considered & rejected

- **Assess immediately after submitting PowerOn.** verified: `power_lpar(..., wait=False)` returns
  the submitted job without observing a terminal status, so it cannot establish activation.
- **Always assess powered-on provisions.** judgment: extra SSH traffic and policy enforcement would
  change the established default workflow.
- **Infer warning versus failure from configured policy.** verified: issue #318 requires explicit
  caller-selected warning or fail-closed behavior; ADR 0087 likewise forbids implicit `fail`.
- **Roll back on a failed assessment.** judgment: deleting a running, newly provisioned partition is
  disproportionate and would obscure the truthful partial outcome.
