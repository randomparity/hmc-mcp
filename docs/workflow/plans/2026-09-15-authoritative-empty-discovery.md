# Authoritative empty discovery answers — implementation plan

**Goal.** Stop `HMCClient`'s two opt-in pre-flight caches from collapsing "the type defines
nothing" into "the names are unknown", so `validate=True` refuses locally on a type that defines
nothing and says so.

**Architecture.** Both discovery reads (`list_search_parameters`, `list_quick_properties`) already
consult a container element when they find no name; they then return `([], version)` for that and
for a 204 alike. Each read instead returns `None` in place of the list when the level's answer is
not a fact about the type, and each cache (`_defined_search_parameter_names`,
`_defined_quick_property_names`) stores `None` only for that. Everything else — the locks, the
cache key and lifetime, the `HMCError` degradation, the opt-in default — is untouched.

**Tech stack.** Python 3.11 (`.python-version`), `httpx` + `respx`, `pytest`/`pytest-asyncio`,
`uv`, `just`, `ruff`, `ty`.

Expected implementation size: 180–260 changed lines (M) — derived from the file map below: two
symmetric ~30-line source edits with their docstrings, four new or reshaped test cases per twin,
and three prose-record edits.

## Global Constraints

- **Never run bare `uv sync`, `uv run`, or `uv add`.** `just setup` is the only sync recipe
  (AGENTS.md, *Worktree venv hygiene*). Every ad-hoc command uses `uv run --no-sync`.
- **Guardrails.** `just lint`, `just typecheck`, `just test`, `just smoke`; `just verify` is the
  full pre-push suite; CI additionally runs `uv run --no-sync prek run --all-files`.
- **ADR number 0144 is assigned.** Do not renumber anything. There is no ADR index.
- A pre-existing failing test is fixed in this PR, not deferred (AGENTS.md).
- Ambient `HMC_*` environment variables leak into `HMCConfig`; a config test failing oddly is that
  first (AGENTS.md, *Pre-existing test failures*).
- Diff against the merge base: `git --no-pager diff "$(git merge-base HEAD origin/main)"`.
- Decision record: `docs/adr/0144-container-present-empty-discovery-is-authoritative.md`.
  Specification: `docs/workflow/specs/2026-09-15-authoritative-empty-discovery-design.md`.

## File map

| File | Owns now | Owns after |
|---|---|---|
| `src/hmc_mcp/client/core.py` | both discovery reads, both caches, both refusal messages, `_summarize_names` | the same, with the discriminated return contract |
| `tests/unit/test_client.py` | the unit contract for all of the above | the same, plus the unknown-answer and defines-nothing cases |
| `CHANGELOG.md` | Unreleased entries for the four methods | the same, rewritten to the shipped behaviour |
| `docs/workflow/specs/2026-09-15-discover-search-parameters-design.md` | the #789 failure model, which accepts this gap | the same, with that entry struck through and pointed at ADR 0144 |
| `docs/adr/0141-*.md`, `docs/adr/0142-*.md` | the amended decisions | the same, plus a Status banner each (already written) |

No file is created, moved, or removed. No caller migrates: neither `list_*` method has a caller in
`src/` outside `core.py`, and neither is an ADR 0118 facade name, so no compatibility path is kept.

## Task 1 — the search twin

**Interfaces.** Consumes `_find_all_text`, `_SEARCH_PARAMETER_NAME_ELEMENT`,
`_SEARCH_PARAMETER_CONTAINER_ELEMENT`, `_summarize_names`, `HMCError` — all already in
`src/hmc_mcp/client/core.py`. Produces `HMCClient.list_search_parameters(resource_type) ->
tuple[list[str] | None, str | None]` and `HMCClient._defined_search_parameter_names(resource_type)
-> frozenset[str] | None`, which Task 2 mirrors but does not import.

**Files.** Modifies `src/hmc_mcp/client/core.py`, `tests/unit/test_client.py`, `CHANGELOG.md`,
`docs/workflow/specs/2026-09-15-discover-search-parameters-design.md`.

### Verification

