# Implementation plan: live verification and derived staleness

Goal: make the live runner's observations honest, record them in a small closed-shape
format, and derive when they go stale — as a warning on pull requests and a failure on the
weekly run.

Architecture: `scripts/live_test/observation.py` holds the result vocabulary, failure
classification and expected outcomes. `scripts/live_test_runner.py` keeps `RunState`, gains
the runtime argument check, `record_verified`, and observation emission.
`scripts/check_capability_inventory.py` gains maturity format 2, the closure fingerprint,
derived states and the report. One `justfile` recipe and one `ci.yml` job expose the report.
Nothing under `src/hmc_mcp/` changes.

Tech stack: Python 3.11, standard library, the existing `fastmcp` client. No new dependency.

Spec: [`../specs/2026-09-06-live-verification-staleness-design.md`](../specs/2026-09-06-live-verification-staleness-design.md).
Decision: [ADR 0127](../../adr/0127-derived-live-verification-staleness.md).

Expected implementation size: 1,150–1,400 changed lines (L) — summed from the file map:
`observation.py` (~120), runner (~200), workflow modules (~180: 23 corrections, 17
migrations, 3 conversions), validator (~260 net of ~90 deleted), CI and justfile (~45),
two test modules (~420), documents and config (~60). Below cycle 1's estimate because the
reason-code registry, environment block, evidence artifact and redaction gate are gone.

## Global Constraints

Transcribed from the spec:

- Python 3.11 floor; CI runs 3.11–3.14 on amd64 and arm64.
- No new runtime or development dependency.
- Every `uv run` in `justfile` passes `--no-sync`.
- `ci.yml` keeps exactly one `permissions:` block, `contents: read`; no `workflow_dispatch`;
  no `fetch-depth`; every action pinned to the SHAs in `tests/test_ci_pipeline.py::ACTION_PINS`.
- Guardrails: `just verify`, then `uv run --no-sync prek run --all-files`.

Repository conventions: never a bare `uv sync`/`uv run`/`uv add`; diff against the merge
base; a new recipe outside `static` needs no prek hook; ADR filenames match
`NNNN-lowercase-kebab-slug.md` and the H1 announces the same number; there is no ADR index.

## Resume facts

Branch `feat/trustworthy-live-evidence-623`, base `main`, worktree
`/home/dave/src/hmc-mcp-worktrees/feat-trustworthy-live-evidence-623`. Issue #623, cycle-2
scope annotation `q623-d32263bc` (comment 5562652705). Baseline `ded24a77`.

## Deferrals carried into this plan

None.

## File map

Created: `scripts/live_test/observation.py` (vocabulary, `CallFailure`, `Assertion`,
`ExpectedOutcome`, `classify_failure`).

Modified:

| Path | Change |
|---|---|
| `scripts/live_test_runner.py` | runtime argument check; `CallFailure` on failure; `record` → `observed`; `record_verified`; `record_with_expected`; `test_user_uuid` artifact; env keys; observation emission |
| `scripts/live_test/*.py` (twelve modules) | 23 dispatch corrections; 17 `record_with_expected` migrations; 3 `record_verified` conversions |
| `scripts/check_capability_inventory.py` | format 2 (own version); observation shape; `closure_fingerprint`; derived states; `--verification-report`, `--fail-on-stale`; delete `implementation_fingerprint` and its error block |
| `docs/capabilities/maturity.json` | `"format_version": 2` |
| `docs/capabilities/README.md` | maturity section rewritten for format 2 and the report |
| `docs/adr/0126-*.md` | Status banner only |
| `justfile` | `verification-report *ARGS` |
| `.github/workflows/ci.yml` | `verification-report` job |
| `.gitignore` | `test-results*.json` |
| `.env.example` | two `LIVE_TEST_ENV_*` keys |
| `tests/test_live_runner.py` | argument guard; runner tests |
| `tests/scripts/test_check_capability_inventory.py` | format 2, closure, report tests; **two** fixture bumps (`:95`, `:177`) plus one error-text update (`:381`) — see Task 4 step 1 |
| `tests/test_ci_pipeline.py` | job test; counts 4→5 and 5→6; recipe assertion |
| `CHANGELOG.md` | one entry |

---

## Task 1: Structured outcomes

Creates `scripts/live_test/observation.py`. Modifies `scripts/live_test_runner.py`,
`tests/test_live_runner.py`.

### Interfaces

Provides:

```python
# scripts/live_test/observation.py
RESULTS = frozenset({"observed", "passed", "failed", "skipped"})
CLEANUP = frozenset({"not-run", "not-required", "failed", "passed"})
# CLEANUP's "not-run" is a cleanup disposition (cleanup did not run) and is unrelated to
# ADR 0126's deleted `not-run` observation shape. RESULTS deliberately has no such member.
ASSERTION_ID = re.compile(r"\A[a-z][a-z0-9-]{2,63}\Z")
SCENARIO_ID = re.compile(r"\Ast\d+-[a-z0-9-]+\Z")

@dataclass(frozen=True)
class CallFailure:
    exception_type: str
    message: str
    traceback_text: str
    http_status: int | None
    denied: bool

@dataclass(frozen=True)
class Assertion:
    id: str      # must match ASSERTION_ID; ValueError otherwise
    holds: bool

@dataclass(frozen=True)
class ExpectedOutcome:
    reason: str
    error_codes: frozenset[str] = frozenset()
    denial: bool = False
    def matches(self, failure: CallFailure) -> bool: ...   # whole-token search of failure.message

def classify_failure(exc: BaseException) -> CallFailure: ...
```

`_HTTP_STATUS_RE = re.compile(r"\bHTTP (\d{3})\b")`;
`_DENIAL_RE = re.compile(r" is not permitted (?:on .+ )?by access policy ")` — the segment is
optional so the three `target_scope.py` templates that omit it (`:71`, `:76`, `:80`) still
classify as denials. `ExpectedOutcome`
raises `ValueError("an expected outcome must name an error code or a denial")` when both
are empty. `matches` is
`any(re.search(rf"\b{re.escape(c)}\b", failure.message) for c in self.error_codes) or (self.denial and failure.denied)`.

