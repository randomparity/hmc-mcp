# `hmc-mcp` and `python -m hmc_mcp` are one program

Issue [#722](https://github.com/randomparity/hmc-mcp/issues/722).
Governed by [ADR 0128](../../adr/0128-l5-module-entry-point-launch.md); no new decision.

## Problem

`pyproject.toml:33` binds `hmc-mcp` to `hmc_mcp:main`; `src/hmc_mcp/__main__.py` reaches it through
`raise SystemExit(main())`. ADR 0128:105-116 holds them equivalent by construction,
not by a standing test — yet L5 now proves an audit-sink property through the module form.

## Scope

One new module, `tests/app/test_entry_point_equivalence.py`; no production change. Each case runs
both forms as subprocesses with `timeout=60` and `cwd=tmp_path`, under one environment:
`dict(os.environ)` less every `HMC_*` key, `XDG_CONFIG_HOME`, `APPDATA`, `FORCE_COLOR` and
`CLICOLOR_FORCE`, plus `HOME=tmp_path`, `NO_COLOR=1`, `LINES=50`, `COLUMNS=100`. Click sizes help
from the terminal and rich styles it; 100 keeps the longer name's `Usage:` line unwrapped. The
console script comes from `shutil.which`, skipped when absent, under the same-checkout guard at
`tests/app/test_fail_closed_startup.py:463-476`; the module form is `[sys.executable, "-m",
"hmc_mcp"]`. `cwd=tmp_path` rather than `-P` keeps `sys.path[0]` as an operator's launch has it,
shadowing nothing.

- **Command tree.** `--help` for the root and for `systems`, the representative subcommand. Each
  run must exit 0 and carry exactly one `Usage:` line. Those two lines share a prefix and a tail;
  the differing middle is the program name, replaced by one placeholder. The invoked command path
  must fall in the shared tail, so a diverged tree cannot normalise itself away. No other line is
  touched: `hmc-mcp` recurs in `--profile`'s help. Trailing whitespace is then stripped per line,
  as rich pads it; the normalised outputs must match.
- **Exit status.** An unknown root subcommand (parser-raised) and `serve` without
  `--access-policy` (raised in the command body); each must exit 2 under both forms.

Deferral carried: `sys.path[0]` shadowing coverage, to be filed as a follow-up issue.

## Success

1. The help arm fails when the two command trees differ anywhere but the program name.
2. The exit-status arm fails when the forms disagree on a bad argument's exit code.
3. Neither arm passes vacuously: each proves its runs reached the program.
4. Neither arm fails for the two non-identities ADR 0128:117-125 records as intended.
5. The module skips, rather than fails, where no console script is on `PATH`.

## Validation

- **Both arms bite** (success 1, 2). Mode: focused-test. Red, exit arm: `main` returning a status
  *and* `__main__.py` weakened to a bare `main()` — measured console 3, module 0. A bare `main()`
  alone does not: `app()` raises `SystemExit` in Click's standalone mode, so the ADR's named
  regression needs both halves. Red, help arm: an option added to the root command under one form.
  Green: `uv run --no-sync pytest tests/app/test_entry_point_equivalence.py --no-cov`, then
  `just verify`.
- **Not vacuous** (success 3). Mode: focused-test, same case. Red: `systems` renamed away, or
  `typer` unimportable — both forms then fail identically and empty; the exit-0, one-`Usage:`-line,
  shared-tail and exit-2 guards catch it.
- **Non-identities tolerated** (success 4). Mode: focused-test, same case. Red: dropping the
  normalisation; the raw help differs at HEAD. `sys.path[0]` needs none: no compared output
  reads it.
- **Skip path** (success 5). Mode: task-test-not-applicable. Reached only when `shutil.which`
  returns `None`; forcing that tests `shutil.which`. Idiom at
  `tests/app/test_fail_closed_startup.py:470-472`.