- **`list_search_parameters` discriminates its three empty answers.** Mode: `focused-test`.
  Observable: `([], "V1_0")` for container + no `ParameterName` element; `(None, "V1_0")` for a 204
  and for `ParameterName` elements that are all empty. Cases:
  `test_list_search_parameters_empty_set_returns_no_names` (kept, one parametrization) and
  `test_list_search_parameters_unknown_answer_returns_none` (new, two).
  Red: `assert ([], 'V1_0') == (None, 'V1_0')`.
  Green: `uv run --no-sync pytest tests/unit/test_client.py -k list_search_parameters -q`.
- **The cache stores `frozenset()` for the authoritative empty answer and refuses on it.**
  Mode: `focused-test`. Observable: `search_uom(..., validate=True)` raises `ValueError` ending
  `The type defines none at all.` with the instance-search route unused. Case:
  `test_search_uom_validate_refuses_a_type_defining_nothing` (new).
  Red: no exception, and that route records one call.
  Green: `uv run --no-sync pytest tests/unit/test_client.py -k search_uom_validate -q`.
- **The cache still stores `None` for 204, all-empty and `HMCError`.** Mode: `focused-test`.
  Observable: `test_search_uom_validate_degrades_and_caches_the_failure` passes with a fifth
  parametrization, `empty-elements`. It is a regression guard, so it passes before the change too;
  its red is taken by faulting the all-empty branch to return `[]` and observing `ValueError`.
  Green: `uv run --no-sync pytest tests/unit/test_client.py -k search_uom_validate -q`.
- **`CHANGELOG.md` and the #789 spec entry describe the shipped behaviour.**
  Mode: `task-test-not-applicable`. Reason: both are prose records with no executable consumer —
  `just doc-freshness` reads only a generated document's first-line banner, and neither file
  carries one — so the only possible test would search for wording.

### Steps

1. Read `src/hmc_mcp/client/core.py:1116-1182` and `1033-1040` so the edits below land on the
   current text.
2. Write `test_list_search_parameters_unknown_answer_returns_none` in
   `tests/unit/test_client.py`, immediately after
   `test_list_search_parameters_empty_set_returns_no_names`, parametrized over a 204 response and
   over `_search_parameter_entry(_EMPTY_SET_TYPE, "", "   ")`, asserting
   `await hmc.list_search_parameters(_EMPTY_SET_TYPE) == (None, "V1_0")`. Reduce
   `test_list_search_parameters_empty_set_returns_no_names` to its first parametrization, whose
   assertion stays `([], "V1_0")`, and move its `if n`-filter comment to the new test with its
   conclusion corrected: without the filter the comprehension yields `["", ""]`, a *non-empty*
   positive set holding only `""`, which rejects every real name.
3. Run `uv run --no-sync pytest tests/unit/test_client.py -k list_search_parameters -q`.
   Expect the new test to fail with `assert ([], 'V1_0') == (None, 'V1_0')`.
4. In `list_search_parameters`, replace the body from `if resp.status_code == 204:` to the final
   `return` with:

   ```python
           if resp.status_code == 204:
               return None, schema_version
           if resp.status_code != 200:
               raise HMCError(f"GET {path} failed", resp.status_code, resp.text)
           found = _find_all_text(
               resp.text, f"GET {path}", _SEARCH_PARAMETER_NAME_ELEMENT
           )
           names = [n for n in found if n]
           if names:
               return names, schema_version
           # The container separates "defines none" from "not this shape at all",
           # and is only consulted when no name was found. Its presence alone is
           # tested: it carries no text of its own, so a text filter would reject
           # the very body this distinguishes.
           if not _find_all_text(
               resp.text, f"GET {path}", _SEARCH_PARAMETER_CONTAINER_ELEMENT
           ):
               raise HMCError(
                   f"GET {path} returned no {_SEARCH_PARAMETER_CONTAINER_ELEMENT} "
                   "element; expected the search parameters the type defines",
                   resp.status_code,
                   resp.text,
               )
           # Container and no ParameterName element at all: the type defines no
           # search parameters, and that is a fact about the type (ADR 0144).
           # Elements present but every text empty is the parse-artefact shape
           # instead -- the container holds parameters this parse cannot name --
           # so it reads as unknown, like a 204.
           return ([] if not found else None), schema_version
   ```

