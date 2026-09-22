# Live-test operator tooling — design

Decision record: [ADR 0162](../../adr/0162-live-test-operator-tooling.md)

## Problem

A live run against a real HMC is started, read, and closed out by hand. The runner validates
configuration at startup, but only as part of starting a run — there is no way to ask whether
a run would start, nothing predicts whether the hardware is in the ADR 0053 envelope, nothing
says what an arm will create and delete, nothing attributes a result to the commit that
produced it, and nothing confirms from outside that teardown happened.

PR #869 paid for three of those: its matrix was gathered on a branch two weeks behind `main`,
hand-copied with no commit attached, and survived a merge that left the arm unable to import.
Every later reader treated it as evidence.

This change is for an agent that has not read the source and will not.

## Scope

### Current and intended ownership

| Path | Owns today | Owns after |
|---|---|---|
| `scripts/live_test_runner.py` | dispatch, config load, results write, **stale usage prose** | + a `run` provenance block in the results document; + four `LiveTestArtifacts` fields; corrected docstring and `RawDescriptionHelpFormatter` |
| `scripts/live_test/pcie.py` | the arms | + populates the four artifact fields at ST29 |
| `scripts/check_test_layout.py` | rejects stray `conftest.py` | + enforces one test module per `scripts/` file |
| `scripts/live_test_preflight.py` | — | pre-run validation and a predicted per-arm verdict |
| `scripts/live_test_evidence.py` | — | commit-stamped matrix rendered from a results document |
| `scripts/live_test_recovery.py` | — | outside confirmation that a run left nothing stranded |
| `scripts/live_{round2,vmedia,sriov,dedicated}.py` | — | one named entry point per arm |
| `docs/live-testing.md` | — | the operator/agent runbook |
| `AGENTS.md` | repo conventions | + a live-test section binding future runs |

No caller migrates. Three existing files change and each change is additive except the
docstring correction, which replaces prose that is wrong.

**The runner must stamp its own results document.** It holds `config`, `hmc`, `artifacts`,
`results` and no provenance; the only `tested_commit` goes to a sibling `-observations.json`
that `_emit_observations` skips when nothing resolves or `_repository_root()` is `None` — so
attribution disappears exactly when a run fails early. It gains:

```json
"run": {"tested_commit": "<sha>", "tree_clean": true, "group": "dedicated",
        "subtasks": [24], "finished": "<iso8601>"}
```

reusing the commit and clean-tree reads the observations path already performs.
`subtasks` lists what actually dispatched, which a nullable single id cannot
express for a group; `finished` is named for when the block is written, since the
document is written on exit.

**Recovery needs a structured record.** Its inputs exist today only inside a formatted `data`
string, and the run marker is per-run random. `LiveTestArtifacts` gains
`pcie_run_marker`, `pcie_fixture_lpar`, `pcie_drc_index`, `pcie_baseline_io_slots`, all
defaulting to `None`, populated by the dedicated arm at ST29.

**`check_test_layout.py` must enforce the mapping AGENTS.md states.** Today it checks only
`conftest.py` placement, and the coverage gate is `--cov=hmc_mcp`, which never measures
`scripts/`. Adding seven scripts under an unenforced convention adds seven files nothing
requires be tested. The checker gains a scripts→tests mapping with the two documented
exceptions (`check_env_vars.py` → `tests/test_env_var_guard.py`, `live_test_runner.py` →
`tests/test_live_runner.py`).

Each new `scripts/` file takes a matching `tests/scripts/test_<name>.py`.

### Out of scope

Approved 2026-09-21; the third was narrowed the same day.

- **Automated recovery.** `live_test_recovery.py` reports and exits non-zero; it issues no
  mutating command. Owner: the operator, following the printed command.
- **CI integration.** No workflow, `just` target, or prek hook reaches an HMC.
- **Changing arm behaviour.** No guard, dispatch path, cleanup decision, `SUBTASK_GROUPS`
  semantic or `RunState` method changes. **Narrowed:** the arm may *record what it created*
  into `LiveTestArtifacts`, which changes no decision the arm takes.

Retrofitting the other arms' configuration was in scope and is **empty**:
`rg -n 'os\.environ|getenv|env_var_value' scripts/live_test/*.py` at `4ae17fc4` returns reads
only in `vmedia.py`, which merge `HMC_ISO_URL_ALLOWLIST` — an `HMC_*` connection setting
ADR 0115 leaves at its existing precedence. Preflight asserts that property; nothing moves.

## Failure model

**Actors and deployments.** A human operator at a terminal on a lab host with HMC network
reach; an AI agent on that same host with the same credentials. Credentials reach both from
`~/.config/hmc-mcp/config.toml` (the documented priority-2 source) or, failing that, a local
`.env`; `LIVE_TEST_*` scenario settings come only from `.env`. No anonymous or multi-tenant
caller. No CI runner — no deployment of these scripts has an HMC.

**Invariants and assets at stake.**
- A managed system's partitions and profiles: the arms create, mutate and delete real ones.
- Credentials in the TOML profile or `.env`, and the identifiers in a results document:
  hostnames, account names, serials, DRC indices, U-codes.
