# Discover defined quick-property names via `/quick` and `/quick/all`

Issue #788, under epic #785. Decision record: [ADR 0140](../../adr/0140-quick-property-discovery-returns-a-json-name-list.md).

## Problem

`get_quick_property` fetches one quick property for one instance and takes the property name as a
free string, so a typo is discovered only when the HMC rejects the round trip. The HMC publishes
which names a type defines at `/rest/api/uom/{R}/quick` and `/rest/api/uom/{R}/quick/all`, and at
`/rest/api/uom/{P}/{UUID}/{C}/quick[/all]` for a child type. Neither anchor occurs under `src/`.

## Scope

One new read-only method on `HMCClient` in `src/hmc_mcp/client/core.py`, beside
`get_quick_property`: `list_quick_properties(resource_type, *, all_properties=False,
parent_type=None, parent_uuid=None) -> tuple[list[str], str | None]`, whose signature, path grammar
and parse ADR 0140 decides. `all_properties` selects `/quick/all` over `/quick`; both parent
arguments together select the child anchor and neither selects the root anchor. The tuple's **first
element is the plain list of names** the issue's outcome asks for; the second is the response's
`X-HMC-Schema-Version`, the pairing ADR 0139 established and named this read as inheriting. It
reuses `_request_with_uuid_path_arguments`, which owns UUID validation and reaches
`_reject_dot_segments`.

Ownership is unchanged — the generic uom reads already live on `HMCClient` in `core.py`, so this is
a clean extension with no caller migration and no obsolete path to remove — and the method has no
callers in this change. `CHANGELOG.md` gains one `### Added` bullet, because `HMCClient` is one of
ADR 0118's six facade names and `CONTRIBUTING.md` requires recording facade movement; no gate
compels it.

Out of scope, with owners: modifying `get_quick_property`, its five call sites, or adding a name
cache behind local validation (epic #785); MCP and CLI exposure (#792); the bulk quick-property
**value** fetch (#244); the sibling discovery reads (#789, #790, #791); ledger reconciliation (#793).

## Failure model

**Actors and deployments.** In-process Python callers of the `hmc_mcp.api` facade: a library
consumer, this repository's future MCP/CLI surface (#792, not built here), and the pytest suite. No
MCP tool, CLI command, or network listener reaches this method in this change, so `resource_type`
and `parent_type` arrive from program text, never from HMC-facing untrusted input.

**Invariants and assets at stake.**
- The request addresses the `/quick` anchor the arguments name and no other HMC resource; the
  session carries an authenticated `X-API-Session`. The read is a GET and mutates nothing.
- The signature and return shape become a published contract on merge (ADR 0118 facade).
- A returned name is meant to build a later `get_quick_property` path, so a value that is not a name
  the HMC defined is worse than an error.

**Accepted failure classes.**
- **The live response shape of both anchors is unverified at authoring time.** The reference
  documents them as returning defined names; ADR 0140's Context records the case asymmetry behind
  that. Accepted because the strict parse makes the branch loud — a non-string element raises
  `HMCError` carrying the status, body and observed shape, so no caller receives objects labelled as
  names. Confirmation is owed; see *Covered elsewhere*.
- A `resource_type` or `parent_type` containing extra path separators reaches a different uom GET
  path. Accepted: identical to the existing `list_uom`, `search_uom`, `list_child` and
  `list_operations` contracts, the actors above are in-process, the reach is limited to GET, and
  `_reject_dot_segments` still refuses a path that would resolve upward.
- A firmware level offering neither anchor, insisting on a typed `Accept`, or not supporting
  `all_properties=True` on the child anchor leaves this method with no working call there. Accepted:
  such a level either answers non-200, surfacing as `HMCError` carrying that status and the HMC's
  message, or returns a non-array body, which the strict parse rejects. `/operations` behaved the
  first way at V1_20_0 (ADR 0139) and nothing here remedies a server-side gap.

**Covered elsewhere.**
- Wiring the names into `get_quick_property`, and whatever cache that needs — epic #785.
- Exposing this read to untrusted MCP/CLI callers and its access-policy classification — #792.
- Response-size bounding — `_read_bounded_response` and ADR 0133, on every request this makes.
- Live confirmation of both anchors against a real HMC: **owned by no issue**. It is performed on
  this PR by the operator on a separate host, as `/operations` was confirmed on PR #797, and the
  findings are recorded on the PR and in ADR 0140.

## Threat model

Read against the failure model above rather than restating it.

**Boundaries.** None added; one widened — the HMC REST paths this client builds from caller-supplied
strings now include the `/quick` anchors. Inbound is the response body and one header; outbound is
the request path. **Actors.** The untrusted party is the HMC's response, not the caller: the failure
model's first entry names the callers, all in-process, over a session they already authenticated.

**Controls**, none of them new, all reached by using the existing helper: `_reject_dot_segments`
inside `_request` refuses raw and percent-encoded dot-segments and
`_request_with_uuid_path_arguments` refuses a non-UUID `parent_uuid`, both pre-transport; the body is
decoded by `json` and type-checked element by element before any value is returned, and nothing in
it reaches a filesystem path, a command, or a later request here; the one header is returned
unchanged, never parsed or used in a path; and every raise carries the status and the body truncated
to `MAX_ERROR_BODY_BYTES`, as the rest of this module does.

**Out of scope.** Authorization of who may call this read (#792, not reachable in this deployment);
validating a returned name before a later `get_quick_property` call (epic #785, nothing calls it
here); confidentiality of the name list, firmware metadata identical for every caller.

## Success

- `list_quick_properties("ManagedSystem")` issues `GET /rest/api/uom/ManagedSystem/quick` with
  `Accept: */*` and returns the decoded JSON array as the first tuple element; `all_properties=True`
  issues the same path with `/all` appended.
- Both parent arguments issue `GET /rest/api/uom/{parent_type}/{parent_uuid}/{resource_type}/quick`,
  and with `all_properties=True`, that path with `/all` appended.
- The second tuple element equals the response's `X-HMC-Schema-Version`, `None` when none is sent.
- A 200 body that is not a JSON array of strings — invalid JSON, an object, a scalar, or an array
  holding any non-string element — raises `HMCError` naming the observed shape and carrying the
  response status and body, and returns nothing.
- A non-200, non-204 status raises `HMCError` whose `status_code` is that status. 204 returns
  `([], schema_version)`; a 200 whose body is `[]` returns the same.
- A non-UUID `parent_uuid`, exactly one of the two parent arguments, or a `..` segment in
  `resource_type` or `parent_type` each raise before transport — `ValueError` for the first two,
  `HMCError` for the third — and send no request.
- `just verify` is green, and `uv run --no-sync prek run --all-files` is green.

## Validation

Every behavioural criterion above is machine-checkable by a respx-mocked case in
`tests/unit/test_client.py`, which already owns `HMCClient` transport contracts. Task 1's
`Verification` block in `docs/workflow/plans/2026-09-14-discover-quick-properties.md` is the
per-contract inventory — mode, test name, red failure, focused green command — and its one
`task-test-not-applicable` contract is the `CHANGELOG.md` bullet. The last criterion is the guardrail
one, discharged by the plan's final steps.
