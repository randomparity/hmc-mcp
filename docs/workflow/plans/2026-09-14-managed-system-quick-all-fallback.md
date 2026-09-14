# Managed-system null-property fallback implementation plan

Goal: fix HTTP 500 failures from `get_managed_system(uuid)` and
`list_managed_systems()` on HMC firmware whose managed-system inventory feed
contains a null `VirtualPersistentMemoryVolume/Uuid` sub-field, by falling
back to `find_system_by_name()` via a new
`GET /rest/api/uom/ManagedSystem/quick/All` lookup in `SystemsMixin`
(`src/hmc_mcp/client/client_systems.py`). One new private async helper
(`_quick_all_system_names`) resolves a `{UUID: SystemName}` map from the new
endpoint; both public methods catch the exact null-property HTTP 500 and use
that map plus `find_system_by_name` to resolve what the direct/unfiltered
fetch could not. Stack: Python 3.11, `httpx`, `pytest`/`respx` — all in use here.

See [the design spec](../specs/2026-09-14-managed-system-quick-all-fallback-design.md)
and [ADR 0138](../../adr/0138-managed-system-null-property-fallback.md).

Expected implementation size: 180–280 changed lines (M) across
`client_systems.py`, `client_contracts.py`, and `tests/unit/test_client.py`.

## Global Constraints

- Python 3.11 (`.python-version`); 100-char line length; ruff/ty clean.
- No new dependency; no change to `pyproject.toml`, `uv.lock`,
  `operations/systems/core.py`, the CLI, the MCP tool surface,
  `docs/recipes/system-inventory.md`, or `list_systems(hmc, state=...)`.
- `SystemsClient` (`client_contracts.py`) must stay in sync with what
  `SystemsMixin` calls on `self`.
- Every existing test in `tests/unit/test_client.py` stays green except
  `test_managed_system_serialization_failure_is_not_empty_inventory`, which
  Task 3 modifies deliberately.
- Guardrails: `just test`, `just lint`, `just typecheck`; `just verify` for
  the full pre-push suite (run once, at the end). Fast loop: `uv run
  --no-sync pytest tests/unit/test_client.py -q`.

## Task 1: Add the `quick/All` fetch helper and its protocol contract

### Files

Modify: `client_contracts.py` (add `_request` to `SystemsClient`),
`client_systems.py` (add `_quick_all_system_names`), `tests/unit/test_client.py`.

### Interfaces

- Provides: `SystemsMixin._quick_all_system_names(self: SystemsClient) ->
  dict[str, str]` — consumed by Task 2 and Task 3.
- Consumes: `HMCClient._request` (`core.py:427`, already declared on
  `PcmClient` but not `SystemsClient` — this task adds that declaration).

### Where this fits

Foundational helper, no behavior change on its own; Tasks 2 and 3 each wire
one public method to use it.

### Steps

1. In `src/hmc_mcp/client/client_contracts.py`'s `SystemsClient` Protocol
   (directly above `class TemplatesClient(JobClient, Protocol):`), add,
   immediately after the class docstring and before `async def list_uom`:

   ```python
       async def _request(
           self, method: str, path: str, **kwargs: Any
       ) -> httpx.Response: ...

   ```

2. In `src/hmc_mcp/client/client_systems.py`, add the new method to
   `SystemsMixin`, directly above `async def list_managed_systems`:

   ```python
       async def _quick_all_system_names(self: SystemsClient) -> dict[str, str]:
           """UUID -> SystemName map from GET .../ManagedSystem/quick/All.

           Not documented in this repo's vendored HMC REST API reference (only
           the per-UUID quick/{Property} form is); evidenced by IBM's public
           project-pim repository (ADR 0138). No typed Accept header, matching
           get_quick_property's precedent (core.py) that a uom+xml header 406s
           on quick/ endpoints, and project-pim's own quick/All calls, which
           send none either. Used only as a fallback when the direct/unfiltered
           feed trips the null-property serialization bug, so an unexpected
           shape here is treated defensively: entries missing UUID or
           SystemName are skipped rather than raised.
           """
           resp = await self._request(
               "GET",
               "/rest/api/uom/ManagedSystem/quick/All",
               headers={"Accept": "*/*"},
           )
           if resp.status_code != 200:
               raise HMCError(
                   "GET /rest/api/uom/ManagedSystem/quick/All failed",
                   resp.status_code,
                   resp.text,
               )
           try:
               summaries = resp.json()
           except ValueError as exc:
               raise HMCError(
                   "GET /rest/api/uom/ManagedSystem/quick/All returned invalid "
                   f"JSON: {str(exc)[:500]}"
               ) from exc
           if not isinstance(summaries, list):
               raise HMCError(
                   "GET /rest/api/uom/ManagedSystem/quick/All returned a JSON "
                   f"{type(summaries).__name__}; expected an array"
               )
           return {
               entry["UUID"]: entry["SystemName"]
               for entry in summaries
               if isinstance(entry, dict) and "UUID" in entry and "SystemName" in entry
           }
   ```