On `RunState`:

```python
def record(self, subtask, tool, status, data, note="") -> None          # unchanged signature; result "observed"/"failed"
def record_with_expected(self, subtask, tool, status, data, expected: Sequence[ExpectedOutcome]) -> None
def record_verified(self, subtask, tool, *, operation: str, scenario: str,
                    assertions: Sequence[Assertion], cleanup: str, data: Any) -> None
```

`record_verified` appends to `self.results` a row with `result` `passed` when every
`holds` and `cleanup in {"passed","not-required"}`, else `failed`, and additionally appends
to `self.observations` (new `list[dict]` field) the catalog-shaped observation minus
`tested_commit`, `closure_fingerprint`, `hmc_release`, `hardware_family` — Task 5 fills
those four at emission, and only those four. Everything else in `ATTEMPTED_KEYS` is written
here, **including `"channel": "live"`**, whose only legal value on this path is `live`; it is
neither an emission field nor derivable later, so leaving it to Task 5 would produce a record
the validator rejects. `id` is `f"st{subtask}-{tool}"` with `_` → `-` and any ` (` suffix
dropped; uniqueness is checked at emission. `observed_at` is
`datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")`.

### Verification

- **`classify_failure` reads status and denial from the message only.**
  Mode: focused-test. `test_classify_failure_reads_the_message_not_the_traceback`: an
  exception whose message carries `HTTP 400` and whose traceback text carries `HTTP 500`
  yields `http_status == 400`. Red: `ImportError` before the module exists. Green:
  `uv run --no-sync pytest tests/test_live_runner.py -q -k classify_failure`.
- **A real ADR 0038 denial classifies as `denied`.** Mode: focused-test.
  `test_a_real_access_policy_denial_classifies_as_denied` composes the app with
  `compile_legacy_policy(TOOL_SECURITY, (DEFAULT_CONNECTION_TOKEN,), include_arbitrary_command=False)`,
  calls `hmc_list_systems` with `profile="not-granted"` through `fastmcp.Client`, catches
  the `ToolError`, and asserts `classify_failure(exc).denied is True`. Verified on the branch:
  that call raises `ToolError: … hmc_list_systems is not permitted on connection
  'not-granted' by access policy 'legacy-equivalent' …`. (Calling an *unregistered* tool
  such as `hmc_run_command` under this policy raises `Unknown tool`, which is not a denial —
  do not use it.) Red: `denied is False` before `_DENIAL_RE`. Green: `-k denial`.
- **A target-scope denial without an `on <targets>` segment also classifies.** Mode:
  focused-test. `test_a_target_scope_denial_classifies_as_denied` over the rendered
  `target_scope.py:76` template — `"<tool> is not permitted by access policy <policy>: the
  <argument> argument …"` — asserting `denied is True`. Red: against the
  `on .+`-requiring pattern, which returns `False` for three of the four target-scope
  templates.
- **`ExpectedOutcome` matches whole tokens in the message.** Mode: focused-test.
  `test_expected_outcome_matches_whole_tokens_in_the_message`: `REST000E` matches
  `"… REST000E …"`, not `"… REST000EX …"`, not a traceback line. Red: `ImportError`.
  Green: `-k whole_tokens`.
- **`ExpectedOutcome` with neither code nor denial is rejected.** Mode: focused-test.
  `test_expected_outcome_requires_a_code_or_a_denial`, `pytest.raises(ValueError)`.
- **Unmatched failure records `failed`, never `skipped`.** Mode: focused-test.
  `test_an_unmatched_failure_is_recorded_as_failed`. Red: `AttributeError` before
  `record_with_expected`. Green: `-k unmatched`.
- **`record` cannot yield `passed`.** Mode: focused-test.
  `test_record_always_yields_a_non_promoting_result`. Red: the row has no `result` key.
- **`record_verified` yields `failed` on a false assertion and on failed cleanup.** Mode:
  focused-test. Two tests. Red: `AttributeError`.
- **`Assertion` rejects an id outside the pattern.** Mode: focused-test.
  `test_assertion_id_must_be_a_closed_shape_token`, `pytest.raises(ValueError)` on
  `"entry UUID equals job id"`.

### Steps

1. Create `scripts/live_test/observation.py` with the Interfaces content and the docstring
   `"""Result vocabulary, failure classification and expected outcomes for live tests."""`.
2. In `scripts/live_test_runner.py` import `Assertion, CallFailure, ExpectedOutcome,
   classify_failure, CLEANUP, RESULTS, SCENARIO_ID` from `live_test.observation`.
3. In `RunState.call`, change the `except` branch to `return "FAIL", classify_failure(exc)`.
4. Add `observations: list[dict[str, Any]] = field(default_factory=list)` to `RunState`.
5. In `record`, map the status to the result explicitly — `"failed"` when
   `status == "FAIL"`, `"skipped"` when `status == "SKIP"`, `"observed"` otherwise. The
   `SKIP` arm is what gives the `skipped` vocabulary entry a producer: `RunState.skip`
   delegates to `record(subtask, tool, "SKIP", None, reason)`
   (`scripts/live_test_runner.py:501-503`), so without it all 53 plain `skip()` rows would be
   stamped `observed` and `skipped` would be dead in the table. `record_with_expected` reaches
   the same arm when a declared `ExpectedOutcome` matches. None of the three values promotes;
   only `record_verified` reaches `passed`.
   When `data` is a `CallFailure`, persist `_redact_failure_text(data.message)` as the
   entry's `data` and drop the traceback; the existing redaction helpers stay in the runner.
6. Replace `record_expected_or_real` with `record_with_expected` per Interfaces; an
   `InvalidDispatch` failure (Task 2) records `failed` before any declaration is consulted.
7. Add `record_verified` per Interfaces; raise `ValueError` on empty `assertions`,
   `cleanup not in CLEANUP`, or `scenario` not matching `SCENARIO_ID`.
