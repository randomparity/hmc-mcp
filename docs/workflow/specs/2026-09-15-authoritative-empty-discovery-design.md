# Authoritative empty discovery answers — design

Issue #812. Decision record: [ADR 0144](../../adr/0144-container-present-empty-discovery-is-authoritative.md).

## Problem

`HMCClient`'s two opt-in pre-flights cache their discovery read with
`frozenset(names) if names else None`, and `None` means "do not validate". The parse below them
already separates *container present, no names* from *204 or not this shape*; the cache discards
that at the return boundary, where both arrive as `([], version)`.

So `validate=True` checks nothing on a type that defines nothing — six of the eleven types
captured at `V1_17_0` and `V1_20_0` (ADR 0142), where the round trip the pre-flight exists to save
is a captured HTTP 500. The #789 design spec records this as an accepted failure class rather than
a fix, because taking it trades today's fail-open for a new fail-closed mode and "needs a decision,
not just an implementation".

## Scope

`src/hmc_mcp/client/core.py` keeps every responsibility it has. Two discovery reads change their
return contract, two caches stop collapsing it, two refusal messages gain an empty branch, and one
helper's stated precondition changes its ground. No responsibility moves between files, no caller
migrates, and no compatibility path is retained: neither `list_search_parameters` nor
`list_quick_properties` is on ADR 0029's lifecycle allowlist that ADR 0118 retains — both are
unsupported generic UOM helpers — and neither has a caller in `src/` outside `core.py`, so the old
shape is replaced rather than kept beside the new one.

- **`list_search_parameters`, `list_quick_properties`** → `tuple[list[str] | None, str | None]`.
  `None` means the level's answer is not a fact about the type. Full mapping in ADR 0144's
  Decision table. The new third state — container present, with an item element
  (`SearchParameter`, `QuickProperty`) or a name element under it but no usable name — reads as
  `None`, so the parse-artefact shape does not become authoritative. The item element is consulted
  beside the name element because it is the evidence that the container holds something at all;
  testing only the name element was implemented, then rejected in review and recorded in ADR 0144's
  *Considered & rejected*.
- **`_defined_search_parameter_names`, `_defined_quick_property_names`** → cache `None` only for
  that unknown answer, `frozenset(names)` otherwise, including `frozenset()`. The lock, the
  re-check under it, the key, the lifetime and the `HMCError` degradation are untouched.
- **`search_uom`, `get_quick_property`** → the refusal message branches on an empty positive set:
  `<Type> defines no search parameter named 'X'. The type defines none at all.` The non-empty
  message is byte-identical to today's.
- **`_summarize_names`** → unchanged code; its docstring records that the non-empty precondition
  now holds because each caller branches on the empty set first.
- **`CHANGELOG.md`** → the Unreleased entries for all four methods are rewritten in place. They
  describe unreleased APIs, so this is a correction, not a `Changed` note. The
  `list_quick_properties` entry is additionally stale from #811 and is corrected in the same edit.
- **Three merged specs carrying empty-answer clauses this change falsifies** → each clause is
  struck through in place with a one-sentence ADR 0144 pointer, matching the entry already struck
  that way in the first of them. The enumeration is the result of grepping the falsified literals
  `([], version)`, `([], schema_version)` and `tuple[list[str], str | None]` across `docs/adr/` and
  `docs/workflow/`, not of reading one section per file:
  - `2026-09-15-discover-search-parameters-design.md` — the *Failure model* entries **performs no
    check on a type that defines nothing** and the empty-answer tail of **the wrong-set class**;
    the *Changes* sentence run declaring the 204 and nameless-200 returns; and success criterion 7's
    204 clause.
  - `2026-09-14-validate-quick-property-names-design.md` — the empty-answer tail under the *fewer
    names* entry, and success criterion 4's 204 clause.
  - `2026-09-14-discover-quick-properties.md` — the *Scope* line declaring
    `-> tuple[list[str], str | None]`, and the *Success* bullet's 204 clause.

  No other line in any of the three is touched. Occurrences of the same literals elsewhere are
  deliberately left: in this issue's own ADR, spec and plan they describe the behaviour being
  replaced; `2026-09-14-discover-job-operations.md` is `list_operations`, which does not change;
  the merged implementation plans are execution records of a past run, not the durable record; and
  ADR 0141's and ADR 0142's bodies stay under the merged-ADR rule, amended by Status banner only.
  Only the first spec is named by completion criterion 6; the other two are its quick-twin
  equivalents, reached as an unavoidable consequence of criterion 3, and this bullet is where that
  extension of the charter's declared surface is recorded rather than arriving unannounced in the
  diff.
