# System-scoped name resolution implementation plan

**Goal:** Fail closed on ambiguous HMC names and make destructive duplicate-name
operations actionable through optional managed-system scope.

**Architecture:** Client finders own exact match selection and ambiguity
diagnostics. Shared resolvers translate an optional system name/UUID into the
finder's system UUID; operation and tool layers only forward the selector.

**Tech stack:** Python 3.13, httpx/respx, pytest, FastMCP, uv, ruff, ty.

## Global constraints

- Branch `feat/ambiguous-resolver-scope-140`, base `main`; use `just verify`.
- Host `arm64`; no target architecture is declared.
- Preserve UUID pass-through and existing single/no-match behavior.
- Do not extend system scope to unrelated read, storage, or provisioning tools.
- Ambiguity diagnostics must retain every candidate and identify partition
  parents; parent-discovery failure aborts lookup and resolution must never fall
  back to result zero.

## Task 1: Fail closed in client finders

**Files:** Modify `src/hmc_mcp/client/client_lpars.py`,
`src/hmc_mcp/client/client_systems.py`, `src/hmc_mcp/client/client_contracts.py`; test in
`tests/unit/test_client.py` and `tests/unit/test_client_domain_mixins.py`.

**Interfaces:** Define
`find_partition_by_name(name: str, system_uuid: str | None = None)` and
`find_vios_by_name(name: str, system_uuid: str | None = None)`; retain
`find_system_by_name(name: str)`. Later tasks consume these signatures.

1. Add duplicate-result tests for all three finders. Partition/VIOS fixtures
   contain two UUIDs mapped through two managed-system child feeds; assertions
   require both resource and parent identities. Run
   `uv run pytest -q tests/unit/test_client.py tests/unit/test_client_domain_mixins.py`;
   expect the new tests to fail because result zero is returned.
2. Add missing-parent, multiple-parent, and failed-parent-request tests; require
   actionable lookup failure with no returned candidate. Add scoped zero-,
   one-, and many-result tests for both LPAR and VIOS child collections; the
   many-result assertions require every UUID in deterministic diagnostics and
   no returned candidate. Expect failure because the keyword is unsupported.
3. Implement zero/one/many selection, deterministic candidate formatting,
   parent discovery on ambiguous partition results, and scoped child listing.
4. Re-run the focused command; expect all tests to pass. Commit as
   `fix: reject ambiguous HMC name matches`.

**Acceptance:** No finder silently collapses multiple results; scoped matches
use only the requested system; zero and one retain their prior values.

## Task 2: Thread resolver scope

**Files:** Modify `src/hmc_mcp/common.py`; create
`tests/unit/test_common_resolvers.py`.

**Interfaces:** Define keyword-only
`resolve_lpar_uuid(hmc, value, *, system_name_or_uuid=None)` and the equivalent
`resolve_vios_uuid`; consume `resolve_system_uuid` and Task 1 finders.

1. Add tests proving name+scope resolves the system then calls the finder with
   its UUID, UUID resources skip both lookups, and no-match messages remain
   unchanged. Run `uv run pytest -q tests/unit/test_common_resolvers.py`; expect
   scoped tests to fail.
2. Implement the two signatures and forwarding without changing unscoped call
   sites. Run `uv run pytest -q tests/unit/test_common_resolvers.py`; expect all
   tests to pass. Commit as
   `fix: add managed-system scope to partition resolvers`.

**Acceptance:** Scope is optional, UUID pass-through performs no I/O, and legacy
no-match guidance is unchanged.

## Task 3: Expose scope on destructive tools

**Files:** Modify `src/hmc_mcp/operations/lpar/core.py`,
`src/hmc_mcp/operations/vios.py`, `src/hmc_mcp/server_tools/lpars.py`, and
`src/hmc_mcp/server_tools/vios.py`; test in `tests/app/test_server_tools.py`,
`tests/app/test_capabilities.py`, `tests/lpar/test_power_tools.py`,
`tests/vios/test_vios_lifecycle.py`, and `tests/vios/test_vios_backup.py`.

**Interfaces:** `delete_lpar` and `rename_lpar` use their existing
`system_name_or_uuid` during LPAR resolution; `power_lpar` and `power_vios` accept keyword-only optional
system scope. Destructive public tools forward an optional
`system_name_or_uuid`; VIOS restore's private helper accepts the same selector.

1. Add tests for LPAR delete, rename, and power-off plus VIOS delete, restore, and
   power-off, asserting scope reaches the resolver before mutation. Run
   `uv run pytest -q tests/app/test_server_tools.py tests/app/test_capabilities.py tests/lpar/test_power_tools.py tests/vios/test_vios_lifecycle.py tests/vios/test_vios_backup.py`;
   expect failures from unsupported parameters or unscoped calls.
2. Implement minimal forwarding and update docstrings. Do not change power-on,
   read, storage, or provisioning tool signatures. Re-run the same focused
   pytest command; expect all to pass.
3. Run `just verify`; expect lint, types, 991+ tests, smoke import, and CLI group
   checks to pass. Commit as `fix: scope destructive partition operations`.

**Acceptance:** Every destructive name path in the owned LPAR/VIOS tool surface
can disambiguate; existing callers remain valid through optional defaults.

## Task 4: Final verification and cleanup

**Files:** Review all changed paths; no new runtime surface.

1. Run `git diff --check main...HEAD` and inspect the complete diff; expect no
   whitespace errors or unrelated paths.
2. Run `just verify`; expect every gate to pass with zero warnings from project
   tools (known third-party TLS warnings may remain).
3. If implementation changed the design, update the spec/ADR first and rerun
   their review; otherwise commit any test-only cleanup separately.

**Acceptance:** The branch is internally consistent, fully verified, and ready
for adversarial branch review.
