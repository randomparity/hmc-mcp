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
landed for `/quick` (PR #800, ADR 0140) and the opt-in validation wiring #799 landed for
`get_quick_property` (PR #805, ADR 0141). Both shapes are reused rather than re-decided.

**The response shape is unknown and this design says so.** The vendored reference corpus carries the
path grammar for both anchors at `docs/refs/hmc-rest-api-p10/000-hmc-rest-apis.md:67-68` and
`docs/refs/hmc-rest-api-p11/000-hmc-rest-apis.md:67-68,84-85`, and nothing else: case-insensitive
searches over the whole corpus for `SearchParameter`, `Search_Collection`, `SearchElement` and
`searchable` return zero hits — no content type, no body example, no element vocabulary. The
solution record above names #789 as one of three sibling reads that will hit exactly this wall. The
fixtures here are therefore **constructed, not captured**, and ADR 0142 records what that costs and
how the design bounds it.

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
completion — a cancelled discovery read caches nothing and is retried on the next validated call,
since caching a cancellation would disable validation for that type on a caller's timeout.
First-time discovery for *different* types is serialized by one client-wide lock: that costs
latency, never a wrong answer. `search_uom` continuing to work at levels where the discovery read
fails (#789 criterion 2 read with criterion 4). The six `hmc_mcp.api` exports.

**Accepted failure classes.**

- **The parsed element name is unverified against firmware.** `_SEARCH_PARAMETER_NAME_ELEMENT` is
  an inference from the sibling `/quick` anchor, not a capture. Accepted for this branch only, and
  bounded three ways: the constant is the single point of change; a wrong guess fails loudly as
  `HMCError` naming the element rather than returning a wrong answer, because a 200 yielding no
  name raises; and `validate=True` degrades to today's behaviour on that `HMCError`, so a wrong
  guess cannot break `search_uom`. The exclusion's owner and the closing evidence are named in
  *Validation*. ADR 0142 records it.
- A stale positive cache wrongly rejects a name a newer level added, for a client held across a
  firmware change. Accepted: unreachable for the per-call CLI and MCP actors, and the remaining
  actor's escape is `validate=False`, the default. Carried from ADR 0141.
- A transient discovery failure is cached as durably as a firmware-level one, so one connection
  blip leaves validation off for that type until a new client is constructed, with no signal.
  Accepted: the degraded state is today's unvalidated behaviour rather than an error, the client is
  per tool call in the CLI and MCP deployments so the window is one call, and re-reading instead
  would spend the per-call request the cost bound rules out. Stated in `search_uom`'s docstring.
- A discovery read that succeeds with *fewer* names than the level serves would make `validate=True`
  reject a working name. Accepted: validation is opt-in and the `False` default is the escape. No
  claim is made about whether any level answers short — this repository has not checked, and the
  acceptance does not rest on it. The *empty* answer is not accepted, because it is reachable: a
  204 returns `([], version)` without raising, and an empty positive set would reject every name
  for the client's lifetime. It is degraded from, exactly as a failed read is.
- Cache growth is unbounded in the number of distinct `resource_type` values passed. Accepted:
  entries are one short string keyed to a frozenset of short strings, resource types come from
  literals in this repository, and the dict dies with the client.

**Covered elsewhere.** Path traversal in `property_name`, `resource_type`, `parent_type` or the
child type: `_reject_dot_segments` (`core.py:115`), which `_request` (`core.py:435`) applies to
every request, `_request` being the only site that builds or sends one. `parent_uuid` validation:
`_request_with_uuid_path_arguments` (`core.py:453`). Percent-encoding of the instance-search
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
4. A resource type the HMC does not recognise surfaces `HMCError` carrying the HMC's status.
   (#789 criterion 4)
5. Across repeated `validate=True` calls on one `HMCClient`, requests to `/rest/api/uom/{R}/search`
   number at most one per distinct `{R}`, whether the first read succeeded or failed; a fresh
   client reads again.
6. When the discovery read yields no names — an `HMCError` from a 4xx or 5xx, an
   `HMCTransportError` from a connection failure, or a 204 returning `([], version)` —
   `validate=True` sends the search anyway and returns its result.
7. `validate` defaults to `False`; a call omitting it makes no discovery request and behaves
   exactly as at `a0d29ac7`. The decision is in ADR 0142 and the parameter in `CHANGELOG.md`.
8. `hmc_mcp.api` still exports exactly the six names ADR 0118 names.

## Validation

All entries are in `tests/unit/test_client.py` unless named otherwise. Every body in the new block
is **constructed, not captured**, and the block head says so.

| Contract | Mode | Evidence |
|---|---|---|
| Both anchors are reached at the documented paths (Success 1) | `focused-test` | `::test_list_search_parameters_reads_both_anchors`, parametrized over the root and child forms |
| Exactly one parent argument is a caller error (Success 1) | `focused-test` | `::test_list_search_parameters_refuses_bad_arguments`, parametrized over each half |
| The names come from the named element and not its siblings (Success 1) | `focused-test` | `::test_list_search_parameters_reads_the_named_element_not_its_siblings` — the fixture carries `RESTElement` and `Description` siblings holding plausible-but-wrong strings, the discriminator the solution record's item 5 requires |
| A single defined parameter returns as a one-element list (Success 1) | `focused-test` | `::test_list_search_parameters_returns_a_single_name_as_a_one_element_list` — the `element_to_dict` collapse ADR 0139 recorded |
| The response's `X-HMC-Schema-Version` is returned verbatim (Success 1) | `focused-test` | `::test_list_search_parameters_returns_the_response_schema_version` |
| A 204 returns no names rather than raising (Success 1, 6) | `focused-test` | `::test_list_search_parameters_204_returns_no_names` |
| A 200 yielding no name raises rather than reporting "defines nothing" (Success 1) | `focused-test` | `::test_list_search_parameters_200_without_a_name_raises`, parametrized over an `HttpErrorResponse` feed and an empty collection |
| An unknown resource type surfaces `HMCError` carrying the status (Success 4) | `focused-test` | `::test_list_search_parameters_unknown_type_raises_hmc_error_with_status` |
| `validate=True` refuses an undefined property before transport (Success 2) | `focused-test` | `::test_search_uom_validate_refuses_an_undefined_property` |
| `validate=True` passes a defined property through and returns its results (Success 2) | `focused-test` | `::test_search_uom_validate_allows_a_defined_property` |
| The names are read once per type per client, sequentially and concurrently (Success 5) | `focused-test` | `::test_search_uom_validate_reads_the_names_once_per_type`, `::test_search_uom_validate_reads_the_names_once_under_concurrency` |
| The cache is keyed per resource type and not shared between clients (Success 5) | `focused-test` | `::test_search_uom_validate_caches_per_resource_type`, `::test_search_uom_validate_rereads_for_a_new_client` |
| A discovery read yielding no names degrades and is cached rather than retried (Success 5, 6) | `focused-test` | `::test_search_uom_validate_degrades_and_caches_the_failure`, parametrized over a 500, a 400, an `httpx.ConnectError` and a 204 |
| The default makes no discovery request and behaves as today (Success 7) | `focused-test` | `::test_search_uom_defaults_to_no_validation` |
| `validate` is keyword-only with default `False` (Success 7) | `focused-test` | `::test_search_uom_validate_is_keyword_only_and_defaults_false` |
| ADR 0142 is a well-formed numbered record (Success 7) | `focused-test` | `just adr-numbering`, which checks the filename, unique number and H1 agreement |
| The HTTP 400 rationale in `search_uom`'s docstring (Success 3) | `task-test-not-applicable` | The contract is prose addressed to a human reader; no executable consumer validates it, and asserting its wording would snapshot prose — the practice the plan's conventions forbid |
| The six facade exports are unchanged (Success 8) | `task-test-not-applicable` | Unchanged by this design, and `tests/unit/test_public_api.py` already fails on any drift; a second test would observe that test's subject, not this change |
| The `CHANGELOG.md` entry (Success 7) | `task-test-not-applicable` | `tests/unit/test_changelog.py` binds only the declared `pyproject.toml` version, which this change does not alter; no executable consumer validates an unreleased entry |
| `_SEARCH_PARAMETER_NAME_ELEMENT` matches firmware (Failure model, entry 1) | `task-test-not-applicable` | Not observable from this repository: no firmware is reachable from CI or a workstation, and a `respx` fixture asserts only against itself. Closing evidence is a live capture from the ppc64le host, owned by the operator and named in the charter's exclusions |