3. In `tests/unit/test_client.py`, add the focused test below (near the
   existing `test_managed_system_serialization_failure_is_not_empty_inventory`
   test, after its definition):

   ```python
   @pytest.mark.asyncio
   async def test_quick_all_system_names_maps_uuid_to_name(mock_hmc):
       mock_hmc.get("/rest/api/uom/ManagedSystem/quick/All").mock(
           return_value=httpx.Response(
               200,
               json=[
                   {"UUID": "sys-uuid-1", "SystemName": "sys1", "State": "operating"},
                   {"UUID": "sys-uuid-2", "SystemName": "sys2", "State": "operating"},
                   {"State": "operating"},
               ],
           )
       )
       async with HMCClient(make_config()) as hmc:
           names = await hmc._quick_all_system_names()
       assert names == {"sys-uuid-1": "sys1", "sys-uuid-2": "sys2"}


   @pytest.mark.asyncio
   async def test_quick_all_system_names_raises_on_non_200(mock_hmc):
       mock_hmc.get("/rest/api/uom/ManagedSystem/quick/All").mock(
           return_value=httpx.Response(500, text="boom")
       )
       async with HMCClient(make_config()) as hmc:
           with pytest.raises(HMCError, match="quick/All failed"):
               await hmc._quick_all_system_names()
   ```

### Verification

- Contract: maps a `quick/All` JSON array to `{UUID: SystemName}`, skipping
  malformed entries, and raises `HMCError` on a non-200 response.
  - Mode: focused-test — `test_quick_all_system_names_maps_uuid_to_name`,
    `test_quick_all_system_names_raises_on_non_200`
  - Expected red: before step 2, `AttributeError` — the method doesn't exist.
  - Green: `uv run --no-sync pytest tests/unit/test_client.py -k quick_all_system_names -q`

### Acceptance criteria

- `SystemsClient` declares `_request`; `ty check` reports no missing-attribute error for it.
- Both new tests pass; no other test's behavior changes (Task 1 adds no caller of the helper yet).

### Guardrails

`uv run --no-sync ruff check src/hmc_mcp/client/`; `uv run --no-sync ty check`.

## Task 2: Wire `get_managed_system` to the fallback

### Files

Modify: `client_systems.py`, `tests/unit/test_client.py`.

### Interfaces

- Consumes: `_quick_all_system_names` (Task 1), `self.find_system_by_name`
  and `self.get_uom` (existing).
- On the null-property 500 when the fallback also can't resolve, changes the
  raised exception from `get_uom`'s propagated `HMCError` to a new actionable
  one. Signature and return shape are unchanged.

### Where this fits

