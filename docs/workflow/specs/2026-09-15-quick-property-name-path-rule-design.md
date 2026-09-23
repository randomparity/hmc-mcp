# Quick-property name path rule (issue #818)

## Problem

`HMCClient.get_quick_property` interpolates the caller's `property_name` into
`/rest/api/uom/{resource_type}/{uuid}/quick/{property_name}` with no grammar and
no encoding, on the line ADR 0143 hardened for the type segment. ADR 0146's
Context carries the reproduction: at `856e3aec` with httpx 0.28.1, a `?` appends
a query string this client did not name, a `#` silently truncates the value so a
different property is read, and a CRLF escapes the client's exception contract.

`tests/unit/test_request_path_safety.py` lists `property_name` in
`_KNOWN_UOM_SEGMENT_ARGUMENTS`, so `test_every_uom_path_interpolation_is_a_known_argument`
passes for exactly the case its docstring describes catching.

## Scope

Refuse a dot segment and percent-encode `property_name` at the one call site,
record the decision as
**ADR 0146** (`docs/adr/0146-quick-property-names-are-percent-encoded.md`), and
make the classification non-vacuous rather than merely renamed.

- `src/hmc_mcp/client/core.py` — `get_quick_property` calls
  `_reject_dot_segments("GET", f"/quick/{property_name}")`, then binds
  `encoded_property = quote(property_name, safe="")` and interpolates that.
  Reuses the local name `search_uom` already uses for this same argument, so the
  segment inventory shrinks by one entry and gains none.
- `tests/unit/test_request_path_safety.py` — drop `property_name` from
  `_KNOWN_UOM_SEGMENT_ARGUMENTS`; add `_ENCODED_SEGMENT_ARGUMENTS`
  (`encoded_property`, `encoded_value`) and the site-directed assertion ADR 0146's
  Decision specifies, collecting quote bindings in `_uom_path_sites()`' existing
  single walk via `_is_quote_binding`. Without it the reclassification would be
  vacuous in a new way — a name trusted for what it is called.
- `docs/adr/0146-*.md` — new record.

No transition of ownership: `get_quick_property` keeps the responsibility, and
neither control is a new predicate — one is a local binding, the other reuses
`_reject_dot_segments` unchanged. No production file outside `core.py` changes.

### Failure model

**Actors and deployments.** A local operator running the CLI; an MCP client
driving the tool surface; code importing `HMCClient` from `hmc_mcp.api`. Only the
third reaches `property_name`: no CLI command or MCP tool exposes it, all five
`src/` call sites pass the literal `"PartitionState"`, and `get_quick_property`
sits outside `_SUPPORTED_CLIENT_LIFECYCLE` (ADR 0118's callable-but-unsupported
class).

**Invariants and assets at stake.**
- The request path addresses the property the caller named, inside the resource
  the selector named (ADR 0039 target scope).
- The client's exception contract: `HMCError`, `HMCTransportError`, `ValueError`.
- The wire format for the six quick-property names this repository passes.

**Accepted failure classes.**
- An unknown-but-well-formed name still reaches the HMC and is answered there —
  held by ADR 0141's opt-in `validate=True` check against the HMC's own list.
- An empty `property_name` addresses the `/quick/` container anchor; same
  resource, and refusing it is a grammar fragment, the option ADR 0146 declines.
- A non-`str`, `bytes` excepted, raises `TypeError` from `quote`. `bytes` is the
  exception `quote` accepts: the site guard reads its `repr` through the f-string
  while the encoder decodes it, so the two see different values. The `str`
  signature forbids both, and no in-repository caller passes either.

**Covered elsewhere.**
- The type segment's grammar and length bound — ADR 0143, ADR 0147.
- The `group` query value — ADR 0145, issue #819.
- Translating `httpx.InvalidURL` at `_request` for caller-supplied whole paths —
  ADR 0145's follow-up candidate; not claimed here.

### Threat model

**Boundary inventory.** One boundary, widened not added: the `property_name`
argument of `get_quick_property`, crossing from caller-controlled data into a
URL path this client builds. No new entry point, no dependency change, no
permission change.

**Actor model.** The untrusted party is whatever supplies `property_name` to a
process that already imports `HMCClient` and holds HMC credentials. The design
trusts `resource_type` and `uuid` to their existing guards and trusts the HMC to
answer an unknown name; it does not trust `property_name`.

