# Coordinated MCP framework pins

Issue #753; scope token `q753-83d5ead2`.

## Problem and authority

FastMCP 3.4.7 constrains MCP below 2.0. Issue #753 requires the server and
client extras, SDK and standalone types to advance together, after #752.
The [scope charter](https://github.com/randomparity/hmc-mcp/issues/753#issuecomment-5613555924)
records the operator-approved exclusions and completion criteria.

## Design

Set both `fastmcp-slim` extras to 4.0.3, `mcp` to 2.2.0, and add the exact
`mcp-types==2.2.0` pin to the app extra. These are the non-yanked current stable
releases observed from each distribution's PyPI JSON metadata on 2026-09-09.
FastMCP's server/client extras accept MCP >=2.0.0,<3.0.0; MCP 2.2.0 requires
mcp-types==2.2.0. Each declares Python >=3.10, preserving this project's >=3.11.

Regenerate with `uv lock`, without broad upgrade flags, and restore through
`just setup`. Required transitive resolution changes belong to this upgrade;
unrelated upgrades do not. Keep the library dependency ranges unchanged.
Extend the existing app-only package inventory and core import blocker to
include the new distribution/import name. Existing supply-chain tests enforce
exactness, declaration coverage and lock agreement without new machinery.

Retain `from mcp.types import ToolAnnotations` as established by
[the #752 evidence](../mcp-2-type-import-evidence.md). Existing FastMCP server
construction and in-memory client composition are the integration path.
Use canonical `read_only_hint` and `destructive_hint` constructor keywords in
`annotations_for`: MCP 2 dynamically generates the camelCase aliases, which
ty 0.0.75 reports as discarded extras despite retaining them at runtime.
The canonical constructor preserves the same camelCase wire serialization.
This necessary typecheck compatibility adjustment leaves the import unchanged.
No new application behavior, registry functions, or import compatibility layer.

Adapt existing composition-test consumers to FastMCP 4: use `client.instructions`
because modern discovery does not populate `initialize_result`, and use canonical
annotation fields. The test-only served-schema probe reads serialized
`model_dump(by_alias=True)["inputSchema"]`, preserving its wire-schema assertions.
Production live-runner schema capture and its reconciliation remain with #754.

## Success and validation

- The coordinated exact pins and resolved lock agree; the core import probe
  rejects presentation imports, including `mcp_types`.
- Focused optional-dependency, supply-chain, registry, server-composition and
  smoke-script tests pass; `just smoke` completes and exposes 155 tools.
- `just verify` and `uv run --no-sync prek run --all-files` pass locally.
- Hosted CI passes its declared Python 3.11–3.14 amd64/arm64 matrix and
  dependent wheel/floor checks before merge handoff.
- Changes required in #754's generated-reference, schema-capture or audit
  surfaces trigger a scope checkpoint; a red gate cannot be waived.

## Failure model

- Actors and deployments: local app operators and CI on Python 3.11–3.14,
  amd64/arm64; library-only consumers must retain optional isolation.
- Invariants and assets: reproducible dependency resolution, importability,
  MCP registration/handshake and existing authorization contracts.
- Accepted failure classes: none within the completion criteria; unsupported
  Python/platform combinations are outside this change's matrix.
- Covered elsewhere: import investigation #752 (merged); generated reference,
  live-runner schema and audit reconciliation #754; unrelated upgrades and
  Dependabot workflow restoration #718. Excluded work is not a waiver for CI.

## Threat model

- Boundary inventory: dependency artifacts enter local and CI Python execution;
  no entry point or permission grant is added or widened by repository code.
- Actor model: package publishers control upstream code; MCP clients supply
  protocol inputs. Trust the project's existing PyPI resolver and release pins.
- Controls: exact app/dev pins, lock hashes, declared-import and isolation
  checks, existing protocol/security tests, full CI and security diff review.
- Outside scope: new supply-chain infrastructure and new MCP authorization
  behavior; existing repository controls remain responsible for those threats.

## Rollback

Revert the coordinated metadata and lock change together, then `just setup`.
No persisted-data migration or deployment-order change is introduced.
