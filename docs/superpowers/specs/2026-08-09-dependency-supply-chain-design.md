# Dependency Supply-Chain Tightening Design

## Scope and authority

Issue #26 requests locked dependency sources and Dependabot. The campaign triage makes the
completion surface explicit: controlled runtime, development, and build declarations; a
reproducible `uv.lock`; and Dependabot configuration for uv. The frozen scope is limited to
`pyproject.toml`, `uv.lock`, `.github/dependabot.yml`, ADR 0001, these design artifacts, and
directly required tests or documentation. Application behavior, dependency substitutions,
workflow changes, and unrelated upgrades are excluded.

ADR [0001](../../adr/0001-pin-direct-dependencies-and-automate-updates.md) governs the
pinning and update policy.

## Design

All direct dependencies in `[project].dependencies`, `[dependency-groups].dev`, and
`[build-system].requires` will use exact PEP 440 `==` constraints at their currently
declared releases. `uv lock` will re-resolve the existing committed universal lockfile from
those declarations, and the lockfile will remain generated rather than hand-edited.

`.github/dependabot.yml` will contain one root-level `uv` update entry. It will run weekly,
apply a seven-day cooldown to new version releases, and group all version updates so that a
coherent manifest-and-lockfile change is reviewed together. No credentials, private
registries, auto-merge behavior, workflow permission changes, or GitHub Actions updater are
added.

## Alternatives

1. **Exact direct pins plus uv lock and grouped updates (selected).** This satisfies both
   declaration control and transitive reproducibility, while Dependabot supplies the
   maintenance path.
2. **Broad declarations plus uv lock.** This preserves library-style compatibility but does
   not control installs that consume project metadata without the repository lock.
3. **Git tags or commit SHAs.** This is unsuitable for registry dependencies and discards
   the established wheel/index distribution path.

## Verification

A policy test will parse `pyproject.toml` and `uv.lock` with the standard library and assert
that every direct runtime, development, and build requirement is exactly pinned, every pin
appears at that version in the lock, and the Dependabot configuration contains the selected
uv schedule, grouping, and cooldown. The test will first fail on the current broad direct
constraints and missing Dependabot configuration. Before that red run, `uv lock --check`
and `uv sync --locked` will validate the baseline and provision the worktree without
refreshing the lock; the test then runs through `.venv/bin/python` rather than `uv run`.
After implementation, a clean-tree `uv lock --check` will prove the committed lock matches
the manifest before `just verify` executes any `uv run` command. This ordered proof is a
maintainer and pull-request review responsibility because workflow changes are excluded and
the repository has no checked-in CI workflow.

## Failure handling

Dependency resolution failure is a hard failure: do not commit a stale or partial lockfile.
A policy mismatch fails the test with the affected dependency or missing configuration
field. Dependabot update PRs require the same ordered local proof and human review; this
design does not claim an automated merge gate that the repository does not contain.

## Threat model

### Boundary inventory

- **Existing boundary widened:** package registry metadata and artifacts enter developer,
  test, build, and runtime environments through uv. The change narrows accepted direct
  versions and records transitive versions.
- **New boundary:** GitHub's Dependabot service may propose repository changes to the
  dependency manifest and lockfile. It receives no repository secret from this config and
  does not gain merge authority.

### Actor model

Untrusted parties are publishers or compromised accounts able to release dependency
versions, and malicious transitive packages reachable through the public registry. GitHub
and the public Python package index remain trusted to provide their documented update and
distribution services. Maintainers remain responsible for reviewing and merging updates.

### Controls

- Exact direct pins and the committed hash-bearing uv lockfile bound resolution to reviewed
  releases and artifacts.
- A seven-day version cooldown reduces immediate exposure to newly published releases;
  security updates are not delayed by that cooldown.
- Grouped Dependabot PRs keep the direct declarations and complete resolution together, and
  the required ordered local proof checks the proposed graph before maintainers merge it.
- Dependabot receives no credentials, permissions expansion, or auto-merge path from the
  configuration.

### Explicitly out of scope

This change does not add artifact signing, an alternate registry, runtime sandboxing,
automatic vulnerability remediation, or GitHub Actions dependency updates. Those are not
required by issue #26 and are separate trust-boundary decisions.
