# Implementation plan: trustworthy live-runner evidence

Goal: make `scripts/live_test_runner.py` emit observations a maturity catalog can promote
from, and make the offline capability validator refuse a promoting claim that no committed
runner artifact backs.

Architecture: three new modules under the existing `scripts/live_test/` package hold the
redaction detector, the observation vocabulary, and the run/evidence artifact.
`scripts/live_test_runner.py` keeps ownership of `RunState` and gains a runtime dispatch
check plus evidence emission. `scripts/check_capability_inventory.py` gains maturity
format 2, a `live-artifact` provenance kind, and evidence-artifact validation. No file
under `src/hmc_mcp/` changes.

Tech stack: Python 3.11, standard library only, plus the `fastmcp` client already used by
the runner. No new dependency.

Spec: [`../specs/2026-09-06-trustworthy-live-evidence-design.md`](../specs/2026-09-06-trustworthy-live-evidence-design.md).
Decision: [ADR 0127](../../adr/0127-trusted-live-evidence-artifacts.md).

Expected implementation size: 1,650–1,950 changed lines (L) — summed from the file map
below: three new modules (~380), the runner (~260), twelve workflow modules (~260), the
validator (~240), two test modules (~660), documents (~55). The range sits above the L
band's 1,000-line mapping because 23 broken dispatch corrections and two test modules
dominate it; the band is unchanged and the mapped denominator is not revised.

## Global Constraints

Transcribed from the spec's *Global constraints* section:

- Python 3.11 is the floor; CI runs 3.11, 3.12, 3.13 and 3.14 on amd64 and arm64.
- No new runtime or development dependency. Standard library, the existing `fastmcp`
  client, and code already in `scripts/`.
- `scripts/` files get one test module each, named `tests/scripts/test_<name>.py`
  (`AGENTS.md`). `scripts/live_test_runner.py` and the `scripts/live_test/` package keep
  their existing exception, `tests/test_live_runner.py`.
- Guardrails: `just verify`, then `uv run --no-sync prek run --all-files`.
- Public artifacts carry no hostname, IP address, absolute path, serial number or
  credential.

Repository conventions that bind every task:

- Never run a bare `uv sync`, `uv run` or `uv add`; every `uv run` passes `--no-sync`.
- Diff against the merge base: `git --no-pager diff "$(git merge-base HEAD origin/main)"`.
- A new `static` sub-recipe needs a matching prek hook. **This change adds no `static`
  sub-recipe**, so no `.pre-commit-config.yaml` edit is required or permitted.
- ADR filenames match `NNNN-lowercase-kebab-slug.md` and the H1 announces the same number.
  There is no ADR index in this repository.

## Resume facts

- Branch `feat/trustworthy-live-evidence-623`, base `main`.
- Worktree `/home/dave/src/hmc-mcp-worktrees/feat-trustworthy-live-evidence-623`.
- Issue #623, frozen scope annotation `q623-d32263bc`.
- Baseline measured at `ded24a77`.

## Deferrals carried into this plan

None. No `$trial-loop` run on this branch has disposed of a finding as deferred.

## File map

Created:

| Path | Answerable for |
|---|---|
| `scripts/live_test/redaction.py` | the one definition of the private-identifier patterns and the detector both the runner and the validator use |
| `scripts/live_test/observation.py` | the result vocabulary, `CallFailure`, `Assertion`, `ExpectedOutcome`, the reason-code registry, and failure classification |
| `scripts/live_test/evidence.py` | `RunMetadata`, environment settings, and building and writing the evidence artifact |
| `docs/adr/0127-trusted-live-evidence-artifacts.md` | the decision (already written) |
| `docs/workflow/specs/2026-09-06-trustworthy-live-evidence-design.md` | the design (already written) |

Modified:

| Path | Change |
|---|---|
| `scripts/live_test_runner.py` | imports the shared redaction module; `RunState.call` validates arguments and classifies failures; `record` yields `observed`; new `record_verified` and `record_with_expected`; `skip` takes a reason code; run metadata and evidence emission; `--evidence-file` |
| `scripts/live_test/{connectivity,escape_hatch,inventory,lpar,metrics,network,pcie,profiles,provisioning,storage,users,vmedia}.py` | 23 dispatch corrections, 52 skip reason codes, 17 expected-outcome migrations, 3 verified conversions |
| `scripts/check_capability_inventory.py` | maturity format 2, `live-artifact` provenance, evidence-artifact validation, promotion rules |
| `docs/capabilities/maturity.json` | `format_version` 2 |
| `.env.example` | the six `LIVE_TEST_ENV_*` keys, keeping it the complete example |
| `docs/capabilities/README.md` | format 2 and the evidence-artifact section |
| `tests/test_live_runner.py` | argument guard and the runner unit tests |
| `tests/scripts/test_check_capability_inventory.py` | evidence, provenance and promotion tests |
| `CHANGELOG.md` | one entry |

`docs/capabilities/evidence/` is **not** created. It has no committed artifact yet, git
cannot hold an empty directory, and the validator treats its absence as "no artifacts".

---

## Task 1: Extract the shared redaction detector

Creates `scripts/live_test/redaction.py`. Modifies `scripts/live_test_runner.py`,
`tests/test_live_runner.py`.

Where it fits: the evidence artifact is public and is written by the runner and read by
the validator, so both need the same detector. Two copies would drift invisibly. This task
moves the existing patterns and adds the detector the later tasks call.

### Interfaces

Consumes: nothing from earlier tasks.

Provides to later tasks:

```python
# scripts/live_test/redaction.py
def redact_text(value: str) -> str: ...
def redact_data(data: Any) -> Any: ...
def private_identifiers(value: str) -> tuple[str, ...]: ...
```

`redact_text` and `redact_data` are the existing `_redact_failure_text` and
`_redact_failure_data` bodies from `scripts/live_test_runner.py:87-101`, moved unchanged.
`private_identifiers` returns the distinct pattern names (`"secret"`, `"url-userinfo"`,
`"hostname"`, `"absolute-path"`) whose pattern matches *value*, empty when none do. It is
the detector; `redact_text` is the transformer.

