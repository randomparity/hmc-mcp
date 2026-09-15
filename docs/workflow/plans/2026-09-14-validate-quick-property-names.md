# Plan: validate quick-property names before transport

**Goal.** Give `HMCClient.get_quick_property` an opt-in path that refuses, before any request is
sent, a `property_name` the resource type does not define, using the names
`HMCClient.list_quick_properties` already reads, cached once per resource type per client.

**Architecture.** Everything lands in one module, `src/hmc_mcp/client/core.py`, which already owns
both methods and the client's per-instance state. `HMCClient.__init__` gains one dict; a new private
async helper fills it on a cache miss by calling the existing `list_quick_properties`, storing the
failure as well as the success so a failing anchor is read at most once; and `get_quick_property`
gains one keyword-only `validate` flag that consults the helper and raises `ValueError` before
building its path. No existing caller changes, because the default is `False`.

**Tech stack.** Python (`>=3.11`, per `pyproject.toml`), `httpx` for transport, `pytest` +
`pytest-asyncio` + `respx` for tests, `uv` for the environment, `just` for guardrails.

Expected implementation size: 150–210 changed lines (M) — derived from the file map and task list
below: roughly 45 lines of source across three edits in `core.py`, roughly 130 lines of new tests in
`tests/unit/test_client.py`, and a CHANGELOG entry. It excludes the design artifacts themselves
(ADR 0141, this plan, the spec), which are not implementation.

## Global Constraints

Transcribed from the spec and the repository's own instructions; every task's requirements
implicitly include this section.

- **Design record.** `docs/adr/0141-quick-property-name-validation-is-opt-in.md` governs this
  change. `validate` defaults to `False`; the cache lives on the `HMCClient` instance, is keyed by
  resource type, is never invalidated or refreshed within a session, and stores a failed discovery
  read as a negative entry. Do not change any of these without returning to the design.
- **Protected contract.** `hmc_mcp.api` exports exactly six names (ADR 0118): `HMCClient`,
  `HMCConfig`, `ConfigError`, `HMCError`, `HMCTransportError`, `TLSVerificationDisabledWarning`.
  Do not add to it. Do not edit `tests/unit/test_public_api.py`.
- **Files you may change.** `src/hmc_mcp/client/core.py`, `tests/unit/test_client.py`,
  `CHANGELOG.md`. Nothing else. In particular do not touch
  `src/hmc_mcp/client/client_parse.py`, `client_systems.py`, `client_updates.py`, `client_pcm.py`,
  the `justfile`, `AGENTS.md`, or `scripts/`.
- **Environment.** Never run a bare `uv sync`, `uv run` or `uv add`: they prune the `app` extra and
  break `just typecheck`. The worktree is bootstrapped with `just setup`; run tools through `just`
  or `uv run --no-sync`.
- **Guardrails.** Run them bare — no pipes, no `| tail`, no `|| true`. Exit codes are the truth.
  Iterate with `just test` and the named `static` sub-recipes (`just lint`, `just typecheck`,
  `just adr-numbering`, `just doc-freshness`); the full pre-push gate is `just verify`, and the hook
  leg `just verify` does not cover is `uv run --no-sync prek run --all-files`.
- **Test configuration.** `tests/conftest.py` supplies `make_config(**kw)` (line 395), which builds
  an `HMCConfig` with host `hmc.test`, and the `mock_hmc` fixture (line 408), a `respx` router with
  `PUT`/`DELETE /rest/api/web/Logon` pre-mocked and `assert_all_called=False`. Use both; do not
  build an `HMCConfig` by hand.
- **Existing test helpers in `tests/unit/test_client.py`.** `_quick_property_entry(rest_element,
  *properties)` (line 2404) renders the FW950 `<entry>` body, where each property is a nickname
  string or a `(nickname, description)` pair. `_MANAGED_SYSTEM_NICKNAMES` (line 2382) is a list of
  eight captured `ManagedSystem` names beginning `"ProcessorThrottling"` and including
  `"SystemType"`. Reuse both.
