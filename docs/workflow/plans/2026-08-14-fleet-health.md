# Fleet health implementation plan

**Goal:** Add a bounded, read-only estate exception snapshot with a stable public envelope.

**Architecture:** A presentation-neutral operation curates four unhealthy-resource collections
and warnings. A thin MCP module and systems CLI share it. Core inventory is all-or-error; only the
known unsupported global Job feed degrades to a warning.

**Tech stack:** Python 3.12+, asyncio TaskGroup/Semaphore, frozen dataclasses, FastMCP, Typer,
pytest, uv, Ruff, ty.

## Global Constraints

- Branch `feat/fleet-health-152`; base `main`; guardrail `just verify`.
- Return `FleetHealthResult(systems, vios, lpars, failed_jobs, warnings)` on every success.
- Limit simultaneous per-system inspections to eight.
- Fail on core inventory errors and unexpected Job errors; tolerate only the exact unsupported
  global Job-root error with an actionable warning.
- Curate named fields only; do not return raw UOM entries.
- Keep #145 broad documentation and #151 migration-validation behavior out of scope.
- New public functions use Google-style docstrings and at most five positional parameters.

## Task 1: Implement and test the fleet operation

**Files:** create `src/hmc_mcp/operations/health.py` and
`tests/system/test_fleet_health.py`.

**Interfaces:** provide
`FleetHealthResult(systems, vios, lpars, failed_jobs, warnings)` and
`async fleet_health(hmc: HMCClient) -> FleetHealthResult`. The operation uses a fixed internal
limit of eight simultaneous system inspections; it exposes no caller-controlled concurrency knob.
Later tasks consume both names unchanged.

1. Write tests constructing healthy and degraded UOM dictionaries. Assert exact curated keys,
   `unknown` for missing state, the four exception predicates, deterministic sort order, and empty
   tuples for a healthy estate. For failed jobs, prove that filtering considers only the first 20
   records in HMC feed order, then matches every canonical `jobs.FAILED_JOB_STATUSES` value while
   excluding representative successful, running, warning, and unknown statuses; prove missing,
   blank, and non-string errors become `unknown`, and accepted error text is truncated to 500
   characters. For each affected curator, prove missing, blank, and non-string names and child/job
   UUIDs become `unknown` before sorting and record construction. Run
   `uv run pytest -q --no-cov tests/system/test_fleet_health.py`; expect import failure.
2. Write async tests with an `AsyncMock` HMC client. Assert unsupported Job root returns one
   warning with empty failed jobs. Add parameterized near misses that independently change the HTTP
   400 status, omit `REST000E`, and omit `Unrecognized root REST type of Job`; assert each
   `HMCError` propagates. Assert a system inventory failure propagates, a managed-system entry with
   a missing, blank, or non-string UUID fails without returning partial child inventory or issuing
   child requests for that entry, and an instrumented client never observes more than eight active
   system inspections. Instrument task creation over an estate larger than eight and assert the
   operation creates at most eight system-worker tasks, rather than one waiting task per system.
   With a strict recording client double, assert the complete operation ledger contains only
   managed-system, LPAR, VIOS, and Job list reads and no mutating client call. Run the same command;
   expect failures for missing behavior.
3. Implement the dataclass, pure curator functions that import and reuse
   `jobs.FAILED_JOB_STATUSES`, exact unsupported-error predicate, bounded
   system inspection as at most eight fixed workers consuming a shared system queue, fail-closed
   system identity validation, `unknown` normalization for malformed names and child/job UUIDs,
   first-20 Job-feed slicing before failure classification, bounded failed-job error normalization,
   deterministic sorting, and `fleet_health`. Do not eagerly create one inspection task per managed
   system; the global Job read remains one separate task.
4. Run `uv run pytest -q --no-cov tests/system/test_fleet_health.py`; expect all tests pass.
5. Run `uv run ruff check src/hmc_mcp/operations/health.py tests/system/test_fleet_health.py`
   and `uv run ty check`; expect both pass. Commit with
   `feat: compute fleet health exceptions`.

**Acceptance:** all four categories are curated and stable; healthy returns empty collections;
the active-inspection and scheduled-task bounds, read-only call ledger, and failure semantics are
executable tests.

## Task 2: Expose the MCP and CLI contract

**Files:** create `src/hmc_mcp/server_tools/health.py`; modify `src/hmc_mcp/server.py`,
`src/hmc_mcp/_app.py`, `src/hmc_mcp/cli_commands/systems.py`, `tests/app/test_capabilities.py`,
`tests/unit/test_server_module_boundaries.py`, `tests/app/test_cli_commands.py`, and
`tests/app/test_application_boundaries.py`; create `tests/system/test_health_tools.py`.

**Interfaces:** consume `FleetHealthResult` and `fleet_health`. Export
`hmc_fleet_health(profile: str | None = None) -> dict[str, Any]`, serialized from the operation's
`FleetHealthResult`. Register
`systems health --json` and human table output.

1. Add failing capability and boundary tests asserting the registered name, `_READ_ONLY`
   annotation, server module ownership, and incremented independent-app tool count. Add an MCP
   handler test that invokes `hmc_fleet_health` with a profile and proves client-profile forwarding,
   exactly one awaited call to the shared operation, and the exact serialized five-key mapping.
   Add CLI tests asserting delegation, JSON arrays for every collection, degraded category output,
   and warning output. Run the named test files; expect failures for the missing surface.
2. Implement `server_tools/health.py`, compose and re-export it in `server.py`, add the tool to
   `READ_ONLY_TOOLS`, and add one concise composite-tool guidance line in `_app.py`.
3. Implement `systems health` in `cli_commands/systems.py`; JSON uses `dataclasses.asdict`, while human
   output prints only non-empty exception tables plus warnings and a healthy message when all
   categories and warnings are empty.
4. Run
   `uv run pytest -q --no-cov tests/app/test_capabilities.py tests/unit/test_server_module_boundaries.py tests/app/test_cli_commands.py tests/app/test_application_boundaries.py tests/system/test_fleet_health.py tests/system/test_health_tools.py`;
   expect all pass. Run Ruff and ty; expect both pass. Commit with
   `feat: expose fleet health snapshot`.

**Acceptance:** one read-only MCP tool and one CLI mirror expose the same result without adding a
second operation implementation.

## Task 3: Document and verify the public distinction

**Files:** modify `README.md`; verify all prior changed files.

**Interfaces:** no new code interface. Documentation names `hmc_fleet_health` and
`hmc-mcp systems health --json` and states why system summaries/capacity cannot substitute.

1. Add the MCP tool-table row and a concise usage example. State that the tool returns only
   exceptions, includes individual RMC failures and failed jobs, and warns when global Job listing
   is unsupported. Do not expand unrelated workflow documentation.
2. Run `just verify`; expect Ruff, ty, secrets, workflow audit, environment guard, all tests,
   113-tool smoke handshake, and CLI group loading to pass.
3. Review `git diff origin/main...HEAD` for scope, names, line length, and stable result wording.
   Commit with `docs: explain fleet health exceptions`.

**Acceptance:** documentation and PR rationale make the exception-index distinction explicit;
the full guardrail is green.