### Verification

- **Contract:** `redact_text` behaviour is unchanged by the move.
  **Mode: focused-test.** `tests/test_live_runner.py::test_call_failure_is_redacted_when_recorded`
  already covers it and must stay green.
  Red observation: delete one `sub` call in `redact_text`; the test fails on an
  unredacted host. Green: `uv run --no-sync pytest tests/test_live_runner.py -q -k redact`.
- **Contract:** `private_identifiers` names every pattern class that matches, and nothing
  when a value is clean.
  **Mode: focused-test.** New `tests/test_live_runner.py::test_private_identifiers_names_each_matching_class`,
  parametrised over one value per class plus a clean value.
  Red observation: written before the function exists — `ImportError`.
  Green: `uv run --no-sync pytest tests/test_live_runner.py -q -k private_identifiers`.

### Steps

1. Create `scripts/live_test/redaction.py` with the module docstring
   `"""Private-identifier patterns shared by the live runner and the offline validator."""`
   and `from __future__ import annotations`.
2. Move `_SECRET_VALUE_RE`, `_URL_USERINFO_RE`, `_HOSTNAME_RE` and `_ABSOLUTE_PATH_RE`
   from `scripts/live_test_runner.py:75-84` into it verbatim, renaming each to drop the
   leading underscore (`SECRET_VALUE_RE`, `URL_USERINFO_RE`, `HOSTNAME_RE`,
   `ABSOLUTE_PATH_RE`) because they are now a module API.
3. Move the bodies of `_redact_failure_text` and `_redact_failure_data`
   (`scripts/live_test_runner.py:87-101`) into `redact_text` and `redact_data`, unchanged
   apart from the renamed patterns.
4. Add:

   ```python
   _PATTERNS = (
       ("secret", SECRET_VALUE_RE),
       ("url-userinfo", URL_USERINFO_RE),
       ("hostname", HOSTNAME_RE),
       ("absolute-path", ABSOLUTE_PATH_RE),
   )


   def private_identifiers(value: str) -> tuple[str, ...]:
       """Return the pattern classes that match *value*, in declaration order."""
       return tuple(name for name, pattern in _PATTERNS if pattern.search(value))
   ```

5. In `scripts/live_test_runner.py`, delete the four patterns and both functions, and add
   `from live_test.redaction import redact_data, redact_text` to the existing
   `live_test.*` import block. Replace the three call sites — `_redact_failure_data` at
   line 481 and `_redact_failure_text` at lines 787 and 865 — with the new names.
6. Add the new test named in Verification.
7. Run `uv run --no-sync pytest tests/test_live_runner.py -q`. Expect
   `passed` with no failures and no errors.
8. Run `just lint` and `just typecheck`. Expect no output and exit 0 from each.
9. Commit: `refactor: share the live-test redaction detector`.

### Acceptance criteria

- `rg -n '_redact_failure' scripts/` returns nothing.
- `scripts/live_test/redaction.py` imports nothing from `hmc_mcp` or `fastmcp`, so the
  offline validator can import it without composing an MCP application.
- `just lint`, `just typecheck` and `tests/test_live_runner.py` are green.

---

## Task 2: Structured outcomes and the result vocabulary

Creates `scripts/live_test/observation.py`. Modifies `scripts/live_test_runner.py`,
`tests/test_live_runner.py`.

Where it fits: this is the task that makes a recorded result mean something. Everything
after it depends on `RESULTS`, `CallFailure` and `record_verified`.

### Interfaces

Consumes from Task 1: `redact_data`, `redact_text`.

Provides to later tasks:

```python
# scripts/live_test/observation.py
RESULTS: frozenset[str]  # {"observed", "passed", "failed", "skipped"}
CLEANUP: frozenset[str]  # {"not-run", "not-required", "failed", "passed"}

@dataclass(frozen=True)
class CallFailure:
    exception_type: str
    message: str
    traceback_text: str
    error_codes: frozenset[str]
    http_status: int | None
    denied: bool

@dataclass(frozen=True)
class Assertion:
    description: str
    holds: bool

@dataclass(frozen=True)
class ExpectedOutcome:
    reason_code: str
    reason: str
    error_codes: frozenset[str] = frozenset()
    denial: bool = False
    def __post_init__(self) -> None: ...   # raises ValueError when it would match everything
    def matches(self, failure: CallFailure) -> bool: ...

def classify_failure(exc: BaseException) -> CallFailure: ...
def register_reason_code(code: str, description: str) -> str: ...
def reason_codes() -> Mapping[str, str]: ...
```

Provides on `RunState` (defined in `scripts/live_test_runner.py`):

```python
async def call(self, client: Client, tool: str, **kwargs: Any) -> tuple[str, Any]: ...
def record(self, subtask: int, tool: str, status: str, data: Any, note: str = "") -> None: ...
def skip(self, subtask: int, tool: str, reason: str, reason_code: str) -> None: ...
def record_with_expected(
    self, subtask: int, tool: str, status: str, data: Any,
    expected: Sequence[ExpectedOutcome],
) -> None: ...
def record_verified(
    self, subtask: int, tool: str, *, operation: str, scope: dict[str, Any],
    scenario: dict[str, str], assertions: Sequence[Assertion], cleanup: str, data: Any,
) -> None: ...
```

