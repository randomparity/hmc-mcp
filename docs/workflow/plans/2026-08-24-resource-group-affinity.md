# Implement capability-aware resource-group affinity

**Goal:** expose read-only current and calculated resource-group affinity scores behind a stable,
evidence-backed capability result.

**Architecture:** the SSH boundary admits HMC V11R1M1110+, constructs fixed `lsmemopt` commands,
strictly parses projected output, and recognizes only `HSCLCA00` as managed-system capability
absence. Shared operations resolve system selectors and return a presentation-neutral envelope;
MCP, Python API, CLI, and the live runner delegate to them.

**Tech stack:** Python 3.13, asyncssh, FastMCP, Typer, pytest, uv, ruff, ty.

## Global constraints

- Minimum admitted HMC is V11R1M1110; earlier or malformed version output is capability-unavailable.
- Current fields are exactly `resource_group_name,resource_group_id,curr_score`.
- Calculated fields are exactly `resource_group_name,resource_group_id,curr_score,predicted_score,
  requested_lpar_names,requested_lpar_ids,protected_lpar_names,protected_lpar_ids`.
- Only `HSCLCA00` is translated after version admission; every other HMC failure propagates.
- Preserve string score values including `none`; calculated rows add
  `prediction_guaranteed: false`.
- Invoke no `optmem` command and perform no mutation.
- Use ADR 0084 only; leave any ADR index unchanged.

## Design handoff prerequisite

Before Task 1, commit ADR 0084, the approved design spec, and this approved plan together as
`docs: design resource-group affinity contract`. Confirm `git status --porcelain` is empty before
writing the first test so the implementation range has an unambiguous reviewed design base.

## Task 1: Establish the SSH capability and score contract

**Files:** modify `src/hmc_mcp/ssh_commands.py`; create
`tests/lpar/test_resource_group_affinity.py`.

**Interfaces:** define frozen `MemoptResourceGroupSelector(names: tuple[str, ...] = (),
ids: tuple[int, ...] = (), all: bool = False)`; define version admission and two primitives
returning `list[dict[str, object]]` or a package-owned capability sentinel consumed by Task 2.

1. Write tests containing the complete V10/V11 multiline version fixtures, current/calculated
   headers and rows, header-only and blank output, `none`, `HSCLCA00`, generic failure, malformed
   headers/rows, shell-heavy names, duplicate/blank names, negative/duplicate IDs, the observed
   valid ID `0`, mixed modes, and all selection. Assert exact command strings and that V10 sends no
   resource-group query.
2. Run `uv run pytest -q --no-cov tests/lpar/test_resource_group_affinity.py`; expect failures
   because the selector and primitives do not exist.
3. Implement the selector, version parser, fixed projections, strict delimited parser calls,
   commands, and exact `HSCLCA00` recognition. Keep functions below 100 lines and validate before
   I/O.
4. Re-run the focused test; expect all cases to pass. Break the version threshold and one required
   field check in turn, confirm the relevant tests fail, then restore them.
5. Commit as `feat: add resource-group affinity SSH contract`.

## Task 2: Add the shared result envelope and MCP/API adapters

**Files:** modify `src/hmc_mcp/operations/ssh_network.py`,
`src/hmc_mcp/server_tools/lpar_config.py`, `src/hmc_mcp/_app.py`, `src/hmc_mcp/server.py`,
`src/hmc_mcp/api.py`, `tests/lpar/test_resource_group_affinity.py`,
`tests/app/test_application_boundaries.py`, and `tests/app/test_tool_security.py`.

**Interfaces:** define `ResourceGroupAffinityResult` with capability, mode, system, selector, items,
and unavailable reason; define the two async signatures from the spec; define the two MCP names
from the spec. Task 3 consumes both operations and selector.

1. Add failing operation and MCP adapter tests for UUID resolution, default-all normalization,
   available results, both capability reasons, and error propagation. Update exact API/MCP/security
   inventories.
2. Run `uv run pytest -q --no-cov tests/lpar/test_resource_group_affinity.py
   tests/app/test_application_boundaries.py tests/app/test_tool_security.py`; expect missing-contract
   or inventory failures.
3. Implement the envelope and operations, add MCP adapters, register read-only metadata, and export
   the operations/types through `hmc_mcp.api` and MCP exports.
4. Re-run that same three-file command; expect all cases to pass. Temporarily remove one API export,
   confirm the facade inventory test fails, then restore it and rerun the command before commit.
5. Commit as `feat: expose resource-group affinity operations`.

## Task 3: Add CLI and live-runner paths

**Files:** modify `src/hmc_mcp/cli_commands/lpars.py`, `scripts/live_test_runner.py`,
`tests/app/test_cli_commands.py`, and `tests/test_live_runner.py`.

**Interfaces:** consume Task 2's two async operations and
`MemoptResourceGroupSelector`; expose the two CLI commands and add current/calculated live calls.

1. Add failing CLI tests for JSON/text available and unavailable results, all/name/ID selectors,
   mutually exclusive options, and generic errors. Add live-runner lifecycle tests requiring only
   `lsmemopt -r resgroup -o currscore|calcscore` and retaining the no-`optmem` invariant.
2. Run `uv run pytest -q --no-cov tests/app/test_cli_commands.py tests/test_live_runner.py -k
   'resource_group or affinity_live_paths'`; expect missing-command/call failures.
3. Implement selector construction, commands, output rendering, and the two read-only live calls.
4. Re-run the focused tests; expect all selected cases to pass. Temporarily break one CLI
   delegation/selector path and one live-runner affinity call or no-`optmem` assertion in turn;
   confirm the focused command fails for each, restore both, and rerun the identical command.
5. Commit as `feat: add resource-group affinity CLI and live checks` only after the restored focused
   command passes.

## Task 4: Verify the complete contract

**Files:** all files changed above plus the ADR, spec, and plan.

**Interfaces:** no new interfaces; this task verifies Tasks 1–3 as one public contract.

1. Re-read the diff for naming, scope, source citations, and accidental `optmem` paths.
2. Run `just test`; expect the exact coverage gate and all tests to pass.
3. Run `just smoke`; expect the MCP handshake and updated tool count to pass.
4. Run `just verify`; expect all repository guardrails to pass with zero warnings and a clean
   tracked/untracked inventory.
5. Run `git status --porcelain`; expect no output. If verification required a documentation
   correction, commit that correction separately with a conventional subject and repeat the check.
