# UOM type-segment guard: its bound and its reach

Issue #820. Decisions: ADR 0147, amending ADR 0143.

## Problem

`_reject_unknown_uom_type` (`src/hmc_mcp/client/core.py`) bounds a uom type segment's
character set but not its length, so a megabyte of `A` reaches the URL and the `Accept`
header. Separately, `client_users._child_path` builds a type segment in a module the
`core.py`-scoped AST drift tests cannot see; both its callers pass literals today, so that
is drift risk rather than a live defect.

## Scope

Add `_MAX_UOM_TYPE_LENGTH = 128`; refuse a longer value before the character check, with a
message naming its length and the maximum. Move `_reject_unknown_uom_type` and `_UOM_TYPE`
to `client_contracts.py` and call the predicate from `_child_path`. `core.py` imports
`client_users` before defining the predicate, so a mixin cannot import it from `core`
without a circular import; `client_contracts.py` is the existing leaf module that hosts
`validate_adapter_type` and that `client_users.py` already imports. Ownership moves with no
caller migration, no obsolete path and no retained compatibility path: `core.py` imports the
name, so its fifteen call sites and the drift test's `ast.Name` match are unchanged.
`tests/unit/test_request_path_safety.py` imports from the new home; `_uom_path_sites`,
`_is_boundary_check` and `_KNOWN_UOM_SEGMENT_ARGUMENTS` stay untouched — #818 owns them.

### Failure model

- Actors: a local CLI operator; an MCP client model calling `hmc_list_resources` with a
  free-form `resource_type`; `hmc_mcp.api` importers. No anonymous traffic reaches this code.
- At stake: the path and `Accept` header address the resource the caller named, at a size
  this process bounds rather than the HMC.
- Accepted: a real HMC type name over 128 characters is refused locally — ADR 0143 accepts
  that class for the character grammar, the longest type name in this repository is 32
  characters, and the remedy is the same one-place widening. A uom path built without an
  f-string stays invisible to the drift tests — also ADR 0143.
- Covered elsewhere: `property_name` (#818), the `group` parameter (#819), dot segments
  (`_reject_dot_segments`).
- Threat model: the boundaries are `_child_path`'s `child_type`, newly controlled, and the
  predicate's `value`, whose control widens. The untrusted party is the MCP client model.
  Control is refusal before interpolation, a `ValueError` naming the argument and one
  character or the length, never the value. Out of scope, both ADR 0143: RFC 2045 quoting of
  `Accept`, and types the HMC itself rejects.

## Success

1. A 129-character grammar-valid type is refused; 128 characters is accepted.
2. The refusal names the argument, the value's length, and the maximum.
3. `_child_path` raises `ValueError` on a grammar-invalid `child_type`, building no path.

## Validation

- Length bound — Mode: focused-test. `tests/unit/test_request_path_safety.py`, parametrized
  at 128, 129 and 1 MiB. Red: 129 and 1 MiB are accepted today; 128 passes either way and
  pins the boundary. Green: `uv run --no-sync pytest tests/unit/test_request_path_safety.py`.
- `_child_path` type refusal — Mode: focused-test. `tests/unit/test_client_users.py`. Red: it
  returns a path for `"UserProfile?group=x"`. Green:
  `uv run --no-sync pytest tests/unit/test_client_users.py`.
- Relocation keeps every call site — Mode: focused-test. The drift and grammar tests import
  from the new home and stay green under the command above.
