# Implement LPAR memory-optimization score tools

**Goal:** ship read-only `lsmemopt -o currscore` access as two MCP tools and
two `lpars` CLI commands.

**Architecture:** `ssh_commands.py` owns the fixed-verb HMC CLI commands and
parsing; `operations_ssh_network.py` owns selector-aware workflows shared by
the MCP and CLI adapters; `hmc_mcp.api` exports those async operations under
ADR 0029; both presentations delegate to the shared operations.
Verified live on hmc5.labda.sva.de (P9 9009 systems): default output is
`lpar_name=…,lpar_id=…,curr_lpar_score=…` key=value rows, score may be the
literal string `none`, unknown LPAR exits non-zero.

**Tech stack:** Python 3.11+, async SSH transport (`ssh.py`), FastMCP, Typer,
pytest, Ruff, ty, prek.

## Global constraints

- Branch `lpar_score`; base `main`.
- Guardrails: `just verify`; `UV_NO_SYNC=1 uv run prek run --all-files`.
- No new dependencies, no ADR, no `HMC_*` env var, no multi-value `--filter`,
  no `calcscore` support. Add the two async operations to `hmc_mcp.api` and its
  contract inventory/tests, as explicitly authorized for issue #310.
- Both tools are read-only: `_READ_ONLY` annotations, `READ_ONLY_TOOLS`
  registry entries, rendered descriptions for every parameter.
- Public payload keeps raw HMC keys as strings
  (`lpar_name`, `lpar_id`, `curr_lpar_score`).

## Task 1: ssh_commands layer

**Files:** modify `src/hmc_mcp/ssh_commands.py`; create
`tests/lpar/test_memopt_score.py`.

**Interfaces:**
`async def get_lpar_memopt_score(config, system_name, lpar_name) ->
dict[str, str]` and `async def list_lpar_memopt_scores(config, system_name,
lpar_name=None) -> list[dict[str, str]]`. Later tasks consume these exact
coroutines.

1. Write transport-mocked unit tests: exact `lsmemopt` command strings
   (no filter; with `--filter lpar_names=<name>`), shlex quoting of names
   with spaces, parsing of multi-line rows including the literal `none`
   score, `list` empty output `[]`, `get` no-row `ValueError`/`HMCCLIError`,
   empty `lpar_name` `ValueError` before I/O.
2. Run `uv run --no-sync pytest -q tests/lpar/test_memopt_score.py`; expect
   import failures.
3. Implement both functions next to `list_fc_ports`, running
   `run_hmc_command` and parsing via `_parse_lshwres_output`; `get` takes
   the single row and raises `HMCCLIError` when none was reported.
4. Rerun the focused tests; expect green.
5. Commit explicit paths with
   `feat: add lsmemopt currscore SSH commands`.

## Task 2: Shared operations, facade, MCP tools, registration, re-exports

**Files:** modify `src/hmc_mcp/operations_ssh_network.py`, the `hmc_mcp.api`
facade and contract inventory/tests, `src/hmc_mcp/server_lpar_config.py`,
`src/hmc_mcp/_app.py`, and `src/hmc_mcp/server.py`; extend
`tests/lpar/test_memopt_score.py`.

**Interfaces:**
`hmc_get_lpar_memopt_score(system_name_or_uuid: str, lpar_name_or_uuid: str,
profile: str | None = None) -> dict[str, str]` and
`hmc_list_lpar_memopt_scores(system_name_or_uuid: str,
lpar_name_or_uuid: str | None = None, profile: str | None = None) ->
list[dict[str, str]]`, both `_READ_ONLY`.

1. Add operation and full-stack tests through the reusable API and public tools (mock_hmc +
   mock_uuid_resolution + patched asyncssh.connect): UUID → REST resolution,
   name pass-through, returned dicts, `none` score, unknown-LPAR
   `HMCCLIError`, list-empty `[]`.
2. Run the focused tests plus `uv run --no-sync pytest -q
   tests/app/test_capabilities.py`; expect failures for the absent tools.
3. Add the shared selector-aware async operations, their facade exports and
   contract inventory, the two `@tool(annotations=_READ_ONLY)` wrappers with
   exhaustive Google-style Args docstrings (every parameter rendered), the two
   `READ_ONLY_TOOLS` entries, and the `server.py` re-exports.
4. Rerun focused tests; expect green. Break one registry entry, observe the
   capability test fail, restore it, and rerun green.
5. Commit explicit paths with `feat: expose lpar memory-optimization score
   tools`.

## Task 3: CLI mirroring and README

**Files:** modify `src/hmc_mcp/cli_lpars.py`,
`tests/app/test_cli_commands.py`, `README.md`.

**Interfaces:** `hmc-mcp lpars memopt-score LPAR SYSTEM [--json]` and
`hmc-mcp lpars memopt-scores SYSTEM [--lpar NAME] [--json]`, delegating
name-or-UUID selector resolution to the shared operations before the SSH call.

1. Add CLI wiring tests (monkeypatch `ssh_commands.run_hmc_command`):
   exit 0 with expected output for both commands, `--json` shape, exit 1 on
   `HMCCLIError`, exit 2 on missing arguments.
2. Run the focused CLI tests; expect failures.
3. Implement both commands using the `lpars` group conventions (LPAR
   argument first on the single-score command, as with `get-description`),
   calling the Task 2 shared operations.
4. Add two README rows to the "LPAR / system properties (SSH/CLI)" table.
5. Rerun focused tests; expect green.
6. Commit explicit paths with `feat: add lpars memopt-score CLI commands`.

## Task 4: Live runner entry and full verification

**Files:** modify `scripts/live_test_runner.py`.

1. Add `hmc_get_lpar_memopt_score` and `hmc_list_lpar_memopt_scores` calls
   to the ST4 section following the existing `record(state, 4, ...)`
   pattern.
2. Run `just verify` (static, pytest with the 90% coverage gate, MCP smoke,
   build, artifact validation, CLI group loads) and
   `UV_NO_SYNC=1 uv run prek run --all-files`.
3. Commit explicit paths with
   `chore: add memopt score live-runner entries` (or fold into Task 3 if
   the diff is trivial).
4. Optional operator step: verify against the live HMC
   (`hmc-mcp lpars memopt-score <lpar> <system>`; on p9da10 `p9da10v1t`
   is expected to report `100` and `dalpar2rrd1t` the literal `none`).
