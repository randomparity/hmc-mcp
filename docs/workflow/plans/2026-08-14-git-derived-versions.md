# Git-derived package versions implementation plan

**Goal:** derive artifact and runtime versions from validated Git provenance under ADR 0023.

**Architecture:** Hatchling 1.32.0 delegates dynamic version metadata to Versioningit 3.3.0.
Project-owned methods select strict release tags, validate repository state, and apply the single
release-line selector. Runtime code reads installed distribution metadata.

**Tech stack:** Python 3.11+, Hatchling 1.32.0, Versioningit 3.3.0, pytest, uv, Git.

## Global constraints

- Branch `feat/git-derived-versions-159`; base `main`.
- Host `arm64`; targets `amd64` and `arm64`; relationship included.
- Release tags are canonical decimal `X.Y.Z`; choose the highest reachable semantic version.
- `release-line` accepts exactly `patch`, `minor`, or `major`; no fallback version input.
- Dirty means staged, tracked unstaged, or untracked files; ignored files do not count.
- Shallow repositories fail. Git-less unpacked sdists may use valid embedded `PKG-INFO`; ordinary
  Git-less source copies fail.
- Setup: `just setup` performs the single `uv sync --locked`. Post-setup just recipes use
  `uv run --no-sync`; the standalone gate is `UV_NO_SYNC=1 uv run prek run --all-files`.

## Task 1: Prove and implement deterministic Git version calculation

**Files:** create `scripts/versioning.py`; create `tests/scripts/test_versioning.py`.

**Interfaces:** `describe_git(*, project_dir: str | Path, params: dict[str, object]) ->
VCSDescription`; `next_release(*, version: str, branch: str | None,
params: dict[str, object]) -> str`. Task 2 configures these exact callables.

1. Write tests using temporary Git repositories for canonical exact tags, highest reachable tag,
   lower tag on `HEAD`, development distance, repository-unique SHA, no-tag origin, annotated tags,
   ignored prefixed/leading-zero tags, staged/unstaged/untracked/ignored states, shallow clones, and
   patch/minor/major plus invalid/missing selector values.
2. Run `uv run --with versioningit==3.3.0 pytest --no-cov -q tests/scripts/test_versioning.py`;
   expect failures because `scripts/versioning.py` does not exist.
3. Implement subprocess-backed Git queries with bounded literal arguments, actionable errors,
   strict tag parsing, integer-triple comparison, Versioningit `VCSDescription`, and semantic
   component bumping. Keep each function under project complexity limits and add no dependency.
4. Re-run the focused command; expect all version tests to pass.
5. Confirm the shallow fixture reports `true` from `git rev-parse --is-shallow-repository`.
   Mutation-check dirty rejection, shallow rejection, the lower-tag-on-`HEAD` base selection, and
   one transition by temporarily breaking each invariant, observing failures, and restoring it.
6. Re-run the exact focused command from step 4 and expect all tests to pass. Inspect the diff to
   confirm no mutation residue remains, then commit as `feat: compute versions from Git provenance`.

## Task 2: Make Git computation the package metadata authority

**Files:** modify `pyproject.toml`, `uv.lock`, and `src/hmc_mcp/__init__.py`; create
`tests/test_package_version.py`.

**Interfaces:** `pyproject.toml` points Versioningit's VCS and next-version methods to Task 1 and
sets `release-line = "minor"`. `hmc_mcp.__version__` is the result of
`importlib.metadata.version("hmc-mcp")`.

1. Add failing metadata/runtime tests that build a wheel in a clean temporary repository, inspect
   wheel contents and `METADATA`, rebuild from its sdist without Git, install editable in an isolated
   environment, and reject a Git-less source copy without `PKG-INFO`.
2. Run `uv run pytest --no-cov -q tests/test_package_version.py`; expect failures against static
   `0.1.0` metadata.
3. Replace static project version with `dynamic = ["version"]`; replace `uv_build` with exact pins
   `hatchling==1.32.0` and `versioningit==3.3.0`; configure Hatch/Versioningit and update the lock.
4. Replace the runtime literal with `importlib.metadata.version`.
5. Run the focused artifact tests against their clean fixture repositories and expect all to pass.
   Run the relevant Ruff and ty checks, then commit the verified integration and tests together as
   `feat: derive package metadata versions from Git`.
6. Run `uv sync` from the now-clean feature worktree and re-run the focused tests; expect pass under
   the repository's installed editable environment.

## Task 3: Give CI complete provenance and close the full gates

**Files:** modify `justfile`, `CONTRIBUTING.md`, `.github/workflows/ci.yml`, and focused
command/workflow-policy tests.

**Interfaces:** every active checkout feeding a project `uv` invocation has `fetch-depth: 0` and
retains `persist-credentials: false`.

1. Add failing policy tests that identify active jobs invoking project `uv`, require full checkout
   depth without weakening credential handling, and require canonical no-sync commands after setup.
   Add a regression fixture that syncs a clean copy, dirties `pyproject.toml`, and proves just/prek
   reach their tools rather than invoking an editable rebuild.
2. Run its exact pytest node; expect failure on the current shallow checkout configuration.
3. Add `fetch-depth: 0` to both active checkout steps. Make `just setup` the explicit sync point,
   convert post-setup recipes to `uv run --no-sync`, and update CI/CONTRIBUTING/contract references
   to `UV_NO_SYNC=1 uv run prek run --all-files`. Re-run focused tests; expect pass.
4. Run `just verify`; expect all static, test, smoke, and CLI gates to pass with zero warnings.
5. Run `UV_NO_SYNC=1 uv run prek run --all-files`; expect every hook to pass without modifying
   tracked files.
6. Commit as `ci: fetch full history for version provenance`.

## Rollback and cleanup

Each task is separately revertible. Temporary repositories and virtual environments use pytest
fixtures and clean themselves. Build artifacts stay in fixture-owned temporary directories. No tag,
publication, migration, remote HMC state, or campaign merge action occurs.
