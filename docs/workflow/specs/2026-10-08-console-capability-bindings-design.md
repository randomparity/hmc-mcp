# Console capability bindings (#1411 PR2)

## Problem

The console and generic operations currently bind unrelated CLI rows. The
ManagementConsole source units exist but are classified as non-operation.

## Scope

Under #1411 and the operator's 2026-10-07 Add ManagementConsole row decision,
add `rest:management-console` from the six existing POWER10/POWER11
`management-console` units. Resource units are modes; the capability note is
included in source units; parameters are empty. Reassign those units' accounting
to the row without changing their IDs, hashes, text or topic classification.
Use supported disposition because `console.info` implements the documented feed.
No ownership transition or additional generator is needed.

Bind `console.info` only to that row. The generic-binding rule is to explain
caller-selected operations rather than assigning an arbitrary fixed row set:
`console.list_resources` lists the caller's root UOM resource type;
`command.run` executes the caller's CLI command over SSH. Both have empty
`row_ids` and truthful `composite_reason`. Drop their placeholder joins while
preserving existing rows and coverage-child dispositions. Update the README's
row total and regenerate projections/documentation if their recipes change them.
Authority is the frozen PR2 WORK:SCOPE and campaign dispatch; ADR 0125 already
permits rows or explained composites, so no additional architectural decision.

### Failure model

Actors are offline catalog maintainers and registry/documentation consumers on
the supported Python versions. Preserve unit accounting and reference identity.
Missing/double units, invalid joins and fixed bindings for generic dispatchers
are failures tested here. Live HMC behavior is outside this metadata change;
existing row owners retain coverage work. Snapshot bindings belong to #1378;
lpar.modify to #1386; migration/remote_restart to PR3 after #1418.

## Success

One ManagementConsole row represents both snapshots and is the sole
`console.info` binding. Both generic operations have no fixed rows and explain
why. Existing coverage-child dispositions survive. Inventory and guardrails
pass. PR2 says Part of #1411; only PR3 may close the issue.

## Validation

- Mode: focused-test. `tests/scripts/test_check_capability_inventory.py` checks
  the ManagementConsole row, six accounting joins, modes, releases and supported
  disposition. Red: row missing. Green command: `uv run --no-sync pytest
  tests/scripts/test_check_capability_inventory.py -q --no-cov`.
- Mode: focused-test. The same module checks the three exact operation joins
  and nonempty generic explanations. Red: placeholder joins remain. Same command.
- Mode: task-test-not-applicable. README count and this specification are prose
  with no executable consumer asserting their wording; review their factual
  correspondence to the catalog. Run `just capability-metadata`,
  `just tool-docs-check`, `just doc-freshness`, `just verify`, and
  `uv run --no-sync prek run --all-files`; no live contact.