`call` keeps its two-element return so the twelve workflow modules' `st, data = await
state.call(...)` unpacking is unchanged. On failure `data` is a `CallFailure`.

### Verification

- **Contract:** `classify_failure` extracts HMC error codes as whole tokens from the
  message only, never the traceback.
  **Mode: focused-test.** New `test_classify_failure_reads_codes_from_the_message_only`:
  an exception whose message carries `REST000E` and whose traceback text carries
  `REST999Z` yields `error_codes == {"REST000E"}`.
  Red: written before `classify_failure` exists — `ImportError`.
  Green: `uv run --no-sync pytest tests/test_live_runner.py -q -k classify_failure`.
- **Contract:** an ADR 0038 denial is recognised across the FastMCP boundary.
  **Mode: focused-test.** New `test_a_real_access_policy_denial_classifies_as_denied`
  composes the application with a policy granting nothing, calls a tool through
  `fastmcp.Client`, and asserts `classify_failure(exc).denied is True`.
  Red: written before the denial pattern exists — `denied` is `False`.
  Green: `uv run --no-sync pytest tests/test_live_runner.py -q -k denial`.
- **Contract:** `ExpectedOutcome` rejects a declaration that would match everything.
  **Mode: focused-test.** New `test_expected_outcome_requires_a_code_or_a_denial`
  expects `pytest.raises(ValueError)`.
  Red: before `__post_init__` exists, no exception is raised.
  Green: `uv run --no-sync pytest tests/test_live_runner.py -q -k expected_outcome`.
- **Contract:** a failure no declaration matches records `failed`, never `skipped`.
  **Mode: focused-test.** New `test_an_unmatched_failure_is_recorded_as_failed`.
  Red: before `record_with_expected` exists — `AttributeError`.
  Green: `uv run --no-sync pytest tests/test_live_runner.py -q -k unmatched_failure`.
- **Contract:** `record` cannot produce `passed`.
  **Mode: focused-test.** New `test_record_always_yields_a_non_promoting_result`
  drives a successful call and asserts the row's `result` is `observed`.
  Red: before the vocabulary change the row carries `status: "PASS"` and no `result`.
  Green: `uv run --no-sync pytest tests/test_live_runner.py -q -k non_promoting`.
- **Contract:** `record_verified` yields `failed` when any assertion is false, and when
  cleanup is `failed`.
  **Mode: focused-test.** New `test_record_verified_fails_on_a_false_assertion` and
  `test_record_verified_fails_on_failed_cleanup`.
  Red: before `record_verified` exists — `AttributeError`.
  Green: `uv run --no-sync pytest tests/test_live_runner.py -q -k record_verified`.
- **Contract:** every reason code is registered exactly once.
  **Mode: focused-test.** New `test_every_reason_code_is_registered_once` walks
  `LIVE_WORKFLOW_MODULES` for string literals passed as `reason_code` and compares them to
  `reason_codes()`.
  Red: add an unregistered literal to a fixture source; the test reports it.
  Green: `uv run --no-sync pytest tests/test_live_runner.py -q -k reason_code`.

### Steps

1. Create `scripts/live_test/observation.py` with the docstring
   `"""The result vocabulary and failure classification for live-test observations."""`.
2. Define `RESULTS = frozenset({"observed", "passed", "failed", "skipped"})` and
   `CLEANUP = frozenset({"not-run", "not-required", "failed", "passed"})`.
3. Define the classification patterns, anchored, module-level:

   ```python
   _HMC_CODE_RE = re.compile(r"\bREST\d{4}[A-Z]\b")
   _HTTP_STATUS_RE = re.compile(r"\bHTTP (\d{3})\b")
   # Both ADR 0038 denial templates render this clause; the concrete exception type
   # (ConnectionScopeError, TargetScopeError) does not survive FastMCP's ToolError.
   _DENIAL_RE = re.compile(r" is not permitted on .+ by access policy ")
   ```

4. Define `CallFailure`, `Assertion` and `ExpectedOutcome` exactly as in the Interfaces
   block. `ExpectedOutcome.__post_init__` raises
   `ValueError("an expected outcome must name an error code or a denial")` when
   `not self.error_codes and not self.denial`. `matches` returns
   `bool(self.error_codes & failure.error_codes) or (self.denial and failure.denied)`.
5. Define `classify_failure`:

   ```python
   def classify_failure(exc: BaseException) -> CallFailure:
       """Classify one dispatch failure from its message, never its traceback."""
       message = str(exc)
       status = _HTTP_STATUS_RE.search(message)
       return CallFailure(
           exception_type=type(exc).__name__,
           message=message,
           traceback_text=traceback.format_exc(),
           error_codes=frozenset(_HMC_CODE_RE.findall(message)),
           http_status=int(status.group(1)) if status else None,
           denied=bool(_DENIAL_RE.search(message)),
       )
   ```

6. Define the reason-code registry as a module-level `dict[str, str]` with
   `register_reason_code(code, description)` raising `ValueError` on a duplicate code and
   returning the code, and `reason_codes()` returning `MappingProxyType` of it. Register
   the two codes this task introduces: `invalid-arguments` ("the harness dispatched
   arguments the tool does not accept") and `build-identity-unverified` ("the deployed
   build did not match the tested revision").
7. In `scripts/live_test_runner.py`, import the new names and rewrite `RunState.call` so
   the `except` branch returns `("FAIL", classify_failure(exc))` instead of the formatted
   string.
8. Rewrite `RunState.record` to build the entry with a `result` key of `"observed"` for a
   `PASS` status and `"failed"` for a `FAIL` status, keeping the existing `status`,
   `timestamp`, `note` and redacted `data` keys so the persisted document stays readable.
   Redaction now applies when `data` is a `CallFailure`: store
   `redact_text(data.message)` and drop `traceback_text` from the persisted row, because
   a traceback is exactly the field the redaction detector cannot make safe.
9. Add `skip`'s required `reason_code` parameter and store it on the entry.
10. Replace `record_expected_or_real` with `record_with_expected`, which finds the first
    declaration whose `matches` returns true for a `CallFailure` data value and records
    `skipped` with that declaration's `reason_code` and `reason`; otherwise delegates to
    `record`.
11. Add `record_verified`, which computes `result` as `"passed"` when every assertion
    holds and `cleanup in {"passed", "not-required"}`, and `"failed"` otherwise, and
    stores `operation`, `scope`, `scenario`, the assertion descriptions of the assertions
    that hold, and `cleanup` on the entry. It raises `ValueError` when `assertions` is
    empty or `cleanup not in CLEANUP`.
12. Add the seven tests named in Verification.
13. Run `uv run --no-sync pytest tests/test_live_runner.py -q`. Expect failures only in
    tests that call `state.skip` with three arguments — those are Task 4's to fix. Record
    the exact count before proceeding.
14. Commit: `feat: give live-test observations a structured result vocabulary`.

### Acceptance criteria

- `rg -n 'expected_fail_substrings' scripts/ tests/` returns nothing after Task 4; after
  this task it returns only the twelve workflow modules.
- No recorded entry produced by `record` carries `result: "passed"`.
- `classify_failure` never reads `traceback_text` when computing `error_codes`,
  `http_status` or `denied`.

---

## Task 3: Validate dispatch arguments against the served schema

Modifies `scripts/live_test_runner.py`, `tests/test_live_runner.py`.

Where it fits: this is the guard issue #623 asks for. It goes red on the 23 dispatches
Task 4 corrects; that red is the task's own evidence and is expected here.

### Interfaces

Consumes from Task 2: `register_reason_code`, the `invalid-arguments` code.

Provides:

```python
# scripts/live_test_runner.py
def _dispatch_problems(tool: str, keywords: Iterable[str],
                       schemas: Mapping[str, Mapping[str, Any]]) -> tuple[str, ...]: ...
