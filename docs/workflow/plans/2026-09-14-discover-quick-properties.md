# Plan: discover defined quick-property names via `/quick`

Issue #788. Spec: `docs/workflow/specs/2026-09-14-discover-quick-properties.md`. Decision:
`docs/adr/0140-quick-property-discovery-reads-atom-nicknames.md`.

> **Rewritten after the live run on PR #800.** The original plan built the method around a JSON
> array of strings and a lowercase `/quick/all` anchor. The live run found `/quick` answering an
> Atom feed and `/quick/all` answering 400, so both premises were false and the plan they produced
> could not be salvaged by annotation. What that plan got right — the path grammar, `Accept: */*`,
> the schema-version pairing, the pre-transport refusals — survives below unchanged. The design-time
> record of the superseded version is the branch history at `f22166d3`.

**Goal, architecture, stack.** Add one read-only method to `src/hmc_mcp/client/core.py` beside
`get_quick_property`, returning the quick-property names an HMC defines for a resource type at the
root and child `/quick` anchors. It builds the path from its arguments, sends it through the
existing `_request_with_uuid_path_arguments` with `Accept: */*`, and reads the `Nickname` texts out
of the Atom body. One new XML primitive, `xmlutil.find_all_text`, and its `client_parse` wrapper.
No new class, module, or dependency; nothing calls the method yet. Python 3.11, `httpx` transport,
`pytest` + `pytest-asyncio` + `respx`, `uv`, `just`.

## Global Constraints
- Python floor 3.11 (`.python-version`); CI also runs 3.12, 3.13 and 3.14 on amd64 and arm64.
- Never run a bare `uv sync`, `uv run` or `prek`. Bootstrap with `just setup`; run tools as
  `uv run --no-sync <tool>`. No new dependency: `defusedxml` already backs every XML read here.
- `HMCClient` is one of ADR 0118's six `hmc_mcp.api` facade names, so a new method on it is facade
  movement and `CONTRIBUTING.md` requires a `CHANGELOG.md` entry.
- Guardrails: `just test`, `just static`, `just verify`, then `uv run --no-sync prek run --all-files`.
  Diff against the merge base: `git --no-pager diff "$(git merge-base HEAD origin/main)"`.

Expected implementation size: 330–370 changed lines (S). The frozen `S` complexity is unchanged;
the count grew over the superseded plan's 300–330 because the rewrite adds the `find_all_text`
primitive with its own three contracts, two contracts covering shapes the live run revealed
(single-property arity, nesting independence), and the signature guard, while dropping the four
JSON-shape contracts that no longer describe anything.

## Task 1 — `HMCClient.list_quick_properties`

Creates nothing. Modifies `src/hmc_mcp/xmlutil.py`, `src/hmc_mcp/client/client_parse.py`,
`src/hmc_mcp/client/core.py`, `tests/unit/test_xmlutil.py`, `tests/unit/test_client_parse.py`,
`tests/unit/test_client.py`, `CHANGELOG.md`.

**Interfaces.** Consumes, confirmed present at `70e1bc28`:
`HMCClient._request_with_uuid_path_arguments(method: str, path: str, *, uuid_path_arguments:
Mapping[str, str], **kwargs) -> httpx.Response` (`core.py:453`), raising
`ValueError(f"{argument} must be a UUID")`; `HMCError(message, status_code=None, body=None)`
(`src/hmc_mcp/errors.py`), already imported in `core.py`; `_reject_dot_segments`, applied inside
`_request` (`core.py:435`); `client_parse._tag_parse_errors`, which inserts a `context` string as
the second argument and converts `DET.ParseError` / `DefusedXmlException` into `HMCError`;
`xmlutil.localname` and `xmlutil.DET`; `mock_hmc` and `make_config()` from `tests/conftest.py` and
the `_PARENT_UUID` constant in `tests/unit/test_client.py`. Publishes
`xmlutil.find_all_text(xml_text: str, *names: str) -> list[str]`,
`client_parse._find_all_text(xml_text, context, *names)`, and
`HMCClient.list_quick_properties(resource_type: str, *, parent_type: str | None = None,
parent_uuid: str | None = None) -> tuple[list[str], str | None]`; no later task depends on them.

