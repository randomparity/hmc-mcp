# Startup power-guard audit design

## Scope

Issue #533 requires a serve process to surface the effective
`authorize_power_operations` value per connection when `hmc_effective_permissions` is
withheld. This design uses the operator-only audit stream and the invocation-scoped resolver
merged by #536. It does not add an MCP tool, migration, or alternate resolution path.

## Decision and data flow

[ADR 0107](../../adr/0107-startup-power-guard-audit.md) selects unconditional warning-level
audit records. `_serve_application` installs logging, calls `resolve_power_guards(policy)`
once, and passes every `PowerOwnershipGuard` to a record builder in `audit.records`.
Each JSON record has `time`, `event: "power-ownership-guard"`, `connection`,
`authorize_power_operations`, `source`, and `detail`, preserving the resolver's closed and
bounded disclosure vocabulary.

Resolution errors remain values: `authorize_power_operations` is null, `source` is
`unresolved`, and `detail` is the resolver's exception class label. Audit submission cannot
abort startup because the existing sink's `emit` boundary contains writer failures; delivery
is best-effort and subject to the audit stream's documented drops.

## Security and privacy

The existing trust boundary is operator-authored environment/TOML to an operator-owned audit
stream. No boundary is added or widened toward MCP clients. The resolver already removes
paths, values, and validation messages from returned rows; the record copies only that closed
shape. Configuration content, credentials, hostnames, and exception text are forbidden.
Audit sink failure continues to drop diagnostics without affecting startup. Authentication,
policy enforcement, and the policy's ability to withhold tools are out of scope and unchanged.

## Acceptance tests

- A unit test pins the event vocabulary, exact field order, warning level, booleans, null, and
  bounded string rendering.
- An app test starts the serve bootstrap with the inspection tool withheld and proves one
  record per policy-reachable connection, including differing profile values.
- A failure-path test proves unreadable configuration submits `unresolved` and startup returns
  an application.
- Documentation defines the event and replaces the obsolete “no second in-process channel”
  statement with an audit-stream pointer.

## Global constraints

Python versions and architectures remain those declared by the repository and CI. Add no
dependency, migration, public API export, MCP surface, or configuration key. Run `just verify`
and `UV_NO_SYNC=1 uv run --no-sync prek run --all-files`.
