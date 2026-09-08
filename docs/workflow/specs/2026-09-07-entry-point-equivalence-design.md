# `hmc-mcp` and `python -m hmc_mcp` are one program

Issue [#722](https://github.com/randomparity/hmc-mcp/issues/722).
Governed by [ADR 0128](../../adr/0128-l5-module-entry-point-launch.md); no new decision.

## Problem

ADR 0128:105-116 holds the two entry points equivalent by construction, not by a standing test —
and L5 now proves an audit-sink property through the module form.

## Scope

One new module, `tests/app/test_entry_point_equivalence.py`; no production change. Each case runs
both forms as subprocesses with `timeout=60` and `cwd=tmp_path`, under one environment:
`dict(os.environ)` less every `HMC_*` key, `XDG_CONFIG_HOME`, `APPDATA`, `FORCE_COLOR`,
`CLICOLOR_FORCE`, `TERMINAL_WIDTH`; plus `HOME=tmp_path`, `NO_COLOR=1`, `LINES=50`,
`COLUMNS=100`. Typer prefers `TERMINAL_WIDTH` to `COLUMNS`; 100 keeps the longer name's `Usage:`
line unwrapped. The console script comes from `shutil.which` under the same-checkout guard
at `tests/app/test_fail_closed_startup.py:463`; the module form is
`[sys.executable, "-m", "hmc_mcp"]`, in that same virtualenv. `cwd=tmp_path` rather than
`-P` keeps `sys.path[0]` operator-shaped, shadowing nothing.

- **Command tree.** `--help` for the root and for `systems`, one representative subcommand. Each
  run must exit 0, write nothing to stderr, and carry exactly one `Usage:` line. The two lines
  share a prefix and tail; the differing middle is the program name, replaced by a placeholder.
  The invoked command path must be a whole token in that tail, so a diverged tree cannot hide
  inside it. No other line is touched: `hmc-mcp` recurs in `--profile`'s help. SGR escapes and
  trailing whitespace come off every line — rich pads and styles what it renders, which
  `GITHUB_ACTIONS` forces on every leg. The normalised outputs must match.
- **Exit status.** An unknown root subcommand (parser-raised) and `serve` without
  `--access-policy` (command-body-raised); each must exit 2 under both forms.

Deferral carried: `sys.path[0]` shadowing coverage, owned by #725.

## Success

1. The help arm fails when the two command trees differ anywhere but the program name.
2. The exit-status arm fails when the forms disagree on a bad argument's exit code — reached
   only once both halves of the named regression land.
3. Neither arm passes vacuously: each proves its runs reached the program.
4. Neither arm fails for the two non-identities ADR 0128:117-125 records as intended.
5. The module skips, rather than fails, where no console script is on `PATH`.

## Validation

- **Both arms bite** (success 1, 2). Mode: focused-test. Red, exit arm: `main` returning a status
  *and* `__main__.py` weakened to a bare `main()` — measured console 3, module 0; a bare `main()`
  alone does not, because `app()` raises `SystemExit` itself, so the wrapper's deletion is
  invisible here. Red, help arm: an option added to the root command under one form, or a stderr
  banner from `__main__.py`. Green:
  `uv run --no-sync pytest tests/app/test_entry_point_equivalence.py --no-cov`, then `just verify`.
- **Not vacuous** (success 3). Mode: focused-test, same case. Red: `systems` renamed away, or
  `typer` unimportable — both forms fail identically and empty, caught by the exit-0,
  one-`Usage:`-line, whole-token and exit-2 guards.
- **Non-identities tolerated** (success 4). Mode: focused-test, same case. Red: dropping the
  normalisation, since the raw help differs at HEAD. `sys.path[0]` needs none.
- **Skip path** (success 5). Mode: task-test-not-applicable. Reached only when `shutil.which`
  returns `None`; forcing it tests `shutil.which` (`tests/app/test_fail_closed_startup.py:470`).
