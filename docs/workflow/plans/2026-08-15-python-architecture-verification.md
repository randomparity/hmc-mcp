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
- Guardrails: `just setup`; focused `uv run --no-sync pytest -q --no-cov tests/test_ci_pipeline.py`; `just
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
3. Run `uv run --no-sync pytest -q --no-cov tests/test_ci_pipeline.py`. Expect failure because arm64 has only
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
   `.wheel-venv/bin/hmc-mcp lpars --help`, `.wheel-venv/bin/hmc-mcp storage --help`,
   `.wheel-venv/bin/hmc-mcp network --help`, `.wheel-venv/bin/hmc-mcp templates --help`, and
   `.wheel-venv/bin/hmc-mcp metrics --help`. Before MCP smoke, run a Python assertion that resolves
   `hmc_mcp.__file__` and requires it to be beneath the resolved `.wheel-venv` path. Then run
   `.wheel-venv/bin/python scripts/smoke_mcp.py`.
6. Run `uv run --no-sync pytest -q --no-cov tests/test_ci_pipeline.py`. Expect all tests in the file to pass.
7. Run `just workflow-security`. Expect exit 0 with no zizmor findings. Review
   `git diff --check` and the workflow diff, confirming the delimited ppc64le block is unchanged.
8. Stage only `.github/workflows/ci.yml` and `tests/test_ci_pipeline.py`, then commit with `git
   commit -m "ci: verify every native Python architecture arm"`.

**Acceptance criteria:** Tests prove exactly eight producer and eight matching consumer arms;
matrix/job failures identify architecture and Python; each consumer installs exactly its producer's
wheel into a fresh environment and exercises installed CLI/MCP; permissions and pins remain bounded;
the ppc64le template is unmodified and inactive.

**Rollback:** Revert the task commit. No persistent data, published package, runner registration, or
external configuration requires cleanup.

## Task 2: Verify the integrated workflow contract

**Files:** No planned modifications. Any evidence-backed correction gets its own explicitly staged
review-fix commit and must remain inside the frozen surface.

**Interfaces:** Consumes the completed workflow and tests from Task 1. Produces a fully verified
branch suitable for adversarial review and delivery.

1. Run `just setup`. Expect the locked environment and hooks to install successfully.
2. Run `just verify`. Expect pytest, static checks, MCP smoke, CLI help paths, artifact construction,
   and artifact validation all to exit 0.
3. Run `UV_NO_SYNC=1 uv run prek run --all-files`. Expect every hook to pass.
4. Run `git diff --check` and inspect `git status --short --untracked-files=all`. Expect no whitespace
   errors and an empty tree. The durable spec and plan are prerequisites committed before Task 1;
   do not sweep them into the implementation commit. Every commit explicitly stages only its named
   files. Do not combine later review fixes with implementation or design commits.

**Acceptance criteria:** All required guardrails pass from the external worktree, the documented
contract matches the workflow byte-for-contract tests, and commit history keeps design,
implementation, and review fixes logically separated.

**Rollback:** Revert only the commit whose behavior or documentation is being removed; the two
logical commits remain independently bisectable.

## Task 3: Observe every native arm on the delivered branch head

**Files:** No repository modifications. This is delivery evidence collected after the branch is
pushed and the pull request exists.

**Interfaces:** Consumes the immutable delivered HEAD SHA and pull-request number from the delivery
phase. Produces evidence that GitHub Actions executed every required producer and consumer check for
that exact SHA.

1. Resolve the pull request with `gh pr view <PR> --repo randomparity/hmc-mcp --json
   headRefOid,headRefName,baseRefName,mergeable,mergeStateStatus`. Require `headRefOid` to equal the
   full reviewed `git rev-parse HEAD`, head `feat/python-architecture-matrix-163`, and base `main`.
2. Poll `gh pr checks <PR> --repo randomparity/hmc-mcp --json name,state` on a backing-off interval
   until checks reach terminal states. Expect one successful check for each exact name `<amd64|arm64>
   / Python <3.11|3.12|3.13|3.14> / verify` and one for each matching name ending `/ wheel smoke`.
3. Treat any absent, duplicate, skipped, cancelled, timed-out, or failed required arm as incomplete.
   Also require every other required repository check to succeed and the pull request to report
   `MERGEABLE` with merge state `CLEAN`; after one evidence-backed correction, surface another
   failure instead of polling indefinitely.

**Acceptance criteria:** The exact delivered branch head has 16 successful matrix checks, all other
required checks are green, and the pull request is clean and mergeable. Every observed job name
identifies its architecture, Python version, and producer or consumer stage.

**Rollback:** None; this task is read-only. A failed live arm returns to diagnosis and a new reviewed
commit rather than changing external infrastructure or retrying blindly.
