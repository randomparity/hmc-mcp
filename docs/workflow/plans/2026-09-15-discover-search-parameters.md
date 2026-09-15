# Discover valid search parameters via /search — implementation plan

**Goal.** Give `HMCClient` a reader for the HMC's type-anchored `/search` discovery anchor, and wire
it into `search_uom` as an opt-in pre-flight check so an unsupported search property fails locally
instead of as an HTTP 400 from the HMC.

**Architecture.** Everything lands in one module, `src/hmc_mcp/client/core.py`, which already owns
`search_uom` and both sibling discovery reads. A new `list_search_parameters` performs the read and
parse. A private `_defined_search_parameter_names` caches the root-anchor answer per resource type
on the client instance, behind an `asyncio.Lock`, and degrades to "unknown" whenever the read yields
no usable names. `search_uom` consults that helper only when its new keyword-only `validate`
argument is true.

**Tech stack.** Python 3.11+, `httpx` for transport, `pytest` + `pytest-asyncio` + `respx` for
tests, `uv` for the environment, `just` for guardrails.

Design: [spec](../specs/2026-09-15-discover-search-parameters-design.md),
[ADR 0142](../../adr/0142-search-parameter-discovery-parses-one-named-element.md).

Expected implementation size: 310–415 changed lines (M) — derived from the file map below: `core.py`
gains one public method with a long docstring, one private helper and two `__init__` lines;
`tests/unit/test_client.py` gains one fixture block and sixteen test functions; `CHANGELOG.md` gains
one entry.

## Global Constraints

Transcribed from `AGENTS.md` and the design. These bind every task; no task repeats them.

- **Never run a bare `uv sync`, `uv run`, or `uv add`.** Use `just setup` to bootstrap, and
  `uv run --no-sync …` for anything ad hoc. A bare `uv sync` prunes the `app` extra and breaks
  `just typecheck` far from the cause.
- **Guardrails.** `just verify` is the full pre-push suite (`static test smoke build
  verify-artifacts`). Narrow a `static` failure with the sub-recipe: `just lint`, `just typecheck`,
  `just secrets`, `just adr-numbering`, `just doc-freshness`. CI additionally runs
  `uv run --no-sync prek run --all-files` after `just verify`; run it locally before pushing.
- **Diff against the merge base, never against `main`:**
  `git --no-pager diff "$(git merge-base HEAD origin/main)"`.
- **No ADR index exists.** `just adr-numbering` checks that an ADR's filename matches
  `NNNN-lowercase-kebab-slug.md` and that its H1 announces the same number. No index row is owed.
- **Conventional commits**, imperative, ≤72-character subject, one logical change each.
- **Never assert on prose wording in a test.** A test that greps a docstring or a Markdown file for
  a phrase is forbidden; the spec's Validation table records such contracts as
  `task-test-not-applicable` instead.
- **Fixture provenance is labelled at the block head.** Every body added by this plan is
  **constructed**, not captured, and the block head must say so and say from what.
- **`HMCConfig` reads `HMC_*` from the ambient environment.** Tests use the existing
  `make_config()` helper from `tests/conftest.py`; do not construct `HMCConfig` directly.

## File map

| Path | Owns now | Owns after |
|---|---|---|
| `src/hmc_mcp/client/core.py` | `search_uom`, `list_operations`, `list_quick_properties`, `_defined_quick_property_names`, per-client caches | the same, plus `_SEARCH_PARAMETER_NAME_ELEMENT`, `list_search_parameters`, `_defined_search_parameter_names`, and `search_uom`'s `validate` branch |
| `tests/unit/test_client.py` | the `list_quick_properties` and `get_quick_property` blocks | the same, plus a `list_search_parameters` block and a `search_uom` validation block |
| `docs/adr/0142-search-parameter-discovery-parses-one-named-element.md` | — (created in the design phase) | unchanged by these tasks |
| `docs/workflow/specs/2026-09-15-discover-search-parameters-design.md` | — (created in the design phase) | unchanged by these tasks |
| `CHANGELOG.md` | released and unreleased entries | plus one `### Added` entry naming both additions |