```

Returns a tuple of human-readable problems: an unregistered tool, an unknown keyword, or a
missing required keyword. Empty means the dispatch is valid. `RunState.schemas` holds the
served schemas, populated in `main` and empty by default so a unit test can construct a
`RunState` without composing an application.

```python
# tests/test_live_runner.py
def _dispatched_calls(source: str) -> list[tuple[int, str, tuple[str, ...]]]: ...
async def _served_schemas() -> dict[str, dict[str, object]]: ...
```

`_dispatched_calls` is `_dispatched_tool_names` widened to return line number, tool name
and keyword names, raising `AssertionError` on a non-literal tool name or a `**` splat.
`_dispatched_tool_names` is reimplemented on top of it so the three existing #485 guard
tests keep passing unchanged.

### Verification

- **Contract:** every dispatch's keywords are a subset of the tool's schema properties and
  a superset of its required properties.
  **Mode: focused-test.** New `test_every_dispatched_argument_matches_the_served_schema`.
  Red: it reports 23 offending dispatches on the current modules — this is the measured
  baseline in the spec, and the assertion message lists each `file:line`.
  Green after Task 4: `uv run --no-sync pytest tests/test_live_runner.py -q -k served_schema`.
- **Contract:** the guard refuses a dispatch it cannot read.
  **Mode: focused-test.** New `test_argument_guard_refuses_a_splat_it_cannot_read` over
  the source `await state.call(client, "hmc_get_job", **kwargs)`, expecting
  `pytest.raises(AssertionError, match="cannot read")`.
  Red: before the splat branch exists, the call is silently skipped and no exception is
  raised. Green: `uv run --no-sync pytest tests/test_live_runner.py -q -k splat`.
- **Contract:** the guard bites on a synthetic bad dispatch.
  **Mode: focused-test.** New `test_argument_guard_reports_an_unknown_keyword` over the
  source `await state.call(client, "hmc_get_job", job_uuid="x")`, asserting the reported
  problem names `job_uuid` and `job_id`.
  Red: before `_dispatch_problems` exists — `ImportError`.
  Green: `uv run --no-sync pytest tests/test_live_runner.py -q -k unknown_keyword`.
- **Contract:** an invalid dispatch records `failed` with `invalid-arguments` and never
  reaches the client.
  **Mode: focused-test.** New `test_an_invalid_dispatch_never_reaches_the_client` uses a
  stub client whose `call_tool` raises `AssertionError` if called.
  Red: before the runtime check exists, the stub raises.
  Green: `uv run --no-sync pytest tests/test_live_runner.py -q -k invalid_dispatch`.

### Steps

1. In `scripts/live_test_runner.py`, add `_dispatch_problems` as specified. Read
   `schema.get("properties", {})` and `schema.get("required", [])`; an absent tool yields
   the single problem `f"{tool} is not a registered tool"`.
2. Add `schemas: dict[str, dict[str, Any]] = field(default_factory=dict)` to `RunState`.
3. At the top of `RunState.call`, when `self.schemas` is non-empty, compute
   `problems = _dispatch_problems(tool, kwargs, self.schemas)` and, when non-empty,
   return `("FAIL", CallFailure(exception_type="InvalidDispatch",
   message="; ".join(problems), traceback_text="", error_codes=frozenset(),
   http_status=None, denied=False))` without calling the client. `self.schemas` is empty
   only in unit tests that supply their own stub client.
4. In `record_with_expected`, refuse to match an `InvalidDispatch` failure against any
   declaration: check `failure.exception_type == "InvalidDispatch"` first and record
   `failed` with reason code `invalid-arguments`. This is the routing the spec's *Dispatch
   argument validity* section forbids leaving to a declaration.
5. In `main`, after the client is open, populate
   `state.schemas = {tool.name: tool.inputSchema for tool in await client.list_tools()}`
   before the subtask loop.
6. In `tests/test_live_runner.py`, add `_dispatched_calls` and reimplement
   `_dispatched_tool_names` as `{tool for _, tool, _ in _dispatched_calls(source)}`.
   Extend the unreadable-dispatch branch to also raise when any keyword's `arg` is `None`,
   with the message `"call() dispatches arguments this guard cannot read — name them"`.
7. Add `_served_schemas`, composing the application exactly as
   `scripts/live_test_runner.py:819-826` does — `compile_legacy_policy(TOOL_SECURITY,
   (DEFAULT_CONNECTION_TOKEN,), include_arbitrary_command=True)`, `create_mcp`, `_gates`,
   `configure_arbitrary_command_tool(True, ...)` — then reading `client.list_tools()`.
   Composition needs no HMC credential; `scripts/gen_tool_reference.py` already relies on
   that.
8. Add the four tests named in Verification.
9. Run `uv run --no-sync pytest tests/test_live_runner.py -q -k served_schema`. **Expect
   it to fail, naming 23 dispatches.** Copy that list into the Task 4 worksheet and
   confirm it matches the table below exactly; a different list means the schema source or
   the walk is wrong, and Task 4 must not start until they agree.
10. Commit: `test: guard live-test dispatch arguments against the served schema`.

### Acceptance criteria

- The guard test fails with exactly 23 named dispatches at this commit.
- The three existing #485 guard tests
  (`test_every_dispatched_tool_name_is_registered`,
  `test_dispatch_guard_reports_a_tool_missing_from_the_registry`,
  `test_dispatch_guard_refuses_a_tool_name_it_cannot_read`) still pass unchanged.

---

## Task 4: Correct the dispatches and migrate the workflow modules

Modifies the twelve modules under `scripts/live_test/`, and `tests/test_live_runner.py`.

Where it fits: it turns Task 3's guard green and moves 52 skips and 17 expected-failure
sites onto Task 2's structured vocabulary.

### Interfaces

Consumes from Task 2: `ExpectedOutcome`, `Assertion`, `register_reason_code`.
Consumes from Task 3: the guard that measures this task's completion.

Provides: nothing new. Every change is a call-site correction.

### The 23 dispatch corrections

Each row gives the site, the wrong arguments, and the correct call. Values come from
`state.config` and `state.artifacts`, both already in scope at every site.

| Site | Tool | Wrong | Correct |
|---|---|---|---|
| `metrics.py:81` | `hmc_get_job` | `job_uuid=` | `job_id=job_uuid` |
| `metrics.py:91` | `hmc_wait_for_job` | `job_uuid=` | `job_id=job_uuid` |
| `lpar.py:126` | `hmc_delete_lpar` | omits system | add `system_name_or_uuid=config.system_name` |
| `network.py:172` | `hmc_delete_lpar` | omits system | add `system_name_or_uuid=config.system_name` |
| `provisioning.py:78` | `hmc_delete_lpar` | omits system | add `system_name_or_uuid=config.system_name` |
| `provisioning.py:37` | `hmc_provision_lpar` | flat `port_vlan_id`, `vios_partition_id`, `vios_slot`, `vios_uuid`, `storage_name`, `storage_kind`, `vg_uuid`, `desired_memory` | nested `adapters={"port_vlan_id":…, "vios_partition_id":…, "vios_slot":…}`, `storage={"vios_uuid":…, "storage_name":…, "kind":…, "vg_uuid":…}`, `resources={"desired_memory":…}` |
| `provisioning.py:163` | `hmc_provision_lpar` | same, plus `**_baseline_provision_resources(state)` | same nesting; the helper's result is spread into `resources=` explicitly, not into the call |
| `users.py:27` | `hmc_create_user` | `name=`, `taskrole=` | `console_uuid=artifacts.console_uuid`, `user_id=config.test_user`, `password=_TEST_USER_PASSWORD`, `associated_task_role="viewer"` |
| `users.py:45,72,91` | `hmc_list_users` | omits console | add `console_uuid=artifacts.console_uuid` |
| `users.py:56` | `hmc_modify_user` | `name=` | `console_uuid=…`, `user_profile_uuid=artifacts.test_user_uuid` |
| `users.py:67` | `hmc_delete_user` | `name=` | `console_uuid=…`, `user_profile_uuid=artifacts.test_user_uuid` |
| `vmedia.py:737,833` | `hmc_read_lpar_boot_order` | `lpar_uuid=` | `lpar_name_or_uuid=lpar_uuid` |
| `vmedia.py:750,815,951` | `hmc_set_lpar_boot_order` | `lpar_uuid=` | `lpar_name_or_uuid=lpar_uuid` |
| `vmedia.py:823` | `hmc_clear_lpar_boot_order` | `lpar_uuid=` | `lpar_name_or_uuid=lpar_uuid` |
| `vmedia.py:763` | `hmc_power_on_lpar` | `timeout=120` | `timeout_seconds=120` |
| `vmedia.py:579,798,992` | `hmc_unmount_optical_media` | `mapping_uuid=` | `lpar_name_or_uuid=`, `media_name=` |

Two rows need new state rather than a rename:

- **`users.py`** needs the created user's profile UUID. Add
  `test_user_uuid: str | None = None` to `LiveTestArtifacts`, to
  `_ARTIFACT_NULLABLE_STRINGS`, and capture it from the `hmc_list_users` result that
  follows the create: the entry whose `UserID` equals `config.test_user`, reading its
  `UUID` through `live_test.results.resource`. When it is absent, skip the modify and
  delete steps with reason code `user-profile-uuid-unavailable` instead of dispatching.
- **`vmedia.py:992`** iterates mapping entries and unmounts by mapping UUID. Read
  `lpar_name_or_uuid` and `media_name` from each mapping entry through
  `live_test.results.resource`, and when either is absent skip that entry with reason code
  `mapping-identity-incomplete` rather than dispatching a call that cannot succeed.

### Verification

- **Contract:** all 23 dispatches match the served schema.
  **Mode: focused-test.** `test_every_dispatched_argument_matches_the_served_schema`
  from Task 3.
  Red: the same test at Task 3's commit, naming 23 dispatches.
  Green: `uv run --no-sync pytest tests/test_live_runner.py -q -k served_schema`.
- **Contract:** every existing workflow behavioural test still describes the real call
  sequence.
  **Mode: focused-test.** The existing
  `test_vmedia_workflows_execute_their_behavioral_contracts` parametrisation and
  `test_sriov_orchestrator_*` suite.
  Red: the boot-order and unmount renames break their stub expectations first.
  Green: `uv run --no-sync pytest tests/test_live_runner.py -q`.
- **Contract:** the user path skips rather than dispatching when the profile UUID is
  unavailable.
  **Mode: focused-test.** New `test_user_administration_skips_without_a_profile_uuid`.
  Red: before the guard exists, the stub client receives a `hmc_modify_user` call.
  Green: `uv run --no-sync pytest tests/test_live_runner.py -q -k profile_uuid`.

### Steps

1. Apply every rename in the table above, one module at a time, running
   `uv run --no-sync pytest tests/test_live_runner.py -q -k served_schema` after each
   module and confirming the reported count falls.
2. Add `test_user_uuid` to `LiveTestArtifacts` and `_ARTIFACT_NULLABLE_STRINGS`, and the
   capture and guard described above in `scripts/live_test/users.py`.
3. Rewrite the `vmedia.py:992` sweep to read the mapping's LPAR and media identity.
4. Replace all 17 `record_expected_or_real` calls with `record_with_expected`, converting
   each `expected_fail_substrings` list into module-level `ExpectedOutcome` constants.
   `_REST000E_SKIP = ["REST000E", "400", "not available on this HMC"]`
   (`scripts/live_test/users.py:20`) becomes a single declaration naming `REST000E` only:
   `"400"` matched any three-digit run anywhere in a traceback, and
   `"not available on this HMC"` is prose the HMC does not emit as a code.
5. Add a `reason_code` to all 52 `state.skip` call sites, registering each code once via
   `register_reason_code` in the module that uses it.
6. Convert the two job scenarios in `scripts/live_test/metrics.py` and the
   `hmc_console_info` probe in `scripts/live_test/connectivity.py` to `record_verified`.
   The job assertions are: the returned entry is a mapping; its `UUID` or `JobID` equals
   the `job_id` passed. Cleanup is `not-required` for all three — they are reads.
7. Add the new test named in Verification.
8. Run `uv run --no-sync pytest tests/test_live_runner.py -q`. Expect all tests to pass.
9. Run `just lint` and `just typecheck`. Expect no output and exit 0 from each.
10. Commit: `fix: correct 23 live-test dispatches that no tool accepts`.

### Acceptance criteria

- `test_every_dispatched_argument_matches_the_served_schema` is green.
- `rg -n 'expected_fail_substrings|record_expected_or_real' scripts/ tests/` returns
  nothing.
- Every `state.skip` call passes a registered `reason_code`.

---

## Task 5: Run, build and environment identity, and the evidence artifact

Creates `scripts/live_test/evidence.py`. Modifies `scripts/live_test_runner.py`,
`tests/test_live_runner.py`.

### Interfaces

Consumes from Task 1: `private_identifiers`. From Task 2: `RESULTS`, `CLEANUP`,
`register_reason_code`.

Provides:

```python
# scripts/live_test/evidence.py
ENVIRONMENT_FIELDS: tuple[str, ...]  # hmc_release, hmc_build, hardware_family,
                                     # firmware, licensing, topology

