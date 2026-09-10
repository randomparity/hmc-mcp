## Problem

Pytest prepend import mode and the repository's `tests` Python path make a second
`conftest.py` shadow the canonical `tests/conftest.py`. Collection then fails far
from the newly added file. The repository needs a pre-collection static failure
that identifies the conflicting path and the one-file invariant.

## Scope

Add a Python checker that asks Git for tracked and unignored untracked files and
rejects each repository-visible `conftest.py` whose path is not exactly
`tests/conftest.py`. Wire it as a focused just recipe, a member of `static`, and
an unrestricted system prek hook. Add its name to the deliberately independent
static-gate inventory.

### Failure model

- Actors and deployments: contributors and CI running repository static checks.
- Invariants and assets at stake: only `tests/conftest.py` may provide pytest
  configuration; diagnostics identify each repository-visible conflicting path.
- Accepted failure classes: ignored environment files are outside the committed
  repository layout and therefore outside the scan.
- Covered elsewhere: pytest fixture and application behavior remain owned by the
  existing test suite; bare-import replacement remains future test-layout work.

## Success

The checker exits zero for the current canonical layout. It exits nonzero for a
second root-level or nested `conftest.py`, reports each offending path, and states
that `tests/conftest.py` is the only permitted location. The `static` recipe and
prek correspondence keep the guard on local and CI paths.

## Validation

- Contract: canonical layout. Mode: focused-test; case:
  `test_accepts_only_canonical_conftest`; expected red: checker module absent;
  green: `uv run --no-sync pytest tests/scripts/test_check_test_layout.py -q --no-cov`.
- Contract: nested and second conftest rejection with actionable diagnostics.
  Mode: focused-test; cases: `test_rejects_nested_conftest` and
  `test_rejects_second_root_conftest`; expected red: checker module absent;
  green: `uv run --no-sync pytest tests/scripts/test_check_test_layout.py -q --no-cov`.
- Contract: static, prek, and inventory wiring. Mode: focused-test; case:
  `test_prek_hooks_delegate_to_focused_just_recipes`; expected red: new gate is
  absent; green: `uv run --no-sync pytest tests/test_ci_pipeline.py -q --no-cov`.
