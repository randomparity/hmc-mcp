# PCIe and SR-IOV capability contract implementation plan

## Goal

Land documentation-backed PCIe/SR-IOV evidence, a presentation-neutral strict CSV parser, and
tests that pin the identities, percentage units, operation matrix, unavailable-capability failure
behavior, and no-mutation boundary defined by ADR 0053 and the design spec.

The parser is one small reusable boundary in `ssh_commands.py`; it does not execute SSH. JSON
evidence records keep IBM source excerpts separate from explicitly synthetic parser examples.
System tests validate the records and the durable operation contract.

**Tech stack:** Python 3.11+, stdlib `csv`/`io`/`json`, pytest, Markdown, `just verify`.

**Focused test command:** `uv run --no-sync pytest -q --no-cov tests/system/test_pcie_contract.py`.
Use it for every red, green, and bite run below; bare `just verify` owns coverage enforcement.

## Global constraints

- Supported repository range remains HMC V8–V11, but Power-generation documentation is not
  represented as a precise HMC software release. Use `hmc_release: "not-established"` unless the
  source itself names an exact release.
- Evidence sources are HTTPS IBM pages. Synthetic examples are labelled `synthetic` and never
  described as live HMC output.
- Exact common fields are those in the design; unknown family/resource cells remain unknown.
- Capacity values are decimal percentages with up to two decimal places; no byte, bandwidth,
  zero, sum, or availability inference is added.
- This issue adds no SSH execution, mutation implementation, tool metadata, CLI/server/API export,
  or call from the parser to `run_hmc_command`.
- `BASE_BRANCH=main`; full guardrail is `just verify`; ADR index coupling is `no index`.

## File map

- `src/hmc_mcp/ssh_commands.py`: add only `parse_hmc_delimited_rows` and parser validation.
- `tests/system/test_pcie_contract.py`: parser tests, evidence-schema tests, identity/unit checks,
  state-matrix assertions, and diff-scoped no-mutation assertions.
- `tests/fixtures/pcie/*.json`: one record per admitted documentation family/resource claim.
- `docs/adr/0053-evidence-backed-pcie-capability-contract.md`: flip Proposed to Accepted only after
  all tests and `just verify` pass.
- `docs/workflow/specs/2026-08-20-pcie-capability-contract-design.md`: durable contract, already
  reviewed; change only if implementation discovers a contradiction.

## Task 1: Implement the strict delimited-row parser with tests

**Files:** modify `src/hmc_mcp/ssh_commands.py`; create
`tests/system/test_pcie_contract.py`.

**Interfaces**

- Provides:
  `parse_hmc_delimited_rows(text: str, fields: Sequence[str], delimiter: str = ",") -> list[dict[str, str]]`
- Consumes only stdlib `csv`, `io.StringIO`, and `collections.abc.Sequence`.
- Task 2 relies on this function to parse each synthetic example.

1. Add failing tests for empty/duplicate/blank fields, invalid delimiters, missing or wrong header,
   header-only output, empty/blank input, quoted commas, empty values, whitespace preservation,
   blank lines, final-newline absence, delimiter-only rows, and column-count mismatch.
2. Run the focused test command; expect failures because the parser is absent.
3. Add the parser:

```python
def parse_hmc_delimited_rows(
    text: str,
    fields: Sequence[str],
    delimiter: str = ",",
) -> list[dict[str, str]]:
    expected = tuple(fields)
    if not expected or any(not field or field.strip() != field for field in expected):
        raise ValueError("fields must contain non-empty names without surrounding whitespace")
    if len(set(expected)) != len(expected):
        raise ValueError("fields must not contain duplicates")
    if len(delimiter) != 1 or delimiter in {"\r", "\n"}:
        raise ValueError("delimiter must be one non-newline character")

    records = [line for line in text.splitlines() if line.strip()]
    if not records:
        raise ValueError("HMC delimited output is missing its header")
    parsed = list(csv.reader(records, delimiter=delimiter))
    if tuple(parsed[0]) != expected:
        raise ValueError("HMC delimited header does not match the requested fields")

    rows: list[dict[str, str]] = []
    for number, values in enumerate(parsed[1:], start=2):
        if len(values) != len(expected):
            raise ValueError(
                f"HMC delimited row {number} has {len(values)} columns; expected {len(expected)}"
            )
        rows.append(dict(zip(expected, values, strict=True)))
    return rows
```

4. Run the focused test; expect all parser cases green. Temporarily change the header comparison to
   accept a wrong header, confirm the wrong-header test fails, then restore and rerun green.
5. Run `just verify`; expect green. Commit:
   `feat: add strict HMC delimited parser`.

