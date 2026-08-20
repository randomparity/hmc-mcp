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
- Provides evidence records with keys `evidence_kind`, `documentation_family`, `hmc_release`,
  `source_url`, `source_section`, `command`, `fields`, `source_excerpt`, `parser_examples`, and
  optional `capacity_unit`.
- Task 3 relies on these tests proving every documented/unknown matrix cell and state contract.

1. Add failing parameterized tests that enumerate the exact expected record names and require:
   allowed documentation families; `hmc_release` exact or `not-established`; IBM HTTPS source;
   read-only command; exact fields; non-empty source excerpt; synthetic example label; parser
   success; stable identity fields; and `percent` for all capacity-bearing records.
2. Run the focused test; expect missing-fixture failures.
3. Add exactly these records (source text drift is a stop condition: if the named section no
   longer contains the excerpt, do not substitute another claim without returning to design):

| File | Kind | URL/section | Command/fields or admitted claim |
|---|---|---|---|
| `power8-profile.json` | `read-fixture` | `https://www.ibm.com/docs/en/power8/8284-22A?topic=commands-lssyscfg`, profile examples | `lssyscfg -r prof -m sys1 -F io_slots,sriov_eth_logical_ports --header`; those two fields |
| `power9-io-slot.json` | `read-fixture` | ADR 0053 Power9 `lshwres`, physical-I/O example | `lshwres -r io --rsubtype slot -m sys1 -F drc_index,description,lpar_name --header` |
| `power9-sriov-adapter.json` | `read-fixture` | same URL, SR-IOV synopsis/filters | adapter command; `adapter_id,slot_id,config_state` |
| `power9-sriov-physport.json` | `read-fixture` | same URL, SR-IOV synopsis/filters | physical-port `--level eth`; fields from the spec |
| `power9-sriov-logport.json` | `read-fixture` | same URL, SR-IOV synopsis/filters | logical-port `--level eth`; fields from the spec |
| `power10-sriov-contract.json` | `contract-evidence` | ADR 0053 Power10 `chhwres`, SR-IOV attributes/examples | adapter/logical-port mutation templates; percentage and granularity claims; read support `unknown` |
| `power11-sriov-contract.json` | `contract-evidence` | ADR 0053 Power11 `chhwres`, SR-IOV attributes/examples | same admitted current contract; read support `unknown` |

   A `read-fixture` payload is exactly:

```json
{
  "record_kind": "read-fixture",
  "evidence_kind": "documentation",
  "documentation_family": "Power9",
  "hmc_release": "not-established",
  "source_url": "https://www.ibm.com/docs/en/power9/0000-REF?topic=POWER9_REF%2Fp9edm%2Flshwres.htm",
  "source_section": "EXAMPLES — physical I/O slots",
  "support": "documented",
  "command": "lshwres -r io --rsubtype slot -m sys1 -F drc_index,description,lpar_name --header",
  "fields": ["drc_index", "description", "lpar_name"],
  "source_excerpt": "List the DRC index, description, and owning partition for each physical I/O slot",
  "parser_examples": {
    "kind": "synthetic",
    "stdout": "drc_index,description,lpar_name\n21010003,PCIe slot,lpar1\n21010004,PCIe slot,\n"
  }
}
```

   The other `read-fixture` records use the exact matrix fields and synthetic values `adapter_id=1`,
   `slot_id=21010202`, `config_state=1`, `phys_port_id=0`, `state=1`,
   `phys_port_loc=U78D5-P1-C1-T1`, `min_eth_capacity_granularity=0.25`,
   `logical_port_id=27004001`, `lpar_id=7`, `lpar_name=lpar1`, `capacity=10.25`, and
   `max_capacity=20.50` where applicable. Empty-owner variants set both owner columns empty.
   Capacity-bearing records add `"capacity_unit": "percent"`.

   A `contract-evidence` record omits parser fields and contains exactly `record_kind`,
   `evidence_kind`, `documentation_family`, `hmc_release`, `source_url`, `source_section`,
   `support: "unknown"`, non-empty `source_excerpt`, `capacity_unit: "percent"`, and an
   `admitted_claims` string array copied from ADR 0053. It never contains synthetic stdout.
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

1. Add failing tests that read the spec and require all three operation rows, all four state
   columns, exact `lssyscfg` state/profile reads, exact `chhwres` dynamic templates, profile record
   grammar, stable readback identities, error/no-fallback wording, and the explicit disposition of
   the conflicting existing adapter-mode function to #214.
2. Add a repository-only diff-scoped test. When `git rev-parse --show-toplevel` fails, skip with
   `repository-only no-mutation guard requires Git metadata`; make no packaging claim. Otherwise
   inspect `git diff main -- src/hmc_mcp` (committed, staged, and unstaged tracked source together)
   plus `git ls-files --others --exclude-standard -- src/hmc_mcp`. Reject any changed production
   path other than `src/hmc_mcp/ssh_commands.py`, any untracked production file, or added line
   containing `run_hmc_command`, `@tool`, `server`, `cli`, or `api` export syntax. Limit token
   scanning to added lines outside the parser function so the pre-existing baseline is ignored.
3. Run the focused test; expect green against the reviewed spec. Add a temporary comment containing
   `run_hmc_command` inside the new parser, run the exact guard and confirm failure, remove it, then
   rerun green. Temporarily remove one required matrix token from the test's in-memory text,
   confirm failure, and restore.
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
