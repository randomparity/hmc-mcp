# Design: a rule for the `group` query parameter

Issue #819. Decision: `docs/adr/0145-uom-group-query-values-are-percent-encoded.md`.

## Problem

`list_uom` and `get_uom` append `f"?group={group}"` unencoded, while `search_uom`
percent-encodes both of its query values; no record says which is intended. Raw, a
`group` value can append a query parameter this client did not name, truncate itself
at a `#`, or raise `httpx.InvalidURL` — outside `_request`'s handled families.

## Scope

Percent-encode `group` with `quote(group, safe="")` at both sites in
`src/hmc_mcp/client/core.py`, bound to a local named `encoded_group` as
`search_uom` names `encoded_property`, and record ADR 0145. Signatures, the
`str | None` type, and the `if group:` guard are unchanged, and no caller migrates:
`core.py` already owns these paths, and nothing in `src/` or `tests/` passes `group`.
Tests are additive in `tests/unit/test_request_path_safety.py`, leaving its
type-segment inventory constants untouched. Excluded: the five literal `?group=`
sites elsewhere in `client/`, widening `_reject_dot_segments`, ADR 0143's type
segment, #818's `property_name`.

### Failure model

- Actors: a local operator via CLI or MCP server, and a caller of the pre-release
  `HMCClient` module API (ADR 0123) — the only path reaching `group`, since no MCP
  tool or CLI command passes it. The HMC is trusted; `group` is untrusted input.
- Invariants: a request carries only the query parameters this client names; a
  failure surfaces as `HMCError`, `HMCTransportError`, or `ValueError`.
- One boundary, `group` entering the query string; its control here is
  destination encoding, not validation. `_reject_dot_segments` still guards path
  form only.
- Accepted: an unknown group name reaches the HMC and is answered there — no list
  of HMC group names is available to this checkout. `group="A&group=B"` stops naming
  two groups — nothing passes it, and `get_vios_storage_detail` reads multiple groups
  from its own literal path.
- Covered elsewhere: the type segment by ADR 0143; `property_name` by #818.

## Success

1. Both sites encode `group` before it reaches the path.
2. A `group` holding `&`, `=`, `?`, `#`, `/`, space, CR, or LF builds one well-formed
   request whose query holds exactly one `group` parameter, raising no `InvalidURL`.
3. Each of the three group names this repository passes builds today's URL.
4. A future `?group=` f-string in `core.py` interpolating an unencoded name
   fails the test set.
5. `just verify` and `uv run --no-sync prek run --all-files` are green.

## Validation

- Success 1 and 2. Mode: focused-test — `tests/unit/test_request_path_safety.py`
  parametrizes those characters against a recording transport and asserts one `group`
  parameter holding the encoded value; red before the `quote` calls.
- Success 3. Mode: focused-test — same module, the three literal group names, each
  asserting a built path byte-identical to today's.
- Success 4. Mode: focused-test — same module, an AST walk over `core.py`'s
  `?group=` f-strings requiring a declared encoded name.
- Success 5. Mode: focused-test — `just adr-numbering` fails on an ADR filename or
  H1 mismatch; `just verify` and the hook run cover the rest.