8. Add the eight tests. Run `uv run --no-sync pytest tests/test_live_runner.py -q`; expect
   failures only in `record_expected_or_real` callers (Task 3 migrates them) — count them.
9. Commit `feat: structure live-test outcomes and postconditions`.

### Acceptance criteria

`rg -n "REST\\\\d" scripts/` returns nothing; no `record`-produced row has
`result: "passed"`; `classify_failure` never reads `traceback_text` for classification.

---

## Task 2: Dispatch argument guard

Modifies `scripts/live_test_runner.py`, `tests/test_live_runner.py`.

### Interfaces

```python
# scripts/live_test_runner.py
def _dispatch_problems(tool: str, keywords: Iterable[str],
                       schemas: Mapping[str, Mapping[str, Any]]) -> tuple[str, ...]: ...
# RunState gains: schemas: dict[str, dict[str, Any]] = field(default_factory=dict)
```

```python
# tests/test_live_runner.py
def _dispatched_calls(source: str) -> list[tuple[int, str, tuple[str, ...]]]: ...
async def _served_schemas() -> dict[str, dict[str, object]]: ...
```

`_dispatched_tool_names` is reimplemented as `{t for _, t, _ in _dispatched_calls(s)}` so
the three #485 tests pass unchanged. `_served_schemas` composes the app exactly as
`scripts/live_test_runner.py:819-826` does and reads `client.list_tools()`.

### Verification

- **Every dispatch's keywords match the served schema.** Mode: focused-test.
  `test_every_dispatched_argument_matches_the_served_schema`. Red: names exactly 23
  dispatches on the current modules (the measured baseline; the message lists `file:line`).
  Green after Task 3: `-k served_schema`.
- **A `**` splat fails the guard.** Mode: focused-test.
  `test_argument_guard_refuses_a_splat_it_cannot_read`,
  `pytest.raises(AssertionError, match="cannot read")`.
- **The guard bites on a synthetic bad dispatch.** Mode: focused-test.
  `test_argument_guard_reports_an_unknown_keyword` over
  `state.call(client, "hmc_get_job", job_uuid="x")`; problem names `job_uuid` and `job_id`.
- **An invalid dispatch never reaches the client and records `invalid-arguments`.** Mode:
  focused-test. `test_an_invalid_dispatch_never_reaches_the_client` with a stub client whose
  `call_tool` raises `AssertionError`.

### Steps

1. Add `_dispatch_problems`: unregistered tool → `f"{tool} is not a registered tool"`;
   unknown keyword → `f"{tool}: unknown argument {name}"`; missing required →
   `f"{tool}: missing required argument {name}"`.
2. Add `schemas` to `RunState`; at the top of `call`, when `self.schemas` is non-empty and
   problems exist, return `("FAIL", CallFailure("InvalidDispatch", "; ".join(problems), "",
   None, False))`.
3. In `main`, after the client opens:
   `state.schemas = {t.name: t.inputSchema for t in await client.list_tools()}`.
4. Add `_dispatched_calls`, `_served_schemas`, and the four tests. A keyword whose
   `arg is None` — the `**mapping` splat — is **reported as one problem**, not raised on:
   `f"{path}:{lineno} dispatches arguments this guard cannot read — name them"`. The raising
   behaviour is exercised only by `test_argument_guard_refuses_a_splat_it_cannot_read` over a
   synthetic source string.

   This ordering is load-bearing. Exactly one splat exists in the tree —
   `**_baseline_provision_resources(state)` at `scripts/live_test/provisioning.py:175`, inside
   the `state.call` that begins at `:163` — and it is not removed until Task 3. A guard that
   raised on it would abort before enumerating anything, so the count in step 5 could never be
   observed and Task 3's per-module reruns would stay dark until that one site was rewritten.
5. Run `-k served_schema`; **expect 23 problems at 23 sites** — the sites in Task 3's table.
   `provisioning.py:163` is one of them; because its arguments are behind the splat it
   reports as unreadable at this commit rather than naming its unknown and missing keywords,
   and it reports its enumerated form only after Task 3 step 3 rewrites the sweep. Confirm
   the site list equals the table before proceeding.
6. Commit `test: guard live-test dispatch arguments against the served schema`.

### Acceptance criteria

The three #485 guard tests pass unchanged; the new guard reports exactly 23 problems at the
23 sites in Task 3's table at this commit.

---

## Task 3: Correct the dispatches and migrate the workflow modules

Modifies the twelve `scripts/live_test/*.py` modules, `scripts/live_test_runner.py`
(`LiveTestArtifacts`, `_decode_artifacts`), `tests/test_live_runner.py`.

### The 23 corrections

| Site | Tool | Wrong | Correct |
|---|---|---|---|
| `metrics.py:81` | `hmc_get_job` | `job_uuid=` | `job_id=job_uuid` |
| `metrics.py:91` | `hmc_wait_for_job` | `job_uuid=` | `job_id=job_uuid` |
| `lpar.py:126`, `network.py:172`, `provisioning.py:78` | `hmc_delete_lpar` | omits system | add `system_name_or_uuid=config.system_name` |
| `provisioning.py:37` | `hmc_provision_lpar` | flat `port_vlan_id`, `vios_uuid`, `vios_partition_id`, `vios_slot`, `storage_name`, `desired_memory` (six unknowns; `adapters`, `storage` missing) | `adapters={"port_vlan_id":…,"vios_partition_id":…,"vios_slot":…}`, `storage={"vios_uuid":…,"storage_name":…}`, `resources={"desired_memory":…}` — this site passes no `storage_kind` or `vg_uuid`, and `ProvisionStorage` defaults `kind="VirtualDisk"` and `vg_uuid=None`, so neither key is written |
| `provisioning.py:163` | `hmc_provision_lpar` | flat, plus `storage_kind`, `vg_uuid` and `**_baseline_provision_resources(state)` at `:175` | same nesting, but `storage=` carries all four keys `{"vios_uuid":…,"storage_name":…,"kind":…,"vg_uuid":…}`; the helper's mapping becomes the `resources=` value and the splat goes away |
| `users.py:27` | `hmc_create_user` | `name=`, `taskrole=` | `console_uuid=artifacts.console_uuid`, `user_id=config.test_user`, `password=_TEST_USER_PASSWORD`, `associated_task_role="viewer"` |
| `users.py:45,72,91` | `hmc_list_users` | omits console | add `console_uuid=artifacts.console_uuid` |
| `users.py:56` | `hmc_modify_user` | `name=` | `console_uuid=…`, `user_profile_uuid=artifacts.test_user_uuid` |
| `users.py:67` | `hmc_delete_user` | `name=` | `console_uuid=…`, `user_profile_uuid=artifacts.test_user_uuid` |
| `vmedia.py:737,833` | `hmc_read_lpar_boot_order` | `lpar_uuid=` | `lpar_name_or_uuid=lpar_uuid` |
| `vmedia.py:750,815,951` | `hmc_set_lpar_boot_order` | `lpar_uuid=` | `lpar_name_or_uuid=lpar_uuid` |
| `vmedia.py:823` | `hmc_clear_lpar_boot_order` | `lpar_uuid=` | `lpar_name_or_uuid=lpar_uuid` |
| `vmedia.py:763` | `hmc_power_on_lpar` | `timeout=120` | `timeout_seconds=120` |
| `vmedia.py:579,798,992` | `hmc_unmount_optical_media` | `mapping_uuid=` | `lpar_name_or_uuid=`, `media_name=` |

