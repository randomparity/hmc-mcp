# Git-derived package versions design

Decision: [ADR 0023](../../adr/0023-git-derived-package-versions.md)

## Scope and guarantees

Issue #159 replaces both static `0.1.0` declarations with one build-time computation. Tags must
match decimal `X.Y.Z` exactly. Among matching tags reachable from `HEAD`, the highest semantic
version is the base; annotated and lightweight tags are equivalent and nonmatching tags are
ignored. An exact tagged commit produces the tag version. Other commits produce
`<next-version>.devN+g<sha>`, where `N` is `git rev-list --count TAG..HEAD` and `sha` is Git's
seven-character abbreviated object name. Dirty and shallow
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
after the nearest tag. Exact tags bypass formatting and remain clean. No default/fallback
version is configured. `hmc_mcp.__version__` uses `importlib.metadata.version("hmc-mcp")`, so
runtime and artifact metadata share one authority.

Active CI jobs that run `uv` against the project set `fetch-depth: 0`. This is required input
preparation, not a provenance bypass.

## Error handling

- A shallow repository raises `RuntimeError` identifying shallow history and instructing the
  operator to fetch full history.
- A dirty repository raises `RuntimeError` identifying uncommitted changes and instructing the
  operator to commit or clean them.
- An unknown or missing `release-line` raises `ValueError` listing exactly the accepted values.
- Missing/corrupt Git data and malformed release tags remain hard Versioningit/build failures;
  there is no fallback version.

## Tests

Focused tests create isolated temporary Git repositories and exercise exact tags, highest-version
selection across reachable tags, ignored malformed/prefixed tags, commits after tags, dirty state,
shallow clones, no-tag history, all three release-line transitions, and invalid selectors. Build
tests inspect wheel contents and metadata, rebuild a wheel from the sdist, exercise editable
installation, and import the installed package version path.
Supply-chain tests cover the pinned direct build requirements, and workflow tests assert full
checkout depth for every active job that invokes `uv` on the project. The final gates are
`just verify` and `uv run prek run --all-files`.

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
separately CI-gated `uv run prek run --all-files`.
