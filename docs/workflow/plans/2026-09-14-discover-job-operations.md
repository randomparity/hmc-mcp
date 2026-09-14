# Plan: discover defined job operations via `/operations`

**Goal.** Give `HMCClient` a read-only method that returns the job operations a target HMC
actually defines for a resource type, at the root and child anchors, paired with the schema
version the HMC answered under.

**Architecture.** One new `async` method, `list_operations`, on `HMCClient` in
`src/hmc_mcp/client/core.py`, placed at the end of the `# uom resources` block just after
`search_uom`. It builds the anchor path, issues one GET through the existing
`_request_with_uuid_path_arguments` helper, and parses the body with the existing
`_parse_feed`. It adds no module, no class, no dependency and no caller.

**Tech stack.** Python 3.11+, `httpx` (async), `defusedxml` via `hmc_mcp.xmlutil`, `pytest` +
`pytest-asyncio` + `respx` for tests, `uv` for the environment, `just` for guardrails.

Spec: `docs/workflow/specs/2026-09-14-discover-job-operations.md`.
Decision: `docs/adr/0139-discovery-reads-return-feed-and-schema-version.md`.

Expected implementation size: 160–230 changed lines (M) — derived from the file map below:
one ~50-line method in `core.py`, ~140 lines of tests in `tests/unit/test_client.py`, and a
~6-line `CHANGELOG.md` entry.

## Global Constraints

- **Environment.** Never run a bare `uv sync`, `uv run`, or `uv add`. Bootstrap with
  `just setup`. Every command below uses `uv run --no-sync` or a `just` recipe.
- **Worktree.** All work happens in
  `/home/dave/src/hmc-mcp-worktrees/feat-discover-job-operations-787` on branch
  `feat/discover-job-operations-787`. `BASE_BRANCH` is `main`.
- **Guardrails.** Iteration: `just lint`, `just typecheck`, `just test`. Final:
  `just verify`, then `uv run --no-sync prek run --all-files`. A prek `pre-commit` hook runs
  every `static` member on each commit; expect a commit to take as long as `just static`.
- **Coverage.** `pyproject.toml` sets `fail_under = 90.5` package-wide and `addopts` always
  measures coverage. A focused pytest run must pass `--no-cov`, or its package-wide figure
  will fail the gate for reasons unrelated to the change.
- **Typing.** `ty check` covers `src/hmc_mcp` only. Annotate the new method fully; do not use
  `assert` for narrowing in `src/` — structure the branches so the checker narrows on its own.
- **Changelog.** `hmc_mcp.api` exports `HMCClient` (ADR 0118), so a new method on it is facade
  movement and belongs under `## [Unreleased]` / `### Added` in `CHANGELOG.md`
  (`CONTRIBUTING.md`, *Changelog*).
- **ADR file.** `docs/adr/0139-...` already exists from the design phase. This repo has no ADR
  index; do not create one. `just adr-numbering` checks that the filename number and the H1
  number agree.
- **No new dependency.** Everything needed is already imported by `core.py`.

## File map

| Path | Currently owns | After |
|---|---|---|
| `src/hmc_mcp/client/core.py` | session lifecycle, HTTP transport, generic uom reads, jobs, raw escape hatch | the same, plus `list_operations` in the uom-reads block |
| `tests/unit/test_client.py` | `HMCClient` transport and uom-read contracts | the same, plus eight `list_operations` tests (ten cases) and one feed fixture |
| `CHANGELOG.md` | release notes | the same, plus one `Unreleased / Added` bullet |

No file is created, moved, or removed. No caller migrates: `list_operations` has no callers
in this change, by exclusion 3 of the frozen scope.

## Task 1 — `HMCClient.list_operations`

**Where it fits.** The whole change. Epic #785 queues #788, #789 and #791 behind it; they
reuse the shape this task establishes but are not built here.

**Interfaces.**

Consumed from the existing codebase (each confirmed present at `975b0121`):

- `HMCClient._request_with_uuid_path_arguments(self, method: str, path: str, *, uuid_path_arguments: Mapping[str, str], **kwargs: Any) -> httpx.Response` — `src/hmc_mcp/client/core.py:453`. Raises `ValueError(f"{argument} must be a UUID")` for a non-UUID value, then delegates to `_request`, which applies `_reject_dot_segments`.
- `_parse_feed(xml_text: str, context: str) -> list[dict[str, Any]]` — `src/hmc_mcp/client/client_parse.py:40`, already imported into `core.py` at line 35. Raises `HMCError(f"Failed to parse {context} response: ...")` on malformed XML.
- `HMCError(message: str, status_code: int | None = None, body: str | None = None)` — `src/hmc_mcp/errors.py:17`, already imported into `core.py` at line 27.

