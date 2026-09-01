# SR-IOV physical-port state implementation plan

Goal: support evidence-backed RoCE and converged-Ethernet physical-port reads
with fail-closed selection and normalized up/down state.

Architecture: the SSH boundary validates the adapter selector, performs the two
literal-level reads, and returns one validated row set. The operation boundary
retains environment admission and maps HMC state into the existing public
inventory model. Python 3.11+, pytest, Ruff, ty, and the repository `just`
recipes remain the stack.

Expected implementation size: 140–220 changed lines (L) — derived from three
production functions, focused SSH/system/cross-operation tests, and three contract documents.

## Global constraints

- Preserve HMC V10R3 M1060 and managed-system model 8375-42A as the exact
  admitted environment pair; do not add a dependency or migration.
- Query only literal `roce` and `ethc` levels with the existing projection and
  adapter filter; the captured POWER9 JSON remains byte-for-byte unchanged.
- Require a positive decimal adapter ID, exactly one non-empty level, matching
  row adapter values, and state values limited to `0` and `1`. Preserve
  `phys_port_type` as opaque HMC data; it does not equal the query level in the
  captured RoCE fixture.
- Support the repository's amd64 and arm64 CI targets on Python 3.11–3.14.
- Guardrails are `just verify` and
  `UV_NO_SYNC=1 uv run --no-sync prek run --all-files`; focused test commands
  use `uv run --no-sync`.

## File map

- `src/hmc_mcp/ssh/network.py`: validate and query both physical-port levels.
- `src/hmc_mcp/operations/pcie.py`: map `state` to availability.
- `src/hmc_mcp/operations/lpar/assignments.py`: consume normalized port health.
- `tests/unit/test_sriov_ssh_contract.py`: selector, command, and row failures.
- `tests/system/test_normalized_pcie_inventory.py`: state and admission behavior.
- `tests/lpar/test_pcie_assignments.py`: assignment compatibility with normalized state.
- `tests/fixtures/pcie/power9-v10r3m1060-live-sriov.json`: read-only evidence.
- `docs/hmc-cli-cheatsheet.md`, ADR 0056, ADR 0113, and `CHANGELOG.md`: contracts.

## Task 1: validate and select the physical-port level

Interfaces:

- Consume existing `build_filter`, `run_hmc_command`, `_parse_admitted_rows`,
  and `list_sriov_physical_port_rows(config, system_name, adapter_id)`.
- Preserve the function's `list[dict[str, str]]` return for the operation layer.

1. Add parameterized async tests in `tests/unit/test_sriov_ssh_contract.py`
   whose mocked command results prove RoCE-only and ethc-only selection. Assert
   both commands use the same filter/projection and their literal levels.
2. Run `uv run --no-sync pytest -q --no-cov
   tests/unit/test_sriov_ssh_contract.py`; expect the new tests to fail because
   only `roce` is queried.
3. Add tests that expect `ValueError` for adapter IDs `0`, `-1`, `null`, and
   `unavailable`; both non-empty; and a row whose `adapter_id` differs. Add a
   both-empty test that asserts the SSH boundary returns an empty list for its
   existing internal consumers. Include a RoCE row whose `phys_port_type` is
   `eth` and assert it remains accepted, matching the captured fixture.
4. Implement the minimum validation and two-command selection in
   `list_sriov_physical_port_rows`. Keep command construction in that function
   and use the existing parser for each result.
5. Re-run the focused command; expect all tests to pass. Temporarily change one
   accepted level in a test fixture to the opposite value, confirm that test
   fails, restore it, then commit with conventional test/fix history.

Acceptance: invalid selectors run no SSH command; exactly two reads occur for a
valid selector; exactly one valid result is returned; malformed and ambiguous
cases raise actionable `ValueError`; both empty returns no SSH rows.

## Task 2: normalize physical-port state

Interfaces:

- Consume `list_sriov_physical_port_rows` and construct existing
  `SriovPhysicalPort(system, adapter_id, physical_port_id, availability,
  location, configured_logical_ports, max_logical_ports,
  current_logical_ports)` values.
- Preserve `list_sriov_physical_ports(...) -> InventoryResult[SriovPhysicalPort]`.

1. Extend `tests/system/test_normalized_pcie_inventory.py` with rows containing
   state `1` and `0`; assert availability `up` and `down`.
