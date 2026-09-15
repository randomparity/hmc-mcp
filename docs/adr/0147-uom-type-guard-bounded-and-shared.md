# ADR 0147: The uom type guard carries a length bound and lives where mixins can reach it

## Status

Accepted

Amends [ADR 0143](0143-uom-type-segments-validated-not-encoded.md): the predicate that record
placed in `src/hmc_mcp/client/core.py` moves to `src/hmc_mcp/client/client_contracts.py`, and
its grammar gains a length bound.

## Context

ADR 0143 validates a uom type segment against `[A-Za-z][A-Za-z0-9]*` with `fullmatch`. That
bounds the character set and not the size, so a caller passing a megabyte of `A` gets a
megabyte-long URL built and a megabyte-long `Accept` header sent. Issue #820 asks that the bound
be stated against the #808 precedent — `_MAX_REPORTED_NAME_LENGTH = 64`, which truncates
operator-supplied names in a refusal message — rather than picked fresh.

They are not the same kind of bound, and that is what decides the value. A display bound is cheap
to get wrong either way: too low only shortens a message. An acceptance bound is not symmetric —
too low refuses an operation that would have worked, and ADR 0143 already records an accepted
unknown here: the grammar comes from the vendored V10/V11 corpora and this client's own call set,
not from a published list of HMC type names. A tight bound adds a second way to refuse a name
nobody enumerated.

The same issue reports that `client_users._child_path` interpolates a `child_type` into a uom
path outside `core.py`, where ADR 0143's drift tests walk. Its seven call sites pass three
literals; six then hand the same literal to `_get`, `_put` or `_post`, which route it through
`_uom_headers` into the existing check, so the type is validated today only because those callers
pass one value twice. `delete_hmc_user` calls `_delete`, which sends `_uom_headers(None)`, and has
no check at all. Routing `_child_path` through the predicate is blocked by module structure:
`core.py` is the composition root and imports every mixin before it defines the predicate.

## Decision

**The type grammar carries an explicit length bound, checked first and reported separately; and
the predicate moves to the leaf module the mixins can import.**

`_MAX_UOM_TYPE_LENGTH = 128` bounds an accepted type segment. `_reject_unknown_uom_type` checks
the length before the character class and reports it with its own detail — the value's length and
the maximum — because the existing detail branch cannot describe an over-long value that is
otherwise grammar-valid. Against a megabyte every candidate bound performs identically, so the
choice is decided by the headroom it leaves rather than by how tightly it fits: 128 is four times
the longest type name this repository carries, which is the margin the accepted unknown above
asks for. It is deliberately not `_MAX_REPORTED_NAME_LENGTH`, whose concern is message
truncation; coupling them would make a change to either a change to both.

`_reject_unknown_uom_type` and `_UOM_TYPE` move to `client_contracts.py`, which already hosts
`validate_adapter_type` — the sibling refusal ADR 0143's own docstring names as the same family —
and which `client_users.py` already imports. `core.py` imports the predicate, so its sixteen call
sites are unchanged and the drift test's `ast.Name` match still resolves.
`UsersMixin._child_path` calls it on `child_type`, so the guarantee stops depending on a caller
passing the same value to two different parameters.

## Consequences

- An over-long type segment is refused in this process, in both destinations ADR 0143 names,
  before any request is built.
- A real HMC type name longer than 128 characters would now be refused locally — the same
  accepted unknown ADR 0143 states for the character class, with the same remedy: widen the
  constant in one place, with the refused name as the evidence.
- `client_contracts.py` gains a private predicate beside its protocols and constants, and stays a
  leaf module — stdlib, `httpx`, `..config` — so no import cycle is introduced.
- `_child_path`'s guarantee is now a runtime refusal rather than a test-time observation, so it
  no longer depends on drift tests that do not walk its file. `client_lpm._lpar_job`'s
  `operation` is the one other non-UUID segment interpolated into a uom path outside `core.py`;
  it needs a grammar of its own rather than this one, and is a follow-up candidate.
- `_KNOWN_UOM_SEGMENT_ARGUMENTS` and `_uom_path_sites()` are untouched, leaving #818 a clean edit.
- This bounds the type segment only. `search_uom` percent-encodes `property_value` without
  bounding it — the same defect family on the same line, with no owner, and a follow-up candidate.

## Considered & rejected

- **Reuse `_MAX_REPORTED_NAME_LENGTH = 64`.** judgment: a display bound and an acceptance bound
  fail in opposite directions and want different headroom, so sharing the constant couples two
  changes that should move independently. Also excluded by the issue's approved scope.
- **Bound at or just above the observed maximum — 32 or 40.** verified: the longest
  grammar-matching literal in `src/` is 32 characters, `VirtualFibreChannelClientAdapter`, also
  the longest `AdapterType` member (`rg -o '"[A-Za-z][A-Za-z0-9]*"' -g '*.py' src/` at
  `4d823cbb`). judgment: spends the whole margin the accepted unknown asks for, and buys nothing
  against a megabyte that 128 does not.
- **Fold the bound into the pattern as `[A-Za-z][A-Za-z0-9]{0,127}`.** verified: the existing
  detail branch reports `a value containing 'A'` for an over-long all-alphanumeric value, because
  `next(...)` finds no offending character and falls back to `value[0]`. judgment: one fewer
  branch, for a message naming a rule the value did not break.
- **No bound; let the HMC refuse it.** verified: ADR 0143 records HTTP 400 `INVALID_URL` at
  V1_17_0 for an unknown type. judgment: that refusal arrives after the megabyte was built and
  sent, which is the cost the issue is about.
- **Import the predicate from `core` at `client_users` module level.** verified:
  `ImportError: cannot import name '_reject_unknown_uom_type' from partially initialized module
  'hmc_mcp.client.core' (most likely due to a circular import)`, from
  `uv run --no-sync python -c "import hmc_mcp.client.core"` with that import added at `4d823cbb`.
- **A function-local import inside `_child_path`.** verified: it lints clean —
  `ruff check --config pyproject.toml` exits 0, PLC0415 being neither a ruff 0.16 default nor in
  this repository's `extend-select`. judgment: it encodes the cycle rather than removing it, runs
  on every call, and the next mixin needing the predicate repeats it.
- **Move `_child_path` into `core.py` instead of the predicate.** verified: `UsersClient` declares
  `_child_path` (`client_contracts.py:396`) and every call is `self._child_path(...)`, so the MRO
  would resolve it from `HMCClient`. judgment: it splits `UsersMixin`'s path building from the
  seven methods using it, and puts a users-specific builder in the composition root, to avoid
  touching a leaf module that already hosts the sibling predicate.
- **Duplicate the grammar in `client_users.py`.** judgment: two copies of one grammar drift apart,
  which is the defect family this record exists to close.
- **Widen `_uom_path_sites()` to walk the whole client package.** verified: the same walk over
  `src/hmc_mcp/client/*.py` surfaces eight further interpolated names that
  `_KNOWN_UOM_SEGMENT_ARGUMENTS` does not classify — `cluster_uuid`, `console_path_id`,
  `lpar_uuid`, `network_uuid`, `operation`, `system_uuid`, `vg_uuid`, `vios_uuid` — each needing
  its own rule. judgment: it buys a test-time observation where routing buys a runtime refusal.
  Excluded by the issue's approved scope.
- **Leave `_child_path` alone.** judgment: six of its seven sites are guarded only by callers
  passing one value to two parameters, and the seventh is not guarded at all.