@dataclass(frozen=True)
class RunMetadata:
    run_id: str
    started_at: str
    finished_at: str
    implementation_revision: str
    deployed_revision: str
    build_identity_verified: bool
    implementation_fingerprint: str
    environment: Mapping[str, str]

def read_environment(path: Path) -> dict[str, str] | None: ...
def mint_run_metadata(repo_root: Path, environment: Mapping[str, str],
                      started_at: str, finished_at: str) -> RunMetadata: ...
def build_artifact(metadata: RunMetadata, results: Sequence[Mapping[str, Any]]) -> dict[str, Any]: ...
def write_artifact(path: Path, artifact: Mapping[str, Any]) -> None: ...
```

`read_environment` returns `None` when no `LIVE_TEST_ENV_*` key is present, the six-key
mapping when all are present and non-empty, and raises `ValueError` naming the missing
keys for any partial subset. `mint_run_metadata` imports
`implementation_fingerprint` from `check_capability_inventory`; that function exists at
`scripts/check_capability_inventory.py:826` with the signature
`implementation_fingerprint(repo_root: Path) -> str`.

### Verification

- **Contract:** a partial `LIVE_TEST_ENV_*` block is a configuration error.
  **Mode: focused-test.** New `test_partial_environment_settings_are_rejected`.
  Red: before `read_environment` exists — `ImportError`.
  Green: `uv run --no-sync pytest tests/test_live_runner.py -q -k partial_environment`.
- **Contract:** `LiveTestConfig.from_env_file` loads a `.env` that carries the
  environment block instead of rejecting it as an unknown setting.
  **Mode: focused-test.** The existing
  `test_live_config_reads_the_complete_example_and_ignores_exports`, over the extended
  `.env.example`.
  Red: add the six keys to `.env.example` before the skip exists; the test fails with
  `invalid live-test configuration: unknown setting LIVE_TEST_ENV_HMC_RELEASE`.
  Green: `uv run --no-sync pytest tests/test_live_runner.py -q -k complete_example`.
- **Contract:** `build_identity_verified` is false for a dirty tree or a revision
  mismatch, and every row is then `failed`.
  **Mode: focused-test.** New `test_unverified_build_identity_forces_every_row_to_failed`.
  Red: before the forcing branch exists, a `passed` row survives.
  Green: `uv run --no-sync pytest tests/test_live_runner.py -q -k build_identity`.
- **Contract:** only `passed`, `failed` and `skipped` rows enter the artifact.
  **Mode: focused-test.** New `test_observed_rows_never_enter_the_evidence_artifact`.
  Red: before the filter exists, the `observed` row appears.
  Green: `uv run --no-sync pytest tests/test_live_runner.py -q -k observed_rows`.
- **Contract:** an unredacted leaf fails the write.
  **Mode: focused-test.** New `test_write_artifact_refuses_a_private_identifier`,
  expecting `pytest.raises(ValueError)` and asserting the destination does not exist.
  Red: before the detector call exists, the file is written.
  Green: `uv run --no-sync pytest tests/test_live_runner.py -q -k refuses_a_private`.
- **Contract:** `--evidence-file` selects the destination, and the default derives from
  the results path.
  **Mode: focused-test.** Extend the existing
  `test_live_runner_parses_selection_and_result_defaults`.
  Red: before the argument exists, `argparse` errors on the unknown option.
  Green: `uv run --no-sync pytest tests/test_live_runner.py -q -k result_defaults`.

### Steps

1. Create `scripts/live_test/evidence.py` with the docstring
   `"""Run, build and environment identity for live-test evidence artifacts."""`.
2. Define `ENVIRONMENT_FIELDS = ("hmc_release", "hmc_build", "hardware_family",
   "firmware", "licensing", "topology")` and `read_environment`, which parses the same
   `.env` file `LiveTestConfig.from_env_file` reads, taking keys
   `LIVE_TEST_ENV_<FIELD upper>`.
3. In `scripts/live_test_runner.py`, make `LiveTestConfig.from_env_file` skip keys
   beginning `LIVE_TEST_ENV_` — `continue` before the `cls._CONFIG_FIELDS` membership test
   at line 327 — so they are read by `read_environment` rather than reported as
   `unknown setting`. Add the six keys to `.env.example` with example values
   (`V10R3`, `2340`, `POWER10`, `FW1030.20`, `PCM enabled`,
   `single-managed-system`), so it stays the complete authoritative mapping
   `test_live_config_reads_the_complete_example_and_ignores_exports` asserts.
4. Define `mint_run_metadata`. `run_id` is
   `f"lt-{datetime.now(UTC).date().isoformat()}-{secrets.token_hex(4)}"`.
   `implementation_revision` is `git rev-parse HEAD`. `deployed_revision` reads the
   installed distribution's recorded revision, falling back to
   `implementation_revision` under this repository's editable install.
   `build_identity_verified` is
   `deployed_revision == implementation_revision and not git status --porcelain -- src scripts`.
   Every subprocess call passes `check=False` and treats a non-zero exit as
   `build_identity_verified = False` rather than raising: a runner that cannot read git
   must still record its run.
5. Define `build_artifact`, keeping only rows whose `result` is in
   `{"passed", "failed", "skipped"}` and, when `build_identity_verified` is false,
   rewriting each row's `result` to `"failed"` and its `reason` to the registered
   `build-identity-unverified` description.
6. Define `write_artifact`, which walks every string leaf through `private_identifiers`
   and raises `ValueError` naming the offending JSON path when any matches, then writes
   atomically through the runner's existing `_write_results` helper.
7. In `scripts/live_test_runner.py`, add `--evidence-file` to `_parse_arguments`, its
   default derived from the results path stem plus `-evidence.json`, and add the field to
   `RunnerArguments`. Thread it into `main`.
8. In `main`, capture `started_at` before the subtask loop and `finished_at` after it. Call
   `read_environment`; when it returns `None`, print
   `"  ℹ  No LIVE_TEST_ENV_* settings — no evidence artifact written"` and skip emission.
   Otherwise mint metadata, build and write the artifact, and print its path.
9. Add the six tests named in Verification.
10. Run `uv run --no-sync pytest tests/test_live_runner.py -q`. Expect all green.
11. Run `just lint`, `just typecheck`. Expect no output and exit 0 from each.
12. Commit: `feat: emit a redacted live-test evidence artifact`.

### Acceptance criteria

- Running the runner with no `LIVE_TEST_ENV_*` settings writes no evidence artifact and
  says so.
- An artifact containing a hostname-shaped value is never written.

---

## Task 6: Maturity format 2 and the trusted live-artifact validator

Modifies `scripts/check_capability_inventory.py`, `docs/capabilities/maturity.json`,
`tests/scripts/test_check_capability_inventory.py`.

### Interfaces

Consumes from Task 1: `private_identifiers`. From Task 5: the artifact shape.

Provides:

```python
# scripts/check_capability_inventory.py
EVIDENCE_DIRECTORY = "evidence"
PROVENANCE_KINDS = {"unverified", "live-artifact"}
EVIDENCE_REFERENCE_RE = re.compile(r"\Aevidence/([a-z0-9][a-z0-9-]*\.json)#([a-z0-9][a-z0-9-]*)\Z")

