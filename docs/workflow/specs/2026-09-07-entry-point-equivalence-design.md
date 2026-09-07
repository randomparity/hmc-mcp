# `hmc-mcp` and `python -m hmc_mcp` are one program

Issue [#722](https://github.com/randomparity/hmc-mcp/issues/722).
Governed by [ADR 0128](../../adr/0128-l5-module-entry-point-launch.md); no new decision.

## Problem

`pyproject.toml:33` binds `hmc-mcp` to `hmc_mcp:main`; `src/hmc_mcp/__main__.py` reaches that same
`main` through `raise SystemExit(main())`. ADR 0128:105-116 holds them equivalent by construction,
not by a standing test, and L5 now proves an audit-sink property through the module form — a
divergence would leave L5 green about a program nobody runs.

## Scope

One new module, `tests/app/test_entry_point_equivalence.py`; no production change. Each case runs
both forms as subprocesses with `timeout=60` and `cwd=tmp_path`, under one environment:
`dict(os.environ)` less every `HMC_*` key, `XDG_CONFIG_HOME`, `APPDATA`, `FORCE_COLOR` and
`CLICOLOR_FORCE`, plus `HOME=tmp_path`, `NO_COLOR=1`, `LINES=50`, `COLUMNS=100`. Click sizes help
from the terminal and rich styles it; 100 keeps the longer program name's `Usage:` line unwrapped,
which no substitution reconciles. The console script comes from `shutil.which`, skipped when
absent, under the same-checkout guard at `tests/app/test_fail_closed_startup.py:463-476`; the
module form is `[sys.executable, "-m", "hmc_mcp"]`. `cwd=tmp_path` rather than `-P` keeps the
`sys.path[0]` an operator's launch has, at a directory that shadows nothing.

- **Command tree.** `--help` for the root and for `systems`, the representative subcommand. Each
  run must exit 0 and carry exactly one `Usage:` line; the program name is read from that line and
  replaced *there only*, since `hmc-mcp` recurs in `--profile`'s help text where
  `python -m hmc_mcp` does not. Trailing whitespace is then stripped per line, as rich pads it to
  the width; the normalised outputs must match.
- **Exit status.** An unknown root subcommand and `serve` without `--access-policy`, under both
  forms; each must exit 2.

Deferral carried: `sys.path[0]` shadowing coverage, owned by a follow-up issue; other exclusions
are the frozen charter's.

## Success

1. The help arm fails when the two command trees differ anywhere but the program name.
2. The exit-status arm fails when the forms disagree on a bad argument's exit code.
3. Neither arm passes vacuously: each proves its runs reached the program.
4. Neither arm fails for the two non-identities ADR 0128:117-125 records as intended.
5. The module skips, rather than fails, where no console script is on `PATH`.

## Validation

- **Both arms bite** (success 1, 2). Mode: focused-test. Red, exit arm: `main` returning a status
  *and* `__main__.py` weakened to a bare `main()` — measured console 3, module 0. A bare `main()`
  alone does not: `app()` raises `SystemExit` in Click's standalone mode, so ADR 0128's named
  regression needs both halves. Red, help arm: an option added to the root command under one
  form only. Green: `uv run --no-sync pytest tests/app/test_entry_point_equivalence.py --no-cov`.
- **Not vacuous** (success 3). Mode: focused-test, same case. Red: `systems` renamed away, or
  `typer` unimportable — both forms then fail identically and empty, caught by the exit-0,
  one-`Usage:`-line and exit-2 guards.
- **Non-identities tolerated** (success 4). Mode: focused-test, same case. Red: dropping the
  normalisation, since the raw help differs at HEAD. `sys.path[0]` needs no red: neither `--help`
  nor an exit code reads it.
- **Skip path** (success 5). Mode: task-test-not-applicable. Reached only when `shutil.which`
  returns `None`; forcing that tests `shutil.which`. Idiom:
  `tests/app/test_fail_closed_startup.py:470-472`.
