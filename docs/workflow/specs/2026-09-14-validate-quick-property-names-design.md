# Validate quick-property names before transport — design

Issue: [#799](https://github.com/randomparity/hmc-mcp/issues/799), under epic
[#785](https://github.com/randomparity/hmc-mcp/issues/785).
Decision record: [ADR 0141](../../adr/0141-quick-property-name-validation-is-opt-in.md).
Prior records read: [ADR 0118](../../adr/0118-core-library-facade.md),
[ADR 0139](../../adr/0139-discovery-reads-return-feed-and-schema-version.md),
[ADR 0140](../../adr/0140-quick-property-discovery-reads-atom-nicknames.md).

## Problem

`HMCClient.get_quick_property` (`src/hmc_mcp/client/core.py:692`) interpolates `property_name`
into `/rest/api/uom/{R}/{uuid}/quick/{property_name}` and sends it. A misspelled name round-trips
to the HMC and comes back as an `HMCError`.

PR #803 landed `HMCClient.list_quick_properties` (`src/hmc_mcp/client/core.py:717`), which returns
the names a resource type defines. At `fac7c19e` it has no caller anywhere under `src/`, and ADR
0140's *Consequences* assigns wiring it in to this issue. So the client can ask the HMC which names
exist and nothing does.

One premise in the issue body is superseded by live evidence and does not govern here: there is no
`/quick/all` bulk-name path. ADR 0140 records FW950 answering 400 on both its anchors, and
`list_quick_properties` has no `all_properties` argument. Validation is against single-anchor names.

## Scope

One unit: a per-client name cache and an opt-in validation branch in
`src/hmc_mcp/client/core.py`, plus the record and the changelog entry that facade movement requires.

### Ownership

`src/hmc_mcp/client/core.py` already owns both methods and the client's per-instance state, so this
is a clean extension of the current owner: no responsibility moves, no caller migrates, and no path
becomes obsolete. The five call sites (`src/hmc_mcp/operations/vios/core.py:99`,
`src/hmc_mcp/operations/lpar/core.py:140,417,481`,
`src/hmc_mcp/operations/lpar/decommission.py:542`) are unchanged: the new behaviour is reachable
only through a parameter none of them passes.

`hmc_mcp.api`'s six exported names (ADR 0118) are a protected contract and stay exactly as they are;
`tests/unit/test_public_api.py` is read, not edited.

### Changes

- `HMCClient.__init__` gains `self._quick_property_names: dict[str, frozenset[str] | None] = {}`.
- A private `HMCClient._defined_quick_property_names(resource_type)` reads
  `list_quick_properties(resource_type)` on a cache miss, stores `frozenset(names)` on success and
  `None` on any `HMCError`, and returns the cached value.
- `get_quick_property` gains keyword-only `validate: bool = False`. When true it consults that
  helper and raises `ValueError` before building the path if the set is non-`None` and does not
  contain `property_name`. The error names the type, the rejected name, and the defined names.
- `CHANGELOG.md` gains an entry under the unreleased heading for the added parameter.

Out of scope, with owners: bulk quick-property **value** fetch (#244); `/quick/all` (does not
exist); MCP and CLI exposure of the discovery calls (#792); the `/operations`, `/search`, XSD and
job-feed anchors (#787, #789, #790, #791). No parent-anchored validation: ADR 0141 rejects adding
parent arguments to `get_quick_property`.

### Failure model

**Actors and deployments.**

- A local operator running the `hmc-mcp` CLI against a configured HMC.
- An MCP client calling a tool, where `_app.with_client` opens and closes one `HMCClient` per call.
- A Python caller holding an `HMCClient` across several reads — the only actor for whom the cache
  saves a request, and the only one with a cache that can go stale.
- CI, which exercises this code only against `respx` mocks.

**Invariants and assets at stake.**

- `get_quick_property`'s default behaviour, which ADR 0118's facade exposes: unchanged by this
  design, because `validate` defaults to `False`.
- The cost bound stated to callers: at most one discovery request per resource type per client
  session, failures included.
- `get_quick_property` keeps working at firmware levels where the discovery read fails
  (issue #799 criterion 4).
- The six `hmc_mcp.api` exports (ADR 0118).

**Accepted failure classes.**

- A stale positive cache wrongly rejects a name a newer firmware level added, for a client held
  across a firmware change. Accepted: unreachable for the per-call MCP and CLI actors, and the
  escape for the remaining actor is `validate=False`, which is the default. ADR 0141 records it.
- Concurrent validated calls for the same type, before the first discovery read returns, each issue
  their own read. Accepted: bounded by the number of calls already in flight, idempotent, and
  costs at most one extra GET in a client whose dominant lifetime is one tool call.
- A discovery read that succeeds but returns fewer names than the level actually serves would make
  `validate=True` reject a working name. Accepted: no level has shown it — ADR 0140 records a 200
  with no names at all as the observed bad shape, which `list_quick_properties` already raises on
  and this design degrades from — and the escape is again the `False` default.
- Cache growth is unbounded in the number of distinct `resource_type` values a caller passes.
  Accepted: entries are one short string keyed to a frozenset of short strings, resource types come
  from literals in this repository, and the dict dies with the client.

**Covered elsewhere.**

- Path-traversal in `property_name` or `resource_type`: `_reject_dot_segments`
  (`src/hmc_mcp/client/core.py:115`), which every request passes through.
- UUID validation for `uuid`: `_request_with_uuid_path_arguments`
  (`src/hmc_mcp/client/core.py:453`).
- The shape, parsing and error behaviour of the discovery read itself: ADR 0140 and the
  `list_quick_properties` tests at `tests/unit/test_client.py:2365-2811`.
- Exposing discovery through MCP and CLI: #792.

This change is not security-relevant, so it carries no threat model: it adds no entry point, no
authentication, authorization or tenancy logic, no secret, no deserialization of foreign input
(the discovery parse already exists and is unchanged), no new non-literal in a constructed path
beyond the `resource_type` the method already interpolated, no permission grant and no dependency.
It narrows what reaches the transport rather than widening it. `$quest` step 6 re-judges this
against the actual diff.

## Success

1. `get_quick_property(..., validate=True)` raises `ValueError` without sending a request to
   `/rest/api/uom/{R}/{uuid}/quick/{name}` when `name` is not among the names
   `list_quick_properties` returns for `{R}`. (#799 criterion 1)
2. Across repeated `validate=True` calls on one `HMCClient`, the number of requests to
   `/rest/api/uom/{R}/quick` is at most one per distinct `{R}`, whether the first read succeeded
   or failed. (#799 criterion 2)
3. A fresh `HMCClient` performs the discovery read again on first validated use; no entry is
   invalidated or refreshed during a client's lifetime. (#799 criterion 3, ADR 0141)
4. When the discovery read raises `HMCError` — including `HMCTransportError` — `validate=True`
   sends the quick-property request anyway and returns its result. (#799 criterion 4)
5. `validate` defaults to `False`, and a call that omits it makes no discovery request and behaves
   exactly as it does at `fac7c19e`. The decision is recorded in ADR 0141 and the added parameter
   in `CHANGELOG.md`. (#799 criterion 5)
6. `hmc_mcp.api` still exports exactly the six names ADR 0118 names.

## Validation

- **Contract: `validate=True` refuses an undefined name before transport.**
  Mode: `focused-test` — `tests/unit/test_client.py::test_get_quick_property_validate_refuses_an_undefined_name`.
- **Contract: `validate=True` passes a defined name through and returns its value.**
  Mode: `focused-test` — `tests/unit/test_client.py::test_get_quick_property_validate_allows_a_defined_name`.
- **Contract: the discovered names are cached per type for the client's lifetime.**
  Mode: `focused-test` — `tests/unit/test_client.py::test_get_quick_property_validate_reads_the_names_once_per_type`.
- **Contract: a fresh client re-reads; nothing is shared between clients.**
  Mode: `focused-test` — `tests/unit/test_client.py::test_get_quick_property_validate_rereads_for_a_new_client`.
- **Contract: a failed discovery read degrades to unvalidated behaviour.**
  Mode: `focused-test` — `tests/unit/test_client.py::test_get_quick_property_validate_degrades_when_discovery_fails`.
- **Contract: the degraded state is cached, not retried per call.**
  Mode: `focused-test` — `tests/unit/test_client.py::test_get_quick_property_validate_caches_a_failed_discovery`.
- **Contract: `validate` defaults to `False` and the default path makes no discovery request.**
  Mode: `focused-test` — `tests/unit/test_client.py::test_get_quick_property_defaults_to_no_validation`
  and `::test_get_quick_property_validate_is_keyword_only_and_defaults_false`.
- **Contract: ADR 0118's six facade exports are unchanged.**
  Mode: `task-test-not-applicable` — the contract is unchanged by this design and
  `tests/unit/test_public_api.py` already fails on any drift in it; a second test asserting the
  same six names would observe that existing test's subject, not this change.
- **Contract: ADR 0141 is a well-formed numbered record.**
  Mode: `focused-test` — `just adr-numbering`, which checks the filename, the unique number and the
  H1 agreement for `docs/adr/0141-quick-property-name-validation-is-opt-in.md`.
- **Contract: the `CHANGELOG.md` entry.**
  Mode: `task-test-not-applicable` — `tests/unit/test_changelog.py` binds only the declared
  `pyproject.toml` version, which this change does not alter, and no executable consumer validates
  an unreleased entry's prose. Asserting its wording would be a snapshot of prose.
