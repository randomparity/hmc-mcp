# LPAR ownership inventory implementation

Goal: make ADR 0092 classification exhaustive. The test parses selected
operation modules, compares public async operations with a classification map,
and verifies guarded sources reference existing ownership helpers.

## Global Constraints

Python 3.11+; no new dependencies; preserve existing authorization behavior.

Expected implementation size: 80–140 changed lines (M) — one test inventory and ADR link.

## Task 1

Modify `tests/unit/test_adr_0092_citations.py` and ADR 0092. Add the module
inventory, classifications, and AST reachability assertions. Verify with
`uv run --no-sync pytest tests/unit/test_adr_0092_citations.py -q --no-cov`;
expect the test to fail when a fixture operation is unclassified, then pass.

## Task 2

Run `just test` and `just verify`; expect success. Commit the inventory and
design records together.
