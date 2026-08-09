# Local and GitHub CI Design

## Scope authority

- **Interaction:** unattended campaign dispatch.
- **Scope identity:** issue #48 and `scope-48-20260809-a1`.
- **Outcome:** coordinated local and GitHub CI with setup automation, lint,
  type/static checks, prek hooks, secret detection, and GitHub Actions.
- **Completion criteria:** the public issue requires Ruff, type checking,
  linting, secret detection, and a `setup` target that prepares the virtual
  environment and hooks. Campaign triage requires the local and hosted paths
  to be coordinated while retaining `just verify` and existing pytest coverage.
- **Provenance:** public issue #48, campaign triage, and campaign orchestrator
  authorization to track `.secrets.baseline` after the existing test fixtures
  were shown to require it.
- **Exclusions:** no application behavior changes and no requirements beyond
  the named CI/setup surface.
- **Permitted surface:** `justfile`, `pyproject.toml`, `uv.lock`,
  `.pre-commit-config.yaml`, `.secrets.baseline`, `.github/workflows/*`, ADR
  0002, this design, the implementation plan, and directly required tests/docs.
- **Ambiguities:** none. Exact tool and workflow configuration is an
  implementation choice constrained by the outcomes above.

## Goal

A contributor can bootstrap the development environment and hooks with one
command, run every required check with one command, and get the same result in
GitHub Actions. A check failure must identify the focused recipe that failed.

## Current state

`just verify` runs 502 pytest tests, the MCP handshake, and every CLI command
group. Ruff 0.15.22 reports the current tree lint-clean, but Ruff formatting
would rewrite 77 application and test files. ty 0.0.62 reports pre-existing
diagnostics across the complete tree, while the configuration, document
builder, and error-contract modules pass under ty's normal rules. detect-secrets
1.5.0 finds seven intentional credential-shaped values in tracked tests.

These observations constrain the rollout: preserve existing verification,
introduce strict checks on honest clean boundaries, and baseline intentional
secret fixtures without excluding tests.

## Approaches

### Recommended: just recipes as the shared command graph

Add focused recipes for lint, type checking, and secret scanning, then compose
them into `verify`. Configure local hooks to call the focused recipes and the
hosted workflow to call `setup` and `verify`. This gives every entry point one
implementation and keeps failures independently runnable.

### Alternative: hook configuration as the command graph

GitHub Actions and developers could both run `prek run --all-files`. This is
compact but turns a hook manager into the public verification interface, makes
focused troubleshooting less obvious, and repeats existing `just verify`
coverage or forces it behind a hook.

### Alternative: separate local and workflow commands

The workflow could spell out uv, Ruff, ty, secrets, and pytest directly while
hooks use their native entries. This avoids wrapper recipes but creates two
definitions for each gate and does not satisfy coordinated behavior over time.

## Command contract

The `justfile` will expose:

- `setup`: run locked dependency synchronization, then install the configured
  hooks with project-pinned prek. Repeated runs are safe.
- `lint`: run Ruff lint over the repository.
- `typecheck`: run ty against the explicit clean module include set from
  `pyproject.toml`.
- `secrets`: run detect-secrets across every tracked file using the committed
  baseline and offline verification behavior.
- `static`: compose lint, type checking, and secret scanning.
- `verify`: compose static checks, the existing tests and smoke check, and the
  existing CLI-loading assertions.

`setup` fails on lock drift rather than silently updating the lock. Dependency
changes remain explicit `uv lock` work. Focused recipes use tools pinned in the
dev dependency group, so local and hosted runs resolve the same versions.

## Tool configuration

The dev group pins Ruff 0.15.22, ty 0.0.62, prek 0.4.10, and detect-secrets
1.5.0, the current stable releases verified from their official PyPI project
pages on 2026-08-09. Ruff lint uses its default rule set because the tree is
already clean. Ruff formatting is not enabled by this issue.

