# Live verification and derived staleness design

Status: approved for implementation by issue #623, cycle-2 frozen scope annotation
`q623-d32263bc` (issue comment 5562652705).
Decision: [ADR 0127](../../adr/0127-derived-live-verification-staleness.md), superseding
ADR 0126's evidence model.

## Outcome

Know, per operation, whether a live test validated it; derive when that validation is
stale; warn at pull-request time and fail the weekly run. Make the runner's observations
honest enough to be worth recording: arguments validated against the served schema,
postconditions asserted rather than assumed, expected failures declared rather than
substring-matched.

## Boundaries

Extends `scripts/live_test_runner.py`, `scripts/live_test/`, and
`scripts/check_capability_inventory.py`. Changes no file under `src/hmc_mcp/`. Runs no live
test. Adds no real observation to `docs/capabilities/maturity.json`. Adds no CI permission.
The reference inventory (#621), discovery surfacing (#624) and scenario generation (#706)
are other issues' work.

## Current defects

Measured on `main` at `ded24a77`:

- 23 of 173 `state.call` dispatches send an argument no tool accepts or omit a required
  one, including the whole provisioning path calling a signature that no longer exists.
  Three route through `record_expected_or_real`, where a substring match over the exception
  text *and traceback* can record the harness's own defect as a known HMC limitation.
- `PASS` means the call returned. A job that came back `FAILED_BEFORE_COMPLETION` records
  identically to one that succeeded: the IBM job-status reference defines ten terminal
  statuses and only `COMPLETED_OK` is success.
- `REST000E` and `REST000B`, the only codes the runner matches, are absent from the IBM
  REST reference; they are field-observed. No documented grammar covers them.
- ADR 0126's fingerprint is one digest over 209 files; a stale one is a hard error. Detailed
  in ADR 0127's Context.

## Dispatch argument validity

Every `state.call(client, "<literal>", **kwargs)` must name a served tool, pass only keyword
names in its input-schema `properties`, and pass every name in `required`.

Enforced statically by extending the #485 name guard in `tests/test_live_runner.py` to
keywords, over `LIVE_WORKFLOW_MODULES` and the runner; a non-literal tool name or a `**`
splat fails the guard. Enforced at runtime in `RunState.call` before dispatch: a violation
records `failed` with reason `invalid-arguments` and never reaches the client. FastMCP would
reject the call anyway; the runtime check exists so the failure carries a stable reason
rather than a pydantic rendering. `scripts/live_test/provisioning.py:163` is rewritten to
name its arguments.

## Failure classification and expected outcomes

`RunState.call` keeps its `(status, data)` return. On failure `data` is a `CallFailure`
carrying the exception class name, the message, the traceback text, an `http_status` parsed
as `HTTP \d{3}` from the message, and `denied`, true when the message matches ADR 0038's
closed template ` is not permitted on .+ by access policy ` — the concrete exception type
does not survive FastMCP's `ToolError`, and a test provokes a real denial through the
composed application to hold that coupling.

`ExpectedOutcome(reason, error_codes=frozenset(), denial=False)` replaces
`expected_fail_substrings`. It matches a `CallFailure` when any declared code occurs as a
whole token (`\b<code>\b`) in the **message**, or when `denial` and the failure is a denial.
It rejects construction with neither. There is no code-family regex: matching declared
literals is what a family regex was approximating, and a family regex is how the four-digit
`REST\d{4}` bug happened. `record_with_expected` records `skipped` on a match and `failed`
otherwise; an `invalid-arguments` failure never matches. The 17 sites migrate; `skip()` is
unchanged.

## Result vocabulary and postconditions

| `result` | Meaning | Can be an observation |
|---|---|---|
| `observed` | the tool returned; nothing asserted | never |
| `passed` | every declared assertion held; cleanup `passed` or `not-required` | yes |
| `failed` | error, unmatched denial, invalid arguments, or a false assertion | yes |
| `skipped` | a declared `ExpectedOutcome` matched | never |

`record` keeps its signature and yields `observed`, so the 196 existing sites cannot promote
by omission. `record_verified(subtask, tool, *, operation, scenario, assertions, cleanup,
data)` is the only path to `passed`. `assertions` is a non-empty tuple of
`Assertion(id, holds)` where `id` matches `[a-z][a-z0-9-]{2,63}`; `operation` must exist in
`operations.json` (guard test); `scenario` matches `st\d+-[a-z0-9-]+`.

Converted now, so the path is exercised. Both job scenarios in
`scripts/live_test/metrics.py` assert the same three ids over a
`hmc_mcp.jobs.JobOutcome`: `job-found` (`found`), `job-identity-matches` (`job_id` equals
the identifier passed), and `job-status-successful` (`status` in
`hmc_mcp.jobs.SUCCESSFUL_JOB_STATUSES`). `hmc_wait_for_job` already returns that outcome as
a mapping; `hmc_get_job` returns the raw HMC entry (or `null`), which the scenario
normalizes with `hmc_mcp.jobs.job_outcome(job_id, data)` — the module's own reader, which
finds `Status` under the nested `Resource` and treats `None` as `found=False`. No scenario
reads a status key by hand. `hmc_get_console_info` in `scripts/live_test/connectivity.py`
asserts `console-uuid-present`: `data.get("uuid") or data.get("UUID")` is a non-empty
string — the expression the runner already uses to capture `console_uuid` at
`connectivity.py:24`.

## Observation record

An attempted live observation in `maturity.json` format 2:

```json
{
  "id": "st12-job-get",
  "channel": "live",
  "result": "passed",
  "scenario": "st12-job-inspection",
  "tested_commit": "<40 hex>",
  "observed_at": "2026-09-06T00:03:02Z",
  "hmc_release": "V10R3",
  "hardware_family": "POWER10",
  "cleanup": "not-required",
  "closure_fingerprint": "<64 hex>",
  "assertions": ["entry-identity-matches", "job-status-successful"]
}
```

Exact key set. `id` unique catalog-wide and matching `[a-z0-9][a-z0-9-]*`. `result` in
`{passed, failed}`. `observed_at` matches the validator's existing `TIMESTAMP` (`Z` form).
`hmc_release` and `hardware_family` match `[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}` and are the
only free text; the validator additionally rejects a value containing a dot-separated run
that parses as an IPv4 address. A `passed` observation has non-empty `assertions` and
`cleanup` in `{passed, not-required}`. A `not-run` observation keeps ADR 0126's shape and
obligation rules: `id`, `channel`, `result`, `scenario`, `reason`, `prerequisites`,
`obligation`.

`admission_policy` and the implementation record are unchanged from format 1. Removed from
observations: `currency`, `invalidated_by`, `promotion`, `implementation_fingerprint`,
`implementation_revision`, `deployed_revision`, `environment`, `scope`, `provenance`.

## Closure fingerprint

`closure_fingerprint(repo_root, handler_module)` resolves the handler's module file under
`src/hmc_mcp/`, parses it with `ast`, and follows every `from .x import y`,
`from ..x import y` (resolved by `level`) and `from hmc_mcp.x import y`; when `y` names a
module file it is included too. It recurses until closed, restricted to `src/hmc_mcp/`,
includes `TYPE_CHECKING`-guarded imports (conservative), and hashes the sorted
(path, bytes) sequence with the same length-prefixed SHA-256 construction 0126 used.
`scripts/` is excluded: the instrument changing is not the implementation changing, and the
instrument changes on most pull requests. The handler module is the `handler` field of
`discover_registry()`'s record with its last dotted component removed — `hmc_mcp.server_tools.jobs`
from `hmc_mcp.server_tools.jobs.hmc_get_job`, and `hmc_mcp.server_tools.permissions` from the
composed `hmc_effective_permissions` record, whose last component is `<composed>` rather than a
function name.

## Derived staleness and the report

For each operation the report derives one state from its most recent attempted live
observation:

| State | Condition |
|---|---|
| `unrecorded` | no maturity record |
| `unevidenced` | record, no attempted live observation |
| `stale` | latest observation's `closure_fingerprint` ≠ recomputed, or `observed_at` older than 90 days |
| `failed` | latest observation `failed` and not stale |
| `current` | latest observation `passed` and not stale |

Staleness is never a validation error. `check_capability_inventory.py --verification-report`
prints one line per operation and a summary
`verification coverage: <n> unrecorded, <n> unevidenced, <n> stale, <n> failed, <n> current (of <total>)`,
exits 0, and — when `GITHUB_ACTIONS` is set — emits one `::warning::` per stale operation and
appends the table to `$GITHUB_STEP_SUMMARY`. `--fail-on-stale` exits 1 when any operation is
`stale`. The 90-day ceiling is one module constant.

ADR 0126's repository-wide `implementation_fingerprint` and its stale-fingerprint error
block are deleted; nothing consumes them.

## Runner emission

`LIVE_TEST_ENV_HMC_RELEASE` and `LIVE_TEST_ENV_HARDWARE_FAMILY` are read from `.env`; both
or neither, a lone one is a configuration error. `LiveTestConfig.from_env_file` skips the
`LIVE_TEST_ENV_` prefix, and `.env.example` carries both keys.

At the end of a run, when both are set, the tree is clean under `src` and `scripts`
(`git status --porcelain`), and at least one `record_verified` observation exists, the
runner writes `<results-stem>-observations.json`: a list of `{"operation": …,
"observation": {…}}` objects in exactly the catalog shape, `tested_commit` from
`git rev-parse HEAD`. A dirty tree or missing environment prints why and writes nothing.
`.gitignore` gains `test-results*.json`, which covers this path and closes the existing gap.
A human copies observations into `maturity.json`; the runner never writes there.

## CI

`justfile` gains `verification-report *ARGS`, running
`uv run --no-sync python scripts/check_capability_inventory.py --verification-report {{ARGS}}`.
It is not a `static` member and has no prek hook. `ci.yml` gains one job,
`verification-report`, shaped like `python-support-drift`: `ubuntu-24.04`, five-minute
timeout, pinned checkout and `setup-uv`, `just setup`, then two steps — `just
verification-report` guarded `if: github.event_name != 'schedule'` and `just
verification-report --fail-on-stale` guarded `if: github.event_name == 'schedule'`. No
expression appears in a `run:` line. `permissions:` is untouched. `tests/test_ci_pipeline.py`
gains one test for the job and updates the `ubuntu-24.04` and checkout-settings counts.

## Threat model

**Boundaries.** The validator parses `maturity.json` (already did); the runner reads two new
`.env` keys whose values reach a committed record; CI gains a job that reads the repository
and writes a job summary.

**Actors.** A contributor opening a pull request; a local operator with a real HMC. No
network actor: the validator and report are offline.

**Controls.** Every committed field except two is a closed vocabulary, hash, SHA, date or
pattern-bound id; the two environment strings are pattern-bound, length-capped and
IPv4-rejected. The runner emits only to a gitignored path. The CI job has `contents: read`
and no secret. Workflow `run:` lines carry no `${{ }}` expression.

**Out of scope.** Proving an observation true; a maintainer committing a fabricated
observation together with a matching closure hash — the record is small and reviewed.

## Verification

| Contract | Evidence |
|---|---|
| dispatch keywords match served schemas; 23 corrected | argument guard, green |
| unreadable dispatch fails the guard | guard test over a `**` source |
| `CallFailure` classifies HTTP status and a real ADR 0038 denial | unit tests |
| `ExpectedOutcome` matches whole tokens in the message, not the traceback | unit test |
| `record` cannot yield `passed`; `record_verified` fails on a false assertion or failed cleanup | unit tests |
| job assertions use `SUCCESSFUL_JOB_STATUSES` | unit test with a `FAILED_BEFORE_COMPLETION` entry |
| format 2 shape; format 1 rejected | validator tests |
| closure fingerprint changes when an imported module changes, not an unrelated one | validator test over a fixture package |
| each derived state, the summary line, `--fail-on-stale` exit code | validator tests |
| env-string pattern and IPv4 rejection | validator tests |
| runner writes nothing on a dirty tree or missing env; writes catalog-shaped objects otherwise | runner tests |
| `from_env_file` accepts the extended `.env.example` | existing complete-example test |
| CI job shape and justfile recipe | `tests/test_ci_pipeline.py` |
| `.gitignore` covers `test-results-round2.json` | `git check-ignore` in a test |

## Global constraints

- Python 3.11 floor; CI runs 3.11–3.14 on amd64 and arm64.
- No new runtime or development dependency.
- Every `uv run` in `justfile` passes `--no-sync`.
- `ci.yml` keeps exactly one `permissions:` block, `contents: read`; no `workflow_dispatch`;
  no `fetch-depth`; every action pinned to the SHAs in `tests/test_ci_pipeline.py::ACTION_PINS`.
- Guardrails: `just verify`, then `uv run --no-sync prek run --all-files`.
