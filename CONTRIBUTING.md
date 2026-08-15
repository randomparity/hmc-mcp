# Contributing

Contributions should be focused, tested, and easy to review.

1. Fork the repository or create a feature branch.
2. Install the locked development environment with `uv sync --locked`.
3. Make one focused change and add or update tests for its behavior and error paths.
4. Run `just verify` and `uv run prek run --all-files`.
5. Open a pull request that explains the current behavior of the change.

Keep dependencies pinned and avoid adding one unless the change requires it. Follow the repository
instructions in `AGENTS.md`, including its commit and verification conventions.

Suspected vulnerabilities do not belong in a public issue or pull request. Follow the
[security policy](SECURITY.md) to report them privately.

