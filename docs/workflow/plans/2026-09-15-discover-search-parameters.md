# Discover valid search parameters via /search — implementation plan

**Goal.** Give `HMCClient` a reader for the HMC's type-anchored `/search` discovery anchor, and wire
it into `search_uom` as an opt-in pre-flight check so an unsupported search property fails locally
instead of as an HTTP 400 from the HMC.

**Architecture.** Everything lands in one module, `src/hmc_mcp/client/core.py`, which already owns
`search_uom` and both sibling discovery reads. `list_search_parameters` performs the read and parse.
A private `_defined_search_parameter_names` caches the root-anchor answer per resource type on the
client instance, behind an `asyncio.Lock`, and degrades to "unknown" whenever the read yields no
usable names. `search_uom` consults it only when its new keyword-only `validate` argument is true.

**Tech stack.** Python 3.11+, `httpx` transport, `pytest` + `pytest-asyncio` + `respx`, `uv`, `just`.

Design: [spec](../specs/2026-09-15-discover-search-parameters-design.md),
[ADR 0142](../../adr/0142-search-parameter-discovery-parses-one-named-element.md).

Expected implementation size: 600–720 changed lines (M) — derived from the file map below:
`core.py` gains one public method with a long docstring, one private helper and four `__init__`
lines (~180); `tests/unit/test_client.py` gains two block-head provenance comments, two constructed-
body helpers and seventeen test functions, at this repository's observed density of ~26 test lines
per function (~500); `CHANGELOG.md` gains one entry (~16).

**This line was corrected after the build, and the reason is recorded rather than hidden.** It first
read 310–415, taken from the twin PR #805's 230 test lines without scaling for a test count nearly
double that branch's nine. The plan's task coverage was not wrong — every implemented line traces
to a step below — only the arithmetic was, so no implementation was cut to fit the number, which
forge's estimate rule forbids. The actual diff is 180 + 499 + 16 = 695 changed lines. The frozen
design denominator of 250 is unaffected: it is derived from the validated complexity, never from
this estimate, and this line has never been an input to it.

## Global Constraints

Transcribed from `AGENTS.md` and the design. These bind the task; it does not repeat them.

- **Never run a bare `uv sync`, `uv run`, or `uv add`** — a bare `uv sync` prunes the `app` extra
  and breaks `just typecheck` far from the cause. Bootstrap with `just setup`; run ad-hoc commands
  as `uv run --no-sync …`.
- **Guardrails.** `just verify` is the full pre-push suite. Narrow a failure with `just lint`,
  `just typecheck`, `just adr-numbering`, `just doc-freshness`. CI additionally runs
  `uv run --no-sync prek run --all-files` after `just verify`; run it before pushing.
- **Diff against the merge base:** `git --no-pager diff "$(git merge-base HEAD origin/main)"`.
- **Never assert on prose wording in a test.** A test that greps a docstring or a Markdown file for
  a phrase is forbidden; such contracts are recorded `task-test-not-applicable` instead.
- **Fixture provenance is labelled at the block head**, stating whether each body is captured or
  constructed and from what.
- **Tests build config through `make_config()`** (`tests/conftest.py:395`), never `HMCConfig`
  directly: `HMCConfig` reads `HMC_*` from the ambient environment.
- **Conventional commits**, imperative, ≤72-character subject.

## File map

| Path | Owns now | Owns after |
|---|---|---|
| `src/hmc_mcp/client/core.py` | `search_uom`, `list_operations`, `list_quick_properties`, `_defined_quick_property_names`, per-client caches | the same, plus `_SEARCH_PARAMETER_NAME_ELEMENT`, `list_search_parameters`, `_defined_search_parameter_names`, and `search_uom`'s `validate` branch |
| `tests/unit/test_client.py` | the `list_quick_properties` and `get_quick_property` blocks | the same, plus a `list_search_parameters` and `search_uom` validation block |
| `CHANGELOG.md` | released and unreleased entries | plus one `### Added` entry naming both additions |

Created in the design phase and unchanged by this task:
`docs/adr/0142-search-parameter-discovery-parses-one-named-element.md`,
`docs/workflow/specs/2026-09-15-discover-search-parameters-design.md`, and this plan.