New state: `test_user_uuid: str | None = None` on `LiveTestArtifacts` and in
`_ARTIFACT_NULLABLE_STRINGS`; captured from the post-create `hmc_list_users` entry whose
`UserID` equals `config.test_user` via `live_test.results.resource`; when absent, skip
modify and delete with reason `"user profile UUID not found after create"`.
`_decode_artifacts` defaults a missing `test_user_uuid` key to `None` so results documents
written before this change still restore. `vmedia.py:992` reads `lpar_name_or_uuid` and
`media_name` from each mapping entry and skips an entry lacking either.

### Verification

- **All 23 corrected.** Mode: focused-test. Task 2's `-k served_schema`, now green.
- **Existing workflow behavioural tests still describe the call sequence.** Mode:
  focused-test. `test_vmedia_workflows_execute_their_behavioral_contracts` and
  `test_sriov_orchestrator_*`; red on the renames first. Green: whole module.
- **User path skips without a profile UUID.** Mode: focused-test.
  `test_user_administration_skips_without_a_profile_uuid`; red: stub receives
  `hmc_modify_user`.
- **Old results document restores without `test_user_uuid`.** Mode: focused-test.
  `test_restore_artifacts_tolerates_a_results_document_without_test_user_uuid`; red:
  `ValueError("results artifact fields do not match")`.
- **Job assertions reject a failed job status, on both return shapes.** Mode: focused-test.
  `test_job_scenarios_fail_on_a_non_successful_status`, parametrised twice: the
  `hmc_get_job` stub returns the raw entry `{"UUID": "j", "Status": "FAILED_BEFORE_COMPLETION"}`;
  the `hmc_wait_for_job` stub returns the normalized outcome
  `{"job_id": "j", "found": True, "timed_out": False, "status": "FAILED_BEFORE_COMPLETION", "error": "…", "job": {…}, "job_href": None}`.
  In each case the row is `failed` and `job-status-successful` is not among the held
  assertions. Red: before conversion both rows are `observed`.
- **A scenario's declared assertion ids are pinned.** Mode: focused-test.
  `test_scenarios_declare_their_expected_assertion_ids`, using the same AST reader Task 2
  builds to collect `record_verified` call sites, against the literal
  `{"st12-job-inspection": {"job-found", "job-identity-matches", "job-status-successful"},
  "st1-console-identity": {"console-uuid-present"}}`.

  The closure covers only `src/hmc_mcp/` and deliberately excludes `scripts/`, so neither
  staleness trigger sees the harness change. Without this pin, deleting
  `Assertion("job-status-successful", …)` from `metrics.py` leaves a committed observation
  that still lists the id, still matches the recomputed closure hash, and still reports
  `current` — a reader concludes a postcondition was checked that the harness no longer
  checks. The test fails in the pull request that changes the assertions, which is where a
  reviewer can still see which committed observations are about to become misleading. Red:
  remove an id from a scenario and the suite stays green.
- **`hmc_wait_for_job`'s real serialized shape normalizes.** Mode: focused-test.
  `test_wait_for_job_outcome_normalizes_from_the_served_shape`. The scripted stub returns a
  `dict`, so it cannot catch a normalizer that only handles mappings — this arm builds a
  `FastMCP` server holding one tool annotated `-> JobOutcome`, calls it through
  `fastmcp.Client`, and asserts both that `result.data` is not a `dict` (pinning the
  assumption to the installed FastMCP, so a future version that changes it fails here rather
  than silently in the field) and that `_as_outcome` returns a `JobOutcome` with the right
  `status`. Red: against an `isinstance(data, dict)`-only normalizer, which returns `None`.

### Steps

1. Apply the table module by module, re-running `-k served_schema` after each.
2. Add `test_user_uuid` capture and guard; add the `_decode_artifacts` default.
3. Rewrite the `vmedia.py:992` sweep.
4. Replace the 17 `record_expected_or_real` sites with `record_with_expected` and
   module-level `ExpectedOutcome` constants. `_REST000E_SKIP` (`users.py:20`) becomes
   `ExpectedOutcome(reason="HmcUser REST not supported on this HMC", error_codes=frozenset({"REST000E"}))`;
   `"400"` and the prose entries are dropped.
