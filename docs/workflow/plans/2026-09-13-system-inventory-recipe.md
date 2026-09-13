# System inventory recipe implementation plan

**Goal:** document a standalone, read-only managed-system capture workflow.

**Architecture:** one recipe owns command sequencing and capture naming; one
structural test owns the documented contract. Existing CLI commands remain the
only HMC interface. No dependency or runtime code is added.

**Tech stack:** Markdown, Python pytest, existing `hmc-mcp` CLI.

## Global constraints

- Use only existing read commands and `raw get`; never use raw POST or mutations.
- Captures are sensitive local operational data and use generic placeholders.
- Run focused pytest, then `just verify` and `uv run --no-sync prek run --all-files`.

Expected implementation size: 180–260 changed lines (M) — one recipe, index link, and focused structural test.

## Task 1: Add the read-only recipe

**Files:** create `docs/recipes/system-inventory.md`.

**Interfaces:** consumes existing `hmc-mcp` CLI read commands; produces local JSON,
raw XML, and error records for an operator.

**Verification:**

- Contract: all required categories and worksheet fields are present. Mode:
  focused-test; expected red is a missing required marker in the recipe test;
  focused green command: `uv run --no-sync pytest tests/test_system_inventory_recipe.py -q`.
- Contract: the recipe contains no mutation form. Mode: focused-test; expected
  red is a forbidden command marker; focused green command: same command.

1. Write the capture setup, UUID resolution, stable JSON captures, labelled raw
   XML captures, error-recording helper, and operator worksheet.
2. Confirm the focused test passes after Task 2 adds it.

**Acceptance:** every command is read-only and unavailable categories are recorded.

## Task 2: Index and structural contract

**Files:** modify `docs/index.md`; create `tests/test_system_inventory_recipe.py`.

**Interfaces:** consumes the recipe and index text; protects their required
discoverability and safety markers.

**Verification:**

- Contract: index links the recipe and the recipe's structural contract remains
  intact. Mode: focused-test; expected red is an assertion failure after a
  required marker is removed; focused green command:
  `uv run --no-sync pytest tests/test_system_inventory_recipe.py -q`.

1. Add a recipe link to the documentation index.
2. Add a focused test that asserts required categories, worksheet fields,
   sensitive-data guidance, stable-versus-raw distinction, and forbidden forms.
3. Run the focused test, `just verify`, and the configured prek command.

**Acceptance:** the recipe is discoverable and automated checks reject a safety regression.
