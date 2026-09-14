# Discover defined job operations via `/operations`

Issue #787, under epic #785. Decision record: [ADR 0139](../../adr/0139-discovery-reads-return-feed-and-schema-version.md).

## Problem

`hmc-mcp` submits jobs by building `do/{Operation}` paths from operation names hardcoded in
Python — 14 call sites. Nothing asks the target HMC which job operations it actually defines,
so an operation the firmware does not offer is discovered only when the submission fails. The
HMC publishes that answer at `/rest/api/uom/{R}/operations` and, for a child type anchored to
a parent instance, at `/rest/api/uom/{P}/{UUID}/{C}/operations`. No `/operations` path occurs
anywhere under `src/` today.

## Scope

One new read-only method on `HMCClient`, in `src/hmc_mcp/client/core.py` beside the existing
uom reads:

```python
async def list_operations(
    self,
    resource_type: str,
    *,
    parent_type: str | None = None,
    parent_uuid: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
```

Neither parent argument reads the root anchor `/rest/api/uom/{resource_type}/operations`;
both read the child anchor
`/rest/api/uom/{parent_type}/{parent_uuid}/{resource_type}/operations`. It reuses two things
rather than rebuilding them: `_request_with_uuid_path_arguments`, which owns UUID validation
and reaches `_reject_dot_segments`, and `_parse_feed` (`client_parse.py:40`), which owns feed
parsing. Nothing here interprets the parsed entries further. *Success* below states the
resulting behaviour criterion by criterion.

The method has no callers in this change. Ownership is unchanged: the generic uom reads
already live on `HMCClient` in `core.py`, so this is a clean extension of an existing
responsibility with no caller migration and no obsolete path to remove.

One more file changes: `CHANGELOG.md` gains a single `## [Unreleased]` / `### Added` bullet.
`HMCClient` is one of ADR 0118's six `hmc_mcp.api` facade names, so a new method on it is
facade movement, and `CONTRIBUTING.md` (*Changelog*) requires recording it. No gate compels
that bullet — `tests/unit/test_changelog.py` checks only that the version declared in
`pyproject.toml` has a matching heading — so it is sourced to the convention, not to the
guardrail criterion.

