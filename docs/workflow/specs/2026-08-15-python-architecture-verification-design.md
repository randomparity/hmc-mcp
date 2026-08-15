# Python-by-architecture verification design

## Goal and scope

Issue #163 completes pull-request verification for every supported CPython version on both native
GitHub-hosted architectures. The active matrix is the Cartesian product of Python 3.11, 3.12,
3.13, and 3.14 with amd64 and arm64. Each producer arm runs the canonical `just verify` graph and
uploads its validated wheel. A matching consumer arm downloads that exact retained wheel into a
fresh environment, installs it without the source checkout as an install source, and exercises the
installed CLI and MCP handshake.

[ADR 0020](../../adr/0020-rolling-cpython-support-policy.md) governs the explicit supported Python
set. [ADR 0021](../../adr/0021-bounded-qemu-ppc64le-ci.md) governs the architecture boundary: active
pull-request checks are native amd64 and arm64 only, while the inactive bounded ppc64le QEMU
release-artifact template remains unchanged and makes no pull-request coverage claim. [ADR
0024](../../adr/0024-separate-artifact-build-and-validation.md) governs the validated wheel handoff
from producer to downstream fresh-environment consumer. This design composes those accepted
decisions and introduces no new architecture decision, so it does not add ADR 0025.

The change is limited to the CI workflow, its contract tests, and these workflow design records. It
does not change package APIs, dependencies, supported platforms, the ppc64le template, publication,
permissions, credentials, or runner providers.

## Workflow architecture and data flow

The existing `ci` producer job expands from five entries to exactly eight explicit entries. The
matrix lists every `(architecture, runner, python-version)` tuple rather than using independent
axes, keeping runner selection reviewable and preventing an invalid architecture/runner pairing.
Its displayed name remains `<architecture> / Python <version> / verify`, so a failed producer
identifies the exact arm. Every producer executes `just setup`, `just verify`, uploads only
`dist/*.whl` under the existing matrix-unique artifact name, and runs all hooks.

A new `wheel-smoke` consumer job depends on successful completion of all producer arms and uses the
same eight explicit tuples. Its displayed name is `<architecture> / Python <version> / wheel smoke`.
Each consumer checks out the repository without credentials, installs the same pinned `uv`, and
downloads exactly `release-wheel-<architecture>-py<version>` into `dist/` with the immutable
`actions/download-artifact` v8.0.1 commit
`3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c`. Matrix-unique names establish a one-to-one producer
and consumer mapping; merge mode is not used.

The consumer creates `.wheel-venv` with the matrix Python. It exports the frozen production
dependency graph from `uv.lock` without the project, installs those exact dependencies into the new
environment, and then installs the sole downloaded wheel with `--no-deps`. It invokes only
executables from `.wheel-venv`: `hmc-mcp --help` plus every installed CLI group help path (`lpars`,
`storage`, `network`, `templates`, `metrics`) and `.wheel-venv/bin/python scripts/smoke_mcp.py`.
Before smoke, the consumer imports `hmc_mcp`, resolves its `__file__`, and fails unless that path is
beneath `.wheel-venv`; the package-boundary proof therefore does not depend only on the repository's
`src/` layout. The consumer does not run `just setup`, install the editable project, rebuild the
artifact, or resolve dependencies outside the lockfile.

The consumer's checkout supplies only the smoke script. The wheel is the sole package installation
source and was already validated by the corresponding producer's `just verify`. A missing,
duplicate, mismatched, or uninstallable artifact fails that named consumer arm. Shell globbing must
resolve exactly one wheel before installation; the workflow checks the count and reports the
architecture and Python version in the failure message.

## Failure behavior

GitHub reports each producer and consumer by architecture and Python version. Matrix fail-fast is
disabled within both stages, so one producer does not cancel the other producer arms and one
consumer does not cancel the other consumer arms. Producer failures remain attributable to the
canonical source and artifact verification graph. Consumer failures distinguish artifact download,
exact-count validation, locked dependency installation, wheel installation, installed CLI, and
installed MCP smoke steps.

The workflow has no retry loop or fallback architecture. An unavailable runner, missing artifact,
or failed smoke remains a failed arm. The job-level `wheel-smoke` dependency means any producer
failure skips the entire consumer stage even when seven wheels were retained; that intentionally
avoids presenting a partial fresh-wheel matrix as complete, at the cost of requiring a later green
producer run before any hidden consumer failure becomes visible. Producer job names remain the
primary diagnosis for that run. The scheduled Python-policy job remains separate and unchanged.

## Security model

### Boundary inventory and actors

The changed boundary is GitHub's artifact service transferring a wheel from a producer job to its
matching consumer job. Pull-request authors control repository source, the build configuration,
the wheel payload, and the smoke script. GitHub controls hosted runners and artifact storage. The
workflow trusts the accepted pinned Actions and locked Python dependency graph; it does not trust
artifact names or counts to select themselves safely.

### Controls

- Global `contents: read` remains the only workflow permission, and every checkout keeps
  `persist-credentials: false` with full history where project Git provenance is required.
- Upload and download actions use immutable full commit pins with version comments. The downloader
  requests one matrix-derived artifact name and one fixed destination; it does not merge artifacts
  or use a wildcard artifact selector.
- The producer validates the wheel before upload. The consumer fails unless `dist/` contains
  exactly one regular `.whl`, exports locked non-project production dependencies, installs those
  dependencies, then installs only that wheel path with dependency resolution disabled.
- No token, secret, cache credential, publication permission, package-index upload, self-hosted
  runner, or persistent environment is added. Existing timeouts bound both native job classes.
- Failure output names only public architecture, Python, step, and local artifact information; it
  does not print environment variables or credentials.

### Explicitly out of scope

The workflow does not defend against a pull-request author changing both the producer and its tests;
branch review and repository protections govern that actor. Supply-chain compromise of GitHub,
hosted runners, pinned actions, `uv`, or locked dependencies remains outside this change. The
inactive ppc64le template performs no work and is neither activated nor broadened.

## Verification

`tests/test_ci_pipeline.py` proves the exact eight-entry producer matrix and exact matching consumer
matrix, `fail-fast: false`, job-name identity, producer-to-artifact-to-consumer mapping, immutable
action pins, exact wheel count, fresh-environment installation, installed-package path assertion,
installed CLI and MCP commands, least privilege, and the absence of active ppc64le execution. It
continues to prove the retained ppc64le template byte-for-contract behavior.

The TDD proof first changes the expected matrix and consumer contract so the focused test fails on
the five-entry workflow. Implementation then makes that test pass. Required guardrails are `just
setup`, focused `uv run --no-sync pytest -q tests/test_ci_pipeline.py`, `just verify`, and
`UV_NO_SYNC=1 uv run prek run --all-files`. The branch is `feat/python-architecture-matrix-163`, the
base is `main`, the host is arm64, declared targets are amd64, arm64, and ppc64le, and the host is
included.
