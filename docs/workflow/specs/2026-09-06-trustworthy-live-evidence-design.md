# Trustworthy live-runner evidence design

Status: approved for implementation by issue #623 and frozen scope annotation
`q623-d32263bc`.
Decision: [ADR 0127](../../adr/0127-trusted-live-evidence-artifacts.md).

## Outcome

Make the existing live runner produce observations a maturity catalog can promote from,
and make everything it cannot prove visibly non-promoting. The runner validates every
dispatch against the current served tool schema, classifies failures structurally instead
of by substring, separates a returned tool result from an asserted postcondition, and
emits a redacted evidence artifact carrying run, build and environment identity. The
offline capability validator becomes the trusted channel validator ADR 0126 assigned to
this issue: it binds a promoting maturity observation to a committed evidence row and
refuses a hand-typed one.

## Boundaries

This extends `scripts/live_test_runner.py` and the `scripts/live_test/` workflow modules;
it adds no second harness. It changes no `src/hmc_mcp/` runtime behaviour, no
authorization or ownership rule, no MCP tool, and no generated tool document. It runs no
live HMC test and adds no real evidence row to `docs/capabilities/maturity.json` — issues
#621 (inventory), #624 (discovery) and the V1–V10 verification children own those.

The 23 dispatches this design corrects are in-surface: the argument guard below fails on
them, and this repository does not ship over a red guardrail.

## Current defects

Measured on `main` at `ded24a77`, by walking every `state.call` dispatch in
`scripts/live_test/` and `scripts/live_test_runner.py` against the served input schema of
the composed MCP application:

