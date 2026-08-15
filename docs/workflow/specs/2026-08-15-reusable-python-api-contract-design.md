# Reusable Python API contract design

Issue #188 records the compatibility decision required before epic #186 exposes a reusable Python
facade. [ADR 0029](../../adr/0029-supported-reusable-python-api-contract.md) is the normative
artifact; this design describes how the ADR is derived and verified without implementing the
facade or changing runtime behavior.

## Scope and authority

The issue and epic require an accepted ADR that defines the `hmc_mcp.api` selection rule, exact
operation-module inventory, strict `0.x` SemVer boundary, package-owned versus opaque payload
contracts, supported `HMCClient` lifecycle members, the `app` extra boundary, and the Python 3.13
wheel-contract runtime.

This slice creates the ADR and the quest design records only. Facade code and contract tests belong
to #190; optional dependencies belong to #189; installed-wheel proof belongs to #191; user-facing
installation and API documentation belong to #192. No Python, packaging, workflow, or runtime
behavior changes in this slice.

## Inventory method

Enumerate every tracked `src/hmc_mcp/operations_*.py` module. For each module, inspect its
top-level definitions and public signatures. Every non-underscore top-level asynchronous function
is included, including asynchronous policy-enforcement workflows. Synchronous transformation,
parsing, and validation helpers are excluded because the public operation contract is
asynchronous. Every package-owned model or literal alias in selected signatures is included.
Underscore definitions are excluded as internal. Built-in containers, `Any`, and their opaque HMC
payload contents are not package-owned types.

The resulting 15-row inventory is written directly into ADR 0029 so later facade work has one
reviewable source for exact names and exclusion reasons. A future module or operation requires an
intentional ADR/facade/test update rather than silently becoming supported.

## Compatibility boundary

The facade is the sole reusable-library import contract. Strict pre-1.0 SemVer means an
incompatible removal, rename, signature change, or owned-model change waits for a minor release.
An additive facade export also requires a minor release and an intentional exact-export test
update. Patches remain compatible and do not change the export set.

`HMCClient` remains concrete for construction, async context management, and injection of a
constructed instance into operation functions. Its exact supported lifecycle allowlist is
`__init__`, `__aenter__`, `__aexit__`, `is_logged_on`, `logon`, and `logoff`. Discoverable inherited
methods are explicitly unsupported. Duck-typed fakes are a testing technique, not a supported
alternate-client protocol. The facade also owns the configuration entry points and error hierarchy
named in ADR 0029.

Package-owned dataclasses and literal aliases are stable facade types. Raw resource mappings are
opaque IBM HMC data: container shape in a signature is supported, but resource keys and nested
schema are not. Generic UOM helpers, mixin methods, builders, parsers, SSH primitives, and
presentation adapters remain internal.

## Dependency and runtime decisions

One `app` optional extra will contain Typer, Rich, FastMCP, and MCP. The bare install must import
the facade without those packages. The installed-wheel library contract will run on Python 3.13;
ADR 0020 independently continues to govern all supported CPython versions. These decisions are
recorded here and in ADR 0029 but implemented only by the dependent issues.

## Verification

Review compares the ADR inventory against an AST-derived list of every current operation module,
non-underscore top-level function, and signature-owned package type. It also checks the lifecycle
allowlist against `HMCClient`'s concrete definitions and confirms every requirement in #188 and
epic #186 requirements 1 and 2 has a normative statement.

The documentation-only branch runs `just verify` and the separately CI-gated
`UV_NO_SYNC=1 uv run prek run --all-files`. The host is arm64; declared targets are amd64, arm64,
and ppc64le; the host is included. The change is architecture-independent.

## Durable workflow context

- Branch: `feat/reusable-python-api-contract-188`
- Base branch: `main`
- Scope token: `3ee956be-937c-4499-98cb-638e737dfc97`
- Guardrails: `just verify`; `UV_NO_SYNC=1 uv run prek run --all-files`
