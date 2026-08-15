# Public tool parameter normalization implementation plan

**Goal:** Replace misleading public parameter names, repair installation wait timing,
and expose closed HMC vocabularies in MCP schemas.

**Architecture:** Public server functions own the MCP schema, while presentation-neutral
operations and client/builders carry values to HMC requests. Rename each concept through
that full path, centralize only genuinely shared vocabulary aliases, and keep existing
result and error contracts unchanged.

**Tech stack:** Python 3.11–3.14, FastMCP, Typer, pytest, uv, ruff, ty.

## Global Constraints

- Replace old public names outright; do not add aliases, shims, or dual formats.
- Binary storage values use MiB/GiB names and are forwarded without conversion.
- Install defaults are `hmc_timeout_minutes=60`, `wait=False`,
  `wait_timeout_seconds=None`, `poll_interval=5`; an omitted wait budget derives
  `hmc_timeout_minutes * 60 + poll_interval` only when waiting.
- Host is arm64; project targets are amd64 and arm64; CI covers Python 3.11–3.14.
- Guardrails are `just verify` and `UV_NO_SYNC=1 uv run prek run --all-files`.

## Task 1: Pin the final schemas and install timing behavior

**Files:** modify `tests/app/test_capabilities.py`, `tests/vios/test_vios_lifecycle.py`,
and `tests/lpar/test_lpar_install.py`.

**Interfaces:** Tests consume the public tool registry and the exact install signatures
from the spec. Later tasks must make these assertions pass without compatibility names.

1. Add registry assertions for the replacement storage, switch, and install properties;
   assert each displaced property is absent.
2. Pin the enum arrays for PCM categories, system/partition state filters, and processor
   compatibility mode.
3. Add install tests showing omitted client wait derives 3,605 seconds from the 60-minute
   HMC default, an explicit budget wins, `wait=False` submits without polling, and negative
   HMC timeouts that are zero or negative and negative client timeouts fail before
   submission.
4. Run `uv run --no-sync pytest -q tests/app/test_capabilities.py tests/vios/test_vios_lifecycle.py tests/lpar/test_lpar_install.py`; expect failures identifying the old contract.

**Acceptance:** New tests fail against `main` for the intended missing names and behavior.

## Task 2: Implement install timing and unit-safe storage names

**Files:** modify `src/hmc_mcp/server_vios.py`, `src/hmc_mcp/jobs.py`, storage server,
operations, clients and builders under `src/hmc_mcp/`, plus their direct tests and CLI
adapters in `src/hmc_mcp/cli_storage.py` and `src/hmc_mcp/cli_cluster.py`.

**Interfaces:** Install server functions expose the exact signature in Global Constraints.
Storage functions expose `capacity_mib`, `size_mib`, and `lu_size_gib` end to end; HMC XML
field names remain unchanged.

1. Add the smallest shared validation/effective-budget helper needed by both install tools.
2. Rename both install signatures and forward the derived polling budget to
   `wait_for_submitted_job`.
3. Rename storage quantities through server, operation, client, document/job builders,
   CLI adapters, and tests without numerical conversion.
4. Run focused VIOS, LPAR-install, storage, job, and CLI tests; expect all selected tests
   to pass.
5. Commit as `fix(server): clarify install and storage units`.

**Acceptance:** The wait collision is covered and all storage schemas identify binary units.

## Task 3: Normalize switch names and closed vocabularies

**Files:** modify network/adapter/SSH server and operation modules, `server_systems.py`,
`server_metrics.py`, `operations_pcm.py`, `server_lpar_config.py`, applicable CLI modules,
and their direct tests.

**Interfaces:** Numeric selectors are `virtual_switch_id`; the vNIC selector is
`virtual_switch_name`. Public state, PCM category, and processor-mode parameters consume
the aliases enumerated in the design.

1. Rename switch parameters through every direct caller and test, preserving the existing
   HMC field and SSH payload spelling.
2. Define the finite aliases beside the domain that owns them and type every public and
   presentation-neutral consumer consistently.
3. Update CLI annotations so their generated choices match MCP schema vocabularies.
4. Run focused network, adapter, system, metrics, processor-compatibility, schema, CLI, and
   live-runner tests; expect all selected tests to pass.
5. Commit as `fix(server): normalize switch names and vocabularies`.

**Acceptance:** Schema pins pass and no in-repository caller uses a displaced name.

## Task 4: Publish the final contract and run branch guardrails

**Files:** modify `README.md`; verify all files changed by Tasks 1–3.

**Interfaces:** Documentation consumes the final names only. Issue #148 may quote this
contract after merge.

1. Update README examples/tool guidance for final parameter and CLI option names, units,
   install timing interaction, migration `wait_time` seconds, and per-system processor-mode
   selection.
2. Extend the registry test from Task 1 to walk every public tool signature and fail when
   any displaced parameter name is present. Run that test as the mechanical contract gate.
   Separately inventory old-name text with
   `rg -n 'capacity_mb|size_mb|lu_size_gb|vswitch_id|vswitch_name' src tests README.md` and
   account for every remaining match as either a negative schema assertion or an HMC
   protocol payload spelling; the inventory is diagnostic, not the pass/fail gate.
3. Run `just verify`; expect exit 0 and every verification group to load.
4. Run `UV_NO_SYNC=1 uv run prek run --all-files`; expect exit 0.
5. Commit as `docs: publish normalized tool parameters`.

**Acceptance:** Both branch guardrails pass and README documents only implemented names.

## Rollback

Rollback is a coordinated contract release: revert the implementation, tests, and public
documentation together; add a new ADR that supersedes ADR-0025 and records restoration of
the old schema; then rerun both guardrails. Any pre-release caller already moved to the new
keywords must roll back within the same release boundary. No persisted-data cleanup is
required because this change stores no data and introduces no migration.
