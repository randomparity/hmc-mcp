# Plan: quick-property name path rule (issue #818)

**Goal.** Percent-encode `property_name` where `get_quick_property` builds a uom
path, and replace its vacuous pre-classification in the request-path-safety tests
with a site-directed check that the encoding is actually present.

**Architecture.** `HMCClient` is composed from mixins in `src/hmc_mcp/client/`.
Request paths are built by f-string interpolation in `core.py` and handed to
`_request`, which applies `_reject_dot_segments` at the transport waist.
Per-argument rules live where the segment is built — a predicate call for a type
segment, a `quote(..., safe="")` binding for data — and AST walks in
`tests/unit/test_request_path_safety.py` hold those declarations to the sites.

**Tech stack.** Python 3.11+, httpx 0.28.1, pytest, `uv`, `just`, `prek`.

## Global Constraints

- `BASE_BRANCH` is `main`. Branch: `feat/quick-property-name-path-rule-818`.
- Worktree: `/Volumes/Source Code Volume/src/hmc-mcp-worktrees/feat-quick-property-name-path-rule-818`.
  Bootstrap with `just setup` only — never a bare `uv sync`/`uv run`/`uv add`
  (AGENTS.md, "Worktree venv hygiene": a bare sync prunes the `app` extra).
- Guardrails: `just verify` (full suite), then `uv run --no-sync prek run --all-files`.
  Narrow a `static` failure with `just lint`, `just typecheck`, `just adr-numbering`,
  `just doc-freshness`.
- The decision is ADR 0146,
  `docs/adr/0146-quick-property-names-are-percent-encoded.md`. The number is
  orchestrator-assigned; do not renumber. No ADR index exists in this repository,
  so no index row is written.
- A guard or binding must be recognisable to the AST walks: call `quote` as a
  bare name, never as a qualified attribute. Each task's Interfaces block names
  the existing symbols it borrows and the signature assumed for each.
- Expected implementation size: 70–110 changed lines (M) — derived from the file
  map below: two production lines in `core.py`, one helper widened and four tests
  added in `test_request_path_safety.py`.

## File map

- `src/hmc_mcp/client/core.py` (modified, Task 1) — `get_quick_property`
  interpolates `encoded_property` instead of raw `property_name`.
- `tests/unit/test_request_path_safety.py` (modified, Tasks 1 and 2) — classifies
  and site-checks the encoded class instead of naming `property_name`, and gains
  the character, no-op and dot-segment cases.
- `docs/adr/0146-quick-property-names-are-percent-encoded.md` (created, written).

No caller migration: the signature and return are unchanged and all four `src/`
call sites pass the literal `"PartitionState"`. No obsolete path to remove, no
compatibility path retained.

---

## Task 1 — Encode `property_name` at the call site

**Files.** Modifies `src/hmc_mcp/client/core.py`. Tests
`tests/unit/test_request_path_safety.py`.

**Interfaces.** Consumes `quote` (already imported, `core.py:18`). Produces a
local `encoded_property` inside `get_quick_property`; Task 2 relies on that exact
name being the interpolated one. `get_quick_property`'s public signature
`(self, resource_type: str, uuid: str, property_name: str, *, validate: bool = False) -> str | None`
does not change.

**Where it fits.** This is the decision ADR 0146 records, applied. Task 2 then
makes the test suite able to see that it is present.

**Verification.**

- Contract: *the whole of `property_name` stays inside the last path segment*.
  Mode: `focused-test`. Test
  `test_a_quick_property_name_cannot_re_point_the_request` in
  `tests/unit/test_request_path_safety.py`, parametrized over `?`, `#`, `/`,
  space, a non-ASCII character and CRLF. Expected red before the `core.py` edit: the
  `?` case builds a URL whose query is `group=None`; the `#` case builds a URL
  whose last segment is `PartitionState`, losing the rest; the CRLF case raises
  `httpx.InvalidURL` before any URL exists. Green:
  `uv run --no-sync pytest tests/unit/test_request_path_safety.py -k quick_property -q`
  → all selected tests pass.
- Contract: *no wire-format change for the names this repository passes*.
  Mode: `focused-test`. Test
  `test_encoding_is_a_no_op_on_the_quick_property_names_this_client_passes`,
  asserting the built URL for `"PartitionState"` ends exactly
  `/rest/api/uom/LogicalPartition/<UUID_A>/quick/PartitionState`, and that
  `quote(n, safe="") == n` for each of
  `PartitionState`, `PartitionID`, `PartitionName`, `SystemType`, `RMCState`,
  `NoSuchProperty`, `all`, `All`. Expected red only if the encoding altered a
  legitimate name. Same green command.
- Contract: *the dot-segment refusal identity*. Mode: `focused-test`. Test
  `test_a_dot_segment_quick_property_name_is_still_refused`, asserting `HMCError`
  for `".."`, `"."` and `"../../x"` and that no request is built. Expected red if
  encoding had moved that refusal. Same green command.

**Steps.**

1. Write the three tests above in `tests/unit/test_request_path_safety.py`, in a
   new section after the existing `# The type-segment length bound (ADR 0147)`
   section. Build the client with the module's existing `_client()` helper and
   capture the URL by replacing `client._http.build_request` with a function that
   records `str(...)` of a URL built from the same base, as
   `test_no_unsafe_type_segment_reaches_transport` does for the transport.