- **`docs/adr/0141-*.md`, `docs/adr/0142-*.md`** → a Status amendment banner each, and nothing else.

Out of scope, with owners: the `validate` default (settled, ADR 0141/0142); retrofitting the
discrimination onto the child-anchored validation forms (a future issue); whether an empty answer
at an unmeasured firmware level is trustworthy (the next firmware capture — stated, not resolved).

## Success

1. `list_search_parameters` and `list_quick_properties` each return `([], version)` for a 200
   carrying the container with no item element and no name element under it, `(None, version)` for
   a 204 and for a container carrying either of those elements with no usable name, and raise
   `HMCError` for a 200 that yields no usable name and carries no container. A 200 carrying usable
   names returns them whether or not the container is present, as today.
2. `_defined_search_parameter_names` and `_defined_quick_property_names` each cache `frozenset()`
   for the first of those and `None` for the second and third, and still cache `None` on any
   `HMCError` from the read.
3. `search_uom(..., validate=True)` and `get_quick_property(..., validate=True)` raise `ValueError`
   before any request is sent when the cached positive set is empty, with a message ending
   `The type defines none at all.` and no bare `"."`.
4. Every call with `validate` left at its default sends exactly the requests it sends today, and
   every existing non-empty refusal message is unchanged.
5. `just verify` and `uv run --no-sync prek run --all-files` are green.

## Failure model

**Actors and deployments.** A local operator through the CLI and an MCP client through
`_app.with_client`, both of which build one `HMCClient` per call; a library caller importing
`hmc_mcp.api` and holding one client across several reads; CI, which exercises this only against
`respx` fixtures. No anonymous or multi-tenant deployment exists — the client speaks to one HMC
named by the operator's own configuration.

**Invariants and assets at stake.**
- Default-path behaviour: a caller who did not pass `validate=True` must reach the HMC exactly as
  before.
- Availability of the opt-in path: a wrong positive set refuses work the HMC would have done.
- The cost bound ADR 0141/0142 published: at most one discovery request per type per client.
- Message disclosure bounds: `_MAX_REPORTED_NAMES` and `_MAX_REPORTED_NAME_LENGTH`.

**Accepted failure classes.**
- A level keeping the container and renaming both the item element and the name element under it
  caches an empty positive set and refuses every name for that client's lifetime. A level renaming
  only the name element reads as unknown and fails open, because the item element is tested too.
  Accepted with its bounds stated in ADR 0144's Consequences: opt-in, one call per client in the
  CLI and MCP deployments, and a local `ValueError` naming the condition. Unmeasured, and stated as
  unmeasured.
- A transient discovery failure is cached as durably as a firmware-level one. Carried unchanged
  from ADR 0141/0142; this change does not touch the failure path.
- The wrong-set class — a read that succeeds with the wrong names — carried unchanged from
  ADR 0142. The empty answer leaves that class by this change; nothing else does.
- No firmware level has been observed answering the `/quick` anchor with the container and no
  `QuickProperty`. Accepted: the decision governs how such an answer is read, and asserts nothing
  about whether one occurs.

