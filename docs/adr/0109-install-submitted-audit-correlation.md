# ADR 0109: Correlate a successful install submission with its remote PID

## Status

Accepted (2026-08-29)

## Context

ADR 0102 records `install-attempted` before detached `installios` submission because a
raised SSH call cannot prove that nothing started. That record cannot contain the PID,
which exists only after a successful submit. In served deployments the returned
`InstallHandle` reaches the MCP caller while the operator sees only the configured audit
stream, leaving the operator without the handle needed to abort the remote process.

## Decision

After `run_installios` returns a PID, emit a warning-level `install-submitted` record on
the reserved audit logger. It repeats `system`, `partition`, `log_path`, `host`, and
`attribution` from `install-attempted`, and adds integer `pid`. Operators correlate the
pair on those repeated fields and stream order. Concurrent submissions with identical
fields are deliberately not assigned a new attempt identifier; each successful outcome
still carries the PID needed to act, and the stream does not promise one-to-one pairing.

If submission raises, emit no `install-submitted` record. The preceding
`install-attempted` remains the truthful ambiguous-failure signal. Audit emission keeps
the existing best-effort semantics: a sink failure never changes the install result.

## Consequences

- An operator using only the served audit stream can obtain the remote PID after success.
- Success produces two bounded records; a raised submit produces only the attempted record.
- Absence of `install-submitted` does not prove nothing started, and absence of either
  record does not prove no submission occurred because the audit sink may drop records.
- The public `InstallHandle` and `hmc_mcp.api.__all__` do not change; the audit `Event`
  literal and its documented vocabulary do.

## Considered & rejected

- **Add an `attempt_id` to both records.** judgment: it expands both record shapes and
  introduces identifier generation solely to pair outcomes that already repeat all
  operator-relevant fields; the PID-bearing outcome is independently actionable.
- **Route the existing module log to the audit sink.** verified: ADR 0043 reserves a
  structured one-JSON-record-per-line contract, while the existing line is free text and
  the generic namespace-routing work belongs to #534.
- **Document HMC-side PID recovery only.** judgment: it leaves the served operator without
  the only prompt abort handle despite the PID already being available at the emit point.
