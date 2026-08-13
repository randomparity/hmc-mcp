# ADR 0011: Multi-Agent LPAR Ownership — Advisory Protocol

## Status

Accepted

## Context

Multiple AI agents can share one hmc-mcp server (or point separate servers at
the same HMC). Nothing today prevents agent Bob from deleting or modifying an
LPAR that agent Alice created. Identity is process-global: one `HMCConfig`
instance, one `audit_memento` (`"hmc-mcp"`). The description field is the only
per-LPAR metadata writable over SSH with no external service dependency.

Issue #132 proposes four enforcement levels. The operator chose **Phase 0 + 1**
for the initial deployment: local, single-process, stdio transport — a setting
where process-level isolation is not meaningful and hard HMC-user enforcement
(Phase D) would require role-maintenance tooling that does not yet exist.

## Decision

### Phase 0 — per-agent attribution

Add an optional `HMC_AGENT_ID` environment variable to `HMCConfig`. When set,
the config property `effective_audit_memento` returns `hmc-mcp/<agent_id>`;
when unset it returns the existing default `"hmc-mcp"`. `effective_audit_memento`
replaces the direct `audit_memento` field as the value sent in `X-Audit-Memento`,
so every REST mutation carries per-agent attribution without changing the rest
of the stack.

### Phase 1 — ownership token (advisory)

The three LPAR create paths — `hmc_create_lpar`, `hmc_provision_lpar`,
`hmc_deploy_partition_template` — stamp the LPAR description field after
successful creation using a best-effort SSH call. For
`hmc_deploy_partition_template`, which submits an async job, stamping is only
attempted when `wait=True`; `wait=False` (the default) produces an unstamped
LPAR, since the job outcome is unknown at return time. Operators who require the
ownership token should pass `wait=True`. The token format is:

```
[hmc-mcp owner:<agent_id> created:<YYYY-MM-DD>]
```

where `<agent_id>` is `HMC_AGENT_ID` if set, or `"hmc-mcp"` otherwise.

Stamping is **best-effort**: a failure to set the description (SSH error, HMC
rejected it) is logged as a warning in the tool result but does not fail the
create step itself, since the LPAR already exists.

Destructive and rename-capable tools (`hmc_delete_lpar`, `hmc_modify_lpar`,
`hmc_set_lpar_description`) receive advisory docstring language instructing the
agent to check the description first, and to stop and ask the operator if the
token's owner differs from the current agent.

The server `FastMCP` instructions include a summary of the multi-agent ownership
protocol so agents aware of the instructions block understand the convention
before they invoke any tool.

### What this is and is not

This is a cooperative **discouragement** protocol: it relies on the agent
following instructions, not on code enforcement. An agent that ignores the
advisory can still delete another agent's LPAR. This is the right trade for a
local, single-operator, stdio deployment. Phases B and D (middleware enforcement,
per-HMC-user isolation) are explicit future work and require a separate ADR.

## Consequences

- Every LPAR created through the three create paths carries a machine-readable
  ownership token visible in the HMC GUI Partitions tab and readable via
  `hmc_get_lpar_description`.
  **Known gap (issue #135):** `hmc_deploy_partition_template` currently stubs
  ownership stamping with `ownership_stamped=None` for all outcomes because the
  deploy job does not reliably return the new LPAR name across HMC firmware
  versions.  Full implementation (diff the LPAR list before/after the job) is
  tracked in issue #135.
- Attribution in the HMC REST audit log improves: `X-Audit-Memento` is now
  `hmc-mcp/<agent_id>` rather than the generic `hmc-mcp` when `HMC_AGENT_ID` is
  set.
- Stamping adds one SSH round trip after create. This is a best-effort call with
  no impact on the create result.
- LPARs created before this change carry no ownership token; the advisory docstring
  treats an absent token as "no claim" (proceed with caution rather than block).
- `HMC_AGENT_ID` is intentionally not surfaced in the `hmc_list_configured_hosts`
  output — it is a runtime identity, not a connection profile field.

## Considered & rejected

**Embed the owner in the LPAR name (namespace: `alice-lpar1`).**
Enforced by regex; visible without an SSH call. Rejected: modifying the name
policy breaks existing LPARs and clashes with operator naming conventions.
The description field is the only writable metadata that does not affect addressing.

**Use a local SQLite/JSON ownership registry (Phase C).**
Rejected per issue #132: drifts from reality when a second server instance runs
or LPARs are created out-of-band. Only viable as a cache, not as source of truth.
Adds a persistence dependency for no durable guarantee.

**Hard enforcement via middleware + `hmc_run_command` gating (Phase B).**
Out of scope for this PR (operator decision). A future ADR will cover it once
the advisory layer has demonstrated the protocol's utility.

**Advisory docstrings only, no description stamping.**
Rejected: without a machine-readable token in the description, agents cannot
programmatically detect foreign ownership; free-text advisory language alone is
fragile and relies entirely on an agent reading and interpreting arbitrary prose.
The stamp is the protocol's one verifiable artifact.

**Skip stamping when the LPAR was created via the CLI fallback path.**
Rejected: the CLI fallback path (HTTP 406 → mksyscfg) also creates an LPAR;
stamping should happen regardless of which path succeeded. The stamp call uses
the same system-name resolution already done for the create, so the extra work
is minimal.
