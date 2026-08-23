# Logical-partition PCM paths design

## Scope

Issue #400 requires processed and aggregated logical-partition metrics to use
the documented managed-system nesting, while preferences and Long Term Monitor
reject that category. SSP support and live licensed-HMC verification remain out
of scope. [ADR 0077](../../adr/0077-nest-logical-partition-pcm-metrics-under-managed-system.md)
governs the target representation.

## Contract

PCM resource resolution returns a target containing `resource_uuid` and an
optional `system_uuid`. Managed-system resolution sets only `resource_uuid`.
Logical-partition resolution requires `system_name_or_uuid`, resolves that
system first, and resolves a named partition within it; UUID partitions pass
through after the owner is resolved.

The four processed/aggregated MCP tools gain an optional
`system_name_or_uuid`, and CLI `metrics show` gains the equivalent optional
`--system` selector. It is required only for `LogicalPartition`; omitting it
raises a clear `ValueError` before network I/O. Supplying the selector for a
managed-system request is rejected as misuse on both interfaces.

Client processed and aggregated methods accept `system_uuid` as a keyword-only
argument. For `LogicalPartition`, it is required and produces
`/rest/api/pcm/ManagedSystem/{system_uuid}/LogicalPartition/{lpar_uuid}/{kind}`.
For `ManagedSystem`, it must be absent and the existing flat path is preserved.

Preferences reject `LogicalPartition` in the operation layer with a message
directing callers to `ManagedSystem` preferences. The client Long Term Monitor
method rejects it with a message naming the documented ManagedSystem-only feed.
Both checks occur before any request.

## Error handling

Unknown categories retain the current pass-through behavior only outside the
typed public surface. Missing owners, owners supplied for managed systems, and
unsupported category/operation combinations raise `ValueError` with the bad
combination and suggested supported form. Existing HMC error translation is
unchanged.

## Testing

Focused tests prove nested processed and aggregated paths for named and UUID
partitions, owner resolution and ambiguity behavior, early preference and LTM
rejection, and unchanged managed-system paths. MCP and CLI tests cover selector
forwarding, missing selectors, and managed-system misuse. Tests assert rejected
calls make no HTTP request. The repository test, smoke, and verify recipes
remain the release gates.

## Durable execution context

- Branch: `feat/fix-lpar-pcm-paths-400`
- Base branch: `main`
- Guardrails: `just test`, `just smoke`, `just verify`
- Architecture: host `x86_64`; no target declared; relationship
  `no-target-declared`
