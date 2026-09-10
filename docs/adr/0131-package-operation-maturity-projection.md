# ADR 0131: Package a generated operation-maturity projection

## Status

Accepted

## Context

ADR 0125 keeps the capability inventory under `docs/capabilities/`, and ADR 0127
keeps operation maturity sparse and derives live-evidence staleness. Issue #624 must
show those facts through installed CLI and MCP discovery as well as generated docs.
The documentation catalogs are not package resources, and copying their full corpus
would add data that runtime discovery neither needs nor should interpret.

The operator approved a read-only `hmc-mcp capabilities` command and a shared
operation-keyed projection in the issue #624 quest on 2026-09-10.

## Decision

Keep `docs/capabilities/maturity.json` authoritative. Generate a compact package
resource containing only the presentation state derived for its recorded operations:
implementation state, live-verification state and reason, observation time needed for
age expiry, and the catalog's runtime-eligibility policy. Operations absent from the
sparse catalog remain absent from the resource and resolve to explicit `unrecorded`
presentation values at runtime.

The existing capability-inventory validator owns generation, join validation and exact
freshness checking. A small internal package reader validates the resource and supplies
one immutable operation projection to CLI output, namespaced MCP tool `_meta`, and the
tool reference generator. MCP and CLI presentation names join by operation ID, so no
alias can own independent evidence. Sparse absence is a valid `unrecorded` result for
consumers, not an occasion to repeat the repository join check.

The packaged reader may age a recorded observation from current or failed to stale at
ADR 0127's boundary. CLI rows resolve it when the command runs. A `tools/list` middleware
joins each already-filtered tool name through the complete security catalog and resolves
the value for each request, so a long-running server does not retain a past verification
state. Tool registration remains unchanged. The reader does not re-evaluate source
closure: the repository freshness gate proves the resource was generated against the
packaged source before release.

## Consequences

Installed discovery remains available without the repository checkout, configured
profile, or an HMC connection. The existing CLI root callback still parses root-option
environment fallbacks before dispatch. The wheel gains one small generated JSON resource,
one internal reader and one discovery middleware. A maturity or implementation change
must regenerate that resource; the existing catalog gate rejects drift. Custom MCP
metadata is advisory and clients may ignore it, while authorization and runtime
capability checks remain authoritative.

## Considered & rejected

- **Package the complete capability catalogs.** judgment: the corpus, normalized rows,
  handlers and test links are documentation evidence, not runtime discovery data; this
  would enlarge the installed contract and duplicate parsing responsibilities.
- **Read `docs/capabilities/` at runtime.** verified: `pyproject.toml` at
  `5e74ab0255ec6333a600b2dbec968691fbc61293` includes `src/hmc_mcp` in the built
  package and does not include `docs/`, so an installed wheel cannot resolve that path.
- **Generate Python source constants.** judgment: executable generated code offers no
  benefit over a closed JSON shape and makes review of data-only changes less clear.
- **Store maturity independently on every presentation name.** verified: ADR 0125 and
  `docs/capabilities/operations.json` key the inventory by operation ID; duplicating
  evidence on CLI or MCP names permits those views to disagree.
- **Do nothing.** verified: issue #624 requires installed CLI/MCP discovery, while the
  accepted catalogs currently live outside the packaged `src/hmc_mcp` tree.
