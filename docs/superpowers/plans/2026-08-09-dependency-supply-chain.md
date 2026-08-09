# Dependency Supply-Chain Tightening Implementation Plan

> Branch: `feat/tighten-dependency-supply-chain-26`
> Base branch: `main`
> Guardrails: `just verify`; focused checks are named below.
> Scope authority: issue #26 and WORK:SCOPE token
> `e2763c1e-4463-4221-b621-4741c7f6651e`.

## Task 1: Add an executable supply-chain policy test

**Files:** create `tests/test_supply_chain.py`.

1. Parse `pyproject.toml` and assert every runtime, development, and build requirement has
   exactly one `==` specifier with no direct URL or additional range operator.
2. Parse the existing `uv.lock` and check that every pinned runtime/development name and
   version is in the resolved package set. The build backend is outside uv's project lock
   and is covered by the exact-declaration assertion.
3. Read `.github/dependabot.yml` and assert the one intended updater is the root `uv`
   ecosystem with a weekly schedule, seven-day default cooldown, and an all-dependencies
   version-update group.
4. Run `uv lock --check`, then `uv sync --locked` to provision `.venv` without refreshing
   the lock. Run `.venv/bin/python -m pytest tests/test_supply_chain.py --no-cov -q` and
   confirm it fails against the current lower-bound declarations and missing Dependabot
   configuration. Confirm `git status --short --untracked-files=all` shows only the policy
   test and design-plan state expected at this phase.

## Task 2: Pin direct dependencies and regenerate the lock

**Files:** modify `pyproject.toml` and `uv.lock`.

1. Replace each runtime and development requirement's range with an exact `==` pin at its
   existing locked release. Pin `uv_build` to current stable 0.12.3, matching uv 0.12.3 and
   eliminating the build-range warning. Do not add, remove, or substitute dependencies.
2. Run `uv lock` to regenerate the universal lockfile; never hand-edit it.
3. Run `uv lock --check` from the clean pre-test state before any `uv run`, then run the
   focused policy test. Confirm the test still fails only because Dependabot configuration
   is not present.
4. Commit the manifest and lockfile as one logical dependency-resolution change. Rollback is
   removal of this commit; no external state or migration exists.

## Task 3: Configure grouped Dependabot updates

**Files:** create `.github/dependabot.yml`.

1. Configure schema version 2 and one `package-ecosystem: "uv"` update rooted at `/`.
2. Schedule weekly updates, set `cooldown.default-days` to 7, and group all version updates
   with `patterns: ["*"]` and `applies-to: version-updates`.
3. Do not add secrets, registries, reviewers, labels, auto-merge, action updates, or token
   permissions.
4. Run `uv lock --check` before any `uv run`, then the focused policy test and `just verify`.
   Commit the configuration and policy test together so the guard lands with the behavior
   it checks.

## Task 4: Review and ship

**Files:** inspect the full branch diff; edit only the frozen surface when a defensible
finding requires it.

1. Run the branch adversarial review with the work-issue focus and resolve every finding.
2. Run a dependency-focused threat scan because the diff changes dependencies and a
   lockfile. Resolve any owned finding without widening permissions or introducing a new
   dependency.
3. Run the simplification pass. If it changes behavior, repeat adversarial review.
4. From a clean working tree, run `uv lock --check` before `just verify`, push the feature
   branch, open a PR against `main` ending with `Closes #26`, post the review annotation,
   and wait until checks are green and GitHub reports the PR CLEAN/MERGEABLE.
5. Set issue #26 to `status:awaiting-merge`, post the hand-off trajectory, and stop. The
   campaign orchestrator owns merge and worktree cleanup.