def load_evidence_artifacts(capabilities_root: Path, errors: list[str]) -> dict[str, dict[str, object]]: ...
def validate_evidence_artifact(name: str, document: object, operation_ids: Collection[str],
                               errors: list[str]) -> dict[str, dict[str, object]]: ...
```

`scripts/check_capability_inventory.py` runs as a script, which puts `scripts/` on
`sys.path` automatically. `tests/scripts/test_check_capability_inventory.py:13-19` instead
loads it through `importlib.util.spec_from_file_location` and never adds `scripts/` to
`sys.path`, so a bare `from live_test.redaction import …` resolves under the recipe and
raises `ModuleNotFoundError` under pytest. The guard is therefore required, not defensive.
Add it beside the existing `ROOT` definition at line 19; `sys` is already imported at
line 11:

```python
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_test.redaction import private_identifiers
```

### Verification

- **Contract:** the validator accepts only `format_version` 2.
  **Mode: focused-test.** New `test_maturity_format_one_is_rejected`.
  Red: before the bump, format 1 is accepted.
  Green: `uv run --no-sync pytest tests/scripts/test_check_capability_inventory.py -q -k format_one`.
- **Contract:** a `live-artifact` provenance binds to its row on every compared field.
  **Mode: focused-test.** New
  `test_live_artifact_provenance_must_equal_its_evidence_row`, parametrised over each
  compared field — operation, scope, scenario, result, assertions, cleanup, environment,
  both revisions, fingerprint — mutating one at a time and asserting a reported error
  naming that field.
  Red: before the comparison exists, every mutation is accepted.
  Green: `uv run --no-sync pytest tests/scripts/test_check_capability_inventory.py -q -k must_equal`.
- **Contract:** the six controlled cases in issue #623 cannot promote.
  **Mode: focused-test.** New `test_controlled_cases_cannot_promote`, parametrised over
  wrong argument, isError/denial, failed postcondition, cleanup failure, missing licence
  and absent target — each expressed as an evidence row and asserted to yield
  `eligible: false` or a validation error.
  Red: before the promotion conjunction exists, each is accepted as eligible.
  Green: `uv run --no-sync pytest tests/scripts/test_check_capability_inventory.py -q -k controlled_cases`.
- **Contract:** a stale fingerprint revokes eligibility.
  **Mode: focused-test.** New `test_a_changed_fingerprint_revokes_promotion`.
  Red: before condition 4 exists, the observation stays eligible.
  Green: `uv run --no-sync pytest tests/scripts/test_check_capability_inventory.py -q -k revokes_promotion`.
- **Contract:** a traversing, absolute or symlinked reference is rejected.
  **Mode: focused-test.** New `test_evidence_reference_confinement`, parametrised over
  `evidence/../secrets.json#row`, `/etc/passwd`, and a symlink whose target is outside
  the directory.
  Red: before confinement, the first two resolve and the symlink is read.
  Green: `uv run --no-sync pytest tests/scripts/test_check_capability_inventory.py -q -k confinement`.
