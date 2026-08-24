# Contributing

Contributions should be focused, tested, and easy to review.

1. Fork the repository or create a feature branch.
2. Install the locked development environment and hooks with `just setup`.
3. Make one focused change and add or update tests for its behavior and error paths.
4. Run `just verify` and `UV_NO_SYNC=1 uv run prek run --all-files`.
5. Open a pull request that explains the current behavior of the change.

## Changelog

Every user-facing change that ships in a release must be recorded in `CHANGELOG.md`, which
follows the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. Two rules are
mandatory and enforced by `tests/unit/test_changelog.py`:

- The version declared in `pyproject.toml` must have a matching `## [<version>]` entry, so a
  release cannot ship without one.
- Every release entry — including `[Unreleased]` — must contain a `### Facade manifest` section,
  even when nothing moved ("no change to `hmc_mcp.api.__all__`" is a positive statement for
  consumers). Where the manifest changed, name every added, removed, and renamed export and every
  changed exported enum member or literal alternative; per ADR 0029 any of these requires a minor
  release during `0.x`.

Keep dependencies pinned and avoid adding one unless the change requires it. Follow the repository
instructions in `AGENTS.md`, including its commit and verification conventions.

Suspected vulnerabilities do not belong in a public issue or pull request. Follow the
[security policy](SECURITY.md) to report them privately.
