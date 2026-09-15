# UOM type-segment guard: its bound and its reach

Issue #820. Decisions: ADR 0147, amending 0143.

## Problem

`_reject_unknown_uom_type` (`src/hmc_mcp/client/core.py`) bounds a uom type segment's character
set but not its length, so a megabyte of `A` reaches the URL and `Accept` header. Separately,
`client_users._child_path` builds a type segment where the `core.py`-scoped drift tests cannot
see it; six of its seven sites are validated only because the same literal also reaches
`_get`/`_put`/`_post`.

## Scope

Add `_MAX_UOM_TYPE_LENGTH = 128`; refuse a longer value before the character check, with a
message naming its length and the maximum. Move `_reject_unknown_uom_type` and `_UOM_TYPE` from
`core.py` to `client_contracts.py` — the leaf module hosting `validate_adapter_type` that
`client_users.py` already imports — and call the predicate from `_child_path`. Ownership moves
with no caller migration, no obsolete path and no retained compatibility path: `core.py` imports
the name, so its sixteen call sites and the drift
test's `ast.Name` match are unchanged. `tests/unit/test_request_path_safety.py` imports from
the new home; its drift helpers stay untouched — #818 owns them.

### Failure model

- Actors: a local CLI operator; an MCP client model calling `hmc_list_resources` with a free-form
  `resource_type` over stdio; and under `serve --http` an unauthenticated caller — loopback by
  default, any reachable host with `--allow-remote` (`server.py:11-18`).
- At stake: the *type segment* of the path and the `Accept` header is bounded here, not by the
  HMC. The rest of the path is not.
- Accepted: a real HMC type name over 128 characters is refused locally — ADR 0143 accepts that
  class for the character grammar, the longest type name here is 32 characters, remedied by
  widening one constant. A uom path built without an f-string stays invisible to the drift
  tests (ADR 0143). `search_uom`'s unbounded `property_value` is the same defect family on the
  same line, ownerless, and a follow-up candidate, not bounded here.
- Covered elsewhere: `property_name` (#818), `group` (#819), dot segments.
- Threat model: boundaries are `_child_path`'s `child_type`, newly controlled, and the
  predicate's `value`, whose control widens. Untrusted: the MCP client model and, on
  `--http`, an unauthenticated caller. Control is refusal before interpolation, a `ValueError`
  naming the argument and one character or the length, never the value. Out of scope (ADR
  0143): `Accept` quoting; types the HMC rejects.

## Success

1. A 129-character grammar-valid type is refused; 128 characters is accepted.
2. The refusal names the argument, the value's length, and the maximum.
3. `_child_path` raises `ValueError` on a grammar-invalid `child_type`, building no path.

## Validation

- Length bound and criterion 2's message — Mode: focused-test.
  `tests/unit/test_request_path_safety.py`, parametrized at 128, 129 and 1 MiB, asserting the
  message names the argument, the length and the maximum. Red: 129 and 1 MiB accepted, no such
  message; 128 passes either way. Green:
  `uv run --no-sync pytest tests/unit/test_request_path_safety.py`.
- `_child_path` type refusal — Mode: focused-test. `tests/unit/test_client_users.py`. Red: it
  returns a path for `"UserProfile?group=x"`. Green:
  `uv run --no-sync pytest tests/unit/test_client_users.py`.
- Relocation keeps all sixteen sites — Mode: focused-test. The drift and grammar tests
  import from the new home, green under that command.