- **Style.** Prefer ≤100 lines per function and ≤100-character lines. Zero new warnings. Comments
  explain non-obvious invariants only.

## File map

| File | Owns today | Owns after |
|---|---|---|
| `src/hmc_mcp/client/core.py` | `HMCClient.__init__`, `get_quick_property` (line 692), `list_quick_properties` (line 717) | the same, plus the per-client name cache, the private read-through helper, and `get_quick_property`'s `validate` branch |
| `tests/unit/test_client.py` | the `list_quick_properties` tests at lines 2365–2811 | the same, plus one `get_quick_property` validation block after them |
| `CHANGELOG.md` | the `## [Unreleased] / ### Added` list | the same, plus one entry for the new parameter |

No file is created, moved or removed. No caller migrates: the new behaviour is unreachable without
passing `validate=True`, and none of the five existing call sites
(`src/hmc_mcp/operations/vios/core.py:99`, `src/hmc_mcp/operations/lpar/core.py:140,417,481`,
`src/hmc_mcp/operations/lpar/decommission.py:542`) does. No path becomes obsolete, and no
compatibility path is retained, because nothing is replaced.

## Task 1 — The cache, the read-through helper, and the `validate` branch

**Where this fits.** This is the whole implementation. It is one task because no reviewer could
accept the cache without the branch that uses it, or the branch without the cache it reads.

**Creates:** nothing. **Modifies:** `src/hmc_mcp/client/core.py`, `CHANGELOG.md`.
**Tests:** `tests/unit/test_client.py`.

**Interfaces.**

Consumed from the existing codebase, each confirmed present at `fac7c19e` with the signature
assumed here:

- `HMCClient.list_quick_properties(self, resource_type: str, *, parent_type: str | None = None,
  parent_uuid: str | None = None) -> tuple[list[str], str | None]` —
  `src/hmc_mcp/client/core.py:717`. Raises `HMCError` on a non-200/204 status, on a 200 carrying no
  `Nickname`, and on malformed XML.
- `HMCError` and `HMCTransportError` — `src/hmc_mcp/errors.py:17` and `:47`.
  `class HMCTransportError(HMCError)`, so `except HMCError` catches both.
- `HMCClient._request_with_uuid_path_arguments(self, method: str, path: str, *,
  uuid_path_arguments: Mapping[str, str], **kwargs: Any) -> httpx.Response` —
  `src/hmc_mcp/client/core.py:453`.

Published for later readers (there is no later task; this block is what the tests bind to):

- `HMCClient.get_quick_property(self, resource_type: str, uuid: str, property_name: str, *,
  validate: bool = False) -> str | None`
- `HMCClient._defined_quick_property_names(self, resource_type: str) -> frozenset[str] | None`
- `HMCClient._quick_property_names: dict[str, frozenset[str] | None]`

### Verification inventory

- **Contract: `validate=True` raises `ValueError` before transport for an undefined name.**
  Mode: `focused-test`. Observable: no request reaches
  `/rest/api/uom/ManagedSystem/{uuid}/quick/NoSuchProperty`, and `ValueError` is raised.
  Test: `tests/unit/test_client.py::test_get_quick_property_validate_refuses_an_undefined_name`.
  Expected red before step 4: `TypeError: get_quick_property() got an unexpected keyword argument
  'validate'`. Green: `uv run --no-sync pytest
  "tests/unit/test_client.py::test_get_quick_property_validate_refuses_an_undefined_name" -q`.
  The `justfile` has no single-test recipe, so a focused run goes through `pytest` directly; the
  repository-wide gate stays `just test`.
- **Contract: `validate=True` passes a defined name through.**
  Mode: `focused-test`. Observable: the value route is called and its value is returned.
  Test: `::test_get_quick_property_validate_allows_a_defined_name`. Expected red: the same
  `TypeError`. Green: the same `pytest` invocation with this node id.
- **Contract: at most one discovery request per resource type per client.**
  Mode: `focused-test`. Observable: `discovery.call_count == 1` after two validated calls.
  Test: `::test_get_quick_property_validate_reads_the_names_once_per_type`. Expected red: the same
  `TypeError`. Green: the same `pytest` invocation with this node id.