5. Update that method's docstring: the paragraph beginning **A 200 with no name is not always an
   error** now states the three-way answer, and the `Returns` paragraph states that the first
   element is `None` when the answer is not a fact about the type.
6. In `_defined_search_parameter_names`, replace the `except HMCError:` body's `names = []` with
   `names = None`, and the `defined = frozenset(names) if names else None` line and its comment
   with:

   ```python
           # None is the level's answer not being a fact about the type: a 204, a
           # failed read, or a container whose ParameterName elements are all
           # empty. An empty list is a fact -- the six captured types defining
           # nothing -- and caching it as an empty positive set is what makes
           # validate=True refuse locally there (ADR 0144).
           defined = None if names is None else frozenset(names)
   ```

   Update the docstring's second sentence to say the empty positive set is cached for a type
   defining nothing.
7. In `search_uom`, replace the `validate` block's `raise ValueError(...)` with:

   ```python
               if defined is not None and property_name not in defined:
                   detail = (
                       f"Defined names: {_summarize_names(defined)}"
                       if defined
                       else "The type defines none at all."
                   )
                   raise ValueError(
                       f"{resource_type} defines no search parameter named "
                       f"{property_name!r}. {detail}"
                   )
   ```

   Replace the docstring's final paragraph (`A type defining no search parameters reads as
   unknown…`) with one saying such a type is refused locally, and that a 204, a failed read, and a
   container with no usable name still validate nothing (ADR 0144).
8. Add an `"empty-set"` branch to `_mock_search_validation_routes`' `discovery` argument,
   answering `httpx.Response(200, text=_search_parameter_entry(_SEARCH_VALIDATION_TYPE))`, and
   note it in the helper's docstring beside the existing `200`/`204` sentence.
9. Write `test_search_uom_validate_refuses_a_type_defining_nothing`: mock with
   `discovery="empty-set"`, call `search_uom(_SEARCH_VALIDATION_TYPE, "PartitionName", "web",
   validate=True)` inside `pytest.raises(ValueError)`, assert the message ends
   `The type defines none at all.` and contains no `": ."`, and assert `defined.call_count == 0`.
10. Add the `empty-elements` parametrization to
    `test_search_uom_validate_degrades_and_caches_the_failure`, answering the discovery route with
    `_search_parameter_entry(_SEARCH_VALIDATION_TYPE, "", "   ")`, and correct that test's
    docstring sentence about the 204 being "the one that would fail *closed*": a 204 now returns
    `(None, version)`, and the all-empty body is the case that would fail closed if the parse
    treated it as authoritative.
11. Run `uv run --no-sync pytest tests/unit/test_client.py -k "list_search_parameters or
    search_uom" -q`. Expect every case to pass.
12. Rewrite the `CHANGELOG.md` Unreleased entries for `HMCClient.list_search_parameters` and
    `HMCClient.search_uom`: the read returns `None` rather than a list when the answer is not a
    fact about the type, and `validate=True` refuses locally on a type defining none. Keep every
    other claim in both entries.
13. In `docs/workflow/specs/2026-09-15-discover-search-parameters-design.md`, strike through the
    *Failure model* entry headed `**validate=True performs no check on a type that defines
    nothing.**` and append one sentence naming ADR 0144 as where it was taken, matching the
    struck-through entry above it in the same list.
14. Run `just lint`, `just typecheck`, and `just test`. Expect all three green.
15. Commit: `fix(client): refuse locally when a type defines no search parameters`.

