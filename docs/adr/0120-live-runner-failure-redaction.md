# ADR 0120: Redact live-runner failures at ingestion

## Status

Accepted (2026-09-05)

## Context

`scripts/live_test_runner.py` prints and persists exception messages and
tracebacks received from a live HMC run. Those strings can carry credential
values, URL userinfo, hostnames, or local paths. Its bootstrap message also
prints the selected config path and HMC host.

## Decision

Sanitize failure text once when `RunState.call()` catches an exception, before
the text can be recorded, printed, or serialized. Replace secret values, URL
userinfo, hostnames, and local paths with explicit redaction tokens. Keep error
types and non-sensitive message text readable. Do not print configuration path
or host during bootstrap. Successful tool results are outside this change.

## Consequences

Failure diagnostics contain less environment-specific detail, but terminal and
JSON output have the same redaction guarantee. New sensitive-value patterns
belong in the single sanitizer with a direct regression test.

## Considered & rejected

- **Redact separately while printing and serializing.** verified: `RunState.record()`
  appends to `results` and prints the same `data` value, as shown by
  `sed -n '411,470p' scripts/live_test_runner.py` on 2026-09-05; two sites
  could diverge.
- **Leave failures verbatim for diagnosis.** judgment: live-run output is
  routinely persisted and can expose identifiers or credentials outside the
  immediate terminal session.