Unchanged, and checked rather than edited: `src/hmc_mcp/client/client_contracts.py` (its two
`search_uom` declarations at lines 89 and 319 stay satisfied by a wider implementation);
`src/hmc_mcp/api.py` and `tests/unit/test_public_api.py` (ADR 0118's six exports); the six
production `search_uom` call sites.

---

## Task 1 — `list_search_parameters` reads both `/search` discovery anchors

Creates the discovery read. Nothing else in the plan works without it.

**Files.** Modifies `src/hmc_mcp/client/core.py`. Tests in `tests/unit/test_client.py`.

**Interfaces.**

Consumes, all already present in `core.py` — confirmed at `a0d29ac7`:

- `from .client_parse import _find_all_text` (imported at `core.py:35`; defined at
  `client_parse.py:49` as `_tag_parse_errors(find_all_text)`), so the effective signature is
  `_find_all_text(xml_text: str, context: str, *names: str) -> list[str]` — the `context` argument
  is the second positional one, and the wrapper converts a parse failure into `HMCError` naming it.
  A matching element with no text contributes an empty string (`xmlutil.py:308-315`), which is why
  the comprehension below filters falsy values.
- `HMCClient._request_with_uuid_path_arguments(self, method: str, path: str, *, uuid_path_arguments: Mapping[str, str], **kwargs: Any) -> httpx.Response`
  (`core.py:462-469`). `headers=` reaches the transport through `**kwargs`.
- `HMCError(message: str, status_code: int | None = None, body: str | None = None)`
  from `..errors` (`errors.py:24-26`). The third parameter is named `body`; the plan passes it
  positionally, as `list_quick_properties` does.

Provides to Task 2:

- `_SEARCH_PARAMETER_NAME_ELEMENT: str`
- `async def list_search_parameters(self, resource_type: str, *, parent_type: str | None = None, parent_uuid: str | None = None) -> tuple[list[str], str | None]`

**Verification.**

- Contract: both anchors are reached at the documented paths. `Mode: focused-test`. Observable: the
  request path `respx` sees. Test: `tests/unit/test_client.py::test_list_search_parameters_reads_both_anchors`,
  parametrized over the root and child forms. Expected red before the method exists:
  `AttributeError: 'HMCClient' object has no attribute 'list_search_parameters'`. Green:
  `uv run --no-sync pytest tests/unit/test_client.py -k list_search_parameters --no-cov -q`.
- Contract: exactly one parent argument is a caller error. `Mode: focused-test`. Test:
  `::test_list_search_parameters_refuses_bad_arguments`, parametrized over each half. Expected red:
  `AttributeError` as above; after a naive implementation, `Failed: DID NOT RAISE <class 'ValueError'>`.
- Contract: the names come from `_SEARCH_PARAMETER_NAME_ELEMENT` and not its siblings.
  `Mode: focused-test`. Test: `::test_list_search_parameters_reads_the_named_element_not_its_siblings`.
  Expected red on a parse reading `Description`: the returned list holds the description strings and
  the assertion on the name list fails.
- Contract: a single defined parameter returns as a one-element list. `Mode: focused-test`. Test:
  `::test_list_search_parameters_returns_a_single_name_as_a_one_element_list`. Expected red on a
  `_parse_feed`-based parse: the single name collapses to a bare value and the list assertion fails.
- Contract: `X-HMC-Schema-Version` is returned verbatim. `Mode: focused-test`. Test:
  `::test_list_search_parameters_returns_the_response_schema_version`. Expected red on an
  implementation returning only names: `ValueError: too many values to unpack`.
- Contract: a 204 returns no names rather than raising. `Mode: focused-test`. Test:
  `::test_list_search_parameters_204_returns_no_names`. Expected red on an implementation that
  raises for any non-200: `HMCError`.
- Contract: a 200 yielding no name raises rather than reporting "defines nothing".
  `Mode: focused-test`. Test: `::test_list_search_parameters_200_without_a_name_raises`,
  parametrized over an `HttpErrorResponse` feed and an empty collection. Expected red on an
  implementation returning `[]`: `Failed: DID NOT RAISE`.
- Contract: an unknown resource type surfaces `HMCError` carrying the HMC's status.
  `Mode: focused-test`. Test: `::test_list_search_parameters_unknown_type_raises_hmc_error_with_status`.
  Expected red: `Failed: DID NOT RAISE`, or a raised error whose `status_code` is `None`.

**Steps.**

1. In `tests/unit/test_client.py`, after the existing `get_quick_property` validation block, add the
   new block's head comment. It must state that every body below is **constructed, not captured**;
   that the reference corpus carries the path grammar at
   `docs/refs/hmc-rest-api-p10/000-hmc-rest-apis.md:67-68` and the P11 equivalent and nothing else;
   that `Nickname` is inferred from the sibling `/quick` anchor per ADR 0142; and that the closing
   evidence is a live capture from the operator's ppc64le host.
2. Below the comment, add the constructed-body helper. It mirrors `_quick_property_entry`
   (`tests/unit/test_client.py`, in the `list_quick_properties` block) in structure, and carries the
   `RESTElement` and `Description` siblings deliberately so a parse reading the wrong neighbour is
   detectable:

   ```python
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

3. Add the two name lists the tests share, above the helper:

   ```python
   _MANAGED_SYSTEM_SEARCH_PARAMETERS = ["SystemName", "State", "MachineType", "SerialNumber"]
   _LOGICAL_PARTITION_SEARCH_PARAMETERS = ["PartitionName", "PartitionID", "PartitionState"]
   ```

4. Write `test_list_search_parameters_reads_both_anchors`, parametrized exactly as
   `test_list_quick_properties_reads_the_live_anchors` is, over
   `("ManagedSystem", {}, "/rest/api/uom/ManagedSystem/search", _MANAGED_SYSTEM_SEARCH_PARAMETERS)`
   and
   `("LogicalPartition", {"parent_type": "ManagedSystem", "parent_uuid": _PARENT_UUID},
   f"/rest/api/uom/ManagedSystem/{_PARENT_UUID}/LogicalPartition/search",
   _LOGICAL_PARTITION_SEARCH_PARAMETERS)`. Assert the request path and the returned names, and
   assert the request's `Accept` header is not a typed uom media type.
5. Run `uv run --no-sync pytest tests/unit/test_client.py -k list_search_parameters --no-cov -q`.
   Expect `AttributeError: 'HMCClient' object has no attribute 'list_search_parameters'`.
6. In `src/hmc_mcp/client/core.py`, beside the other module-level constants, add:

   ```python
   # The element whose text holds a search-parameter name. INFERRED from the
   # sibling /quick anchor, never captured from firmware -- ADR 0142 records why
   # and bounds it. This constant is the single point of change when a live
   # round settles the shape.
   _SEARCH_PARAMETER_NAME_ELEMENT = "Nickname"
   ```

7. In `core.py`, immediately after `search_uom`, add `list_search_parameters`. Its body mirrors
   `list_quick_properties`:

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

8. Write its docstring above the body. It must state: which two anchors it reads and that supplying
   exactly one parent argument is a caller error; that the returned second element is
   `X-HMC-Schema-Version` verbatim and is not guaranteed to hold a version, because ADR 0139 and
   ADR 0140 both record the HMC echoing `X-Audit-Memento` into it; that `Accept: */*` is sent
   because this anchor's content type is unobserved; that a type not serving the anchor asked for
   surfaces as `HMCError` carrying that status, with ADR 0139's observation that an unrecognised
   type answers 400 `INVALID_URL` rather than 404; that a 200 yielding no name raises rather than
   reporting an empty set, because the HMC is known to answer 200 with an `HttpErrorResponse` feed;
   and — explicitly — that the parsed element name is an inference recorded in ADR 0142 and not yet
   confirmed against firmware.
9. Re-run the step 5 command. Expect the two `reads_both_anchors` cases to pass.
10. Write the remaining seven tests named in **Verification**, running the step 5 command after
    each and confirming the stated red before the behaviour exists where the step order allows it.
    For `refuses_bad_arguments`, parametrize over `{"parent_type": "ManagedSystem"}` and
    `{"parent_uuid": _PARENT_UUID}` and assert `pytest.raises(ValueError)`. For
    `reads_the_named_element_not_its_siblings`, build the body with `(name, description)` pairs
    whose descriptions are themselves plausible parameter-looking strings, and assert the result
    equals the names and contains none of the descriptions. For
    `returns_a_single_name_as_a_one_element_list`, pass one parameter and assert
    `names == ["PartitionName"]`. For `returns_the_response_schema_version`, return the header
    `X-HMC-Schema-Version: V1_0` and assert the second element equals `"V1_0"`, plus a case with the
    header absent asserting `None`. For `204_returns_no_names`, assert
    `await hmc.list_search_parameters("ManagedSystem") == ([], "V1_0")`. For
    `200_without_a_name_raises`, parametrize over `_search_parameter_entry("ManagedSystem")` — an
    empty collection — and an `HttpErrorResponse`-shaped feed carrying no `Nickname`, and assert
    `HMCError`. For `unknown_type_raises_hmc_error_with_status`, return a 400 and assert the raised
    `HMCError` has `status_code == 400`.
11. Run `just lint` and `just typecheck`. Both exit 0 with no output on success.
12. Commit: `feat(client): read the /search discovery anchors`.

**Acceptance.** `uv run --no-sync pytest tests/unit/test_client.py -k list_search_parameters
--no-cov -q` reports 9 or more passed and 0 failed. `just lint` and `just typecheck` exit 0.
`src/hmc_mcp/client/client_contracts.py` is unmodified.

---

## Task 2 — `search_uom` gains an opt-in pre-flight check

Wires Task 1's read into `search_uom`. Independently reviewable: Task 1 could ship alone as an
unused reader, and this task is what gives it a caller.

**Files.** Modifies `src/hmc_mcp/client/core.py` and `CHANGELOG.md`. Tests in
`tests/unit/test_client.py`.

**Interfaces.**

Consumes from Task 1:

- `async def list_search_parameters(self, resource_type: str, *, parent_type: str | None = None, parent_uuid: str | None = None) -> tuple[list[str], str | None]`

Consumes, already present in `core.py` — confirmed at `a0d29ac7`:

- `HMCClient.__init__(self, config: HMCConfig) -> None`, which already constructs
  `self._quick_property_names` and `self._quick_property_names_lock` at `core.py:255,260`.
- `asyncio`, imported at the top of `core.py`.
- `async def search_uom(self, resource_type: str, property_name: str, property_value: str) -> list[dict[str, Any]]` at `core.py:859`.

Provides:

- `async def _defined_search_parameter_names(self, resource_type: str) -> frozenset[str] | None`
- `search_uom`'s new keyword-only `validate: bool = False`

**Verification.**

- Contract: `validate=True` refuses an undefined property before transport. `Mode: focused-test`.
  Observable: `ValueError` raised, and the `respx` route for
  `/rest/api/uom/{R}/search/({P}=={V})` records zero calls. Test:
  `::test_search_uom_validate_refuses_an_undefined_property`. Expected red before the branch exists:
  `Failed: DID NOT RAISE <class 'ValueError'>`. Green:
  `uv run --no-sync pytest tests/unit/test_client.py -k search_uom --no-cov -q`.
- Contract: `validate=True` passes a defined property through. `Mode: focused-test`. Test:
  `::test_search_uom_validate_allows_a_defined_property`. Expected red: `TypeError:
  search_uom() got an unexpected keyword argument 'validate'`.
- Contract: the names are read at most once per type per client, sequentially and concurrently.
  `Mode: focused-test`. Observable: the discovery route's `call_count`. Tests:
  `::test_search_uom_validate_reads_the_names_once_per_type` and
  `::test_search_uom_validate_reads_the_names_once_under_concurrency`, the latter driving several
  calls through `asyncio.gather`. Expected red without the cache: `assert 3 == 1`; without the lock,
  the concurrency case fails the same way intermittently, so it asserts on `call_count` after
  `gather` rather than on timing.
- Contract: the cache is keyed per resource type and not shared between clients.
  `Mode: focused-test`. Tests: `::test_search_uom_validate_caches_per_resource_type`,
  `::test_search_uom_validate_rereads_for_a_new_client`. Expected red on a single-slot cache: the
  second type's discovery route records 0 calls and its defined name is wrongly rejected.
- Contract: a discovery read yielding no names degrades and is cached rather than retried.
  `Mode: focused-test`. Test: `::test_search_uom_validate_degrades_and_caches_the_failure`,
  parametrized over a 500, a 400, an `httpx.ConnectError` and a 204. Expected red on an
  implementation that lets the error propagate: `HMCError` instead of a returned result.
- Contract: the default makes no discovery request and behaves as today. `Mode: focused-test`.
  Test: `::test_search_uom_defaults_to_no_validation`. Expected red on a default-on implementation:
  the discovery route records 1 call, not 0.
- Contract: `validate` is keyword-only with default `False`. `Mode: focused-test`. Observable:
  `inspect.signature`. Test: `::test_search_uom_validate_is_keyword_only_and_defaults_false`.
  Expected red on a positional parameter: the `kind` assertion fails.
- Contract: the HTTP 400 rationale in the docstring. `Mode: task-test-not-applicable`. The changed
  surface is prose addressed to a human reader; no executable consumer validates it, and asserting
  its wording would snapshot prose, which the Global Constraints forbid.
- Contract: the `CHANGELOG.md` entry. `Mode: task-test-not-applicable`.
  `tests/unit/test_changelog.py` binds only the declared `pyproject.toml` version, which this change
  does not alter; no executable consumer validates an unreleased entry.

**Steps.**

1. Write `test_search_uom_validate_refuses_an_undefined_property` in `tests/unit/test_client.py`,
   below the Task 1 block, under its own head comment stating that these bodies are constructed on
   the same terms. Mock `/rest/api/uom/LogicalPartition/search` with
   `_search_parameter_entry("LogicalPartition", *_LOGICAL_PARTITION_SEARCH_PARAMETERS)` and mock the
   instance-search path as well, so the test can assert it was never called. Call
   `await hmc.search_uom("LogicalPartition", "NoSuchProperty", "x", validate=True)` inside
   `pytest.raises(ValueError)`, then assert the instance-search route's `call_count == 0`.
2. Run `uv run --no-sync pytest tests/unit/test_client.py -k search_uom --no-cov -q`. Expect
   `TypeError: search_uom() got an unexpected keyword argument 'validate'`.
3. In `core.py`'s `__init__`, directly below the existing quick-property cache lines, add:

   ```python
   # Search-parameter names per resource type, on the same terms as the
   # quick-property cache above: read once, kept for this client's lifetime,
   # None meaning a discovery read that yielded no names (ADR 0142).
   self._search_parameter_names: dict[str, frozenset[str] | None] = {}
   self._search_parameter_names_lock = asyncio.Lock()
   ```

4. In `core.py`, immediately after `list_search_parameters`, add the read-through helper:

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
               # search_uom. That is #789's fourth criterion.
               names = []
           # An empty answer is "unknown", never "defines nothing": a 204
           # returns ([], version) without raising, and an empty positive set
           # would reject every property for this client's lifetime.
           defined = frozenset(names) if names else None
           self._search_parameter_names[resource_type] = defined
           return defined
   ```

5. Add the `validate` parameter and the pre-flight branch to `search_uom`, keeping the existing body
   below it unchanged:

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

6. Replace `search_uom`'s one-line docstring with a full one. It must state: the path it builds;
   that the HMC answers an unsupported search property with **HTTP 400**, which is why the
   pre-flight exists (#789 criterion 3); that `validate` checks the name against
   `list_search_parameters(resource_type)` before anything is sent and raises `ValueError`;
   that the names are cached for this client's lifetime so validating costs at most one extra
   request per resource type per session; that it is off by default because the client is
   constructed per tool call, so the cache would rarely be reused (ADR 0142); that a level where the
   discovery read fails validates nothing rather than raising; and that a transport failure is
   cached as durably as a firmware-level one, so a transient one leaves validation off for that type
   until a new client is constructed.
7. Re-run the step 2 command. Expect `test_search_uom_validate_refuses_an_undefined_property` to
   pass and the pre-existing `search_uom` tests at `tests/unit/test_client.py:824,840` to stay green.
8. Write the remaining eight tests named in **Verification**, running the step 2 command after each.
   For `allows_a_defined_property`, mock both routes, call with
   `property_name="PartitionName", validate=True`, and assert the parsed result. For
   `reads_the_names_once_per_type`, make three validated calls and assert the discovery route's
   `call_count == 1`. For `reads_the_names_once_under_concurrency`, drive five validated calls
   through `asyncio.gather` on one client and assert the same. For `caches_per_resource_type`, mock
   two types' discovery routes and assert each records exactly 1 call. For `rereads_for_a_new_client`,
   run two `async with HMCClient(make_config())` blocks and assert `call_count == 2`. For
   `degrades_and_caches_the_failure`, parametrize the discovery route over a 500, a 400,
   `httpx.ConnectError`, and a 204, then assert two validated calls both return the instance-search
   result and the discovery route recorded exactly 1 call. For `defaults_to_no_validation`, call
   without `validate` and assert the discovery route recorded 0 calls. For
   `validate_is_keyword_only_and_defaults_false`, read
   `inspect.signature(HMCClient.search_uom).parameters["validate"]` and assert
   `kind is inspect.Parameter.KEYWORD_ONLY` and `default is False`.
9. Add one entry to `CHANGELOG.md` under `## [Unreleased]` / `### Added`, naming
   `HMCClient.list_search_parameters` and `search_uom`'s `validate` parameter, and stating that the
   parsed response shape is not yet confirmed against firmware. Create the `### Added` subsection
   only if `## [Unreleased]` does not already have one.
10. Run `just lint`, `just typecheck`, then `just verify`. Each exits 0; `just verify` prints a
    compact success summary and the coverage gate passes.
11. Run `uv run --no-sync prek run --all-files`. It exits 0 with every hook `Passed` or `Skipped`.
12. Commit: `feat(client): validate search properties against the discovered set`.

**Acceptance.** `uv run --no-sync pytest tests/unit/test_client.py -k "search_uom or
list_search_parameters" --no-cov -q` reports 0 failed. `just verify` exits 0.
`uv run --no-sync prek run --all-files` exits 0. `git --no-pager diff --name-only "$(git merge-base
HEAD origin/main)"` lists exactly `CHANGELOG.md`, the ADR, the plan, the spec,
`src/hmc_mcp/client/core.py` and `tests/unit/test_client.py`.

**Cleanup.** None: no temporary file, fixture directory or generated artifact is created. If
`just doc-freshness` reddens, the fix is to run the recipe the failing document names and commit its
output, never to hand-edit the document.
