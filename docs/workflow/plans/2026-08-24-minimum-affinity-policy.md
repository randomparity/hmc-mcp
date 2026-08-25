# Minimum-affinity policy implementation plan

Goal: expose validated Power11 minimum-affinity policy reads and capture them in portable snapshots.
Architecture: probe the managed system's advertised compatibility, parse an explicit CLI projection
into one shared envelope, and adapt that contract at Python, MCP, CLI, and snapshot boundaries.
Python 3.11+ with async SSH operations, dataclasses, Typer, FastMCP, Pydantic, pytest, Ruff, and ty.

## Global constraints

- Read only: no setter, DPO control, or inferred REST parity.
- Available score is integer `0..100`; action is exactly `none|warn|fail`.
- Unsupported systems stay usable with a nonblank actionable reason.
- Preserve existing version-1 snapshots that omit the new optional observation.
- Use ADR 0086 only; `BASE_BRANCH=main`; run `just test`, `just smoke`, and `just verify`.

## Task 1: SSH policy contract and parser

Files: `tests/lpar/test_minimum_affinity_policy.py`, `src/hmc_mcp/ssh_commands.py`, and
`src/hmc_mcp/operations_ssh_network.py`.

Interfaces: define frozen `MinimumAffinityPolicyQuery` at the SSH boundary and frozen
`MinimumAffinityPolicyResult` plus
`async get_minimum_affinity_policy(config: HMCConfig, system: str, lpar: str) ->
MinimumAffinityPolicyResult` at the shared-operation boundary. Later tasks consume only the latter.

1. Add tests with a fake command runner proving capability-unavailable short-circuit, valid boundary
   scores/actions, exact command projection, and malformed missing/extra rows, headers, scores, and
   actions. Run `uv run pytest -q tests/lpar/test_minimum_affinity_policy.py`; expect failures for
   missing symbols.
2. Implement the capability probe with `lssyscfg -r sys -F lpar_proc_compat_modes`, then the
   explicit policy projection and closed parser. Re-run the focused test; expect pass.
3. Commit with `feat: read minimum affinity policy`.

Acceptance: unsupported capability sends no policy command; available results are fully validated;
non-capability errors propagate.

## Task 2: Public read adapters

Files: `tests/lpar/test_minimum_affinity_policy.py`, `tests/unit/test_public_api.py`,
`tests/unit/test_tool_registry.py`, `tests/app/test_cli_commands.py`,
`src/hmc_mcp/api.py`, `src/hmc_mcp/server_lpar_config.py`, `src/hmc_mcp/cli_lpars.py`, and the CLI
registration module that currently registers affinity commands.

Interfaces: export `get_minimum_affinity_policy`; add MCP operation
`lpar.get_minimum_affinity_policy`; add CLI `hmc-mcp lpars get-minimum-affinity-policy` using the
same system/LPAR/profile selector contract as existing LPAR affinity reads.

1. Add delegation, authorization-target, registry, JSON, human available, human unavailable, and
   malformed-error propagation tests. Run the named test files; expect missing-adapter failures.
2. Add thin adapters without duplicating validation. Re-run the named tests; expect pass.
3. Commit with `feat: expose minimum affinity policy`.

Acceptance: all surfaces return the shared envelope, MCP metadata says read/LPAR, and no setter is
registered.

## Task 3: Portable snapshot capture

Files: `tests/unit/test_snapshot.py`, `tests/lpar/test_snapshot_capture.py`,
`src/hmc_mcp/snapshot.py`, and `src/hmc_mcp/operations_snapshot.py`.

Interfaces: define `MINIMUM_AFFINITY_POLICY_MEDIA_TYPE`, optional
`SnapshotCapability.unavailable_reason`, and optional
`SnapshotObservations.minimum_affinity_policy`; capture consumes `get_minimum_affinity_policy`
from Task 1. For the minimum-affinity capability, `supported=False` requires a nonblank reason and
no observation, while `supported=True` forbids a reason and requires the observation.

1. Add old-document compatibility, supported round-trip, supported capture, unsupported capture,
   blank unsupported reason, supported-with-reason, and capability/observation mismatch tests. Run
   `uv run pytest -q tests/unit/test_snapshot.py tests/lpar/test_snapshot_capture.py`; expect
   missing-field/capture failures.
2. Add the optional observation, capability entry, and one capture call. Re-run those tests; expect
   pass.
3. Commit with `feat: capture minimum affinity policy`.

Acceptance: supported policy is serialized, unsupported snapshots remain valid with a reason, and
existing snapshots parse unchanged.

## Task 4: Documentation, live proof, and guardrails

Files: `tests/test_live_runner.py`, `scripts/live_hmc_test.py`, `README.md`, and directly relevant
command documentation discovered from existing affinity references.

Interfaces: the live runner calls the Task-1 shared operation; no new production interface.

1. Add a live-runner call-coverage test, implement the read call, and document CLI/MCP behavior and
   capability absence. Run `uv run pytest -q tests/test_live_runner.py`; expect pass.
2. Run `just test`, `just smoke`, and `just verify`; expect all checks green with zero warnings.
3. Review `git diff main...HEAD`, confirm every path serves #315, and commit documentation/live
   changes with `docs: explain minimum affinity policy reads`.

Acceptance: live proof is read-only, docs claim only checked-in evidence, and all guardrails pass.

Rollback: revert the task commits in reverse order; no external or persisted state needs cleanup.