5. Convert the two job scenarios and `hmc_get_console_info` to `record_verified`. In
   `metrics.py`: `from hmc_mcp.jobs import SUCCESSFUL_JOB_STATUSES, JobOutcome, job_outcome`
   (all three are exported by `src/hmc_mcp/jobs/__init__.py:9-22`; `job_outcome(requested_id:
   str, job: dict | None) -> JobOutcome` is defined at `src/hmc_mcp/jobs/core.py:109`).
   For `hmc_get_job`, `outcome = job_outcome(job_uuid, data if isinstance(data, dict) else None)`.
   For `hmc_wait_for_job`, `data` is **not** a `dict`: the tool is annotated `-> JobOutcome`
   (`src/hmc_mcp/server_tools/jobs.py:112`), so FastMCP serves an unwrapped seven-property
   `outputSchema` and `result.data` is a generated pydantic model. Normalize by field name,
   not by mapping test:

   ```python
   def _as_outcome(data: Any) -> JobOutcome | None:
       names = [f.name for f in fields(JobOutcome)]
       if isinstance(data, dict):
           source = data
       elif all(hasattr(data, n) for n in names):
           source = {n: getattr(data, n) for n in names}
       else:
           return None
       try:
           return JobOutcome(**{n: source[n] for n in names})
       except (KeyError, TypeError):
           return None
   ```

   A `None` return records a failed observation. Assertions for both:
   `Assertion("job-found", outcome.found)`,
   `Assertion("job-identity-matches", outcome.job_id == job_uuid)`,
   `Assertion("job-status-successful", outcome.status in SUCCESSFUL_JOB_STATUSES)`;
   cleanup `not-required`; scenario `st12-job-inspection`. For `hmc_get_console_info`:
   `Assertion("console-uuid-present", bool(isinstance(data, dict) and (data.get("uuid") or data.get("UUID"))))`;
   scenario `st1-console-identity`.
6. Add the four new tests. Run the module; expect green. Run `just lint`, `just typecheck`.
7. Commit `fix: correct 23 live-test dispatches and assert job outcomes`.

### Acceptance criteria

`rg -n "expected_fail_substrings|record_expected_or_real" scripts/ tests/` is empty;
`-k served_schema` green.

---

## Task 4: Maturity format 2, closure fingerprint, derived states and the report

Modifies `scripts/check_capability_inventory.py`, `docs/capabilities/maturity.json`,
`tests/scripts/test_check_capability_inventory.py`.

### Interfaces

```python
MATURITY_FORMAT_VERSION = 2
STALE_AFTER_DAYS = 90
ATTEMPTED_KEYS = {"id","channel","result","scenario","tested_commit","observed_at",
                  "hmc_release","hardware_family","cleanup","closure_fingerprint","assertions"}
# No NOT_RUN_KEYS: format 2 has one observation shape (see spec §Observation record).
HMC_RELEASE = re.compile(r"\AV\d+R\d+(?:M\d+)?\Z")   # V10R3
HARDWARE_FAMILY = re.compile(r"\APOWER\d+\Z")        # POWER10
ASSERTION_ID = re.compile(r"\A[a-z][a-z0-9-]{2,63}\Z")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

STALE_REASONS = ("closure-changed", "age-exceeded")

@dataclass(frozen=True)
class OperationState:
    state: str                  # unrecorded | unevidenced | stale | failed | current
    reason: str | None = None   # a STALE_REASONS member when state == "stale", else None

def closure_fingerprint(repo_root: Path, handler_module: str) -> str: ...
def closure_paths(repo_root: Path, handler_module: str) -> list[Path]: ...
def derive_states(records, registry, repo_root, now: datetime) -> dict[str, OperationState]: ...
def verification_report(states: Mapping[str, OperationState], *, fail_on_stale: bool) -> int: ...
```

`derive_states` returns the reason alongside the state because the report cannot recover it
otherwise: the two triggers are distinct by construction, and a bare `str` state discards
which one fired — leaving `::warning::<operation> is stale: <reason>` unimplementable without
recomputing fingerprints inside the report. When both triggers apply, `closure-changed` wins,
since it is the specific fact and the age ceiling is the backstop.

The per-operation listing is emitted **sorted by operation id**, so the CI job summary is
stable run to run. Registry order and catalog order are both available and neither is
guaranteed stable against the other — `discover_registry()` yields tools sorted by tool name,
while `operations.json` carries its own order.

`_validate_versions` checks `corpora.json`, `rows.json`, `operations.json` at 1;
`maturity.json` is checked separately at `MATURITY_FORMAT_VERSION`. `implementation_fingerprint`,
`_implementation_paths`, and the error block at `:940-955` are deleted. `main` gains
`--verification-report` and `--fail-on-stale`; the report runs after validation and only
when validation passed.

### Verification

- **Format 1 rejected; format 2 accepted.** Mode: focused-test.
  `test_maturity_format_one_is_rejected` (red: accepted today), with the two maturity
  fixtures (`:95`, `:177`) bumped to 2 and the other eleven `format_version` sites left at 1.
  Green: whole module.
- **The `not-run` shape is gone.** Mode: focused-test.
  `test_a_not_run_observation_is_rejected`: an observation with `result: "not-run"`, or
  carrying any of `reason`, `prerequisites`, `obligation`, fails the exact-key-set check.
  Red: accepted today by `_validate_not_run`.
- **The observation has an exact key set.** Mode: focused-test.
  `test_observation_key_sets_are_exact`, parametrised over one extra and one missing key.
- **Environment strings admit their grammar and nothing else.** Mode: focused-test.
  `test_environment_values_reject_private_identifiers`, parametrised: `"V10R3"` and
  `"V10R3M1"` accepted for `hmc_release`, `"POWER10"` for `hardware_family`; every one of
  `"hmc01.lab.example.com"`, `"0644C7T"`, `"U78CB.001.WZS0044-P1-C2"`, `"lab-hmc-3"`,
  `"10.1.2.3"` rejected for both. These five are the identifier classes the repository's
  privacy rule names, and all five pass a `[A-Za-z0-9 ._-]{0,39}` class with an IPv4
  rejection — which is why that class was replaced rather than patched. Red: accepted today.
- **Committed hashes, assertion ids and cleanup are validated on the way in.** Mode:
  focused-test. `test_attempted_observation_fields_are_pattern_bound`, parametrised over a
  `tested_commit` that is not 40 hex, a `closure_fingerprint` that is not 64 hex, an
  `assertions` element that is not an `ASSERTION_ID`, a duplicated assertion id, and a
  `failed` observation whose `cleanup` is outside `CLEANUP` — each rejected. The runner bounds
  these fields on the way out, but `maturity.json` is hand-copied and hand-editable, so the
  validator is the only check that sees a hand-authored record. Red: all five accepted.