**Verification.** Fifteen `focused-test` contracts plus one non-applicable. Each row is the test
specification: write it with the fixture and assertion named. Before the method exists every
`test_client.py` row fails with `AttributeError: 'HMCClient' object has no attribute
'list_quick_properties'`. Focused green commands:
`uv run --no-sync pytest tests/unit/test_client.py -k list_quick_properties -q --no-cov` for rows
1–11, and `uv run --no-sync pytest tests/unit/test_xmlutil.py tests/unit/test_client_parse.py -q
--no-cov` for rows 12–14. `--no-cov` is load-bearing: without it the repository's `fail-under` gate
fires on the partial run and the command exits non-zero while every selected case passes.

| # | Contract | Test name | Fixture / mock | Assertion, and its red observation |
|---|---|---|---|---|
| 1 | Two live anchors, `Accept: */*` | `test_list_quick_properties_reads_the_live_anchors` | parametrized over root and child, each mocked 200 with `_quick_property_feed(*names)` carrying the live run's verbatim names | request path equals `/rest/api/uom/ManagedSystem/quick` and `/rest/api/uom/ManagedSystem/{_PARENT_UUID}/LogicalPartition/quick`; `Accept` is `*/*`; names equal the fixture's. Red if a segment is wrong or the parse misses |
| 2 | No `/quick/all` surface | `test_list_quick_properties_has_no_all_properties_argument` | none; reads `inspect.signature` | `all_properties` absent and the parameter list is exactly `self, resource_type, parent_type, parent_uuid`. Red if the argument is re-added |
| 3 | Single property keeps arity | `test_list_quick_properties_returns_a_single_name_as_a_one_element_list` | the captured `VirtualNetwork` child anchor, the real single-property type | returns `["SystemName"]`. Red if the parse collapses one element to a bare string, the `OperationSet` hazard of ADR 0139 |
| 4 | Nesting independence | `test_list_quick_properties_reads_names_at_any_depth` | parametrized: collection at document root, and wrapped one level deeper | returns `["State"]` for both. Red if the parse is tightened to a fixed element path |
| 4b | Nickname, not its siblings | `test_list_quick_properties_returns_nicknames_not_descriptions` | the capture's verbatim first two `LogicalPartition` properties, one of which is *named* `Description` | returns the nicknames. Red if the parse reads `Description` or `RESTElement`, which also hold plausible strings |
| 5 | Schema-version pairing | `test_list_quick_properties_returns_the_response_schema_version` | parametrized `V1_0`, the observed non-version `hmc-mcp`, and absent | second tuple element equals the expected value verbatim. Red if the header is dropped, defaulted, or validated as a level |
| 6 | 204 | ~~`test_list_quick_properties_204_returns_no_names`~~ | 204 with `X-HMC-Schema-Version: V1_0` | ~~returns `([], "V1_0")`~~. Red if 204 falls through to the parse. Renamed `test_list_quick_properties_204_returns_an_unknown_answer` (`tests/unit/test_client.py:2679`); ADR 0144 changed the 204 return to `(None, "V1_0")`. |
| 7 | Empty name dropped | `test_list_quick_properties_drops_an_empty_nickname` | 200 feed with a populated, an empty, and a populated `Nickname` | returns the two populated names. Red if `""` is returned as a property name |
| 8–10 | Nameless 200 raises | ~~`test_list_quick_properties_200_without_a_name_raises`~~ | parametrized: `HttpErrorResponse` feed, empty collection, all-empty names | ~~raises `HMCError` matching `no QuickProperty/Nickname element`, with `status_code == 200` and the body preserved. Red if any returns `[]`~~. Split under ADR 0144 into `test_list_quick_properties_200_without_the_container_raises` (`tests/unit/test_client.py:2719`, still raises on the `HttpErrorResponse` shape), `test_list_quick_properties_empty_set_returns_no_names` (`:2747`, returns `([], "V1_0")`), `test_list_quick_properties_all_empty_names_return_an_unknown_answer` (`:2768`, returns `(None, "V1_0")`), and `test_list_quick_properties_unnamed_items_return_an_unknown_answer` (`:2794`, returns `(None, "V1_0")`). |
| 11 | Malformed XML tagged | `test_list_quick_properties_malformed_xml_raises_hmc_error` | 200 body `"<feed><entry>"` | raises `HMCError` naming `GET /rest/api/uom/ManagedSystem/quick`. Red if a bare `ParseError` escapes, i.e. the raw parser was called instead of the wrapper |
| 12 | Non-200 surfaces | `test_list_quick_properties_unknown_type_raises_hmc_error_with_status` | 400 with the `INVALID_URL` / `REST000E` body shape captured live for `/operations` on PR #797, path changed to `/rest/api/uom/NoSuchType/quick`; its docstring must say it is modelled on that capture and is not itself a `/quick` capture | `raised.value.status_code == 400` and the HMC's message text is preserved. Red if the status is swallowed or remapped |
| 13 | Pre-transport refusals | `test_list_quick_properties_refuses_bad_arguments` | no mock; parametrized over `parent_uuid="not-a-uuid"`, each parent argument alone, and `..` segments in `resource_type` and in `parent_type` | raises `ValueError` matching `must be a UUID` / `must be given together`, or `HMCError` matching `'\.\.' segment`; `respx` records no request for a `quick` path. Red if any reaches transport |
| 14 | `find_all_text` semantics | `test_find_all_text`, `test_find_all_text_keeps_arity_at_one`, `test_find_all_text_keeps_empty_elements_as_empty_strings` in `tests/unit/test_xmlutil.py`; `test_find_all_text_parse_error_tags_context` in `tests/unit/test_client_parse.py` | plain XML strings, no HTTP | all matches in document order; a single match is a one-element list; an empty match contributes `""`; a truncated body raises `HMCError` naming the context with a `DET.ParseError` cause. Red if the helper returns only the first match, collapses arity, skips empties, or leaks `ParseError` |