- The integrity of a cited matrix: a result attributed to the wrong commit is worse than no
  result, because it is believed.

**Accepted failure classes.**
- Preflight says RUNNABLE and the arm SKIPs. Accepted: it predicts from exported admission
  predicates and only the run decides; the runbook says so and the verdict is worded as a
  prediction.
- Preflight cannot reach the HMC and so cannot check the envelope. Accepted: it reports the
  reachability failure and degrades to configuration-only.
- A results document hand-edited between the run and `live_test_evidence.py`. Accepted: the
  operator owns their filesystem, and the commit stamp makes the claim checkable.
- Recovery run against a system another run also touched may report a foreign stranding.
  Accepted: it reports rather than acts, and names the marker it matched on.
- A `tool` label whose HMC-derived text precedes its first `(` would survive sanitisation.
  Accepted: no arm builds one that way today, and Validation 6 pins the shape.

**Covered elsewhere.**
- Redaction of FAIL text: ADR 0120, via the runner's existing pass.
- Ownership stamping and cleanup refusal: ADR 0064 and ADR 0161's guards.
- Which `.env` keys exist and are required: ADR 0115 and `LiveTestConfig`.
- Credential precedence: `_bootstrap_config`, which preflight calls rather than reimplements.

## Threat model

**Boundary inventory.** No boundary is added. Three are read by new code: the TOML profile
and `.env` (credentials, operator-controlled), and a `test-results-*.json` document
(HMC-derived text, redacted **only on FAIL rows**). One is widened:
`live_test_evidence.py` produces output whose purpose is to be pasted into a pull request,
issue, or ADR.

**Actor model.** The untrusted input is the HMC's own response text. Contrary to an earlier
draft of this spec, it does **not** all arrive redacted: `RunState.record`
(`scripts/live_test_runner.py:725`) applies `_redact_failure_data` only when
`status == "FAIL"`, and the `tool` label is redacted on no path at all. Evidence must
therefore treat result rows as untrusted, not as pre-cleaned. The operator and agent are
trusted with the credentials they already hold.

**Control per boundary.**
- Credential read: preflight delegates to `_bootstrap_config` and `_ensure_schema_version`
  and reports only a per-key present/absent verdict. No `HMC_*` **value** appears in any
  output stream. Scenario values (`LIVE_TEST_DEDICATED_PCIE_SYSTEM_NAME`, `_LPAR_PREFIX`,
  `_DRC_INDEX`) are deliberately printed — naming what a run will mutate is the point of the
  verdict, and none is a credential.
- Results document read: consumed as data; no path, command, or URL is built from it.
- Public output: a closed allowlist of `subtask`, `status`, `result`, `timestamp` and a
  `tool` truncated at its first `(`. Never a row's `data` or `note`, and never the `hmc`
  block — `_hmc_identity` returns `host`, `port`, `user`, `verify_ssl`, the first and third
  being the HMC hostname and account name. An allowlist fails closed as the document grows.

**Explicitly out of scope.** A malicious HMC: an operator pointed at a hostile management
console has already lost. Credential rotation and file permissions are the operator's.
Redaction correctness for FAIL rows belongs to ADR 0120.

## Success

1. An agent following `docs/live-testing.md` alone can run the dedicated arm end to end and
   produce a citable matrix. The runbook explains that result rows carry subtask ids up to
   34 while dispatch ids stop at 24.
2. The runner's `--help` prescribes `uv run --no-sync`, states the range 0–24, and renders
   its usage block unwrapped.
3. `live_test_preflight.py` exits non-zero for every invalid configuration the runner itself
   would reject, and zero otherwise.
4. Preflight's verdict names, for each selected mutating arm, the managed system, fixture
   LPAR prefix and DRC index it will touch.
5. `live_test_evidence.py` refuses to emit a matrix it cannot attribute to a commit, and
   emits no field outside its allowlist.
6. `live_test_recovery.py` exits non-zero when any partition carrying the run marker
   survives, the selected slot is stranded, or the profile differs from its baseline.
7. Each of the four arms is runnable by one named script that goes through the runner's
   argument entry point.
8. `just test-layout` fails when a `scripts/` file has no matching test module.
9. AGENTS.md states that a live matrix is evidence only for the commit it ran on.

"Every invalid configuration the runner itself would reject" in (3) is bounded to what
`LiveTestConfig.from_env_file`, `_bootstrap_config` and `_ensure_schema_version` reject:
a missing, empty, unknown or duplicate `LIVE_TEST_*` key, a non-integer or non-positive
numeric value, inconsistent resource limits, unresolvable credentials, and a missing
`HMC_SCHEMA_VERSION`.

An HMC record delimiter in a value is **not** in that set, contrary to an earlier draft:
`from_env_file` performs no delimiter check. `_config_value_safe` does, inside
`pcie._dedicated_config`, and it stops that arm rather than the run — so preflight reports
it as a predicted SKIP at exit 0. Anything requiring the HMC is likewise a predicted SKIP
per the failure model, not a detection.

## Validation

