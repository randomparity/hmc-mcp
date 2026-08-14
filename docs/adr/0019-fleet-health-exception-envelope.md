# ADR 0019: Fleet-health exception envelope

## Status

Accepted (2026-08-14)

## Context

The server can summarize one managed system and can report fleet capacity, but neither
operation answers which resources are unhealthy across an estate. Repeating
`hmc_system_summary` returns healthy and unhealthy state together, omits LPAR RMC detail and
recent jobs, and makes callers filter large per-system results. A new composite must keep a
stable public shape, avoid unbounded endpoint fan-out, and distinguish an unsupported global
Job feed from a supported feed with no failures.

## Decision

Add `hmc_fleet_health` as a read-only exception query. It returns a stable envelope with
`systems`, `vios`, `lpars`, `failed_jobs`, and `warnings` collections on every call. The first
four collections contain only curated unhealthy records. Healthy estates return empty
collections.

The exception predicates and record fields are fixed:

- `systems` contains `{uuid, name, state}` when normalized state is not `operating`;
- `vios` contains `{uuid, name, state, system_uuid, system_name}` when normalized partition
  state is not `running`;
- `lpars` contains `{uuid, name, state, rmc_state, system_uuid, system_name}` when normalized
  RMC state is neither `active` nor `busy`; and
- `failed_jobs` contains `{uuid, name, status, error}` when case-normalized status belongs to
  `jobs.FAILED_JOB_STATUSES` among the first 20 records in HMC Job-feed order.

Normalization accepts string values only, strips surrounding whitespace, and compares them
case-insensitively. Missing, blank, or non-string health states normalize to `unknown` and qualify
as exceptions. Missing, blank, or non-string Job statuses normalize to `unknown` and do not match
the canonical failure set. The collections are sorted by name and then UUID. `warnings` contains
strings that describe tolerated optional-data gaps.

Fetch managed systems once, then fetch each system's LPAR and VIOS collections concurrently.
Limit the number of systems being inspected at once to eight. A core inventory error fails the
whole operation so an incomplete estate is never reported as healthy. Only the known HMC error
for an unsupported global Job root is tolerated: HTTP 400 with both `REST000E` and
`Unrecognized root REST type of Job` in the HMC response body. In that case, `failed_jobs` stays
empty and `warnings` explains that job health is unavailable. Other Job-feed errors fail the
operation.

Job failure classification reuses `jobs.FAILED_JOB_STATUSES`, the canonical semantics introduced
by issue #141. It includes `COMPLETED_WITH_ERROR`, `EXCEPTION`, `FAILED`,
`FAILED_BEFORE_COMPLETION`, `FAILED_BEFORE_COMPLETION_RETRY`, and `FAILED_TO_START`.
Successful, running, warning, and unknown statuses are not reported as failed.

## Consequences

The operation is an exception index, not a third summary: it omits every healthy resource and
does not expose capacity fields. The fixed envelope makes unsupported optional telemetry
visible without a result-shape union. Bounded concurrency reduces latency without creating one
task per estate system at once. Callers receive no partial core inventory; they must retry or
surface the underlying error. The result is assembled from multiple reads and is not an atomic
point-in-time view; resources can change while the operation is collecting them.

## Considered & rejected

**Do nothing.** Existing summaries cannot identify the unhealthy resources named by the issue,
so callers would continue to fan out, filter, and reconcile incompatible views themselves.

**Call `hmc_system_summary` N times.** That returns per-system state and capacity, not
exception-only fleet health, and lacks LPAR RMC and recent-job failure detail.

**Return partial inventory with warnings for every failed endpoint.** Missing systems could be
mistaken for healthy ones. Failing the query is safer than presenting an incomplete health
snapshot.

**Treat an unsupported Job feed as a fatal error.** Job listing varies by HMC firmware and is
optional telemetry. A stable warning preserves useful core health while making the gap explicit.

**Return only four keys.** An empty failed-job list cannot distinguish a healthy supported feed
from an unsupported one. An always-present warnings collection preserves a single schema and
the distinction.
