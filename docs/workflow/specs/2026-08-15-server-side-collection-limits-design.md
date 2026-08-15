# Client-side collection payload limits design

## Goal

Issue #154 will let callers bound the agent-facing result of every public
collection tool backed directly by an HMC UOM Atom feed, without changing its
list result shape. The full HMC feed is still transferred and parsed before the
result is sliced. The existing `hmc_list_recent_jobs(limit=20)` contract remains
client-side because measured HMC firmware offers no source-side mechanism.
ADR [0026](../../adr/0026-server-side-collection-limits.md) records both this
contract and the accepted future path for large single-resource payloads.

## Scope and constraints

The affected public tools are `hmc_list_systems`, `hmc_list_lpars`,
`hmc_list_vios`, `hmc_list_resources`, `hmc_list_recent_jobs`,
`hmc_list_adapters`, `hmc_list_virtual_switches`,
`hmc_list_virtual_networks`, `hmc_list_network_bridges`,
`hmc_list_volume_groups`, `hmc_list_clusters`, and
`hmc_list_shared_storage_pools`. Each is backed directly by a UOM feed.

CLI-backed lists, HMC web-resource lists, template-library lists, and composite
operations are excluded because they do not map a public request directly to a
UOM collection feed. Single-resource fetches and metric documents are also
excluded. No summary tool or polymorphic detail mode is added.

The implementation is architecture-independent and follows the rolling Python
3.11+ support policy in ADR 0020. Pull-request checks exercise amd64 and arm64;
ppc64le remains supported under ADR 0021 without a required pull-request job.
The change adds no dependency and preserves every result as
`list[dict[str, Any]]`. It does not modify HMC request URLs or claim reduced
network, HMC, or parsing cost.

## Public contract

The eleven newly limited tools receive `limit: int | None = None` after their
existing `profile` parameter so direct Python callers retain every existing
positional binding. `hmc_list_recent_jobs` keeps its existing
`(limit=20, profile=None)` order unchanged. MCP callers continue to use named
JSON fields. Omitted returns the complete parsed feed; a positive integer is the
maximum number of already-parsed entries placed in the MCP response; zero still
runs the complete HMC operation and then returns `[]`; a negative value raises
`ValueError` before session creation. `hmc_list_recent_jobs` retains
`limit: int = 20` and its post-parse slicing behavior.

Every `limit` entry in the generated MCP input schema has a non-empty
description stating those semantics. Existing state, system, adapter-type, and
resource-type selectors remain independent and keep the list shape.

## Collection flow

A shared server helper validates the optional limit, runs the existing async
collection operation through `_run`, and slices the resulting list. Negative
values fail before `_run`; zero and positive values run the complete operation
before slicing. Existing client request methods and presentation-neutral
operations remain unchanged, which prevents an unsupported query parameter
from reaching the HMC.

The same helper is used by every affected tool, including recent jobs after its
existing unsupported-firmware error translation. State, parent, adapter-type,
and resource-type selectors still execute on the HMC or existing client path
before the post-parse cap is applied.

## Errors and compatibility

Negative limits fail with `ValueError("limit must be greater than or equal to
0")` before session creation. Zero still performs the request. Existing HMC errors,
including firmware that does not support global Job listing, retain their
current translations. Existing calls that omit `limit` keep their behavior;
list shapes and resource dictionaries are unchanged.

## Threat model

The added boundary is an MCP caller supplying `limit`, which influences only a
Python list slice after the HMC response is parsed. FastMCP/Pydantic constrains
the decoded value to an integer and the tool rejects negative values before
client construction. It never enters an HMC URL or request. Existing profile
routing, authentication, authorization, TLS, and resource-selector trust
boundaries are unchanged.

This design does not cap the complete feed's transfer or parsing cost, the byte
size of one UOM entry, or repeated calls. A very large limit is equivalent to
returning the complete collection. Those residuals require HMC support,
operational policy, or separately named summary contracts and remain outside
issue #154.

## Verification

An inventory test over the exact twelve named tools will prove each exact
parameter order, signature default, and generated-schema description.
Table-driven runtime tests will prove omitted, zero, positive, and negative
behavior for every tool; representative state, parent, adapter-type, and
resource-type cases will prove selectors complete before slicing. Recent-jobs
tests will additionally preserve its unsupported-firmware error translation.
A request-boundary regression test will prove no limit query is sent. README
examples and the tool table will state that the complete feed is transferred
and parsed. The final branch must pass `just verify` and
`UV_NO_SYNC=1 uv run prek run --all-files`.

## Durable handoff

Branch: `feat/server-side-collection-limits-154`. Base branch: `main`.
Guardrails: `just verify`; `UV_NO_SYNC=1 uv run prek run --all-files`.
