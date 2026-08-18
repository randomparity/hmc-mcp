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

## Decision

`[tool.coverage.report]` carries both `fail_under = 90` and `precision = 2`, and
`--cov-fail-under` is removed from `addopts`.

Both settings belong to coverage.py rather than to pytest: `fail_under` is its pass/fail policy
and `precision` is the reporting precision that policy is compared at. pytest-cov's
`--cov-fail-under` and `--cov-precision` are overrides of those two settings, so the canonical
home is coverage.py's own table. `addopts` is left saying only what the pytest run does — measure
`hmc_mcp`, report term-missing — and the gate's policy and its precision sit adjacent under one
heading.

The floor stays at 90. Package coverage is raised above it by adding tests rather than by lowering
the declared number, and is held with enough margin to absorb the per-interpreter variance measured
below.

## Consequences

A total below 90.00% now fails `just test`, which aborts `just verify` before `smoke`, `build`, and
`verify-artifacts` — the composed suite stops at the first red gate as it always claimed to.
Terminal reports print two decimal places for every file and for the total.

Coverage totals differ by interpreter: 611 missed statements of 5977 on CPython 3.11, 612 on
CPython 3.14. That is one statement, or 0.017 points, measured on two of the eight legs
`just verify` runs; the other six are unmeasured, so it is a floor from a partial sample, not a
bound. The floor is therefore held with margin: treat a total within a tenth of a point of 90.00%
as unfinished. That tenth is a chosen safety factor over a partial sample, not a derived number,
and a leg that later disagrees by more is a reason to raise it.

The comparison is exact to two decimal places, not perfectly exact, and the original defect's
banner/exit mismatch survives inside that residue: pytest-cov composes its banner from the
unrounded total while the exit status uses the rounded one. In the window `[89.995, 90.0)` a run
passes while printing `FAIL Required test coverage of 90% not reached. Total coverage: 90.00%`,
verified with a synthetic package at 89.996%. That window is 0.005 points against the 0.5 points
the defect spanned — narrowed by two orders of magnitude, not closed. It is accepted rather than
engineered around, since closing it means reimplementing pytest-cov's reporting.

Because the floor now lives in `[tool.coverage.report]`, every coverage.py reporting subcommand
honours it — `report`, `html`, `xml`, `json`, and `lcov` each exit `2` below the floor. Anyone
later adding a `coverage xml` or `coverage html` step inherits that failure. A contributor running
a focused subset must pass `--no-cov`, as the retained `addopts` comment directs, rather than
relying on the gate being inert.

The precedence hazard between a command-line floor and a configured one is relocated, not removed.
`--cov-fail-under` on the command line still overrides the configured 90 silently, so a CI job or
an ad-hoc invocation passing it replaces the floor with no warning. The configured value is the
one under version control and under test, which is what makes the relocation worth making, but it
is not a guarantee against an override.

## Considered & rejected

- **Set `precision = 2` and leave `--cov-fail-under=90` in `addopts`.** The issue's own suggested
  fix, and it does work — the probe confirms it. Rejected because it splits the gate across the
  two mechanisms: the floor stays a pytest flag while its precision becomes a coverage.py setting,
  so neither location shows the whole gate. The probe also showed the split is actively unsafe
  rather than merely untidy — with `fail_under = 95` configured and `--cov-fail-under=50` in
  `addopts`, the run reported "Required test coverage of 50% reached" and exited `0`, discarding
  the configured floor with no warning. A reader who later adds `fail_under` to the config table
  would get a floor that looks declared and is inert.
- **Put both in `addopts` as `--cov-fail-under=90 --cov-precision=2`.** pytest-cov 7.1.0 does
  ship `--cov-precision`, it overrides the config value, and it feeds the same comparison that
  sets the exit status; a probe at 89.84% with no `[tool.coverage.report]` section at all exits
  `1`. So this works, and it is the smallest change of any option here — one flag added rather
  than one removed plus a new table. Rejected because it puts coverage.py's pass/fail policy and
  reporting precision in pytest's invocation string, where `coverage` invoked directly cannot see
  either, and where they sit among flags (`--cov`, `--cov-report`) that are genuinely pytest's.
  The chosen design pays two extra lines to keep each setting in its owning tool's configuration.
  This is a close call on a small diff, not a decisive defect in the alternative.
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
