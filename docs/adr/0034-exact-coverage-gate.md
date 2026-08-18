# 0034: Compare package coverage against its floor exactly

## Status

Accepted

## Context

`pyproject.toml` declared the package coverage floor as `--cov-fail-under=90` in
`[tool.pytest.ini_options].addopts` and set no `[tool.coverage.report]` section. coverage.py's
default `precision` is `0`, and it compares the total **rounded to that precision** against
`fail_under`. At a real total of 89.78% the rounded value was `90`, so `90 >= 90` held and
`just test` exited `0` — while pytest-cov composed its own message from the unrounded total and
printed `FAIL Required test coverage of 90% not reached`. The declared 90% floor was in practice
89.5%, and `just verify` ran on through `smoke`, `build`, and `verify-artifacts` because nothing
had failed.

An isolated probe against the repository's own pinned pytest 9.1.1 / pytest-cov 7.1.0 /
coverage 7.15.4 confirmed all three legs of this: a synthetic package at 89.84% exits `0` under
the old configuration, exits `1` once `[tool.coverage.report] precision = 2` is set in
`pyproject.toml`, and exits `1` with `fail_under` read from that same section instead of from
`addopts`.

The floor and the precision that decides whether the floor means anything were separable settings
in two different files' worth of syntax — a command-line string and an absent config table. That
separation is why the defect was invisible: the `addopts` comment acknowledged a "rounded
full-suite baseline" with no adjacent knob showing what the rounding cost.

## Decision

Express the whole gate in one place. `[tool.coverage.report]` carries both `fail_under = 90` and
`precision = 2`, and `--cov-fail-under` is removed from `addopts`.

The floor stays at 90. Package coverage is raised above it by adding tests rather than by lowering
the declared number, and is held with enough margin to absorb the per-interpreter variance measured
below.

## Consequences

A total below 90.00% now fails `just test`, which aborts `just verify` before `smoke`, `build`, and
`verify-artifacts` — the composed suite stops at the first red gate as it always claimed to.
Terminal reports print two decimal places for every file and for the total.

Coverage totals differ slightly by interpreter: the same suite reports 611 missed statements of
5977 on CPython 3.11 and 612 on CPython 3.14. The CI matrix spans four interpreters on two
architectures, so a total that lands exactly on the floor on one leg can fail on another. The floor
is therefore held with margin, not met precisely; a change that leaves the total within roughly a
tenth of a point of 90.00% is not finished.

The comparison is exact to two decimal places, not perfectly exact. A total of 89.995% still
rounds to 90.00 and passes — a residual of about a third of one statement at the current package
size, against the roughly 30 statements the previous rounding concealed.

Because the floor now lives in `[tool.coverage.report]`, `coverage report` invoked directly honours
it too, and a contributor running a focused subset must pass `--no-cov` (as the retained `addopts`
comment directs) rather than relying on the gate being inert.

## Considered & rejected

- **Set `precision = 2` and leave `--cov-fail-under=90` in `addopts`.** The issue's own suggested
  fix, and it does work — the probe confirms it. Rejected because it preserves the split that hid
  the defect: the floor and its precision stay in separate settings, so the next person to adjust
  one still cannot see the other. The probe also showed the split is actively unsafe rather than
  merely untidy — with `fail_under = 95` configured and `--cov-fail-under=50` in `addopts`, the
  run reported "Required test coverage of 50% reached" and exited `0`, discarding the configured
  floor with no warning. A reader who later adds `fail_under` to the config table would get a
  floor that looks declared and is inert.
- **Lower the floor to `--cov-fail-under=89` to match the real total.** Strictly more honest than a
  gate that cannot fail, and explicitly offered by the issue. Rejected by the operator in favour of
  raising coverage; codifying 89 would have made the reported number honest by giving up the
  standard rather than meeting it.
- **Move the gate to a separate `just coverage` recipe running `coverage report --fail-under=90
  --precision=2`.** Adds a recipe and a second reporting pass over the same data, and puts the gate
  somewhere `pytest` alone no longer enforces it, so a developer running `pytest` directly loses
  the signal. No gain over configuring the run that already produces the measurement.
- **Do nothing and treat the printed `FAIL` as sufficient warning.** A message with no exit status
  behind it trains readers to skim red text in guardrail output, and leaves every downstream
  consumer of "CI is green" wrong about what was checked.