- **Closure fingerprint tracks the closure and nothing else.** Mode: focused-test.
  `test_closure_fingerprint_changes_with_an_imported_module_only` over a `tmp_path` package
  `src/hmc_mcp/{__init__,a,b,c}.py` where `a` holds `from .b import thing`: editing `b`
  changes `a`'s hash, editing `c` does not. Red: `ImportError`.
- **Every import form that reaches `src/hmc_mcp/` enters the closure.** Mode: focused-test.
  `test_closure_covers_each_import_form`, parametrised over the three forms in the spec —
  `from .b import thing`, `from . import b`, and `import hmc_mcp.b` — each in its own
  `tmp_path` package; every one must put `b` in `closure_paths(a)`. Red for the
  `from . import b` case against a walk that only reads `ImportFrom.module`, which is `None`
  there.
- **The closure does not collapse to the whole package.** Mode: focused-test.
  `test_closure_excludes_function_body_imports`, over the **real tree**, not `tmp_path`:
  `closure_paths(ROOT, "hmc_mcp.server_tools.permissions")` must not contain
  `src/hmc_mcp/cli.py` and must hold fewer than 20 files. Every `tmp_path` test in this task
  passes under either traversal reading because their `__init__.py` files are empty, so this
  is the only arm that bites. `src/hmc_mcp/__init__.py:15` imports `.cli` inside `main()` and
  sits on every resolution path, so an `ast.walk` implementation yields 179 of 180 files for
  all three handler modules measured (51, 75 and 7 respectively under the module-body rule).
  Red: 179 files, `cli.py` present.
- **A package import pulls in the package and its modules.** Mode: focused-test.
  `test_closure_resolves_packages`: `closure_paths` for `hmc_mcp.server_tools.jobs` contains
  both `src/hmc_mcp/jobs/__init__.py` and `src/hmc_mcp/jobs/core.py`. `server_tools/jobs.py:9`
  is `from ..jobs import JobOutcome` — a package, and a class rather than a module — so a
  module-file-only walk finds neither, and a change to `SUCCESSFUL_JOB_STATUSES` in `core.py`
  would not invalidate the observations that assert against it. Red: both paths absent.
- **The real `lpar.delete` closure holds its sibling modules.** Mode: focused-test.
  `test_lifecycle_closure_includes_bare_relative_imports`: `closure_paths` for
  `hmc_mcp.server_tools.lpar.lifecycle` contains `lifecycle_boot.py` and
  `lifecycle_create.py`. This is the repository instance the parametrised test abstracts;
  `src/hmc_mcp/server_tools/lpar/lifecycle.py:27-28` imports both as `from . import …`, and
  without it an edit to either leaves `hmc_delete_lpar` and `hmc_power_on_lpar` falsely
  current. Red: both paths absent.
- **The walk stays inside the package.** Mode: focused-test.
  `test_closure_containment`, two arms over `tmp_path`: a symlinked `.py` under the package
  is skipped rather than read and hashed (otherwise a link pointing outside the repository
  makes the fingerprint machine-dependent, so the runner and CI never agree and the
  observation reads `stale` forever), and a `from ....x import y` whose `level` exceeds the
  package depth is skipped rather than resolving outside. Red: both traverse.
- **Each derived state.** Mode: focused-test. `test_derived_states`, parametrised over the
  five states with a fixed `now`. Red: `ImportError`.
- **A re-run replaces an operation's observation.** Mode: focused-test.
  `test_re_validation_replaces_the_previous_observation`: emitting `st12-hmc-get-job` twice
  leaves one observation, and `just capability-inventory` reports no duplicate-id error. Red:
  two observations and `id must be catalog-wide unique`.
- **The listing carries the implementation state.** Mode: focused-test.
  `test_report_line_carries_implementation_state`: the `sriov.set_mode` line reads
  `partial current`, not `current`. Red: implementation state absent.
- **Report summary line and exit codes.** Mode: focused-test.
  `test_verification_report_summary_and_fail_on_stale`: exit 0 with a stale operation
  unless `--fail-on-stale`, then 1; summary line exact.
- **Staleness is never a validation error.** Mode: focused-test.
  `test_a_stale_observation_is_not_an_error`: `validate_inventory` returns no error for a
  mismatched fingerprint. Red: today returns `stale implementation fingerprint`.

### Steps

1. Change `_validate_versions` and add the maturity version check; set maturity.json to 2.
   **Only `maturity.json` moves.** The module holds 13 `format_version` occurrences and just
   two of them write a maturity fixture: `:95` (`_minimal_inventory`) and `:177`
   (`_write_maturity`) — bump those to 2. Update the expected text at `:381` from
   `"maturity.json: format_version must be integer 1"` to `… integer 2`. Every other
   occurrence stays at 1: `:35` (`corpora.json`), `:73` (`rows.json`), `:90`, `:195`, `:261`,
   `:303` (`operations.json`/`corpora.json`), and `:224`, which is the duplicate-key literal
   `'{"format_version":1,"format_version":1}'` and is not a version fixture at all. Bumping
   them would contradict this task's own `_validate_versions` contract, which keeps the other
   three catalogs at 1, and would turn the module red.
2. Replace `_validate_observation` and its helpers with the single-shape validator per
   Interfaces; keep `_validate_implementation` and `_scope_identity` whole. **Delete
   `_validate_not_run` entirely**, along with `_validate_evidence_lists` and every check
   reading `reason`, `prerequisites`, `obligation`, `assertions` (in its old form), `cleanup`,
   `observed_at`, `implementation_revision`, `deployed_revision`, `provenance` or
   `implementation_fingerprint`. Format 2 has one observation shape; `result` no longer admits
   `not-run`, so nothing reaches those checks. `scenario` becomes a `SCENARIO_ID` string match
   in place of the `{id, description}` object check.
