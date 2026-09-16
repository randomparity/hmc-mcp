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

Percent-encode `property_name` at the one call site, record the decision as
**ADR 0146** (`docs/adr/0146-quick-property-names-are-percent-encoded.md`), and
make the classification non-vacuous rather than merely renamed.

- `src/hmc_mcp/client/core.py` — `get_quick_property` binds
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

No transition of ownership: `get_quick_property` keeps the responsibility, and the
guard stays a local binding rather than a new predicate. No production file outside
`core.py` changes.

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
- A caller-pre-encoded dot segment in any form — `..%2f..%2f…` or `%2e%2e` —
  stops being refused by `_reject_dot_segments` and is sent double-encoded as one
  inert segment; a single decode no longer yields a dot segment, so the
  retargeting is closed rather than opened.
- A non-`str` raises `TypeError` from `quote`; the `str` signature forbids it.

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

**Control per boundary.** `quote(property_name, safe="")` — destination encoding,
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
4. A caller-pre-encoded dot segment (`..%2f..%2f…`, `%2e%2e`) reaches the
   transport double-encoded as one inert segment instead of being refused, while
   `..`, `.` and `../../x` are still refused with `HMCError`.
5. Removing the `quote` binding turns items 1, 3 and 4 red.
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
- **Contract: the one refusal that moves.** Mode: `focused-test`.
  `…::test_a_caller_percent_encoded_quick_property_name_reaches_the_transport_as_data`
  — asserts that `..%2f..%2fweb%2fHmcUser%2froot` and `%2e%2e` now reach
  `build_request` double-encoded rather than raising `HMCError`. Red against the
  unfixed code, where both are refused. Same green command.
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