- **Contract: a fresh client re-reads.**
  Mode: `focused-test`. Observable: `discovery.call_count == 2` after one validated call in each of
  two client sessions. Test: `::test_get_quick_property_validate_rereads_for_a_new_client`.
  Expected red: the same `TypeError`. Green: the same `pytest` invocation with this node id.
- **Contract: a failed discovery read degrades to unvalidated behaviour.**
  Mode: `focused-test`. Observable: discovery answers 500, the value request is still sent, and the
  value is returned rather than an exception raised.
  Test: `::test_get_quick_property_validate_degrades_when_discovery_fails`. Expected red: the same
  `TypeError`. Green: the same `pytest` invocation with this node id.
- **Contract: the failure is cached, not retried per call.**
  Mode: `focused-test`. Observable: `discovery.call_count == 1` after two validated calls against a
  failing anchor. Test: `::test_get_quick_property_validate_caches_a_failed_discovery`. Expected
  red: the same `TypeError`. Green: the same `pytest` invocation with this node id.
- **Contract: the default is `False` and makes no discovery request.**
  Mode: `focused-test`. Observable: an undefined name still round-trips and returns the HMC's
  answer, with `discovery.call_count == 0`.
  Test: `::test_get_quick_property_defaults_to_no_validation`. Expected red: the test's
  `discovery.call_count == 0` assertion cannot fail before the change, so the red observation for
  this contract is carried by the signature test below, which fails on the missing parameter.
- **Contract: `validate` is keyword-only and defaults to `False`.**
  Mode: `focused-test`. Observable: `inspect.signature(HMCClient.get_quick_property)` lists
  `validate` with `kind == KEYWORD_ONLY` and `default is False`.
  Test: `::test_get_quick_property_validate_is_keyword_only_and_defaults_false`. Expected red:
  `KeyError: 'validate'`. Green: the same `pytest` invocation with this node id.
- **Contract: ADR 0118's six facade exports are unchanged.**
  Mode: `task-test-not-applicable`. `tests/unit/test_public_api.py` already asserts that exact set
  and fails on any drift; a new test here would observe that existing test's subject rather than
  anything this task changes, and this task adds no export.
- **Contract: the `CHANGELOG.md` entry.**
  Mode: `task-test-not-applicable`. `tests/unit/test_changelog.py` asserts only that the version
  declared in `pyproject.toml` (`0.1.0`) has a `## [0.1.0]` heading; this task changes no version
  and adds prose under `## [Unreleased]`, which no executable consumer validates. Asserting its
  wording would snapshot prose.

### Steps

**Step 1 — read the code you are about to change.** Open
`src/hmc_mcp/client/core.py` at lines 248–272 (`HMCClient.__init__`), 692–715
(`get_quick_property`) and 717–790 (`list_quick_properties`). Confirm `get_quick_property` today
takes exactly `(self, resource_type, uuid, property_name)` and builds
`f"/rest/api/uom/{resource_type}/{uuid}/quick/{property_name}"`.

**Step 2 — write the failing tests.** Append this block to `tests/unit/test_client.py`, after the
existing `list_quick_properties` tests (which end at line 2811). `inspect`, `httpx`, `pytest` and
`HMCClient` are already imported at the top of that module; `make_config` and `mock_hmc` come from
`tests/conftest.py`.

