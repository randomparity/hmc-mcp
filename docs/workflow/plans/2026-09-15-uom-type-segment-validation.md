# Validate UOM type path segments — implementation plan

**Goal.** Refuse a caller-supplied HMC resource-type path segment that falls
outside the HMC's own type-name grammar, before the client builds a request
path or an `Accept` header from it.

**Architecture.** One module-level predicate in `src/hmc_mcp/client/core.py`,
`_reject_unknown_uom_type`, beside the existing `_reject_dot_segments`. Ten
public client methods call it on each caller-supplied type argument before
interpolating one into a path; `_uom_headers` calls it on the `resource_type` it
interpolates into the `Accept` media-type parameter. Nothing else changes: the
transport waist, the UUID check, and the search-term encoding all keep their
current contracts.

**Tech stack.** Python 3.11+, `httpx`, `pytest`, `respx`. No new dependency.

Spec: `docs/workflow/specs/2026-09-15-uom-type-segment-validation.md`.
Decision: `docs/adr/0143-uom-type-segments-validated-not-encoded.md`.

Expected implementation size: 150–210 changed lines (M) — derived from the file
map below: one ~20-line predicate, twelve one-line calls across ten methods plus
`_uom_headers`, one docstring edit, one test-file edit, and ~130 lines of new
tests.

## Global Constraints

- Python floor 3.11 (`.python-version` pins 3.11; CI runs 3.11–3.14 on amd64 and
  arm64). Use no syntax newer than 3.11.
- Never run a bare `uv sync`, `uv run`, or `uv add`. `just setup` is the only
  sync recipe. Every `uv run` carries `--no-sync`.
- Guardrails: `just lint`, `just typecheck`, `just test`, `just verify`, and
  `uv run --no-sync prek run --all-files` before pushing.
- Line length 100 (`ruff`). Functions ≤100 lines, cyclomatic complexity ≤8.
- A refusal message names the argument and the offending character only — never
  the full path, the host, or the caller's whole string. This is
  `_reject_dot_segments`' existing rule (`core.py`, its docstring) and the new
  predicate inherits it.
- The type grammar is exactly `^[A-Za-z][A-Za-z0-9]*$`.
- `ValueError` is the refusal type, matching `_request_with_uuid_path_arguments`,
  `validate_adapter_type`, and ADR 0065.
- Do not add an ADR index; this repository has none (`AGENTS.md`).

## File map

| File | Owns now | Owns after |
|---|---|---|
| `src/hmc_mcp/client/core.py` | path construction, the dot-segment and UUID guards | the same, plus the type-grammar predicate and its call sites |
| `tests/unit/test_request_path_safety.py` | the path-safety contracts | the same, plus the type-grammar contracts and the AST drift test |
| `tests/unit/test_client.py` | per-method client behaviour | the same, with one refusal identity updated |

No file is created, moved, or removed. No caller outside `core.py` changes: every
`resource_type` reaching the uom helpers from `client_lpars.py`,
`client_network.py`, `client_storage.py`, `client_systems.py`, and
`client_users.py` is already a PascalCase literal inside the grammar (verified at
`b269bbb6`). No compatibility path is retained, because no contract is removed.

## Task 1 — the predicate, its call sites, and its tests

**Where this fits.** It is the whole change. There is no second deliverable: a
predicate with no call sites proves nothing, and a call site without the
predicate does not exist.

**Interfaces.**

Consumes, from the existing module:

- `_reject_dot_segments(method: str, path: str) -> None` — unchanged; only its
  docstring gains a contract sentence.
- `_uom_headers(self, resource_type: str | None, include_schema_version: bool = True) -> dict[str, str]`

Provides:

- `_UOM_TYPE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")`
- `_reject_unknown_uom_type(argument: str, value: str) -> None`

**Verification inventory.**

- *Contract: the grammar.* Mode: `focused-test`. Test
  `test_a_well_formed_type_is_accepted` / `test_a_malformed_type_is_refused` in
  `tests/unit/test_request_path_safety.py`. Red before the predicate exists:
  `ImportError: cannot import name '_reject_unknown_uom_type'`. Green:
  `uv run --no-sync pytest tests/unit/test_request_path_safety.py -q`.
