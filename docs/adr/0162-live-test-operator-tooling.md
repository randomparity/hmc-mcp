# ADR 0162: Live runs are started, cited, and closed out by dedicated scripts

## Status

Proposed on 2026-09-21.

## Context

`scripts/live_test_runner.py` is the only executable in the live-test surface. It dispatches
25 subtasks across five groups against a real HMC, and several of those create, mutate and
delete partitions on a managed system.

**Pre-run validation exists, but it cannot be asked a question.** `_run_from_arguments`
(`scripts/live_test_runner.py:1105-1140`) validates the `.env` through
`LiveTestConfig.from_env_file`, refuses a results path git does not ignore, resolves
credentials via `_bootstrap_config` — `~/.config/hmc-mcp/config.toml` first, `.env` as
fallback — and checks `HMC_SCHEMA_VERSION`, all before the first dispatch and each with a
non-zero exit. The runner's own docstring calls this "the preflight", and it is one.

What it cannot do is answer whether a run *would* start without being the run. It also never
establishes two facts at all: whether the HMC is reachable and reports the ADR 0053-admitted
release and model, and what a selected arm will create, mutate and delete. The dedicated
arm's envelope check runs after the client is built and the baseline is read.

**Nothing dates a result.** The PASS/SKIP/FAIL matrix quoted in PR #869's body and its ADR
was hand-copied from a terminal. It carried no commit, so nothing contradicted it when the
branch moved two weeks and the arm stopped being able to import. A matrix that cannot be
falsified is not evidence.

**Nothing confirms teardown from outside.** The arm's cleanup guards refuse to mutate on a
mismatch and emit a manual-recovery row, which is correct, but the only witness that
recovery happened is the same run that failed to complete it.

The runner's module docstring — which argparse renders as the `--help` description — is
separately wrong: it prescribes a bare `uv run`, which AGENTS.md forbids because it prunes
the `app` extra, and documents subtasks 0–15 when the range is 0–24. argparse itself renders
`--group` and the full choice set correctly; only the prose is stale. Because the parser
uses the default formatter, that prose is also re-wrapped into a single paragraph.

## Decision

Three capability scripts under `scripts/`, one per missing capability, plus one thin wrapper
per arm:

| Script | Answers |
|---|---|
| `live_test_preflight.py` | may this run start, and what will it touch |
| `live_test_evidence.py` | what did this run prove, and on which commit |
| `live_test_recovery.py` | is the system clean now |
| `live_{round2,vmedia,sriov,dedicated}.py` | run one arm |

They are scripts, not `just` recipes. The `justfile` is this repository's guardrail
vocabulary — `static`, `test`, `verify`, and the sub-recipes CI calls by name — and every
target in it is safe to run on any checkout. A live arm mutates a managed system. Putting
the two in one namespace invites an operator to run hardware mutation believing they ran a
check.

**Preflight calls the runner's own validators; it never re-derives a verdict.** It invokes
`LiveTestConfig.from_env_file`, `_bootstrap_config` and `_ensure_schema_version` — the same
three the runner gates on — so there is exactly one definition of a valid configuration and
one credential precedence. What preflight adds is asking without running, plus the two facts
the runner never establishes. It is therefore authoritative about configuration because it
is delegating to the authority, not competing with it.

**Preflight is advisory about the hardware.** An unreachable HMC or an out-of-envelope
system is a predicted SKIP, not a blocker: the arm already SKIPs correctly on both, and a
blocking preflight would be a second copy of an admission rule ADR 0053 moves.

**Recovery reports; it never remediates.** It names what is stranded and prints the exact
command that clears it. An automated remediator acting on a partial read is the failure the
arm's own guards exist to prevent.

**Recovery reads a structured record, not prose.** Its inputs — run marker, fixture LPAR
name, DRC index, captured baseline — exist today only inside a formatted `data` string on
one result row, and the marker is per-run random (`pcie-{uuid4().hex[:8]}`). `LiveTestArtifacts`
therefore gains four additive fields the arm populates at ST29. Recording what a run created
is not arm behaviour: no guard, dispatch path or cleanup decision changes. The operator
narrowed the arm-logic non-goal to permit exactly this on 2026-09-21.

**Evidence is generated, never transcribed**, and refuses a document it cannot attribute to
a commit. That attribution has to exist first, and today it does not: the results document
carries `config`, `hmc`, `artifacts` and `results` and no provenance, while the only
`tested_commit` is written to a sibling `-observations.json` that is skipped when no
observation resolves or the runner is outside the repository. Attribution that vanishes
exactly when a run goes badly is not attribution, so the runner stamps a `run` block into
the document it already writes.

**Evidence renders through a field allowlist, and sanitises what the allowlist admits.**
`RunState.record` redacts only when `status == "FAIL"`, so PASS rows reach the document
unredacted, and the `tool` label is never redacted on any path — `vmedia.py` builds labels
like `f"hmc_delete_optical_media ({media_name})"` straight from an HMC response. Evidence
therefore renders `subtask`, `status`, `result`, `timestamp` and a `tool` truncated at its
first `(`, never a row's `data` or `note`, and never the document's `hmc` block, which
carries the HMC hostname and account name.

