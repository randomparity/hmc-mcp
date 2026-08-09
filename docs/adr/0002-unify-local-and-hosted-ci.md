# 0002: Unify local and hosted CI behind just recipes

## Status

Accepted

## Context

The repository has a `just verify` recipe for tests, the MCP smoke handshake,
and CLI loading, but no hosted CI workflow or committed hook configuration.
Issue #48 requires setup automation, linting, type checking, static checks,
secret detection, and GitHub Actions. Duplicating commands between hooks and
workflow YAML would let those entry points drift.

The current tree passes Ruff lint but is not globally Ruff-formatted or
type-clean. Enabling broad checks by reformatting or suppressing existing
diagnostics would expand this CI change into an application-wide rewrite.
Existing test fixtures also contain intentional credential-shaped values, so
secret scanning needs a reviewed baseline rather than a test exclusion.

## Decision

The `justfile` is the single command graph. It exposes focused lint,
type-check, secret-scan, workflow-security, test, and smoke recipes; `verify`
composes all of them. The `setup` recipe performs a locked `uv sync` and
installs hooks with the project-pinned `prek` executable. Local hooks and
GitHub Actions invoke these recipes instead of copying their commands.

Ruff initially gates lint only. ty initially gates the already-clean typed
contract modules (`config.py`, `documents.py`, and `errors.py`) through an
explicit include list, retaining ty's normal strict diagnostics for those
files. The boundary is visible configuration, not blanket rule suppression.
Secret detection scans tracked content against a committed baseline that
records existing intentional fixtures and the scanner command's self-reference;
tests and the justfile remain in the scan. An option terminator keeps tracked
filenames from changing scanner configuration. The workflow-security recipe
runs project-pinned zizmor without mutable online audit inputs.

GitHub Actions uses read-only repository permissions, immutable action SHAs,
exact uv and just runtime versions, a bounded job, locked dependency
synchronization, and the same `just verify` entry point.

## Consequences

Developers can reproduce hosted checks with `just setup` followed by
`just verify`, and installed hooks run the same focused gates before commits.
Changing a gate requires changing its just recipe once.

The first type-check boundary is intentionally incremental, and Ruff format is
not yet a gate. Expanding either boundary requires making the added files clean;
it must not be done by globally disabling diagnostics. The secret baseline must
be reviewed when intentional fixture values change. Every new or changed
baseline entry is a security-sensitive bypass and must be matched to its
intentional fixture or scanner self-reference during review; a baseline update
is not evidence that a finding is safe.

Partial type coverage is an accepted residual of this change, not a claim that
the repository is globally type-clean. Maintainers should add a production
module to the explicit include set whenever they make that module clean, and
new production modules should enter the set before merge when they pass ty.
This is a manual ratchet: CI cannot prove coverage growth while existing modules
remain outside the boundary, so reviewers still own that check.

The CI workflow depends on pinned checkout, uv, and just-setup actions plus
locked Python development dependencies, including the workflow auditor.
Dependabot's existing uv configuration maintains the Python pins; action SHA
updates remain ordinary reviewed changes.

## Considered & rejected

- **Duplicate commands in GitHub Actions and hook configuration.** This is
  initially direct, but every tool change needs coordinated edits and drift is
  undetectable until one path fails differently.
- **Run only `prek run --all-files` everywhere.** This makes hooks canonical,
  but hides useful focused commands and adds hook-runner overhead to routine
  local verification.
- **Gate the entire tree immediately.** This would require broad formatting,
  type fixes, or suppressions outside issue #48. It obscures the CI change and
  makes rollback harder without improving the requested coordination.