- **Contract:** an unreferenced artifact is still validated.
  **Mode: focused-test.** New `test_an_unreferenced_evidence_artifact_is_validated`.
  Red: before the directory walk, a malformed unreferenced artifact is ignored.
  Green: `uv run --no-sync pytest tests/scripts/test_check_capability_inventory.py -q -k unreferenced`.
- **Contract:** an unredacted artifact leaf is rejected.
  **Mode: focused-test.** New `test_an_unredacted_evidence_leaf_is_rejected`.
  Red: before the detector call, the artifact is accepted.
  Green: `uv run --no-sync pytest tests/scripts/test_check_capability_inventory.py -q -k unredacted`.

### Steps

1. Change the `format_version` check at `scripts/check_capability_inventory.py:902` to
   require 2, and set `"format_version": 2` in `docs/capabilities/maturity.json`.
2. Add `load_evidence_artifacts`, which walks `docs/capabilities/evidence/*.json` when the
   directory exists, rejecting a symlink or a non-regular file, parsing each through the
   existing `load_json` (which already rejects duplicate keys at
   `scripts/check_capability_inventory.py:71`), and returning a mapping of file name to
   document. An absent directory yields an empty mapping and no error.
3. Add `validate_evidence_artifact`, checking: exact top-level key set; `format_version`
   is 1; `run_id` matches `\Alt-\d{4}-\d{2}-\d{2}-[0-9a-f]{8}\Z`; both revisions match
   `SHA_1`; the fingerprint matches `SHA_256`; `build_identity_verified` is a bool;
   `environment` has exactly the six keys with non-empty string values; `rows` is a list
   of objects with exact keys and unique ids; each row's `result` is in
   `{"passed", "failed", "skipped"}`; each row's `operation` is in *operation_ids*; a
   `passed` row has non-empty `assertions`, `cleanup` in `{"passed", "not-required"}` and
   `build_identity_verified` true; every string leaf yields no `private_identifiers`.