Second of the two fixes named in issue #784's Expected section (`systems
show <UUID>`).

### Steps

1. In `src/hmc_mcp/client/client_systems.py`, replace `get_managed_system`'s
   current one-line body (`return await self.get_uom("ManagedSystem", uuid)`)
   with:

   ```python
       async def get_managed_system(
           self: SystemsClient, uuid: str
       ) -> dict[str, Any] | None:
           # Some firmware 500s on a direct UUID fetch over a null hardware
           # property (see list_managed_systems); quick/All supplies the
           # missing name so find_system_by_name (a different, working path)
           # can resolve it.
           try:
               return await self.get_uom("ManagedSystem", uuid)
           except HMCError as exc:
               if not (
                   exc.status_code == 500
                   and "Nested path contains null property" in str(exc)
               ):
                   raise
               entry = None
               fallback_exc: Exception | None = None
               try:
                   names = await self._quick_all_system_names()
                   name = names.get(uuid)
                   if name:
                       entry = await self.find_system_by_name(name)
               except (HMCError, ValueError) as fb_exc:
                   fallback_exc = fb_exc
               if entry is None:
                   raise HMCError(
                       f"Managed system {uuid} is unavailable because this "
                       "HMC firmware could not serialize a null hardware "
                       "property, and it could not be resolved from the "
                       "managed-system summary; update the HMC firmware or "
                       "query the system by name with systems show",
                       status_code=500,
                       body=exc.body,
                   ) from (fallback_exc or exc)
               return entry
   ```

   (Unlike `find_system_by_name`'s own contract, its ambiguous-name
   `ValueError` is caught here too — a collision counts as a fallback miss,
   mirroring Task 3's per-entry handling. `fallback_exc`, when set, is chained
   instead of the original 500 so the more specific failure isn't discarded.)

2. In `tests/unit/test_client.py`, add a shared minimal `ManagedSystem` feed
   builder near `LPAR_FEED` (after its definition), and the two new tests
   near `test_managed_system_serialization_failure_is_not_empty_inventory`:

   ```python
   def _managed_system_feed(uuid: str, name: str) -> str:
       return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
   <feed xmlns="http://www.w3.org/2005/Atom">
     <entry>
       <id>urn:uuid:{uuid}</id>
       <title>ManagedSystem:{name}</title>
       <link rel="SELF" href="{BASE}/rest/api/uom/ManagedSystem/{uuid}"/>
       <content type="application/vnd.ibm.powervm.uom+xml">
         <ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
           <SystemName>{name}</SystemName>
         </ManagedSystem>
       </content>
     </entry>
   </feed>
   """


   @pytest.mark.asyncio
   async def test_get_managed_system_falls_back_via_quick_all(mock_hmc):
       uuid = "sys-uuid-1"
       mock_hmc.get(f"/rest/api/uom/ManagedSystem/{uuid}").mock(
           return_value=httpx.Response(
               500,
               text="Nested path contains null property, "
               "currentProperty=Uuid nestedPath=VirtualPersistentMemoryVolume/Uuid/Value/Value",
           )
       )
       mock_hmc.get("/rest/api/uom/ManagedSystem/quick/All").mock(
           return_value=httpx.Response(
               200, json=[{"UUID": uuid, "SystemName": "sys1"}]
           )
       )
       mock_hmc.get("/rest/api/uom/ManagedSystem/search/(SystemName==sys1)").mock(
           return_value=httpx.Response(200, text=_managed_system_feed(uuid, "sys1"))
       )
       async with HMCClient(make_config()) as hmc:
           entry = await hmc.get_managed_system(uuid)
       assert entry is not None
       assert entry["UUID"] == uuid
       assert entry["Resource"]["SystemName"] == "sys1"


   @pytest.mark.parametrize(
       ("quick_all_response", "expect_quick_all_cause"),
       [
           (httpx.Response(200, json=[]), False),
           (httpx.Response(500, text="boom"), True),
       ],
       ids=["miss", "quick-all-fails"],
   )
   @pytest.mark.asyncio
   async def test_get_managed_system_fallback_failure_raises_actionable_error(
       mock_hmc, quick_all_response, expect_quick_all_cause
   ):
       uuid = "sys-uuid-1"
       mock_hmc.get(f"/rest/api/uom/ManagedSystem/{uuid}").mock(
           return_value=httpx.Response(
               500, text="Nested path contains null property"
           )
       )
       mock_hmc.get("/rest/api/uom/ManagedSystem/quick/All").mock(
           return_value=quick_all_response
       )
       async with HMCClient(make_config()) as hmc:
           with pytest.raises(HMCError, match="could not be resolved") as exc_info:
               await hmc.get_managed_system(uuid)
       assert exc_info.value.status_code == 500
       if expect_quick_all_cause:
           assert "quick/All failed" in str(exc_info.value.__cause__)


   @pytest.mark.asyncio
   async def test_get_managed_system_http_error_raises(mock_hmc):
       uuid = "sys-uuid-1"
       mock_hmc.get(f"/rest/api/uom/ManagedSystem/{uuid}").mock(
           return_value=httpx.Response(500, text="<m><Message>boom</Message></m>")
       )
       async with HMCClient(make_config()) as hmc:
           with pytest.raises(HMCError) as exc_info:
               await hmc.get_managed_system(uuid)
       assert exc_info.value.status_code == 500
       assert "boom" in str(exc_info.value)


   @pytest.mark.asyncio
   async def test_get_managed_system_does_not_catch_runtime_errors(mock_hmc):
       async with HMCClient(make_config()) as hmc:
           hmc.get_uom = AsyncMock(side_effect=RuntimeError("parser invariant failed"))
           with pytest.raises(RuntimeError, match="parser invariant failed"):
               await hmc.get_managed_system("sys-uuid-1")
   ```

### Verification

- Contract: fallback success (resolves via `quick/All` + `find_system_by_name`).
  - Mode: focused-test — `test_get_managed_system_falls_back_via_quick_all`
  - Expected red: before step 1, the raw 500 propagates unhandled.
  - Green: `uv run --no-sync pytest tests/unit/test_client.py::test_get_managed_system_falls_back_via_quick_all -q`
- Contract: actionable `HMCError` when the fallback can't resolve (miss, or
  `quick/All` itself fails), chained to the fallback's own exception when one
  was raised.
  - Mode: focused-test — `test_get_managed_system_fallback_failure_raises_actionable_error`
    (both parametrize cases)
  - Expected red: before step 1, the raw `"Nested path contains null
    property"` message fails the `match="could not be resolved"` assertion.
  - Green: `uv run --no-sync pytest tests/unit/test_client.py -k get_managed_system_fallback_failure -q`
- Contract: a non-matching `HMCError`, or a non-`HMCError` exception, from the
  direct fetch re-raises unchanged (the new `try/except` doesn't over-catch).
  - Mode: task-test-not-applicable — `get_managed_system` is currently a
    one-line passthrough, so `test_get_managed_system_http_error_raises` and
    `test_get_managed_system_does_not_catch_runtime_errors` already pass; they
    guard an invariant step 1 must preserve, not a new behavior.
  - Green: `uv run --no-sync pytest tests/unit/test_client.py -k "get_managed_system_http_error_raises or get_managed_system_does_not_catch" -q`

### Acceptance criteria

- All four new tests pass (five cases, counting the parametrized fallback-failure test's two ids).

### Guardrails

`uv run --no-sync ruff check src/hmc_mcp/client/client_systems.py`; `uv run --no-sync ty check`.

## Task 3: Wire `list_managed_systems` to the fallback and update its existing test

### Files

Modify: `client_systems.py`, `tests/unit/test_client.py`.

### Interfaces

- Consumes: `_quick_all_system_names` (Task 1), `self.find_system_by_name`
  (existing).
- Return contract stays `list[dict[str, Any]]`; the list may now be a subset
  on affected firmware, rather than always "all or nothing."

### Where this fits

First of the two fixes named in issue #784's Expected section (`systems
list`); completes the design.

### Steps

1. In `src/hmc_mcp/client/client_systems.py`, replace the existing
   `list_managed_systems` method body (the `try/except HMCError` block) with:

   ```python
       async def list_managed_systems(self: SystemsClient) -> list[dict[str, Any]]:
           # Some firmware 500s on the unfiltered feed over a null
           # hardware-inventory property (e.g. VirtualPersistentMemoryVolume/Uuid).
           # quick/All + find_system_by_name (a different, working path) resolve
           # what they can; a system that still fails (or resolves ambiguously)
           # is skipped rather than failing the whole call.
           try:
               return await self.list_uom("ManagedSystem")
           except HMCError as exc:
               if not (
                   exc.status_code == 500
                   and "Nested path contains null property" in str(exc)
               ):
                   raise
               quick_all_exc: HMCError | None = None
               try:
                   names = await self._quick_all_system_names()
               except HMCError as qa_exc:
                   names = {}
                   quick_all_exc = qa_exc
               resolved: list[dict[str, Any]] = []
               for name in names.values():
                   try:
                       entry = await self.find_system_by_name(name)
                   except (HMCError, ValueError):
                       continue
                   if entry is not None:
                       resolved.append(entry)
               if resolved:
                   return resolved
               raise HMCError(
                   "Managed-system inventory is unavailable because this HMC "
                   "firmware could not serialize a null hardware property; "
                   "update the HMC firmware or query a managed system directly",
                   status_code=500,
                   body=exc.body,
               ) from (quick_all_exc or exc)
   ```

2. In `tests/unit/test_client.py`, update
   `test_managed_system_serialization_failure_is_not_empty_inventory` to
   register a `quick/All` route that resolves nothing, so it exercises the
   real fallback-also-fails (empty-result) path instead of an unmocked-route
   artifact. Insert immediately after the `firmware_error = HMCError(...)` block:

   ```python
       mock_hmc.get("/rest/api/uom/ManagedSystem/quick/All").mock(
           return_value=httpx.Response(200, json=[])
       )
   ```

   The trailing `assert exc_info.value.__cause__ is firmware_error` assertion
   is unchanged: `quick/All` succeeds here (empty match, no `quick_all_exc`),
   so the raised error still chains to `firmware_error`.

3. In `tests/unit/test_client.py`, add a parametrized test covering both the
   partial-success and full-success paths, near the test modified in step 2:

   ```python
   @pytest.mark.parametrize(
       ("sys2_response", "expected_uuids"),
       [
           (
               httpx.Response(500, text="Nested path contains null property"),
               {"sys-uuid-1"},
           ),
           (
               httpx.Response(200, text=_managed_system_feed("sys-uuid-2", "sys2")),
               {"sys-uuid-1", "sys-uuid-2"},
           ),
       ],
       ids=["partial", "all-resolve"],
   )
   @pytest.mark.asyncio
   async def test_list_managed_systems_resolves_serializable_systems(
       mock_hmc, sys2_response, expected_uuids
   ):
       firmware_error = HMCError(
           "GET failed: Nested path contains null property",
           status_code=500,
           body="Nested path contains null property",
       )
       mock_hmc.get("/rest/api/uom/ManagedSystem/quick/All").mock(
           return_value=httpx.Response(
               200,
               json=[
                   {"UUID": "sys-uuid-1", "SystemName": "sys1"},
                   {"UUID": "sys-uuid-2", "SystemName": "sys2"},
               ],
           )
       )
       mock_hmc.get("/rest/api/uom/ManagedSystem/search/(SystemName==sys1)").mock(
           return_value=httpx.Response(
               200, text=_managed_system_feed("sys-uuid-1", "sys1")
           )
       )
       mock_hmc.get("/rest/api/uom/ManagedSystem/search/(SystemName==sys2)").mock(
           return_value=sys2_response
       )
       async with HMCClient(make_config()) as hmc:
           hmc.list_uom = AsyncMock(side_effect=firmware_error)
           systems = await hmc.list_managed_systems()
       assert {s["UUID"] for s in systems} == expected_uuids
   ```

4. In `tests/unit/test_client.py`, add one more test near the one added in
   step 3:

   ```python
   @pytest.mark.asyncio
   async def test_list_managed_systems_fallback_quick_all_fails_raises_actionable_error(
       mock_hmc,
   ):
       firmware_error = HMCError(
           "GET failed: Nested path contains null property",
           status_code=500,
           body="Nested path contains null property",
       )
       mock_hmc.get("/rest/api/uom/ManagedSystem/quick/All").mock(
           return_value=httpx.Response(500, text="boom")
       )
       async with HMCClient(make_config()) as hmc:
           hmc.list_uom = AsyncMock(side_effect=firmware_error)
           with pytest.raises(HMCError, match="firmware could not serialize") as exc_info:
               await hmc.list_managed_systems()
       assert "quick/All failed" in str(exc_info.value.__cause__)
   ```

### Verification

- Contract: returns every serializable system (all resolve) or the
  serializable subset (some resolve), skipping systems that still fail.
  - Mode: focused-test — `test_list_managed_systems_resolves_serializable_systems`
    (both parametrize cases)
  - Expected red: before step 1, the method raises instead of returning a list.
  - Green: `uv run --no-sync pytest tests/unit/test_client.py -k list_managed_systems_resolves_serializable -q`
- Contract: raises the original actionable `HMCError` when nothing resolves
  (no `quick/All` matches, or `quick/All` itself fails), chained to the
  fallback's own exception when one was raised.
  - Mode: focused-test — `test_managed_system_serialization_failure_is_not_empty_inventory`
    (modified in step 2), `test_list_managed_systems_fallback_quick_all_fails_raises_actionable_error`
  - Expected red: step 1 alone, without step 2/4's routes, hits an
    unmocked-route assertion error — step 1's code now calls `quick/All`
    unconditionally on this failure pattern.
  - Green: `uv run --no-sync pytest tests/unit/test_client.py -k "serialization_failure_is_not_empty or list_managed_systems_fallback_quick_all_fails" -q`

### Acceptance criteria

- The two new tests pass (three cases, counting the parametrized test's two
  ids); the modified `test_managed_system_serialization_failure_is_not_empty_inventory`,
  `test_http_error_raises`, and `test_managed_system_fallback_does_not_catch_runtime_errors`
  stay green.
- `just test` reports the coverage gate green (no untested branch introduced).

### Guardrails

`uv run --no-sync ruff check src/hmc_mcp/client/client_systems.py`; `uv run --no-sync ty check`;
`just verify` once, at the end of Task 3, as the full pre-push run for this plan.

## Plan self-review

Every spec acceptance criterion maps to a task above; all consumed method
names are pre-existing, confirmed against `client_systems.py`/`core.py`.
