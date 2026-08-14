# Fleet health exception snapshot design

## Scope and governing decision

Issue #152 requests one read-only estate query for unhealthy managed systems, VIOS partitions,
LPAR RMC state, and recent jobs. [ADR 0019](../../adr/0019-fleet-health-exception-envelope.md)
governs the envelope, concurrency, and failure contract. ADR 0012 governs stable public shapes.
Broad documentation work from #145 and migration validation from #151 are excluded.

## Public contract

`hmc_fleet_health(profile: str | None = None) -> FleetHealthResult` returns a frozen dataclass:

- `systems`: `{uuid, name, state}` entries whose normalized state is not `operating`;
- `vios`: `{uuid, name, state, system_uuid, system_name}` entries whose normalized partition
  state is not `running`;
- `lpars`: `{uuid, name, state, rmc_state, system_uuid, system_name}` entries whose normalized
  RMC state is neither `active` nor `busy`;
- `failed_jobs`: `{uuid, name, status, error}` entries for normalized failure terminal states;
- `warnings`: strings describing tolerated optional-data gaps.

Missing or blank state is unhealthy and rendered as `unknown`, because silence must not look
healthy. Lists are deterministically sorted by name then UUID. A healthy supported estate returns
five empty tuples, which FastMCP serializes as JSON arrays rather than nulls.

## Components and data flow

`operations_health.py` owns pure record curation and the asynchronous fleet operation. It fetches
the managed-system feed once. Up to eight system inspections run concurrently under an
`asyncio.Semaphore`; each inspection fetches LPAR and VIOS collections concurrently. A separate
global Job-feed request runs alongside core inventory. `server_health.py` adapts the operation to
FastMCP with `_READ_ONLY`. `cli_systems.py` provides `systems health`, using the same operation and
printing JSON or exception tables.

The operation calls `HMCClient.list_uom("Job")` directly so it can distinguish the exact known
unsupported-root `HMCError`. That error produces an empty `failed_jobs` collection plus one
warning. Every other Job or inventory error propagates. No partially collected core result is
returned.

## Why existing summaries are insufficient

Calling `hmc_system_summary` N times returns one record per system, including healthy systems,
capacity, firmware, and aggregate partition counts. It cannot identify the individual LPAR whose
RMC is inactive, and it does not inspect recent jobs. `hmc_capacity_report` is estate-wide but
answers placement capacity, not health. The new operation is an exception index: its size scales
with unhealthy resources, not estate size, and healthy resources are absent.

## Error handling and observability

Core inventory failures propagate with the existing HMC error translation. Malformed entries
remain visible as curated `unknown` exceptions rather than being discarded. Unsupported Job-feed
warnings explicitly state that system, VIOS, and LPAR health remains available while recent-job
health is unknown. The CLI displays warnings even when all four exception tables are empty.

## Threat model

### Boundary inventory

- Added: an MCP/CLI caller triggers multiple authenticated read requests to the configured HMC.
- Widened: parsed HMC inventory and error text cross into a new curated result.

### Actor model

The caller is an operator or agent already allowed to invoke this unauthenticated local MCP/CLI
surface. The HMC is trusted to authenticate requests but its response fields are untrusted input.
This change adds no network listener, credential handling, or authorization decision.

### Controls

The operation accepts no estate selector, path, query, or command input beyond the existing
profile selector. Concurrency is capped at eight system inspections. Curators select named scalar
fields only; raw entries and response bodies are not returned. Existing HMC error translation
controls failure disclosure. The known unsupported-feed match checks status and the specific HMC
error identifiers rather than suppressing arbitrary HTTP 400 responses.

### Out of scope

Authentication for the MCP HTTP transport is unchanged and documented by the existing server
warning. Per-HMC authorization and audit policy remain HMC responsibilities. This read-only query
does not attempt retry, caching, or historical monitoring.

## Testing and verification

Pure curator tests cover case normalization, missing states, stable keys, deterministic sorting,
and normalized failed-job states. Async operation tests cover a healthy estate, all four degraded
categories, unsupported global Job listing, unrelated Job failure propagation, core inventory
failure, and an observed maximum of eight concurrent system inspections. Server/capability tests
pin read-only registration and schema. CLI tests pin JSON, warning, and degraded output. Run
focused tests during TDD and `just verify` before every push.

## Durable workflow context

- Branch: `feat/fleet-health-152`
- Base branch: `main`
- Guardrail: `just verify`
- Architecture: host arm64; repository target architectures not declared; relationship
  `no-target-declared`
