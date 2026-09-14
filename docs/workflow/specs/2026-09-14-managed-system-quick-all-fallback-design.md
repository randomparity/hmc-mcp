# Managed-system null-property fallback design

## Goal

Fix HTTP 500 failures from `get_managed_system(uuid)` and `list_managed_systems()`
on HMC firmware whose managed-system inventory feed contains a null
`VirtualPersistentMemoryVolume/Uuid` sub-field (issue #784), by falling back to
the already-working `find_system_by_name()` path via a new `quick/All` lookup.
See [ADR 0138](../../adr/0138-managed-system-null-property-fallback.md) for the
`quick/All` decision and its rejected alternatives.

## Constraints

- Client-library surface only: `src/hmc_mcp/client/client_systems.py`,
  `src/hmc_mcp/client/client_contracts.py`, `tests/unit/test_client.py`.
- No change to `operations/systems/core.py`, the CLI, or the MCP tool surface —
  both methods keep their existing signatures and return types.
- No change to `docs/recipes/system-inventory.md` (owned by unmerged PR #783,
  the file does not exist on `main`) or to `list_systems(hmc, state=...)`
  (already uses `search_uom("ManagedSystem", "State", state)` directly and is
  unaffected by this bug per the issue's own reproduction).
- No speculative `search_uom("ManagedSystem", "UUID", ...)` call — no evidence
  it is a supported search field (ADR 0138).
- No new dependency; same HMC REST API this client already depends on.

## Design

Add one private helper to `SystemsMixin`:

```python
async def _quick_all_system_names(self: SystemsClient) -> dict[str, str]:
    """UUID -> SystemName map from GET /rest/api/uom/ManagedSystem/quick/All.

    Undocumented in this repo's vendored HMC REST API reference; evidenced by
    IBM's public project-pim repository (ADR 0138). Needs live-HMC
    verification.
    """
```

It issues `self._request("GET", "/rest/api/uom/ManagedSystem/quick/All",
headers={"Accept": "*/*"})` — no typed `Accept` header, matching this repo's
own `get_quick_property` precedent (a typed `uom+xml` header 406s on `quick/`
endpoints, per its docstring in `core.py`) and `project-pim`'s working
`quick/All` calls, which also send none. Response handling otherwise follows
the existing `PcmMixin.fetch_json` pattern (`client_pcm.py`): non-200 and
JSON-decode failures raise `HMCError`; a non-list JSON body raises `HMCError`.
It builds the returned map defensively — an entry missing `UUID` or
`SystemName` is skipped, not raised.

`get_managed_system(uuid)` keeps its current direct `get_uom("ManagedSystem",
uuid)` call. On the exact null-property 500, it calls
`_quick_all_system_names()`, looks up `uuid`, and — if found — delegates to
`find_system_by_name(name)`, catching `HMCError` and the ambiguous-name
`ValueError` from either call (a collision can't be disambiguated, so it
counts as a miss here too, mirroring `list_managed_systems` below). A miss
raises a new actionable `HMCError` naming the `systems show <name>` fallback,
chained to the fallback's own exception when one was raised and to the
original 500 otherwise, so the more specific failure isn't discarded. Any
other `HMCError`, or a non-`HMCError` exception, re-raises unchanged.

`list_managed_systems()` keeps its current `list_uom("ManagedSystem")` call.
On the same failure pattern, it resolves every `_quick_all_system_names()`
entry via `find_system_by_name()`, catching `HMCError` and the ambiguous-name
`ValueError` per system so one failure doesn't drop the others, and returns
the resolved subset when non-empty. When nothing resolves (including when
`_quick_all_system_names()` itself fails), it raises the existing actionable
`HMCError` unchanged — chained to `_quick_all_system_names()`'s own exception
when it raised one, and to the original 500 otherwise.

`SystemsClient` (`client_contracts.py`) gains
`async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response: ...`,
matching `PcmClient`'s existing declaration — `_quick_all_system_names` is the
first `SystemsMixin` method to need it directly.

### Failure model

- Actors and deployments: an `hmc-mcp` CLI operator or MCP tool caller against
  a live HMC (REST) or a test harness against a respx-mocked HMC. No
  unauthenticated or externally-reachable actor is introduced.
- Invariants and assets at stake: none newly at stake — both methods remain
  read-only GETs; the fallback adds no write, no new credential use, and no
  new data retention. Correctness of the returned entry (same shape as the
  existing direct-fetch/search paths) is the only invariant.
- Accepted failure classes:
  - `quick/All` unavailable or firmware-different response shape: caught,
    treated as fallback-unavailable, original actionable error surfaces —
    bounded, never left un-actionable.
  - Ambiguous `SystemName` during `list_managed_systems`'s per-system
    resolution: that one system is skipped, not raised — bounded to a partial
    result rather than failing the whole call, consistent with "return the
    systems that are serializable."
  - `quick/All`'s exact response contract is unverified against a live HMC
    (`verification:live-hmc`) — accepted for this change because it is
    exercised only through respx-mocked tests in this repo; not reachable in
    CI, and tracked by the issue's own label rather than this spec.
  - `list_managed_systems`'s fallback resolves each system with its own
    sequential `find_system_by_name` call, so N systems cost 1+N request round
    trips instead of one on affected firmware — accepted because it only
    activates on firmware already broken today, with no evidence this
    firmware class hosts inventories large enough for the added latency to be
    material.
- Covered elsewhere: none — this is the totality of the changed surface's
  failure handling.

## Acceptance criteria

- `get_managed_system(uuid)` on the null-property HTTP 500 pattern resolves
  via `quick/All` + `find_system_by_name` and returns the entry on success.
- `get_managed_system(uuid)` raises a clear actionable `HMCError` when the
  fallback cannot resolve the system, instead of the raw nested-path message.
- `list_managed_systems()` on the same failure pattern returns the
  serializable subset of systems, skipping any that still fail.
- `list_managed_systems()` raises the existing actionable `HMCError` unchanged
  when nothing resolves via the fallback.
- Behavior for firmware without this bug, and for HTTP 500s that don't match
  the null-property pattern, is unchanged (`test_http_error_raises`,
  `test_managed_system_fallback_does_not_catch_runtime_errors` stay green
  without modification).
- `just verify` is green.

## Testing

Add respx routes for `GET /rest/api/uom/ManagedSystem/quick/All` in
`tests/unit/test_client.py` covering, per method: fallback success; fallback
miss (actionable error); `quick/All` itself failing (actionable error); and,
for `list_managed_systems` only, partial success (one resolves, one still
fails — the existing `test_managed_system_serialization_failure_is_not_empty_inventory`
gets a `quick/All` route added so its no-match case exercises the real
fallback-also-fails path). `get_managed_system` also needs its own
non-matching-error/non-`HMCError` passthrough tests: the existing
`test_http_error_raises` and
`test_managed_system_fallback_does_not_catch_runtime_errors` cover only
`list_managed_systems`. See the implementation plan for exact test cases.
