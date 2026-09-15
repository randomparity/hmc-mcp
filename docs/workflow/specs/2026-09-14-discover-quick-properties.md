# Discover defined quick-property names via `/quick`

Issue #788, under epic #785. Decision record: [ADR 0140](../../adr/0140-quick-property-discovery-reads-atom-nicknames.md).

## Problem

`get_quick_property` fetches one quick property for one instance and takes the property name as a
free string, so a typo is discovered only when the HMC rejects the round trip. The HMC publishes
which names a type defines at `/rest/api/uom/{R}/quick`, and at `/rest/api/uom/{P}/{UUID}/{C}/quick`
for a child type. Neither anchor occurs under `src/`.

## Scope

One new read-only method on `HMCClient` in `src/hmc_mcp/client/core.py`, beside
`get_quick_property`: `list_quick_properties(resource_type, *, parent_type=None, parent_uuid=None)
-> tuple[list[str], str | None]`, whose signature, path grammar and parse ADR 0140 decides. Both
parent arguments together select the child anchor and neither selects the root anchor. The tuple's
**first element is the plain list of names** the issue's outcome asks for; the second is the
response's `X-HMC-Schema-Version`, the pairing ADR 0139 established and named this read as
inheriting. It reuses `_request_with_uuid_path_arguments`, which owns UUID validation and reaches
`_reject_dot_segments`.

The issue's title also names `/quick/all`. That anchor is **not implemented**: the live run recorded
on PR #800 found it answering 400 on both the root and the child form, so there is no
`all_properties` argument and no path this method can build for it. ADR 0140 records the narrowing.

Parsing the `Nickname` elements needs one new primitive, `xmlutil.find_all_text` — the all-matches
sibling of the existing `find_text` — wrapped by `client_parse._find_all_text` so a malformed body
raises `HMCError` naming the call, the tagging contract that module exists for.

Ownership is unchanged — the generic uom reads already live on `HMCClient` in `core.py`, so this is
a clean extension with no caller migration and no obsolete path to remove — and the method has no
callers in this change. `CHANGELOG.md` gains one `### Added` bullet, because `HMCClient` is one of
ADR 0118's six facade names and `CONTRIBUTING.md` requires recording facade movement; no gate
compels it.

