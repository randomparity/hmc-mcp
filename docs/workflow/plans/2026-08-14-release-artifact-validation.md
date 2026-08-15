# Release artifact construction and validation plan

**Goal:** Build one wheel and one sdist from a clean checkout, independently validate their package
boundary, and retain only the wheel in CI for downstream installation tests.

**Architecture:** Canonical Just recipes separate destructive construction from read-only artifact
validation. A repository-internal command directly inspects archive structure, metadata, complete
package-member equality against the clean source checkout, and wheel `RECORD`, without extraction or
subprocess execution. CI composes the commands through `just verify` and uploads the validated wheel.

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
- The canonical global `just setup` command uses `uv sync --locked --link-mode copy` so setup is
  reliable when this repository and uv's cache are on different filesystems.

## Task 1: Validate existing wheel and sdist artifacts

**Files:** create `tests/validate_release_artifacts.py` and `tests/test_release_artifacts.py`; modify
`pyproject.toml` to close the sdist member set; reuse `tests/test_package_version.py` helpers only by
copying small test-specific setup until a third repetition justifies extraction.

**Interfaces:** `tests/validate_release_artifacts.py ARTIFACT_DIR PROJECT_ROOT` is a dedicated command
that validates caller-supplied existing archives against the explicit clean source checkout and
returns zero only when every invariant holds. It is not collected by ordinary pytest.
`tests/test_release_artifacts.py` imports its functions and tests the CLI; Task 2's
`verify-artifacts` recipe passes `dist .` directly.

1. Run `just setup` before the first `uv run --no-sync` command. Rerun setup before later no-sync
   checks if `pyproject.toml` or `uv.lock` changes.
2. Write a failing happy-path test that creates a clean tracked-project Git fixture, builds its
   wheel and sdist, and calls
   `validate_release_artifacts.main([artifact_dir, project_root])`. Assert normalized name; one
   valid version across both filenames, both archive roots, and both metadata documents; Python
   requirement; MIT license; console entry point; runtime dependencies;
   byte-for-byte package contents and sdist build inputs. Run
   `uv run --no-sync pytest -q --no-cov tests/test_release_artifacts.py`; expect import failure
   because the validator module is absent.
3. Implement the minimum archive and metadata inspection plus CLI in
   `tests/validate_release_artifacts.py`. Keep archive paths logical and do not extract into the
   checkout. Run the focused test; expect it to pass.
4. Add parameterized failing cases that independently mutate valid archives for absent artifact
   directory, missing/duplicate/unexpected artifacts, malformed archives, name/Python/license/
   each version-bearing filename/archive-root/metadata location plus one synchronized invalid
   version across all six locations,
   wheel/sdist entry-point and dependency mismatches, source/wheel/sdist complete package-member
   `.py` path/byte equality and sentinels (including synchronized omission, an extra non-Python
   package member, and
   independently byte-divergent wheel and sdist package files; update wheel `RECORD` in the wheel
   mutation so archive-record validation still passes),
   missing/non-regular/byte-divergent required sdist inputs and an unexpected sdist file outside the
   package tree, absolute/drive-like/backslash-bearing/non-NFC/empty-component/dot-component/
   escaping paths, canonical path collisions, non-regular package/
   metadata/required-input members, and wheel `RECORD` missing/extra/duplicate/wrong-size/wrong-digest
   rows. Every wheel mutation outside the `RECORD`-specific cases must update its digest and size
   row so the intended check is isolated. Each assertion must name the artifact and intended
   invariant. Run the focused test and confirm each new case fails before its corresponding check
   exists.
5. Add failing cases for absent/duplicate/malformed `WHEEL`, unsupported `Wheel-Version`, false or
   duplicate `Root-Is-Purelib`, missing/duplicate/filename-discordant tags, unexpected wheel
   top-level and `.data` payloads, each tar link/special member type plus escaping link targets,
   duplicate singleton core-metadata fields, and absent/unsupported `Metadata-Version` in both
   metadata documents. Preserve valid `RECORD` rows outside its focused cases. Run the focused test
   and confirm each case fails on its intended invariant.
6. Implement the minimum fail-closed direct-inspection checks and configure hatchling's sdist target
   to include only `src/hmc_mcp`, `scripts/versioning.py`, `pyproject.toml`, `README.md`, and
   `LICENSE`; hatchling additionally supplies `.gitignore` and `PKG-INFO`, which the validator
   includes in the closed byte-checked set. The validator must not extract an archive or invoke a
   build subprocess. Run the focused test; expect all cases to pass.
7. Mutate one version-consistency comparison, run its focused test and observe failure, restore the
   comparison, then rerun green.
8. Invoke `uv run --no-sync python tests/validate_release_artifacts.py <fixture-artifact-dir>
   <fixture-project-root>`;
   expect exit 0. Invoke it with the wrong arity, with a missing artifact directory, and with a
   missing project-root directory; expect nonzero status and actionable messages that identify the
   invalid input.
