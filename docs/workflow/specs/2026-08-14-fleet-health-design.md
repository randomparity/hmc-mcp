# Fleet health exception snapshot design

## Scope and governing decision

Issue #152 requests one read-only estate query for unhealthy managed systems, VIOS partitions,
LPAR RMC state, and recent jobs.
[ADR 0019](../../adr/0019-fleet-health-exception-envelope.md)
governs the envelope, concurrency, and failure contract. ADR 0012 governs stable public shapes.
Broad documentation work from #145 and migration validation from #151 are excluded.

## Public contract

The presentation-neutral `fleet_health` operation returns a frozen `FleetHealthResult` dataclass.
`hmc_fleet_health(profile: str | None = None) -> dict[str, Any]` serializes that result as a
five-key mapping at the MCP boundary:

- `systems`: `{uuid, name, state}` entries whose normalized state is not `operating`;
- `vios`: `{uuid, name, state, system_uuid, system_name}` entries whose normalized partition
  state is not `running`;
- `lpars`: `{uuid, name, state, rmc_state, system_uuid, system_name}` entries whose normalized
  RMC state is neither `active` nor `busy`;
- `failed_jobs`: `{uuid, name, status, error}` entries whose case-normalized status belongs to
  `jobs.FAILED_JOB_STATUSES`, among the first 20 records in HMC Job-feed order;
- `warnings`: strings describing tolerated optional-data gaps.

Missing or blank state is unhealthy and rendered as `unknown`, because silence must not look
healthy. Lists are deterministically sorted by name then UUID. A healthy supported estate returns
five empty tuples in the operation result, serialized as JSON arrays rather than nulls at the MCP
boundary.

## Components and data flow

`operations_health.py` owns pure record curation and the asynchronous fleet operation. It fetches
the managed-system feed once. At most eight fixed workers consume a shared system queue, so both
active inspections and scheduled system-worker tasks remain bounded; each inspection fetches LPAR
and VIOS collections concurrently. A separate global Job-feed request runs alongside core
inventory. `server_health.py` adapts the operation to FastMCP with `_READ_ONLY`. `cli_systems.py`
provides `systems health`, using the same operation and printing JSON or exception tables.

The operation calls `HMCClient.list_uom("Job")` directly so it can distinguish the exact known
unsupported-root `HMCError`. The HMC controls Job-feed ordering and retention; matching
`hmc_list_recent_jobs` behavior, the snapshot applies no local time cutoff and inspects the first
20 records returned by the feed. That error produces an empty `failed_jobs` collection plus one
warning. Every other Job or inventory error propagates. No partially collected core result is
returned.

## Why existing summaries are insufficient

Calling `hmc_system_summary` N times returns one record per system, including healthy systems,
capacity, firmware, and aggregate partition counts. It cannot identify the individual LPAR whose
RMC is inactive, and it does not inspect recent jobs. `hmc_capacity_report` is estate-wide but
answers placement capacity, not health. The new operation is an exception index: its size scales
with unhealthy resources, not estate size, and healthy resources are absent.

## Error handling and observability

Core inventory failures propagate with the existing HMC error translation. A managed-system entry
with a missing, blank, or non-string UUID fails the operation because its child inventory cannot be
inspected; the operation never skips that system and returns a partial snapshot. Missing, blank, or
non-string names and child/job UUIDs are rendered as `unknown`, preserving string-valued records
and deterministic name-then-UUID sorting without discarding an exception. Malformed scalar health
fields on otherwise identifiable entries remain visible as curated `unknown` exceptions rather
than being discarded. A failed job's missing, blank, or non-string error is rendered as `unknown`;
accepted error text is limited to its first 500 characters before crossing the result boundary.
Unsupported Job-feed warnings explicitly state that system, VIOS, and LPAR health remains
available while recent-job health is unknown. The CLI displays warnings even when all four
exception tables are empty.

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
every canonical `FAILED_JOB_STATUSES` value, representative successful/running/warning/unknown
states, Job-feed limit boundaries, missing/blank/non-string names and child/job UUIDs, and missing,
blank, non-string, and oversized job errors. Async
operation tests cover a healthy estate, all four degraded categories,
unsupported global Job listing, and near misses that independently change the HTTP 400 status,
omit `REST000E`, or omit `Unrecognized root REST type of Job`, with each near miss propagating.
They also cover a missing, blank, or non-string managed-system UUID failing without a partial
result or child request for that entry, other core inventory failures, an observed maximum of eight
concurrent system inspections, and at most eight scheduled system-worker tasks for an estate larger
than the limit. Server/capability tests pin
read-only registration and schema. CLI tests pin JSON, warning, and degraded output. Run focused
tests during TDD and `just verify` before every push.

## Durable workflow context

- Branch: `feat/fleet-health-152`
- Base branch: `main`
- Guardrail: `just verify`
- Architecture: host arm64; repository target architectures not declared; relationship
  `no-target-declared`
