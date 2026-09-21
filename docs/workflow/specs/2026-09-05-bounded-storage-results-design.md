# Bounded storage results design

## Scope and authority

The user authorized this pre-release refactor on 2026-09-05 while pursuing the
`refactor/pre-release` code-health branch. It replaces raw HMC storage mappings
at the presentation-neutral operations boundary. The change covers the supported
Python API, MCP storage tools, CLI storage output, their tests, generated tool
documentation, and the release manifest. Client transport methods, HMC request
documents, authorization behavior, and non-storage raw-resource APIs are out of
scope.

## Design

`operations.storage` will own frozen value objects for the stable storage
concepts exposed to callers: volume groups, virtual disks, optical media, and
vSCSI mappings. Each object will use domain names and JSON-compatible scalar
fields. A single private translator validates that the needed HMC fields have
the expected shape, preserving absent optional fields as `None`; malformed
responses raise `HMCError` instead of leaking a partly interpreted mapping.

The operation functions that presently return these concepts will return the
corresponding value object or tuple of objects. They resolve selectors and call
the client as before, then translate exactly once. Mutation operations whose
response is a parent VolumeGroup will return a `StorageOperationResult` carrying
the requested identity and optional HMC response fields only where this is a
useful stable result. Raw client mappings stay private to the client and to
operations that need HMC-specific nested fields to complete a mutation.

MCP tools will serialize value objects with the existing `serialize_tool_result`
helper. CLI commands will use `asdict`, matching other typed operation results.
The supported facade replaces raw-return annotations and exports the new models;
the ADR 0029 inventory and Unreleased facade manifest will be updated in the
same change. This is an intentional pre-release replacement, not a compatibility
shim.

## Failure and compatibility contract

Selector resolution, ownership checks, request behavior, and existing transport
errors are unchanged. A successful empty HMC response remains `None` where the
current operation permits it. A response that cannot supply a required stable
identifier or has a wrong field type raises `HMCError` naming the operation and
field; it is never silently represented as a raw mapping. The new result types
are frozen and contain only JSON-compatible values, so MCP and CLI conversion is
deterministic.

Existing consumers of the supported raw mappings must migrate to the named
fields. As an internal/pre-release surface, the old result shape is removed.

## Testing and acceptance

Focused operation tests will prove translation of representative HMC resources,
absent optional fields, malformed required fields, and that nested raw mappings
do not cross the operation boundary. MCP and CLI tests will prove the serialized
shape. Public-API tests will prove the facade inventory and transitive export
closure. `just test`, `just smoke`, generated tool documentation, and the
desloppify resolve command must pass before the implementation commit.
