# Design: a rule for the `group` query parameter

Issue #819. Decision: `docs/adr/0145-uom-group-query-values-are-percent-encoded.md`.

## Problem

`list_uom` and `get_uom` append `f"?group={group}"` unencoded, while `search_uom`
percent-encodes both of its query values; no record says which is intended. Raw, a
`group` value can append a query parameter this client did not name, truncate itself
at a `#`, or raise `httpx.InvalidURL` — outside `_request`'s handled families.

## Scope

Percent-encode `group` with `quote(group, safe="")` at both sites in
`src/hmc_mcp/client/core.py`, bound to a local `encoded_group` as `search_uom` does,
and record ADR 0145. Signatures, the `str | None` type and the `if group:` guard are
unchanged; no caller migrates, because nothing in `src/` or `tests/` passes `group`.
Tests are additive in `tests/unit/test_request_path_safety.py`, leaving its
type-segment inventory intact.
Excluded: the five literal `?group=` sites elsewhere in `client/`, widening
`_reject_dot_segments`, ADR 0143's type segment, #818's `property_name`.

### Failure model

- Actors: a local operator via CLI or MCP server, and a caller of the pre-release
  `HMCClient` module API (ADR 0123) — the only path reaching `group`, since no MCP
  tool or CLI command passes it. The HMC is trusted; `group` is untrusted input.
- Invariants: a request carries only the query parameters this client names; a
  failure surfaces as `HMCError`, `HMCTransportError`, or `ValueError`.
- One boundary, `group` entering the query string; its control is destination
  encoding, not validation. `_reject_dot_segments` still guards path form only.
- Accepted, none passed by any caller: an unknown group name is answered by
  the HMC, since no list of group names is available here; `group="A&group=B"` stops
  naming two groups; a caller-encoded `x%2F..%2Fy` stops tripping the waist's decode
  arm, riding after the `?` where no path resolution applies; and a non-`str` `group`
  raises `TypeError` from `quote`, which the `str | None` signature already forbids.
- Covered elsewhere: the type segment by ADR 0143; `property_name` by #818;
  `httpx.InvalidURL` escaping `_request` elsewhere, a reported follow-up.

## Success

1. Both sites encode `group` before it reaches the path.
2. A `group` holding `&`, `=`, `?`, `#`, `/`, space, CR, or LF builds one well-formed
   request with exactly one `group` parameter and no `httpx.InvalidURL` — unless its
   decoded form holds a dot segment, which `_reject_dot_segments` refuses as `HMCError`.
3. Each of the three group names this repository passes builds today's URL.
4. A `path += f"?group={name}"` site in `core.py` fails the test set unless `name`
   is bound by a literal `quote(name, safe="")` in the same function.
5. `just verify` and `uv run --no-sync prek run --all-files` are green.

## Validation

- Success 1-3. Mode: focused-test — `tests/unit/test_request_path_safety.py`
  parametrizes those characters and the three literal names against a recording
  transport, asserting the encoded single-parameter query, today's URL for a known
  name, and `HMCError` for a dot segment; red before the `quote` calls.
- Success 4. Mode: focused-test — same module, an AST walk matching that assignment,
  the site-directed shape `_is_boundary_check` uses for the type segment.
- Success 5. Mode: focused-test — `just adr-numbering` fails on an ADR filename or
  H1 mismatch; `just verify` and the hooks cover the rest.
