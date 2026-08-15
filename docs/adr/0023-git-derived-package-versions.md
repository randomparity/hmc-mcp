# ADR 0023: Git-derived package versions

## Status

Accepted

## Context

`hmc-mcp` declares `0.1.0` independently in package metadata and runtime code. That
duplication cannot identify artifacts built between releases. The repository also needs a
deliberate patch, minor, or major next-release line, and must reject builds whose Git state
cannot prove their provenance.

## Decision

Use pinned Hatchling 1.32.0 with Versioningit 3.3.0 as the PEP 517 backend and dynamic
version provider. Project-owned methods validate a full, clean Git worktree and compute the
next release from the single `release-line` setting in `pyproject.toml`, whose only valid
values are `patch`, `minor`, and `major`.

Only tags matching canonical decimal `X.Y.Z` exactly are release tags: each component has no
leading zero unless it is the single digit `0`. Annotated and lightweight tags are equivalent
after resolving them to commits. The base is the highest semantic version among
release tags reachable from `HEAD`. If `HEAD` is that tag's commit, the result is `X.Y.Z`.
Otherwise the result is `<next-version>.devN+g<sha>`, where `N` is the number of commits in
`TAG..HEAD`. Nonmatching tags are ignored. A full-history repository with no release tags uses
the semantic origin `0.0.0` and counts every commit reachable from `HEAD`, so its first commit is
development commit 1. Dirty or shallow repositories fail before metadata is produced. Runtime
version access reads installed distribution metadata.

CI jobs that install the project fetch full Git history so the same provenance checks apply
locally and in hosted builds.

## Consequences

- Package builds require Git provenance unless building from metadata already embedded in an
  sdist by the backend.
- Development versions sort before their selected next release and identify the source commit.
- Changing the next release line is one explicit configuration edit.
- The build dependency graph replaces `uv_build` with two pinned packages and their locked
  transitive dependencies.
- Backend compatibility tests must prove the expected wheel package contents, sdist-to-wheel
  rebuild, editable installation, and installed metadata; version correctness alone is not
  sufficient evidence for replacing `uv_build`.

## Considered & rejected

- **Keep static metadata.** This preserves duplicate, source-ambiguous versions.
- **Use an environment variable or branch name as the selector.** These are less reproducible
  and do not provide the authorized explicit project setting.
- **Use setuptools-scm with setup.py or a custom plugin.** Custom patch/minor/major selection
  requires a less direct legacy or separately installed integration.
- **Wrap uv_build in a project PEP 517 backend.** This retains the current backend at the cost
  of more bespoke metadata-hook code than the chosen Versioningit extension points.
- **Supply a fallback version for shallow or missing history.** That creates another authority
  and can publish artifacts whose source cannot be proven.
