# 0018 — Validation-first LPM submission

## Status

Accepted (2026-08-14)

> **Partially superseded by [0081](0081-classify-actionable-job-outcomes.md)** (2026-08-23)

## Context

The server exposes migration validation and migration submission separately. Callers can
therefore bypass the HMC's canonical validation step, and `wait=False` makes a naïve
validate-then-migrate sequence unsafe because validation has not reached a terminal result.
ADR 0012 requires one stable public result shape.

## Decision

`hmc_migrate_lpar` gains `validate_first: bool = True`. When enabled, it submits validation
and always waits for its terminal outcome before migration, independently of whether the caller
wants to wait for the later migration. Only `COMPLETED`, `COMPLETED_OK`, and
`COMPLETED_WITH_WARNINGS` permit migration. Failure, exception, cancellation, timeout, or a
non-terminal outcome raises `HMCError` containing the normalized validation status/error and no
migration is submitted. `validate_first=False` submits migration directly.

Migration and standalone validation return `JobOutcome` on every successful call. The caller's
`wait` flag controls only whether the final requested job is polled; the validation gate is always
terminal. Existing shared job normalization and wait validation remain authoritative.

## Consequences

The safe sequence becomes the default without adding a tool. Default calls make an additional
HMC request and may block up to `timeout_seconds` before migration submission. Validation failures
are exceptions rather than a second result variant. Explicit opt-out preserves direct submission.

## Considered & rejected

**Return validation failures in a new envelope.** This creates an input/outcome-dependent public
union and conflicts with ADR 0012.

**Respect `wait=False` for validation.** Submitting migration before validation reaches a terminal
state defeats the safety guarantee.

**Keep validation caller-managed.** The existing defect is precisely that the safe sequence is
only convention.