- *Contract: refusal precedes transport, per method.* Mode: `focused-test`.
  `test_no_unsafe_type_segment_reaches_transport`, parametrized over the ten
  methods. Red before the call sites exist: the parametrized cases fail with
  `DID NOT RAISE ValueError`. Green: the same command.
- *Contract: the `Accept` destination.* Mode: `focused-test`.
  `test_uom_headers_refuses_a_malformed_type` and
  `test_uom_headers_passes_a_valid_type_through`. Red before the
  `_uom_headers` call: `DID NOT RAISE ValueError`. Green: the same command.
- *Contract: no site drifts.* Mode: `focused-test`.
  `test_every_uom_path_interpolation_is_a_known_argument`. Red when seeded with
  an unknown name: the assertion lists it. Green: the same command.
- *Contract: `_reject_dot_segments` unchanged.* Mode:
  `task-test-not-applicable`. Its existing parametrized accept/refuse cases in
  the same file are the observation and this task does not modify them; a second
  test asserting the identical behaviour would observe nothing new.
- *Contract: the moved refusal identity.* Mode: `focused-test`.
  `test_list_search_parameters_refuses_a_dot_segment_resource_type` in
  `tests/unit/test_client.py`. Red before the call sites exist, in the
  already-updated form: it expects `ValueError` and gets `HMCError`. Green:
  `uv run --no-sync pytest tests/unit/test_client.py -q -k list_search_parameters`.

**Steps.**

1. In `tests/unit/test_request_path_safety.py`, import
   `_reject_unknown_uom_type` beside `_reject_dot_segments`, and add the grammar
   tests. Accept: `LogicalPartition`, `ManagedSystem`, `VirtualIOServer`,
   `Cluster`, `SharedStoragePool`, `jobs`, `Job`, `UserProfile`, `TaskRole`,
   `ResourceRole`, `VirtualSwitch`, `VirtualNetwork`, `NetworkBridge`,
   `VolumeGroup`, and every member of `client_contracts.ADAPTER_TYPES`. Refuse:
   `LogicalPartition?group=None`, `LogicalPartition#x`, `..`, `%2e%2e`,
   `Logical Partition`, `Logical/Partition`, `Logical-Partition`,
   `Logical_Partition`, `1LogicalPartition`, `""`, `'LogicalPartition\r\nEvil: 1'`.
   Assert the message names the argument and carries neither the host nor the
   whole offending value.
2. Run `uv run --no-sync pytest tests/unit/test_request_path_safety.py -q`.
   Expect a collection error naming `_reject_unknown_uom_type`.
3. In `src/hmc_mcp/client/core.py`, beside `_DOT_SEGMENTS`, add:

   ```python
   # Every `/rest/api/uom/` type segment in the vendored V10 and V11 corpora, and
   # every type name this client passes, matches this. It is deliberately an
   # allowlist: a denylist over a URL path segment has to discover `?`, `#`, `%`,
   # `;`, `@`, `:`, and CRLF one incident at a time, while the type namespace is
   # closed and documented (ADR 0143).
   _UOM_TYPE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
   ```

4. Below `_reject_dot_segments`, add:

   ```python
   def _reject_unknown_uom_type(argument: str, value: str) -> None:
       """Refuse a type segment outside the HMC's own type-name grammar.

       Raised as ``ValueError`` because it reports a malformed caller argument,
       not a path this client declines to send — the same family as
       ``_request_with_uuid_path_arguments``' UUID check and
       ``validate_adapter_type`` (ADR 0143). The message names the argument and
       the first offending character only, never the whole value, which on the
       CLI and API paths can carry an operator's own strings.
       """
       if _UOM_TYPE.match(value):
           return
       if not value:
           detail = "an empty value"
       elif not (value[0].isascii() and value[0].isalpha()):
           detail = f"a value starting with {value[0]!r}"
       else:
           offending = next(
               (c for c in value if not (c.isascii() and c.isalnum())), value[0]
           )
           detail = f"a value containing {offending!r}"
       raise ValueError(
           f"{argument} must be an HMC resource type name: ASCII letters and "
           f"digits only, starting with a letter. Got {detail}."
       )
   ```

   `!r` is what keeps a CR or LF out of the message text rather than into it.

5. Add the contract sentence to `_reject_dot_segments`' docstring, after its
   existing first paragraph: *"Its scope is path form, and only dot segments:
   `?`, `#`, and every other character are legitimate here, because* `list_uom`
   *appends* `?group=` *and* `search_uom` *passes an instance grammar. A type
   segment's own character grammar is checked at the boundary instead
   (ADR 0143)."*
