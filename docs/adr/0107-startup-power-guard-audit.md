# ADR 0107: Audit effective power guards at serve startup

## Status

Accepted

## Context

`hmc_effective_permissions` reports the effective, post-precedence
`authorize_power_operations` value per reachable connection. An access policy may withhold
that inspection tool, leaving no in-process evidence that the fail-open guard took effect.
Serve startup already owns operator diagnostics and audit-sink installation.

## Decision

After installing the audit sink, `serve` resolves the same per-connection guard set used by
`hmc_effective_permissions` and submits one `power-ownership-guard` audit record for every row.
The event is unconditional and at `WARNING`: both boolean values, their source, and an
unresolved result remain visible at the shipped audit threshold. Resolution stays total, so
unreadable configuration produces an `unresolved` record rather than preventing startup.

The record contains only `connection`, `authorize_power_operations`, `source`, and `detail`.
It does not add an MCP-visible channel or expose configuration paths or credentials.

## Consequences

Every serve invocation submits operator-visible, structured evidence for each connection the
policy can route, even when the inspection tool is absent. Delivery remains best-effort under
the audit sink's documented non-persistence and drop semantics. Startup performs one bounded
config document read and may emit multiple warning-level records. The audit event vocabulary
grows additively.

## Considered & rejected

- **Add another MCP inspection channel.** judgment: it would defeat the policy's deliberate
  ability to withhold the existing inspection tool and expand the disclosure surface.
- **Write only a stderr warning when the guard is false.** judgment: free-form diagnostics are
  less durable and omit evidence that a true value took effect.
- **Emit only fail-open values.** judgment: absence would remain ambiguous between an enforced
  guard and a missing startup observation.
- **Resolve only the default connection.** verified: `resolve_power_guards` documents and
  implements policy-reachable named profiles at commit `05ebdc7d`; a default-only record would
  disagree with the server's dispatch surface.
