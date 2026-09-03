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
  without a code. Every remaining finding is either fixed in code or carries a **coded,
  per-site `# noqa: <CODE> - <reason>`** justified in the disposition table below. A site
  earns the directive only where fixing it would change a contract this issue does not own
  — an exported error type, persisted metadata, a framework's validator protocol, a
  cancellation boundary — never because the fix is tedious. Every such site is named in the
  table: 18 sites are named exactly in advance — `TRY004` 7, `DTZ011` 7, `PLC0414` 4 — and
  the retained subsets of `BLE001` and `S110` are settled by reading each site during the
  reviewed pass, since which of those 24 sites re-raise with context is not decidable from
  the counts alone.
- **R6.** Safe mechanical fixes land as their own commit, separate from reviewed fixes, so
  the reviewed work is legible on its own.
- **R7.** `just verify` and `uv run --no-sync prek run --all-files` are green on the final
  tree. CI is the arbiter and must pass, and "CI" means **all five jobs** in
  `.github/workflows/ci.yml`, not only the one whose name is `ci`: `ci` (the {amd64, arm64}
  × py{3.11–3.14} verify matrix, 8 legs), `library-wheel-smoke`, `library-range-floors`,
  `wheel-smoke` (its own architecture × version matrix), and `python-support-drift`. Two of
  the four unnamed ones are touched by this diff — `python-support-drift` runs
  `scripts/check_python_support.py`, which loses a shebang and holds four `TRY004` sites,
  and `library-range-floors` installs at the declared dependency floors, where a `UP035` or
  `UP017` modernisation would surface if it assumed a newer floor than `pyproject.toml`
  declares. A green `ci` matrix alone must not be read as "CI passed". No task in the
  implementation plan can discharge this: the plan ends at a proven local tree, and pushing
  the branch, opening the pull request, and reading the jobs belong to the shipping phase
  that follows. R7's CI arm is owned there, not by the plan.
- **R8.** Behaviour is preserved. A lint fix that would change what a test asserts, what a
  tool returns, or what an error type is, is not applied as a lint fix; either the code is
  corrected deliberately or the finding is **escalated**, which means exactly this and
  nothing looser: leave the code unchanged, stop the task rather than editing the
  assertion to fit the fix, and write the site into the plan's *Deferrals* section with
  its `file:line`, the asserting test, and the contract in question. An unattended run has
  no one to escalate to, so the written record is the escalation; the run then stops for a
  human with `just lint` still red on that family, and says so. Adding a coded
  `# noqa: <CODE> - <reason>` is permitted only where this design's disposition table
  already allows one for that family (`BLE001`, `S110`, `TRY004`, `DTZ011`, `PLC0414`).

  **One exemption**, because R8 would otherwise forbid a fix the table already assigns: an
  assertion may move together with the raise when both are the *same test's own fault
  injection* and no production contract sits on either side. That is the `TRY002` /`B017`
  pair at `tests/storage/test_upload_iso.py:864` and `:877` — the test raises a bare
  `Exception` to simulate a network failure and catches it with
  `pytest.raises(Exception)`; narrowing both to `RuntimeError` together changes nothing the
  test is for.

  **R8 has a known blind spot, and it is not a safety net.** Its trigger is "a test asserts
  otherwise", so it cannot see a contract change where the test computes its expected value
  the same way production does. `DTZ011` is exactly that case — the six test sites build the
  expected ownership stamp with the same `date.today()` call the source uses, so changing
  both keeps the suite green while the persisted metadata moves. That family is therefore
  dispositioned by reading, not by relying on R8 to fire.

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
auto-fixable.

**649 is a pre-fix census, not a work inventory.** Ruff's fixer iterates, so applying the
safe fixes exposes diagnostics the census never counted: `ruff check . --fix` on this tree
reports `Found 666 errors (341 fixed, 325 remaining)`, and among the 325 is one `PLR0133`
that no census row predicted. Every count below is therefore a starting figure. The
implementation re-runs `--statistics` after the mechanical pass and treats any rule code
absent from the table below as work that must be dispositioned before the reviewed pass
begins.

The cascade is not confined to the mechanical pass. Any later `--fix` run cascades the
same way, and the measured instance is `I001`: rewriting `X as X` to `X` changes the sort
keys isort uses, so the `PLC0414` pass re-introduces about 22 `I001` findings in a family
the mechanical pass had cleared. Every fix pass is therefore followed by a re-run of
`--fix` and `--statistics`, and the churn is committed with the change that caused it.

## Rule-family dispositions

Every family is fixed in code unless the table says otherwise. Counts are the pre-fix
census under the configuration above, and sum to 649; `PLR0133` is listed separately
because the fixer's cascade surfaces it, so the table's own total is 650.

