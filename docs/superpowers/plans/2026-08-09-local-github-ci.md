# Local and GitHub CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development
> (recommended) or executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give contributors and GitHub Actions one pinned, reproducible command
graph for setup, static checks, secrets detection, tests, and smoke checks.

**Architecture:** Focused `just` recipes are the canonical checks. Local prek
hooks and one GitHub Actions job call those recipes, while policy tests verify
that the configurations remain coordinated and least-privileged.

**Tech Stack:** just 1.58.0, uv, Ruff 0.15.22, ty 0.0.62, prek 0.4.10,
detect-secrets 1.5.0, pytest, GitHub Actions.

## Global Constraints

- Root interaction is unattended; authority is issue #48 plus campaign scope
  token `scope-48-20260809-a1` and the orchestrator-authorized baseline addition.
- Permitted files are `justfile`, `pyproject.toml`, `uv.lock`,
  `.pre-commit-config.yaml`, `.secrets.baseline`, `.github/workflows/*`, ADR
  0002/design/plan artifacts, and directly required tests/docs.
- No application behavior changes and no edits to existing credential fixtures.
- `just verify` remains the single full-suite entry point and keeps all existing
  pytest, MCP smoke, and CLI-loading coverage.
- Actions use full commit SHAs with release comments, checkout uses
  `persist-credentials: false`, and workflow permissions are `contents: read`.
- The type gate covers only the explicit clean module set and disables no rules.
- The secret scan includes tests; each baseline entry must match an intentional
  fixture and every baseline diff receives security review.
- Guardrail command: `just verify`, plus `uv lock --check`,
  `uv run prek run --all-files`, `actionlint`, and `zizmor .github/workflows/`.

---

### Task 1: Add the local CI contract and regression tests

**Files:**

- Create: `tests/test_ci_pipeline.py`
- Create: `.pre-commit-config.yaml`
- Create: `.secrets.baseline`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `justfile`

**Interfaces:**

- Consumes: the existing `test`, `smoke`, and CLI-loading commands in `justfile`.
- Produces: `setup`, `lint`, `typecheck`, `secrets`, `static`, and extended
  `verify` recipes; project-pinned tool executables; prek hook configuration.

- [ ] **Step 1: Write the failing configuration tests**

Create tests that load `pyproject.toml` with `tomllib` and read the other text
artifacts. Assert exact dev pins for Ruff, ty, prek, and detect-secrets; assert
the exact ty include set and absence of `[tool.ty.rules]`; assert the six just
recipe headers and that `verify` depends on `static test smoke`; assert all
three local hooks call `just <focused-recipe>` with `pass_filenames: false`;
and assert the baseline's complete result set contains exactly eight findings:
the seven reviewed fixture findings and the justfile scanner self-reference,
with no other result or excluded `tests/` path.

- [ ] **Step 2: Run the focused tests and confirm the expected red state**

Run: `uv run pytest -q tests/test_ci_pipeline.py --no-cov`

Expected: failures because the tool pins, hook configuration, baseline, and
new recipes do not exist.

- [ ] **Step 3: Add exact tool pins and the strict type include boundary**

Add these dev requirements to `pyproject.toml` and regenerate `uv.lock`:

```toml
"detect-secrets==1.5.0",
"prek==0.4.10",
"ruff==0.15.22",
"ty==0.0.62",
```

Add:

```toml
[tool.ty.src]
include = [
    "src/hmc_mcp/config.py",
    "src/hmc_mcp/documents.py",
    "src/hmc_mcp/errors.py",
]
```

Run `uv lock`, `uv sync`, and `uv lock --check`.

- [ ] **Step 4: Add the canonical just recipes**

Implement idempotent `setup` with `uv sync --locked` and
`uv run prek install`. Implement `lint` as `uv run ruff check .`, `typecheck`
as `uv run ty check`, and `secrets` as a tracked-file invocation of
`uv run detect-secrets-hook --baseline .secrets.baseline --no-verify`.
Make `static` depend on the three focused recipes and extend `verify` to depend
on `static test smoke` while preserving every existing CLI assertion.

- [ ] **Step 5: Add local prek hooks**

Create three `repo: local`, `language: system` hooks. Each sets
`pass_filenames: false` and invokes exactly `just lint`, `just typecheck`, or
`just secrets`. Do not duplicate the underlying uv commands.

- [ ] **Step 6: Generate and review the secret baseline**

