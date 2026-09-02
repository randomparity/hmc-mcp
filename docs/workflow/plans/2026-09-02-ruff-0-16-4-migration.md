# Ruff 0.16.4 migration — implementation plan

Derived from [the design](../specs/2026-09-02-ruff-0-16-4-migration-design.md) and
[ADR 0114](../../adr/0114-ruff-default-rule-set-adoption.md). Issue
[#597](https://github.com/randomparity/hmc-mcp/issues/597).

**Goal.** Move the pinned linter from `ruff==0.15.22` to `ruff==0.16.4`, adopt Ruff's new
expanded default rule set, restore the eighteen rules 0.16 dropped, and fix every
resulting finding in code apart from three narrowly justified configuration entries.

**Architecture.** There is no runtime architecture to change. The change has three layers:
the dependency pin and its lock; a small `[tool.ruff.lint]` table in `pyproject.toml`; and
roughly 650 mechanical-to-reviewed edits across `scripts/`, `src/`, and `tests/`. The lint
gate `just lint` (a single `uv run --no-sync ruff check .`) is all-or-nothing, so the
branch is only green at the end.

**Tech stack.** Python 3.11–3.14, `uv` for dependency management, `just` for recipes,
`prek` for git hooks, `pytest` for tests, `ruff` for linting, `ty` for type checking.

Expected implementation size: 1600–2600 changed lines (L) — derived from the file map
below: 206 import-block rewrites, 138 alias removals, 93 `with`-statement merges, and ~212
smaller per-site edits, plus ~30 lines of configuration.

## Global Constraints

Transcribed from the design and from `AGENTS.md`:

- Ruff is pinned **exactly**: `ruff==0.16.4` in `pyproject.toml`'s `[dependency-groups]
  dev` list and in `TOOL_PINS` in `tests/test_ci_pipeline.py`. A pin that does not equal
  the locked version fails `tests/test_supply_chain.py`.
- Add the dependency with `uv add --dev --no-sync "ruff==0.16.4"`, then `just setup`.
  **Never** a bare `uv sync`, a bare `uv run`, or a bare `uv add` — a bare `uv sync`
  prunes the `app` extra and removes `typer`, which breaks `just typecheck`.
- This workstation has uv 0.12.1; CI pins 0.12.3. Regenerate the lock with
  `uvx uv@0.12.3 lock` so CI's `uv sync --locked` accepts it.
- Supported Python floor is 3.11, so `typing.Self` and `datetime.UTC` are available
  without `typing_extensions`.
- No blanket `ignore`, no `exclude`, no bare `# noqa` without a rule code. A retained
  `# noqa: <CODE>` carries a reason on the same line.
- All shell is non-interactive: `GIT_EDITOR=true`, `git --no-pager`.
- Diff against the merge base: `git --no-pager diff "$(git merge-base HEAD origin/main)"`.
- Run gates **bare** — no `| tail`, no `>/dev/null`, no `|| true`. A pipeline returns the
  last exit code and hides the failure.
- The pre-commit hook runs the lint gate, so intermediate commits taken before the tree is
  clean need `--no-verify`. The final commit must pass without it.
- Guardrails: `just verify`, then `uv run --no-sync prek run --all-files`. Narrow a
  `static` failure with `just lint` / `typecheck` / `secrets` / `workflow-security` /
  `env-vars` / `nicknames` / `tool-docs-check` / `adr-numbering` / `doc-freshness`.
- A green local `just verify` does not predict green CI: eight legs, {amd64, arm64} ×
  py{3.11, 3.12, 3.13, 3.14}.

## File map

**Created**

- `docs/adr/0114-ruff-default-rule-set-adoption.md` — the lint-policy decision.
- `docs/workflow/specs/2026-09-02-ruff-0-16-4-migration-design.md` — the design.
- `docs/workflow/plans/2026-09-02-ruff-0-16-4-migration.md` — this plan.

**Modified — contract**

- `pyproject.toml` — the pin, plus the new `[tool.ruff.lint]` table and its two
  sub-tables. Answerable for the whole lint policy.
- `uv.lock` — the resolved `ruff` 0.16.4 entry and its wheel hashes.
- `tests/test_ci_pipeline.py` — `TOOL_PINS`. Answerable for asserting the declared pin.

**Modified — lint fixes** (no file gains a new responsibility; each is edited only where
Ruff reports)

- `scripts/` — 6 shebang removals, plus `ISC004`, `TRY004`, `RUF100`, `BLE001`, `PIE808`,
  `UP017`, `UP041`, `I001` sites in `check_adr_numbering.py`, `check_env_vars.py`,
  `check_generated_docs.py`, `check_nicknames.py`, `check_python_support.py`,
  `gen_tool_reference.py`, `live_test_runner.py`.
- `src/hmc_mcp/` — ~200 sites, dominated by `I001` (105). Notable contract-adjacent files:
  `cli.py` (`PLC0414` re-export aliases), `audit/sink.py` (`S110`), `ssh/lpar.py`
  (`DTZ011`, `B008`), `tool_registry.py` (`RUF100`), `_app.py` (`PYI061`),
  `client/core.py` (`PYI034`).
- `tests/` — ~430 sites, dominated by `PLC0414` (134), `I001` (91), and `SIM117` (92).

**Not modified**

- `.pre-commit-config.yaml` — the Ruff hook already exists and no `static` sub-recipe is
  added, so the 1:1 hook correspondence `tests/test_ci_pipeline.py` asserts is unchanged.
- `docs/adr/README.md` — there is no ADR index in this repository (`AGENTS.md`).

## Task 1 — Pin Ruff 0.16.4 and synchronize the lock

**Files:** `pyproject.toml`, `tests/test_ci_pipeline.py`, `uv.lock`.

**Where this fits.** First, because every later measurement must come from the pinned
binary rather than an ad-hoc `uvx` invocation.

**Interfaces.** Produces: the string `"ruff==0.16.4"` in both
`pyproject.toml`'s `[dependency-groups] dev` list and the `TOOL_PINS` set in
`tests/test_ci_pipeline.py`; a `uv.lock` whose `ruff` entry reads `version = "0.16.4"` and
whose `[package.metadata]` requires-dist line reads
`{ name = "ruff", specifier = "==0.16.4" }`. Every later task consumes
`uv run --no-sync ruff` from the venv this task establishes.

**Steps.**

1. In `pyproject.toml`, replace `    "ruff==0.15.22",` with `    "ruff==0.16.4",`.
2. In `tests/test_ci_pipeline.py`, replace `    "ruff==0.15.22",` with
   `    "ruff==0.16.4",` inside `TOOL_PINS`.
3. Run `uvx uv@0.12.3 lock`. Expect stdout to contain
   `Updated ruff v0.15.22 -> v0.16.4`.
4. Run `just setup`. Expect the sync output to contain `- ruff==0.15.22` and
   `+ ruff==0.16.4`, and the run to end `prek installed at …/.git/hooks/pre-commit`.
5. Run `uv run --no-sync ruff --version`. Expect exactly `ruff 0.16.4`.
6. Run `uv run --no-sync pytest tests/test_supply_chain.py -q`. Expect all tests to pass —
   this is what proves the pin equals the lock.
7. Commit with `--no-verify` (the lint gate is red by construction until Task 6):
   `GIT_EDITOR=true git commit --no-verify -m "chore: pin ruff 0.16.4"` with a body
   naming the defaults overhaul and stating that the gate stays red until the following
   commits land.

**Acceptance criteria.** `uv run --no-sync ruff --version` prints `ruff 0.16.4`;
`tests/test_supply_chain.py` passes; `git --no-pager diff --stat HEAD~1` shows exactly
`pyproject.toml`, `tests/test_ci_pipeline.py`, and `uv.lock`.

**Rollback.** `git revert` the commit and re-run `just setup`.

## Task 2 — Record the design

**Files:** `docs/adr/0114-ruff-default-rule-set-adoption.md`,
`docs/workflow/specs/2026-09-02-ruff-0-16-4-migration-design.md`,
`docs/workflow/plans/2026-09-02-ruff-0-16-4-migration.md`.

**Where this fits.** Before any code fix, so the rule dispositions the later tasks apply
are recorded rather than inferred from the diff.

**Interfaces.** Produces the three artifact paths above. Task 3 consumes the ADR's
configuration decision verbatim.

**Steps.**

1. Write the three files as specified in the design.
2. Run `just adr-numbering`. Expect it to exit 0 — the record is `0114`, unique, and its
   H1 opens `# ADR 0114:`.
3. Run `just doc-freshness`. Expect it to exit 0 — none of the three files opens with a
   generation banner, so none is checked for staleness.
4. Commit with `--no-verify`.

**Acceptance criteria.** `just adr-numbering` and `just doc-freshness` both exit 0. The
ADR's `## Status` reads `Accepted`. There is no `docs/adr/README.md` row to add — this
repository keeps no ADR index.

**Rollback.** Delete the three files.

## Task 3 — Add the `[tool.ruff.lint]` configuration

**Files:** `pyproject.toml`.

**Where this fits.** Before the fixes, so every later measurement is taken against the
final rule set and no finding is fixed that the configuration was going to retire.

**Interfaces.** Consumes nothing from earlier tasks except the installed `ruff 0.16.4`.
Produces the configuration every later task's `ruff check` reads.

**Steps.**

1. Append to `pyproject.toml`, after the `[dependency-groups]` block and before
   `[tool.ty.src]`:

```toml
[tool.ruff.lint]
# Ruff 0.16 dropped these eighteen rules from its defaults. They were enforced here
# under 0.15.22, and ten existing `# noqa: E402` directives in tests/ depend on E402
# staying enabled -- without this list they become RUF100 findings whose only fix is
# deleting documented suppressions. See ADR 0114.
extend-select = [
    "E401", "E402", "E701", "E702", "E703", "E711", "E712", "E713", "E714",
    "E721", "E731", "E741", "E742", "E743", "F403", "F405", "F406", "F722",
]

[tool.ruff.lint.flake8-bugbear]
# B008 guards against a *mutable* default shared across calls. typer.Option and
# typer.Argument are Typer's declarative parameter constructors, which the framework
# requires in that position; LparResources and LparPcieAssignments are both
# @dataclass(frozen=True). B008 stays enabled and still fires on a genuinely mutable
# default. See ADR 0114.
extend-immutable-calls = [
    "typer.Option",
    "typer.Argument",
    "hmc_mcp.documents.LparResources",
    "hmc_mcp.operations.lpar.assignments.LparPcieAssignments",
]

[tool.ruff.lint.per-file-ignores]
# These two modules assert that the audit stream cannot be forged with bidirectional
# and control characters, so their fixtures must literally contain U+202E and U+2028.
# PLE2502's premise -- that such characters in source are unintended -- is false here
# and only here. See ADR 0114.
"tests/unit/test_audit.py" = ["PLE2502"]
"tests/unit/test_ownership.py" = ["PLE2502"]
```

2. Run `uv run --no-sync ruff check . --statistics`. Expect the trailer to read
   `Found 649 errors.` and `[*] 325 fixable with the --fix option`, and expect `B008` and
   `PLE2502` to be absent from the table.
3. Run `uv run --no-sync ruff check . --select E401,E402,E701,E702,E703,E711,E712,E713,E714,E721,E731,E741,E742,E743,F403,F405,F406,F722 --ignore-noqa --statistics`.
   Expect `10 E402 module-import-not-at-top-of-file` — the ten directives are live again.
4. Commit with `--no-verify`.

**Acceptance criteria.** The finding count drops from 712 to 649; `B008` and `PLE2502`
report nothing; the `extend-select` list restores exactly eighteen rules; every one of the
three entries carries its justification comment in the file.

**Rollback.** Remove the `[tool.ruff.lint]` block and its two sub-tables.

## Task 4 — Apply the safe mechanical fixes

**Files:** every file `ruff check --fix` touches (expected: ~325 sites across `scripts/`,
`src/`, `tests/`).

**Where this fits.** The issue asks for these as their own commit so the reviewed work in
Task 5 is legible separately.

**Interfaces.** Consumes the configuration from Task 3. Produces a tree whose remaining
findings are exactly the ~324 that need review.

**Steps.**

1. Run `uv run --no-sync ruff check . --fix`. Expect stdout to report `Found 649 errors
   (325 fixed, 324 remaining).`
2. Run `git --no-pager diff --stat`. Read the file list and confirm nothing outside
   `scripts/`, `src/`, and `tests/` changed.
3. Run `uv run --no-sync ruff check . --statistics`. Expect `I001`, `FURB157`, `UP032`,
   `UP017`, `UP037`, `UP035`, `UP041`, `UP012`, `UP033`, `PYI061`, `PIE808`, `PLR0402`,
   `B009`, `FURB167`, `PLR1711`, `RET501`, `RUF022`, and `RUF100` to be gone.
4. Run `just test`. Expect the compact success summary and exit 0. A failure here means a
   "safe" fix changed behaviour — read the failure and fix the cause, per R8, rather than
   reverting the whole run.
5. Run `just typecheck`. Expect exit 0.
6. Commit with `--no-verify`:
   `GIT_EDITOR=true git commit --no-verify -m "style: apply ruff 0.16.4 safe autofixes"`.

**Acceptance criteria.** `just test` and `just typecheck` both exit 0; the remaining
finding count is 324; the commit contains no hand edit.

**Rollback.** `git revert` the commit.

## Task 5 — Fix the reviewed remainder, by rule family

**Files:** as reported per family.

**Where this fits.** This is the substance. Each family is a separate, independently
reviewable edit; they are grouped into commits by family so a reviewer can read one
decision at a time.

**Interfaces.** Consumes the 324-finding tree Task 4 produced. Produces a tree on which
`uv run --no-sync ruff check .` reports `All checks passed!`.

**Steps.** For each family in this order — largest and most mechanical first, so a
behaviour regression surfaces against the smallest possible set of concurrent edits:

1. `PLC0414` (138). Run `uv run --no-sync ruff check . --select PLC0414 --fix
   --unsafe-fixes`. Then run `uv run --no-sync ruff check . --select F401 --statistics`
   and expect no findings: an alias whose name was unused would now be an unused import.
   Read `src/hmc_mcp/cli.py` by hand — it is a re-export facade, so confirm
   `uv run --no-sync pytest tests/app/test_cli.py tests/unit/test_public_api.py -q`
   passes before committing. Commit.
2. `SIM117` (93). Run `uv run --no-sync ruff check . --select SIM117 --fix
   --unsafe-fixes`, then `just test`. Commit.
3. `BLE001` (21). Read every site. Where the handler re-raises with context or totality is
   the contract, add `# noqa: BLE001 - <reason>`; otherwise narrow the caught exception
   type. Run `uv run --no-sync ruff check . --select BLE001,RUF100 --statistics` and
   expect neither. Commit.
4. `ISC004` (20). Wrap each implicit concatenation in parentheses. Read each site first to
   confirm a missing comma is not the real defect — Ruff offers both readings. Commit.
5. `TRY004` (12). Change the `isinstance`-guard failures to raise `TypeError`. Before each,
   run `rg -n '<the message text>' tests/` and confirm no test asserts `ValueError` at that
   site; escalate any that does rather than changing the assertion. Commit.
6. `PLW1510` (9). Add an explicit `check=False` to each `subprocess.run`, stating the
   existing behaviour. Commit.
7. `FURB192` (8), `DTZ011` (7), `DTZ001` (1), `RUF059` (7), `EXE001` (6), `PYI034` (3),
   `SIM102` (4), `S110` (3), `G201` (3), `TRY002` (2), `B017` (1), and the remaining
   singletons. Fix each per the design's disposition table. For `EXE001`, delete the
   `#!/usr/bin/env python3` line and the blank line following it from
   `scripts/check_adr_numbering.py`, `scripts/check_env_vars.py`,
   `scripts/check_generated_docs.py`, `scripts/check_nicknames.py`,
   `scripts/check_python_support.py`, and `scripts/gen_tool_reference.py`. For `DTZ011` in
   `src/hmc_mcp/ssh/lpar.py`, run `uv run --no-sync pytest tests/unit/test_ownership.py -q`
   and confirm the stamp format is unchanged. Commit in one or more grouped commits.
8. Run `uv run --no-sync ruff check .`. Expect exactly `All checks passed!`.

**Acceptance criteria.** `uv run --no-sync ruff check .` prints `All checks passed!` and
exits 0. `just test` exits 0. No bare `# noqa` was added, and every added `# noqa` names a
rule code and a reason. `git --no-pager diff "$(git merge-base HEAD origin/main)"` shows
no change to `.pre-commit-config.yaml` and no change to `pyproject.toml` beyond Tasks 1
and 3.

**Rollback.** Each family is its own commit, so a bad family is reverted alone.

## Task 6 — Prove the branch

**Files:** none — this task only runs gates.

**Where this fits.** Last. It is what converts a green lint run into a shippable branch.

**Interfaces.** Consumes the finished tree. Produces the guardrail evidence the pull
request reports.

**Steps.**

1. Run `just verify` **bare**. Expect exit 0. Bound the run generously — sibling runs
   observed 4–7 minutes and this branch is larger.
2. Run `uv run --no-sync prek run --all-files` **bare**. Expect every hook to report
   `Passed`. Run it exactly this way, not as a bare `prek`: the dev group pins the `prek`
   version and a globally installed one is a different binary.
3. Run `git --no-pager diff --stat "$(git merge-base HEAD origin/main)"` and read the file
   list against the design's file map. Anything outside it is a finding.
4. If any commit still needs to be made, make it without `--no-verify` — the hook must
   pass on the final tree.

**Acceptance criteria.** `just verify` exits 0 and `uv run --no-sync prek run --all-files`
exits 0, both run bare, on the final commit. The eight-leg CI matrix is green on the pull
request.

**Rollback.** A red gate is diagnosed with the narrowing sub-recipes listed in Global
Constraints, not worked around.

## Deferrals

None recorded yet. Any deferral a review of this design or branch disposes of as
`deferred-tracked` is added here with its owning record path or tracker issue.
