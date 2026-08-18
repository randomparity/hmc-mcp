# Git-derived package versions design

> **Superseded by [ADR 0033](../../adr/0033-static-package-version.md)** (2026-08-18)


Decision: [ADR 0023](../../adr/0023-git-derived-package-versions.md)

## Scope and guarantees

Issue #159 replaces both static `0.1.0` declarations with one build-time computation. Tags must
match canonical decimal `X.Y.Z` exactly; each component has no leading zero unless it is `0`.
Among matching tags reachable from `HEAD`, the highest semantic
version is the base; annotated and lightweight tags are equivalent and nonmatching tags are
ignored. If `HEAD` is the selected highest-version base tag's commit, it produces the clean tag
version. Other commits produce
`<next-version>.devN+g<sha>`, where `N` is `git rev-list --count TAG..HEAD` and `sha` is Git's
repository-unique abbreviation with a minimum length of seven characters. Dirty and shallow
repositories fail with an error that names the invalid state and remediation. No publishing,
tag creation, or release automation is added.

The single release-line input is `tool.versioningit.next-version.release-line` in
`pyproject.toml`; it accepts exactly `patch`, `minor`, or `major`. From tag `1.2.3`, these
select `1.2.4`, `1.3.0`, and `2.0.0`. With no tags, the same rules apply to semantic origin
`0.0.0`, producing `0.0.1`, `0.1.0`, and `1.0.0`.

## Architecture

`pyproject.toml` uses dynamic project version metadata, Hatchling 1.32.0, and Versioningit
3.3.0. `scripts/versioning.py` provides two narrow build-time functions:

- `describe_git(project_dir, params)` rejects shallow and dirty repositories, selects the highest
  reachable strict release tag, and returns Versioningit's description fields. With no release
  tag it uses internal semantic origin `0.0.0` and `git rev-list --count HEAD`.
- `next_release(version, branch, params)` validates the one `release-line` parameter and bumps
  the requested semantic component.

Versioningit's basic formatter emits `{next_version}.dev{distance}+{vcs}{rev}` for commits
after the selected base tag. Exact tags bypass formatting and remain clean. No default version is
configured. `hmc_mcp.__version__` uses `importlib.metadata.version("hmc-mcp")`, so
runtime and artifact metadata share one authority.

Hatchling writes the computed version into the sdist's `PKG-INFO`. Versioningit's standard
sdist fallback must read that embedded value when rebuilding without `.git`; this preserves
already-proven artifact metadata rather than inventing a fallback version. A Git-less source
copy without valid `PKG-INFO` fails as provenance-incomplete.

Active CI jobs that run `uv` against the project set `fetch-depth: 0`. This is required input
preparation, not a provenance bypass.

`just setup` performs the one explicit `uv sync --locked`. Every post-setup `justfile` tool uses
`uv run --no-sync`; the separately invoked hook gate uses
`UV_NO_SYNC=1 uv run prek run --all-files`. CI, contributor documentation, and command-contract
tests use those same forms. This lets guardrails inspect a dirty `pyproject.toml` without asking
the build backend to produce metadata from a dirty tree; it does not relax artifact rejection.

## Error handling

- A shallow repository raises `RuntimeError` identifying shallow history and instructing the
  operator to fetch full history.
- A dirty repository raises `RuntimeError` identifying uncommitted changes and instructing the
  operator to commit or clean them. Staged changes, tracked unstaged changes, and all untracked
  files count as dirty; ignored files do not.
- An unknown or missing `release-line` raises `ValueError` listing exactly the accepted values.
- Tags outside strict decimal `X.Y.Z` are ignored. A Git-less source tree without valid sdist
  `PKG-INFO`, corrupt Git data, or invalid embedded metadata is a hard build failure; there is no
  configured fallback version.

## Tests

Focused tests create isolated temporary Git repositories and exercise exact tags, highest-version
selection across reachable tags, a lower-version tag on `HEAD` beneath a higher reachable base,
ignored malformed, prefixed, and leading-zero tags, commits after tags, dirty state,
including staged, tracked unstaged, untracked source, and ignored-file cases; shallow clones,
no-tag history, repository-unique abbreviation resolution, all three release-line transitions,
and invalid selectors. Build tests inspect wheel contents and metadata, rebuild a wheel from the
sdist, exercise editable
installation, import the installed package version path, and reject a Git-less ordinary source
copy with no `PKG-INFO`.
Supply-chain tests cover the pinned direct build requirements, and workflow tests assert full
checkout depth for every active job that invokes `uv` on the project. A regression fixture first
synchronizes a clean copy, dirties `pyproject.toml`, and proves canonical just/prek commands reach
their tools without a rebuild. The final gates are `just verify` and
`UV_NO_SYNC=1 uv run prek run --all-files` after `just setup`.

## Threat model

The new boundary is local Git metadata supplied to the build backend by a developer or CI job.
The actor is a local operator or CI checkout, not a remote HMC user. Exact tag syntax and the
closed selector values bound version output; dirty and shallow checks reject incomplete state;
Versioningit and PEP 440 parsing reject malformed versions. Failure messages reveal only local
repository state categories, not secrets or paths. Dependency pins and the existing lock/supply-
chain gates control the added backend packages. CI credential handling remains unchanged with
`persist-credentials: false`. Malicious Git executables, compromised build dependencies, and tag
authorization policy are outside this change; existing workstation/CI trust and dependency review
own those risks.

## Durable checkpoint

Branch `feat/git-derived-versions-159`; base `main`. Host architecture `arm64`; target
architectures `amd64` and `arm64`; relationship included. Guardrails are `just verify` and the
separately CI-gated `UV_NO_SYNC=1 uv run prek run --all-files`, both after `just setup`.
