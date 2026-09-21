# ADR 0123: Pre-release client boundaries

## Status

Accepted (2026-09-06)

## Context

Several internal `HMCClient` mixin methods accept optional controls
positionally, allowing adjacent values to be silently misbound. Some REST CLI
commands duplicate the runtime client lifecycle even though `with_client`
already owns it. The snapshots package also directs consumers to a facade that
does not export snapshot contracts.

## Decision

Make optional controls keyword-only after each method's required resource
identity parameters. Route the named single-operation REST CLI delegates
through `with_client`, leaving validation, confirmation, and output at the
command boundary. Describe snapshots' owning pre-release modules accurately and
retain ADR 0118's six-name facade boundary. Do not add compatibility wrappers.

## Consequences

Internal callers must name optional client controls, making their intent and
future evolution explicit. CLI lifecycle behavior has one owner. Consumers
receive accurate guidance: snapshot imports are pre-release implementation
paths, while `hmc_mcp.api` remains the stable connection/configuration/error
facade. Existing positional callers break immediately, as permitted for this
pre-release internal surface.

## Considered & rejected

- **Keep optional controls positional.** verified: `rg -n 'async def (add_network_adapter|create_logical_unit|lpar_migrate|lpar_migrate_validate)' src/hmc_mcp/client` showed all four methods expose optional parameters after their resource identities on 2026-09-06.
- **Add positional compatibility wrappers.** judgment: wrappers retain the ambiguous call form and conflict with the approved pre-release policy to remove debt rather than preserve it.
- **Keep command-local client lifecycle closures.** verified: `src/hmc_mcp/cli_commands/runtime.py` already provides `with_client`, which enters `client()` and routes exceptions through `run_cli_coroutine`.
