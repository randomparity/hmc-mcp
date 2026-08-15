# Release artifact construction and validation plan

**Goal:** Build one wheel and one sdist from a clean checkout, independently validate their package
boundary, and retain only the wheel in CI for downstream installation tests.

**Architecture:** Canonical Just recipes separate destructive construction from read-only artifact
validation. Pytest-backed packaging checks inspect existing archives and rebuild only from the sdist
in temporary state. CI composes the commands through `just verify` and uploads the validated wheel.

**Tech stack:** Just, uv, hatchling/versioningit, Python 3.11+, pytest, GitHub Actions.

## Global constraints

- `just build` creates one wheel and one source distribution in `dist/`.
- `just verify-artifacts` validates existing artifacts without rebuilding the source checkout.
- `just verify` includes both commands after existing source verification.
- Git provenance failures stay actionable and public-safe.
- CI uploads only the validated wheel; no PyPI publication or credential is introduced.
- Host architecture is arm64; effective targets are amd64 and arm64; the host is included.
- `BASE_BRANCH` is `main`.
- Required gates are `just setup`, `just verify`, and separately
  `UV_NO_SYNC=1 uv run prek run --all-files`.

## Task 1: Validate existing wheel and sdist artifacts

**Files:** create `tests/validate_release_artifacts.py` and `tests/test_release_artifacts.py`; reuse
`tests/test_package_version.py` helpers only by copying small test-specific setup until a third
repetition justifies extraction.

**Interfaces:** `tests/validate_release_artifacts.py ARTIFACT_DIR` is a dedicated command that
validates caller-supplied existing archives and returns zero only when every invariant holds. It is
not collected by ordinary pytest. `tests/test_release_artifacts.py` imports its functions and tests
the CLI; Task 2's `verify-artifacts` recipe invokes the command directly.

1. Write a failing happy-path test that creates a clean tracked-project Git fixture, builds its
   wheel and sdist, and calls `validate_release_artifacts.main([artifact_dir])`. Assert normalized
   name, equal version, Python requirement, MIT license, console entry point, runtime dependencies,
   package paths, sdist build inputs, and Gitless reconstruction equality. Run
   `uv run --no-sync pytest -q --no-cov tests/test_release_artifacts.py`; expect import failure
   because the validator module is absent.
2. Implement the minimum archive and metadata inspection plus CLI in
   `tests/validate_release_artifacts.py`. Keep archive paths logical and do not extract into the
   checkout. Run the focused test; expect it to pass.
3. Add parameterized failing cases that independently mutate valid archives for absent artifact
   directory, missing/duplicate/unexpected artifacts, malformed archives, name/version/Python/license/
   entry-point/dependency mismatches, source/wheel/sdist package-member equality and sentinels
   (including synchronized omission from both archives), required sdist inputs,
   absolute and escaping paths, symlink/hard-link/device/FIFO members, rebuild failure, and rebuilt
   version/metadata/entry-point/dependency/package-member mismatch caused through embedded sdist
   inputs. Each assertion must name the artifact and invariant. Run the focused test and
   confirm each new case fails before its corresponding check exists.
4. Implement the minimum fail-closed checks and automatic temporary-state cleanup. Complete all
   artifact-set, archive-header, member-name, member-type, metadata, and required-input checks before
   calling the rebuild subprocess. Add a subprocess test double asserting zero calls for every
   unsafe-member case. Run the focused test; expect all cases to pass.
5. Mutate one version-consistency comparison, run its focused test and observe failure, restore the
   comparison, then rerun green.
6. Invoke `uv run --no-sync python tests/validate_release_artifacts.py <fixture-artifact-dir>`;
   expect exit 0. Invoke it without an argument and with a missing directory; expect nonzero status
   and actionable usage/invariant messages.
7. Run `just verify`; expect the pre-existing suite to remain green because the dedicated validator
   command is not a pytest test module and no recipe calls it yet.
8. Commit as `feat: validate built package artifacts`.

**Acceptance:** Existing archives are checked without rebuilding the source checkout; malformed or
inconsistent inputs fail actionably; a valid sdist rebuilds the same wheel version without Git.

