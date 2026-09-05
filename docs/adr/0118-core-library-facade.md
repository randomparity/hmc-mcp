# ADR 0118: Core library facade

## Status

Accepted (2026-09-05)

## Context

ADR 0029 made every presentation-neutral operation and transitive operation
type a supported `hmc_mcp.api` export. The resulting 271-name facade duplicates
the domain-module namespace, makes incidental implementation types compatibility
promises, and provides no small library entry point.

## Decision

`hmc_mcp.api` has exactly six supported names: `HMCClient`, `HMCConfig`,
`ConfigError`, `HMCError`, `HMCTransportError`, and
`TLSVerificationDisabledWarning`. It is the stable connection, configuration,
and common-error boundary. `HMCClient` retains the lifecycle allowlist from ADR
0029; inherited protocol methods are callable but unsupported.

Operations and operation-owned models are no longer facade exports. Callers
that deliberately use a domain module import from that module and accept its
pre-release implementation status. The CLI and MCP layers remain separate and
do not use the facade as an operation registry. This supersedes ADR 0029.

## Consequences

The reusable facade is small, explicit, and cheap to maintain. Existing facade
operation imports fail and must move to their owning modules. The package makes
no compatibility shim because this is an authorized pre-release replacement.
Existing HMC request, tool, CLI, and domain-operation behavior is unchanged.

## Considered & rejected

- **Retain every operation and remove only result types.** verified:
  `wc -l src/hmc_mcp/api.py` reported 616 lines on 2026-09-05, and its
  `__all__` still listed operations across nearly every subsystem; that leaves
  the omnibus catalog intact.
- **Keep deprecated facade aliases.** judgment: a second import path obscures
  the new boundary and postpones the same compatibility burden.
- **Remove the facade entirely.** judgment: one stable client/configuration and
  common-error import remains useful without promising domain operations.
