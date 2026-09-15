# ADR 0140: Quick-property discovery returns a JSON name list

## Status
Accepted

## Context
Issue #788, under epic #785, adds the second HMC discovery read: `/rest/api/uom/{R}/quick` and
`/rest/api/uom/{R}/quick/all`. ADR 0139 fixed the shape these reads share — the parsed answer paired
with the response's `X-HMC-Schema-Version` — but not the body, because `/operations` answers with an
Atom entry and these two do not.

The open question is a case asymmetry. The vendored reference documents the lowercase `/quick/all`
as "get a list of all defined quick properties for type {R}"
(`docs/refs/hmc-rest-api-p11/000-hmc-rest-apis.md`), and `164-managed-system.md` says supported
property *names* are at `/rest/api/uom/ManagedSystem/quick`. This repository already sends the
capitalized `/quick/All` (`client_systems.py:52`) and decodes per-**instance** objects — values, not
names — a shape ADR 0138 records on IBM project-pim evidence, live confirmation still owed. Whether
the HMC distinguishes the two spellings by case is unverified.

## Decision
`HMCClient.list_quick_properties(resource_type, *, all_properties=False, parent_type=None,
parent_uuid=None) -> tuple[list[str], str | None]`.

The path is `/rest/api/uom/{R}/quick`, with `/all` appended when `all_properties` is true, and
`/rest/api/uom/{P}/{UUID}/{C}/quick[/all]` when both parent arguments are given. It sends
`Accept: */*` through `_request_with_uuid_path_arguments`, for the reasons ADR 0139 records.

The body is decoded as JSON and must be an array whose every element is a string; that array is the
property names. Anything else — invalid JSON, an object, a scalar, or an array holding a
non-string — raises `HMCError` carrying the status, the body, and the observed shape, rather than
being coerced. The second element is the response's `X-HMC-Schema-Version`, `None` when none is sent.

## Consequences
The strict parse makes the case question safe to leave open: a level answering the lowercase path
with per-instance objects raises and names that shape, at the cost of having no working call here
until a follow-up decides what to do with it. Live confirmation of both anchors is owed and held by
no existing issue; the specification's failure model records it. Callers destructure a 2-tuple as
they already do for `list_operations`, and the return adds no public type to ADR 0118's facade.
`get_quick_property` and its five call sites are untouched — epic #785 owns wiring validation in.

## Considered & rejected
- **Route the body through `_parse_feed`.** verified: the sibling type-anchored quick path this
  repository already speaks returns JSON, not Atom (`client_systems.py:52-92` at `70e1bc28`), and
  issue #788's acceptance states the response is a plain list.
- **Return a bare `list[str]`, dropping the schema version.** judgment: ADR 0139 names #788 as
  inheriting its pairing, and a later call to learn the level is the race it rejected.
- **Use `/quick/All`, the spelling this repository already sends.** verified: it decodes a JSON array
  of per-instance `{UUID, SystemName}` objects (`client_systems.py:52-91`), a shape ADR 0138 records
  on IBM project-pim evidence with live confirmation still owed, while the reference documents the
  lowercase spelling as returning defined names. The rejection stands on that documented asymmetry,
  not on a live capture.
- **Return the decoded JSON verbatim, or coerce non-strings with `str()`.** judgment: the first makes
  every caller re-derive the names and re-discover both shapes; the second turns a values response
  into plausible-looking names, the one failure nobody would notice.
- **Refuse `all_properties=True` on the child anchor, which the reference does not list.** judgment:
  the uniform grammar costs one f-string, an undocumented combination either answers non-200 or
  returns a non-array body and the strict parse rejects both, and a local refusal would also refuse a
  level that supports it.
- **Do nothing; keep quick-property names as literals at call sites.** verified: `rg -n
  'rest/api/uom/[^ ]*quick' src/` at `70e1bc28` returns five lines naming two paths — the
  single-instance `get_quick_property` read and the `/quick/All` value fallback. Nothing asks an HMC
  which names it defines, and all five `get_quick_property` call sites pass literals.