**Acceptance:** exact header and row shape are enforced; blank stream is malformed; header-only is
available-empty; the parser never executes a command.

## Task 2: Add documentation evidence and contract tests

**Files:** create JSON records under `tests/fixtures/pcie/`; extend
`tests/system/test_pcie_contract.py`.

**Interfaces**

- Consumes `parse_hmc_delimited_rows` from Task 1.
- Provides the spec's two closed evidence-record shapes, discriminated by `record_kind`.
- Task 3 relies on these tests proving every documented/unknown matrix cell and state contract.

1. Add failing parameterized tests that enumerate the exact expected record names and compare
   every loaded JSON object with the canonical values below. Also require parser success, stable
   identity fields, and `percent` for all capacity-bearing records.
2. Run the focused test; expect missing-fixture failures.
3. Add exactly these records (source text drift is a stop condition: if the named section no
   longer contains the excerpt, do not substitute another claim without returning to design):

| File | Kind | URL/section | Command/fields or admitted claim |
|---|---|---|---|
| `power8-profile.json` | `read-fixture` | `https://www.ibm.com/docs/en/power8/8284-22A?topic=commands-lssyscfg`; `Partition profile properties` | command `lssyscfg -r prof -m sys1 -F io_slots,sriov_eth_logical_ports --header`; fields `io_slots,sriov_eth_logical_ports`; excerpt `Profile output exposes the io_slots and sriov_eth_logical_ports properties.`; stdout `io_slots,sriov_eth_logical_ports\n21010003//0,1/0/27004001/10.25/20.50\n` |
| `power9-io-slot.json` | `read-fixture` | `https://www.ibm.com/docs/en/power9/0000-REF?topic=POWER9_REF%2Fp9edm%2Flshwres.htm`; `EXAMPLES — physical I/O slots` | command `lshwres -r io --rsubtype slot -m sys1 -F drc_index,description,lpar_name --header`; fields `drc_index,description,lpar_name`; excerpt `List the DRC index, description, and owning partition for each physical I/O slot.`; stdout `drc_index,description,lpar_name\n21010003,PCIe slot,lpar1\n21010004,PCIe slot,\n` |
| `power9-sriov-adapter.json` | `read-fixture` | same exact URL; `SYNOPSIS — SR-IOV adapters` | command `lshwres -r sriov --rsubtype adapter -m sys1 -F adapter_id,slot_id,config_state --header`; fields `adapter_id,slot_id,config_state`; excerpt `SR-IOV adapter output identifies adapters, slots, and configuration state.`; stdout `adapter_id,slot_id,config_state\n1,21010202,1\n` |
| `power9-sriov-physport.json` | `read-fixture` | same exact URL; `SYNOPSIS — SR-IOV physical ports` | command `lshwres -r sriov --rsubtype physport --level eth -m sys1 -F adapter_id,phys_port_id,state,phys_port_loc,min_eth_capacity_granularity --header`; fields `adapter_id,phys_port_id,state,phys_port_loc,min_eth_capacity_granularity`; excerpt `Ethernet physical-port output identifies its adapter, port, state, location, and minimum capacity granularity.`; stdout `adapter_id,phys_port_id,state,phys_port_loc,min_eth_capacity_granularity\n1,0,1,U78D5-P1-C1-T1,0.25\n`; unit `percent` |
| `power9-sriov-logport.json` | `read-fixture` | same exact URL; `SYNOPSIS — SR-IOV logical ports` | command `lshwres -r sriov --rsubtype logport --level eth -m sys1 -F adapter_id,phys_port_id,logical_port_id,lpar_id,lpar_name,capacity,max_capacity --header`; fields `adapter_id,phys_port_id,logical_port_id,lpar_id,lpar_name,capacity,max_capacity`; excerpt `Ethernet logical-port output identifies its adapter, physical port, logical-port DRC index, owner, and capacities.`; stdout `adapter_id,phys_port_id,logical_port_id,lpar_id,lpar_name,capacity,max_capacity\n1,0,27004001,7,lpar1,10.25,20.50\n1,0,27004002,,,10.25,20.50\n`; unit `percent` |
| `power10-sriov-contract.json` | `contract-evidence` | `https://www.ibm.com/docs/en/power10/7063-CR1?topic=commands-chhwres`; `SR-IOV attributes and examples` | excerpt `SR-IOV operations select slots, adapters, and logical ports and express capacity and minimum granularity as percentages.`; exact claims `adapter mode uses -o a/r with slot_id`, `logical-port operations use adapter_id and logical_port_id`, `capacity, max_capacity, and minimum granularity are percent with up to two decimals`; unit `percent` |
| `power11-sriov-contract.json` | `contract-evidence` | `https://www.ibm.com/docs/en/power11/9824-42A?topic=commands-chhwres`; `SR-IOV attributes and examples` | same excerpt and exact claims as Power10; unit `percent` |

   Every `read-fixture` uses the table's literal values and exactly these common keys:
   `record_kind: "read-fixture"`, `evidence_kind: "documentation"`,
   `documentation_family` equal to the filename's family, `hmc_release: "not-established"`,
   `support: "documented"`, and the table's literal `source_url`, `source_section`, `command`,
   `fields`, and `source_excerpt`. Its `parser_examples` is exactly
   `{"kind": "synthetic", "stdout": <table stdout>}`. Capacity-bearing records add
   `"capacity_unit": "percent"` and others omit it.

   A `contract-evidence` record omits parser fields and contains exactly `record_kind`,
   `evidence_kind`, `documentation_family`, `hmc_release`, `source_url`, `source_section`,
   `support: "unknown"`, non-empty `source_excerpt`, `capacity_unit: "percent"`, and an
   `admitted_claims` string array in the table's order. It never contains synthetic stdout.
