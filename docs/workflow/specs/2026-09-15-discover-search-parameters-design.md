# Discover valid search parameters via /search — design

Issue [#789](https://github.com/randomparity/hmc-mcp/issues/789), under epic
[#785](https://github.com/randomparity/hmc-mcp/issues/785). Decision record:
[ADR 0142](../../adr/0142-search-parameter-discovery-parses-one-named-element.md). Prior records
read: [ADR 0118](../../adr/0118-core-library-facade.md),
[ADR 0139](../../adr/0139-discovery-reads-return-feed-and-schema-version.md),
[ADR 0140](../../adr/0140-quick-property-discovery-reads-atom-nicknames.md),
[ADR 0141](../../adr/0141-quick-property-name-validation-is-opt-in.md). Prior solution record read:
[fixtures invented for an endpoint never spoken](../../solutions/2026-09-14-fixtures-invented-for-an-endpoint-never-spoken.md).

## Problem

`HMCClient.search_uom` (`src/hmc_mcp/client/core.py:859`) interpolates `property_name` into
`/rest/api/uom/{R}/search/({P}=={V})` and sends it, so an unsupported property is discovered only as
an HTTP 400 from the HMC. The type-anchored `/rest/api/uom/{R}/search` anchor, which answers what
properties a search may use, has no reader anywhere under `src/`, and neither does its child form.

#789 is the union of two already-merged siblings applied to `/search`: the discovery read #788
landed for `/quick` (PR #803, ADR 0140) and the opt-in validation wiring #799 landed for
`get_quick_property` (PR #805, ADR 0141). Both shapes are reused rather than re-decided.

**The response shape is unknown and this design says so.** The vendored reference corpus describes
the anchor — path grammar for both forms, and per-type prose pointing at it — but never its
response: no content type, no body example, no element vocabulary. ADR 0142 carries the citations
and the searches that establish those three absences. The solution record above names #789 as one of
three sibling reads that will hit exactly this wall. The fixtures here are therefore **constructed,
not captured**, and ADR 0142 records what that costs and how the design bounds it.

## Scope

One unit: a new discovery read, a per-client name cache, and an opt-in validation branch in
`src/hmc_mcp/client/core.py`, plus the record and changelog entry facade movement requires.

**Ownership.** `core.py` already owns `search_uom`, both sibling discovery reads
(`list_operations`, `list_quick_properties`) and the client's per-instance state, so this is a clean
extension of the current owner: no responsibility moves, no caller migrates, no path becomes
obsolete, and no compatibility path is retained. The six production call sites
(`client_lpars.py:60`, `client_systems.py:179,251`, `operations/vios/core.py:42`,
`operations/systems/core.py:44`, `operations/lpar/core.py:96`) are untouched — the new behaviour is
unreachable without passing `validate=True`, which none of them does. The two `search_uom`
declarations in `client_contracts.py` (lines 89, 319) are also untouched: a keyword-only parameter
with a default widens the implementation without narrowing the protocol, so both declarations stay
satisfied. `hmc_mcp.api`'s six exports (ADR 0118) are a protected contract and stay as they are.

**Changes.** A module-level `_SEARCH_PARAMETER_NAME_ELEMENT = "Nickname"` isolates the one
unverified assumption. `list_search_parameters(resource_type, *, parent_type=None,
parent_uuid=None)` reads `/rest/api/uom/{R}/search`, or
`/rest/api/uom/{P}/{UUID}/{C}/search` when both parent arguments are given, sends `Accept: */*`,
and returns `(names, schema_version)` — the texts of that element read document-wide via
`_find_all_text`, paired with the response's `X-HMC-Schema-Version`. A 204 returns
`([], schema_version)`; a non-200 raises `HMCError` carrying the status; a 200 yielding no name
raises `HMCError` naming the element it looked for. `HMCClient.__init__` gains
`self._search_parameter_names: dict[str, frozenset[str] | None] = {}` and
`self._search_parameter_names_lock = asyncio.Lock()`. A private
`_defined_search_parameter_names(resource_type)` reads the root anchor once per type per client,
storing `frozenset(names)` when it yields names and `None` otherwise. `search_uom` gains
keyword-only `validate: bool = False`; when true it consults that helper and raises `ValueError`
before building the path if the set is non-`None` and lacks `property_name`. `CHANGELOG.md` gains
one `## [Unreleased] / ### Added` entry.

**Out of scope, with owners.** MCP and CLI exposure of the discovery calls (#792); capability-ledger
rows (#794); the XSD anchor (#790) and job feeds (#791); live `/operations` reconciliation (#793).
Live-firmware confirmation of the response shape is the operator's, on the ppc64le live-test host,
after this PR is pushed. No parent-anchored validation — `search_uom` reads a root-anchored path,
so parent arguments would describe a resource it does not address, the same ground ADR 0141 gave.

## Failure model

**Actors and deployments.** A local operator running the `hmc-mcp` CLI; an MCP client calling a
tool, where `_app.with_client` opens and closes one `HMCClient` per call; a Python caller holding an
`HMCClient` across several reads, including one issuing validated calls concurrently on that client
— the only actor for whom the cache saves a request and the only one whose cache can go stale; CI,
which exercises this code only against `respx` mocks and never against firmware.

**Invariants and assets at stake.** `search_uom`'s default behaviour, which ADR 0118's facade
exposes through `HMCClient`, and which six in-repo call sites depend on. The cost bound stated to
callers: at most one discovery request per resource type per client session, failures included,
holding for concurrent callers as well as sequential ones, and covering calls that run to
completion — a discovery read cancelled by `asyncio.CancelledError` caches nothing and is retried on
the next validated call, because `CancelledError` is a `BaseException` that the helper's
`except HMCError` does not catch. A *timeout* is not that case: `_request` converts
`httpx.TimeoutException` to `HMCTransportError` (`core.py:451-456`), which the helper does catch and
cache, exactly as accepted class 3 below states. First-time discovery for *different* types is
serialized by one client-wide lock: that costs latency, never a wrong answer. `search_uom`
continuing to work at levels where the discovery read fails, which is the pre-flight's own
degradation promise rather than any numbered criterion of #789. The six `hmc_mcp.api` exports.

**Accepted failure classes.**

- **The parsed element name is unverified against firmware.** Accepted for this branch only, on the
  bounding properties ADR 0142 records and with the closing evidence named in *Validation*. One of
  those properties is bounded, not absolute: a wrong guess matching *nothing* fails loudly, but a
  wrong guess matching some other element returns a plausible wrong set, which reaches an opted-in
  caller as `ValueError` on a legitimate property rather than through the degradation path. That
  consequence is the *wrong-set* class below, whose acceptance holds on its own grounds.
- A stale positive cache wrongly rejects a name a newer level added, for a client held across a
  firmware change. Accepted: unreachable for the per-call CLI and MCP actors, and the remaining
  actor's escape is `validate=False`, the default. Carried from ADR 0141.
- A transient discovery failure is cached as durably as a firmware-level one, so one connection
  blip leaves validation off for that type until a new client is constructed, with no signal.
  Accepted: the degraded state is today's unvalidated behaviour rather than an error, the client is
  per tool call in the CLI and MCP deployments so the window is one call, and re-reading instead
  would spend the per-call request the cost bound rules out. Stated in `search_uom`'s docstring.
- **The wrong-set class.** A discovery read that succeeds with the *wrong* names — fewer than the
  level serves, or the texts of an element that is not the one holding parameter names — makes
  `validate=True` reject a working property. Accepted: validation is opt-in and the `False` default
  is the escape, so no caller who has not asked for the check can be broken by it. No claim is made
  about whether any level answers short; this repository has not checked, and the acceptance does
  not rest on it. The *empty* answer is not accepted, because it is reachable: a 204 returns
  `([], version)` without raising, and an empty positive set would reject every name for the
  client's lifetime. It is degraded from, exactly as a failed read is.
- Cache growth is unbounded in the number of distinct `resource_type` values passed. Accepted:
  resource types come from literals in this repository and the dict dies with the client.
  **The per-entry size is bounded by `HMC_MAX_RESPONSE_BYTES`, not by the names being short** —
  that reason would assume the parse is right, which the first accepted class declines to assume.
  A level answering the query-less root anchor with an instance feed would fill one entry with
  per-instance data up to the configured response ceiling (ADR 0133). The entry still dies with the
  client; what is *not* accepted is rendering it: `search_uom`'s refusal message enumerates at most
  `_MAX_REPORTED_NAMES` names and summarizes the rest, so a wrong parse cannot turn one rejection
  into a response-sized string, nor put operator instance names in it.

**Covered elsewhere.** Path traversal in `property_name`, `resource_type`, `parent_type` or the
child type: `_reject_dot_segments` (`core.py:115`), which `_request` (`core.py:436`) applies to
every request, `_request` being the only site that builds or sends one. `parent_uuid` validation:
`_request_with_uuid_path_arguments` (`core.py:462`). Percent-encoding of the instance-search
grammar: `search_uom`'s existing `quote(..., safe="")` calls, pinned by
`tests/unit/test_client.py:824`. Response-body bounding: ADR 0133. MCP and CLI exposure: #792.

Not security-relevant, so no threat model: the change adds no entry point, no authn/authz or
tenancy logic, no secret, no new deserialization mechanism (`_find_all_text` already exists,
unchanged), no new non-literal in a constructed path beyond the ones `_reject_dot_segments` already
governs, no permission grant and no dependency. It narrows what reaches the transport. `$quest`
step 6 re-judges this against the actual diff.

## Success

1. `list_search_parameters(R)` reads `/rest/api/uom/{R}/search`, and
   `list_search_parameters(C, parent_type=P, parent_uuid=U)` reads
   `/rest/api/uom/{P}/{U}/{C}/search`; supplying exactly one parent argument raises `ValueError`.
   (#789 criterion 1)
2. `search_uom(..., validate=True)` raises `ValueError` without sending a request to
   `/rest/api/uom/{R}/search/({P}=={V})` when `{P}` is not among the names
   `list_search_parameters` returns for `{R}`. (#789 criterion 2)
3. `search_uom`'s docstring records that an unsupported search property yields HTTP 400 from the
   HMC, so the reason the pre-flight exists survives. (#789 criterion 3)
4. A resource type the HMC does not recognise surfaces `HMCError` carrying the HMC's status — from
   `list_search_parameters`' own non-200 raise, and on the `validate=True` path from `_get`'s
   non-200 raise for the instance search (`core.py:505-506`). (#789 criterion 4)
5. Across repeated `validate=True` calls on one `HMCClient` **that run to completion**, requests to
   `/rest/api/uom/{R}/search` number at most one per distinct `{R}`, whether the first read
   succeeded or failed; a fresh client reads again. A read cancelled by `asyncio.CancelledError`
   caches nothing and is retried, as the failure model states.
6. `list_search_parameters` sends `Accept: */*` on both anchors, exactly, because the anchor's
   content type is unobserved and a typed uom Accept would be a second guess stacked on the first.
7. When the discovery read yields no names — an `HMCError` from a 4xx or 5xx, an
   `HMCTransportError` from a connection failure, or a 204 returning `([], version)` —
   `validate=True` sends the search anyway and returns its result.
8. `validate` defaults to `False`; a call omitting it makes no discovery request and behaves
   exactly as at `a0d29ac7`. The decision is in ADR 0142 and the parameter in `CHANGELOG.md`.
9. `hmc_mcp.api` still exports exactly the six names ADR 0118 names.

## Validation

Every success criterion above is covered by a `focused-test` entry in the implementation plan's
Verification table
([plan](../plans/2026-09-15-discover-search-parameters.md), *Task 1 — Verification*), which is the
single inventory: it names each contract, its test, and the red that test shows before the
behaviour exists. It is not restated here. Every body in the new test block is **constructed, not
captured**, and the block head says so.

Three contracts carry `Mode: task-test-not-applicable`, and the reason is a design judgment rather
than a plan mechanic, so it is recorded here:

- **The HTTP 400 rationale in `search_uom`'s docstring** (Success 3). Prose addressed to a human
  reader. No executable consumer validates it, and asserting its wording would snapshot prose.
- **The six facade exports are unchanged** (Success 9). Unchanged by this design, and
  `tests/unit/test_public_api.py` already fails on any drift; a second test would observe that
  test's subject rather than this change.
- **The `CHANGELOG.md` entry** (Success 8). `tests/unit/test_changelog.py` binds only the declared
  `pyproject.toml` version, which this change does not alter; no executable consumer validates an
  unreleased entry.

One contract is **not observable from this repository at all**: that
`_SEARCH_PARAMETER_NAME_ELEMENT` matches firmware (*Failure model*, entry 1). No firmware is
reachable from CI or a workstation, and a `respx` fixture asserts only against itself — a mutation
score over these tests measures whether they discriminate between implementations given the
fixture, and must not be reported as fixture validation. The closing evidence is a live capture from
the ppc64le host, owned by the operator and named in the charter's exclusions.