6. Add the call as the first statement of each method, one per caller-supplied
   type argument:

   | Method | Calls |
   |---|---|
   | `list_uom` | `("resource_type", resource_type)` |
   | `get_uom` | `("resource_type", resource_type)` |
   | `get_quick_property` | `("resource_type", resource_type)` |
   | `list_quick_properties` | `("resource_type", resource_type)`; and `("parent_type", parent_type)` inside the branch where `parent_type is not None` |
   | `search_uom` | `("resource_type", resource_type)` |
   | `list_search_parameters` | `("resource_type", resource_type)` |
   | `list_operations` | `("resource_type", resource_type)`; and `("parent_type", parent_type)` inside the branch where `parent_type is not None` |
   | `list_child` | `("parent_type", parent_type)`, `("child_type", child_type)` |
   | `create_child` | `("parent_type", parent_type)`, `("child_type", child_type)` |
   | `delete_child` | `("parent_type", parent_type)`, `("child_type", child_type)` |

   In `get_quick_property`, `list_quick_properties`, `list_operations`, and
   `search_uom`, the call goes **before** the optional `validate` discovery read
   — a malformed type must not cost a request.
7. In `_uom_headers`, inside the existing `if resource_type:` branch, call
   `_reject_unknown_uom_type("resource_type", resource_type)` before building
   the `Accept` value.
8. Add the per-method, header, and drift tests to
   `tests/unit/test_request_path_safety.py`. The drift test walks `core.py`'s
   AST, collects every `ast.FormattedValue` name inside a `JoinedStr` whose
   first constant part starts with `/rest/api/uom/`, and asserts the set is a
   subset of `{"resource_type", "parent_type", "child_type", "uuid",
   "parent_uuid", "child_uuid", "property_name", "job_id", "encoded_property",
   "encoded_value"}` — so a new kind of segment fails until someone decides
   which rule governs it.
9. In `tests/unit/test_client.py`, update the three tests that pass a `..` type
   and assert `HMCError` with `'..' segment` — a `..` type fails the grammar, so
   the boundary check now fires before the waist guard. Change the expected
   exception to `ValueError` and the match to `must be an HMC resource type
   name`, keep every "no request recorded" assertion, and note ADR 0143 in each
   docstring:
   - `test_list_operations_rejects_a_dot_segment_type` (both parametrized cases)
   - `test_list_quick_properties_refuses_bad_arguments` (the two cases whose
     `error` is `HMCError`; the three `ValueError` cases are unchanged, and the
     "must be given together" case still wins because `parent_type` is validated
     inside the paired branch)
   - `test_list_search_parameters_refuses_a_dot_segment_resource_type`
10. Run `uv run --no-sync pytest tests/unit/test_request_path_safety.py tests/unit/test_client.py -q`.
    Expect all tests to pass.
11. Run `just lint` and `just typecheck`. Expect no output and exit 0.
12. Run `just test`. Expect the compact success summary and the coverage gate to
    pass.
13. Commit: `fix(client): validate HMC resource-type path segments at the boundary`.

**Acceptance criteria.** Every test named in the Verification inventory passes;
each of the ten methods called with a `?`-bearing type raises `ValueError` and
records no request; `_uom_headers` refuses a malformed `resource_type` and
returns an unchanged `Accept` for a valid one; the drift test fails when seeded
with an unknown name; `_reject_dot_segments`' behaviour is unchanged and only
its docstring differs; `just verify` is green.

**Rollback.** Single commit, no data or schema change; `git revert` restores the
previous behaviour exactly.

## Deferrals carried into this plan

None.
