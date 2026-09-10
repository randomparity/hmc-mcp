# Operation maturity discovery design

## Problem

The repository has a finite operation inventory and a sparse, validated maturity
catalog, but installed discovery does not expose them. Generated tool pages show effect,
operation and target only. MCP `tools/list` carries security annotations but no maturity,
the CLI has no operation-level maturity view, and `docs/compatibility.md` makes blanket
HMC and POWER compatibility claims that the recorded evidence cannot support.

Issue #624 requires one evidence-backed view without changing which operations are
installed, exposed or callable. This is foundation F4 of epic #620 and consumes the
merged F1/F2/F3 records from issues #621, #622 and #623.

## Scope

### Architecture

`docs/capabilities/maturity.json` remains canonical. The capability validator generates
and freshness-checks `src/hmc_mcp/_operation_maturity.json`, a sparse runtime projection
defined by [ADR 0131](../../adr/0131-package-operation-maturity-projection.md). An
internal stdlib-only reader validates that resource and returns immutable
`OperationMaturity` values. The reader supplies the CLI command, MCP registration and
tool-reference generator; none reads the documentation catalogs independently.

Each value keeps three dimensions separate:

- `implementation`: `unrecorded`, `absent`, `partial` or `implemented`;
- `verification`: `unrecorded`, `unevidenced`, `current`, `stale` or `failed`, with a
  bounded reason when present; and
- `runtime_eligibility`: the catalog value `existing-runtime-guards`.

Presentation names join through their `ToolSecurity.operation`. `hmc-mcp capabilities`
groups the current registered tool names by operation and renders a table by default or
a JSON array with `--json`. It reads neither HMC configuration nor the network. Each MCP
tool carries the same value below one namespaced `_meta` key. Generated group pages add
the three dimensions to their operation row and link their meaning from the index.

The projection stores the observation time for a current or failed live result. The
reader applies ADR 0127's 90-day rule at query time. Repository generation additionally
derives closure staleness, and exact-byte comparison makes source/catalog drift fail
`just capability-inventory` until `just capability-metadata` regenerates the resource.

`docs/compatibility.md` describes the frozen POWER10/POWER11 reference corpus and points
to per-operation evidence. It retains specific, sourced limitations such as the VIOS
backup floor and write-path behavior, but removes the universal V8–V11/all-POWER claim.

### Error handling

- Generation refuses invalid canonical catalogs before writing and uses atomic replace.
- The catalog gate reports a missing, malformed or stale projection and the regeneration
  command; it never repairs files in check mode.
- The installed reader rejects unknown fields, duplicate projected operations, invalid
  states, invalid operation-ID grammar and invalid timestamps with an actionable
  `OperationMaturityError`; it never publishes a partial mapping.
- An operation absent from the sparse projection resolves to explicit `unrecorded`
  values. Canonical operations absent from the registry are rejected by the repository
  gate before projection generation.
- An age-expired observation reports `stale` with reason `age-exceeded`; evidence failure
  never changes runtime eligibility.

## Failure model

### Actors and deployments

- Local operators run the installed CLI on Python 3.11–3.14.
- MCP clients inspect tools over the existing stdio or HTTP deployments.
- CI and maintainers validate catalogs, generate docs and build distributions on the
  declared amd64 and arm64 targets.

### Invariants and assets at stake

- Installed discovery must not overstate implementation or verification.
- CLI, MCP metadata and generated docs must resolve one operation to one projection.
- Existing tool schemas, security annotations, policy ceilings and dispatch checks must
  remain unchanged.
- The generated resource is a packaged public contract for this release.

### Accepted failure classes

- MCP clients may ignore custom `_meta`; it is advisory protocol metadata.
- An installed release cannot see evidence added to a later release; claims are scoped to
  the installed artifact and still age locally.
- Sparse absence reports `unrecorded`; ADR 0127 deliberately makes absence non-promoting.
- A tampered installation may fail to load; distribution integrity is not repaired at
  runtime.

### Covered elsewhere

- Canonical catalog validity and staleness: ADR 0127 and `just capability-inventory`.
- Authorization, ownership and capability admission: existing access-policy and operation
  guards; issue #624 changes none of them.
- Generated-document and package freshness: `just tool-docs-check`, `just doc-freshness`,
  `just build` and `just verify-artifacts`.
- Live hardware coverage: verification issues #625–#635 and operators.

### Threat model

- Added boundary: a local CLI command reads one bundled, size-bounded JSON resource.
  Strict shape and vocabulary validation control it; failures reveal no file contents.
- Widened boundary: MCP `tools/list` returns fixed public maturity fields in `_meta`.
  Values come only from the bundled projection, carry no credentials or HMC identifiers,
  and existing policy filtering still decides which tool records are returned.
- Trusted parties: the built package and repository generation gate. Untrusted CLI input
  is limited to `--json`; it selects formatting and never a path or operation execution.
- Out of scope: authenticating the existing HTTP transport, package-signing policy, and
  clients that discard extension metadata; their existing owners and warnings remain.

## Success

- For the registry operation set at build time, each MCP tool, CLI capability row and
  generated tool row resolves through the same operation-keyed projection.
- Canonical maturity entries occur once in the projection; multiple presentation names
  for one operation consume that value rather than copying evidence.
- The three maturity dimensions remain separately named in table, JSON, MCP and docs.
- The CLI command runs from an installed artifact without HMC configuration or I/O.
- MCP names, schemas, annotations, exposure ceilings, authorization and calls are byte-
  or behavior-equivalent apart from the added namespaced `_meta` value.
- Catalog and generated-document checks reject missing, invalid or stale derived output.
- Compatibility prose makes only corpus- or operation-evidence-backed claims.
- This change records no new live observation and claims no hardware exercise.

## Validation

- Focused runtime-reader tests cover shape validation, sparse defaults, immutability and
  the current-to-stale time boundary.
- Focused catalog tests cover generation plus missing, malformed and drifted projections.
- MCP tests compare every served tool's metadata with its operation projection and retain
  annotation and ceiling assertions.
- CLI tests compare table and JSON rows without HMC configuration or network calls.
- Generator tests cover maturity columns, shared operation values and regeneration drift.
- Project-metadata tests reject the removed blanket compatibility claims and require the
  evidence-backed replacement. Artifact tests prove the projection ships.
- New behavioral tests receive a controlled fault before implementation, then the focused
  tests, `just verify`, `uv run --no-sync prek run --all-files`, and hosted CI run bare.