- **23 dispatches send arguments the tool does not accept, or omit ones it requires.**
  `hmc_get_job`/`hmc_wait_for_job` send `job_uuid` where the parameter is `job_id`
  (the pair issue #623 names); `hmc_read_lpar_boot_order`, `hmc_set_lpar_boot_order`,
  `hmc_clear_lpar_boot_order` send `lpar_uuid` where the parameters are
  `system_name_or_uuid` and `lpar_name_or_uuid`; every `hmc_list_users`,
  `hmc_modify_user`, `hmc_delete_user` and `hmc_create_user` dispatch omits
  `console_uuid`; `hmc_delete_lpar` omits `system_name_or_uuid`;
  `hmc_unmount_optical_media` sends `mapping_uuid` where the parameters are
  `lpar_name_or_uuid` and `media_name`; and `hmc_provision_lpar` sends seven flat
  arguments where the signature takes nested `adapters` and `storage` objects.
- **Every one of them currently records as a `FAIL` row indistinguishable from a real HMC
  rejection**, and three of them are routed through `record_expected_or_real`, where a
  substring match can convert the harness's own defect into a `SKIP` that reads as a known
  HMC limitation.
- **`PASS` means the call returned**, not that anything was asserted. A tool that returned
  `{}` having done nothing records exactly as one that did the work.
- **`record_expected_or_real` matches `expected_fail_substrings` against
  `str(data).lower()`**, and `data` on failure is the exception text *plus the full
  traceback*. A substring such as `"400"` matches a line number, a byte count or a UUID
  fragment anywhere in that traceback.
- **Nothing records build, environment or scenario identity**, so no result can be scoped
  to the HMC release or the revision that produced it, which epic #620 requirements 7 and
  16 both require.

## Dispatch argument validity

One rule, enforced twice, because a static guard cannot read a computed dispatch and a
runtime guard cannot run without an HMC.

**Statically**, `tests/test_live_runner.py` extends the #485 name guard to arguments. For
every `state.call(client, "<literal>", **kwargs)` in `LIVE_WORKFLOW_MODULES` and in the
runner, the keyword names must be a subset of the served tool's input-schema
`properties` and a superset of its `required`. A dispatch whose tool name is not a string
literal, or which carries a `**` splat the guard cannot read, fails the guard — the same
refusal `_dispatched_tool_names` already makes for names, for the same reason.

The schema source is the input schema of the composed application, obtained offline the
way `scripts/gen_tool_reference.py` already composes it. It is the current served
contract, not a second transcription of it.

**At runtime**, `RunState.call` checks the same two conditions before dispatch. A
violation records `result: failed` with `reason_code: invalid-arguments` and never
reaches the HMC. It can never become `skipped` or `passed`: this is the harness's own
defect, and routing it to `skipped` is the failure mode this section exists to remove.

`scripts/live_test/provisioning.py:163` builds its dispatch with `**` splats. It is
rewritten to name its arguments, so the static guard can read it.

## Failure classification

`RunState.call` returns `(status, data)` as it does today, so the workflow modules'
`st, data = await state.call(...)` and `st == "PASS"` idioms are unchanged. What changes
is that on failure `data` is a `CallFailure` record rather than a formatted string:

```python
@dataclass(frozen=True)
class CallFailure:
    exception_type: str          # the class name across the FastMCP boundary
    message: str                 # the exception text, unredacted in memory
    traceback_text: str
    error_codes: frozenset[str]  # HMC codes matched as whole tokens: REST\d{4}[A-Z]
    http_status: int | None      # matched as "HTTP <ddd>", not a bare number
    denied: bool                 # an ADR 0038 access-policy denial
```

`error_codes` and `http_status` are matched with anchored patterns against the exception
*message* only, never the traceback. `denied` matches ADR 0038's closed denial template —
both `connection_denial` and `target_denial` render
`"<tool> is not permitted on <what> by access policy <policy>."` — because the concrete
exception type (`ConnectionScopeError`, `TargetScopeError`) does not survive FastMCP's
`ToolError` boundary. That coupling is load-bearing, so a test composes the application,
provokes a real denial through it, and asserts the classifier reports `denied`. If ADR
0038's template changes, that test fails rather than denials silently reclassifying as
ordinary errors.

## Structured skips and expected denials

`expected_fail_substrings` is removed. A workflow declares what it tolerates:

```python
@dataclass(frozen=True)
class ExpectedOutcome:
    reason_code: str                          # stable kebab-case id
    reason: str                               # public-safe prose
    error_codes: frozenset[str] = frozenset()
    denial: bool = False
```

`RunState.record_with_expected(subtask, tool, status, data, expected)` records `skipped`
with the matching declaration's `reason_code` only when the `CallFailure` carries one of
its `error_codes`, or is a denial and the declaration sets `denial`. Anything else records
`failed`. A declaration that names neither an error code nor `denial` is rejected at
construction: it would match everything.

`RunState.skip` requires a `reason_code` alongside its prose reason. All 52 existing skip
sites supply one. A guard test asserts that each `reason_code` used anywhere in the
runner is declared exactly once in a single module-level registry, so a typo is a test
failure rather than a new silent category.

## Result vocabulary and postconditions

A recorded observation carries one `result`:

| `result` | Meaning | Promoting |
|---|---|---|
| `observed` | The tool returned. Nothing was asserted about what it did. | never |
| `passed` | Every declared postcondition held, and cleanup passed or was not required. | eligible |
| `failed` | The call errored, was denied without a matching declaration, was dispatched with invalid arguments, or a declared postcondition did not hold. | never |
| `skipped` | A declared `ExpectedOutcome` matched. | never |

`RunState.record` keeps its signature and produces `observed`. This is the design's main
safety property: the 196 existing record sites assert nothing, so they must not be able to
produce promoting evidence, and the cheapest guarantee of that is that the API they
already call cannot express one. Omission is safe by construction rather than by review.

`RunState.record_verified(subtask, tool, *, operation, scope, scenario, assertions,
cleanup, data)` is the only way to reach `passed`. `assertions` is a non-empty tuple of
`Assertion(description, holds)`; the result is `passed` only when every `holds` is true
and `cleanup` is `passed` or `not-required`, and `failed` otherwise. `operation` is a
stable operation ID from `operations.json`, checked against it by a guard test.

To keep this a used path rather than scaffolding, the two job scenarios issue #623 names —
`hmc_get_job` and `hmc_wait_for_job` in `scripts/live_test/metrics.py` — and the
`hmc_console_info` probe in `scripts/live_test/connectivity.py` are converted to
`record_verified` with real postconditions: for the job scenarios, that the returned
entry's UUID or JobID equals the identifier passed.

## Run, build and environment identity

`RunMetadata` is minted once per invocation:

- `run_id`: `lt-<UTC date>-<8 lowercase hex>`.
- `started_at` / `finished_at`: RFC 3339 UTC.
- `implementation_revision`: `git rev-parse HEAD`, 40 hex.
- `deployed_revision`: the revision the imported `hmc_mcp` package was built from. Under
  this repository's editable install that is the same checkout, which is exactly what
  epic #620 requirement 16 asks to be verified rather than assumed.
- `build_identity_verified`: true only when `deployed_revision` equals
  `implementation_revision` **and** `git status --porcelain` over `src` and `scripts` is
  empty. A dirty tree or a mismatch sets it false.
- `implementation_fingerprint`: `check_capability_inventory.implementation_fingerprint`,
  reused rather than recomputed, so the runner and the validator cannot disagree.
- `environment`: six required fields — `hmc_release`, `hmc_build`, `hardware_family`,
  `firmware`, `licensing`, `topology` — supplied by the operator as `LIVE_TEST_ENV_*`
  entries in the same `.env` file `LiveTestConfig` already reads.

The environment block is all-or-nothing and separate from `LiveTestConfig`'s required
settings, so an existing operator's `.env` keeps working: all six present emits an
evidence artifact; none present emits no artifact and prints why; any partial subset is a
configuration error, because a half-named environment is the one that produces a
wrongly-scoped claim.

`LiveTestConfig.from_env_file` currently reports any `LIVE_TEST_*` key outside its own
table as `unknown setting` and refuses to load, so it must skip the `LIVE_TEST_ENV_`
prefix explicitly; without that, adding the block to a `.env` breaks the runner's
configuration load rather than extending it. The checked-in `.env.example` gains all six
keys, which keeps it the complete authoritative mapping
`tests/test_live_runner.py::test_live_config_reads_the_complete_example_and_ignores_exports`
asserts it to be.

When `build_identity_verified` is false, the artifact is still written and every row is
forced to `failed` with `reason_code: build-identity-unverified`. Recording the run
honestly is the point; promoting from it is what must not happen.

## Evidence artifact

The runner writes `<results-stem>-evidence.json` beside its results document, atomically,
by the same `_write_results` path. Committing one under
`docs/capabilities/evidence/` is a deliberate human act; the runner never writes there.

```json
{
  "format_version": 1,
  "run_id": "lt-2026-09-06-a1b2c3d4",
  "started_at": "2026-09-06T00:00:00+00:00",
  "finished_at": "2026-09-06T00:04:11+00:00",
  "implementation_revision": "<40 hex>",
  "deployed_revision": "<40 hex>",
  "build_identity_verified": true,
  "implementation_fingerprint": "<64 hex>",
  "environment": {
    "hmc_release": "V10R3", "hmc_build": "2340", "hardware_family": "POWER10",
    "firmware": "FW1030.20", "licensing": "PCM enabled", "topology": "single-managed-system"
  },
  "rows": [
    {
      "id": "st12-job-get",
      "operation": "job.get",
      "scope": {"variant": "job-by-uuid", "parameters": []},
      "scenario": {"id": "st12-job-inspection", "description": "Read a submitted job by its identifier"},
      "result": "passed",
      "assertions": ["returned entry UUID equals the requested job_id"],
      "cleanup": "not-required",
      "observed_at": "2026-09-06T00:03:02+00:00",
      "reason": null
    }
  ]
}
```

Only `passed`, `failed` and `skipped` rows appear; `observed` rows are the working
results document's business and never enter the artifact. Row ids are unique.
A `passed` row requires non-empty `assertions`, `cleanup` in `{passed, not-required}` and
`build_identity_verified`. Every string leaf passes the redaction detector before the file
is written; a leaf that does not is a write failure, not a redacted-and-shipped value.

## Trusted provenance and promotion

`docs/capabilities/maturity.json` moves to `format_version: 2`. The validator accepts
only 2. These are pre-release repository artifacts with no external consumer, so the
format is replaced rather than dual-supported.

Provenance gains a second kind:

- `{"kind": "unverified", "reference": "<public-safe source>"}` — as in format 1, never
  promoting.
- `{"kind": "live-artifact", "reference": "evidence/<name>.json#<row-id>"}` — promoting
  eligible.

For a `live-artifact` provenance the validator resolves `<name>.json` as a direct child of
`docs/capabilities/evidence/`, then requires the observation to **equal** the named row
on: `operation`, scope identity, `scenario` id and description, `result`, `assertions` as
a set, `cleanup`, `environment`, `implementation_revision`, `deployed_revision` and
`implementation_fingerprint`. The catalog cannot state a fact the runner did not record,
and an observation whose row is missing is an error rather than an unverified claim.

`promotion.eligible` may be `true` only when all of the following hold; otherwise it must
be `false` with a non-empty reason, as in format 1:

1. `channel` is `live`, `result` is `passed`, `currency` is `current`;
2. provenance kind is `live-artifact` and every equality above holds;
3. the artifact's `build_identity_verified` is true;
4. the artifact's `implementation_fingerprint` equals the fingerprint computed now;
5. the observation's scope is in the operation's current `implemented_scope`.

Condition 4 is what makes promotion decay: any change under `src`, `scripts`,
`pyproject.toml` or `uv.lock` invalidates it, exactly as ADR 0126 specified.

Every artifact under `docs/capabilities/evidence/` is validated for shape, redaction and
operation existence whether or not any observation references it, so an unreferenced
artifact cannot sit in the tree accumulating drift.

## Threat model

**Boundaries added.** The offline validator parses two inputs it did not produce:
`docs/capabilities/evidence/*.json`, and the path fragment inside a `live-artifact`
provenance reference. **Boundary widened.** `.env` gains six `LIVE_TEST_ENV_*` operator
values that flow into a committed, public artifact.

**Actors.** A contributor opening a pull request, untrusted for artifact content; a local
operator running the runner against their own HMC, trusted to run it but not trusted to
hand-write an evidence claim. There is no network actor: the validator is offline and
reads only repository-local files. The design places its trust in *review of a small
committed file*, and says so rather than implying cryptographic assurance it does not
provide.

**Control per boundary.**

- *Provenance reference → filesystem path.* The `<name>` fragment must match
  `[a-z0-9-]+\.json` with no path separator and no `..`; the resolved path must be a
  regular, non-symlink, direct child of `docs/capabilities/evidence/`. Compared after
  resolution, not by string prefix.
- *Evidence artifact → validator.* Parsed with `load_json`, which already rejects
  duplicate keys (`_unique_pairs`, `scripts/check_capability_inventory.py:71`). Exact key
  sets, closed vocabularies, `SHA_1`/`SHA_256` and RFC 3339 shape checks, unique row ids,
  and a byte-size bound. No `eval`, no import, no subprocess driven by artifact content.
- *Artifact string leaves → public repository.* The redaction detector rejects hostname-,
  IP-, absolute-path- and secret-shaped values, in the runner before writing and in the
  validator before accepting. Enforcing it on both sides is deliberate: the runner
  protects the operator, and the validator protects the repository from an artifact the
  runner did not write.
- *Operator environment values → artifact.* Non-empty, length-bounded, redaction-checked.

The redaction patterns already exist in `scripts/live_test_runner.py` and are now needed
by the validator too, so they move to `scripts/live_test/redaction.py` and both import
them. One definition, because two would drift and the divergence would be invisible.

**Explicitly out of scope.** The validator proves shape, consistency and provenance
binding — never that an observation is *true*; ADR 0126 already states this and this
change does not weaken it. It does not defend against a maintainer who commits a
fabricated artifact together with a matching observation; the control there is that both
are small, human-readable and reviewed in the same pull request. It adds no signing, no
key management and no attestation service, none of which this repository has anywhere to
put.

## Verification

| Contract | Evidence |
|---|---|
| Every live dispatch's arguments match the served schema | `tests/test_live_runner.py` guard over `LIVE_WORKFLOW_MODULES`; asserted to bite on a synthetic bad dispatch |
| The 23 current mismatches are corrected | the same guard, green on the real modules |
| An unreadable dispatch fails rather than being skipped | guard test over a `**` splat source |
| `CallFailure` classifies HMC codes, HTTP status and denials | unit tests; the denial case provokes a real ADR 0038 denial through the composed app |
| A non-matching failure never becomes `skipped` | `record_with_expected` unit test |
| `record` cannot produce `passed` | unit test asserting the result vocabulary |
| `record_verified` yields `failed` when an assertion is false or cleanup failed | unit tests, one per condition |
| Invalid arguments never yield `passed` or `skipped` | runtime-guard unit test |
| A false `build_identity_verified` forces every row to `failed` | unit test |
| Partial `LIVE_TEST_ENV_*` is a configuration error | unit test |
| `from_env_file` loads a `.env` carrying the environment block | the existing complete-example test, over the extended `.env.example` |
| The artifact rejects an unredacted leaf before writing | unit test |
| `maturity.json` format 2 accepts `live-artifact` provenance and binds it to a row | `tests/scripts/test_check_capability_inventory.py` |
| Each of the six controlled cases in issue #623 cannot promote | one test per case: wrong argument, isError/denial, failed postcondition, cleanup failure, missing licence, absent target |
| A promoting observation decays when the fingerprint changes | validator test |
| Path traversal in a provenance reference is rejected | validator test over `../`, absolute and symlink references |
| `docs/capabilities/README.md` matches the shipped format | `just capability-inventory`, `just doc-freshness` |

## Global constraints

- Python 3.11 is the floor; CI runs 3.11–3.14 on amd64 and arm64.
- No new runtime or development dependency. Everything here uses the standard library,
  the existing `fastmcp` client, and code already in `scripts/`.
- `scripts/` files get one test module each, named `tests/scripts/test_<name>.py`
  (`AGENTS.md`); `scripts/live_test_runner.py` keeps its existing exception,
  `tests/test_live_runner.py`.
- Guardrails: `just verify`, then `uv run --no-sync prek run --all-files`.
- Public artifacts carry no hostname, IP address, absolute path, serial number or
  credential.