Out of scope, with owners: MCP tool and CLI surface (#792); capability-ledger reconciliation
against a live HMC (#793); changing the 14 hardcoded `do/{Operation}` call sites or wiring
validation into `submit_job` (epic #785); the sibling discovery reads `/quick`, `/quick/all`,
`/search`, XSD fetch and job feeds (#788, #789, #790, #791).

## Failure model

**Actors and deployments.** In-process Python callers of the `hmc_mcp.api` facade — a library
consumer, this repo's own future MCP/CLI surface (#792, not built here), and the pytest suite.
The method is reachable from no MCP tool, no CLI command and no network listener in this
change. Its `resource_type` and `parent_type` arguments therefore arrive from program text or
from a caller that has already decided what to ask for, never from HMC-facing untrusted input.

**Invariants and assets at stake.**
- The request must address the `/operations` anchor the arguments name and no other HMC
  resource; `HMCClient` sessions carry an authenticated `X-API-Session`.
- `HMCClient` is exported by the ADR 0118 facade, so this signature and return shape become a
  published contract on merge.
- The read is a GET and mutates nothing on the HMC.

**Accepted failure classes.**
- A caller passing a `resource_type` or `parent_type` containing extra path separators reaches
  a different uom GET path. Accepted: identical to the existing `list_uom`, `search_uom` and
  `list_child` contracts, the actors above are in-process, the reach is limited to GET, and
  `_reject_dot_segments` still refuses any path that would resolve upward.
- The exact `Accept` header `/operations` requires, and the element shape of its feed, are
  unconfirmed against live firmware: no reference in this repository documents either.
  Accepted: `*/*` is the one `Accept` that cannot fail negotiation, and a firmware level that
  insists on a typed `Accept` answers 406, which surfaces as `HMCError` carrying 406.
- An HMC that returns malformed XML with HTTP 200 surfaces as `HMCError` from the existing
  `_tag_parse_errors` wrapper. Accepted: that is the established contract for every other
  feed read in this module.

**Covered elsewhere.**
- Comparing documented job operations against a live HMC's `/operations` output — #793
  (`verification:live-hmc`). That issue does **not** cover confirming the `Accept` header the
  endpoint requires or the element shape of its entries: its four acceptance items commit no
  one to either. That confirmation is unowned, and is carried as a follow-up candidate rather
  than claimed here.
- Exposing this read to untrusted MCP/CLI callers, and the access-policy classification that
  would then bind it — #792.
- Response-size bounding — already held by `_read_bounded_response` and ADR 0133 on every
  request this method makes.

## Threat model

**Boundary inventory.** The change adds no trust boundary. It widens one: the set of HMC REST
paths this client will construct from caller-supplied strings now includes the `/operations`
anchors. Data crossing inward is the HMC's response body and headers; data crossing outward is
the request path.

**Actor model.** The untrusted party is the HMC's response, not the caller — `resource_type`
and `parent_type` come from in-process Python (see the failure model's actors), and the HMC is
reached over an authenticated session the caller already established. This design places its
trust in the caller and withholds it from the response body.

**Control per boundary.**
- Outbound path — `_reject_dot_segments`, applied inside `_request` to the raw and
  percent-decoded form of every path, refuses a segment that would resolve away from the
  resource named. `_request_with_uuid_path_arguments` refuses a `parent_uuid` that is not a
  UUID before transport. Neither is new; both are reached by using the existing helper.
- Inbound body — parsed by `defusedxml` through `parse_feed`, with `_tag_parse_errors`
  converting a parse failure into `HMCError` naming the request path. Size is bounded by
  `_read_bounded_response`. Nothing in the body reaches a filesystem path, a command, or a
  subsequent request.
- Inbound headers — one header is read as an opaque string and returned unchanged; it is never
  parsed, compared, or used to build a path.
- Failure disclosure — `HMCError` carries the HMC's status and its body truncated to
  `MAX_ERROR_BODY_BYTES`, the same disclosure every other read in this module makes.

**Explicitly out of scope.** Authorization of *who* may call this read: not reachable in this
deployment, because no MCP tool or CLI command exposes it (#792 owns that, and the access
policy it will need). Validating that a returned operation name is safe to submit: nothing
submits anything here (epic #785). Confidentiality of the operation list: it is firmware
metadata, identical for every caller of a given HMC.

## Success

- `list_operations("ManagedSystem")` issues `GET /rest/api/uom/ManagedSystem/operations` with
  `Accept: */*`, and returns exactly `_parse_feed`'s flattened entries — this change adds no
  parser.
- `list_operations("LogicalPartition", parent_type="ManagedSystem", parent_uuid=<uuid>)`
  issues `GET /rest/api/uom/ManagedSystem/<uuid>/LogicalPartition/operations`.
- The returned schema version equals the response's `X-HMC-Schema-Version`, and is `None` when
  the response carries none.
- A non-200, non-204 status raises `HMCError` whose `status_code` is that status.
- A non-UUID `parent_uuid` raises `ValueError` and sends no request.
- Supplying exactly one of `parent_type` / `parent_uuid` raises `ValueError` and sends no
  request.
- 204 returns `([], schema_version)`.
- A `resource_type` carrying a `..` path segment raises `HMCError` and sends no request,
  because the call reaches `_reject_dot_segments` on the shared request path.
- `just verify` is green, and `uv run --no-sync prek run --all-files` is green.

## Validation

Each behavioural success criterion above is machine-checkable by a respx-mocked case in
`tests/unit/test_client.py`, the module that already owns `HMCClient` transport contracts. The
last criterion is the guardrail one, discharged by the plan's final two steps rather than by a
test.
The per-contract inventory — each contract's mode, test name, expected red failure and exact
focused green command — is Task 1's `Verification` block in
`docs/workflow/plans/2026-09-14-discover-job-operations.md`. One contract there is
`task-test-not-applicable`: the `CHANGELOG.md` bullet, which is prose about a facade addition
that no executable consumer reads.
