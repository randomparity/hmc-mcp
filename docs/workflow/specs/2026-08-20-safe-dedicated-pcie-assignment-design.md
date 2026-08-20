# Safe dedicated PCIe assignment design

## Goal

Replace the one-way forced profile mutation with symmetric dedicated PCIe assign/unassign
contracts that obey [ADR 0053](../../adr/0053-evidence-backed-pcie-capability-contract.md) and
[ADR 0055](../../adr/0055-safe-dedicated-pcie-assignment.md).

## Contract

`assign_dedicated_pcie_slot` and `unassign_dedicated_pcie_slot` consume the normalized system,
LPAR, profile name, and `drc_index` selector plus `ownership_override=False`. The eventual
`AssignmentResult` models profile and effective before/after state separately; one cannot stand in
for the other. Empty or duplicate selectors are errors.

Before capability selection, the operation resolves the target LPAR and reads its description
token through the existing ADR 0011 ownership path. An absent token is an advisory no-claim and
may proceed; a malformed or foreign token fails unless `ownership_override=True`, which callers
may supply only after operator approval. Slot occupancy remains a separate precondition.

ADR 0053 admits inventory but no verifiable profile mutation path. Therefore every assign or
unassign raises `PcieAssignmentUnavailableError` before mutation, even when effective inventory
appears to match: effective ownership cannot prove profile membership. Symmetric SSH profile
commands remain private transport primitives and omit `--force`; public operations do not select
them until exact readback is admitted.

The old `hmc_assign_profile_io_slot` public path and raw-output contract are removed. MCP and CLI
expose `pcie.assign_dedicated_slot` and `pcie.unassign_dedicated_slot`; the reusable Python API
exports the same presentation-neutral operations.

## Errors and verification

Inventory failures propagate without mutation. Unknown slots, blank selectors, duplicate DRC
rows, and foreign owners raise actionable `ValueError`s. Capability-unavailable is a distinct
exception. Since no mutation is currently permitted, readback mismatch and mutation failure are
structurally unreachable; tests pin that no mutation command is selected. Command-construction
tests independently pin symmetric `+=`/`-=` records and absence of `--force`.

## Threat model

The widened boundaries are authenticated MCP/CLI/Python callers supplying system, LPAR, profile,
and DRC values, and SSH command construction. Selector resolution and attribute-record validation
are existing controls. Ownership is re-read from normalized inventory immediately before any
decision. Shell arguments use quoting and record values reject structural characters. HMC
authorization remains outside this change; no live mutation is attempted under the admitted
capability matrix.

## Testing

Behavior tests cover repeatable capability-unavailable assign/unassign, absent/malformed/foreign
ownership tokens and explicit override, absent and duplicate selectors, inventory/readback
failures, command construction,
MCP metadata, CLI JSON output, and removal of the old entry point. `just verify` is the final gate.

## Resume facts

- Branch: `feat/safe-pcie-assignment-213`
- Base branch: `main`
- Guardrail: `just verify`
