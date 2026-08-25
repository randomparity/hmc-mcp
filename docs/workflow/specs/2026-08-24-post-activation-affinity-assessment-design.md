# Post-activation affinity assessment design

Issue: #318  
Decision: [ADR 0089](../../adr/0089-post-activation-affinity-assessment.md)

## Outcome

Provisioning can optionally wait for successful activation and assess the achieved LPAR affinity
through ADR 0088. Omission preserves the current workflow exactly.

## Contract

`ProvisionAffinityAssessment` supplies captured score and policy evidence, captured managed-system
and LPAR identities, capture time, staleness limit, optional caller thresholds, explicit `warn` or
`fail` behavior, and bounded PowerOn polling settings. The identities must equal the provisioning
request's system and LPAR selectors; mismatch fails before HMC traffic. It does not accept current or
predicted scores: provisioning obtains those only after the PowerOn job reports `COMPLETED` or
`COMPLETED_OK`.

The opt-in path records an `affinity_assessment` step after `power_on`. Its successful result contains
the stable `AffinityAssessmentResult`, plus explicit `achieved_score` and `predicted_score` fields;
the prediction is never described as achieved. Adverse classifications (`regression`,
`optimization-opportunity`, `policy-violation`, and `unsupported-data`) append a stable warning in
warning mode. Fail mode records the assessment step as `error` and returns
`workflow_completed=false`; creation and activation remain truthfully successful.

PowerOn is polled only for this opt-in path. A failed terminal job or timeout records `power_on` as
an error and skips assessment. With `power_on=false`, assessment is skipped and the workflow is not
complete. Dry-run emits dry-run records without power or affinity calls. Capability absence becomes
ADR 0088 `unsupported-data`, then follows the selected warning/fail behavior.

When provisioning also applies `minimum_affinity_policy`, that value is passed as the current
configured minimum. Captured policy evidence is preserved unchanged. Equal configured minima permit
normal assessment; changed configured minima become ADR 0088 contradictory `unsupported-data`;
captured absent or unsupported states remain explicit. Without an applied policy, provisioning reads
the current policy and uses its capability and value as the current policy input.

## Components and data flow

`operations_provision.py` validates the request before HMC traffic, waits for PowerOn, normalizes its
terminal outcome, reads current/calculated affinity and current minimum policy through existing
presentation-neutral SSH operations, calls `assess_affinity`, and records stable steps/warnings.
`server_provision.py` exposes the optional request without adding another tool. Public exports expose
the request type beside existing provisioning types.

## Failure handling

PowerOn terminal failure or timeout and expected failures from the new current-score,
calculated-score, and policy reads are structured partial results. Programming errors still
propagate. No assessment error rewrites already successful steps or attempts rollback. No path
derives achieved evidence from a submission response or calculated score.

## Acceptance tests

- Omission preserves the existing step order and asynchronous PowerOn behavior.
- Successful opt-in activation records separate successful power and assessment steps.
- Power-off and dry-run/no-power-on paths never query affinity.
- Timeout and failed terminal activation skip assessment and report incomplete workflow.
- Unsupported evidence follows explicit warning or fail behavior.
- Captured evidence for another target fails before any HMC request.
- Newly applied equal, changed, absent, and unsupported captured-policy combinations preserve ADR
  0088 reconciliation behavior.
- Warning mode completes with a stable warning; fail mode reports truthful partial failure.
- Public signatures and dataclass schemas expose the new optional contract.
