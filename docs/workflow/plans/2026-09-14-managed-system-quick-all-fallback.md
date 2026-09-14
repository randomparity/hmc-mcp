# Managed-system null-property fallback implementation plan

Goal: fix HTTP 500 failures from `get_managed_system(uuid)` and
`list_managed_systems()` on HMC firmware whose managed-system inventory feed
contains a null `VirtualPersistentMemoryVolume/Uuid` sub-field, by falling
back to the already-working `find_system_by_name()` path via a new
`GET /rest/api/uom/ManagedSystem/quick/All` lookup, added to `SystemsMixin`
(`src/hmc_mcp/client/client_systems.py`). Architecture: one new private async
helper (`_quick_all_system_names`) resolves a `{UUID: SystemName}` map from the
new endpoint; `get_managed_system` and `list_managed_systems` each catch the
exact null-property HTTP 500 and use that map plus the existing
`find_system_by_name` to resolve what the direct/unfiltered fetch could not.
Tech stack: Python 3.11, `httpx`, `pytest`/`pytest-asyncio`/`respx` for tests —
all already in use in this module and its test file.

See [the design spec](../specs/2026-09-14-managed-system-quick-all-fallback-design.md)
and [ADR 0138](../../adr/0138-managed-system-null-property-fallback.md).

Expected implementation size: 180–280 changed lines (M) — ~70 lines in
`client_systems.py` (one new helper method plus rewrites of two existing
methods), ~10 lines in `client_contracts.py` (one protocol method), ~150–200
lines of new and modified tests in `tests/unit/test_client.py`.

## Global Constraints

- Python 3.11 (`.python-version`); 100-char line length; ruff/ty clean.
- No new dependency; no change to `pyproject.toml` or `uv.lock`.
- `SystemsClient` protocol in `src/hmc_mcp/client/client_contracts.py` must
  stay in sync with what `SystemsMixin` (`client_systems.py`) calls on `self`.
- No change to `operations/systems/core.py`, the CLI, or the MCP tool surface.
- No change to `docs/recipes/system-inventory.md` or `list_systems(hmc,
  state=...)`.
- Every existing test in `tests/unit/test_client.py` must stay green except
  `test_managed_system_serialization_failure_is_not_empty_inventory`, which
  Task 3 modifies deliberately (see that task).
- Guardrails: `just test` (tests + coverage gate), `just lint` (`ruff check
  .`), `just typecheck` (`ty check`); `just verify` for the full pre-push
  suite. Run `uv run --no-sync pytest tests/unit/test_client.py -q` for the
  fast focused loop during this plan; run `just verify` once at the end.

## Task 1: Add the `quick/All` fetch helper and its protocol contract

### Files

- Modify: `src/hmc_mcp/client/client_contracts.py` (add `_request` to
  `SystemsClient`)
- Modify: `src/hmc_mcp/client/client_systems.py` (add
  `_quick_all_system_names`)
- Modify: `tests/unit/test_client.py` (add its focused test)

### Interfaces

- Provides: `SystemsMixin._quick_all_system_names(self: SystemsClient) ->
  dict[str, str]` — consumed by Task 2 and Task 3.
- Consumes: `HMCClient._request(self, method: str, path: str, **kwargs: Any)
  -> httpx.Response` (already defined in `src/hmc_mcp/client/core.py:427`,
  currently declared on `PcmClient` in `client_contracts.py` but not on
  `SystemsClient` — this task adds that declaration).

### Where this fits

Foundational helper with no behavior change to existing callers on its own;
Tasks 2 and 3 each wire one of the two public methods to use it.

### Steps

