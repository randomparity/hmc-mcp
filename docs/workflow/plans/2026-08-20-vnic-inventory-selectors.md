# vNIC inventory selectors implementation plan

**Goal:** Replace opaque vNIC mutation arguments with an evidence-bounded typed selector and
verified add/remove orchestration.

**Architecture:** Strict SSH collectors provide raw admitted state. A presentation-neutral
operation validates/authorizes, dispatches one mutation, and reconciles vNIC plus backing-device
state. MCP, CLI, and Python API only adapt that operation.

**Tech stack:** Python 3.11+, dataclasses, Decimal, async SSH, pytest, Typer, FastMCP.

## Global constraints

- Support only POWER9 8375-42A / HMC V10R3 M1060.
- No dependency, migration, compatibility alias, opaque syntax, failover, or rollback claim.
- Preserve exact inventory identities and decimal percentages.
- Host arm64; targets amd64, arm64, and ppc64le; relationship included.
- Guardrail: `just verify`.

## Task 1: Freeze evidence and SSH grammar

**Files:** create `tests/fixtures/pcie/power9-v10r3m1060-live-vnic.json`; modify
`src/hmc_mcp/ssh_commands.py`, `tests/system/test_pcie_contract.py`, and
`tests/unit/test_vnic_ssh_contract.py`.

**Interfaces:** Provide `list_vnic_rows(config, system, lpar)`,
`list_vnic_backing_rows(config, system)`, `read_vios_identity(config, system, vios)`,
`add_vnic_backing(config, system, lpar, backing_device, vlan)`, and
`remove_vnic_slot(config, system, lpar, slot_num)`.

1. Add fixture and failing tests for exact fields, empty result, strict malformed rows, `-p` add,
   `-s` remove, and whole-payload quoting. Run
   `uv run pytest -q tests/system/test_pcie_contract.py tests/unit/test_vnic_ssh_contract.py`;
   expect failures naming missing collectors/functions.
2. Implement only the strict collectors and command builders. Re-run the command; expect all
   selected tests to pass.
3. Commit as `feat: add evidence-backed vnic ssh contract`.

## Task 2: Implement verified orchestration

**Files:** modify `src/hmc_mcp/operations_ssh_network.py` and
`tests/network/test_vnic_operations.py`; directly consume ADR 0056 inventory collectors and target
LPAR authorization.

**Interfaces:** Define `VnicBackingSelector`, `VnicBackingSnapshot`, `VnicSnapshot`,
`VnicChangeResult`, `VnicCapabilityError`, `VnicPartialError`, and async `add_vnic(...)` /
`remove_vnic(...)` signatures from the spec. `VnicChangeResult.changed` is `bool | None`; it also
defines `mutation_dispatched`, always-present immutable `vnic_before`, `backing_before`,
`vnic_after`, and `backing_after` tuple fields, separate vNIC/backing after-read flags, output, and
ordered error strings. Empty tuple plus a successful flag means verified absence; ambiguous rows
remain observable. The exact ordered field names are transcribed in the spec. Task 3 consumes these
exact names.

1. Add failing tests for blank/range/precision validation, wrong VIOS identity/type, adapter/port
   mismatch, exhausted capacity, duplicate inventory, verified ensure-one retry, degraded retry
   refusal, successful add correlation, mutation failure, readback mismatch, absent remove retry,
   zero/multiple/degraded remove correlation refusal, successful remove, command timeout with one
   failed reconciliation read, and command failure with both reads failed and every cause retained.
   Add table-driven add/remove cases for all six reconciliation rows and the captured HMC-only VLAN
   rejection as a partial error. Add ambiguous-row retention, conflicting cross-projection
   capacity, same logical-port ID on different adapters, every layer-specific HMC delimiter,
   successful-dispatch mismatch error ordering, every result-field invariant, and separately
   other-shell-metacharacter quoting cases.
   Run `uv run pytest -q tests/network/test_vnic_operations.py`; expect collection or
   assertion failures against the old raw-output API.
2. Implement immutable models and the smallest orchestration satisfying each test. Re-run the
   command; expect all tests to pass.
3. Commit as `feat: verify vnic backing mutations`.

## Task 3: Replace every presentation surface

**Files:** modify `src/hmc_mcp/server_network.py`, `src/hmc_mcp/cli_network.py`,
`src/hmc_mcp/api.py`, `tests/network/test_vnics.py`, `tests/app/test_cli_commands.py`,
`tests/app/test_capabilities.py`, `tests/unit/test_public_api.py`, and directly related security
schema tests `tests/app/test_tool_security.py` and `tests/unit/test_ssh_quoting.py`.

**Interfaces:** MCP add takes target system/LPAR plus `vios_name`, `vios_lpar_id`, `adapter_id`,
`physical_port_id`, `capacity_percent`, and `port_vlan_id`; remove takes `slot_num`. CLI mirrors
these fields. Python exports Task 2 models/errors/operations.

1. Replace tests first and assert rendered schemas contain the new fields and omit
   `backing_devices`, `virtual_switch_name`, top-level `capacity`, and `vnic_id`. Run
   `uv run pytest -q tests/network/test_vnics.py tests/app/test_cli_commands.py tests/app/test_capabilities.py tests/app/test_tool_security.py tests/unit/test_public_api.py tests/unit/test_ssh_quoting.py`;
   expect failures against old signatures.
2. Replace adapters and exports without aliases. Run the same command plus
   `uv run python scripts/smoke_mcp.py`; expect passing tests and smoke.
3. Commit as `feat: expose typed vnic selectors`.

## Task 4: Document and verify the complete contract

**Files:** modify `README.md` and any schema-count assertions discovered by the focused suite.

**Interfaces:** Document only the Task 2/3 installed names and stable result/error fields.

1. Update README with the admitted family, selector fields, HMC-assigned logical-port output,
   slot-based remove, partial-error semantics, and unsupported cells.
2. Run `rg -n "backing_devices|vnic_id|virtual_switch_name" src tests README.md`; expect only raw
   HMC readback field names and historical fixture evidence, never public add/remove parameters.
3. Run `just verify` bare; expect exit 0 and report its test, coverage, smoke, CLI, and artifact
   summaries.
4. Commit as `docs: describe verified vnic backing operations`.