```python
# get_quick_property validation (#799) -- ADR 0141: opt-in, cached per client.

_VALIDATION_UUID = "3f2b1c9d-4e5a-4b6c-8d7e-9f0a1b2c3d4e"
_VALIDATION_TYPE = "ManagedSystem"
_VALIDATION_DISCOVERY = f"/rest/api/uom/{_VALIDATION_TYPE}/quick"


def _validation_discovery_body() -> str:
    """The /quick answer for _VALIDATION_TYPE, using the captured names."""
    return _quick_property_entry(_VALIDATION_TYPE, *_MANAGED_SYSTEM_NICKNAMES)


def _mock_validation_routes(router, *, discovery_status=200, value="running"):
    """Mock the discovery anchor and the single-value read, returning both routes."""
    if discovery_status == 200:
        discovery_response = httpx.Response(200, text=_validation_discovery_body())
    else:
        discovery_response = httpx.Response(discovery_status, text="<error/>")
    discovery = router.get(_VALIDATION_DISCOVERY).mock(return_value=discovery_response)
    defined = router.get(
        f"/rest/api/uom/{_VALIDATION_TYPE}/{_VALIDATION_UUID}/quick/SystemType"
    ).mock(return_value=httpx.Response(200, text=value))
    undefined = router.get(
        f"/rest/api/uom/{_VALIDATION_TYPE}/{_VALIDATION_UUID}/quick/NoSuchProperty"
    ).mock(return_value=httpx.Response(200, text="unreachable-when-validating"))
    return discovery, defined, undefined


@pytest.mark.asyncio
async def test_get_quick_property_validate_refuses_an_undefined_name(mock_hmc):
    """An undefined name raises before the value request is built.

    The point of the feature: the refusal costs no round trip, so the value
    route must record no call at all rather than a call that failed.
    """
    discovery, _, undefined = _mock_validation_routes(mock_hmc)

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(ValueError) as excinfo:
            await hmc.get_quick_property(
                _VALIDATION_TYPE, _VALIDATION_UUID, "NoSuchProperty", validate=True
            )

    assert not undefined.called
    assert discovery.call_count == 1
    message = str(excinfo.value)
    assert "NoSuchProperty" in message
    assert _VALIDATION_TYPE in message
    # The defined names are the actionable half: a caller who misspelled one
    # needs to see the spelling that would have worked.
    assert "SystemType" in message


@pytest.mark.asyncio
async def test_get_quick_property_validate_allows_a_defined_name(mock_hmc):
    """A name the type defines is read exactly as it is without validation."""
    discovery, defined, _ = _mock_validation_routes(mock_hmc, value="Fixed")

    async with HMCClient(make_config()) as hmc:
        value = await hmc.get_quick_property(
            _VALIDATION_TYPE, _VALIDATION_UUID, "SystemType", validate=True
        )

    assert value == "Fixed"
    assert defined.call_count == 1
    assert discovery.call_count == 1


@pytest.mark.asyncio
async def test_get_quick_property_validate_reads_the_names_once_per_type(mock_hmc):
    """The cost bound ADR 0141 states: one discovery read per type per client."""
    discovery, defined, _ = _mock_validation_routes(mock_hmc)

    async with HMCClient(make_config()) as hmc:
        await hmc.get_quick_property(
            _VALIDATION_TYPE, _VALIDATION_UUID, "SystemType", validate=True
        )
        await hmc.get_quick_property(
            _VALIDATION_TYPE, _VALIDATION_UUID, "SystemType", validate=True
        )

    assert defined.call_count == 2
    assert discovery.call_count == 1


@pytest.mark.asyncio
async def test_get_quick_property_validate_rereads_for_a_new_client(mock_hmc):
    """Cache lifetime is the client's: a fresh HMCClient reads the names again.

    ADR 0141 states this rather than implying it, because it is what bounds how
    stale an entry can get -- and in the MCP deployment the client is per call.
    """
    discovery, _, _ = _mock_validation_routes(mock_hmc)

    for _ in range(2):
        async with HMCClient(make_config()) as hmc:
            await hmc.get_quick_property(
                _VALIDATION_TYPE, _VALIDATION_UUID, "SystemType", validate=True
            )

    assert discovery.call_count == 2


@pytest.mark.asyncio
async def test_get_quick_property_validate_degrades_when_discovery_fails(mock_hmc):
    """A firmware level whose discovery read fails keeps get_quick_property working.

    ADR 0139 records three V1_20_0 HMCs answering 500 at the sibling
    /operations anchor; #799's fourth criterion is that this must not become a
    broken get_quick_property.
    """
    discovery, _, undefined = _mock_validation_routes(mock_hmc, discovery_status=500)

    async with HMCClient(make_config()) as hmc:
        value = await hmc.get_quick_property(
            _VALIDATION_TYPE, _VALIDATION_UUID, "NoSuchProperty", validate=True
        )

    assert value == "unreachable-when-validating"
    assert discovery.call_count == 1
    assert undefined.call_count == 1


@pytest.mark.asyncio
async def test_get_quick_property_validate_caches_a_failed_discovery(mock_hmc):
    """The failure is cached too, or the cost bound is one extra request per call."""
    discovery, _, undefined = _mock_validation_routes(mock_hmc, discovery_status=400)

    async with HMCClient(make_config()) as hmc:
        for _ in range(2):
            await hmc.get_quick_property(
                _VALIDATION_TYPE, _VALIDATION_UUID, "NoSuchProperty", validate=True
            )

    assert discovery.call_count == 1
    assert undefined.call_count == 2


@pytest.mark.asyncio
async def test_get_quick_property_defaults_to_no_validation(mock_hmc):
    """The default is unchanged behaviour: no discovery read, name sent as given.

    ADR 0141 turns on this staying true -- it is why the CHANGELOG entry records
    an addition rather than facade movement in behaviour.
    """
    discovery, _, undefined = _mock_validation_routes(mock_hmc)

    async with HMCClient(make_config()) as hmc:
        value = await hmc.get_quick_property(
            _VALIDATION_TYPE, _VALIDATION_UUID, "NoSuchProperty"
        )

    assert value == "unreachable-when-validating"
    assert undefined.call_count == 1
    assert discovery.call_count == 0


def test_get_quick_property_validate_is_keyword_only_and_defaults_false():
    """Positional or default-True would both be the facade movement ADR 0141 declined."""
    parameter = inspect.signature(HMCClient.get_quick_property).parameters["validate"]

    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is False
```

