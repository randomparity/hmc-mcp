# Served warning capture implementation plan

Goal: route warnings in served deployments through the existing bounded logging sink without
changing non-served library behavior. The serve bootstrap owns Python's process-global logging
bridge; the existing queue handler and formatter remain the only output mechanism. Python 3.11+
on amd64 and arm64 remains the supported execution context.

## Global constraints

- Use only the standard-library warning bridge and the installed sink; add no dependency.
- Do not enable capture at import or from `create_mcp`.
- Preserve the existing queue capacity, drop behavior, and control escaping.
- Verify with `just verify` and `UV_NO_SYNC=1 uv run --no-sync prek run --all-files`.

## Task 1: Pin served and library warning behavior

Files: modify `tests/app/test_connection_authorization.py` and `tests/conftest.py`.

Interfaces: tests call `install_third_party_stderr_sinks()`, `_serve_application()`, and the
standard `warnings.warn()`. The isolation fixture calls `logging.captureWarnings(False)` before
restoring `warnings.showwarning`, and owns the `py.warnings` logger's handlers, level, and
propagation state.

1. Add tests that expect `py.warnings` to have one sink handler after repeated installation.
2. Add a served-path test that patches the previous `showwarning`, emits a warning containing a
   control character, drains the sink, and expects a prefixed escaped line without a callback.
3. Add a library-path test that emits without serving and expects the prior callback.
4. Add a serve-reset-serve regression proving capture can be installed again after isolation.
5. Run the focused tests and confirm they fail before implementation.

Acceptance: the tests distinguish direct warning output, captured output, escaping, idempotence,
and test-global restoration.

## Task 2: Install warning capture on serve

Files: modify `src/hmc_mcp/server.py`, `docs/adr/0043-non-blocking-stderr-diagnostics.md`, and
`CHANGELOG.md`.

Interfaces: `logging.captureWarnings(capture: bool)` redirects warnings to `py.warnings`;
`sink_handler()` supplies the shared bounded handler; `StreamSafeFormatter(format, prefix)`
escapes and prefixes each line.

1. Add `py.warnings` to the logger set installed by `install_third_party_stderr_sinks`.
2. Enable warning capture in `_serve_application` after the sink handlers are installed.
3. Run the focused tests and expect them to pass.
4. Amend ADR 0043 and the changelog to describe the now-bounded warning route.
5. Run `just verify` and the all-files hook command; expect exit 0.

Acceptance: the complete issue #550 criteria are green; rollback is a normal git revert because
the change has no persisted data or external migration.