Exposed to later work (#788, #789, #791, #792):

- `HMCClient.list_operations(self, resource_type: str, *, parent_type: str | None = None, parent_uuid: str | None = None) -> tuple[list[dict[str, Any]], str | None]`

**Verification.**

One entry per material changed contract. Entries 1–8 are all `Mode: focused-test`, all in
`tests/unit/test_client.py`, and all share one expected red failure and one green command,
because none of their tests can run until the method exists:

- **Expected red** (before step 4): `AttributeError: 'HMCClient' object has no attribute 'list_operations'`.
- **Exact green command:** `uv run --no-sync pytest tests/unit/test_client.py -k list_operations --no-cov -q`.

| # | Changed contract | Test |
|---|---|---|
| 1 | Root anchor path and `Accept: */*` | `test_list_operations_reads_the_root_anchor` |
| 2 | Child anchor path | `test_list_operations_reads_the_child_anchor` |
| 3 | `X-HMC-Schema-Version` captured, and `None` when absent | `test_list_operations_returns_the_response_schema_version` (parametrized over present and absent) |
| 4 | Unknown resource type surfaces the HMC status | `test_list_operations_unknown_type_raises_hmc_error_with_status` |
| 5 | 204 returns no entries | `test_list_operations_204_returns_no_entries` |
| 6 | Non-UUID `parent_uuid` refused before transport | `test_list_operations_rejects_a_non_uuid_parent` |
| 7 | Exactly one parent argument refused before transport | `test_list_operations_requires_both_parent_arguments` (parametrized over each half) |
| 8 | Dot-segment resource type refused before transport | `test_list_operations_rejects_a_dot_segment_type` |

9. `Mode: task-test-not-applicable` — the `CHANGELOG.md` entry. The changed surface is one prose bullet describing a facade addition. `tests/unit/test_changelog.py` asserts only that the version declared in `pyproject.toml` has a `## [<version>]` heading; no executable consumer reads the bullet's wording, and a test that searched for its prose would assert the sentence rather than the behaviour.

**Steps.**

1. Append this fixture constant to the **end** of `tests/unit/test_client.py`. That module
   defines its XML constants next to the tests that use them rather than in one header block
   (`JOB_ENTRY` lives in `tests/conftest.py`, not here), so the end of the file is where it
   belongs:

   ```python
   OPERATIONS_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
   <feed xmlns="http://www.w3.org/2005/Atom">
     <entry>
       <id>urn:uuid:11111111-1111-1111-1111-111111111111</id>
       <title>PowerOn</title>
       <content type="application/vnd.ibm.powervm.web+xml">
         <JobRequest xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/">
           <RequestedOperation>
             <OperationName>PowerOn</OperationName>
             <GroupName>ManagedSystem</GroupName>
           </RequestedOperation>
         </JobRequest>
       </content>
     </entry>
   </feed>
   """
   ```

   The assertions below read only `title`, which `xmlutil._parse_entry` fills from the Atom
   `<entry><title>` for any feed, so they do not depend on the element vocabulary inside
   `<content>`. That inner `<JobRequest>` element **is** a guess — nothing in this repository
   documents what `/operations` puts there — so do not assert on it. Confirming it against
   live firmware is currently unowned; see the spec's *Covered elsewhere*.

2. Append this helper and the eight tests directly after that constant, still at the end of
   the file:

   ```python
   _PARENT_UUID = "44444444-4444-4444-4444-444444444444"


   @pytest.mark.asyncio
   async def test_list_operations_reads_the_root_anchor(mock_hmc):
       path = "/rest/api/uom/ManagedSystem/operations"
       route = mock_hmc.get(path).mock(
           return_value=httpx.Response(200, text=OPERATIONS_FEED)
       )

       async with HMCClient(make_config()) as hmc:
           entries, _ = await hmc.list_operations("ManagedSystem")

       assert route.calls.last.request.url.path == path
       assert route.calls.last.request.headers["Accept"] == "*/*"
       assert [entry["title"] for entry in entries] == ["PowerOn"]


   @pytest.mark.asyncio
   async def test_list_operations_reads_the_child_anchor(mock_hmc):
       path = (
           f"/rest/api/uom/ManagedSystem/{_PARENT_UUID}"
           "/LogicalPartition/operations"
       )
       route = mock_hmc.get(path).mock(
           return_value=httpx.Response(200, text=OPERATIONS_FEED)
       )

       async with HMCClient(make_config()) as hmc:
           entries, _ = await hmc.list_operations(
               "LogicalPartition",
               parent_type="ManagedSystem",
               parent_uuid=_PARENT_UUID,
           )

       assert route.calls.last.request.url.path == path
       assert [entry["title"] for entry in entries] == ["PowerOn"]


   @pytest.mark.asyncio
   @pytest.mark.parametrize(
       ("headers", "expected"),
       [({"X-HMC-Schema-Version": "V1_0"}, "V1_0"), ({}, None)],
   )
   async def test_list_operations_returns_the_response_schema_version(
       mock_hmc, headers, expected
   ):
       mock_hmc.get("/rest/api/uom/ManagedSystem/operations").mock(
           return_value=httpx.Response(200, text=OPERATIONS_FEED, headers=headers)
       )

       async with HMCClient(make_config()) as hmc:
           _, schema_version = await hmc.list_operations("ManagedSystem")

       assert schema_version == expected


   @pytest.mark.asyncio
   async def test_list_operations_unknown_type_raises_hmc_error_with_status(mock_hmc):
       mock_hmc.get("/rest/api/uom/NoSuchType/operations").mock(
           return_value=httpx.Response(
               404, text="<HttpErrorResponse><Message>Unknown type</Message></HttpErrorResponse>"
           )
       )

       async with HMCClient(make_config()) as hmc:
           with pytest.raises(HMCError) as raised:
               await hmc.list_operations("NoSuchType")

       assert raised.value.status_code == 404
       assert "Unknown type" in str(raised.value)


   @pytest.mark.asyncio
   async def test_list_operations_204_returns_no_entries(mock_hmc):
       mock_hmc.get("/rest/api/uom/ManagedSystem/operations").mock(
           return_value=httpx.Response(204, headers={"X-HMC-Schema-Version": "V1_0"})
       )

       async with HMCClient(make_config()) as hmc:
           assert await hmc.list_operations("ManagedSystem") == ([], "V1_0")


   @pytest.mark.asyncio
   async def test_list_operations_rejects_a_non_uuid_parent(mock_hmc):
       """Refused before transport: the request is never attempted."""
       async with HMCClient(make_config()) as hmc:
           with pytest.raises(ValueError, match="parent_uuid must be a UUID"):
               await hmc.list_operations(
                   "LogicalPartition",
                   parent_type="ManagedSystem",
                   parent_uuid="not-a-uuid",
               )


   @pytest.mark.asyncio
   @pytest.mark.parametrize(
       "kwargs",
       [
           {"parent_type": "ManagedSystem"},
           {"parent_uuid": _PARENT_UUID},
       ],
   )
   async def test_list_operations_requires_both_parent_arguments(mock_hmc, kwargs):
       """Refused before transport: the request is never attempted."""
       async with HMCClient(make_config()) as hmc:
           with pytest.raises(ValueError, match="must be given together"):
               await hmc.list_operations("LogicalPartition", **kwargs)


   @pytest.mark.asyncio
   async def test_list_operations_rejects_a_dot_segment_type(mock_hmc):
       """Refused before transport: the request is never attempted."""
       async with HMCClient(make_config()) as hmc:
           with pytest.raises(HMCError, match=r"'\.\.' segment"):
               await hmc.list_operations("../web/Logon")

   ```

   `httpx`, `pytest`, `HMCClient`, `HMCError` and `make_config` are already imported at the
   top of `tests/unit/test_client.py`; confirm that before adding an import.

   The last three tests assert only that the call raises. They need no "and sent no request"
   assertion, and must not be given one: `mock_hmc` leaves respx at its `assert_all_mocked=True`
   default and registers no `/operations` route, so an attempted request raises
   `AllMockedAssertionError` and fails the test rather than matching the expected raise. An
   added assertion there could never fail.

3. Run the focused suite and confirm it is red for the right reason:

   ```sh
   uv run --no-sync pytest tests/unit/test_client.py -k list_operations --no-cov -q
   ```

   Expect 10 failures (the parametrized case counts twice), each an
   `AttributeError: 'HMCClient' object has no attribute 'list_operations'`. A different
   error means the fixture or an import is wrong; fix that before implementing.

4. Open `src/hmc_mcp/client/core.py` and insert this method immediately after `search_uom`
   ends (the line before the `# Virtual adapters (children of LogicalPartition)` comment):

   ```python
       async def list_operations(
           self,
           resource_type: str,
           *,
           parent_type: str | None = None,
           parent_uuid: str | None = None,
       ) -> tuple[list[dict[str, Any]], str | None]:
           """GET the job operations a type defines, with the schema version.

           Reads the root anchor ``/rest/api/uom/{R}/operations``, or the child
           anchor ``/rest/api/uom/{P}/{UUID}/{C}/operations`` when both
           *parent_type* and *parent_uuid* are given; supplying exactly one of
           them is a caller error. Returns the parsed feed paired with the
           response's ``X-HMC-Schema-Version`` (``None`` when the HMC sends
           none), because the operations a firmware level defines are only
           meaningful for the schema version that reported them (ADR 0139).

           Sends ``Accept: */*``. Nothing in this repository's HMC references
           documents what media type ``/operations`` answers with, so ``*/*``
           is the one Accept that cannot fail negotiation; a firmware level
           insisting on a typed Accept answers 406, which surfaces as
           ``HMCError`` carrying 406. No issue yet owns confirming this
           against live firmware.
           """
           uuid_path_arguments: dict[str, str] = {}
           if parent_type is not None and parent_uuid is not None:
               path = (
                   f"/rest/api/uom/{parent_type}/{parent_uuid}"
                   f"/{resource_type}/operations"
               )
               uuid_path_arguments["parent_uuid"] = parent_uuid
           elif parent_type is None and parent_uuid is None:
               path = f"/rest/api/uom/{resource_type}/operations"
           else:
               raise ValueError(
                   "parent_type and parent_uuid must be given together: a "
                   "child-anchored read needs both the parent type and the "
                   "parent instance UUID"
               )
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
           # No empty-body guard: the sibling reads carry one because ``_get``
           # collapses 204 to "", so they cannot tell the two apart. This method
           # returns on 204 above, and an empty 200 body is a malformed feed --
           # ``_parse_feed`` reporting it as HMCError is the honest answer.
           return _parse_feed(resp.text, path), schema_version
   ```

5. Re-run the focused suite and confirm it is green:

   ```sh
   uv run --no-sync pytest tests/unit/test_client.py -k list_operations --no-cov -q
   ```

   Expect `10 passed`.

6. Add the changelog bullet under `## [Unreleased]` / `### Added` in `CHANGELOG.md`, as the
   first bullet of that list:

   ```markdown
   - `HMCClient.list_operations(resource_type, *, parent_type=None, parent_uuid=None)` reads
     the job operations an HMC defines for a resource type, at the root anchor
     `/rest/api/uom/{R}/operations` and the child anchor
     `/rest/api/uom/{P}/{UUID}/{C}/operations`. It returns the parsed feed paired with the
     response's `X-HMC-Schema-Version`, or `None` when the HMC sends none (ADR 0139, #787).
   ```

7. Run the iteration guardrails:

   ```sh
   just lint
   just typecheck
   just test
   ```

   Expect `just lint` and `just typecheck` to print nothing and exit 0, and `just test` to
   print its one-line pass summary with coverage at or above 90.5%.

8. Commit. The prek `pre-commit` hook runs every `static` member, so this takes about as
   long as `just static`:

   ```sh
   git add -A
   git commit -m "feat: discover defined job operations via /operations"
   ```

9. Run the assembled-branch guardrails before any push:

   ```sh
   just verify
   uv run --no-sync prek run --all-files
   ```

   Expect `verify: all groups load OK` from the first and every hook `Passed` from the
   second. Run each bare — no pipe, no redirection — so its exit status is the result.

**Acceptance criteria.**

- `list_operations` exists on `HMCClient` with exactly the signature in the Interfaces block.
- Every contract in the Verification table is covered: all eight tests (ten cases) pass, and
  each was observed red before step 4.
- `CHANGELOG.md` carries the new `Unreleased / Added` bullet.
- `just verify` and `uv run --no-sync prek run --all-files` are both green.

**Rollback.** The change is additive with no callers, so reverting the single commit removes
it completely. Nothing persists, migrates, or has to be undone on an HMC.

## Deferrals

None.