**Step 3 — confirm the expected red.** Run, bare:

```sh
uv run --no-sync pytest tests/unit/test_client.py -k get_quick_property_validate -q
```

Expect failures, and expect them to name the missing parameter:
`TypeError: HMCClient.get_quick_property() got an unexpected keyword argument 'validate'` for the
async tests, and `KeyError: 'validate'` for
`test_get_quick_property_validate_is_keyword_only_and_defaults_false`. A different failure means the
test block is wrong, not the source; fix the test before continuing.

**Step 4 — add the cache to `__init__`.** In `src/hmc_mcp/client/core.py`, inside
`HMCClient.__init__`, immediately after the `self._session_token: str | None = None` line, insert:

```python
        # Quick-property names per resource type, read once and kept for this
        # client's lifetime (ADR 0141). A None value is a discovery read that
        # failed: caching the failure is what holds the one-request-per-type
        # bound, since retrying per call is the request this design promises
        # not to make.
        self._quick_property_names: dict[str, frozenset[str] | None] = {}
```

**Step 5 — add the read-through helper.** In the same file, insert this method immediately before
`async def list_quick_properties`:

```python
    async def _defined_quick_property_names(self, resource_type: str) -> frozenset[str] | None:
        """The quick-property names *resource_type* defines, or None if unknown.

        Reads the root ``/quick`` anchor once per type per client and caches the
        answer, the failure included: a level where discovery does not work
        yields None, which callers read as "do not validate" rather than as "no
        properties" (ADR 0141).
        """
        if resource_type not in self._quick_property_names:
            try:
                names, _ = await self.list_quick_properties(resource_type)
            except HMCError:
                # Covers HMCTransportError too, which subclasses it. Degrading
                # is #799's fourth criterion: ADR 0139 records levels where the
                # sibling /operations anchor answers 500, and ADR 0140 records
                # a type answering 400 at the root /quick anchor.
                self._quick_property_names[resource_type] = None
            else:
                self._quick_property_names[resource_type] = frozenset(names)
        return self._quick_property_names[resource_type]
```

**Step 6 — add the `validate` branch.** Replace the signature and opening of `get_quick_property`
(currently `src/hmc_mcp/client/core.py:692`) so it reads:

