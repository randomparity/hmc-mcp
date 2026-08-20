# PCIe and SR-IOV capability contract implementation plan

## Goal

Land documentation-backed PCIe/SR-IOV evidence, a presentation-neutral strict CSV parser, and
tests that pin the identities, percentage units, operation matrix, unavailable-capability failure
behavior, and no-mutation boundary defined by ADR 0053 and the design spec.

The parser is one small reusable boundary in `ssh_commands.py`; it does not execute SSH. JSON
evidence records keep IBM source excerpts separate from explicitly synthetic parser examples.
System tests validate the records and the durable operation contract.

**Tech stack:** Python 3.11+, stdlib `csv`/`io`/`json`, pytest, Markdown, `just verify`.

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
2. Run `uv run --no-sync pytest -q tests/system/test_pcie_contract.py`; expect failures because the
   parser is absent.
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
3. Add records for Power8 profile-only slot/logical evidence, Power9 dedicated-slot, adapter,
   physical-port, and logical-port evidence, plus Power10/Power11 mutation/unit evidence records.
   For unknown cells, add metadata-only records with `support: "unknown"`, a source excerpt that
   explains the bounded evidence, and no invented parser output.
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
2. Add a diff-scoped test using `git diff main...HEAD --name-only` and `git diff main...HEAD --`
   when `.git` is available. It must reject changes outside the allowed paths and reject added
   `run_hmc_command`, server/CLI/API exports, or `@tool` metadata. Skip only in source distributions
   without Git metadata; artifact validation separately proves the test and docs are packaged.
3. Run the focused test; expect green against the reviewed spec. Temporarily remove one required
   matrix token in the test's in-memory text, confirm failure, and restore.
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
