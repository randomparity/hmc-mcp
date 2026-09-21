# Bounded storage results implementation plan

Goal: replace supported raw storage response mappings with bounded immutable
operation results. Operations translate client payloads once; MCP and CLI only
serialize results. The implementation uses Python dataclasses and existing
`serialize_tool_result`/`asdict` helpers; no dependency or target change.

## Global Constraints

- Keep host `x86_64` and declared targets `amd64` and `arm64` supported.
- Preserve selector resolution, authorization, request paths, and mutation behavior.
- Replace the pre-release public operation result shape; do not retain raw-payload shims.
- Run `just test`, `just smoke`, generated-document checks, and relevant static checks.

Expected implementation size: 180–280 changed lines (M) — operation models,
translation, adapters, contracts, and focused tests.

## Task 1: Define and test storage result translation

Files: modify `src/hmc_mcp/operations/storage.py`; add focused tests under
`tests/storage/`.

Interfaces: define frozen `VolumeGroup`, `VirtualDisk`, `OpticalMedia`, and
`StorageMapping` dataclasses plus private translators from `dict[str, Any]`.
Operation functions return these objects, tuples, or `None`.

Verification: Mode: focused-test. Add representative valid HMC mappings and
wrong/missing required field cases. Expected red: tests fail before translators
exist. Green command: `uv run --no-sync pytest tests/storage/test_storage_results.py -q`.

## Task 2: Move supported surfaces to the bounded contract

Files: modify `src/hmc_mcp/server_tools/storage.py`,
`src/hmc_mcp/cli_commands/storage.py`, `src/hmc_mcp/api.py`,
`docs/adr/0029-supported-reusable-python-api-contract.md`, `CHANGELOG.md`, and
their focused tests.

Interfaces: MCP returns `serialize_tool_result(result)` and lists of serialized
results; CLI prints `asdict` results. `api.__all__` exports the result models.

Verification: Mode: focused-test. Update storage tool/CLI/public API assertions
to require named serialized fields. Expected red: old raw-key assertions fail.
Green commands: `uv run --no-sync pytest tests/storage/ tests/app/test_cli_commands.py tests/unit/test_public_api.py -q` and `just tool-docs-check`.

## Task 3: Prove and record the completed refactor

Files: generated `docs/tools/` only if `just tool-docs` changes it; local
desloppify state is excluded from Git.

Interfaces: no new interface beyond Tasks 1–2.

Verification: Mode: focused-test. Run `just test`, `just smoke`, and the
scanner-provided resolve command. Expected result: all checks exit zero and the
review item is resolved against the commit.
