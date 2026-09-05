# Core library facade design

## Scope and authority

The user approved a pre-release redesign of the reusable Python facade on
2026-09-05, selecting a core-only contract. The change replaces the omnibus
`hmc_mcp.api` facade with exactly `HMCClient`, `HMCConfig`, `ConfigError`,
`HMCError`, `HMCTransportError`, and `TLSVerificationDisabledWarning`.

It covers the facade module, ADR 0029's replacement, facade-contract tests,
the Unreleased manifest, and direct repository consumers. MCP tools, CLI
commands, protocol client methods, operation implementations, and their
domain-module imports remain behaviorally unchanged. No compatibility aliases
are retained in `hmc_mcp.api`.

## Decision

`hmc_mcp.api` is the stable connection boundary, not an operation catalog. Its
six exports let a consumer configure and construct the client and catch common
connection failures. `HMCClient` retains only its existing lifecycle member
allowlist as supported; inherited REST methods remain callable Python
attributes but are not part of the compatibility contract.

Every operation, operation-specific result, enum, literal alias, and error
leaves the facade. A consumer that chooses to call a domain operation imports
that operation and its result types from the owning module directly, accepting
that those module paths are pre-release implementation interfaces. MCP and CLI
continue to import operations from their existing modules, not through the
facade.

## Migration and failure contract

This is an intentional pre-release removal. Importing a removed name from
`hmc_mcp.api` raises Python's normal `ImportError`; no deprecated aliases or
dynamic attribute fallback hide that break. Existing callers must import a
specific operation and any operation-owned type from its defining module.

The retained client/configuration/error behavior is unchanged. This redesign
does not alter HMC requests, authorization, validation, returned data, tool
schemas, CLI output, or error translation. It only narrows which imports are
promised stable.

## Implementation and verification

1. Add ADR 0118 and supersede ADR 0029, recording the exact six-name manifest
   and the removal of operation-selection and transitive-type rules.
2. Remove non-core imports and exports from `api.py`; preserve the existing
   module docstring's common HMC error contract in terms of the retained types.
3. Replace the facade inventory, signature, lifecycle, and type-closure tests
   with exact six-name contract tests and a regression assertion that an
   operation is not bound on the facade. Update the Unreleased facade manifest.
4. Run `just lint`, `just typecheck`, `just test`, `just smoke`, generated-doc
   checks, and `just verify`; resolve the active desloppify task only after the
   full suite is green.

## Acceptance criteria

- `hmc_mcp.api.__all__` contains exactly the six approved names.
- Every removed facade operation and domain model is absent from `api.py`; the
  server and CLI still import and expose their unchanged operations.
- The public API test proves the exact manifest, retained lifecycle allowlist,
  and absence of an operation export.
- ADR 0118, the supersession marker in ADR 0029, and CHANGELOG.md describe the
  intentional pre-release breaking change.
