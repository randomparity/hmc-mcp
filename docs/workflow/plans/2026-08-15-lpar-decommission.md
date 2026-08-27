# Implement safe LPAR decommission

**Goal:** ship a system-scoped, ownership-enforced, previewable LPAR teardown workflow.

**Architecture:** `operations/decommission.py` owns inventory and ordered orchestration.
`server_tools/lpars.py` and `cli_commands/lpars.py` are thin public adapters. Existing resolvers,
ownership authorization, job normalization, and HMC client methods remain the policy
sources.

**Tech stack:** Python 3.11+, async `HMCClient`, FastMCP, Typer, pytest, Ruff, ty, prek.

## Global constraints

- Branch `feat/decommission-lpar-149`; base `main`.
- Targets: amd64, arm64, ppc64le; the current host is included arm64.
- Guardrails: `just verify`; `UV_NO_SYNC=1 uv run prek run --all-files`.
- Storage mappings are observations only; never issue a mapping or backing-storage delete.
- No auto-rollback, migration, dependency, live-HMC test, merge, or ADR-index edit.
- All public fields and parameters need rendered schema descriptions.

## Task 1: Prove and implement the orchestration contract

**Files:** create `tests/lpar/test_decommission_tool.py` and
`src/hmc_mcp/operations/decommission.py`; modify `src/hmc_mcp/operations/lpar.py` only
if a read-only ownership-details helper is needed.

**Interfaces:** define `DecommissionResult(resource_deleted, workflow_completed,
lpar_uuid, dry_run, steps, warnings, blast_radius)` and `async def decommission_lpar(
hmc, system_name_or_uuid, lpar_name_or_uuid, *, dry_run=False,
ownership_override=False, immediate=False, timeout_seconds=300, poll_interval=5)`.
Later tasks consume that exact coroutine and dataclass.

1. Write mocked async tests for system-scoped resolution; ownership refusal and override;
   dry-run inventory with no mutation; active and already-off happy paths; failed or timed
   out power-off; and adapter failure with skipped LPAR deletion. Assert exact call order,
   statuses, curated adapter/mapping fields, and absence of any storage delete.
2. Run `uv run --no-sync pytest -q tests/lpar/test_decommission_tool.py`; expect failures
   because the module and contract do not exist.
3. Implement an inventory helper that lists the resolved system's LPAR children and
   requires exactly one name-or-UUID match (including UUID parent validation), authorizes
   ownership, fetches adapter/system VIOS details, and curates only provably matching
   mappings. Count identity-incomplete mappings and emit the specified incomplete-storage
   warning; cover sparse mapping records in the focused test.
4. Implement the three-step runner. Validate wait inputs first; catch expected HMC errors
   per execution step; power off with normalized successful terminal outcome; delete
   adapters in stable order; delete the retained LPAR UUID; append skipped records after
   the first error.
5. Run the focused test; expect all cases green with zero warnings. Temporarily bypass the
   production ownership authorization call, verify the foreign-owner test fails, restore
   the call, and rerun green.
6. Commit explicit paths with `feat: add safe LPAR decommission workflow`.

## Task 2: Expose MCP contract and registration

**Files:** modify `src/hmc_mcp/server_tools/lpars.py`, `src/hmc_mcp/server.py`,
`src/hmc_mcp/_app.py`, `tests/lpar/test_decommission_tool.py`, and
`tests/test_capabilities.py` or the existing schema-contract test.

**Interfaces:** define `hmc_decommission_lpar(system_name_or_uuid: str,
lpar_name_or_uuid: str, dry_run: bool = False, ownership_override: bool = False,
immediate: bool = False, timeout_seconds: int = 300, poll_interval: int = 5,
profile: str | None = None) -> DecommissionResult`. Register it with `_DESTRUCTIVE`,
export it from `server.py`, and add its exact name to `DESTRUCTIVE_TOOLS`.

1. Add failing tests that import the public tool, inspect its rendered input/output schema
   descriptions, and assert the registry annotation/category.
2. Run the focused schema and capability tests; expect failure for the absent tool.
3. Add the thin wrapper, exhaustive Args documentation, export, and destructive registry
   entry. The wrapper constructs one configured client and delegates unchanged inputs.
4. Rerun focused tests; expect green. Break the registry entry, observe the capability test
   fail, restore it, and rerun.
5. Commit explicit paths with `feat: expose destructive decommission tool`.

## Task 3: Mirror the workflow in the CLI and README

**Files:** modify `src/hmc_mcp/cli_commands/lpars.py`, the existing CLI tests, and `README.md`.

**Interfaces:** add `hmc-mcp lpars decommission LPAR --system SYSTEM
[--dry-run] [--ownership-override] [--immediate] [--timeout-seconds N]
[--poll-interval N] [--json] [--yes]`. It calls Task 1's coroutine and renders either
the dataclass JSON or a concise success/dry-run/partial-failure summary plus step records.

1. Add failing CLI tests for help/schema, dry-run without confirmation, non-dry-run
   confirmation, `--yes`, foreign-owner error propagation, and JSON shape.
2. Run the focused CLI tests; expect failure because the command is absent.
3. Implement the command using the shared client/run/output helpers. Confirmation text names
   both system and LPAR and is skipped only for dry-run or `--yes`.
4. Add the CLI example, composite-tool guidance, ownership note, and destructive tool-table
   row to README without claiming storage deletion or rollback.
5. Run focused tests; expect green. Temporarily remove the production `dry_run` condition
   that suppresses confirmation, observe the dry-run CLI test fail, restore it, and rerun.
6. Commit explicit paths with `feat(cli): add LPAR decommission command`.

## Task 4: Verify the integrated branch

**Files:** verify every changed file; no new implementation surface.

**Interfaces:** the MCP tool, CLI command, result dataclass, registry name, docs, and tests
must agree exactly with Tasks 1–3.

1. Run `uv sync` because campaign dependencies landed after sibling worktrees were created.
2. Run `just verify` bare; expect every static, test, smoke, build, artifact, and CLI check
   to pass with zero warnings.
3. Run `UV_NO_SYNC=1 uv run prek run --all-files` bare; expect every hook to pass without
   modifying tracked files.
4. Review `git diff main...HEAD` for names, complexity, security boundaries, storage-delete
   absence, and docs accuracy. Commit only an evidence-driven correction in its own commit.
