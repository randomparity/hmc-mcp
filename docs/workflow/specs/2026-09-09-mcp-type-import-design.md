# MCP 2 ToolAnnotations import verification

## Problem and scope

Issue #752 asks whether the direct `mcp.types.ToolAnnotations` import survives
MCP 2, before #753 changes dependencies. The frozen authority is
[q752-7fab51a8](https://github.com/randomparity/hmc-mcp/issues/752#issuecomment-5612896059).
An isolated Python 3.11 probe with FastMCP 4.0.3 and MCP/mcp-types 2.2.0
resolved the existing import to `mcp_types._types.ToolAnnotations`; the installed
`mcp/types/__init__.py` explicitly defines the namespace as a supported mirror.
Preserve that import if the supported Python range confirms this observation.
No new interface, dependency, fallback, or architectural decision is needed.

## Design and success

Use isolated temporary environments for Python 3.11, 3.12, 3.13, and 3.14.
Confirm distribution ownership, class identity across the two namespaces, and
existing constructor and wire-field semantics on FastMCP 4.0.3 with MCP and
mcp-types 2.2.0. Run the existing registry and application-security modules
under both the project lock and the isolated upgraded environments.
Strengthen the existing unit annotation contract to assert serialized wire
values for the four effect classes, including the intentionally unspecified
destructive hint on reads and mutation. Retain application registration coverage.
Use serialized hints in its mutating-tool assertion; prove independent copies
by mutating the shared writable `title` field. The upgraded probe showed that
FastMCP's deprecated camelCase properties support reads but have no setter.
Record commands, versions, observed results, and limitations in
`docs/workflow/mcp-2-type-import-evidence.md` for #753.

Dependency pins and lock regeneration belong to #753; generated docs, schema
field naming, and audit artifacts belong to #754; Dependabot restoration and
epic closure belong to #718; broader FastMCP 4 migration belongs to #718 or a
separate issue. These exact exclusions were approved in campaign dispatch.
If import or annotation preservation requires one of those excluded changes,
report the observed dependency to the campaign before altering scope.

## Failure model

- Actors and deployments: maintainers and CI on Python 3.11–3.14;
  local upgraded probes use amd64; existing-lock CI covers amd64 and arm64.
- Invariants and assets: the supported import resolves and tool annotations
  preserve read-only/destructive wire hints for the four declared effects.
- Accepted failure classes: future MCP releases beyond 2.2.0 are untested;
  the evidence is version-bound, not a promise about future releases.
- Covered elsewhere: upgraded arm64/full-suite validation and dependency
  resolution are #753; generated/audit/schema compatibility is #754;
  tool authorization enforcement remains in the existing security suite.

## Validation

The unit wire contract and registered annotation contract use focused tests in
`tests/unit/test_tool_registry.py` and `tests/app/test_tool_security.py`.
A temporary wrong wire-hint mutation must fail the unit contract, then be
removed before the focused green run. Reproduce the import probe and focused
modules across the four named Python versions and record exit statuses.
Run `just verify` and `uv run --no-sync prek run --all-files` on the unchanged
project lock before pushing; read the resulting hosted CI matrix.
The evidence prose has no executable consumer and needs factual review, not
a test that snapshots its wording. Temporary environments stay outside Git.
