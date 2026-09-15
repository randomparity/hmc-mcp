# Validate quick-property names before transport — design

Issue [#799](https://github.com/randomparity/hmc-mcp/issues/799), under epic
[#785](https://github.com/randomparity/hmc-mcp/issues/785). Decision record:
[ADR 0141](../../adr/0141-quick-property-name-validation-is-opt-in.md). Prior records read:
[ADR 0118](../../adr/0118-core-library-facade.md),
[ADR 0139](../../adr/0139-discovery-reads-return-feed-and-schema-version.md),
[ADR 0140](../../adr/0140-quick-property-discovery-reads-atom-nicknames.md).

## Problem

`HMCClient.get_quick_property` (`src/hmc_mcp/client/core.py:692`) interpolates `property_name` into
`/rest/api/uom/{R}/{uuid}/quick/{property_name}` and sends it, so a misspelled name round-trips to
the HMC and returns as an `HMCError`. PR #803 landed `list_quick_properties`
(`src/hmc_mcp/client/core.py:717`), which returns the names a type defines; at `fac7c19e` it has no
caller anywhere under `src/`, and ADR 0140 assigns wiring it in to this issue.

One premise in the issue body is superseded by live evidence: there is no `/quick/all` bulk-name
path — ADR 0140 records FW950 answering 400 on both its anchors, and `list_quick_properties` has no
`all_properties` argument. Validation is against single-anchor names.

## Scope

One unit: a per-client name cache and an opt-in validation branch in `src/hmc_mcp/client/core.py`,
plus the record and changelog entry facade movement requires.

**Ownership.** `core.py` already owns both methods and the client's per-instance state, so this is a
clean extension of the current owner: no responsibility moves, no caller migrates, no path becomes
obsolete, and no compatibility path is retained. The five call sites
(`operations/vios/core.py:99`, `operations/lpar/core.py:140,417,481`,
`operations/lpar/decommission.py:542`) are untouched — the new behaviour is unreachable without
passing `validate=True`, which none of them does. `hmc_mcp.api`'s six exports (ADR 0118) are a
protected contract and stay as they are; `tests/unit/test_public_api.py` is read, not edited.

**Changes.** `HMCClient.__init__` gains `self._quick_property_names: dict[str, frozenset[str] |
None] = {}`. A private `_defined_quick_property_names(resource_type)` reads
`list_quick_properties(resource_type)` on a cache miss, storing `frozenset(names)` when it yields
names and `None` otherwise — on any `HMCError`, and on a successful read carrying no names, which
is what a 204 returns. `get_quick_property` gains keyword-only `validate: bool = False`; when
true it consults that helper and raises `ValueError` before building the path if the set is
non-`None` and lacks `property_name`, naming the type, the rejected name and the defined names.
`CHANGELOG.md` gains one `## [Unreleased] / ### Added` entry.

**Out of scope, with owners.** Bulk quick-property **value** fetch (#244); `/quick/all` (does not
exist); MCP and CLI exposure of the discovery calls (#792); the `/operations`, `/search`, XSD and
job-feed anchors (#787, #789, #790, #791). No parent-anchored validation — ADR 0141 rejects adding
parent arguments to `get_quick_property`.

## Failure model

**Actors and deployments.** A local operator running the `hmc-mcp` CLI; an MCP client calling a
tool, where `_app.with_client` opens and closes one `HMCClient` per call; a Python caller holding an
`HMCClient` across several reads — the only actor for whom the cache saves a request and the only
one whose cache can go stale; CI, which exercises this code only against `respx` mocks.

**Invariants and assets at stake.** `get_quick_property`'s default behaviour, which ADR 0118's
facade exposes. The cost bound stated to callers: at most one discovery request per resource type
per client session, failures included. `get_quick_property` continuing to work at levels where the
discovery read fails (#799 criterion 4). The six `hmc_mcp.api` exports.

**Accepted failure classes.**

- A stale positive cache wrongly rejects a name a newer level added, for a client held across a
  firmware change. Accepted: unreachable for the per-call CLI and MCP actors, and the remaining
  actor's escape is `validate=False`, the default. ADR 0141 records it.
- Concurrent validated calls for one type, before the first read returns, each issue their own read.
  Accepted: bounded by the calls already in flight, idempotent, and costs at most one extra GET in a
  client whose dominant lifetime is one tool call.
- A discovery read that succeeds with *fewer* names than the level serves would make `validate=True`
  reject a working name. Accepted: no level has shown a short answer, and the escape is again the
  `False` default. The *empty* answer is not accepted, because it is reachable — a 204 returns
  `([], version)` without raising (`core.py:780-781`, pinned by
  `tests/unit/test_client.py:2623-2629`) — and an empty positive set would reject every name for the
  client's lifetime. It is degraded from instead, exactly as a failed read is.
- `validate=True` with a malformed `uuid` spends one discovery request before raising the
  `ValueError` that `_request_with_uuid_path_arguments` (`core.py:461-464`) raises today at no cost,
  because the name check sits above the path build and the UUID check is downstream of it. Accepted
  knowingly: one idempotent GET on a caller-error path, cached so it happens at most once per type
  per client and only under the opt-in flag, and the stated cost bound still holds. Hoisting a
  second UUID check into `get_quick_property` would duplicate policy the transport helper owns.
- Cache growth is unbounded in the number of distinct `resource_type` values passed. Accepted:
  entries are one short string keyed to a frozenset of short strings, resource types come from
  literals in this repository, and the dict dies with the client.

**Covered elsewhere.** Path traversal in `property_name` or `resource_type`:
`_reject_dot_segments` (`core.py:115`), which `_request` (`core.py:435`) applies to every request,
`_request` being the only site that builds or sends one. UUID validation:
`_request_with_uuid_path_arguments` (`core.py:453`). The discovery read's own shape, parsing and
error behaviour: ADR 0140 and `tests/unit/test_client.py:2365-2811`. MCP and CLI exposure: #792.

Not security-relevant, so no threat model: the change adds no entry point, no authn/authz or tenancy
logic, no secret, no new deserialization (the discovery parse already exists, unchanged), no new
non-literal in a constructed path, no permission grant and no dependency. It narrows what reaches
the transport. `$quest` step 6 re-judges this against the actual diff.

## Success

1. `get_quick_property(..., validate=True)` raises `ValueError` without sending a request to
   `/rest/api/uom/{R}/{uuid}/quick/{name}` when `name` is not among the names
   `list_quick_properties` returns for `{R}`. (#799 criterion 1)
2. Across repeated `validate=True` calls on one `HMCClient`, requests to `/rest/api/uom/{R}/quick`
   number at most one per distinct `{R}`, whether the first read succeeded or failed.
   (#799 criterion 2)
3. A fresh `HMCClient` performs the discovery read again on first validated use, and no entry is
   invalidated or refreshed during a client's lifetime. (#799 criterion 3, ADR 0141)
4. When the discovery read yields no names — an `HMCError` from a 4xx or 5xx, an
   `HMCTransportError` from a connection failure, or a 204 returning `([], version)` —
   `validate=True` sends the quick-property request anyway and returns its result.
   (#799 criterion 4)
5. `validate` defaults to `False`; a call omitting it makes no discovery request and behaves exactly
   as at `fac7c19e`. The decision is in ADR 0141 and the parameter in `CHANGELOG.md`.
   (#799 criterion 5)
6. `hmc_mcp.api` still exports exactly the six names ADR 0118 names.

## Validation

All entries are in `tests/unit/test_client.py` unless named otherwise.

| Contract | Mode | Evidence |
|---|---|---|
| `validate=True` refuses an undefined name before transport (Success 1) | `focused-test` | `::test_get_quick_property_validate_refuses_an_undefined_name` |
| `validate=True` passes a defined name through and returns its value (Success 1) | `focused-test` | `::test_get_quick_property_validate_allows_a_defined_name` |
| The names are read once per type per client (Success 2) | `focused-test` | `::test_get_quick_property_validate_reads_the_names_once_per_type` |
| A fresh client re-reads; nothing is shared between clients (Success 3) | `focused-test` | `::test_get_quick_property_validate_rereads_for_a_new_client` |
| A discovery read yielding no names degrades, over all four ways it can happen, and is cached rather than retried (Success 2, 4) | `focused-test` | `::test_get_quick_property_validate_degrades_and_caches_the_failure`, parametrized over a 500, a 400, an `httpx.ConnectError` and a 204 |
| The default makes no discovery request and behaves as today (Success 5) | `focused-test` | `::test_get_quick_property_defaults_to_no_validation` |
| `validate` is keyword-only with default `False` (Success 5) | `focused-test` | `::test_get_quick_property_validate_is_keyword_only_and_defaults_false` |
| ADR 0141 is a well-formed numbered record (Success 5) | `focused-test` | `just adr-numbering`, which checks the filename, unique number and H1 agreement |
| The six facade exports are unchanged (Success 6) | `task-test-not-applicable` | Unchanged by this design, and `tests/unit/test_public_api.py` already fails on any drift; a second test would observe that test's subject, not this change |
| The `CHANGELOG.md` entry (Success 5) | `task-test-not-applicable` | `tests/unit/test_changelog.py` binds only the declared `pyproject.toml` version, which this change does not alter; no executable consumer validates an unreleased entry, and asserting its wording would snapshot prose |
