# Design: one served-schema composition (#1119)

**Issue:** #1119 · **Branch:** `refactor/shared-served-schemas-1119` · **Base:** `main`

## Problem

The composition that turns the tool registry into what an MCP client is served
(legacy policy, `create_mcp`, `_gates`, the arbitrary-command toggle, a connected
`Client`, `list_tools`) is written three times: the live runner's run loop, the
argument-guard helper `_served_schemas` in `tests/test_live_runner.py`, and
`served_schemas` in `scripts/scenario_gap_report.py`. Each reads the schema under a
different spelling (`inputSchema`, `model_dump(by_alias=True)["inputSchema"]`,
`input_schema`). Nothing keeps the copies in step. A fourth copy, the `schemas` fixture
in `tests/scripts/test_live_bare_cec.py`, is outside this change's surface and is
reported as a follow-up rather than migrated here.

## Design

`scripts/live_test_runner.py` owns the composition, as two module-level functions:

- `served_client()` — an `asynccontextmanager` that composes the policy, application
  and gates exactly as the run loop does today and yields the connected `Client`.
- `served_schemas(client)` — `{tool.name: tool.input_schema for tool in await
  client.list_tools()}`. `input_schema` is the `mcp.types.Tool` field name
  (`inputSchema` is its alias), so it is the one spelling.

Callers:

- The run loop: `async with served_client() as client: state.schemas = await
  served_schemas(client)`, then dispatches through the same `client`. The composition
  moves inside the existing `try`, so the ISO HTTP server is also closed when
  composition fails; nothing else about the loop changes.
- The test helper `_served_schemas()` and the report's `served_schemas()` each become
  `async with runner.served_client() as client: return await runner.served_schemas(client)`.
  Their copies, the report's "must be made here too" docstring, and the imports only the
  copies used are deleted.

Both functions look up `Client`, `create_mcp` and `configure_arbitrary_command_tool`
as runner module globals, so the existing `monkeypatch.setattr(runner, ...)` isolation
in `tests/test_live_runner.py` keeps working unchanged. The report already imports
`live_test_runner` at import time; the new functions do no work at import.

Considered: a single `served_schemas()` that opens its own client — rejected because
the run loop must dispatch through the connected client it read the schemas from;
composing twice there would reintroduce two compositions. A new `scripts/` module —
excluded by the operator (the one-test-module-per-script layout rule).

## Failure model

1. **Actors and deployments** — an operator running the live runner or the gap report
   from a checkout; `just test` locally and in CI.
2. **Invariants at stake** — the served tool set and every input schema the live run
   checks dispatches against (157 tools at `origin/main` 1e3a133b) must be identical
   before and after; the live run is evidence about the path an operator takes.
3. **Accepted failure classes** — none.
4. **Covered elsewhere** — consolidating the dispatch AST walk (#1120); changing the
   composition itself (policy, gates) is out of scope.

## Validation

- **Shared composition serves the registry** — focused test
  `test_served_client_serves_every_registered_tool` in `tests/test_live_runner.py`:
  `runner.served_schemas` over `runner.served_client()` has exactly `TOOL_SECURITY`'s
  names (so `hmc_run_command`, which only the toggle adds, is present) and a `dict`
  schema for each. Red before the change: `served_client` does not exist.
- **Served map unchanged** — one-off proof, not a committed test: dump the report's map
  as sorted JSON before and after the change; the two files must be byte-identical, and
  the test helper's map must equal it too.
- **Runner uses the shared composition** — existing tests in `tests/test_live_runner.py`
  that run `main` with `_isolate_runner` (fake `Client`, asserting the toggle receives
  the policy's gates) stay green; `test_every_dispatched_argument_matches_the_served_schema`
  stays green through the new helper.
- **Report import-safe offline** — `tests/scripts/test_scenario_gap_report.py` stays green.
- **Guardrails** — `just lint` (no unused imports), `just typecheck`, `just test`, `just verify`.