3. Add `closure_paths` per the spec's *Closure fingerprint*. **Do not reach for `ast.walk`**:
   traverse the module body plus the bodies of module-level `If`/`Try` statements, and never
   descend into `FunctionDef`/`AsyncFunctionDef`/`ClassDef`. `ast.walk` yields 179 of 180
   files for every handler and silently reinstates ADR 0126's repository-wide fingerprint.
   Add
   `closure_fingerprint` (length-prefixed SHA-256 over sorted `(relative path, bytes)`).
4. Add `derive_states` and `verification_report`; the report prints one line per operation
   `verification: <operation> <state>` and the summary from the spec; when
   `os.environ.get("GITHUB_ACTIONS")`, print `::warning::<operation> is stale: <reason>`
   per stale operation and append a Markdown table to `GITHUB_STEP_SUMMARY` if set.
5. Delete `implementation_fingerprint`, `_implementation_paths`, and the error block.
6. Wire `--verification-report` and `--fail-on-stale` in `main`.
7. Add the seven tests; bump the two maturity fixtures per step 1. Run the module; expect
   green. Run
   `just capability-inventory`; expect the existing two lines.
8. Commit `feat: derive live-verification staleness from the import closure`.

### Acceptance criteria

`rg -n "implementation_fingerprint" scripts/ tests/ docs/capabilities/` is empty;
`just capability-inventory` green; `just verification-report` prints 156 operation lines
and, on today's catalog (two records, both with empty evidence), the summary
`verification coverage: 154 unrecorded, 2 unevidenced, 0 stale, 0 failed, 0 current (of 156)`.

---

## Task 5: Runner emission and environment keys

Modifies `scripts/live_test_runner.py`, `.env.example`, `.gitignore`,
`tests/test_live_runner.py`.

### Interfaces

```python
ENVIRONMENT_KEYS = ("LIVE_TEST_ENV_HMC_RELEASE", "LIVE_TEST_ENV_HARDWARE_FAMILY")
def _read_environment(path: Path) -> tuple[str, str] | None: ...      # both, or None; lone → ValueError
def _tree_is_clean(repo_root: Path) -> bool: ...                        # git status --porcelain -- src scripts
def _emit_observations(state: RunState, path: Path, environment, repo_root) -> bool: ...
```

`_emit_observations` fills `tested_commit` (`git rev-parse HEAD`), `hmc_release`,
`hardware_family`, and `closure_fingerprint` — `check_capability_inventory.closure_fingerprint(repo_root, module)`
where `module` is the `discover_registry()` record's `handler` with its last dotted component
removed (so the composed `hmc_effective_permissions` record resolves to
`hmc_mcp.server_tools.permissions`); `check_capability_inventory` is a plain
`import check_capability_inventory`, resolvable because `scripts/` is `sys.path[0]` both
when the runner runs as a script and when `tests/test_live_runner.py:22` loads it — checks
`id` uniqueness, and writes
`json.dumps([{"operation": …, "observation": {…}}, …], indent=2)` through `_write_results`,
which takes the document as a string. It returns `False` and prints why on a dirty tree,
missing environment, or no observations.

### Verification

- **Lone environment key is a configuration error.** Mode: focused-test.
  `test_a_lone_environment_key_is_rejected`, `pytest.raises(ValueError)`.
- **`from_env_file` accepts the extended example.** Mode: focused-test. Existing
  `test_live_config_reads_the_complete_example_and_ignores_exports`; red: add the keys to
  `.env.example` first → `unknown setting LIVE_TEST_ENV_HMC_RELEASE`.
- **Nothing is written on a dirty tree or missing environment.** Mode: focused-test.
  `test_observations_are_not_emitted_from_a_dirty_tree` (monkeypatch `_tree_is_clean`) and
  `test_observations_are_not_emitted_without_environment`.
- **Emitted objects are catalog-shaped.** Mode: focused-test.
  `test_emitted_observations_validate_against_the_catalog_shape`: feed the emitted objects
  through `check_capability_inventory`'s observation validator with an empty error list.
- **A lone environment key fails at startup, not after the run.** Mode: focused-test.
  `test_a_lone_environment_key_exits_before_the_run`: `_run_from_arguments` returns 1 and no
  client is opened. Red: the `ValueError` surfaces from `_emit_observations` after
  `_write_results`, discarding the run summary.
- **Emission refuses an unignored destination.** Mode: focused-test.
  `test_emission_refuses_a_path_git_does_not_ignore`: with `--results-file evidence.json`,
  `_emit_observations` writes nothing and prints the path. Red: writes
  `evidence-observations.json` into the working tree.
- **`.gitignore` covers the runner's default output and its temp file.** Mode: focused-test.
  `test_gitignore_covers_live_test_results`: `git check-ignore` exits 0 for
  `test-results-round2.json` **and** for `.test-results-round2.json.abc123.tmp`. Red: the
  temp name is unignored under both the old and the new `test-results*.json` pattern.
  Reference: `git check-ignore test-results-round2.json`
  exits 0. Red: exits 1 today.

### Steps

1. In `LiveTestConfig.from_env_file`, `continue` on keys starting `LIVE_TEST_ENV_` before
   the `_CONFIG_FIELDS` membership test (`:327`).
2. Add both keys to `.env.example` with values `V10R3` and `POWER10`.
3. Replace `.gitignore` line 2 with `test-results*.json`, and add `.test-results*.tmp` on the
   next line. The second pattern is not redundant: `_write_results` writes through
   `tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")`
   (`scripts/live_test_runner.py:761-773`), so a crash or signal between `mkstemp` and
   `replace` strands `.test-results-round2.json.<rand>.tmp` in the repository root. That file
   holds the whole results document, whose `data` is stored verbatim on the PASS path (`:481`
   redacts only when `status == "FAIL"`) — the raw HMC responses `.gitignore:1` itself
   describes as containing "internal hostnames/IPs/serials". Neither the old pattern nor
   `test-results*.json` matches a name that begins with `.` and ends `.tmp`.