Generate the detect-secrets 1.5.0 baseline from all tracked and new project
files. Confirm every result maps to one of the seven intentional test fixture
locations found during design and that no test path is globally excluded.

- [ ] **Step 7: Prove the secret gate rejects a new finding**

Create a temporary tracked text fixture containing a credential-shaped value,
run `just secrets`, and confirm a non-zero exit naming the temporary path.
Remove the fixture from the index and filesystem, run `just secrets` again,
and confirm exit 0. The temporary fixture must not reach a commit.

- [ ] **Step 8: Run the focused green checks**

Run, bare and independently:

```bash
uv run pytest -q tests/test_ci_pipeline.py --no-cov
uv lock --check
just setup
just lint
just typecheck
just secrets
just verify
uv run prek run --all-files
```

Expected: every command exits 0 with no warnings.

- [ ] **Step 9: Commit the local CI contract**

Stage only the six task files and commit:

```bash
git commit -m "ci: add reproducible local quality gates"
```

### Task 2: Add the hosted CI workflow

**Files:**

- Create: `.github/workflows/ci.yml`
- Modify: `tests/test_ci_pipeline.py`

**Interfaces:**

- Consumes: `just setup`, `just verify`, and the committed prek configuration
  from Task 1.
- Produces: one `ci` job that runs the same gates for pull requests and pushes
  to `main`.

- [ ] **Step 1: Extend the tests with a failing workflow policy test**

Assert the workflow runs on pull requests and `main` pushes, uses
`ubuntu-24.04`, grants only `contents: read`, has concurrency cancellation,
and contains these immutable pins:

```text
actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9 # v9.0.0
extractions/setup-just@53165ef7e734c5c07cb06b3c8e7b647c5aa16db3 # v4
```

Also assert `persist-credentials: false`, just version `1.58.0`, and bare steps
for `just setup`, `just verify`, and `uv run prek run --all-files`.

- [ ] **Step 2: Run the focused test and confirm the expected red state**

Run: `uv run pytest -q tests/test_ci_pipeline.py --no-cov`

Expected: the workflow policy test fails because `ci.yml` is absent.

- [ ] **Step 3: Add the least-privilege workflow**

Create `.github/workflows/ci.yml` with `pull_request` and `push` to `main`,
workflow-level `contents: read`, ref-scoped concurrency cancellation, and one
`ubuntu-24.04` job. Use the exact action pins above, disable persisted checkout
credentials, enable uv cache with Python 3.12, pin just 1.58.0, and run the
three required commands as separate steps. Add no secrets, write permissions,
path filters, or duplicated tool commands.

- [ ] **Step 4: Run workflow and repository guardrails**

Run, bare and independently:

```bash
uv run pytest -q tests/test_ci_pipeline.py --no-cov
actionlint
zizmor .github/workflows/
just verify
uv run prek run --all-files
```

Expected: every command exits 0. `just verify` must report all 502 or more
tests passing, the MCP handshake, and every CLI group loading.

- [ ] **Step 5: Verify the configuration tests bite**

Temporarily change one action SHA and confirm the workflow policy test fails;
restore it and confirm the test passes. Temporarily remove `static` from the
`verify` dependency line and confirm the local policy test fails; restore it
and confirm the test passes. Leave no temporary diff.

- [ ] **Step 6: Commit the hosted workflow**

Stage the workflow and updated test and commit:

```bash
git commit -m "ci: run local quality gates on GitHub"
```

### Task 3: Remove the transient plan and run final local proof

**Files:**

- Delete: `docs/superpowers/plans/2026-08-09-local-github-ci.md`

**Interfaces:**

- Consumes: all Task 1 and Task 2 gates.
- Produces: a branch ready for adversarial review and shipping.

- [ ] **Step 1: Remove the tracked transient plan**

Move this tracked plan to trash, stage its deletion, and commit:

```bash
git commit -m "docs: remove transient implementation plan"
```

The spec and ADR remain as durable design artifacts. If the plan is unexpectedly
untracked on resume, trash it without creating an empty commit.

- [ ] **Step 2: Inspect the final branch diff**

Run `git diff --stat main...HEAD`, `git diff --check main...HEAD`, and read the
full diff for scope, naming, action pins, baseline entries, and accidental
credential disclosure.

- [ ] **Step 3: Run the full guardrail set against final HEAD**

Run `uv lock --check`, `actionlint`, `zizmor .github/workflows/`,
`just verify`, and `uv run prek run --all-files` as bare commands. Record exact
pass/fail results for the review summary.