2. Run `uv run --no-sync pytest tests/unit/test_request_path_safety.py -k quick_property -q`.
   Expect failures for the `?`, `#` and CRLF cases, exactly as stated above.
3. In `src/hmc_mcp/client/core.py`, inside `get_quick_property`, replace

   ```python
   path = f"/rest/api/uom/{resource_type}/{uuid}/quick/{property_name}"
   ```

   with

   ```python
   encoded_property = quote(property_name, safe="")
   path = f"/rest/api/uom/{resource_type}/{uuid}/quick/{encoded_property}"
   ```

   Leave the `validate` branch above it untouched: it checks the caller's name,
   not the encoded one.
4. Extend the method's docstring with one paragraph: the name is percent-encoded
   before interpolation because the path is its only destination, citing ADR 0146.
5. Re-run the command from step 2. Expect all selected tests to pass.
6. Run `uv run --no-sync pytest tests/unit/test_request_path_safety.py tests/unit/test_client.py -q`.
   Expect no failures — no existing test pins a non-alphanumeric quick-property
   path.
7. Commit: `fix(client): percent-encode the quick-property name path segment`.

**Acceptance.** `get_quick_property` interpolates `encoded_property`; the three
tests pass; no other production file changed.

---

## Task 2 — Make the segment classification non-vacuous

**Files.** Modifies `tests/unit/test_request_path_safety.py` only.

**Interfaces.** Consumes, from the same module, `_is_quote_binding(node: ast.AST)
-> str | None` (returns the name a literal `x = quote(x, safe="")` statement
binds — reuse it, do not write a second one) and `_uom_path_sites() ->
tuple[list[tuple[str, str]], dict[str, set[str]]]`; plus the `encoded_property`
local Task 1 introduced. Widens `_uom_path_sites()` to return
`tuple[list[tuple[str, str]], dict[str, set[str]], dict[str, set[str]]]` —
interpolations, type-guarded names per function, quote-bound names per function.
Both existing callers (`test_every_uom_type_interpolation_is_guarded`,
`test_every_uom_path_interpolation_is_a_known_argument`) must be updated to
unpack three values.

**Where it fits.** Task 1 made the encoding present; this makes the suite able to
tell that it is, and removes the classification that made the unclassified-segment
assertion pass for this argument.

**Verification.**

- Contract: *`property_name` is no longer a classified uom path segment*.
  Mode: `focused-test`. The existing
  `test_every_uom_path_interpolation_is_a_known_argument` — red if `core.py` still
  interpolated `property_name` after the set entry is removed. Green:
  `uv run --no-sync pytest tests/unit/test_request_path_safety.py -q`.
- Contract: *an encoded-class segment is quote-bound at its own site*.
  Mode: `focused-test`. New test
  `test_every_encoded_uom_segment_is_quote_bound`. Expected red observation is
  taken by controlled fault injection, since the tree is green by construction:
  delete the `encoded_property = quote(...)` line in `get_quick_property` and
  interpolate `property_name` directly, run the command, observe the test name in
  the failure output, then revert. Green: the same command.

**Steps.**

1. In `tests/unit/test_request_path_safety.py`, remove `"property_name"` from
   `_KNOWN_UOM_SEGMENT_ARGUMENTS`.
2. Add, beside `_TYPE_SEGMENT_ARGUMENTS`:

   ```python
   _ENCODED_SEGMENT_ARGUMENTS = frozenset({"encoded_property", "encoded_value"})
   ```

   and include it in `_KNOWN_UOM_SEGMENT_ARGUMENTS` in place of the two names
   listed there individually today, so one set defines the encoded class.
3. In `_uom_path_sites`, collect quote bindings in the same walk that already
   collects `_is_boundary_check` calls: for each node, when
   `(name := _is_quote_binding(node)) is not None`, add `name` to
   `quote_bound.setdefault(owner.name, set())`. Return the third dict. Update the
   docstring to say the walk now yields three things, and update both existing
   callers to unpack three values.
4. Add `test_every_encoded_uom_segment_is_quote_bound`, asserting that for every
   `(function, name)` interpolation where `name in _ENCODED_SEGMENT_ARGUMENTS`,
   `name in quote_bound.get(function, set())`. Its docstring states what it does
   not cover: the walk sees a literal `quote(<name>, safe="")` assignment and
   nothing else, so a binding built any other way is invisible — the same stated
   limit `test_every_uom_type_interpolation_is_guarded` carries.
5. Run `uv run --no-sync pytest tests/unit/test_request_path_safety.py -q`.
   Expect all tests to pass.
6. Perform the fault injection named in Verification. Record the observed failure
   in the run report, then revert with `git checkout -- src/hmc_mcp/client/core.py`
   and re-run step 5 to confirm green again.
7. Commit: `test(client): site-check the encoded uom segment classification`.

**Acceptance.** `property_name` is absent from every classification set;
`test_every_encoded_uom_segment_is_quote_bound` covers `get_quick_property`'s
`encoded_property` and `search_uom`'s `encoded_property`/`encoded_value`; the
fault injection was observed red and reverted.

---

## Final

Run `just verify`, then `uv run --no-sync prek run --all-files` — both bare, with
no pipe, so the exit code is the command's own. Both must exit 0.