**Control per boundary.** `_reject_dot_segments` on the argument, then
`quote(property_name, safe="")` — a path-form refusal plus destination encoding,
which is the RFC 3986 control for a value with one destination. On a `str` it
raises at most `UnicodeEncodeError` (a lone surrogate), which subclasses
`ValueError` and so is already inside the client's exception contract; it leaks
no value either way. `_reject_dot_segments` at the transport waist keeps refusing
literal dot segments, unchanged.

**Explicitly out of scope.** Name-namespace validation (ADR 0141 owns it); a
length bound (no reproduction, and no second destination — see ADR 0146's
rejected alternatives); widening `_reject_dot_segments` (it would refuse this
client's own legitimate paths).

## Success

1. `get_quick_property` sends the whole of `property_name` inside the last path
   segment, percent-encoded, for each of `?`, `#`, `/`, space, a non-ASCII
   character, and CRLF — no query string, no truncation, no `httpx.InvalidURL`.
2. The URL built for each of the six quick-property names this repository passes
   still ends `/quick/<name>` byte-for-byte, so no pinned path changes.
3. `property_name` is absent from `_KNOWN_UOM_SEGMENT_ARGUMENTS`, and every
   encoded-class segment interpolated into a `/rest/api/uom/` f-string in
   `core.py` is `quote(..., safe="")`-bound in its own function.
4. Every name `_reject_dot_segments` refused before is still refused with
   `HMCError` and no request built — `..`, `.`, `../../x`, the pre-encoded
   `..%2f..%2fweb` and `%2e%2e` that encoding alone would have let through, and
   the URL-shaped `x://%2e%2e` and the netloc-shaped `/..%2f://` that a
   bare-segment or lone-separator guard call would have let through.
5. Removing the `quote` binding turns items 1 and 3 red; removing the
   `_reject_dot_segments` call turns item 4 red.
6. `just verify` and `uv run --no-sync prek run --all-files` exit 0.

## Validation

- **Contract: the encoded path segment.** Mode: `focused-test`.
  `tests/unit/test_request_path_safety.py::test_a_quick_property_name_cannot_re_point_the_request`
  — parametrized over the six inputs; red before the `core.py` edit because the
  built URL carries a query string, is truncated, or raises. Green:
  `uv run --no-sync pytest tests/unit/test_request_path_safety.py -k quick_property -q`.
- **Contract: no wire-format change.** Mode: `focused-test`.
  `…::test_encoding_is_a_no_op_on_the_quick_property_names_this_client_passes` —
  parametrized over all six names and asserting the built URL each time, so the
  assertion runs client code and goes red if the binding is dropped. Same green
  command.
- **Contract: the dot-segment refusal identity.** Mode: `focused-test`.
  `…::test_a_dot_segment_quick_property_name_is_still_refused` — asserts
  `HMCError` for `..`, `.` and `../../x` after encoding. Same green command.
- **Contract: encoding buys no dot segment a passage.** Mode: `focused-test`.
  `…::test_a_caller_percent_encoded_dot_segment_name_is_refused_too` — asserts
  `HMCError` and no request built for `..%2f..%2fweb%2fHmcUser%2froot`, `%2e%2e`,
  `%2E%2E`, `..%2F..%2Fx`, `x://%2e%2e`, `x://%2E%2E`, `x://%2e%2e/y`,
  `/..%2f://`, `/..%2fx://` and `/..%2F://`. Red with the site guard removed (the
  first four reach the transport), red with the guard called on the bare segment
  (the `x://` three), and red with it called behind a lone `/` (the `/..%2f`
  three). Same green command.
- **Contract: the segment classification.** Mode: `focused-test`.
  `…::test_every_encoded_uom_segment_is_quote_bound` plus the existing
  `test_every_uom_path_interpolation_is_a_known_argument` — the first is red
  while any encoded-class name lacks its binding. Green:
  `uv run --no-sync pytest tests/unit/test_request_path_safety.py -q`.
- **Contract: ADR 0146 as a record.** Mode: `task-test-not-applicable`. The
  changed surface is a prose decision record; `scripts/check_adr_numbering.py`
  (run by `just adr-numbering`) validates its number, filename and H1, and no
  executable consumer validates a decision's reasoning. Pinning its prose would
  be a wording snapshot, which this repository's plan rules forbid.

Guardrails: `just verify`, then `uv run --no-sync prek run --all-files`.
`BASE_BRANCH` is `main`; branch `feat/quick-property-name-path-rule-818`.
No deferrals carried into this design.