Unchanged, and checked rather than edited: `src/hmc_mcp/client/client_contracts.py` (its two
`search_uom` declarations at lines 89 and 319 stay satisfied by a wider implementation);
`src/hmc_mcp/api.py` and `tests/unit/test_public_api.py` (ADR 0118's six exports); the six
production `search_uom` call sites.

---

## Task 1 — read the `/search` anchors and wire them into `search_uom`

**One task, not two.** The reader and its only caller ship together: `list_search_parameters` alone
would land an unwired discovery read, which is the state ADR 0140 left behind and #799 existed to
fix. No reviewer should accept the reader here without the wiring, so there is no verdict boundary
between them.

**Files.** Modifies `src/hmc_mcp/client/core.py` and `CHANGELOG.md`. Tests in
`tests/unit/test_client.py`.

**Interfaces.** Consumed, all present at `a0d29ac7`:

- `_find_all_text`, imported at `core.py:35`, defined at `client_parse.py:49` as
  `_tag_parse_errors(find_all_text)`. Effective signature
  `_find_all_text(xml_text: str, context: str, *names: str) -> list[str]`: `context` is the second
  positional argument, and the wrapper converts a parse failure into `HMCError` naming it. A
  matching element with no text contributes an empty string (`xmlutil.py:308-315`), which is why
  the comprehension below filters falsy values.
- `HMCClient._request_with_uuid_path_arguments(self, method: str, path: str, *, uuid_path_arguments: Mapping[str, str], **kwargs: Any) -> httpx.Response`
  (`core.py:462-469`). `headers=` reaches the transport through `**kwargs`.
- `HMCError(message: str, status_code: int | None = None, body: str | None = None)` from `..errors`
  (`errors.py:24-26`) — third parameter named `body`, passed positionally here as
  `list_quick_properties` does. `HMCTransportError` subclasses it (`errors.py:47`).
- `asyncio` (`core.py:12`), `quote` (`core.py:18`), `_parse_feed` (`core.py:35`).
- `HMCClient.__init__`, which already builds `self._quick_property_names` and its lock at
  `core.py:255,260`.
- In tests: `make_config()` (`tests/conftest.py:395`) and `_PARENT_UUID`
  (`tests/unit/test_client.py:2173`).

Produced:

- `_SEARCH_PARAMETER_NAME_ELEMENT: str`
- `async def list_search_parameters(self, resource_type: str, *, parent_type: str | None = None, parent_uuid: str | None = None) -> tuple[list[str], str | None]`
- `async def _defined_search_parameter_names(self, resource_type: str) -> frozenset[str] | None`
- `search_uom`'s keyword-only `validate: bool = False`

**Verification.** Every entry's green command is
`uv run --no-sync pytest tests/unit/test_client.py -k "search_uom or list_search_parameters" --no-cov -q`,
and every test is in `tests/unit/test_client.py`. Seventeen test functions are named below: eight
covering `list_search_parameters` and nine covering `search_uom`'s pre-flight. The table has twenty
rows because two contracts share `::test_list_search_parameters_reads_both_anchors` and two carry no
test. The red in each row
is the one that appears **after** the name under test exists but its behaviour does not — which is
the observation worth confirming. Before the name exists at all, a
`list_search_parameters` test fails with
`AttributeError: 'HMCClient' object has no attribute 'list_search_parameters'` and a `search_uom`
pre-flight test with `TypeError: search_uom() got an unexpected keyword argument 'validate'`.

| Contract | Mode | Test, and the red it shows once the name exists |
|---|---|---|
| Both anchors are reached at the documented paths | `focused-test` | `::test_list_search_parameters_reads_both_anchors`, parametrized root/child — `respx` raises `AllMockedAssertionError` for the unrouted path |
| `Accept: */*` is sent on both anchors, exactly | `focused-test` | `::test_list_search_parameters_reads_both_anchors`, same parametrization — a typed uom Accept, or any other value, fails the equality assertion |
| Exactly one parent argument is a caller error | `focused-test` | `::test_list_search_parameters_refuses_bad_arguments`, parametrized over each half — `Failed: DID NOT RAISE <class 'ValueError'>` |
| Names come from the named element, not its siblings | `focused-test` | `::test_list_search_parameters_reads_the_named_element_not_its_siblings` — a parse reading `Description` returns the description strings and the name assertion fails |
| A single parameter returns as a one-element list | `focused-test` | `::test_list_search_parameters_returns_a_single_name_as_a_one_element_list` — a `_parse_feed` parse collapses it to a bare value and the list assertion fails |
| `X-HMC-Schema-Version` is returned verbatim, `None` when absent | `focused-test` | `::test_list_search_parameters_returns_the_response_schema_version` — a names-only return gives `ValueError: too many values to unpack` |
| A 204 returns no names rather than raising | `focused-test` | `::test_list_search_parameters_204_returns_no_names` — an implementation raising on any non-200 gives `HMCError` |
| A 200 yielding no name raises, rather than reporting "defines nothing" | `focused-test` | `::test_list_search_parameters_200_without_a_name_raises`, parametrized over an `HttpErrorResponse` feed and an empty collection — `Failed: DID NOT RAISE` |
| An unknown resource type surfaces `HMCError` carrying the status | `focused-test` | `::test_list_search_parameters_unknown_type_raises_hmc_error_with_status` — `Failed: DID NOT RAISE`, or a raised error whose `status_code` is `None` |
| `validate=True` refuses an undefined property before transport | `focused-test` | `::test_search_uom_validate_refuses_an_undefined_property` — `Failed: DID NOT RAISE`, and the instance-search route records 1 call, not 0 |
| `validate=True` passes a defined property through | `focused-test` | `::test_search_uom_validate_allows_a_defined_property` — a wrongly-inverted check raises `ValueError` |
| Names are read at most once per type per client | `focused-test` | `::test_search_uom_validate_reads_the_names_once_per_type` — without the cache, `assert 3 == 1` |
| The bound holds under concurrency | `focused-test` | `::test_search_uom_validate_reads_the_names_once_under_concurrency`, five calls through `asyncio.gather` — without the lock, `assert 5 == 1`; asserted on `call_count` after `gather`, never on timing |
| The cache is keyed per resource type | `focused-test` | `::test_search_uom_validate_caches_per_resource_type` — a single-slot cache leaves the second type's route at 0 calls and rejects its defined name |
| Nothing is shared between clients | `focused-test` | `::test_search_uom_validate_rereads_for_a_new_client` — a class-level cache gives `assert 1 == 2` |
| A read yielding no names degrades, and is cached rather than retried | `focused-test` | `::test_search_uom_validate_degrades_and_caches_the_failure`, parametrized over a 500, a 400, `httpx.ConnectError` and a 204 — a propagating error gives `HMCError` instead of a result; a retry gives `assert 2 == 1` |
| The default makes no discovery request | `focused-test` | `::test_search_uom_defaults_to_no_validation` — a default-on implementation leaves the discovery route at 1 call, not 0 |
| `validate` is keyword-only, default `False` | `focused-test` | `::test_search_uom_validate_is_keyword_only_and_defaults_false`, via `inspect.signature` — a positional parameter fails the `kind` assertion |
| The HTTP 400 rationale in `search_uom`'s docstring | `task-test-not-applicable` | Prose addressed to a human reader; no executable consumer validates it, and asserting its wording would snapshot prose, which the Global Constraints forbid |
| The `CHANGELOG.md` entry | `task-test-not-applicable` | `tests/unit/test_changelog.py` binds only the declared `pyproject.toml` version, which this change does not alter; no executable consumer validates an unreleased entry |
| ADR 0142 is a well-formed numbered record | `focused-test` | `just adr-numbering`, which checks filename, unique number and H1 agreement; it is already green and must stay so |

**Steps.**

1. In `tests/unit/test_client.py`, below the existing `get_quick_property` validation block, add the
   new block's head comment. It must state that every body below is **constructed, not captured**;
   that the reference corpus carries the path grammar at
   `docs/refs/hmc-rest-api-p10/000-hmc-rest-apis.md:67-68` and its P11 equivalent and nothing else;
   that `Nickname` is inferred from the sibling `/quick` anchor per ADR 0142; and that the closing
   evidence is a live capture from the operator's ppc64le host.

2. Below it, add the shared names and the constructed-body helper. The `RESTElement` and
   `Description` siblings are deliberate: a fixture carrying only the element under test cannot
   detect a parse reading the wrong neighbour.

   ```python
   _MANAGED_SYSTEM_SEARCH_PARAMETERS = ["SystemName", "State", "MachineType", "SerialNumber"]
   _LOGICAL_PARTITION_SEARCH_PARAMETERS = ["PartitionName", "PartitionID", "PartitionState"]


   def _search_parameter_entry(rest_element: str, *parameters: str | tuple[str, str]) -> str:
       """A CONSTRUCTED <entry> for a /search discovery anchor.

       Not a capture: no firmware has been observed answering this anchor. The
       shape mirrors the /quick collection ADR 0140 captured, which is the
       inference ADR 0142 records and the live round is expected to correct.

       Each entry in *parameters* is a name, or a (name, description) pair when
       the test cares about the Description sibling.
       """
       body = ""
       for item in parameters:
           name, description = item if isinstance(item, tuple) else (item, f"About {item}.")
           body += (
               "<SearchParameter>"
               "<Metadata><Atom/></Metadata>"
               f"<RESTElement>{rest_element}</RESTElement>"
               f"<Nickname>{name}</Nickname>"
               f"<Description>{description}</Description>"
               "</SearchParameter>"
           )
       return (
           '<entry xmlns="http://www.w3.org/2005/Atom">'
           "<id>00000000-0000-0000-0000-000000000000</id>"
           "<title>SearchParameterCollection</title>"
           "<author><name>IBM Power Systems Management Console</name></author>"
           "<content>"
           '<SearchParameter_Collection xmlns="http://www.ibm.com/xmlns/systems/power'
           '/firmware/uom/mc/2012_10/">'
           "<Metadata><Atom/></Metadata>"
           f"{body}"
           "</SearchParameter_Collection>"
           "</content></entry>"
       )
   ```

3. Write `test_list_search_parameters_reads_both_anchors`, parametrized as
   `test_list_quick_properties_reads_the_live_anchors` is, over
   `("ManagedSystem", {}, "/rest/api/uom/ManagedSystem/search", _MANAGED_SYSTEM_SEARCH_PARAMETERS)`
   and `("LogicalPartition", {"parent_type": "ManagedSystem", "parent_uuid": _PARENT_UUID},
   f"/rest/api/uom/ManagedSystem/{_PARENT_UUID}/LogicalPartition/search",
   _LOGICAL_PARTITION_SEARCH_PARAMETERS)`. Assert the request path, the returned names, and that the
   request's `Accept` header equals `*/*` exactly. Asserting only that it is *not* a typed uom
   media type would pass for any other wrong value, which is not the contract.

4. Run the Verification green command. Expect
   `AttributeError: 'HMCClient' object has no attribute 'list_search_parameters'`.

5. In `src/hmc_mcp/client/core.py`, beside the other module-level constants, add:

   ```python
   # The element whose text holds a search-parameter name. INFERRED from the
   # sibling /quick anchor, never captured from firmware -- ADR 0142 records why
   # and bounds it. This constant is the single point of change when a live
   # round settles the shape.
   _SEARCH_PARAMETER_NAME_ELEMENT = "Nickname"
   ```

6. In `core.py`, immediately after `search_uom`, add `list_search_parameters`:

   ```python
   async def list_search_parameters(
       self,
       resource_type: str,
       *,
       parent_type: str | None = None,
       parent_uuid: str | None = None,
   ) -> tuple[list[str], str | None]:
       uuid_path_arguments: dict[str, str] = {}
       if parent_type is not None and parent_uuid is not None:
           path = f"/rest/api/uom/{parent_type}/{parent_uuid}/{resource_type}/search"
           uuid_path_arguments["parent_uuid"] = parent_uuid
       elif parent_type is None and parent_uuid is None:
           path = f"/rest/api/uom/{resource_type}/search"
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
       names = [
           n
           for n in _find_all_text(
               resp.text, f"GET {path}", _SEARCH_PARAMETER_NAME_ELEMENT
           )
           if n
       ]
       if not names:
           raise HMCError(
               f"GET {path} returned no {_SEARCH_PARAMETER_NAME_ELEMENT} element; "
               "expected the search-parameter names the type defines",
               resp.status_code,
               resp.text,
           )
       return names, schema_version
   ```

7. Write its docstring above that body, stating: the two anchors, and that supplying exactly one
   parent argument is a caller error; that the second return value is `X-HMC-Schema-Version`
   verbatim and is **not** guaranteed to hold a version, because ADR 0139 and ADR 0140 both record
   the HMC echoing `X-Audit-Memento` into it; that `Accept: */*` is sent because this anchor's
   content type is unobserved; that a type not serving the anchor asked for surfaces as `HMCError`
   carrying that status, with ADR 0139's observation that an unrecognised type answers 400
   `INVALID_URL` rather than 404; that a 200 yielding no name raises rather than reporting an empty
   set, because the HMC is known to answer 200 with an `HttpErrorResponse` feed; and — explicitly —
   that the parsed element name is an inference recorded in ADR 0142, not yet confirmed against
   firmware.

8. Re-run the green command; expect the two `reads_both_anchors` cases to pass. Then write the
   remaining seven `list_search_parameters` tests from the Verification table, re-running after each
   and confirming the stated red first where the behaviour is not yet present.

9. In `core.py`'s `__init__`, directly below the existing quick-property cache lines, add:

   ```python
   # Search-parameter names per resource type, on the same terms as the
   # quick-property cache above: read once, kept for this client's lifetime,
   # None meaning a discovery read that yielded no names (ADR 0142).
   self._search_parameter_names: dict[str, frozenset[str] | None] = {}
   self._search_parameter_names_lock = asyncio.Lock()
   ```

10. In `core.py`, immediately after `list_search_parameters`, add the read-through helper:

    ```python
    async def _defined_search_parameter_names(
        self, resource_type: str
    ) -> frozenset[str] | None:
        if resource_type in self._search_parameter_names:
            return self._search_parameter_names[resource_type]
        async with self._search_parameter_names_lock:
            # Re-check under the lock: a task that waited here may have been
            # waiting on the very read that populates this entry, and the cost
            # bound is per type per client, not per caller.
            if resource_type in self._search_parameter_names:
                return self._search_parameter_names[resource_type]
            try:
                names, _ = await self.list_search_parameters(resource_type)
            except HMCError:
                # Covers HMCTransportError too, which subclasses it. ADR 0142
                # degrades rather than raising so a level that does not serve
                # the anchor -- or a wrong parsed element -- cannot break
                # search_uom, which an opt-in pre-flight must not do.
                names = []
            # An empty answer is "unknown", never "defines nothing": a 204
            # returns ([], version) without raising, and an empty positive set
            # would reject every property for this client's lifetime.
            defined = frozenset(names) if names else None
            self._search_parameter_names[resource_type] = defined
            return defined
    ```

11. Add the parameter and the pre-flight branch to `search_uom`, leaving the rest of its body
    unchanged:

    ```python
    async def search_uom(
        self,
        resource_type: str,
        property_name: str,
        property_value: str,
        *,
        validate: bool = False,
    ) -> list[dict[str, Any]]:
        if validate:
            defined = await self._defined_search_parameter_names(resource_type)
            if defined is not None and property_name not in defined:
                raise ValueError(
                    f"{resource_type} defines no search parameter named "
                    f"{property_name!r}. Defined names: {', '.join(sorted(defined))}."
                )
        encoded_property = quote(property_name, safe="")
        encoded_value = quote(property_value, safe="")
        path = (
            f"/rest/api/uom/{resource_type}/search/"
            f"({encoded_property}=={encoded_value})"
        )
        xml = await self._get(path, resource_type)
        if not xml:
            return []
        return _parse_feed(xml, path)
    ```

12. Replace `search_uom`'s one-line docstring with a full one, stating: the path it builds; that the
    HMC answers an unsupported search property with **HTTP 400**, which is why the pre-flight exists
    (#789 criterion 3); that `validate` checks the name against
    `list_search_parameters(resource_type)` before anything is sent and raises `ValueError`; that
    names are cached for this client's lifetime, so validating costs at most one extra request per
    resource type per session; that it is off by default because the client is constructed per tool
    call, so the cache would rarely be reused (ADR 0142); that a level where the discovery read
    fails validates nothing rather than raising; and that a transport failure is cached as durably
    as a firmware-level one, so a transient one leaves validation off for that type until a new
    client is constructed.

13. Write the remaining eight `search_uom` tests from the Verification table, re-running the green
    command after each. The degradation case parametrizes the discovery route over a 500, a 400,
    `httpx.ConnectError` and a 204, then asserts both validated calls return the instance-search
    result and the discovery route recorded exactly one call.

14. Add one entry to `CHANGELOG.md` under `## [Unreleased]` / `### Added`, naming
    `HMCClient.list_search_parameters` and `search_uom`'s `validate` parameter, and stating that the
    parsed response shape is not yet confirmed against firmware.

15. Run `just lint`, `just typecheck`, then `just verify`; each exits 0, and `just verify` prints a
    compact success summary with the coverage gate passing. Then run
    `uv run --no-sync prek run --all-files`; it exits 0 with every hook `Passed` or `Skipped`.

16. Commit: `feat(client): discover search parameters and pre-flight search_uom`.

**Acceptance.** The Verification green command reports 0 failed. `just verify` and
`uv run --no-sync prek run --all-files` both exit 0. `git --no-pager diff --name-only "$(git
merge-base HEAD origin/main)"` lists exactly `CHANGELOG.md`, the ADR, the plan, the spec,
`src/hmc_mcp/client/core.py` and `tests/unit/test_client.py` — `client_contracts.py` in particular
must not appear.

**Cleanup.** None: no temporary file, fixture directory or generated artifact is created. If
`just doc-freshness` reddens, the fix is to run the recipe the failing document names and commit its
output, never to hand-edit the document.
