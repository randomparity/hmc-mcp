# ADR 0140: Quick-property discovery reads Atom Nickname elements

## Status
Accepted

## Context
Issue #788, under epic #785, adds the second HMC discovery read: `/rest/api/uom/{R}/quick`. ADR 0139
fixed the shape these reads share — the parsed answer paired with the response's
`X-HMC-Schema-Version` — but not the body.

This record originally decided a JSON body, on the reasoning that the sibling quick path this
repository already speaks (`client_systems.py:52`, the capitalized `/quick/All`) decodes JSON, and
that the vendored reference lists a lowercase `/quick/all` among the type-anchored paths and
describes it as yielding the defined quick-property names
(`docs/refs/hmc-rest-api-p11/000-hmc-rest-apis.md:66`, ignored and local-only). Both halves of
that reasoning were wrong about the endpoint this method actually reads. A live run on PR #800
settled it:

- **`/quick` answers `application/atom+xml`**, not JSON — an Atom document wrapping a
  `QuickProperty_Collection` whose `QuickProperty` elements each carry the name in a `Nickname`
  child. `ManagedSystem` returned 39 names and `LogicalPartition` 28. (That run reported the
  container but not the element path; the second run below captured it.)
- **The lowercase `/quick/all` does not exist.** It answered 400 on both the root and the child
  anchor. The case asymmetry this record was built around is not that the two spellings collide;
  the lowercase one is simply absent.
- **`/quick/All` is confirmed as per-instance value objects**, which retires the live confirmation
  ADR 0138 has owed since PR #795.

`Accept: */*` and the `X-HMC-Schema-Version` pairing both held. The header came back `hmc-mcp`,
the `X-Audit-Memento` echo ADR 0139 recorded at V1_17_0, now seen again at FW950.

A second run against the rewritten parse confirmed it end-to-end and settled the rest:

- **The document root is `<entry>`, not `<feed>`**, with the collection under `<content>`. Each
  `QuickProperty` holds `Metadata`/`Atom`, `RESTElement`, `Nickname` and a prose `Description`.
- **The names are usable**, not merely string-shaped: the first five fed back through
  `get_quick_property` all resolved. `Description` resolved to an empty string, so an empty value is
  not a missing property.
- **The single-property case is real.** `VirtualNetwork` defines exactly one quick property, and the
  method returns `["NetworkName"]` for it. The arity decision below is load-bearing, not defensive.
- **Anchor availability is per type, not only per level.** `NetworkBridge` answers 400 at the root
  anchor and 200 as a child of `ManagedSystem`.

## Decision
`HMCClient.list_quick_properties(resource_type, *, parent_type=None, parent_uuid=None) ->
tuple[list[str], str | None]`.

The path is `/rest/api/uom/{R}/quick`, or `/rest/api/uom/{P}/{UUID}/{C}/quick` when both parent
arguments are given. There is no `all_properties` argument, because there is no path it could build
that works. It sends `Accept: */*` through `_request_with_uuid_path_arguments`, for the reasons
ADR 0139 records.

The body is read for the text of every `Nickname` element, document-wide, and those texts are the
property names. Empty ones are dropped. A 200 yielding no name raises `HMCError` carrying the status
and body rather than returning an empty list. The second tuple element is the response's
`X-HMC-Schema-Version`, `None` when none is sent, returned verbatim.

## Consequences
Reading `Nickname` elements directly, rather than through `_parse_feed`, avoids the arity hazard
ADR 0139 recorded for `OperationSet`: `element_to_dict` collapses a repeated element to a bare value
when the HMC sends exactly one, so a type defining a single quick property would otherwise need a
separate code path. It also costs nothing to be independent of where the collection sits, which is
what absorbed the first draft's wrong guess at the root element; the capture has since settled it.

Raising on a nameless 200 trades a hypothetical loss for an observed one. A type genuinely defining
zero quick properties is not something any level has shown; an HMC answering 200 with an
`HttpErrorResponse` feed is, and `parse_feed` wraps that shape as a synthetic entry rather than
raising. An empty list cannot distinguish the two, so the case that has actually been seen wins.

Dropping `all_properties` narrows what issue #788 asked for — its title names `/quick/all`. The
narrowing is what the live evidence supports; re-adding the argument is a one-line change if a level
is ever found that serves the lowercase path. Callers destructure a 2-tuple as they already do for
`list_operations`, and the return adds no public type to ADR 0118's facade. `get_quick_property` and
its five call sites are untouched — #799 owns wiring validation in.

## Considered & rejected
- **Route the body through `_parse_feed`.** verified: it is an Atom feed, so this would now parse;
  it is rejected on the arity hazard instead. `element_to_dict` returns `["1","2","3"]` for three
  repeated children and the bare string for one (`tests/unit/test_xmlutil.py`,
  `test_repeated_children_become_list`), which is the `OperationSet` collapse ADR 0139 documented.
- **Keep `all_properties`, documenting that FW950 answers 400.** verified: both `/quick/all` anchors
  returned 400 with an `HttpErrorResponse` body on the live run (PR #800, FW950, schema V1_0). A
  parameter whose only observed effect is a 400 is a phantom feature.
- **Keep `all_properties` but raise before transport.** judgment: a parameter that exists only to
  refuse is more surface than no parameter, and the signature is the place to say it does not exist.
- **Return a bare `list[str]`, dropping the schema version.** judgment: ADR 0139 names #788 as
  inheriting its pairing, and a later call to learn the level is the race it rejected.
- **Use `/quick/All`, the spelling this repository already sends.** verified: the live run returned
  `application/json` holding one object per managed system with values populated — per-instance
  values, not names (PR #800). This is the confirmation ADR 0138 was owed.
- **Scope the search to `QuickProperty/Nickname` rather than document-wide.** verified: the capture
  shows the only `Nickname` elements in the document are the ones wanted — the Atom envelope's
  nearest element is `author/name`, a different local name — so the wider search costs no precision,
  and it is what kept the first draft's wrong guess at the root element from mattering.
- **Return an empty list for a 200 with no names.** verified: `parse_feed` wraps a non-feed body as
  one synthetic entry keyed by its root tag rather than raising (`src/hmc_mcp/xmlutil.py:287-295`),
  and ADR 0139 records the HMC answering 200 with `HttpErrorResponse`. An empty list would report
  that as a type defining nothing.
- **Do nothing; keep quick-property names as literals at call sites.** verified: `rg -n
  'rest/api/uom/[^ ]*quick' src/` at `70e1bc28` returns five lines naming two paths — the
  single-instance `get_quick_property` read and the `/quick/All` value fallback. Nothing asks an HMC
  which names it defines, and all five `get_quick_property` call sites pass literals.