## Consequences

An agent handed the runbook can start a run, know what it will touch before it touches it,
and produce a citable matrix without reading source. A reviewer reading a matrix can tell
what it is evidence for.

The four wrappers delegate to `_run_from_arguments`, not to `main`: the results-path naming,
the credential bootstrap and the ignored-destination guard all live in the former, and a
wrapper calling `main` directly would run uncredentialled and overwrite `test-results-round2.json`.

AGENTS.md states one test module per `scripts/` file, but nothing enforced it —
`check_test_layout.py` only rejects stray `conftest.py` files, and the coverage gate is
`--cov=hmc_mcp`, which never measures `scripts/`. Adding seven scripts under an unenforced
convention would be adding seven untested files, so `check_test_layout.py` is extended to
enforce the mapping it was assumed to. This is an in-place strengthening of an existing
`static` member, so it needs no new recipe and no new prek hook.

Preflight imports the arms' admission predicates rather than restating them. A new arm that
adds a precondition and does not export one is invisible to preflight, which will then
predict RUNNABLE for a run that SKIPs. The runbook states that preflight predicts and only
the run decides.

Nothing here runs in CI; the scripts' unit tests run in CI like any other.

## Considered & rejected

- **`just` recipes per arm.** judgment: the `justfile` is the guardrail namespace where every
  target is safe on any checkout; a target that mutates a managed system does not belong
  beside `just verify`.
- **Extend `_run_from_arguments` with the prediction and the what-it-will-touch report, adding
  no script.** judgment: it is the smaller change and it keeps one entry point, but it can only
  report on a run it is already starting, and the question an operator needs answered on a new
  lab host is whether to start one at all. Rejected for that, not for size.
- **A `--preflight` flag on the runner.** judgment: same defect as above with an extra mode
  to maintain, and a validator inside the thing it validates cannot report on a runner too
  broken to import — the exact state PR #869 was in.
- **Re-deriving the configuration verdict inside preflight rather than delegating.** verified:
  `LiveTestConfig._CONFIG_FIELDS` is 40 entries and `_bootstrap_config`'s precedence is three
  deep; a second copy of either is a second thing to keep in sync, which is the ground this
  record uses to reject a blocking envelope check.
- **Preflight blocking on an out-of-envelope system.** verified: `require_admitted_environment`
  (`src/hmc_mcp/operations/virtualization/pcie.py:295`) already SKIPs that case and the arm
  mirrors it in `_environment_admitted` (`scripts/live_test/pcie.py:875`); a blocking preflight
  would duplicate an admission rule ADR 0053 revises.
- **Automated recovery.** judgment: a remediator acting on a partial read strands exactly what
  the cleanup guards refuse to strand.
- **Reading recovery's inputs by regex from the ST29 `data` string.** verified: the marker
  appears only inside an f-string at `scripts/live_test/pcie.py:1150`; coupling a safety check
  to prose in a module nothing gates would let a reword silently disable it.
- **Reading the commit from the sibling `-observations.json`.** verified: `_emit_observations`
  returns early with "no resolvable observations — nothing written" when the document would be
  empty, and `main` skips it when `_repository_root()` is `None`; a run that failed early
  produces no sibling at all, which is the run whose provenance matters most.
- **Computing the commit in the evidence process.** verified: `git rev-parse HEAD` there reports
  where the worktree is now, not where it was during the run; on #869 those differed by a merge
  and two weeks.
- **Rendering the document's `hmc` block in the matrix header.** verified: `_hmc_identity`
  (`scripts/live_test_runner.py:1233-1240`) returns `host`, `port`, `user`, `verify_ssl`; the
  first and third are the HMC hostname and account name, and the matrix exists to be pasted
  into a pull request.
- **Checking for a redaction marker instead of an allowlist.** verified: the runner writes no
  such marker, and `RunState.record` redacts only `status == "FAIL"`, so there is nothing a
  marker could truthfully assert.
- **Re-homing every arm's configuration onto the validated `.env` in this change.** verified:
  `rg -n 'os\.environ|getenv|env_var_value' scripts/live_test/*.py` at `4ae17fc4` returns reads
  only in `vmedia.py`, which merge `HMC_ISO_URL_ALLOWLIST` — an `HMC_*` connection setting
  ADR 0115 leaves at its existing precedence. The dedicated arm was the only offender and is
  fixed; nothing is left to retrofit.
- **Transcribing the matrix by hand, as today.** verified: that produced #869's matrix, which
  survived two weeks and a merge that broke the arm's imports with nothing marking it stale.
- **Do nothing and document the existing runner.** judgment: documentation supplies neither the
  commit stamp, the envelope prediction, nor the outside teardown witness.
