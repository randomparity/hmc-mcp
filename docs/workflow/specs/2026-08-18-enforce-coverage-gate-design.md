# Enforced package coverage gate design

## Goal and scope

Issue #240 reports that the package coverage gate prints `FAIL Required test coverage of 90% not
reached. Total coverage: 89.78%` and exits `0`. `just test` therefore succeeds, `just verify`
continues through `smoke`, `build`, and `verify-artifacts`, and CI goes green on a total below the
declared floor.

This design makes the comparison exact, raises package coverage above the declared 90% floor rather
than lowering the floor, and adds tests that fail if either half of the gate is later removed.

[ADR 0034](../../adr/0034-exact-coverage-gate.md) governs the gate's configuration shape and the
decision to hold the floor at 90 with margin.
[ADR 0002](../../adr/0002-unify-local-and-hosted-ci.md) governs the single composed verification
graph that `just test` participates in; this design does not change that graph's shape.
[ADR 0020](../../adr/0020-rolling-cpython-support-policy.md) fixes the supported interpreter set
whose coverage variance the margin must absorb.

The change is limited to `pyproject.toml`, tests, and these workflow design records. It does not
change package APIs, runtime behavior of any module under `src/hmc_mcp`, dependencies, the CI
workflow, supported platforms, permissions, or credentials. Production code is not deleted or
rewritten to raise the percentage; coverage rises only by exercising code that already ships. Any
statement found to be genuinely unreachable is reported in the pull request rather than removed
under this issue.

## Root cause

coverage.py compares the total **rounded to `[tool.coverage.report].precision`** against
`fail_under`. That precision defaults to `0`. At 89.78% the rounded total is `90`, `90 >= 90`
holds, and the process exits `0`. pytest-cov independently composes its printed message from the
*unrounded* total, which is why the output says "not reached" while the run succeeds.

An isolated probe run against the repository's pinned pytest 9.1.1, pytest-cov 7.1.0, and coverage
7.15.4 established four facts. The first three used a synthetic package measuring 89.84%:

| Configuration | Exit |
|---|---|
| `--cov-fail-under=90` in `addopts`, no `[tool.coverage.report]` | `0` |
| `--cov-fail-under=90` in `addopts` plus `[tool.coverage.report] precision = 2` | `1` |
| `[tool.coverage.report]` carrying both `fail_under = 90` and `precision = 2` | `1` |

The third row is the one this design depends on and the one the issue did not establish: the issue
demonstrated the fix through `COVERAGE_RCFILE`, leaving open whether pytest-cov honours a
`fail_under` supplied by `pyproject.toml`. It does.

The fourth used a package measuring 90.10% with `[tool.coverage.report] fail_under = 95` and
`addopts` carrying `--cov-fail-under=50`. It reported `Required test coverage of 50% reached` and
exited `0`: a command-line floor overrides a configured one, and the configured value is discarded
without a warning. Keeping both would therefore leave the visible configured floor inert. That
precedence is symmetric and is relocated rather than removed — a `--cov-fail-under` added later to
`addopts`, a CI job, or an ad-hoc invocation still overrides the configured 90 silently.

Two further probes settled the remaining alternatives. `--cov-fail-under=90 --cov-precision=2` in
`addopts` with no `[tool.coverage.report]` section at all also exits `1` at 89.84%, so that
smaller alternative genuinely works and is rejected on where the settings belong rather than on
whether they function; ADR 0034 records that. And a package at 89.996% with `fail_under = 90` and
`precision = 2` exits `0` while printing a `FAIL` banner, which is the residual described under
*Failure behavior*.

## Configuration

`[tool.pytest.ini_options].addopts` becomes `--cov=hmc_mcp --cov-report=term-missing`, retaining
its existing comment directing contributors to run focused subsets with `--no-cov`. A new
`[tool.coverage.report]` section carries:

```toml
[tool.coverage.report]
fail_under = 90
precision = 2
```

Both halves of the gate live in one table, so the next reader adjusting the floor sees the
precision that decides whether the floor means anything. No other coverage option is introduced.

## Coverage margin

The floor is held with margin rather than met precisely, because it is enforced independently
inside each of the eight legs CI runs (four interpreters × two architectures) and the measured
total varies between them. The same suite reports 611 missed statements of 5977 on CPython 3.11
and 612 on CPython 3.14 — one statement, or 0.017 percentage points. The binding requirement is
the lowest-scoring leg, so a total that clears the floor on the interpreter a contributor happens
to run locally can still fail one they never ran.

The legs vary on two axes, not one. `src/hmc_mcp/config.py` and `src/hmc_mcp/cli_config.py`
between them carry five `sys.platform` branches, so a developer's darwin machine covers the darwin
arms and CI's `ubuntu-24.04` and `ubuntu-24.04-arm` runners cover the POSIX arms, and each misses
what the other covers. That moves roughly four statements — about 0.067 points, four times the
interpreter spread — and it moves them relative to a 611-missed baseline that was itself measured
on darwin. The margin is a chosen safety factor sized to cover both axes, not a bound derived from
either.

The target is a total of **at least 90.50%**, which at the current package size of 5977 statements
means **no more than 567 missed statements**, down from 611 — a margin of roughly half a point,
requiring at least 44 currently-missed statements to be covered.

The primary vehicle is `src/hmc_mcp/cli_storage.py`, the largest single gap at 110 missed
statements of 199 (45%). It is a thin Typer layer over `operations_storage` and
`operations_provision`, and `tests/app/test_cli_commands.py` already provides the harness for
exercising it: a `FakeHMC` scripted stand-in installed over `cli_app.client_from_env`, driven
through `typer.testing.CliRunner`. New tests follow that established pattern rather than
introducing a second CLI-testing idiom.

Uncovered branches in that module are ordinary command bodies: table rendering paths, `--json`
output paths, confirmation-prompt abort paths, and the `typer.Exit(1)` failure paths of
`attach-disk`, `detach-mapping`, and `upload-iso`. Each is reachable through `CliRunner` with the
existing fake.

## Gate regression tests

Two tests in `tests/test_ci_pipeline.py`, the module that already holds this repository's
guardrail-contract tests, cover the gate itself.

**A behavioral test** proves an under-floor total actually fails. It reads the repository's real
`fail_under` and its real `[tool.coverage.report]` table from `pyproject.toml`, generates a
synthetic package in `tmp_path` whose true coverage rounds up to the floor but sits below it, and
runs `pytest` there as a subprocess with the 180-second timeout this module already uses for
subprocesses, so a hung child fails the test rather than stalling `just test`.

The subprocess runs with `PYTEST_ADDOPTS`, `COVERAGE_RCFILE`, and `COVERAGE_FILE` removed from
the inherited environment, matching the explicit-env idiom the module's existing subprocess test
uses. Without that, an exported `PYTEST_ADDOPTS=--no-cov` makes the child measure nothing and
exit `0` (verified), reddening the gate test for a reason unrelated to the gate — and
`COVERAGE_RCFILE` is the variable the issue itself used to demonstrate the fix, so it is
plausibly set in exactly the debugging sessions where this test matters.

A non-zero exit is **not** a sufficient assertion, and specifying one would be the defect this
test exists to prevent. Every way the harness can break also exits non-zero: a syntax error in the
generated package exits 1 or 2, collecting no tests exits 5, and an interpreter that cannot import
pytest exits 1. A test asserting only the sign would stay green forever while proving nothing
about `fail_under` or `precision`. It therefore asserts two things:

- `returncode == 1` exactly. pytest-cov reaches a coverage failure through `session.testsfailed`,
  so `EXIT_TESTSFAILED` is the only correct code; `2` or `5` mean the harness broke.
- pytest-cov's literal diagnostic in the captured output —
  `Coverage failure: total of 89.90 is less than fail-under=90.00` for the configured floor. That
  one string pins the measured total, the floor, and the precision simultaneously, which is also
  what guards the statement arithmetic below from drifting unnoticed.

  The expected string is **formatted from the configured precision**, not hardcoded to two
  places, for the same reason the synthetic total is derived rather than fixed: at
  `precision = 3` the message becomes `total of 89.900 is less than fail-under=90.000` (verified),
  so a hardcoded string would redden the gate test for a change that strengthened the gate — the
  false signal this section otherwise exists to avoid.

  The emitter is pytest-cov, not coverage.py: coverage.py's own copy of this message lives in its
  CLI, which pytest-cov does not go through. The distinction matters for the record because it
  means a pytest-cov bump, not a coverage.py bump, is what can invalidate the assertion.

A paired control run makes the test able to fail in both directions: the same generator with
`10 * fail_under` covered produces exactly the floor and must exit `0`. Without it, a permanently
failing harness would look like a permanently working gate.

The synthetic total is constructed arithmetically from the configured floor rather than from magic
constants. With a package of exactly 1000 statements and `10 * fail_under - 1` of them covered, the
true total is `fail_under - 0.1` percent — below the floor for any integer floor, and rounding to
the floor at precision `0`. For the configured floor of 90 that is 899 covered of 1000, or 89.90%,
verified to produce exactly that TOTAL line. The temporary project reuses the repository's own
`[tool.coverage.report]` table, so deleting `precision` from `pyproject.toml` makes this test fail.

**A configuration test** asserts the gate's declared shape. Asserting only that
`--cov-fail-under` is absent would guard one of five one-token `addopts` edits that silently
disarm the gate; all are verified to exit `0` on a package that should fail:

| Edit to `addopts` | Effect |
|---|---|
| add `--cov-fail-under=<n>` | overrides the configured floor silently |
| remove `--cov=hmc_mcp` | nothing is measured, so `fail_under` is never consulted |
| add `--no-cov` | same, and it is the flag the retained comment teaches contributors |
| add `--cov-precision=0` | restores the original defect verbatim — prints `FAIL … not reached` and exits `0` |
| add `--cov-config=<path>` | coverage.py reads that file instead, so the floor and precision are never seen |

The last row generalises into a second class the content assertions miss entirely: they guard
what `pyproject.toml` says, not **which file coverage.py reads**. coverage.py tries
`.coveragerc`, `.coveragerc.toml`, `setup.cfg`, `tox.ini`, `pyproject.toml` in order and stops at
the first that reads — and for the two `.coveragerc` forms, merely being readable counts, empty
or not. So an **empty `.coveragerc` at the repository root disarms the gate with `pyproject.toml`
byte-identical** (verified: exit `0` at a total of 89.90%). A `[coverage:report]` section added to
`setup.cfg` or `tox.ini` wins the same way. None of those four files exists in this repository
today.

Every one of these disarmed runs is *more* silent than the original defect: with `fail_under`
falling back to `0`, pytest-cov suppresses its banner entirely, so not even the misleading `FAIL`
line appears.

Guarding only `addopts` would repeat the same mistake one level out, because this design *creates*
a second vector: the table it introduces. `[tool.coverage.report] omit = ["*/uncovered.py"]` on
the probe package takes the total from 89.90% to `TOTAL 898 0 100.00%` and exits `0` (verified) —
one line, and the gate is not merely disarmed but reports perfection. The behavioral test cannot
catch it, because it replays the repository's table into a synthetic project whose filenames no
real `omit` pattern matches, so the added key is inert there. `[tool.coverage.run]`'s
`omit`/`include`/`exclude*` keys do the same. And the floor can be defeated from any pytest
invocation site, not just `addopts` — the `justfile` `test` recipe or a CI step carrying
`--cov-fail-under=0` overrides it exactly as `addopts` would.

The test therefore asserts:

- `fail_under` is 90 and `precision` is at least 2;
- `--cov=hmc_mcp` is present in `addopts`, and none of `--cov-fail-under`, `--no-cov`,
  `--cov-precision`, or `--cov-config` appears in it;
- no `.coveragerc`, `.coveragerc.toml`, `pytest.toml`, `.pytest.toml`, `pytest.ini`, or
  `.pytest.ini` exists at the repository root, and `setup.cfg` and `tox.ini`, if either is ever
  added, declare no `[coverage:*]` section — so `pyproject.toml` stays the file **both** tools
  actually read. Two search orders have to land there, not one. coverage.py's is the
  `.coveragerc` family; pytest's is `pytest.toml`, `.pytest.toml`, `pytest.ini`, `.pytest.ini`,
  then `pyproject.toml` (`_pytest/config/findpaths.py`), and the first four win outright even
  when empty. The pytest side is the worse vector: it removes `--cov=hmc_mcp` from the
  invocation, so nothing is measured, `fail_under` is never consulted, and the output is
  indistinguishable from a project with no coverage configured — where the `.coveragerc` route at
  least still prints a coverage table. Verified against this repository: each of the four takes a
  subset run from exit `1` with a table to exit `0` with none. `tox.ini` and `setup.cfg` are not
  vectors on the pytest side, because `pyproject.toml` precedes them in that order. Adding one of
  these files is an ordinary thing to do — a marker, a `filterwarnings` entry — which is why the
  gate cannot rest on nobody doing it;
- `[tool.coverage.report]`'s key set is exactly `{fail_under, precision}`, and no
  `[tool.coverage.run]` section declares `omit`, `include`, or an `exclude*` key. The two halves
  are deliberately different rules. `[tool.coverage.run]` is an existing namespace this change
  does not create, so only the denominator-changing keys are rejected there.
  `[tool.coverage.report]` is a namespace this change *introduces*, and it is frozen to the gate's
  two keys outright —
  broader than "no denominator key", because coverage.py accepts seventeen keys in that section
  and an enumeration of the harmful ones is a bet that the enumeration stays complete across
  versions. `partial_also` and `partial_branches` already fall outside such an enumeration and
  start mattering the moment anyone adds `--cov-branch`. The cost is that a display-only key such
  as `show_missing` also has to be added to the test — one line, in the same commit, which is what
  keeping the gate's configuration reviewed means. This mirrors the exact-allowlist idiom the
  module already uses for the secrets baseline;
- the whole `justfile` and every workflow carry none of `--cov-fail-under`, `--cov-precision`,
  `--cov-config`, or `COVERAGE_RCFILE`, and no `--no-cov` without a test path beside it; and the
  `test` recipe body is pinned exactly, because every test here lives under `tests/`, so
  `pytest -q --no-cov tests` would satisfy the line rule while disabling the gate on a full-suite
  run. This reuses the justfile/workflow text-reading idiom already in this module.
  `COVERAGE_RCFILE` is in that list because it is `--cov-config` delivered as an environment
  variable; see *Failure behavior* for which half of that vector is guarded and which is accepted.

## Failure behavior

A total below 90.00% fails `just test` with coverage.py's own diagnostic —
`Coverage failure: total of <n> is less than fail-under=90.00` — and a non-zero exit.
`just verify` composes `static test smoke build verify-artifacts` as a recipe dependency list, so
a failing `test` aborts before `smoke` runs; no later stage observes the failure or needs to.

On a successful run no guardrail prints `FAIL`: above the floor, pytest-cov prints
`Required test coverage of 90.0% reached`. The trailing `.0` is new and follows from this change:
the floor is now sourced from coverage.py's float-typed config rather than parsed as an int from
a command-line flag, and the banner interpolates that value directly.

Acceptance criterion 3 is met for every total the project can realistically hold, but it is
narrowed rather than closed, and the residual is recorded rather than hidden. pytest-cov composes
its banner from the unrounded total while the exit status uses the rounded one, so in the window
`[89.995, 90.0)` a run passes and still prints
`FAIL Required test coverage of 90.0% not reached. Total coverage: 90.00%`. A synthetic package at
89.996% reproduces this. The window is 0.005 points wide against the 0.5 points the original
defect spanned, and the target margin of 90.50% keeps the project two orders of magnitude away
from it. Closing it entirely would mean reimplementing pytest-cov's reporting, which is not worth
the risk it removes; [ADR 0034](../../adr/0034-exact-coverage-gate.md) accepts it explicitly.

The floor binds where the process starts. pytest resolves `addopts` by walking upward to the
rootdir, but coverage.py opens its configuration relative to the working directory and does not
walk. `pytest` invoked from the repository root — which is what `just test` and every CI leg do —
reads `[tool.coverage.report]` and enforces the floor; `pytest` invoked from a subdirectory still
measures, because `--cov=hmc_mcp` comes from `addopts`, but finds no configuration, falls back to
`fail_under = 0` and `precision = 0`, and enforces nothing. Verified: the same 502-statement
package reports `89.84%` and exits `1` from the root, and `90%` and exits `0` from `tests/`.

Two environment variables can disarm the gate: `COVERAGE_RCFILE` redirects coverage.py to another
configuration file, and `PYTEST_ADDOPTS` can inject `--no-cov`. This vector has two halves and
they are treated differently.

A **committed** one — `env: COVERAGE_RCFILE:` on a workflow step, or an `export` in a `justfile`
recipe — is inside the repository's reach, lands in every CI leg's fresh runner environment, and
is guarded: the invocation-site scan rejects the `COVERAGE_RCFILE` token in the justfile and in
every workflow, exactly as it rejects `--cov-config`, which is the same redirect spelled as a
flag. `PYTEST_ADDOPTS` needs no separate token, because the spellings that disarm the gate
(`--no-cov`, `--cov-fail-under=0`) are strings the scan already rejects on the line that carries
them.

A **contributor's own local export** is not reachable by any test in this repository and is
accepted. The gate test scrubs both variables from its own subprocess so it cannot be reddened by
a stray export, but nothing can scrub them from that contributor's `just test`. CI is unaffected.
That residual is developer-facing signal loss, in the same class as the working-directory
narrowing below.

This is a **narrowing this change introduces**, not a pre-existing property. `--cov-fail-under=90`
in `addopts` bound from any working directory under the rootdir, because `addopts` itself came
from the rootdir's ini file; the configured floor binds from the repository root only.

Keeping the floor in `addopts` does not avoid it. That is the intuitive compromise and it was
measured: with `--cov-fail-under=90` in `addopts` and only `precision = 2` in the table, the same
package exits `0` reporting `90%` from `tests/` — identical to the chosen design, because
`precision` is read from the table either way and its loss alone is enough to round 89.84% back up
to the floor. Only `--cov-fail-under=90 --cov-precision=2` in `addopts`, with no table at all,
enforces from a subdirectory; it exits `1` reporting `89.84%` from both locations. ADR 0034
rejects that form on settings ownership, and the operator confirmed the trade on 2026-08-18 with
both measurements in hand.

The narrowing is accepted because every path that runs the gate — `just test`, and `just verify`
on each CI leg — starts at the repository root. That is a fact about the repository today, not an
invariant, and the difference matters: a CI step carrying `working-directory: tests` would run the
suite, measure the whole package, enforce nothing, and print no banner saying so. So the CI half
is not left resting on nobody writing that key — the invocation-site test rejects
`working-directory` across the justfile and every workflow, which is the only guard that catches
this vector, since the edit contains no coverage flag at all. What remains developer-facing is a
contributor's own `pytest` run from a subdirectory, which no test can reach. With the CI half
guarded the residual is signal loss rather than a hole, and
a `.coveragerc`, a `COVERAGE_RCFILE` export, or a rootdir-detecting wrapper would each cost more
than the risk. [ADR 0034](../../adr/0034-exact-coverage-gate.md) records the same asymmetry
against its `addopts` alternative, which does not have this sensitivity — so that alternative is
not strictly worse on every axis, and the ownership argument is not the only ground on record.

## Security model

The change is not security-relevant. It adds no entry point, touches no authentication,
authorization, secret, or credential, parses no input it did not produce, builds no command, query,
path, or URL from a non-literal value, changes no dependency or pinned action, and alters no file
mode, network exposure, or security-relevant default. It tightens an existing guardrail rather than
widening one.

The one new process boundary is internal to the test suite: the behavioral gate test invokes
`sys.executable -m pytest` against a package it generates itself inside pytest's `tmp_path`. Its
inputs are literals and values read from the repository's own tracked `pyproject.toml`; no external
input reaches it, and the fixture directory is removed by pytest.

## Verification

- `just test` exits non-zero when the package total is below 90.00%, and zero when it is above.
- `just verify` stops at `test` on a coverage failure and does not run `smoke`.
- **The branch's own CI run reports at least 90.50% on every one of the eight legs.** That is the
  binding measurement, because the legs differ by interpreter and by platform and the requirement
  is the lowest-scoring one. A local run measures at most two interpreters on an architecture no
  leg uses, so it is a pre-push check that the target is plausibly met, never the confirmation.
- Locally, before pushing: the full suite reports at least 90.50% on CPython 3.11 and on
  CPython 3.14, with `.venv` restored to 3.11 afterwards.
- Removing `precision` from `[tool.coverage.report]` makes the behavioral gate test fail; this is
  confirmed by mutating the configuration, observing the failure, and reverting.
- Each of the four gate-disabling `addopts` edits is rejected by the configuration test.
- `just verify` passes end to end on the branch.