4. Replace the format-1 provenance check at
   `scripts/check_capability_inventory.py:681-686` with one that accepts either kind,
   requires `EVIDENCE_REFERENCE_RE` to match a `live-artifact` reference, resolves the
   file as a direct child of the evidence directory by comparing resolved parents rather
   than by string prefix, and requires the named row to exist.
5. Add the equality comparison between the observation and its row over operation, scope
   identity, scenario id and description, result, assertions as a set, cleanup,
   environment, both revisions and the fingerprint. Report each mismatch by field name.
6. Replace the unconditional `promotion.eligible is not False` check at
   `scripts/check_capability_inventory.py:665-670` with the five-condition conjunction in
   the spec's *Trusted provenance and promotion* section. A `false` value still requires a
   non-empty reason.
7. Add the import guard and `private_identifiers` import described in Interfaces.
8. Add the seven tests named in Verification.
9. Run `uv run --no-sync pytest tests/scripts/test_check_capability_inventory.py -q`.
   Expect all green.
10. Run `just capability-inventory`. Expect exit 0 and its existing two output lines.
11. Commit: `feat: bind maturity promotion to a committed evidence artifact`.

### Acceptance criteria

- `just capability-inventory` is green with no evidence directory present.
- No observation can be `eligible` without a resolvable, matching, current evidence row.

---

## Task 7: Document the shipped contract

Modifies `docs/capabilities/README.md`, `CHANGELOG.md`.

### Verification

- **Contract:** the capabilities README describes format 2 and the evidence artifact.
  **Mode: task-test-not-applicable.** The changed surface is the README's prose. The
  repository asserts this file's *content* through no test: `just doc-freshness` reads
  only its first line, looking for a generation banner, and this file carries none because
  it is hand-written rather than generated. Writing a test that greps its wording would be
  the prose snapshot the plan format forbids. The machine-checkable half of this contract —
  that the format the README describes is the format the validator enforces — is covered
  by Task 6's tests.
- **Contract:** `CHANGELOG.md` carries an entry.
  **Mode: task-test-not-applicable.** The changed surface is one prose entry under
  `## [Unreleased]`. `tests/unit/test_changelog.py:14-17` is the only test that reads this
  file, and it asserts that the version `pyproject.toml` declares has a matching
  `## [<version>]` heading — nothing about any entry's wording. An entry added under
  `## [Unreleased]` leaves that assertion untouched, and a test asserting this entry's
  wording would be the prose snapshot the plan format forbids.

### Steps

1. Rewrite the `## Maturity and evidence catalog` section of `docs/capabilities/README.md`
   for format 2: the two provenance kinds, the evidence-artifact shape, the five promotion
   conditions, the confinement rule, and the fact that an operator commits an artifact
   deliberately rather than the runner writing one into the tree.
2. Add the `LIVE_TEST_ENV_*` block to the same document, naming all six fields and the
   all-or-nothing rule.
3. Add a `CHANGELOG.md` entry under the unreleased heading, in the style of the entries
   around it, naming the argument guard, the structured result vocabulary, the evidence
   artifact and maturity format 2.
4. Run `just doc-freshness`. Expect exit 0 and no diff.
5. Commit: `docs: describe maturity format 2 and live evidence artifacts`.

### Acceptance criteria

- `just doc-freshness` and `just adr-numbering` are green.
- The README names no hostname, address or absolute path.

---

## Final verification

Run in the branch worktree, bare, reading each exit code:

1. `just verify` — expect the full suite green, ending with `verify: all groups load OK`.
2. `uv run --no-sync prek run --all-files` — expect every hook `Passed`.
3. `git --no-pager diff --stat "$(git merge-base HEAD origin/main)"` — expect changes
   confined to the file map above.

`just verify` does not run the hooks, and CI runs both, so step 2 is not optional.
