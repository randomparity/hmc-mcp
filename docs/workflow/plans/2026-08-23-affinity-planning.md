# Implement affinity planning

**Goal:** expose validated, read-only current and predicted memory-affinity scores at LPAR and
system scope. The SSH layer owns fixed command construction and parsing; shared async operations
own selector resolution; MCP and CLI only adapt inputs and presentation. Python 3.13, `uv`, Typer,
FastMCP, pytest, ruff, and ty are the existing stack.

## Global constraints

- Invoke `lsmemopt` only; never invoke `optmem` or mutate HMC state.
- Predictions are potential values and must return `prediction_guaranteed: false`.
- `MemoptLparSelector` uses names or positive IDs, never both; explicit selectors are non-empty,
  duplicate-free, bounded, and structurally validated.
- Preserve HMC extension fields while requiring scope-specific current and predicted score fields.
- `BASE_BRANCH=main`; guardrails are `just test`, `just smoke`, and `just verify`.

## Task 1: SSH selector and score primitives

**Files:** `src/hmc_mcp/ssh_commands.py`, `tests/lpar/test_affinity_planning.py`.

**Interfaces:** define `MemoptLparSelector(names: tuple[str, ...] = (), ids: tuple[int, ...] = ())`;
`get_system_memopt_score(config, system_name)`; `plan_lpar_memopt_scores(config, system_name,
prioritized=None, excluded=None)`; and `plan_system_memopt_score(config, system_name,
prioritized=None, excluded=None)` with the signatures in the spec.

1. Add focused tests for valid/invalid selectors, mixed representation, overlap, and exact
   `-p`/`--id`/`-x`/`--xid` commands. Add distinct nonzero stderr fixtures for unsupported
   capability (including multiple resource groups), permission denied, and generic failure; assert
   each `HMCCLIError` retains the command and diagnostic and makes no fallback call. Add exit-zero
   missing-field, empty-system, and multiple-system-row tests with exact cardinality diagnostics.
2. Add minimal importable stubs, then run the named selector, command, diagnostic, and cardinality
   node IDs; expect assertion failures rather than collection errors. Temporarily allow mixed
   representations and drop one diagnostic from the stub, observe the corresponding tests fail,
   then restore the red stub behavior before implementation.
3. Implement the frozen dataclass, command builder, common parser validation, and four SSH-facing
   functions. Add `prediction_guaranteed: False` only to prediction results.
4. Re-run the focused test; expect all tests to pass. Commit as
   `feat: add affinity planning SSH primitives`.

Acceptance: no user scalar can alter command structure; current and predicted shapes cannot be
mistaken; malformed HMC responses fail rather than masquerade as scores.

## Task 2: Shared operations and MCP contract

**Files:** `src/hmc_mcp/operations_ssh_network.py`, `src/hmc_mcp/server_lpar_config.py`,
`src/hmc_mcp/server.py`, `src/hmc_mcp/api.py`, `docs/adr/0029-supported-reusable-python-api-contract.md`,
`tests/lpar/test_affinity_planning.py`, `tests/app/test_target_authorization.py`,
`tests/unit/test_tool_registry.py`, `tests/unit/test_public_api.py`,
`tests/unit/test_i_record_grammar.py`, `tests/app/test_application_boundaries.py`.

**Interfaces:** the operation signatures match Task 1 but resolve `system` names or UUIDs first;
MCP tools are `hmc_get_system_memopt_score`, `hmc_plan_lpar_memopt_scores`, and
`hmc_plan_system_memopt_score`, with optional structured selectors and profile.

1. Add failing delegation, UUID-resolution, MCP registration, API inventory, and public signature
   tests. In `tests/unit/test_tool_registry.py`, assert all three operation names bind the system
   selector with `target_kind="managed_system"`. In `tests/app/test_target_authorization.py`, add
   a policy whose managed-system target permits the selected system but whose LPAR table excludes
   scenario selectors and assert planning is allowed; change the managed-system target to another
   system and assert denial before transport. Add minimal registration stubs, run those named nodes,
   and observe allow/deny assertion failures for the intended policy behavior.
2. Add shared operations and thin read-only MCP adapters. Export all new operations and
   `MemoptLparSelector` from `hmc_mcp.api`; update ADR 0029's governed inventory.
3. Re-run focused tests and `just smoke`; expect all pass and the exposed-tool count to rise by 3.
4. Commit as `feat: expose affinity planning operations`.

Acceptance: target authorization metadata remains LPAR/system scoped, adapters delegate to the
shared operations, and the reusable facade contains no presentation imports.

## Task 3: CLI, live runner, and documentation

**Files:** `src/hmc_mcp/cli_lpars.py`, `tests/app/test_cli_commands.py`,
`scripts/live_test_runner.py`, `tests/test_live_runner.py`, `README.md`.

**Interfaces:** commands `lpars system-memopt-score SYSTEM`, `lpars plan-memopt-scores SYSTEM`, and
`lpars plan-system-memopt-score SYSTEM`; planning commands accept repeatable selector options from
the global contract and `--json`.

1. Add failing CLI tests for human/JSON output, name/ID options, invalid mixed selectors, and
   propagated HMC errors. Register minimal command stubs, run named behavioral node IDs, and observe
   output/validation failures rather than command-not-found.
2. Implement thin CLI adapters that construct `MemoptLparSelector` and delegate to shared
   operations. Human output labels `current`, `predicted`, and `prediction guaranteed: no`.
3. Add live-runner entries for all three read-only operations and README tool/CLI guidance that
   predictions are not guarantees. In `tests/test_live_runner.py`, inventory the new call paths,
   assert their captured commands are the exact `lsmemopt` current/calcscore forms, and reject an
   `optmem` command token anywhere in the runner. Temporarily substitute `optmem` in one fixture,
   observe this test fail, then restore it. Do not add any executable `optmem` path.
4. Run focused tests, `just test`, `just smoke`, and `just verify`; expect all green. Commit as
   `feat: add affinity planning CLI and live checks`.

Acceptance: both structured representations reach exact safe command flags; documentation and
presentation distinguish observation from prediction; the live path cannot start optimization.
