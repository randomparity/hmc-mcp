# ADR 0104: Return the authorized LPAR from storage mapping

## Status

Accepted (2026-08-27)

## Context

The storage-map CLI resolved its LPAR selector to a UUID for display, then passed that UUID
to `operations.storage.map_storage`. The operation resolved the selector again as part of
the shared ownership and containment guard. This duplicated identity work and split
responsibility for the authoritative target between presentation and operation layers.

## Decision

`map_storage` accepts the caller's original selector and returns `StorageMapResult`, a frozen
dataclass containing the authorized `lpar_uuid` and the mapped VIOS `resource`. The operation
alone owns target resolution and authorization. CLI and MCP entry points render the result;
they do not resolve the partition independently.

`StorageMapResult` joins the supported reusable facade under ADR 0029. Changing the exported
operation's return annotation and adding the result type requires a minor release during
`0.x` and is recorded in the changelog manifest.

## Consequences

Callers receive the canonical LPAR UUID chosen by the same boundary that authorized the
mutation. The return shape changes from the raw mapped resource to a concrete result carrying
that resource, so consumers must read its `resource` field.
