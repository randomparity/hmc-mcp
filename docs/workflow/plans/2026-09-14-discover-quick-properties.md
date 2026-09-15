# Plan: discover defined quick-property names via `/quick` and `/quick/all`

Issue #788. Spec: `docs/workflow/specs/2026-09-14-discover-quick-properties.md`. Decision:
`docs/adr/0140-quick-property-discovery-returns-a-json-name-list.md`.

**Goal, architecture, stack.** Add one read-only method to `src/hmc_mcp/client/core.py` beside
`get_quick_property`, returning the quick-property names an HMC defines for a resource type at the
root and child anchors for both `/quick` and `/quick/all`. It builds the path from its arguments,
sends it through the existing `_request_with_uuid_path_arguments` with `Accept: */*`, and decodes
the body as a JSON array of strings. No new class, module, or dependency; nothing calls it yet.
Python 3.11, `httpx` transport, `pytest` + `pytest-asyncio` + `respx`, `uv`, `just`.

## Global Constraints
- Python floor 3.11 (`.python-version`); CI also runs 3.12, 3.13 and 3.14 on amd64 and arm64.
- Never run a bare `uv sync`, `uv run` or `prek`. Bootstrap with `just setup`; run tools as
  `uv run --no-sync <tool>`. No new dependency: `httpx.Response.json()` is already used here
  (`src/hmc_mcp/client/client_systems.py:77`).
- `HMCClient` is one of ADR 0118's six `hmc_mcp.api` facade names, so a new method on it is facade
  movement and `CONTRIBUTING.md` requires a `CHANGELOG.md` entry.
- Guardrails: `just test`, `just static`, `just verify`, then `uv run --no-sync prek run --all-files`.
  Diff against the merge base: `git --no-pager diff "$(git merge-base HEAD origin/main)"`.

Expected implementation size: 140–180 changed lines (S) — from this plan's file map: a ~40-line
method plus docstring, ~100 lines of tests, one `CHANGELOG.md` bullet. It exceeds the S band's
100-line denominator because the tests enumerate nine transport contracts against one small method;
the frozen complexity is unchanged and this estimate is informational.

## Task 1 — `HMCClient.list_quick_properties`

Creates nothing. Modifies `src/hmc_mcp/client/core.py`, `tests/unit/test_client.py`, `CHANGELOG.md`.

**Interfaces.** Consumes, confirmed present at `70e1bc28` with these signatures:
`HMCClient._request_with_uuid_path_arguments(method: str, path: str, *, uuid_path_arguments:
Mapping[str, str], **kwargs) -> httpx.Response` (`core.py:453`), which raises
`ValueError(f"{argument} must be a UUID")`; `HMCError(message, status_code=None, body=None)`
(`src/hmc_mcp/errors.py`), already imported in `core.py`; `_reject_dot_segments`, applied inside
`_request` (`core.py:435`). Tests consume `mock_hmc` and `make_config()` from `tests/conftest.py`
and the `_PARENT_UUID` constant already in `tests/unit/test_client.py`. Publishes
`HMCClient.list_quick_properties(resource_type: str, *, all_properties: bool = False, parent_type:
str | None = None, parent_uuid: str | None = None) -> tuple[list[str], str | None]`; no later task
depends on it.

**Verification.** Nine `focused-test` contracts plus one non-applicable. Each row is the test
specification: write it in `tests/unit/test_client.py` with the fixture and assertion named. Before
the method exists every row fails with `AttributeError: 'HMCClient' object has no attribute
'list_quick_properties'`; each row's own red observation applies once it exists. Every row's focused
green command is `uv run --no-sync pytest tests/unit/test_client.py -k list_quick_properties -q`,
expecting every selected case to pass and none deselected by a collection error.

| # | Contract | Test name | Fixture / mock | Assertion, and its red observation |
|---|---|---|---|---|
| 1 | Four anchors, `Accept: */*` | `test_list_quick_properties_reads_the_documented_anchors` | parametrized over the four kwarg sets, each mocked 200 `'["State", "SystemName"]'` | request path equals `/rest/api/uom/ManagedSystem/quick`, `…/quick/all`, `/rest/api/uom/ManagedSystem/{_PARENT_UUID}/LogicalPartition/quick`, `…/quick/all` respectively; `Accept` is `*/*`; names equal `["State", "SystemName"]`. Red if a segment or the `/all` suffix is wrong |
| 2 | Schema-version pairing | `test_list_quick_properties_returns_the_response_schema_version` | parametrized `({"X-HMC-Schema-Version": "V1_0"}, "V1_0")` and `({}, None)` | second tuple element equals the expected value. Red if the header is dropped or defaulted |
| 3 | 204 | `test_list_quick_properties_204_returns_no_names` | 204 with `X-HMC-Schema-Version: V1_0` | returns `([], "V1_0")`. Red if 204 falls through to the JSON decode |
| 4 | Empty array | `test_list_quick_properties_empty_array_returns_no_names` | 200 `'[]'` | returns `([], None)`. Red if an empty array is rejected as a bad shape |
| 5 | Non-array JSON | `test_list_quick_properties_rejects_non_array_json` | parametrized 200 bodies `'{"State": "operating"}'` and `'"State"'` | raises `HMCError` matching `returned a JSON (dict|str); expected an array`. Red if the body is returned unchecked |
| 6 | Non-string elements | `test_list_quick_properties_rejects_non_string_elements` | 200 `'[{"UUID": "u", "SystemName": "s"}]'` — the per-instance shape `/quick/All` is live-evidenced to return (ADR 0138) | raises `HMCError` matching `array holding dict`. Red if objects are returned as names or coerced with `str()` |
| 7 | Invalid JSON | `test_list_quick_properties_invalid_json_raises_hmc_error` | 200 body `'not json'` | raises `HMCError` matching `returned invalid JSON`. Red if `ValueError` escapes to the caller |
| 8 | Non-200 surfaces | `test_list_quick_properties_unknown_type_raises_hmc_error_with_status` | 400 with the `INVALID_URL` / `REST000E` body shape captured live for `/operations` on PR #797, path changed to `/rest/api/uom/NoSuchType/quick`; its docstring must say it is modelled on that capture and is not itself a `/quick` capture | `raised.value.status_code == 400` and the HMC's message text is preserved. Red if the status is swallowed or remapped |
| 9 | Pre-transport refusals | `test_list_quick_properties_refuses_bad_arguments` | no mock; parametrized over `parent_uuid="not-a-uuid"`, each parent argument alone, and `..` segments in `resource_type` and in `parent_type` | raises `ValueError` matching `must be a UUID` / `must be given together`, or `HMCError` matching `'\.\.' segment`; `respx` records no request. Red if any reaches transport |