**Rollback:** Revert the commit and remove only generated `dist/` through a recoverable trash
operation if cleanup is needed.

## Task 2: Define the canonical command and CI contracts

**Files:** modify `tests/test_ci_pipeline.py`, `justfile`, and `.github/workflows/ci.yml`.

**Interfaces:** Consumes Task 1's `tests/validate_release_artifacts.py ARTIFACT_DIR`. Produces recipes
`build` and `verify-artifacts`, `dist/*.whl` as the wheel handoff, and matrix-unique CI artifact
names. Downstream issue #163 consumes the uploaded wheel.

1. Add failing tests asserting that `build` clears `dist/` and invokes
   `uv build --wheel --sdist --out-dir dist .`, `verify-artifacts` invokes only
   `uv run --no-sync python tests/validate_release_artifacts.py dist`, and `verify` orders both
   after source checks. Run `uv run --no-sync pytest -q --no-cov tests/test_ci_pipeline.py`;
   expect those tests to fail.
2. Add failing tests asserting that CI uploads only `dist/*.whl` after `just verify`, uses
   `actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v7.0.1`, sets
   `if-no-files-found: error`, and names artifacts from matrix architecture and Python version.
   Run the same focused command; expect failure.
3. Implement the two Just recipes, compose them into `verify`, and add the wheel upload step.
   Run the focused command and Task 1's focused tests; expect all to pass.
4. Run `just build` and `just verify-artifacts`; expect one wheel, one sdist, and exit 0. Record the
   filenames for the review ledger.
5. Run `UV_NO_SYNC=1 uv run zizmor -qq --no-online-audits .github/workflows/`; expect exit 0.
6. Run `just verify`; expect exit 0, proving this commit's graph is independently green.
7. Commit as `feat: add canonical artifact command graph`.

**Acceptance:** The tested command graph builds and independently validates `dist/`; CI retains one
matrix-identified wheel and has no publication permission or credential.

**Rollback:** Revert the commit; no external package publication or persistent state exists.

## Task 3: Prove clean-checkout provenance and full integration

**Files:** modify `tests/test_release_artifacts.py` and `tests/test_ci_pipeline.py` only if integration
coverage exposes a missing contract.

**Interfaces:** Consumes the Task 1 validator and Task 2 command graph. Produces the complete local
proof that CI runs unchanged.

1. Add an integration test copying only tracked files, initializing clean Git history, and invoking
   `just build` followed by `just verify-artifacts`; assert success and exactly one wheel/sdist. Run
   the focused test; expect it to pass against Tasks 1 and 2. Temporarily change the copied
   justfile's validator path to a missing file, rerun and observe failure, restore it, then rerun
   green to prove the integration assertion bites.
2. Add a dirty-checkout case that invokes `just build`, asserts nonzero status, and checks the
   existing actionable public-safe provenance message. Confirm it fails if construction bypasses
   the version backend.
3. Apply only corrections proven necessary by these tests, then rerun the focused suite; expect all
   tests to pass. If no correction is necessary, do not manufacture a code change for this task.
4. Run `just setup`, then `just verify`; expect exit 0 and no warnings.
5. Run `UV_NO_SYNC=1 uv run prek run --all-files`; expect exit 0 and no warnings.
6. Re-read `git diff main...HEAD` for naming, scope, secrets, publication credentials, and needless
   abstraction; commit any evidence-backed correction separately.

**Acceptance:** The canonical commands pass from a clean tracked checkout, dirty provenance fails
actionably, all guardrails pass, and the diff contains no publication path or credential.

**Rollback:** Revert Task 3 corrections separately; earlier independently tested command and
validator commits remain bisectable.

## Durable checkpoint

Branch: `feat/build-validate-artifacts-162`. Base: `main`. Guardrails: `just setup`, `just verify`,
and `UV_NO_SYNC=1 uv run prek run --all-files`. Assigned ADR: 0024. ADR index coupling: not coupled;
no index exists. Scope token: `354656a9-5ce6-4cd3-beb6-357a176abcce`.