4. Gate emission on the destination actually being ignored rather than on the pattern being
   assumed correct: `_emit_observations` runs `git check-ignore -q <path>` and refuses,
   printing the path and the reason, when it exits non-zero. `--results-file` (`:589`) lets an
   operator set any stem — `--results-file evidence.json` yields `evidence.json` and
   `evidence-observations.json`, neither ignored by any pattern — so the check is what makes
   the claim true for a path the design cannot enumerate in advance.

   Criterion 14 as originally frozen named only `test-results*.json`. The scope audit flagged
   both mechanisms in this step as unauthorized; the operator amended criterion 14 to cover
   them (amendment 3, 2026-09-06), on the grounds that `.gitignore:1` describes these files as
   holding "internal hostnames/IPs/serials" and both paths are real ways that data reaches a
   commit.
5. Add the three helpers; call `_emit_observations` in `main` after `_write_results`, with
   path `Path(results_path).with_name(Path(results_path).stem + "-observations.json")`.
6. Validate the two `LIVE_TEST_ENV_*` keys in `_run_from_arguments` (`:603-620`), beside
   `LiveTestConfig.from_env_file`, and pass the resolved pair into `main`; `_emit_observations`
   then only decides whether to write. A lone key must not raise from `_emit_observations`:
   that call sits after `_write_results`, i.e. after a completed run against real hardware, and
   an uncaught `ValueError` there would replace the run summary and failed-test listing
   (`:852-866`) with a traceback. `_run_from_arguments` already catches `ValueError` from
   `from_env_file` and returns 1, which is this runner's established shape for a configuration
   error; a one-line `.env` typo should cost a startup exit, not a hardware run's output.
7. Add the six tests. Run the module; `just lint`; `just typecheck`.
8. Commit `feat: emit catalog-shaped live observations from a clean tree`.

### Acceptance criteria

`git check-ignore test-results-round2.json` exits 0; a run without environment keys prints
`no LIVE_TEST_ENV_* settings — observations not written`.

---

## Task 6: Recipe, CI job, and pipeline tests

Modifies `justfile`, `.github/workflows/ci.yml`, `tests/test_ci_pipeline.py`.

### Verification

- **Recipe exists and passes `--no-sync`.** Mode: focused-test. Existing
  `test_just_recipes_sync_only_in_setup_and_otherwise_run_without_sync` and an added line in
  `test_justfile_exposes_one_composed_verification_graph` asserting
  `"\nverification-report *ARGS:\n    uv run --no-sync python scripts/check_capability_inventory.py --verification-report {{ARGS}}\n"`.
- **Job shape.** Mode: focused-test. New
  `test_verification_report_job_warns_on_pull_requests_and_fails_on_schedule`: job body
  contains `timeout-minutes: 5`, `run: just verification-report` under
  `if: github.event_name != 'schedule'`, `run: just verification-report --fail-on-stale`
  under `if: github.event_name == 'schedule'`, no `${{` in any `run:` line, no
  `permissions:`. Existing counts updated: `runs-on: ubuntu-24.04` → 5, checkout settings
  → 6. Red: the new test fails before the job exists.

  This test asserts job shape, not job counts, so it sits outside the charter's original
  narrowing of `tests/test_ci_pipeline.py` to "the assertions that count jobs and recipes".
  The scope audit flagged it; the operator widened the surface for this file to admit it
  (amendment 3, 2026-09-06), because criterion 13 requires the warn-on-PR / fail-on-schedule
  split and pins `permissions: contents: read`, and nothing else would test either.
- **Workflow security.** Mode: focused-test. `just workflow-security` (zizmor) exits 0.

### Steps

1. Add the recipe to `justfile` after `capability-inventory`.
2. Add the `verification-report` job to `ci.yml` after `python-support-drift`. Its steps
   copy the matrix `ci` job's first four (`ci.yml:52-66`) — `actions/checkout` with
   `persist-credentials: false`; `astral-sh/setup-uv` with `enable-cache: true`,
   `version: "0.12.10"` and a literal `python-version: "3.11"` (no matrix here);
   `extractions/setup-just` with `just-version: "1.58.0"`; `run: just setup` — then the two
   guarded report steps. `python-support-drift` is the precedent for `if: schedule` and the
   five-minute timeout only; it does not install `just`, and this job needs the recipe and
   the project venv. Note the pinned `setup-uv` `version: "0.12.10"` count assertion
   (`workflow.count('version: "0.12.10"') == 5`) therefore goes to 6.
3. Update the counts and add the test in `tests/test_ci_pipeline.py`.
4. Run `uv run --no-sync pytest tests/test_ci_pipeline.py -q` and `just workflow-security`;
   expect green.
5. Commit `ci: report live-verification staleness, failing only the weekly run`.

---

## Task 7: Documents

Modifies `docs/capabilities/README.md`, `CHANGELOG.md`.

### Verification

- **README describes format 2 and the report.** Mode: task-test-not-applicable. The README
  carries no generation banner (its first line is `# HMC reference capability ledger`), so
  `just doc-freshness` reads nothing from it, and no test opens it; a wording test would be a
  prose snapshot. The machine-checkable half — the format the validator enforces — is Task 4.
- **CHANGELOG entry.** Mode: task-test-not-applicable. `tests/unit/test_changelog.py:16-19`
  asserts only that the declared version has a `## [<version>]` heading; an entry under
  `## [Unreleased]` leaves it untouched.

### Steps

1. Rewrite the README's maturity section: format 2 shape, the two triggers, the five
   states, the report and its CI behaviour, the two `LIVE_TEST_ENV_*` keys, and that a
   human copies observations from the runner's gitignored output.
2. Add a `CHANGELOG.md` entry under `## [Unreleased]` / `### Changed`.
3. Run `just doc-freshness`, `just adr-numbering`; expect green.
4. Commit `docs: describe derived live-verification staleness`.

---

## Final verification

Bare, reading exit codes: `just verify`; `uv run --no-sync prek run --all-files`;
`git --no-pager diff --stat "$(git merge-base HEAD origin/main)"` confined to the file map.