9. Run `just verify`; expect the pre-existing suite to remain green because the dedicated validator
   command is not a pytest test module and no recipe calls it yet.
10. Commit as `feat: validate built package artifacts`.

**Acceptance:** Existing archives are checked without extraction or rebuilding; malformed or
inconsistent inputs fail actionably.

**Rollback:** Revert the commit and remove only generated `dist/` through a recoverable trash
operation if cleanup is needed.

## Task 2: Define the canonical command and CI contracts

**Files:** modify `tests/test_ci_pipeline.py`, `justfile`, and `.github/workflows/ci.yml`.

**Interfaces:** Consumes Task 1's `tests/validate_release_artifacts.py ARTIFACT_DIR PROJECT_ROOT`.
Produces recipes
`build` and `verify-artifacts`, `dist/*.whl` as the wheel handoff, and matrix-unique CI artifact
names. Downstream issue #163 consumes the uploaded wheel.

1. Add failing tests asserting that `build` clears `dist/` and invokes
   `uv build --wheel --sdist --out-dir dist .`, `verify-artifacts` invokes only
   `uv run --no-sync python tests/validate_release_artifacts.py dist .`, and `verify` orders both
   after source checks. Run `uv run --no-sync pytest -q --no-cov tests/test_ci_pipeline.py`;
   expect those tests to fail.
2. Add failing tests asserting that CI uploads only `dist/*.whl` after `just verify`, uses
   `actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v7.0.1`, sets
   `if-no-files-found: error`, and names artifacts from matrix architecture and Python version.
   Run the same focused command; expect failure.
3. Implement the two Just recipes, compose them into `verify`, and add the wheel upload step.
   Run the focused command and Task 1's focused tests; expect all to pass.
4. Commit as `feat: add canonical artifact command graph`, leaving a clean checkout for the
   provenance-sensitive build.
5. Run `just build` and `just verify-artifacts`; expect one wheel, one sdist, and exit 0. Record the
   filenames for the review ledger.
6. Run `UV_NO_SYNC=1 uv run zizmor -qq --no-online-audits .github/workflows/`; expect exit 0.
7. Run `just verify`; expect exit 0, proving this commit's graph is independently green. If either
   gate exposes a defect, write a focused regression test where applicable, make the minimum
   correction, commit it separately, and repeat both gates from the resulting clean checkout.

**Acceptance:** The tested command graph builds and independently validates `dist/`; CI retains one
matrix-identified wheel and has no publication permission or credential.

**Rollback:** Revert the commit and move the generated `dist/` directory to trash. No external
package publication exists.

## Task 3: Prove clean-checkout provenance and full integration

**Files:** modify `tests/test_release_artifacts.py` and `tests/test_ci_pipeline.py` only if integration
coverage exposes a missing contract.

**Interfaces:** Consumes the Task 1 validator and Task 2 command graph. Produces the complete local
proof that CI runs unchanged.

1. Add an integration test copying only tracked files, initializing clean Git history, clearing
   inherited `VIRTUAL_ENV` and `UV_PROJECT_ENVIRONMENT`, and invoking `just setup`, `just build`, then
   `just verify-artifacts` inside that checkout; assert success and exactly one wheel/sdist. Run the
   focused test; expect it to pass against Tasks 1 and 2. Temporarily change the copied
   justfile's validator path to a missing file, rerun and observe failure, restore it, then rerun
   green to prove the integration assertion bites.
2. Add a dirty-checkout case that invokes `just build`, asserts nonzero status, and checks the
   existing actionable public-safe provenance message. Confirm it fails if construction bypasses
   the version backend.
3. Apply only corrections proven necessary by these tests, then rerun the focused suite; expect all
   tests to pass. If no correction is necessary, do not manufacture a code change for this task.
4. Commit the integration and dirty-checkout tests, together with any correction they proved, as
   `test: prove clean artifact workflow integration`.
5. Run `just setup`, then `just verify`; expect exit 0 and no warnings.
6. Run `UV_NO_SYNC=1 uv run prek run --all-files`; expect exit 0 and no warnings.
7. Re-read `git diff main...HEAD` for naming, scope, secrets, publication credentials, and needless
   abstraction. If either gate or the diff review exposes a defect, make the minimum correction in
   a separate commit and repeat both gates from the resulting clean checkout.

**Acceptance:** The canonical commands pass from a clean tracked checkout, dirty provenance fails
actionably, all guardrails pass, and the diff contains no publication path or credential.

**Rollback:** Revert Task 3 corrections separately; earlier independently tested command and
validator commits remain bisectable.

## Durable checkpoint

Branch: `feat/build-validate-artifacts-162`. Base: `main`. Guardrails: `just setup`, `just verify`,
and `UV_NO_SYNC=1 uv run prek run --all-files`. Assigned ADR: 0024. ADR index coupling: not coupled;
no index exists. Scope token: `b206ccc4-710f-4929-9cd1-13f8d5e232db`.
