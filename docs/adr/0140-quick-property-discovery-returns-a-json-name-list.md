# ADR 0140: Quick-property discovery returns a JSON name list

## Status
Accepted

## Context
Issue #788, under epic #785, adds the second HMC discovery read: `/rest/api/uom/{R}/quick` and
`/rest/api/uom/{R}/quick/all`, which answer which quick properties a type defines. ADR 0139 fixed
the shape these reads share — the parsed answer paired with the response's
`X-HMC-Schema-Version` — and named this issue as inheriting it. It did not settle the body:
`/operations` answers with an Atom entry and these two do not.

The repository already speaks one type-anchored quick path, and it is not this one.
`src/hmc_mcp/client/client_systems.py:52` reads `/rest/api/uom/ManagedSystem/quick/All` — capital
`All` — and receives a JSON array of per-**instance** objects carrying `UUID` and `SystemName`,
i.e. values (ADR 0138). The vendored reference documents the lowercase `/quick/all` as "get a list
of all defined quick properties for type {R}" (`docs/refs/hmc-rest-api-p11/000-hmc-rest-apis.md`),
and `164-managed-system.md` says supported property *names* are available at
`/rest/api/uom/ManagedSystem/quick`. Whether the HMC distinguishes the two spellings by case is
unverified at authoring time.

## Decision
`HMCClient.list_quick_properties(resource_type, *, all_properties=False, parent_type=None,
parent_uuid=None) -> tuple[list[str], str | None]`.

The path is `/rest/api/uom/{R}/quick`, with `/all` appended when `all_properties` is true, and
`/rest/api/uom/{P}/{UUID}/{C}/quick[/all]` when both parent arguments are given. It sends
`Accept: */*` through `_request_with_uuid_path_arguments`, for the reasons ADR 0139 records.

The body is decoded as JSON and must be an array whose every element is a string; that array is the
property names. Anything else — invalid JSON, an object, a scalar, or an array holding a
non-string — raises `HMCError` naming the observed shape rather than being coerced. The second
element is the response's `X-HMC-Schema-Version`, `None` when the HMC sends none.

## Consequences
The strict parse is what makes the open case question safe to leave open: a level that answers the
lowercase path with the per-instance object array `/quick/All` returns raises and names that shape
instead of handing back objects labelled as names. The cost is that such a level has no working call
here until a follow-up decides what to do with it. Live confirmation of both anchors is owed, is held
by no existing issue, and is recorded as the open assumption in the specification's failure model.

Callers destructure a 2-tuple, as they already do for `list_operations`. The return is built from
`list`, `str` and `None`, so no new public type joins ADR 0118's six-name facade.
`get_quick_property` and its five call sites are untouched: this makes local validation *possible*,
and epic #785 owns wiring it.

## Considered & rejected
- **Route the body through `_parse_feed`.** verified: the sibling type-anchored quick path this
  repository already speaks returns JSON, not Atom (`client_systems.py:52-92` at `70e1bc28`, ADR
  0138), and issue #788's acceptance states the response is a plain list.
- **Return a bare `list[str]`, dropping the schema version.** judgment: ADR 0139 names #788 as
  inheriting its pairing, the defined-property set is firmware-level dependent in exactly the way
  that record argues, and a later call to learn the level is the race it rejected.
- **Use `/quick/All`, the spelling this repository already sends.** verified: that spelling is
  live-evidenced to return per-instance values (ADR 0138), while the reference documents the
  lowercase spelling as returning defined names — so the lowercase path is the one whose documented
  answer is this issue's outcome.
- **Return the decoded JSON verbatim, whatever its shape.** judgment: every caller then re-derives
  the names and re-discovers the two shapes, which is the work this method exists to do once.
- **Coerce non-string elements with `str()`.** judgment: it turns a values response into
  plausible-looking names, the one failure nobody would notice.
- **Refuse `all_properties=True` on the child anchor, which the reference does not document.**
  judgment: the uniform grammar costs one f-string, an unsupported combination answers a status this
  method already surfaces, and a local refusal would also refuse a level that supports it.
- **Do nothing; keep quick-property names as literals at call sites.** verified: `rg -n
  'rest/api/uom/[^ ]*quick' src/` at `70e1bc28` returns five lines naming two distinct paths — the
  single-instance `get_quick_property` read and the `/quick/All` value fallback. Nothing asks an HMC
  which names it defines, and all five `get_quick_property` call sites pass literals.