**Covered elsewhere.**
- Type-segment grammar at the URL and `Accept` boundaries: `_reject_unknown_uom_type`, ADR 0143.
- Response body size: `_read_bounded_response` and `HMC_MAX_RESPONSE_BYTES`, ADR 0133/0134.
- `get_quick_property`'s raw `property_name` interpolation: a follow-up candidate on ADR 0143, not
  this change.

## Threat model

**Boundary inventory.** No boundary is added. One existing boundary changes meaning: the HMC
response body parsed by `list_*` now decides whether the client refuses locally, where before it
could only decide whether the client validated at all. No new element, header, or path is parsed.

**Actor model.** The HMC is trusted for correctness and untrusted for *shape* — this module
already treats a 200 body as possibly an `HttpErrorResponse` feed. A network position able to
forge an HMC response is out of the deployment's threat model: the session is TLS with
`verify_ssl` on by default, and an actor holding that position can already answer any read.

**Control per boundary.** The container test is the control, and this change narrows rather than
widens it: authority now requires the container *and* zero item elements *and* zero name elements,
so a body holding parameters this parse cannot name cannot make the client refuse. Failure of the control leaks nothing new — the refusal
message names the type and the caller's own property name, and the non-empty branch keeps the two
existing disclosure bounds.

**Explicitly out of scope.** Denial of function by a compromised or misbehaving HMC: it can
already return an empty feed, a 500, or a wrong positive set, and `validate=False` is the escape
from all of them. Nothing here changes what the client sends or where it sends it.

## Validation

| Contract | Mode | Evidence |
|---|---|---|
| `list_search_parameters` returns `([], v)` for container + no `SearchParameter` and no `ParameterName` element | `focused-test` | `tests/unit/test_client.py::test_list_search_parameters_empty_set_returns_no_names` |
| `list_search_parameters` returns `(None, v)` for a 204, for all-empty `ParameterName` elements, and for `SearchParameter` items carrying no `ParameterName` | `focused-test` | `tests/unit/test_client.py::test_list_search_parameters_204_returns_an_unknown_answer` (renamed), `::test_list_search_parameters_all_empty_names_return_an_unknown_answer` and `::test_list_search_parameters_unnamed_items_return_an_unknown_answer` |
| `list_quick_properties` returns `([], v)` for container + no `QuickProperty` and no `Nickname` element | `focused-test` | `tests/unit/test_client.py::test_list_quick_properties_empty_set_returns_no_names` |
| `list_quick_properties` returns `(None, v)` for a 204, for all-empty `Nickname` elements, and for `QuickProperty` items carrying no `Nickname` | `focused-test` | `tests/unit/test_client.py::test_list_quick_properties_204_returns_an_unknown_answer` (renamed), `::test_list_quick_properties_all_empty_names_return_an_unknown_answer` and `::test_list_quick_properties_unnamed_items_return_an_unknown_answer` |
| Both caches store `frozenset()` for the authoritative empty answer | `focused-test` | `tests/unit/test_client.py::test_search_uom_validate_refuses_a_type_defining_nothing`, `::test_get_quick_property_validate_refuses_a_type_defining_nothing` |
| Both caches still store `None` for 204, all-empty, and `HMCError` | `focused-test` | the existing `*_validate_degrades_and_caches_the_failure` parametrizations, extended with the all-empty case |
| The empty-set refusal message carries no bare `"."` and names the condition | `focused-test` | the two `*_refuses_a_type_defining_nothing` cases assert the message tail |
| Non-empty refusal messages and every default-path request are unchanged | `focused-test` | the existing `search_uom` / `get_quick_property` validation suites, unmodified |
| `_summarize_names` docstring ground | `task-test-not-applicable` | The code is unchanged and the edit is a docstring sentence about why a stated precondition holds; no executable or structural observation of it could fail. |
| ADR 0141/0142 Status banners, ADR 0144, CHANGELOG, the three spec strike-throughs | `task-test-not-applicable` | Prose edits to records. `just adr-numbering` checks filename and H1 only, and no consumer validates record prose; a test searching for wording would assert nothing about behaviour. |