**Acceptance.** `list_search_parameters` returns `([], v)`, `(None, v)` and `HMCError` per
ADR 0144's table; `search_uom(..., validate=True)` raises `ValueError` ending `The type defines
none at all.` on `_EMPTY_SET_TYPE` without a transport call; every pre-existing `search_uom` test
passes unmodified except the two named above.

## Task 2 — the quick twin

**Interfaces.** Consumes `_find_all_text`, `_QUICK_PROPERTY_CONTAINER_ELEMENT`, the literal
`"Nickname"`, `_summarize_names`, `HMCError` — all already in `src/hmc_mcp/client/core.py`.
Produces `HMCClient.list_quick_properties(resource_type, *, parent_type=None, parent_uuid=None) ->
tuple[list[str] | None, str | None]` and `HMCClient._defined_quick_property_names(resource_type)
-> frozenset[str] | None`.

**Files.** Modifies `src/hmc_mcp/client/core.py`, `tests/unit/test_client.py`, `CHANGELOG.md`.

### Verification

- **`list_quick_properties` discriminates its three empty answers.** Mode: `focused-test`.
  Observable: `([], "V1_0")` for container + no `Nickname` element; `(None, "V1_0")` for a 204 and
  for `Nickname` elements that are all empty. Cases:
  `test_list_quick_properties_empty_set_returns_no_names` (kept, one parametrization),
  `test_list_quick_properties_unknown_answer_returns_none` (new, two), and
  `test_list_quick_properties_204_returns_no_names` (assertion updated).
  Red: `assert ([], 'V1_0') == (None, 'V1_0')`.
  Green: `uv run --no-sync pytest tests/unit/test_client.py -k list_quick_properties -q`.
- **The cache stores `frozenset()` for the authoritative empty answer and refuses on it.**
  Mode: `focused-test`. Observable: `get_quick_property(..., validate=True)` raises `ValueError`
  ending `The type defines none at all.` with the value route unused. Case:
  `test_get_quick_property_validate_refuses_a_type_defining_nothing` (new).
  Red: no exception, and that route records one call.
  Green: `uv run --no-sync pytest tests/unit/test_client.py -k get_quick_property_validate -q`.
- **The cache still stores `None` for 204, all-empty and `HMCError`.** Mode: `focused-test`.
  Observable: `test_get_quick_property_validate_degrades_and_caches_the_failure` passes with a
  fifth parametrization, `empty-elements`; its red is taken the same way as Task 1's.
  Green: `uv run --no-sync pytest tests/unit/test_client.py -k get_quick_property_validate -q`.
- **`_summarize_names`' precondition ground.** Mode: `task-test-not-applicable`.
  Reason: the code is unchanged and the edit is one docstring sentence explaining why a stated
  precondition still holds; no executable or structural observation of it could fail.

### Steps

1. Read `src/hmc_mcp/client/core.py:954-994`, `874-907`, `848-856` and `69-77` so the edits below
   land on the current text.
2. Write `test_list_quick_properties_unknown_answer_returns_none` immediately after
   `test_list_quick_properties_empty_set_returns_no_names`, parametrized over a 204 response and
   over `_quick_property_entry("ManagedSystem", ("", ""), ("   ", ""))`, asserting
   `await hmc.list_quick_properties("ManagedSystem") == (None, "V1_0")`. Reduce
   `test_list_quick_properties_empty_set_returns_no_names` to its first parametrization and move
   its `if n`-filter comment to the new test with the same correction Task 1 made. Change
   `test_list_quick_properties_204_returns_no_names` to assert `(None, "V1_0")` and rename it
   `test_list_quick_properties_204_returns_an_unknown_answer`.
3. Run `uv run --no-sync pytest tests/unit/test_client.py -k list_quick_properties -q`.
   Expect failures asserting `([], 'V1_0') == (None, 'V1_0')`.
4. In `list_quick_properties`, replace the body from `if resp.status_code == 204:` to the final
   `return` with:

   ```python
           if resp.status_code == 204:
               return None, schema_version
           if resp.status_code != 200:
               raise HMCError(f"GET {path} failed", resp.status_code, resp.text)
           found = _find_all_text(resp.text, f"GET {path}", "Nickname")
           names = [n for n in found if n]
           if names:
               return names, schema_version
           # The container separates "defines none" from "not this shape at all",
           # and is only consulted when no name was found. Its presence alone is
           # tested: it carries no text of its own, so a text filter would reject
           # the very body this distinguishes.
           if not _find_all_text(
               resp.text, f"GET {path}", _QUICK_PROPERTY_CONTAINER_ELEMENT
           ):
               raise HMCError(
                   f"GET {path} returned no {_QUICK_PROPERTY_CONTAINER_ELEMENT} "
                   "element; expected the quick-property names the type defines",
                   resp.status_code,
                   resp.text,
               )
           # Container and no Nickname element at all: the type defines no quick
           # properties, and that is a fact about the type (ADR 0144). Elements
           # present but every text empty is the parse-artefact shape instead --
           # the container holds properties this parse cannot name -- so it reads
           # as unknown, like a 204.
           return ([] if not found else None), schema_version
   ```

5. Update that method's docstring: the sentence beginning `When no name is found, the body is
   checked for the QuickProperty_Collection container` states the three-way answer, and the
   `Returns` paragraph states that the first element is `None` when the answer is not a fact about
   the type.
6. In `_defined_quick_property_names`, replace the `except HMCError:` body's `names = []` with
   `names = None`, and the `defined = frozenset(names) if names else None` line and its comment
   with:

   ```python
           # None is the level's answer not being a fact about the type: a 204, a
           # failed read, or a container whose Nickname elements are all empty. An
           # empty list is a fact about the type, and caching it as an empty
           # positive set is what makes validate=True refuse locally (ADR 0144).
           defined = None if names is None else frozenset(names)
   ```

   Update the docstring's second sentence the same way Task 1 updated its twin.
7. In `get_quick_property`, replace the `validate` block's `raise ValueError(...)` with:

   ```python
               if defined is not None and property_name not in defined:
                   detail = (
                       f"Defined names: {_summarize_names(defined)}"
                       if defined
                       else "The type defines none at all."
                   )
                   raise ValueError(
                       f"{resource_type} defines no quick property named "
                       f"{property_name!r}. {detail}"
                   )
   ```

   Add to the docstring that a type defining nothing is refused locally.
8. In `_summarize_names`, replace the docstring's second paragraph with one stating that *names*
   is non-empty because each caller renders its own message for an empty positive set before
   calling, and that an empty set would otherwise render a bare `"."`.
9. Add an `"empty-set"` branch to `_mock_validation_routes`' `discovery` argument, answering
   `httpx.Response(200, text=_quick_property_entry(_VALIDATION_TYPE))`, and note it in the
   helper's docstring.
10. Write `test_get_quick_property_validate_refuses_a_type_defining_nothing`: mock with
    `discovery="empty-set"`, call `get_quick_property(_VALIDATION_TYPE, _VALIDATION_UUID,
    "SystemType", validate=True)` inside `pytest.raises(ValueError)`, assert the message ends
    `The type defines none at all.` and contains no `": ."`, and assert `defined.call_count == 0`.
11. Add the `empty-elements` parametrization to
    `test_get_quick_property_validate_degrades_and_caches_the_failure`, answering the discovery
    route with `_quick_property_entry(_VALIDATION_TYPE, ("", ""), ("   ", ""))`, and correct that
    test's docstring sentence about the 204 being the one that would fail closed, the same way
    Task 1 did.
12. Run `uv run --no-sync pytest tests/unit/test_client.py -q`. Expect every case to pass.
13. Rewrite the `CHANGELOG.md` Unreleased entries for `HMCClient.list_quick_properties` and
    `HMCClient.get_quick_property` the same way Task 1 rewrote the search pair. The
    `list_quick_properties` entry additionally still claims a nameless 200 raises `HMCError`,
    which #811 changed and did not record here; correct that claim in the same edit.
14. Run `just verify`, then `uv run --no-sync prek run --all-files`. Expect both green.
15. Commit: `fix(client): refuse locally when a type defines no quick properties`.

**Acceptance.** `list_quick_properties` returns `([], v)`, `(None, v)` and `HMCError` per
ADR 0144's table; `get_quick_property(..., validate=True)` raises `ValueError` ending `The type
defines none at all.` without a transport call; `just verify` and the hook run are green.

## Deferrals carried into implementation

None recorded by the design review at the time of writing; any recorded later is appended here
with its owning record path or tracker issue.
