# `hmc-mcp` and `python -m hmc_mcp` are one program

Issue [#722](https://github.com/randomparity/hmc-mcp/issues/722).
Governed by [ADR 0128](../../adr/0128-l5-module-entry-point-launch.md); no new decision.

## Problem

`pyproject.toml:32` binds `hmc-mcp` to `hmc_mcp:main`; `src/hmc_mcp/__main__.py` reaches the same
`main` through `raise SystemExit(main())`. ADR 0128:105-116 holds the two equivalent "by
construction, not by a standing test", and L5 now proves an audit-sink property *through* the
module form. Were they to diverge, L5 would stay green while proving it about a program no operator
runs. Missing coverage, not a regression.

## Scope

One new module, `tests/app/test_entry_point_equivalence.py`. No production change.

Both forms run as subprocesses from one environment per case: `dict(os.environ)` less every `HMC_*`
key, `XDG_CONFIG_HOME` and `APPDATA`, `HOME` steered to `tmp_path`, `COLUMNS` and `LINES` fixed —
Click sizes help text from the terminal, so inherited geometry would fail the comparison for an
unrelated reason. The console script is located with `shutil.which`, skipped when absent, under the
same-checkout guard from `tests/app/test_fail_closed_startup.py:463-476`.

- **Command tree.** `--help` for the root and for `systems`, the representative subcommand. Each
  form's program name is read out of its own `Usage:` line and replaced by one shared placeholder —
  an opaque string normalised away, never asserted as a mechanism — then trailing whitespace is
  stripped per line, because rich pads that line to the full width and the two names differ in
  length. The normalised outputs must be equal.
- **Exit status.** An unknown root subcommand, and `serve` without `--access-policy`, under
  both forms; the exit codes must be equal.

Out of scope per the frozen charter: editing `__main__.py`; asserting Typer/Click internals;
byte-identical help; a per-subcommand sweep; Windows `argv[0]` rewrites; and `sys.path[0]`
shadowing, deferred to a follow-up issue.

## Success

1. The help arm fails when the two command trees differ anywhere but the program name.
2. The exit-status arm fails when the forms disagree on a bad argument's exit code.
3. Neither arm fails for the two non-identities ADR 0128:117-125 records as intended.
4. The module skips, rather than fails, where no console script is on `PATH`.
5. `just verify` and `uv run --no-sync prek run --all-files` are green.

## Validation

- **Both arms bite** (success 1, 2). Mode: focused-test. Case
  `tests/app/test_entry_point_equivalence.py`. Red, exit arm: `main` returning a status *and*
  `__main__.py` weakened to a bare `main()` — measured console 3, module 0. A bare `main()`
  alone does not diverge: `app()` raises `SystemExit` in Click's standalone mode, so ADR
  0128's named regression needs both halves. Red, help arm: an option added to the root
  command under one form only. Green:
  `uv run --no-sync pytest tests/app/test_entry_point_equivalence.py`.
- **Intended non-identities tolerated** (success 3). Mode: focused-test, the same case. Red:
  dropping the normalisation; the raw help differs at HEAD, where both are already live.
  Green: as above.
- **Skip path** (success 4). Mode: task-test-not-applicable. The branch is reached only when
  `shutil.which` returns `None`, which no environment this repository tests in produces; forcing it
  would exercise `shutil.which`. Idiom: `tests/app/test_fail_closed_startup.py:470-472`.
- **Guardrails** (success 5). Mode: task-test-not-applicable. Repository gates; a test
  asserting they pass would only re-run them.
