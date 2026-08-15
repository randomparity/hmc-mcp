# Registry-wide tool parameter descriptions

**Issue:** [#146](https://github.com/randomparity/hmc-mcp/issues/146)

**Decision:** [ADR 0016](../../adr/0016-rendered-lifecycle-tool-descriptions.md)

**Guardrails:** `just verify`; `UV_NO_SYNC=1 uv run prek run --all-files`

## Outcome

Every tool in the live FastMCP registry exposes useful, non-empty JSON Schema descriptions for
each top-level parameter and every object property reachable through `$defs`. The guarantee holds
with the optional arbitrary-command tool both disabled and enabled.

## Design

The remaining server domain functions use Google-style `Args:` sections, the mechanism already
chosen by ADR 0016 and proven by the core-domain sweep. Descriptions identify selector forms,
units, defaults, enumerated meanings, destructive preconditions, and related discovery tools when
those facts affect correct use. Existing signatures, defaults, validation, and return shapes do
not change.

Nested dataclass fields use Pydantic-compatible `field(metadata={"description": ...})` metadata;
the existing `RepositorySource` TypedDict uses equivalent `Annotated` field metadata.
The registry test recursively follows local `$ref` values and walks object properties, including
objects under combinators and arrays. It reports the tool and schema path for every missing or
blank description. A synthetic schema exercises the negative checker without registering a tool.
The live-registry test runs once against the default registry and once after enabling
`hmc_run_command`, restoring the default state in `finally` after each configured run.

The README tool table remains an inventory, so it is regenerated or minimally updated to match
the current live registry without inventing tools or behavior.

## Error and edge behavior

- A property whose `description` is absent, non-string, or whitespace-only fails the gate.
- Shared or recursive definitions are visited safely without hiding distinct property paths.
- Conditional registration is tested symmetrically and leaves no state behind.
- Schema nodes that are not object properties do not require descriptions.

## Acceptance proof

1. A focused negative test passes a synthetic schema with deliberately undescribed top-level and
   nested properties to the checker and observes both reported paths.
2. Registry-wide tests pass with arbitrary commands disabled and enabled.
3. Nested `$defs` fields are included by the same traversal and existing nested resource/policy
   inputs pass.
4. `just verify` and the separately CI-gated prek command pass on the branch.

## Scope boundaries

No collection limit from #154, new tool, compatibility shim, runtime wrapper, dependency,
migration, or new ADR is introduced. This documentation/schema change does not add or widen a
trust boundary.
