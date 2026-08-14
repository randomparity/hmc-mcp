# 0017 — Stable outcomes for wait-capable LPM recovery operations

## Status

Accepted (2026-08-14)

## Context

LPM abort, recover, and remote-restart submit HMC jobs but cannot currently wait.
Adding a wait flag creates a result-contract choice: return the submission entry
immediately and a different normalized outcome after waiting, or keep one shape
for both modes. ADR 0012 requires each public MCP tool to have one stable result
shape. ADR 0012 does not define how an asynchronous submission represents the
fact that no wait was requested.

## Decision

`hmc_migrate_abort_lpar`, `hmc_migrate_recover_lpar`, and
`hmc_remote_restart_lpar` always return the `JobOutcome` shape introduced for
`hmc_wait_for_job`: `job_id`, `status`, `timed_out`, `error`, and `job`.

With `wait=False`, the outcome normalizes the submitted job entry and sets
`timed_out=False`, because no timeout occurred. With `wait=True`, the operation
polls using the submission identifier and SELF link, then normalizes the last
observed entry; a non-terminal last entry or no entry means `timed_out=True`.
The raw submission or last polled entry remains available in `job`.

Timing is validated before selector resolution or remote submission. Only these
three operations adopt this result contract; migrate and migrate-validate retain
their existing raw-job contract.

## Consequences

Callers receive identical keys and field types whether or not they wait, and can
distinguish submission, terminal success, terminal failure, and timeout without
inspecting HMC-specific XML projections. Existing callers of these three tools
must read the raw entry from `job`. CLI output mirrors the same normalized
object. An HMC submission lacking an identifier remains representable when the
caller does not wait, but requesting a wait fails before polling as today.

## Considered & rejected

**Return a raw submission entry for `wait=False` and `JobOutcome` for
`wait=True`.** This makes the public result shape depend on an input flag and
violates ADR 0012.

**Keep raw HMC entries in both modes.** This adds waiting but leaves terminal
failure and timeout dependent on unstable HMC payload details, defeating the
stable outcome contract established by issue #141.

**Change every LPM job tool at once.** Migrate and migrate-validate are outside
issue #150 and changing them would broaden the public migration unnecessarily.

**Do nothing.** Abort, recovery, and remote restart would remain unable to
provide a bounded terminal result, preserving the arbitrary asymmetry.