1. In `src/hmc_mcp/client/client_contracts.py`, in the `SystemsClient`
   Protocol (the one whose docstring reads `"""Host operations required by
   :class:`client_systems.SystemsMixin`."""`, directly above `class
   TemplatesClient(JobClient, Protocol):`), add, immediately after the class
   docstring and before `async def list_uom`:

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
           project-pim repository (ADR 0138). Used only as a fallback when the
           direct/unfiltered feed trips the null-property serialization bug, so
           an unexpected shape here is treated defensively: entries missing
           UUID or SystemName are skipped rather than raised.
           """
           resp = await self._request(
               "GET",
               "/rest/api/uom/ManagedSystem/quick/All",
               headers={"Accept": "application/json"},
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

- Contract: `_quick_all_system_names` maps a `quick/All` JSON array to
  `{UUID: SystemName}`, skipping malformed entries, and raises `HMCError` on a
  non-200 response.
  - Mode: focused-test
  - Test file/case: `tests/unit/test_client.py::test_quick_all_system_names_maps_uuid_to_name`,
    `tests/unit/test_client.py::test_quick_all_system_names_raises_on_non_200`
  - Expected red: before step 2, `hmc._quick_all_system_names` does not exist
    — `AttributeError: 'HMCClient' object has no attribute
    '_quick_all_system_names'`.
  - Green command: `uv run --no-sync pytest
    tests/unit/test_client.py::test_quick_all_system_names_maps_uuid_to_name
    tests/unit/test_client.py::test_quick_all_system_names_raises_on_non_200 -q`

### Acceptance criteria

- `SystemsClient` declares `_request`; `ty check` reports no missing-attribute
  error for `self._request` inside `SystemsMixin`.
- Both new tests pass; no other test in `tests/unit/test_client.py` changes
  behavior (Task 1 adds no caller of the new helper yet).

### Guardrails

`uv run --no-sync ruff check src/hmc_mcp/client/client_contracts.py
src/hmc_mcp/client/client_systems.py`; `uv run --no-sync ty check`.

## Task 2: Wire `get_managed_system` to the fallback

### Files

- Modify: `src/hmc_mcp/client/client_systems.py`
- Modify: `tests/unit/test_client.py`

### Interfaces

- Consumes: `_quick_all_system_names` (Task 1), `self.find_system_by_name`
  (existing, `client_systems.py:78`), `self.get_uom` (existing, part of
  `SystemsClient`).
- Changes the exception `get_managed_system` raises on the null-property 500
  when the fallback also cannot resolve the system — from the caller's own
  `HMCError` (propagated verbatim from `get_uom`) to a new actionable
  `HMCError`. No other caller of `get_managed_system` changes signature or
  return shape.

### Where this fits

Second of the two behavior fixes named in issue #784's Expected section
(`systems show <UUID>`).

### Steps

1. In `src/hmc_mcp/client/client_systems.py`, replace the existing
   `get_managed_system` method body:

   ```python
       async def get_managed_system(
           self: SystemsClient, uuid: str
       ) -> dict[str, Any] | None:
           return await self.get_uom("ManagedSystem", uuid)
   ```

   with:

   ```python
       async def get_managed_system(
           self: SystemsClient, uuid: str
       ) -> dict[str, Any] | None:
           # Some HMC firmware builds return HTTP 500 on a direct UUID fetch
           # for the same reason list_managed_systems does (see above): a
           # null-valued hardware-inventory sub-field the serialiser cannot
           # encode. find_system_by_name works on this firmware (it uses
           # search_uom, a different serialization path), but this method is
           # keyed by UUID, not name — quick/All supplies the missing name.
           try:
               return await self.get_uom("ManagedSystem", uuid)
           except HMCError as exc:
               if not (
                   exc.status_code == 500
                   and "Nested path contains null property" in str(exc)
               ):
                   raise
               entry = None
               try:
                   names = await self._quick_all_system_names()
                   name = names.get(uuid)
                   if name:
                       entry = await self.find_system_by_name(name)
               except HMCError:
                   entry = None
               if entry is None:
                   raise HMCError(
                       f"Managed system {uuid} is unavailable because this "
                       "HMC firmware could not serialize a null hardware "
                       "property, and it could not be resolved from the "
                       "managed-system summary; update the HMC firmware or "
                       "query the system by name with systems show",
                       status_code=500,
                       body=exc.body,
                   ) from exc
               return entry
   ```

   (An ambiguous-name `ValueError` from `find_system_by_name` is not caught
   here — it propagates, matching `find_system_by_name`'s own contract.)

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


   @pytest.mark.asyncio
   async def test_get_managed_system_fallback_miss_raises_actionable_error(mock_hmc):
       uuid = "sys-uuid-1"
       mock_hmc.get(f"/rest/api/uom/ManagedSystem/{uuid}").mock(
           return_value=httpx.Response(
               500, text="Nested path contains null property"
           )
       )
       mock_hmc.get("/rest/api/uom/ManagedSystem/quick/All").mock(
           return_value=httpx.Response(200, json=[])
       )
       async with HMCClient(make_config()) as hmc:
           with pytest.raises(HMCError, match="could not be resolved") as exc_info:
               await hmc.get_managed_system(uuid)
       assert exc_info.value.status_code == 500
   ```

### Verification

- Contract: `get_managed_system` on the null-property 500 resolves via
  `quick/All` + `find_system_by_name` and returns the entry.
  - Mode: focused-test
  - Test file/case:
    `tests/unit/test_client.py::test_get_managed_system_falls_back_via_quick_all`
  - Expected red: before step 1, `get_managed_system` re-raises the raw 500
    unhandled; `assert entry is not None` fails with that `HMCError`.
  - Green command: `uv run --no-sync pytest
    tests/unit/test_client.py::test_get_managed_system_falls_back_via_quick_all -q`
- Contract: `get_managed_system` raises a clear actionable `HMCError` when the
  fallback cannot resolve the system.
  - Mode: focused-test
  - Test file/case:
    `tests/unit/test_client.py::test_get_managed_system_fallback_miss_raises_actionable_error`
  - Expected red: before step 1, the raised message is the raw
    `"Nested path contains null property"` text, so the `match="could not be
    resolved"` assertion fails.
  - Green command: `uv run --no-sync pytest
    tests/unit/test_client.py::test_get_managed_system_fallback_miss_raises_actionable_error -q`

### Acceptance criteria

- Both new tests pass.
- `test_managed_system_fallback_does_not_catch_runtime_errors` and
  `test_http_error_raises` (existing, exercise `list_managed_systems`, not
  `get_managed_system`) remain unmodified and green.

### Guardrails

`uv run --no-sync ruff check src/hmc_mcp/client/client_systems.py`; `uv run
--no-sync ty check`.

## Task 3: Wire `list_managed_systems` to the fallback and update its existing test

### Files

- Modify: `src/hmc_mcp/client/client_systems.py`
- Modify: `tests/unit/test_client.py`

### Interfaces

- Consumes: `_quick_all_system_names` (Task 1), `self.find_system_by_name`
  (existing).
- `list_managed_systems` return contract stays `list[dict[str, Any]]`; the
  returned list may now be a subset of all managed systems on affected
  firmware, rather than always "all or nothing."

### Where this fits

First of the two behavior fixes named in issue #784's Expected section
(`systems list`); completes the design.

### Steps

1. In `src/hmc_mcp/client/client_systems.py`, replace the existing
   `list_managed_systems` method body (the `try/except HMCError` block) with:

   ```python
       async def list_managed_systems(self: SystemsClient) -> list[dict[str, Any]]:
           # Some HMC firmware builds return HTTP 500 on the unfiltered
           # ManagedSystem feed due to null property values in hardware-inventory
           # sub-elements (e.g. VirtualPersistentMemoryVolume/Uuid,
           # PersistentMemoryDevice/DynamicReconfigurationConnectorIndex, …).
           # The HMC serialiser trips on null-valued sub-fields it cannot encode.
           # find_system_by_name works on this firmware (it uses search_uom, a
           # different serialization path); quick/All supplies the names to
           # resolve each system that way. A system that still fails to resolve
           # (including an ambiguous name) is skipped rather than failing the
           # whole call, so the caller gets every system that is serializable.
           try:
               return await self.list_uom("ManagedSystem")
           except HMCError as exc:
               if not (
                   exc.status_code == 500
                   and "Nested path contains null property" in str(exc)
               ):
                   raise
               try:
                   names = await self._quick_all_system_names()
               except HMCError:
                   names = {}
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
               ) from exc
   ```

2. In `tests/unit/test_client.py`, update
   `test_managed_system_serialization_failure_is_not_empty_inventory` to
   register a `quick/All` route that resolves nothing, so the test exercises
   the real fallback-also-fails path instead of an unmocked-route artifact.
   Change:

   ```python
   @pytest.mark.asyncio
   async def test_managed_system_serialization_failure_is_not_empty_inventory(mock_hmc):
       firmware_error = HMCError(
           "GET failed: Nested path contains null property",
           status_code=500,
           body="Nested path contains null property",
       )
       async with HMCClient(make_config()) as hmc:
           hmc.list_uom = AsyncMock(side_effect=firmware_error)
           with pytest.raises(HMCError, match="firmware could not serialize") as exc_info:
               await hmc.list_managed_systems()

       assert exc_info.value.__cause__ is firmware_error
   ```

   to:

   ```python
   @pytest.mark.asyncio
   async def test_managed_system_serialization_failure_is_not_empty_inventory(mock_hmc):
       firmware_error = HMCError(
           "GET failed: Nested path contains null property",
           status_code=500,
           body="Nested path contains null property",
       )
       mock_hmc.get("/rest/api/uom/ManagedSystem/quick/All").mock(
           return_value=httpx.Response(200, json=[])
       )
       async with HMCClient(make_config()) as hmc:
           hmc.list_uom = AsyncMock(side_effect=firmware_error)
           with pytest.raises(HMCError, match="firmware could not serialize") as exc_info:
               await hmc.list_managed_systems()

       assert exc_info.value.__cause__ is firmware_error
   ```

3. In `tests/unit/test_client.py`, add a new test for the partial-success
   path, near the test modified in step 2:

   ```python
   @pytest.mark.asyncio
   async def test_list_managed_systems_returns_serializable_subset(mock_hmc):
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
           return_value=httpx.Response(500, text="Nested path contains null property")
       )
       async with HMCClient(make_config()) as hmc:
           hmc.list_uom = AsyncMock(side_effect=firmware_error)
           systems = await hmc.list_managed_systems()
       assert [s["UUID"] for s in systems] == ["sys-uuid-1"]
   ```

### Verification

- Contract: `list_managed_systems` on the null-property 500 returns the
  serializable subset (systems that resolve via `quick/All` +
  `find_system_by_name`), skipping systems that still fail.
  - Mode: focused-test
  - Test file/case:
    `tests/unit/test_client.py::test_list_managed_systems_returns_serializable_subset`
  - Expected red: before step 1, `list_managed_systems` raises instead of
    returning a list on this failure pattern.
  - Green command: `uv run --no-sync pytest
    tests/unit/test_client.py::test_list_managed_systems_returns_serializable_subset -q`
- Contract: `list_managed_systems` still raises the original actionable
  `HMCError`, chained to the same underlying error, when nothing resolves via
  the fallback.
  - Mode: focused-test
  - Test file/case:
    `tests/unit/test_client.py::test_managed_system_serialization_failure_is_not_empty_inventory`
    (modified in step 2)
  - Expected red: step 1 alone, without step 2's route, sends this
    unmodified test to an unmocked-route assertion error (step 1's code now
    calls `quick/All` unconditionally on this failure pattern). Step 2's route
    registration makes it green again through the real fallback-also-fails
    path.
  - Green command: `uv run --no-sync pytest
    tests/unit/test_client.py::test_managed_system_serialization_failure_is_not_empty_inventory -q`

### Acceptance criteria

- All three tests above pass.
- `test_http_error_raises` and
  `test_managed_system_fallback_does_not_catch_runtime_errors` remain
  unmodified and green (non-matching-error and non-`HMCError` passthrough is
  unaffected — the new `except HMCError as exc:` branch's `if not (...): raise`
  guard is unchanged from the existing code's equivalent condition).
- `just test` reports the coverage gate green (no untested branch introduced
  by this task's `try/except` additions).

### Guardrails

`uv run --no-sync ruff check src/hmc_mcp/client/client_systems.py`; `uv run
--no-sync ty check`; `just verify` once, at the end of Task 3, as the full
pre-push guardrail run for this plan.

## Plan self-review

- Spec criteria map to tasks: fallback success/miss → Task 2;
  `list_managed_systems` subset/full-failure → Task 3; unaffected-firmware
  behavior → unmodified `test_http_error_raises` and
  `test_managed_system_fallback_does_not_catch_runtime_errors`; `just verify`
  green → Task 3 guardrails.
- `_quick_all_system_names` is called identically in Tasks 2 and 3;
  `find_system_by_name`, `get_uom`, `list_uom`, `search_uom`, `_request` are
  pre-existing names confirmed against `client_systems.py`/`core.py`, not
  invented.
- No task references another task's content by pointer; each task's code
  blocks are self-contained.
