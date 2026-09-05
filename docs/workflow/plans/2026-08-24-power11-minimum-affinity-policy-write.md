# Power11 Minimum-Affinity Policy Write Implementation Plan

## Goal

Add a validated, capability-gated, ownership-authorized CLI setter and thread its optional policy
through provisioning without inventing REST or snapshot/profile application fields.

## Architecture

The shared policy value and CLI command live in `ssh_commands.py`; the presentation-neutral
authorization workflow lives in `operations/ssh_network.py`; adapters and provisioning delegate to
those boundaries. Tests prove fail-fast ordering and command construction.

## Tech stack and global constraints

Python 3.13 with `uv`, Ruff, ty, pytest, and prek. Lines are at most 100 characters, functions at
most 100 lines, cyclomatic complexity at most 8. Host x86_64; targets amd64, arm64, ppc64le.
Guardrails: `just verify`; `prek run --all-files`. Use only documented Power11 CLI fields; preserve
defaults when policy is absent; `fail` must be an explicit literal selection.

## Task 1: Shared validated CLI mutation

Files: `src/hmc_mcp/ssh_commands.py`, `tests/lpar/test_minimum_affinity_policy.py`.

Interfaces: define frozen `MinimumAffinityPolicy(min_affinity_score: int,
min_affinity_score_action: Literal["none", "warn", "fail"])`; define
`validate_minimum_affinity_policy(policy) -> MinimumAffinityPolicy`; define async
`require_minimum_affinity_policy_capability(config, system_name) -> None`; define async
`set_minimum_affinity_policy_cli(config, system_name, lpar_name, policy) -> str`.

1. Add tests showing invalid bounds/action make zero command calls, unsupported compatibility makes
   no `chsyscfg` call, and explicit `fail` produces one quoted `chsyscfg -r lpar` record with both
   fields. Run `uv run pytest -q tests/lpar/test_minimum_affinity_policy.py`; expect failures for
   missing interfaces.
2. Implement the value, validation, capability helper, and setter using existing
   `get_proc_compat_modes`, `build_i_record`, `shlex.quote`, and `run_hmc_command`. Run the focused
   test; expect pass.
3. Commit with `feat: add minimum affinity policy command`.

## Task 2: Authorized public setter

Files: `src/hmc_mcp/operations/ssh_network.py`, `src/hmc_mcp/server_tools/lpar_config.py`,
`src/hmc_mcp/api.py`, `src/hmc_mcp/server.py`, `tests/lpar/test_minimum_affinity_policy.py`, and
public API/registry boundary tests as required.

Interfaces: define async `set_minimum_affinity_policy(hmc: HMCClient, system: str, lpar: str,
policy: MinimumAffinityPolicy, *, ownership_override: bool = False) -> str`; expose MCP
`hmc_set_minimum_affinity_policy(system_name_or_uuid, lpar_name_or_uuid, policy,
ownership_override=False, profile=None) -> str`.

1. Add tests proving invalid input causes zero REST or SSH calls, target resolution, ownership
   authorization, override propagation, denial before SSH mutation, mutate metadata, and explicit
   `fail` input. Run focused tests; expect fail.
2. Validate as the public operation's first statement, then implement resolution through
   `resolve_system_uuid`/`resolve_lpar_uuid`, ownership-name resolution,
   `authorize_lpar_mutation`, and CLI delegation. Add the MCP and supported Python API exports.
   Run focused and public-boundary tests; expect pass.
3. Commit with `feat: expose authorized affinity policy setter`.

## Task 3: Optional provisioning policy

Files: `src/hmc_mcp/operations/provision.py`, `src/hmc_mcp/server_tools/provision.py`, and provisioning
tests.

Interfaces: add keyword `minimum_affinity_policy: MinimumAffinityPolicy | None = None` to
`provision_lpar` and the MCP adapter. The existing `ProvisionResult.steps` records a named
`minimum_affinity_policy` step only when requested.

1. Add tests proving invalid and unsupported policy fails before create, omission preserves the
   old calls/steps, explicit `fail` is accepted, and requested policy applies after create but
   before network/storage/power. Run the focused provisioning tests; expect fail.
2. Validate policy before system resolution, capability-probe after resolving the system name but
   before all mutations, add the optional step, and invoke the authorized setter after successful
   creation. On setter failure record error and skip later steps. Run focused tests; expect pass.
3. Commit with `feat: apply affinity policy during provisioning`.

## Task 4: Documentation and verification

Files: `README.md` and live/public inventory tests only where established lists require updates.

1. Document the setter, capability gate, explicit `fail`, and partial provisioning semantics.
2. Run `just test`; expect the exact coverage gate to pass. Run `just smoke`; expect MCP handshake
   and updated tool count. Run `just verify` and `prek run --all-files`; expect exit 0 with no
   warnings. Review `git status --porcelain`; expect only intended tracked changes.
3. Commit documentation or guardrail-driven corrections as separate conventional commits.

Rollback is `git revert` of the feature commits. It removes the optional surface without data
migration; remote mutations already performed are not automatically reversed.