| # | Contract | Mode | Evidence |
|---|---|---|---|
| 1 | Runbook is sufficient without source | `task-test-not-applicable` | The contract is a human or agent following prose; no executable consumer reads `docs/live-testing.md`, and a test over its wording would assert the assertion. Discharged by criteria 2–9 plus one operator walkthrough recorded in the PR. |
| 2 | `--help` content and shape | `focused-test` | `tests/test_live_runner.py::test_module_docstring_prescribes_no_sync_and_the_real_subtask_range` and `::test_help_renders_the_docstring_unwrapped_with_every_group` — the range is derived from `SUBTASKS` rather than restated, and the usage line survives as its own line. **Done**: both green; faulting the formatter turns the second red. Green: `pytest tests/test_live_runner.py -k 'help or docstring'` |
| 3 | Preflight agrees with the runner's own verdict | `focused-test` | `tests/scripts/test_live_test_preflight.py` — one case per bounded rejection in Success (3), each in `tmp_path` with `monkeypatch.chdir`, asserting preflight's exit status equals what the delegated validator returns. Red: script absent. Green: `pytest tests/scripts/test_live_test_preflight.py` |
| 4 | No `HMC_*` value is printed | `focused-test` | Same module — `monkeypatch.setenv("HMC_PASSWORD", "SENTINEL-d4f2")` and a TOML profile with a sentinel host, on the path where preflight reports credentials resolved; assert neither sentinel appears in stdout or stderr. Red: printing the resolved config. Green: same command. |
| 5 | Verdict names what a mutating arm will touch | `focused-test` | Same module — a `.env` with the four dedicated keys set; assert the verdict line contains the system name, LPAR prefix and DRC index. Red: verdict prints only RUNNABLE. Green: same command. |
| 6 | Evidence emits only allowlisted, sanitised fields | `focused-test` | `tests/scripts/test_live_test_evidence.py` — a document whose rows carry `SENTINEL-9ac1` in `data`, `note`, and inside a `tool` parenthetical, and whose `hmc` block carries a sentinel host and user; assert no sentinel appears in the output and the tool's leading identifier does. Red: renders the row or the header verbatim. Green: `pytest tests/scripts/test_live_test_evidence.py` |
| 7 | Evidence refuses an unattributable run | `focused-test` | Same module — document with no `run.tested_commit`; assert non-zero exit and empty stdout. Red: script absent. Green: same command. |
| 8 | Evidence matrix matches the document | `focused-test` | Same module — fixture with known PASS/SKIP/FAIL totals and a `tree_clean: false` case; assert rendered totals, rows, and the dirty-tree qualifier. Red: script absent. Green: same command. |
| 9 | Recovery detects each stranded condition | `focused-test` | `tests/scripts/test_live_test_recovery.py` — four cases against a stubbed client (surviving marked partition, stranded slot, profile drift, all-clean), plus one driving `--results` from a document with the four artifact fields populated. Red: script absent. Green: `pytest tests/scripts/test_live_test_recovery.py` |
| 10 | Recovery issues no mutating call | `focused-test` | Same module — a stub raising for any tool off a read-only allowlist; assert every case passes. Red: a delete slips in. Green: same command. |
| 11 | The arm records its recovery inputs | `focused-test` | `tests/scripts/test_pcie.py` — run the arm through the existing `ScenarioState` seam and `::test_arm_records_what_it_created_into_artifacts` covers the happy run and `::test_a_fixture_abandoned_before_the_baseline_is_still_recorded` the run that ends between ST29 and ST30 — the case the ST29 write exists for, and the only one that fails when it is removed. **Done**: green, both verified by controlled fault. Green: `pytest tests/scripts/test_pcie.py -k 'artifacts or abandoned'` |
| 12 | Each wrapper dispatches its own group through the argument entry point | `focused-test` | One module per wrapper — patch `live_test_runner._run_from_arguments`, assert it is called with exactly `["--group", "<arm>"]`. Red: wrapper absent, or calls `main` directly. Green: `pytest tests/scripts/ -q` |
| 13 | Runner stamps run provenance | `focused-test` | `tests/test_live_runner.py` — `::test_main_stamps_run_provenance_into_the_results_document` pins the written block's exact key set; `::test_run_provenance_outside_a_repository_reports_no_commit` and `::test_run_provenance_reports_a_dirty_tree` cover the unattributable and dirty cases. **Done**: green, and removing the block from the document write turns the first red. Green: `pytest tests/test_live_runner.py -k provenance` |
| 14 | Test-layout enforces the script→test mapping | `focused-test` | `tests/scripts/test_check_test_layout.py` — a fixture tree with a `scripts/` file and no matching module; non-zero exit naming it, both documented exceptions pass, an exception whose target was deleted fails, and `scripts/live_test/` modules are outside the rule. **Done**: green, and `just test-layout` goes red on a real untested script. Green: `pytest tests/scripts/test_check_test_layout.py` |
| 15 | AGENTS.md live-test section | `task-test-not-applicable` | Prose convention for human and agent readers; no executable consumer, and `just doc-freshness` reads only a first-line generation banner. |

Guardrails: `just verify` exits 0 and `uv run --no-sync prek run --all-files` passes every hook.

## Deferrals

None.
