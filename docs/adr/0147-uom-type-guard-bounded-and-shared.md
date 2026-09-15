# ADR 0147: The uom type guard carries a length bound and lives where mixins can reach it

## Status

Accepted

Amends [ADR 0143](0143-uom-type-segments-validated-not-encoded.md): the predicate that record
placed in `src/hmc_mcp/client/core.py` moves to `src/hmc_mcp/client/client_contracts.py`, and
its grammar gains a length bound.

## Context

ADR 0143 validates a uom type segment against `[A-Za-z][A-Za-z0-9]*` with `fullmatch`. That
bounds the character set and not the size, so a caller passing a megabyte of `A` gets a
megabyte-long URL built and a megabyte-long `Accept` header sent. Issue #820 asks that the
bound be stated against the #808 precedent — `_MAX_REPORTED_NAME_LENGTH = 64`, which truncates
operator-supplied names in a refusal message — rather than picked fresh.

The two are not the same kind of bound, and that is what decides the value. A display bound is
cheap to get wrong in both directions: too low only shortens a message. An acceptance bound is
not symmetric. Too high leaves an absurd value on the wire; too low refuses an operation that
would have worked, and ADR 0143 already records an accepted unknown here — the grammar comes
from the vendored V10/V11 corpora and this client's own call set, not from an authoritative
list of HMC type names, and no such list is published. A tight acceptance bound would add a
second way to refuse a type name nobody enumerated.

The same issue reports that `client_users._child_path` interpolates a `child_type` into a uom
path outside `core.py`, which is where ADR 0143's AST drift tests walk. Both callers pass
literals, so nothing is reachable today. Routing it through the existing predicate is blocked
by module structure: `core.py` is the composition root and imports every mixin, including
`client_users`, before it defines the predicate.

## Decision

**The type grammar carries an explicit length bound, checked first and reported separately; and
the predicate moves to the leaf module the mixins can import.**

`_MAX_UOM_TYPE_LENGTH = 128` bounds an accepted type segment. `_reject_unknown_uom_type` checks
the length before the character class and reports it with its own detail — the value's length
and the maximum — because the existing detail branch cannot describe an over-long value that is
otherwise grammar-valid. The value is four times the longest type name this repository carries,
which leaves room for a type name twice as long as any observed one while reducing the worst
case from unbounded to 128 characters. It is deliberately not `_MAX_REPORTED_NAME_LENGTH`: that
constant governs message truncation, the asymmetry above makes an acceptance bound want more
headroom than a display bound, and coupling them would make a change to either a change to both.

`_reject_unknown_uom_type` and `_UOM_TYPE` move to `client_contracts.py`, which already hosts
`validate_adapter_type` — the sibling refusal ADR 0143's own docstring names as the same family
— and which `client_users.py` already imports. `core.py` imports the predicate, so its fifteen
call sites are unchanged and the drift test's `ast.Name` match still resolves.
`UsersMixin._child_path` calls it on `child_type`, so a future non-literal caller is refused
before a path is built rather than being caught by a test that does not watch the file.

## Consequences

- An over-long type segment is refused in this process, in both destinations ADR 0143 names,
  before any request is built.
- A real HMC type name longer than 128 characters would now be refused locally. This is the
  same accepted unknown ADR 0143 states for the character class, with the same remedy: widen the
  constant in one place, with the refused name as the evidence.
- `client_contracts.py` gains a private predicate alongside its protocols and constants. It
  remains a leaf module — stdlib, `httpx`, and `..config` — so no import cycle is introduced.
- The drift tests still walk only `core.py`. `_child_path` no longer depends on them, because
  its guarantee is now a runtime refusal rather than a test-time observation. `child_type` is
  the only *type* segment interpolated into a uom path outside `core.py`; of the eight other
  names interpolated there, seven are UUIDs or a quoted console id, and the eighth is
  `client_lpm._lpar_job`'s `operation` — a job-operation segment under no grammar, whose five
  callers all pass literals. That one is the same drift-risk shape as `_child_path` was, needs
  a grammar of its own rather than this one, and is reported as a follow-up candidate.
- `_KNOWN_UOM_SEGMENT_ARGUMENTS` and `_uom_path_sites()` are untouched, leaving #818 a clean
  edit to the same functions.

## Considered & rejected

- **Reuse `_MAX_REPORTED_NAME_LENGTH = 64` as the acceptance bound.** judgment: a
  display-truncation bound and an acceptance bound fail in opposite directions and want
  different headroom, so sharing the constant would couple two changes that should move
  independently. Also excluded by the issue's approved scope.
- **Bound at or just above the observed maximum — 32 or 40.** verified: the longest
  grammar-matching string literal in `src/` is 32 characters, `VirtualFibreChannelClientAdapter`,
  which is also the longest `AdapterType` member (`rg -o '"[A-Za-z][A-Za-z0-9]*"' -g '*.py' src/`
  at `4d823cbb`). judgment: a bound that tight buys nothing against a megabyte and spends the
  whole margin ADR 0143's accepted unknown asks for.
- **Fold the bound into the pattern as `[A-Za-z][A-Za-z0-9]{0,127}`.** verified: the existing
  detail branch reports `a value containing 'A'` for an over-long all-alphanumeric value, because
  `next(...)` finds no offending character and falls back to `value[0]`. judgment: one fewer
  branch, in exchange for a message naming a rule the value did not break.
- **No bound; let the HMC refuse it.** verified: ADR 0143 records HTTP 400 `INVALID_URL` at
  V1_17_0 for an unknown type. judgment: that refusal arrives after the megabyte has been built
  and sent, which is the cost the issue is about.
- **Import the predicate from `core` at `client_users` module level.** verified:
  `ImportError: cannot import name '_reject_unknown_uom_type' from partially initialized module
  'hmc_mcp.client.core' (most likely due to a circular import)`, from
  `uv run --no-sync python -c "import hmc_mcp.client.core"` with that import added at `4d823cbb`.
- **A function-local import inside `_child_path`.** judgment: it encodes the cycle rather than
  removing it, needs a lint suppression, and the next mixin needing the predicate repeats it.
- **Duplicate the grammar in `client_users.py`.** judgment: two copies of one grammar drift
  apart, which is the defect family this record exists to close.
- **Widen `_uom_path_sites()` to walk the whole client package.** verified: the same walk over
  `src/hmc_mcp/client/*.py` surfaces eight further interpolated names that
  `_KNOWN_UOM_SEGMENT_ARGUMENTS` does not classify — `cluster_uuid`, `console_path_id`,
  `lpar_uuid`, `network_uuid`, `operation`, `system_uuid`, `vg_uuid`, `vios_uuid` — each needing
  its own rule. judgment: it also buys only a test-time observation, where routing through the
  predicate buys a runtime refusal. Excluded by the issue's approved scope.
- **A new module for the predicate.** judgment: a file for one predicate when an existing leaf
  module already hosts its sibling.
- **Leave `_child_path` alone.** judgment: its callers pass literals by convention and nothing
  holds them to it, which is the "agreeing by accident with no record" ADR 0143 was written
  against.
