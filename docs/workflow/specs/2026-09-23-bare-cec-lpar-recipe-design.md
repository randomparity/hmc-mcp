# Bare-CEC LPAR recipe

## Problem

Issue #877 (epic #871 req. 6; no ADR). No recipe brings an LPAR up on dedicated physical I/O
without a VIOS, shared storage or virtual network. The #879 window runs one verbatim, so it
must follow the bare-cec arm (#876) and name only installed commands.

## Scope

`docs/recipes/bare-cec-lpar.md`: unverified banner citing #879; prerequisites
`HMC_AUTHORIZE_POWER_OPERATIONS=true` and `--system` on every power command, as the arm
requires; every value a placeholder, with the system-inventory recipe's sensitive-data note.
Sequence: `network list-dedicated-pcie-slots` → `lpars create` with explicit processing units
(#938) and `--caller-token` → `network assign-dedicated-pcie-slot` into `default_profile` →
`lpars show` (the operator copies the UUID ending `AssociatedPartitionProfile`'s href) →
`lpars power-on --partition-profile --boot-mode sms --wait` → `jobs show`, `lpars state` →
`lpars refcodes` → console → `lpars power-off --immediate --wait` → unassign → delete →
readback that the name is gone and the slot unowned. Each step states expected output. Before
unassign and before delete, `lpars get-description` must show the run's caller token.

No installed command applies a profile without activating (#939; activation is the recipe's
path) or captures the console (CLI form in unmerged PR #777; MCP tool
`hmc_capture_lpar_console` exists). The recipe states both gaps and names no command.

`tests/app/test_bare_cec_recipe.py` copies PR #777's parser: `bash`-block logical lines
starting `hmcpctl` are shlex-split, placeholders substituted, and resolved through the Click
tree with `make_context`; the resolved set must equal an expected set. It also asserts: every
`bash` line containing `hmcpctl` starts with it (no pipes or substitutions); no command passes
`--ownership-override`; each unassign and delete is directly preceded by `lpars
get-description`; `docs/index.md` and `docs/cli.md` link the recipe once each. One
`CHANGELOG.md` entry. No `src/` change.

### Failure model

1. **Actors and deployments**: an operator at a terminal with HMC reach; CI running the test.
2. **Invariants and assets at stake**
   - No `bash`-block `hmcpctl` command names a command, option or argument the CLI lacks.
   - Unassign and delete run only after the caller-token readback.
   - The public recipe carries no real identifier.
3. **Accepted failure classes**
   - Expected output may be wrong on hardware: marked unverified; #879 corrects it.
   - The test proves command shape, not option values or HMC behaviour.
4. **Covered elsewhere**: live run #879; PR #777, operator; cross-links #782; apply #939.

## Success

1. The recipe covers the Scope sequence and prerequisites, with expected output, unverified.
2. Each `bash`-block `hmcpctl` command parses against the installed CLI; the set is exact.
3. Both gaps cite #939 and PR #777 and name no command.
4. `docs/index.md` and `docs/cli.md` each link it once; `CHANGELOG.md` records it.

## Validation

- `focused-test` (2, 4, readback order): `tests/app/test_bare_cec_recipe.py`; red with no
  recipe, and with a bogus option or a `$(hmcpctl bogus)` line injected. Green:
  `uv run --no-sync pytest tests/app/test_bare_cec_recipe.py -q --no-cov`.
- `task-test-not-applicable` (1, 3, placeholders): prose read by operators; no executable
  consumer validates it.
