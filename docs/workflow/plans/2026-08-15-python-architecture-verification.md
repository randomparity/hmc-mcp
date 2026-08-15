# Python-by-architecture verification implementation plan

**Goal:** Run canonical verification and fresh-wheel installed smoke for every native amd64/arm64
and Python 3.11–3.14 combination while preserving the inactive ppc64le release template.

**Architecture:** One explicit eight-arm producer matrix runs `just verify` and uploads one
matrix-identified validated wheel. A dependent explicit eight-arm consumer matrix downloads the
corresponding wheel, installs it into a new environment, and runs installed CLI and MCP smoke paths.
The workflow composes accepted ADRs 0020, 0021, and 0024.

**Tech stack:** GitHub Actions YAML, Python/pytest contract tests, `uv`, `just`, and pinned artifact
actions.

## Global constraints

- Supported Python versions are exactly 3.11, 3.12, 3.13, and 3.14.
- Active pull-request architectures are native amd64 on `ubuntu-24.04` and native arm64 on
  `ubuntu-24.04-arm`.
- The active matrix contains all eight Python-by-architecture combinations.
- Every producer arm runs the complete `just verify` suite and uploads only its validated wheel.
- Every matching consumer arm installs that wheel into a fresh environment and exercises installed
  CLI and MCP smoke paths.
- Job names identify architecture and Python version, and both matrices disable fail-fast.
- ADR 0021's inactive bounded ppc64le QEMU release-artifact template remains unchanged; no native,
  active, or required ppc64le pull-request claim is introduced.
- Workflow permissions remain `contents: read`; checkout credentials remain disabled; action
  references remain immutable full commits with version comments.
- Branch `feat/python-architecture-matrix-163`; base `main`. Host architecture `arm64`; declared
  targets `amd64`, `arm64`, and `ppc64le`; relationship `included`.
- Guardrails: `just setup`; focused `uv run --no-sync pytest -q tests/test_ci_pipeline.py`; `just
  verify`; `UV_NO_SYNC=1 uv run prek run --all-files`.

## Task 1: Specify and implement the complete producer and consumer matrices

**Files:** Modify `tests/test_ci_pipeline.py` and `.github/workflows/ci.yml`.

**Interfaces:** Consumes the existing `SUPPORTED_PYTHONS`, `NATIVE_MATRIX`, action-pin contract,
`just verify`, artifact name `release-wheel-${{ matrix.architecture }}-py${{
matrix.python-version }}`, and `dist/*.whl`. Produces an eight-tuple `NATIVE_MATRIX`, a `ci` producer
job, and a `wheel-smoke` consumer job whose tuple and artifact interfaces are identical.

1. Change `NATIVE_MATRIX` in `tests/test_ci_pipeline.py` to construct four amd64 and four arm64
   tuples from `SUPPORTED_PYTHONS`. Add the immutable download-action pin
   `actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c  # v8.0.1` to
   `ACTION_PINS`.
2. Extend `test_github_ci_uses_a_bounded_native_architecture_matrix` to require `fail-fast: false`,
   producer name `${{ matrix.architecture }} / Python ${{ matrix.python-version }} / verify`, and
   the exact eight-entry matrix. Add a focused test that extracts `wheel-smoke`, requires `needs:
   ci`, its matching explicit matrix and name, checkout/setup/download pins, the exact matrix-derived
   artifact name, destination `dist`, exact-one-wheel validation, `.wheel-venv` creation, frozen
   non-project dependency export and installation, wheel installation with `--no-deps`, every
   installed CLI help path, and installed MCP smoke. Assert it never runs `just setup`, installs
   the project checkout, resolves dependencies outside `uv.lock`, merges artifacts, or activates
   ppc64le.
3. Run `uv run --no-sync pytest -q tests/test_ci_pipeline.py`. Expect failure because arm64 has only
   Python 3.11, `fail-fast` and `/ verify` are absent, the download pin is absent, and `wheel-smoke`
   does not exist. Preserve this red result in the forge ledger.
4. Expand `.github/workflows/ci.yml` to the exact eight producer tuples, set `fail-fast: false`, and
   suffix its name with `/ verify`. Add `wheel-smoke` with `needs: ci`, the matching eight tuples,
   the same runner and Python setup, credential-free checkout, the pinned downloader, and a
   10-minute timeout.
5. In the consumer, download exactly the matching artifact into `dist`; validate with a Bash array
   that exactly one regular `dist/*.whl` exists and otherwise prints `architecture=<value>
   python=<value>: expected exactly one wheel`. Create `.wheel-venv` with the matrix interpreter,
   run `uv export --frozen --no-dev --no-emit-project --no-header --output-file
   .wheel-requirements.txt`, then install the exported locked requirements. Install only the
   validated wheel with `uv pip install --no-deps --python .wheel-venv/bin/python`. Run
   `.wheel-venv/bin/hmc-mcp --help` and all five group help paths, then run
   `.wheel-venv/bin/python scripts/smoke_mcp.py`.
6. Run `uv run --no-sync pytest -q tests/test_ci_pipeline.py`. Expect all tests in the file to pass.
7. Run `just workflow-security`. Expect exit 0 with no zizmor findings. Review
   `git diff --check` and the workflow diff, confirming the delimited ppc64le block is unchanged.
8. Commit with `git commit -m "ci: verify every native Python architecture arm"`.

**Acceptance criteria:** Tests prove exactly eight producer and eight matching consumer arms;
matrix/job failures identify architecture and Python; each consumer installs exactly its producer's
wheel into a fresh environment and exercises installed CLI/MCP; permissions and pins remain bounded;
the ppc64le template is unmodified and inactive.

**Rollback:** Revert the task commit. No persistent data, published package, runner registration, or
external configuration requires cleanup.

## Task 2: Verify the integrated workflow contract

**Files:** Modify only the design or test wording if a guardrail exposes a factual mismatch; do not
broaden the implementation surface.

**Interfaces:** Consumes the completed workflow and tests from Task 1. Produces a fully verified
branch suitable for adversarial review and delivery.

1. Run `just setup`. Expect the locked environment and hooks to install successfully.
2. Run `just verify`. Expect pytest, static checks, MCP smoke, CLI help paths, artifact construction,
   and artifact validation all to exit 0.
3. Run `UV_NO_SYNC=1 uv run prek run --all-files`. Expect every hook to pass.
4. Run `git diff --check` and inspect `git status --short --untracked-files=all`. Expect no whitespace
   errors and only intended tracked design/workflow/test changes before the final documentation
   commit.
5. Commit the durable spec and plan separately with `git commit -m "docs: specify native matrix
   verification"` if they were not committed before Task 1; otherwise leave the prior design commit
   intact. Do not combine later review fixes into either implementation or design commits.

**Acceptance criteria:** All required guardrails pass from the external worktree, the documented
contract matches the workflow byte-for-contract tests, and commit history keeps design,
implementation, and review fixes logically separated.

**Rollback:** Revert only the commit whose behavior or documentation is being removed; the two
logical commits remain independently bisectable.
