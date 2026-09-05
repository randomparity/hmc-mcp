# Contributing

Contributions should be focused, tested, and easy to review.

1. Fork the repository or create a feature branch.
2. Install the locked development environment and hooks with `just setup`.
3. Make one focused change and add or update tests for its behavior and error paths.
4. Run `just verify` and `UV_NO_SYNC=1 uv run prek run --all-files`.
5. Open a pull request that explains the current behavior of the change.

Suspected vulnerabilities do not belong in a public issue or pull request. Follow the
[security policy](SECURITY.md) to report them privately.

Keep dependencies pinned and avoid adding one unless the change requires it. Follow the repository
instructions in `AGENTS.md`, including its commit and verification conventions.

## Changelog

Every user-facing change that ships in a release must be recorded in `CHANGELOG.md`, which
follows the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. Two rules are
mandatory and enforced by `tests/unit/test_changelog.py`:

- The version declared in `pyproject.toml` must have a matching `## [<version>]` entry, so a
  release cannot ship without one.
- `hmc_mcp.api` is the six-name stable facade defined by ADR 0118. Record changes to it under
  ordinary changelog categories; domain-module APIs remain pre-release.
