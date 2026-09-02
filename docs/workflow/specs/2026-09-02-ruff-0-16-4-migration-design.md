# Ruff 0.16.4 migration — design

Issue: [#597](https://github.com/randomparity/hmc-mcp/issues/597).
Decision record: [ADR 0114](../../adr/0114-ruff-default-rule-set-adoption.md).

## Goal

Move the pinned lint tool from `ruff==0.15.22` to `ruff==0.16.4` with a clean `just lint`
gate, no broad suppression of meaningful diagnostics, and no loss of the lint coverage the
repository enforces today.

## What actually changed upstream

Ruff 0.16.0 replaced its default rule set rather than adding diagnostics to it. Enabled
rules go from 59 to 413, and eighteen previously-default `E` and `F` rules are removed.
The repository carries no `[tool.ruff]` table, so it inherits that change wholesale.

Measured at merge base `3bf3f892` (identical to the count #597 reports at its own,
earlier base):

| | count |
|---|---|
| findings under 0.16.4 defaults | 712 |
| of those, safely auto-fixable | 335 |
| findings under 0.15.22 defaults | 0 |
| `E402` findings hidden by existing `# noqa` | 10 |

## Requirements

- **R1.** `pyproject.toml`'s `dev` group pins `ruff==0.16.4`, and `TOOL_PINS` in
  `tests/test_ci_pipeline.py` carries the same string. `tests/test_supply_chain.py`
  requires the pin to equal the locked version, so `uv.lock` is regenerated in the same
  change.
- **R2.** The eighteen rules 0.16 dropped from defaults are restored through
  `extend-select`. This is required, not merely desirable: ten `# noqa: E402` directives
  already in `tests/` become `RUF100` findings without it.
- **R3.** `B008` is narrowed with `flake8-bugbear.extend-immutable-calls` naming
  `typer.Option`, `typer.Argument`, `hmc_mcp.documents.LparResources`, and
  `hmc_mcp.operations.lpar.assignments.LparPcieAssignments`. The rule stays enabled.
- **R4.** `PLE2502` is silenced by `per-file-ignores` in exactly
  `tests/unit/test_audit.py` and `tests/unit/test_ownership.py`, whose subject is hostile
  bidirectional text.
- **R5.** No other rule is configured. No blanket `ignore`, no `exclude`, no bare `# noqa`
  without a code. Every remaining finding is fixed in code.
- **R6.** Safe mechanical fixes land as their own commit, separate from reviewed fixes, so
  the reviewed work is legible on its own.
- **R7.** `just verify` and `uv run --no-sync prek run --all-files` are green on the final
  tree, and the eight-leg CI matrix passes.
- **R8.** Behaviour is preserved. A lint fix that would change what a test asserts, what a
  tool returns, or what an error type is, is not applied as a lint fix; either the code is
  corrected deliberately or the finding is escalated.

## Configuration surface

The whole of it, in `pyproject.toml`:

```toml
[tool.ruff.lint]
extend-select = [
    "E401", "E402", "E701", "E702", "E703", "E711", "E712", "E713", "E714",
    "E721", "E731", "E741", "E742", "E743", "F403", "F405", "F406", "F722",
]

[tool.ruff.lint.flake8-bugbear]
extend-immutable-calls = [
    "typer.Option",
    "typer.Argument",
    "hmc_mcp.documents.LparResources",
    "hmc_mcp.operations.lpar.assignments.LparPcieAssignments",
]

[tool.ruff.lint.per-file-ignores]
"tests/unit/test_audit.py" = ["PLE2502"]
"tests/unit/test_ownership.py" = ["PLE2502"]
```

Each entry carries a comment in the file naming why the repository's intent differs, per
ADR 0114.

Under that configuration the finding count is **649**, of which **325** are safely
auto-fixable, leaving roughly **324** for reviewed fixes.

## Rule-family dispositions

Every family is fixed in code unless the table says otherwise. Counts are under the
configuration above.

| Rule | n | Disposition |
|---|---:|---|
| `I001` unsorted-imports | 206 | Safe autofix. |
| `PLC0414` useless-import-alias | 138 | Unsafe autofix, then verify. `import x as x` marks a PEP 484 re-export; every site is checked for whether the name is used in-module before the alias is dropped, because dropping it where the name is unused re-arms `F401`. |
| `SIM117` multiple-with-statements | 93 | Display-only fix; applied by hand or with `--unsafe-fixes`, then verified. Purely structural. |
| `FURB157` verbose-decimal-constructor | 31 | Safe autofix. |
| `BLE001` blind-except | 21 | Reviewed per site. `except Exception` is legitimate where the handler re-raises with context or where totality is the contract (the audit sink); each retained site gets a coded `# noqa: BLE001` with a reason, and the rest narrow the caught type. |
| `ISC004` implicit-string-concat-in-collection | 20 | Add the parentheses Ruff asks for. Each site is read to confirm a missing comma is not the actual defect. |
| `UP032`, `UP017`, `UP037`, `UP035`, `UP041`, `UP012`, `UP033` | 45 | Safe autofix (modernisation). |
| `RUF100` unused-noqa | 4 after R2 | Three name rules that are not enabled (`S603` ×2, `PLC0415`); one (`BLE001`) whose site now re-raises. Directive removed, rationale kept as a plain comment. |
| `TRY004` type-check-without-type-error | 12 | Fixed: an `isinstance` guard that fails raises `TypeError`. Public error contracts are checked first; a site whose `ValueError` is asserted by a test is escalated under R8. |
| `PLW1510` subprocess-run-without-check | 9 | Fixed with an explicit `check=False`, which states the existing behaviour rather than changing it. |
| `FURB192` sorted-min-max | 8 | Fixed: `sorted(x)[0]` → `min(x)`. |
| `DTZ011`, `DTZ001` | 8 | Fixed: naive `date.today()` / `datetime(...)` gain explicit UTC. `src/hmc_mcp/ssh/lpar.py` stamps ownership dates, so the fix is checked against the stamp format its tests assert. |
| `RUF059` unused-unpacked-variable | 7 | Fixed by renaming the unused binding to `_`-prefixed. |
| `EXE001` shebang-not-executable | 6 | Shebang removed from the six `scripts/` files that carry one; four already do not, and all ten are run as `uv run --no-sync python scripts/<name>.py`. |
| `PYI061`, `PYI034` | 7 | Safe autofix: `Literal[None]` → `None`, `__aenter__` returns `Self`. |
| `SIM102` collapsible-if | 4 | Fixed by collapsing. |
| `S110` try-except-pass | 3 | Reviewed. Retained sites get a coded `# noqa: S110` naming why swallowing is the contract. |
| `G201` logging-exc-info | 3 | Fixed: `logger.error(msg, exc_info=True)` → `logger.exception(msg)`, which emits identical output, so the assertions in `tests/unit/test_audit.py` are unaffected. |
| `TRY002` raise-vanilla-class | 2 | Fixed: test fault injection raises `RuntimeError`. `B017`'s `pytest.raises(Exception)` at the matching site narrows with it. |
| Remaining singletons and pairs (`C408`, `FLY002`, `FURB162`, `PIE808`, `PIE810`, `PLR0402`, `RUF015`, `RUF022`, `B009`, `B017`, `FURB167`, `PLC0206`, `PLR1711`, `RET501`, `SIM118`) | 22 | Fixed individually; each is a local rewrite with no contract surface. |

## Threat model

The change is security-relevant on one trigger only: it changes a pinned development
dependency and the lockfile. It adds no entry point, touches no authentication or
authorization logic, handles no secret, and parses no untrusted input.

- **Boundary added:** none.
- **Boundary widened:** none. The one boundary in play is the supply chain — the
  `ruff==0.16.4` artifact resolved into `uv.lock`.
- **Actor:** whoever can publish to PyPI under the `ruff` name, plus anyone able to alter
  `uv.lock` in review.
- **Control:** the exact pin (`tests/test_supply_chain.py` requires pin equality with the
  lock), the hashes `uv.lock` records, and CI's `uv sync --locked`, which refuses a lock
  that does not match the manifest. Ruff runs only in CI and on developer workstations; it
  is not a runtime dependency and ships in no artifact.
- **Widened by the diff, indirectly:** `extend-immutable-calls` tells `B008` that four
  named constructors are safe as defaults. If either `hmc_mcp` dataclass later loses
  `frozen=True`, the rule stops guarding those ten call sites. That is a maintenance
  hazard, recorded in ADR 0114's consequences, not a reachable attack.
- **Out of scope:** the `S603` and `PLC0415` rule families, which this change explicitly
  declines to enable (24 and 238 findings respectively); and any behavioural review of
  code the lint fixes touch beyond R8's preservation requirement.

No new control is warranted: the existing pin-equality test and `uv sync --locked` already
govern the only boundary this change moves.

## Testing

The lint gate is itself the primary test. Beyond it:

- `tests/test_supply_chain.py` proves the pin equals the lock.
- `tests/test_ci_pipeline.py::TOOL_PINS` proves the declared pin matches.
- `tests/test_ci_pipeline.py::test_dirty_project_commands_do_not_rebuild_editable_metadata`
  copies the tracked tree and asserts `just lint` passes there — this is what makes
  `EXE001` a real constraint, since the fix has to survive a `shutil.copy2` of tracked
  files.
- `just verify` (static, test, smoke, build, verify-artifacts, CLI-group load) and
  `uv run --no-sync prek run --all-files` prove no fix changed behaviour.
- The eight-leg CI matrix ({amd64, arm64} × py{3.11–3.14}) is the arbiter; a locally green
  run does not predict it.

No new test is added. The change asserts no new behaviour; it preserves existing behaviour
under a stricter linter, and the existing suite is what proves that.