Out of scope, with owners: modifying `get_quick_property`, its five call sites, or adding a name
cache behind local validation (#799); MCP and CLI exposure (#792); the bulk quick-property
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
- **The nesting between `<feed>` and `<QuickProperty_Collection>` is unconfirmed.** The live run
  reported the container and the `Nickname` element but not the path between them. Accepted because
  the parse reads `Nickname` document-wide and so does not depend on that path;
  `test_list_quick_properties_reads_names_at_any_depth` pins the independence. A structural capture
  is requested on PR #800 and will replace the reconstructed fixtures; it can confirm the shape but
  cannot invalidate the parse.
- A `resource_type` or `parent_type` containing extra path separators reaches a different uom GET
  path. Accepted: identical to the existing `list_uom`, `search_uom`, `list_child` and
  `list_operations` contracts, the actors above are in-process, the reach is limited to GET, and
  `_reject_dot_segments` still refuses a path that would resolve upward.
- A firmware level offering no `/quick` anchor, or insisting on a typed `Accept`, leaves this method
  with no working call there. Accepted: such a level answers non-200, surfacing as `HMCError`
  carrying that status and the HMC's message. `/operations` behaved that way at V1_20_0 (ADR 0139)
  and nothing here remedies a server-side gap.
- **A type defining genuinely zero quick properties raises instead of returning `[]`.** Accepted
  deliberately: no level has shown such a type, whereas an HMC answering 200 with an
  `HttpErrorResponse` feed is documented (ADR 0139), and an empty list cannot distinguish them.
  ADR 0140's Consequences records the trade.

**Covered elsewhere.**
- Wiring the names into `get_quick_property`, and whatever cache that needs — #799.
- Exposing this read to untrusted MCP/CLI callers and its access-policy classification — #792.
- Response-size bounding — `_read_bounded_response` and ADR 0133, on every request this makes.
- **Live confirmation of the anchors: done.** Performed by the operator on a separate host and
  recorded on PR #800 (FW950/P10, schema `V1_0`): both `/quick` anchors answered 200 with an Atom
  `QuickProperty_Collection`, returning 39 names for `ManagedSystem` and 28 for `LogicalPartition`;
  both `/quick/all` anchors answered 400; `/quick/All` answered 200 with per-instance JSON value
  objects, which also retires the confirmation ADR 0138 had been owed since PR #795. What that run
  did not capture is the exact element nesting, the first accepted failure class above.

## Threat model

Read against the failure model above rather than restating it.

**Boundaries.** None added; one widened — the HMC REST paths this client builds from caller-supplied
strings now include the `/quick` anchors. Inbound is the response body and one header; outbound is
the request path. **Actors.** The untrusted party is the HMC's response, not the caller: the failure
model's first entry names the callers, all in-process, over a session they already authenticated.

**Controls**, none of them new, all reached by using the existing helpers: `_reject_dot_segments`
inside `_request` refuses raw and percent-encoded dot-segments and
`_request_with_uuid_path_arguments` refuses a non-UUID `parent_uuid`, both pre-transport; the body is
parsed by `defusedxml` through `xmlutil`, so entity-expansion and external-entity attacks are refused
by that library rather than by anything written here, and a `DefusedXmlException` surfaces as
`HMCError` through the same `client_parse` wrapper as a `ParseError`; nothing in the body reaches a
filesystem path, a command, or a later request here; the one header is returned unchanged, never
parsed or used in a path; and every raise carries the status and the body truncated to
`MAX_ERROR_BODY_BYTES`, which `HMCError.__init__` owns.

**Out of scope.** Authorization of who may call this read (#792, not reachable in this deployment);
validating a returned name before a later `get_quick_property` call (#799, nothing calls it here);
confidentiality of the name list, firmware metadata identical for every caller.

## Success

- `list_quick_properties("ManagedSystem")` issues `GET /rest/api/uom/ManagedSystem/quick` with
  `Accept: */*` and returns the `Nickname` texts as the first tuple element.
- Both parent arguments issue `GET /rest/api/uom/{parent_type}/{parent_uuid}/{resource_type}/quick`
  and return the same.
- The method has no `all_properties` parameter and builds no `/quick/all` path.
- A response carrying one `QuickProperty` returns a one-element list, not a bare string.
- The names are found regardless of how deeply `QuickProperty_Collection` is nested.
- The second tuple element equals the response's `X-HMC-Schema-Version` verbatim, `None` when none
  is sent — including a non-version value such as the observed `hmc-mcp`.
- A malformed body raises `HMCError` naming the call, not a bare `ParseError`.
- A 200 carrying no non-empty `Nickname` — an `HttpErrorResponse` feed, an empty collection, or only
  empty names — raises `HMCError` carrying status 200 and the body. An empty `Nickname` among
  populated ones is dropped rather than returned.
- A non-200, non-204 status raises `HMCError` whose `status_code` is that status; 204 returns
  `([], schema_version)`.
- A non-UUID `parent_uuid`, exactly one of the two parent arguments, or a `..` segment in
  `resource_type` or `parent_type` each raise before transport — `ValueError` for the first two,
  `HMCError` for the third — and send no request.
- `just verify` is green, and `uv run --no-sync prek run --all-files` is green.

## Validation

Every behavioural criterion above is machine-checkable by a respx-mocked case in
`tests/unit/test_client.py`, which already owns `HMCClient` transport contracts, except
`find_all_text` itself, covered in `tests/unit/test_xmlutil.py`, and its error tagging, covered in
`tests/unit/test_client_parse.py`. Task 1's `Verification` block in
`docs/workflow/plans/2026-09-14-discover-quick-properties.md` is the per-contract inventory — mode,
test name, red failure, focused green command — and its one `task-test-not-applicable` contract is
the `CHANGELOG.md` bullet. The last criterion is the guardrail one, discharged by the plan's final
steps.

**Fixture provenance.** The bodies in the `list_quick_properties` block are *reconstructed* from the
live run's report, not captured from it: the container shape and the property names are the run's,
the element nesting is this repository's reading of it. The block says so at its head. A structural
capture is requested on PR #800 and replaces them when it arrives — the same two-step PR #797 took
for `/operations`.