```python
    async def get_quick_property(
        self,
        resource_type: str,
        uuid: str,
        property_name: str,
        *,
        validate: bool = False,
    ) -> str | None:
        """GET a quick property, e.g. LogicalPartition/{uuid}/quick/PartitionState.

        quick/ endpoints return a plain-text value and require Accept: */* --
        a typed uom+xml Accept header causes HTTP 406.

        With *validate* the name is checked against the ones the type defines
        before anything is sent, raising ``ValueError`` on a name the HMC does
        not know. The names come from ``list_quick_properties`` and are cached
        for this client's lifetime, so validating costs at most one extra
        request per resource type per session. It is off by default: the client
        is constructed per tool call, so the cache would rarely be reused and
        every call would pay that request (ADR 0141). A level where the
        discovery read itself fails validates nothing rather than raising.
        """
        if validate:
            defined = await self._defined_quick_property_names(resource_type)
            if defined is not None and property_name not in defined:
                raise ValueError(
                    f"{resource_type} defines no quick property named "
                    f"{property_name!r}. Defined names: {', '.join(sorted(defined))}."
                )
        path = f"/rest/api/uom/{resource_type}/{uuid}/quick/{property_name}"
```

Leave the rest of the method body — the `_request_with_uuid_path_arguments` call, the 204 branch,
the status check and the quote-stripping — exactly as it is.

**Step 7 — confirm the green.** Run, bare:

```sh
uv run --no-sync pytest tests/unit/test_client.py -k get_quick_property -q
```

Expect every test in the block to pass and the pre-existing `get_quick_property` tests in that
module to keep passing.

**Step 8 — add the CHANGELOG entry.** In `CHANGELOG.md`, under `## [Unreleased]` / `### Added`,
insert this as the first bullet, above the `list_quick_properties` entry:

```markdown
- `HMCClient.get_quick_property` takes a keyword-only `validate=False`. With `validate=True` the
  property name is checked against the names `list_quick_properties` reports for the resource type
  and an unknown one raises `ValueError` before any request is sent, instead of round-tripping to
  the HMC. The names are read once per resource type and cached for the client's lifetime, so
  validating costs at most one extra request per type per session; a fresh client reads them again
  and nothing is invalidated within a session. A firmware level where the discovery read fails
  validates nothing rather than raising, so `get_quick_property` keeps working there. The default
  is unchanged behaviour, deliberately: the client is constructed per tool call, so default-on
  would add that request to nearly every call (ADR 0141, #799).
```

**Step 9 — run the guardrails, bare.**

```sh
just lint
just typecheck
just test
just adr-numbering
just doc-freshness
```

Each must exit 0. `just adr-numbering` is what proves ADR 0141's filename, number uniqueness and
H1 agree.

**Acceptance criteria.**

- `HMCClient.get_quick_property` has a keyword-only `validate` parameter defaulting to `False`, and
  calling it without that argument sends exactly the request it sent at `fac7c19e`.
- `validate=True` with an undefined name raises `ValueError` naming the type, the rejected name and
  the defined names, and no request reaches the `quick/{name}` path.
- Two validated calls for one resource type on one client produce exactly one request to
  `/rest/api/uom/{R}/quick`, whether that request succeeded or failed.
- A 4xx or 5xx from the discovery anchor leaves `get_quick_property` returning the HMC's value.
- `src/hmc_mcp/client/core.py`, `tests/unit/test_client.py` and `CHANGELOG.md` are the only files
  this task changed.
- `just lint`, `just typecheck`, `just test`, `just adr-numbering` and `just doc-freshness` all
  exit 0.

**Rollback.** The change is three additive edits in one module plus one test block and one
CHANGELOG bullet; reverting the commit restores `fac7c19e` behaviour exactly, and nothing persists
state outside the process.

## Deferrals carried into implementation

None. No `$trial-loop` or design-review finding on this branch has been dispositioned as
`deferred-tracked`. If the design review adds one, record it here with its owning record path or
tracker issue before `$forge` reads this plan.
