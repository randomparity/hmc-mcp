# Bare-CEC LPAR recipe

## Problem

Issue #877 (epic #871 req. 6; no ADR). No operator recipe brings an LPAR up on dedicated
physical I/O without a VIOS, shared storage or virtual network. The #879 window executes one verbatim, so it must exist, follow the
bare-cec arm (#876, `scripts/live_test/bare_cec.py`), and name only installed commands.

## Scope

`docs/recipes/bare-cec-lpar.md`, an unverified banner citing #879, then: inventory
(`network list-dedicated-pcie-slots`) → `lpars create` with explicit processing units
(#938) → `network assign-dedicated-pcie-slot` into `default_profile` → profile UUID from
`lpars show --json` → `lpars power-on --partition-profile --boot-mode sms --wait` →
`jobs show` and `lpars state` → `lpars refcodes` → console → `lpars power-off --immediate
--wait` → `network unassign-dedicated-pcie-slot` → `lpars delete` → readback that the name
is gone and the slot unowned. Each step states its expected output.

Two steps have no installed command on `main`, and the recipe says so instead of naming one:
applying the profile without activating (#939; activation against the profile is the recipe's
path) and console capture (its CLI form is in unmerged PR #777; the MCP tool
`hmc_capture_lpar_console` exists).

`tests/app/test_bare_cec_recipe.py` copies PR #777's parser: every `hmcpctl` line in a
`bash` block is shlex-split, placeholders are substituted, and the command path is resolved
through the Click tree with `make_context`, so an unknown group, command, option, or missing
argument fails. The resolved set must equal an explicit expected set. A second test requires
exactly one link from `docs/index.md` and one from `docs/cli.md`. `CHANGELOG.md` gains one
entry. No `src/` change.

### Failure model

1. **Actors and deployments**: an operator at a terminal with HMC reach; CI running the test.
2. **Invariants and assets at stake**
   - No `hmcpctl` line in a `bash` block names a command, option or argument the CLI lacks.
   - The unassign and delete steps each follow a readback confirming the partition identity.
3. **Accepted failure classes**
   - Expected output may be wrong on hardware: the banner marks it unverified; #879 corrects it.
   - The test proves command shape, not option values or HMC behaviour.
   - `hmcpctl` commands outside `bash` blocks are unchecked; the recipe puts none there.
4. **Covered elsewhere**: live execution #879; PR #777 refresh, operator; recipe
   cross-links #782; profile apply #939.

## Success

1. The recipe covers the Scope sequence, each step with expected output, marked unverified
   until #879 executes it.
2. Every `hmcpctl` command in its `bash` blocks parses against the installed CLI, and the
   parsed set equals the test's expected set.
3. The profile-apply and console gaps cite #939 and PR #777 and name no command for them.
4. `docs/index.md` and `docs/cli.md` each link it once; `CHANGELOG.md` records it.

## Validation

- `focused-test` (criteria 2, 4): `tests/app/test_bare_cec_recipe.py`. Red first with no
  recipe; a bogus option in one recipe line must fail it. Green:
  `uv run --no-sync pytest tests/app/test_bare_cec_recipe.py -q --no-cov`.
- `task-test-not-applicable` (criteria 1, 3): recipe prose — expected outputs, gap
  statements and banner are read by operators; no executable consumer validates prose.
