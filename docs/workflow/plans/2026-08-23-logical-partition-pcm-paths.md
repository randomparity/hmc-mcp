# Logical-partition PCM paths implementation plan

Goal: route logical-partition processed and aggregated PCM requests through
their owning managed system and reject unsupported combinations. The Python
3.13 implementation extends the existing PCM operation/client boundary without
new dependencies.

## Global constraints

- Preserve managed-system paths and behavior.
- Require an owning-system selector for logical-partition metrics.
- Reject logical-partition preferences and Long Term Monitor before I/O.
- Do not add SSP support or live-HMC fixtures.
- Run `just test`, `just smoke`, and `just verify`.

## Task 1: Express documented client paths

Files: `src/hmc_mcp/client_pcm.py`, `src/hmc_mcp/client_contracts.py`,
`tests/unit/test_pcm.py`.

Interfaces: `get_processed_metric_links` and `get_aggregated_metric_links` gain
keyword-only `system_uuid: str | None = None`; `_metrics_links` and its
`PcmClient` protocol declaration consume it. `get_ltm_metric_links` validates
that its category is `ManagedSystem`.

1. Add HTTP-route tests for nested processed and aggregated paths and early LTM
   rejection, plus regression assertions for managed-system paths.
2. Run `uv run pytest -q --no-cov tests/unit/test_pcm.py`; expect failures for
   flat paths and missing rejection.
3. Update the concrete and protocol signatures, then add one path builder with
   explicit category/owner validation and use it from the metric methods.
4. Run the focused test and `just typecheck`; expect success, then commit.

Acceptance: exact documented URLs are requested, invalid combinations issue no
request, and the typed protocol matches the concrete client.

## Task 2: Resolve and thread an owned PCM target

Files: `src/hmc_mcp/operations_pcm.py`, `tests/unit/test_pcm.py`.

Interfaces: define `PcmResource(resource_uuid: str, system_uuid: str | None)`
and change `resolve_pcm_resource(hmc, category, resource,
system_name_or_uuid=None) -> PcmResource`. `metric_links` and `metric_data`
consume it and pass `system_uuid` to the Task 1 client signatures.

1. Add async tests showing a logical-partition name resolves its owner then the
   partition within that owner, a UUID still requires and retains the owner,
   and a managed system yields no owner.
2. Run `uv run pytest -q --no-cov tests/unit/test_pcm.py`; expect failure.
3. Implement the dataclass and resolution contract, threading the optional
   selector through metric operations into the compatible Task 1 methods.
4. Run the focused test; expect success, then commit.

Acceptance: no logical-partition metric operation can lose its owner UUID, and
managed-system resolution remains unchanged.

## Task 3: Expose owner selection and preference errors

Files: `src/hmc_mcp/server_metrics.py`, `src/hmc_mcp/operations_pcm.py`,
`src/hmc_mcp/cli_metrics.py`, `tests/unit/test_pcm.py`,
`tests/app/test_cli_commands.py`.

Interfaces: the four processed/aggregated MCP tools and CLI `metrics show` add
optional `system_name_or_uuid`; preference operations reject
`LogicalPartition` before resolution.

1. Add operation and MCP tests for selector forwarding, missing-owner errors,
   preference rejection, and supplied-owner rejection for `ManagedSystem`;
   assert rejected calls invoke neither a resolver nor an HTTP client method.
2. Add CLI tests that invoke `metrics show LogicalPartition <lpar> --system
   <owner> --start <timestamp>`, assert owner forwarding, verify the `--system`
   help spelling, and cover both missing-owner and managed-system misuse errors.
3. Run `uv run pytest -q --no-cov tests/unit/test_pcm.py
   tests/app/test_cli_commands.py`; expect the new cases to fail.
4. Thread the selector through helpers and update docstrings/CLI help; add the
   preference and selector/category guards before resource resolution.
5. Run the same focused command; expect success, then commit.

Acceptance: users can select the owner where supported and receive actionable
errors where no endpoint exists.

## Task 4: Verify and ship

Run `just test`, `just smoke`, and `just verify`, expecting zero failures and
warnings. Review the diff for accidental public-contract changes, then push and
open a PR that closes #400. Rollback is a normal revert; no persisted data or
external state changes are introduced.