`CHANGELOG.md` bullets — `Mode: task-test-not-applicable`. The changed surface is prose under
`## [Unreleased]` / `### Added`. `tests/unit/test_changelog.py` checks only that the version in
`pyproject.toml` has a matching heading, so no executable consumer reads the content and no
task-specific observation over it could fail meaningfully.

### Steps

1. Add `find_all_text` to `src/hmc_mcp/xmlutil.py` beside `find_text`, and wrap it as
   `_find_all_text` in `src/hmc_mcp/client/client_parse.py`. Add the row 14 tests and run their
   focused command.
2. Add the row 1–13 tests to `tests/unit/test_client.py`, replacing the `list_quick_properties`
   block. Run the focused command and expect the `AttributeError`; a different error means the test
   file is wrong, not the source.
3. Add the method to `src/hmc_mcp/client/core.py` immediately after `get_quick_property` and before
   `search_uom`, importing `_find_all_text` alongside `_find_text` and `_parse_feed`. The source is
   the implementation, not this plan — the superseded version of this file embedded a verbatim copy
   that went stale the moment the live run landed, so the method is described here by its inventory
   above and read from `core.py`.
4. Re-run both focused commands. Expect every case to pass.
5. Verify the tests bite: mutate the parse back to `resp.json()`, read `Description` instead of
   `Nickname`, read `RESTElement` instead, drop the empty-result guard, stop filtering empty
   `Nickname`s, re-add `all_properties`, and return `[]` on a nameless 200. Each must redden at
   least one row. Run the mutants against an out-of-tree copy of `src/` so the working tree is
   never left mutated. The two wrong-sibling mutants are only detectable against capture-derived
   fixtures: a body carrying `Nickname` alone cannot tell them from the real parse.
6. Update the `CHANGELOG.md` bullets under `## [Unreleased]` / `### Added`.
7. Run `just test`, then `just static`. Commit source, tests, records and changelog.
8. Run `just verify`, then `uv run --no-sync prek run --all-files`. Expect both green. Per
   `AGENTS.md`, a failure during pytest collection is diagnosed with `just smoke` first.

**Acceptance.** Every `Success` criterion in the spec holds; both guardrail commands are green; no
file outside those named above, and this branch's design records, changed. **Rollback.** The method
has no callers and no persisted state; `find_all_text` has no caller outside it. Reverting the
commits suffices.