`CHANGELOG.md` bullet — `Mode: task-test-not-applicable`. The changed surface is one prose bullet
under `## [Unreleased]` / `### Added`. `tests/unit/test_changelog.py` checks only that the version in
`pyproject.toml` has a matching heading, so no executable consumer reads this bullet's content and no
task-specific observation over it could fail meaningfully.

### Steps

1. Add the inventory's tests to `tests/unit/test_client.py`, after the `list_operations` block that
   ends the file. Run the focused command above and expect every case to fail with the
   `AttributeError`; a different error means the test file is wrong, not the source.
2. Add the method to `src/hmc_mcp/client/core.py` immediately after `get_quick_property` (ending at
   line 715 at `70e1bc28`) and before `search_uom`:

```python
    async def list_quick_properties(
        self,
        resource_type: str,
        *,
        all_properties: bool = False,
        parent_type: str | None = None,
        parent_uuid: str | None = None,
    ) -> tuple[list[str], str | None]:
        """GET the quick-property names a type defines, with the schema version.

        Reads ``/rest/api/uom/{R}/quick``, or ``/rest/api/uom/{P}/{UUID}/{C}/quick``
        when both *parent_type* and *parent_uuid* are given; supplying exactly one
        of them is a caller error. *all_properties* appends ``/all`` to either.

        Returns the names paired with the response's ``X-HMC-Schema-Version``,
        ``None`` when the HMC sends none (ADR 0139). As for ``list_operations``
        that value is verbatim, is not guaranteed to be a version string, and a
        configured ``HMC_SCHEMA_VERSION`` is not sent: this method passes its own
        headers to the transport and never reaches ``_uom_headers``.

        The body is a plain JSON array of names, not an Atom feed, so it is decoded
        rather than parsed by ``_parse_feed``; one that is not an array of strings
        raises ``HMCError`` naming the shape observed rather than being coerced
        (ADR 0140) -- the differently capitalized ``/quick/All`` returns
        per-instance *value* objects (ADR 0138), and returning those as names would
        be silent. Sends ``Accept: */*``: ``quick/`` endpoints answer 406 to a
        typed uom Accept, as ``get_quick_property`` records.
        """
        uuid_path_arguments: dict[str, str] = {}
        if parent_type is not None and parent_uuid is not None:
            path = f"/rest/api/uom/{parent_type}/{parent_uuid}/{resource_type}/quick"
            uuid_path_arguments["parent_uuid"] = parent_uuid
        elif parent_type is None and parent_uuid is None:
            path = f"/rest/api/uom/{resource_type}/quick"
        else:
            raise ValueError(
                "parent_type and parent_uuid must be given together: a "
                "child-anchored read needs both the parent type and the "
                "parent instance UUID"
            )
        if all_properties:
            path += "/all"
        resp = await self._request_with_uuid_path_arguments(
            "GET",
            path,
            uuid_path_arguments=uuid_path_arguments,
            headers={"Accept": "*/*"},
        )
        schema_version: str | None = resp.headers.get("X-HMC-Schema-Version")
        if resp.status_code == 204:
            return [], schema_version
        if resp.status_code != 200:
            raise HMCError(f"GET {path} failed", resp.status_code, resp.text)
        try:
            names = resp.json()
        except ValueError as exc:
            raise HMCError(
                f"GET {path} returned invalid JSON: {str(exc)[:500]}"
            ) from exc
        if not isinstance(names, list):
            raise HMCError(
                f"GET {path} returned a JSON {type(names).__name__}; expected an "
                "array of quick-property names"
            )
        unexpected = sorted({type(n).__name__ for n in names if not isinstance(n, str)})
        if unexpected:
            raise HMCError(
                f"GET {path} returned an array holding {', '.join(unexpected)}; "
                "expected an array of quick-property names"
            )
        return names, schema_version
```

3. Re-run the focused command. Expect every case to pass.
4. Add the `CHANGELOG.md` bullet under `## [Unreleased]` / `### Added`, above the `list_operations`
   bullet: the method and its four anchors, the returned pair, the strict array-of-strings parse,
   and that live confirmation of the response shape is owed (ADR 0140).
5. Run `just test` (compact summary, no failures, coverage gate met), then `just static` (every
   sub-recipe passes; `ruff` and `ty` are the two this diff can redden — no generated document
   covers `core.py`). Commit source, tests and changelog.
6. Run `just verify`, then `uv run --no-sync prek run --all-files`. Expect both green. Per
   `AGENTS.md`, a failure during pytest collection is diagnosed with `just smoke` first.

**Acceptance.** Every `Success` criterion in the spec holds; both guardrail commands are green; no
file outside the three named above changed. **Rollback.** The method has no callers and no persisted
state; reverting the commit suffices.
