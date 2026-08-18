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

A table is chosen over the equivalent pytest flags on ownership: `fail_under` is coverage.py's
pass/fail policy and `precision` is the precision that policy is compared at, so both live in
coverage.py's own table. `addopts` is left saying only what the pytest run does — measure
`hmc_mcp`, report term-missing.

The floor stays at 90. Package coverage is raised above it by adding tests rather than by lowering
the declared number.

## Consequences

A total below 90.00% now fails `just test`, which aborts `just verify` before `smoke`, `build`, and
`verify-artifacts` — the composed suite stops at the first red gate as it always claimed to.
Terminal reports print two decimal places for every file and for the total.

The floor is enforced independently inside each of the eight CI legs `just verify` runs — CPython
3.11 to 3.14 across amd64 and arm64 — and coverage totals differ between them: 611 missed
statements of 5977 on CPython 3.11, 612 on CPython 3.14. The binding requirement is therefore the
lowest-scoring interpreter, and a contributor who measures locally on one can pass and still turn
a leg red that they never ran.

This change lands at least half a point above the floor for that reason. That half point is a
chosen safety factor covering ordinary coverage churn, not a bound derived from the 0.017-point
interpreter spread, which is thirty times smaller. It is a landing target this change meets, not
an invariant the repository holds: the enforced floor after merge is `fail_under = 90`, so a later
change landing at 90.05% is not objected to by anything here.

The comparison is exact to two decimal places, so the defect's banner/exit mismatch survives in
miniature: within `[89.995, 90.0)` a run passes while still printing `FAIL … Total coverage:
90.00%`, because the banner uses the unrounded total and the exit status the rounded one. That
window is 0.005 points against the 0.5 the defect spanned; `precision = 2` is the value the issue
specified, and a higher precision would shrink it further if it ever matters.

Because the floor now lives in `[tool.coverage.report]`, every coverage.py reporting subcommand
honours it — `report`, `html`, `xml`, `json`, and `lcov` each exit `2` below the floor. Anyone
later adding a `coverage xml` or `coverage html` step inherits that failure. A focused subset
still needs `--no-cov`, as the retained `addopts` comment directs and as it did before, because
the floor is measured package-wide.

Where the floor binds narrows. coverage.py opens its configuration relative to the working
directory and does not walk upward as pytest does for `addopts`, so the previous flag applied
from any directory under the rootdir while the configured floor applies only from the repository
root. A `pytest` run from a subdirectory still measures but enforces nothing — verified against a
synthetic package at a true 89.84%: exit `1` reporting `89.84%` from the root, exit `0` reporting
`90%` from `tests/`. Every gate-running path — `just test`, and `just verify` on each CI leg —
starts at the root, so this is developer-facing signal loss rather than a hole in enforcement.

That narrowing is not avoidable by keeping the floor in `addopts`, which is the intuitive
compromise and does not work: `precision` has to live in coverage.py's table either way, so it is
lost from a subdirectory too and the total rounds back up to the floor. The same probe run with
`--cov-fail-under=90` in `addopts` and only `precision = 2` in the table exits `0` from `tests/`,
identically to the decision above. Only the all-flags form avoids it. The operator was shown both
measurements and confirmed the trade on 2026-08-18, keeping the table.

The precedence hazard between a command-line floor and a configured one is relocated, not removed.
`--cov-fail-under` on the command line still overrides the configured 90 silently, so a CI job or
an ad-hoc invocation passing it replaces the floor with no warning. The configured value is the
one under version control and under test, which is what makes the relocation worth making, but it
is not a guarantee against an override.

## Considered & rejected

- **Set `precision = 2` and leave `--cov-fail-under=90` in `addopts`.** The issue's own suggested
  fix, and it does work — the probe confirms it. Rejected because it leaves the gate split across
  a pytest flag and a coverage.py table, so neither location shows the whole gate, and because it
  invites a duplicate floor: the natural next edit is to move `fail_under` into the table beside
  `precision`, at which point the `addopts` flag silently wins and the configured floor is inert
  (verified — configured `95` with `--cov-fail-under=50` reported "50% reached" and exited `0`).
  That override precedence is symmetric and the chosen design is exposed to it too, as
  Consequences records; what this entry rejects is the duplicate-floor state the split invites,
  not an asymmetry in the hazard. This option is also no cheaper on the cwd axis than the
  decision — verified, and recorded in Consequences — because its `precision` still comes from
  the table.
- **Put both in `addopts` as `--cov-fail-under=90 --cov-precision=2`.** pytest-cov 7.1.0 does
  ship `--cov-precision`, it overrides the config value, and it feeds the same comparison that
  sets the exit status; a probe at 89.84% with no `[tool.coverage.report]` section at all exits
  `1`. So this works, and it is the smallest change of any option here — one flag added rather
  than one removed plus a new table. Rejected on the ownership ground in the Decision: these are
  coverage.py's settings, and `addopts` is pytest's invocation string. This is a close call on a
  small diff, not a defect in the alternative — and the alternative wins on one axis: `addopts`
  binds from any working directory under the rootdir, while a configured floor binds only where
  the process starts, so this alternative would keep the floor applying to a `pytest` run from a
  subdirectory (see Consequences). That is measured, not assumed: the same 89.84% package exits
  `1` from both the root and `tests/` under this form, and it is the **only** form tried that
  does. That was judged not to outweigh ownership, because every path that runs the gate starts
  at the repository root — a trade the operator was shown the measurements for and confirmed on
  2026-08-18.
- **Set `fail_under` to the margin — 90.5 rather than 90.** This is the only mechanism that would
  make the margin an enforced invariant instead of a landing condition that erodes on the next
  pull request. Not adopted: the issue and the operator decision both fix the declared floor at
  90, and raising it is a scope change rather than a fix to the gate that could not fail. Worth
  revisiting if totals repeatedly settle just above 90.00%.
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