ty's source include list names `src/hmc_mcp/config.py`,
`src/hmc_mcp/documents.py`, and `src/hmc_mcp/errors.py`. No diagnostic category
is disabled. This supplies a real, strict type gate while keeping the existing
application diagnostics visible as future work rather than disguising them as
success.

The detect-secrets baseline is generated from the current tracked tree and
reviewed to ensure its entries correspond only to intentional test fixtures.
The scan never excludes `tests/`. Both the hook and `just secrets` use the same
baseline and disable network verification for deterministic/offline runs.

## Hook behavior

`.pre-commit-config.yaml` defines local system hooks managed by prek. Hooks
invoke project-pinned tools through uv and never download a second tool copy.
Ruff receives staged Python filenames. The type check runs once without passed
filenames because its reviewed include boundary is project-level. Secret
detection receives all staged text files and the shared baseline.

`just setup` installs the pre-commit hook type. `prek run --all-files` is an
additional verification arm to prove the committed hook configuration works;
the canonical `just verify` recipes remain the hosted gate.

## GitHub Actions

`.github/workflows/ci.yml` runs for pull requests and pushes to `main` on
`ubuntu-24.04`. It grants only `contents: read`, cancels superseded runs for the
same ref, checks out without persisted credentials, installs uv and just with
immutable action SHAs and version comments, and pins just 1.58.0. The action
versions are current releases verified from their official GitHub repositories
on 2026-08-09.

The job runs `just setup`, `just verify`, and `uv run prek run --all-files`.
The last arm catches a malformed or drifting hook configuration even if the
underlying recipes pass. There are no path-filtered jobs and therefore no
required-check deadlock.

## Failure behavior

Every command returns the underlying tool's non-zero status. Setup reports lock
drift or hook installation failure directly. Secret findings display the
detector type and path without printing secret values. GitHub Actions has no
write permission and stores no project credentials.

## Threat model

### Boundaries and actors

- **Pull-request content to a hosted CI runner:** an untrusted contributor can
  alter repository files executed by CI. The workflow uses no repository
  secrets and has read-only contents permission.
- **Third-party actions to runner execution:** action maintainers publish code
  that GitHub executes. Every action is pinned to a reviewed full commit SHA
  with a release comment; checkout does not persist credentials.
- **Dependency metadata to executed developer tools:** maintainers control
  `pyproject.toml` and `uv.lock`. Setup uses locked synchronization and all
  direct tool dependencies are exact pins.
- **Tracked content to secret detector:** contributors control scanned text.
  Offline scanning bounds behavior to local content and reports locations and
  detector types. The baseline suppresses only reviewed current fixture hashes.
- **Local setup to Git hooks:** a local operator explicitly runs `just setup`.
  prek modifies only the repository's standard hook path using the committed
  configuration.

No existing boundary is widened to application runtime or HMC credentials.

### Out of scope

This change does not scan git history, rotate a real credential, configure
GitHub branch protection, or make the full legacy tree type-clean/formatted.
GitHub platform isolation and the security of pinned upstream commits remain
external trust assumptions; immutable pins limit surprise updates but do not
prove upstream code is benign.

## Verification

Configuration tests will parse the justfile, pyproject, hook configuration,
secret baseline, and workflow to enforce the shared commands, exact dependency
pins, strict type include boundary, read-only permissions, action SHA pins, and
required workflow steps.

TDD will first demonstrate failures against the absent configuration. The
implementation then must pass the configuration tests, `uv lock --check`, each
focused new recipe, `uv run prek run --all-files`, workflow lint/security checks
(`actionlint` and `zizmor`), and the full `just verify` suite. The secrets test
will also inject a temporary credential-shaped tracked file, prove the scanner
fails, remove it, and prove the normal scan succeeds.

## Decision record

[ADR 0002](../../adr/0002-unify-local-and-hosted-ci.md) records the shared
command graph and incremental enforcement boundaries.