4. Extend tests to load decimal examples through `Decimal` and prove two-decimal precision without
   converting to float. Assert the logical-port identity is adapter plus logical-port ID and that
   an empty owner value survives parsing.
5. Run the focused test; expect green. Change one fixture identity field, confirm its test fails,
   restore, and rerun green.
6. Run `just verify`; expect green. Commit:
   `test: pin PCIe capability evidence`.

**Acceptance:** every evidence claim has reproducible IBM provenance; synthetic examples cannot be
mistaken for live capture; identities and units are executable assertions.

## Task 3: Pin the operation matrix and accept ADR 0053

**Files:** extend `tests/system/test_pcie_contract.py`; modify
`docs/adr/0053-evidence-backed-pcie-capability-contract.md`.

**Interfaces**

- Consumes the reviewed spec's exact create/inactive/running/error rows.
- Provides a test-readable matrix assertion and the accepted ADR status.
- No later task may alter mutation code or exports.

1. Add characterization tests that read the already-reviewed spec and require all three operation
   rows, all four state columns, exact `lssyscfg` state/profile reads, exact `chhwres` dynamic
   templates, profile record grammar, stable readback identities, error/no-fallback wording, and
   the explicit disposition of the conflicting existing adapter-mode function to #214. Assert
   LPAR state selection applies only to LPAR-targeted operations, adapter mode uses system-scoped
   inventory/owner preconditions, header-only success is available-empty, and blank success is
   malformed.
2. Add a repository-only structural no-mutation test. When Git metadata or `main` is unavailable,
   skip with `repository-only no-mutation guard requires Git metadata and main`; make no packaging
   claim. Otherwise reject changed or untracked production paths except
   `src/hmc_mcp/ssh_commands.py`; parse that file and `git show main:src/hmc_mcp/ssh_commands.py`
   with `ast`; require the only new top-level definition to be `parse_hmc_delimited_rows`, no new
   imported names because baseline already imports `csv` and `Sequence`, and no removed
   definition/import;
   require the parser's call targets to be exactly the reviewed stdlib/builtin/method allowlist
   (`tuple`, `any`, `len`, `set`, `csv.reader`, `list`, `enumerate`, `dict`, `zip`,
   `field.strip`, `text.splitlines`, `line.strip`, and `rows.append`). This excludes a new mutation
   helper, export, project-call alias, or parser-to-SSH path structurally rather than by substring.
3. Run the focused test; expect green against the reviewed spec. Temporarily add a call to the
   existing SSH executor inside the parser and confirm the AST guard fails, then restore and rerun
   green. Temporarily remove one required matrix token from the actual spec, confirm the
   characterization test fails, restore the spec, and rerun green.
4. Run `just verify`; expect green. Change ADR 0053 Status to `Accepted` and state that the
   evidence, parser/error tests, state-matrix test, no-mutation check, and full guardrail passed on
   2026-08-20.
5. Re-run `just verify`; expect green. Commit:
   `docs: accept evidence-backed PCIe contract`.

**Acceptance:** the durable matrix is mechanically pinned, no mutation surface was added, ADR 0053
is Accepted only after every frozen proof passes, and the full repository guardrail is green.

## Rollback and cleanup

All changes are repository-local and revertible. Reverting removes the parser, fixtures, tests,
spec, and ADR together; no HMC state or persisted application data is touched. Do not remove the
external worktree at handoff; the campaign orchestrator owns post-merge cleanup.
