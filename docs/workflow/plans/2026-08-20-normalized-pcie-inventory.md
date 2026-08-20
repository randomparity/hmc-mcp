# Normalized PCIe inventory implementation plan

## Goal and architecture

Add four stable system-scoped inventory contracts, backed by the one read projection admitted by
ADR 0053 and explicit unavailable results for the three selector-only SR-IOV families. A strict SSH
boundary returns rows; `operations_pcie.py` owns immutable models and normalization; Python, MCP,
and CLI adapters only serialize and present those models.

Tech stack: Python 3.13, immutable dataclasses, `Decimal`, Typer, FastMCP, pytest, `uv`.

## Global constraints

- Python 3.13, `uv`, immutable dataclasses, absolute imports, and 100-character lines.
- No new dependency, inferred HMC field, unsupported-error classifier, mutation, or ADR index edit.
- Stable identities and percentage units are exactly those accepted by ADR 0053.
- Guardrail: `just verify`.

## File map

- `src/hmc_mcp/ssh_commands.py`: exact dedicated-slot command and strict row parser call.
- `src/hmc_mcp/operations_pcie.py`: result/model schemas and four system-selector-aware operations.
- `src/hmc_mcp/api.py`: supported reusable exports.
- `src/hmc_mcp/server_system_resources.py`, `src/hmc_mcp/server.py`: MCP adapters/exports.
- `src/hmc_mcp/cli_network.py`: CLI commands and rendering.
- `tests/system/test_normalized_pcie_inventory.py`: SSH and normalization behavior.
- `tests/unit/test_pcie_inventory_contract.py`: models, API, MCP, and CLI contracts.
- `README.md`: public command/tool/schema documentation.
- Design and ADR 0054: durable decision record; no ADR index exists.

## Task 1: Prove and implement the dedicated-slot boundary

**Interfaces.** Add
`async def list_dedicated_pcie_slot_rows(config: HMCConfig, system_name: str) -> list[dict[str, str]]`.
It consumes `run_hmc_command` and `parse_hmc_delimited_rows`; Task 2 consumes its exact three keys.

1. Add tests that mock `run_hmc_command`, require
   `lshwres -r io --rsubtype slot -m sys1 -F drc_index,description,lpar_name --header`, and assert
   header-only output returns `[]`, blank optional fields survive, header mismatch and missing
   columns raise `ValueError`.
2. Run `uv run pytest -q tests/system/test_normalized_pcie_inventory.py`; expect failures because
   the function is absent.
3. Implement the function with the fixed fields tuple, `shlex.quote(system_name)`, and
   `parse_hmc_delimited_rows`.
4. Re-run the focused test; expect all Task 1 cases to pass. Commit as
   `feat: add strict dedicated PCIe slot reader`.

## Task 2: Prove and implement presentation-neutral schemas

**Interfaces.** Create immutable `InventoryResult[T]`, `DedicatedSlot`, `SriovAdapter`,
`SriovPhysicalPort`, and `SriovLogicalPort`. Add
`list_dedicated_slots(config, system)`, `list_sriov_adapters(config, system)`,
`list_sriov_physical_ports(config, system, adapter_id=None)`, and
`list_sriov_logical_ports(config, system, adapter_id=None, physical_port_id=None)`.
Tasks 3 and 4 consume these types and functions unchanged.

1. Add model tests pinning `resource_kind`, explicit capability/reason, managed-system plus local
   selector identities, parent IDs, `Decimal | None` percentage fields, and dataclass serialization.
   Add operation tests that mock `resolve_ssh_names`, prove dedicated row normalization, reject a
   blank `drc_index`, preserve empty optional values as `None`, and prove SR-IOV operations return
   unavailable without calling an SSH inventory command. Selector filters remain represented in
   the result scope and are never treated as proof that a record exists.
2. Run the focused tests; expect import failures.
3. Implement the dataclasses and operations. Use one private helper for the three repeated
   unavailable-result constructions only after all three call sites exist. Use stable reason text
   referencing ADR 0053's missing projection. Do not catch command/parser failures.
4. Re-run the focused tests; expect all Task 2 cases to pass. Commit as
   `feat: normalize PCIe inventory contracts`.

## Task 3: Expose MCP and supported Python contracts

**Interfaces.** Export every model and operation from `hmc_mcp.api`. Add MCP tools
`hmc_list_dedicated_pcie_slots`, `hmc_list_sriov_adapters`,
`hmc_list_sriov_physical_ports`, and `hmc_list_sriov_logical_ports`; each takes a system selector
and optional family selector arguments, returns a JSON-serializable dictionary, and has read
security metadata targeting `managed_system`.

1. Add contract tests for exact exports, signatures, operation names, target kind, selector
   forwarding, and serialized available/unavailable shapes.
2. Run `uv run pytest -q tests/unit/test_pcie_inventory_contract.py`; expect failures because the
   exports/tools are absent.
3. Implement the API exports and thin MCP adapters using `_ssh_with_client` and `asdict`.
4. Run the unit contract tests and `uv run python scripts/smoke_mcp.py`; expect all tests and the
   handshake to pass. Commit as `feat: expose normalized PCIe inventory tools`.

## Task 4: Expose CLI and documentation contracts

**Interfaces.** Add network commands `list-dedicated-pcie-slots`, `list-sriov-adapters`,
`list-sriov-physical-ports`, and `list-sriov-logical-ports`, all with `--json`; physical/logical
commands accept parent selector options. JSON is the dataclass dictionary; text output states
capability unavailable with its reason or renders available records.

1. Add CliRunner tests for command registration, selector forwarding, JSON schemas, available
   empty output, and unavailable output. Run the unit contract test; expect missing-command failures.
2. Implement commands using the presentation-neutral operations and `asdict`, without duplicating
   model rules. Re-run the focused tests; expect them to pass.
3. Document identities, hierarchy, percentage capacity, explicit unknowns, capability states,
   selectors, and the legacy raw command distinction in `README.md`.
4. Run `just verify`; expect zero warnings/failures and `verify: all groups load OK`. Commit as
   `docs: document normalized PCIe inventory`.

## Rollback and completion

Every commit is independently revertible. Reverting all four removes the new entry points without
changing legacy inventory or mutation behavior. Before shipping, inspect `git diff main...HEAD`,
run the adversarial review and simplification passes, then run `just verify` again.