2. Add malformed-state tests for blank and `2`. Include a red-first case where
   a requested valid port has state `1` but an unselected sibling row has state
   `2`; require the entire call to raise `ValueError`. Retain the current test
   proving an unadmitted environment performs no physical-port read.
   Add a both-empty operation test requiring
   `SriovLogicalPortCapabilityError` instead of available-empty inventory.
3. Run `uv run --no-sync pytest -q --no-cov
   tests/system/test_normalized_pcie_inventory.py`; expect the mapping assertion
   to fail because raw state is currently exposed.
4. Validate and normalize every returned row with a local two-value state map
   before applying `physical_port_id` filtering. Raise `ValueError` naming the
   malformed physical-port state otherwise.
5. Re-run the focused command; expect all tests to pass, then commit.

Acceptance: public inventory exposes only `up`/`down`; invalid state in any
returned row fails even when that row is not selected; the existing
capability-unavailable path remains before physical-port reads; both supported
levels empty raises the existing SR-IOV capability exception.

## Task 3: preserve and publish the evidence-backed contract

Interfaces:

- Consume the existing live fixture through its current fixture loader; do not
  edit its bytes.
- Document the same two literal levels and state mapping implemented above.

1. Add a focused regression test that feeds the captured RoCE command/output
   plus a synthetic empty ethc companion and asserts the captured rows remain
   accepted. Record the fixture checksum before and after the test change.
2. Update `docs/hmc-cli-cheatsheet.md` with both commands, paired selection,
   empty/ambiguity behavior, and `0`/`1` mapping. Add a concise `[Unreleased]`
   changelog bullet and retain ADR 0056's one-line supersession banner.
3. Run both focused pytest commands, `just adr-numbering`, and the applicable
   document checks; expect green results and no fixture diff.
4. Run `just verify`, then
   `UV_NO_SYNC=1 uv run --no-sync prek run --all-files`; expect exit 0. If a
   current failure artifact identifies a defect in scope, fix the cause and
   rerun the failing command before the full gates.

Acceptance: source, tests, ADRs, operator docs, and changelog state the same
contract; the captured fixture is unchanged; all guardrails pass. Rollback is
the task commits in reverse order and requires no external cleanup.

## Task 4: close branch-review compatibility and boundary findings

Interfaces:

- Consume normalized `SriovPhysicalPort.availability` values `up` and `down` in
  `_validate_sriov_inventory`; leave `SriovAdapter.availability` on its existing
  raw `1` contract.
- Preserve `list_sriov_physical_port_rows` and public operation signatures.

1. Add a failing cross-operation test in `tests/lpar/test_pcie_assignments.py`
   that drives assignment prevalidation with a real normalized healthy
   physical-port result and proves `up` is accepted while `down` is rejected.
2. Run `uv run --no-sync pytest -q --no-cov
   tests/lpar/test_pcie_assignments.py`; expect the healthy case to fail because
   prevalidation still compares with raw `1`.
3. Change only the physical-port comparison in
   `src/hmc_mcp/operations/lpar/assignments.py` to require `up`; rerun the
   focused test and expect green.
4. Parameterize invalid adapter-ID tests with Arabic-Indic and full-width digits,
   assert zero SSH reads, and require ASCII decimal digits in the SSH guard.
5. In the captured-fixture regression, assert the first mocked command equals
   the fixture's exact RoCE command and the second equals the corresponding
   literal `ethc` companion command. Keep fixture bytes unchanged.
6. Run the affected focused suites, `just verify`, and
   `UV_NO_SYNC=1 uv run --no-sync prek run --all-files`; expect exit 0, then
   commit the review fixes as one compatibility slice.

Acceptance: declarative assignment accepts normalized healthy ports and rejects
down ports; Unicode decimal selectors run no SSH command; the fixture regression
anchors both exact commands; all repository gates pass.

## Durable handoff

- Branch: `feat/sriov-port-state-557`
- Base branch: `main`
- Guardrails: focused pytest commands above, `just verify`, and
  `UV_NO_SYNC=1 uv run --no-sync prek run --all-files`.
- Open findings and deferrals: none before design review.
- Authorized scope expansion: `src/hmc_mcp/operations/lpar/assignments.py` and
  `tests/lpar/test_pcie_assignments.py`, approved by the operator after branch
  review reproduced the normalized-state compatibility regression.
