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
without a warning. Keeping both would therefore leave the visible configured floor inert.

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

The floor is held with margin rather than met precisely, because the measured total varies by
interpreter. The same suite reports 611 missed statements of 5977 on CPython 3.11 and 612 on
CPython 3.14 — a spread of one statement, or 0.017 percentage points, across two of the four
supported interpreters. CI runs all four on two architectures.

The target is a total of **at least 90.50%** on both CPython 3.11 and CPython 3.14, which at the
current package size of 5977 statements means **no more than 567 missed statements**, down from
611. That is a margin of roughly half a point against an observed variance of hundredths, and it
requires covering at least 44 currently-missed statements.

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
synthetic package in `tmp_path` whose true coverage rounds up to the floor but sits below it, runs
`pytest` there as a subprocess, and asserts a non-zero exit.

The synthetic total is constructed arithmetically from the configured floor rather than from magic
constants. With a package of exactly 1000 statements and `10 * fail_under - 1` of them covered, the
true total is `fail_under - 0.1` percent — below the floor for any integer floor, and rounding to
the floor at precision `0`. For the configured floor of 90 that is 899 covered of 1000, or 89.90%.
The temporary project reuses the repository's own `[tool.coverage.report]` table, so deleting
`precision` from `pyproject.toml` makes this test fail.

**A configuration test** asserts the gate's declared shape: `[tool.coverage.report].fail_under` is
90, its `precision` is at least 2, and `--cov-fail-under` does not appear in `addopts`. The last
assertion prevents the two-mechanism split that concealed the original defect from returning, since
a command-line `--cov-fail-under` silently overrides the configured value.

## Failure behavior

A total below 90.00% fails `just test` with coverage.py's own diagnostic —
`Coverage failure: total of <n> is less than fail-under=90.00` — and a non-zero exit.
`just verify` composes `static test smoke build verify-artifacts` as a recipe dependency list, so
a failing `test` aborts before `smoke` runs; no later stage observes the failure or needs to.

On a successful run no guardrail prints `FAIL`: above the floor, pytest-cov prints
`Required test coverage of 90% reached`. The misleading case the issue reports — a printed `FAIL`
on a run reported as successful — cannot recur, because the only total that produces that message
now also produces a non-zero exit.

The behavioral gate test fails loudly rather than silently skipping if its assumptions break: an
absent `[tool.coverage.report]` section, a non-integer floor, or a `pytest` subprocess that cannot
start each surface as a test failure, not as a passed test that checked nothing.

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
- The full suite reports at least 90.50% on CPython 3.11 and on CPython 3.14, measured locally
  before the pull request is opened.
- Removing `precision` from `[tool.coverage.report]` makes the behavioral gate test fail; this is
  confirmed by mutating the configuration, observing the failure, and reverting.
- `just verify` passes end to end on the branch.