| Rule | n | Disposition |
|---|---:|---|
| `I001` unsorted-imports | 206 | Safe autofix. |
| `PLC0414` useless-import-alias | 138 | Split. The 134 sites in `tests/` take the unsafe autofix: each name is used in its own module, so dropping the alias re-arms nothing. The 4 sites in `src/hmc_mcp/cli.py` are genuine PEP 484 re-exports with no in-module use — measured: after the mechanical fix, `--select F401` reports exactly those four — and keep the `X as X` form under a coded `# noqa: PLC0414 - PEP 484 explicit re-export; see the module docstring`. An `__all__` was considered and rejected: in this repository `__all__` is not a neutral idiom — ADR 0029 makes `hmc_mcp.api.__all__` an exhaustive compatibility manifest that `tests/unit/test_public_api.py` parses and `CHANGELOG.md` tracks, so a second one in `hmc_mcp.cli` would invite a reader to treat that module as supported surface. `F401` is never `--fix`ed. |
| `SIM117` multiple-with-statements | 93 | Only 30 are fixable, and those 30 carry **safe** fixes — measured: `--select SIM117 --fix` without `--unsafe-fixes` reports `Found 93 errors (30 fixed, 63 remaining)`, identical to the `--unsafe-fixes` run. So the mechanical pass consumes them and the reviewed pass inherits 63, which have no fix at any safety level. Those 63 — 92 of the 93 are in `tests/` — are hand restructurings of nested `with` statements, enumerated and committed in file-sized batches. This is the largest hand-edit block in the change. |
| `FURB157` verbose-decimal-constructor | 31 | Safe autofix. |
| `BLE001` blind-except | 21 | Reviewed per site. `except Exception` is legitimate where the handler re-raises with context or where totality is the contract (the audit sink); each retained site gets a coded `# noqa: BLE001` with a reason, and the rest narrow the caught type. Two sites are **retention sites, never narrowing candidates**: `src/hmc_mcp/client/core.py:331` and `:336` catch `BaseException` deliberately, and the docstring above them states the contract — a failing logoff or close is attached to the in-flight exception with `add_note` and never replaces it. Narrowing either to `except Exception` would stop handling `asyncio.CancelledError` and `KeyboardInterrupt`, changing cancellation semantics, and no gate would catch it. |
| `ISC004` implicit-string-concat-in-collection | 20 | Add the parentheses Ruff asks for. Each site is read to confirm a missing comma is not the actual defect. |
| `UP032`, `UP017`, `UP037`, `UP035`, `UP041`, `UP012`, `UP033` | 45 | Safe autofix (modernisation). |
| `RUF100` unused-noqa | 4 after R2 | Three name rules that are not enabled (`S603` ×2, `PLC0415`); one (`BLE001`) whose site now re-raises. Directive removed, rationale kept as a plain comment. |
| `TRY004` type-check-without-type-error | 12 | **All 12 retained** with a coded, per-site `# noqa: TRY004 - <reason>`. The split was first written as 5 fixed / 7 retained, on the assumption that the `scripts/` and `tests/` sites carried no contract; checking them rather than inferring showed otherwise, and the correction is recorded here because the inference is the reusable mistake. `tests/scripts/test_check_python_support.py:55-69` parametrizes `pytest.raises(ValueError, match=…)` over messages that pin **all four** `scripts/check_python_support.py` sites, and `tests/test_live_runner.py:630` asserts `pytest.raises(AssertionError, match="cannot read")` against the guard at `:593`. The seven `src/` sites were verified separately: `authorization/access_policy.py:123,139` (inside a Pydantic `@field_validator`, which collects `ValueError` into `ValidationError` and lets `TypeError` escape uncaught — and the code's own comment says one message covers wrong-string *and* wrong-type); `snapshots/operations.py:210,224,234` (reached from `capture_lpar_snapshot`, `inspect_lpar_snapshot`, and `validate_lpar_snapshot`, all in `hmc_mcp.api.__all__`, which ADR 0029 freezes; `tests/unit/test_snapshot_capture.py:220,233` assert the `ValueError`; and rejecting `bool` is a value-domain decision, since `bool` *is* an `int`); `ssh/lpar.py:29` (`validate_caller_token`'s `ValueError` is what `ssh/lpar.py:102`'s best-effort `(HMCCLIError, OSError, ValueError)` boundary depends on, asserted at `tests/unit/test_ownership.py:752`); and `cli_commands/serve.py:53` (the `isinstance` is on the stdlib's return value, not a caller's argument, so `RuntimeError` states an internal invariant correctly). |
| `PLW1510` subprocess-run-without-check | 9 | Fixed with an explicit `check=False`, which states the existing behaviour rather than changing it. |
| `FURB192` sorted-min-max | 8 | Fixed: `sorted(x)[0]` → `min(x)`. |
| `DTZ011` call-date-today | 7 | **All seven retained** with a coded `# noqa: DTZ011 - <reason>`. `src/hmc_mcp/ssh/lpar.py:85` builds the ADR 0011 ownership stamp `[hmc-mcp owner:… created:<date>]`, which is persisted on the HMC: moving it from the operator's local calendar date to UTC is an externally visible metadata change that outlives the commit, and it belongs to ADR 0011 rather than to a lint migration. The six sites in `tests/unit/test_ownership.py` (125, 161, 758, 777, 1020, 1086) compute the *expected* stamp with the same call, so they must stay in lockstep — fixing them alone would make them disagree with production for part of every day on any non-UTC host, which is the same defect inverted. Both sides move together or neither does. |
| `DTZ001` call-datetime-without-tzinfo | 1 | **Retained** with a coded `# noqa: DTZ001 - naiveness is the subject`. First written as "gains explicit UTC, nothing persists it", which is the same infer-instead-of-check mistake the `TRY004` row records: the naive `datetime(2026, 8, 24)` in `tests/lpar/test_provision_tool.py:338` **is** the invalid evidence the parametrized case supplies, because `operations/affinity.py` rejects a `captured_at` with no tzinfo. Adding a timezone would have turned a rejection case into one that passes validation — deleting the coverage without failing anything. |
| `RUF059` unused-unpacked-variable | 7 | Fixed by renaming the unused binding to `_`-prefixed. |
| `EXE001` shebang-not-executable | 6 | Shebang removed from the six `scripts/` files that carry one; four already do not, and all ten are run as `uv run --no-sync python scripts/<name>.py`. |
| `PYI034` non-self-return-type | 3 | Safe autofix: `__aenter__` returns `Self`. |
| `PYI061` redundant-none-literal | 4 | **No fix available** — Ruff marks it `[ ]`, so the mechanical pass does not touch it. Four hand edits, all in `src/hmc_mcp/_app.py` (lines 243, 253, 263 ×2): `Literal[None]` → `None`. |
| `PLR0133` comparison-of-constant | 1 | Surfaced by the fixer's cascade rather than by the census. Fixed at the site the post-mechanical `--statistics` reports. |
| `SIM102` collapsible-if | 4 | Fixed by collapsing. |
| `S110` try-except-pass | 3 | Reviewed. Retained sites get a coded `# noqa: S110` naming why swallowing is the contract. |
| `G201` logging-exc-info | 3 | Fixed: `logger.error(msg, exc_info=True)` → `logger.exception(msg)`, which emits identical output, so the assertions in `tests/unit/test_audit.py` are unaffected. |
| `TRY002` raise-vanilla-class | 2 | Fixed: test fault injection raises `RuntimeError`. `B017`'s `pytest.raises(Exception)` at the matching site narrows with it. |
| `RUF022` unsorted-dunder-all | 1 | **Retained.** Ruff's safe fix sorted `hmc_mcp.api.__all__`, and that list is grouped by subsystem to mirror ADR 0029's inventory block rather than sorted — `tests/unit/test_public_api.py` asserts the exact order, and the mechanical pass turned it red. Order restored, with a coded `# noqa: RUF022` on the assignment. This one was found by the suite rather than by reading, which is why the mechanical pass is gated on `just test` and not on the lint count alone. |
| Remaining singletons and pairs (`C408`, `FLY002`, `FURB162`, `PIE808`, `PIE810`, `PLR0402`, `RUF015`, `B009`, `B017`, `FURB167`, `PLC0206`, `PLR1711`, `RET501`, `SIM118`) | 21 | Fixed individually; each is a local rewrite with no contract surface. |

### A second contract surface the census could not see

`I001` re-sorts imports, which moves every definition below them. `ADR 0092` §3 cites 44
definitions by `file.py:line` and `tests/unit/test_adr_0092_citations.py` enforces those
citations, so the mechanical pass invalidated 38 of them at a stroke. ADR 0092 states the
duty itself — "a PR that moves a definition cited in §3, §4 or §5 re-verifies that
`file:line` in the same change" — so re-verifying them is that record's own prescribed
action rather than an edit to a merged ADR. All 38 are recomputed from the AST.

Two pre-existing citation defects surfaced while doing it and are **not** fixed here,
because correcting them requires a judgment about what ADR 0092 means that this issue does
not own: §4 cites `ssh_commands.py` and `ssh.py`, neither of which exists — both were
renamed under `ssh/` and §4 was never updated — and §5 cites
`tests/unit/test_public_api.py:309` as its `inspect.isfunction` filter, where that line is
`"CreateUserRequest",` in both the merge base and this branch. Neither section is enforced
by a test, which is why both drifted. This change moves no §4 or §5 citation that was
correct.

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
