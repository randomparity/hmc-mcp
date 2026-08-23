# 0081 — Classify actionable terminal job outcomes

## Status

Accepted (2026-08-23)

## Context

`JobOutcome` is a stable public shape with `status`, `timed_out`, and `error` fields. The job
status sets currently allow canceled jobs to be terminal without being successful or failed, so
they produce `timed_out=False` and `error=None`. Fleet health filters on the failure set and drops
the same jobs. `COMPLETED_WITH_WARNINGS` likewise describes an outcome requiring operator action
but is currently classified as success.

The repository has long accepted `COMPLETED`, `EXCEPTION`, and `FAILED` alongside the documented
HMC vocabulary, and existing callers and tests depend on those compatibility statuses.

## Decision

Every terminal status belongs to exactly one of two sets: successful or actionable. Only
`COMPLETED` and `COMPLETED_OK` are successful. Cancellation, warnings, errors, and failures are
actionable and use the existing failed-status set and fleet-health `failed_jobs` bucket.

An actionable `JobOutcome` always has a non-empty `error`. HMC-provided error detail remains
preferred; when none exists, normalization supplies `Job ended with status <STATUS>`. The public
shape and the fleet-health result shape do not change. Retryability remains encoded only in the
documented status value; no new field is introduced.

## Consequences

Canceled and warning outcomes cannot look clean or disappear from fleet health. Existing callers
that treat membership in `SUCCESSFUL_JOB_STATUSES` as permission to continue now stop on warnings.
The compatibility statuses remain accepted, while an equality assertion between terminal,
successful, and actionable sets guards the classification against future drift.

## Considered & rejected

- **Add needs-attention and retryable fields.** judgment: this changes the stable public result
  contract when the existing status and error fields can represent the required distinction.
- **Add a separate fleet-health warning bucket.** judgment: it changes the stable fleet-health
  shape and forces consumers to merge two exception streams.
- **Keep warnings successful.** verified: issue #408 cites the HMC documentation describing
  `COMPLETED_WITH_WARNINGS` as partial success requiring manual intervention, so clean success is
  misleading.
- **Drop undocumented compatibility statuses.** verified: `git log -- src/hmc_mcp/jobs.py` and
  ADR 0018 show `COMPLETED`, `EXCEPTION`, and `FAILED` are established accepted inputs and
  contract dependencies in this repository.

