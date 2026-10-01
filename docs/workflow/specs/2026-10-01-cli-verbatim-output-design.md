# CLI verbatim output — design (#1029)

## Problem

`cli_commands/output.py` builds a markup- and emoji-enabled Rich `Console`. A `[tag]` or
`:emoji:` inside an HMC value or CLI argument that reaches `console.print` or a table
title, header or cell is rewritten or dropped. #965 fixed six sites; about 100 remain.

## Scope

Surface: `src/hmcpctl/cli_commands/**` except `lpar/profiles.py` (#1248), `tests/`,
`CHANGELOG.md`. Owner of the rule: `output.py`.

- Both consoles get `emoji=False`; no CLI string uses an emoji code.
- `output.VerbatimTable(rich.table.Table)` wraps every `str` title, caption, column header and
  row cell in `rich.text.Text`, which Rich never parses as markup. Every
  `cli_commands/` table import moves to it; `output()` takes `VerbatimTable | None`.
- A print that interpolates passes `markup=False` (colour via `style=`); mixed styling
  uses `Text.assemble`. No value goes through `escape()`, which doubles a trailing
  backslash. `raw get`/`raw post` write the body with `typer.echo(color=True)`.
- Guard: an AST test over `cli_commands/**/*.py`. A markup-parsing `console`/`err_console`
  call without `markup=False` may only take literals, `Text(...)`/`Text.assemble(...)`,
  a conditional or `or` of those, or a name its function binds once to
  `VerbatimTable(...)`; `escape()` is tolerated only in `profiles.py` (#1248). No
  `from_markup`/`render`; only `output.py` imports from `rich` beyond `rich.text`.
- Decision record: [ADR 0187](../../adr/0187-cli-markup-verbatim-output.md) (per-site
  `markup=False` plus a shared table, over a global `markup=False`).

### Failure model

1. Actors and deployments: a local operator running `hmcpctl` in a terminal or script,
   against an HMC that controls the names and bodies it returns.
2. Invariants and assets at stake: displayed text equals the value acted on or
   returned. No HMC request changes.
3. Accepted failure classes: outside `raw`, highlighting still colours numbers (characters
   unchanged); `--json` output is excluded by the charter.
4. Covered elsewhere: `set-boot-order` output (#1248); `require_command_safe_text` and
   MCP tool output (unowned exclusions).

## Success

1. Each issue-listed site prints a `[bold]x[/bold]`-bearing value verbatim: resource-group
   scores, `raw get`, `raw post`, `console info` (link and JSON fields), `lpars state`, the
   set-description/set-msp/set-proc-compat confirmations, `pcie verify`, and
   `memory-pools remove`.
2. A `VerbatimTable` title, header and cell holding `[bold]x` or `:smile:` render
   verbatim, including end to end through `systems list`.
3. The guard passes on the branch and flags a reintroduced interpolated site.

## Validation

- Issue-listed sites. Mode: focused-test. `tests/app/test_cli_verbatim_output.py`,
  parametrized over Success 1 through `CliRunner`. Red: the `[bold]` segment is missing.
  Green: `uv run --no-sync pytest tests/app/test_cli_verbatim_output.py -q`.
- Verbatim table and emoji. Mode: focused-test. A recorded `VerbatimTable` render plus
  `systems list` with a bracketed name. Red: the text is dropped. Green: same command.
- Guard. Mode: focused-test. The guard runs over the tree, and a self-test checks that
  it flags an interpolated snippet. Red: the current tree is flagged. Green: same command.
- CHANGELOG entry. Mode: task-test-not-applicable; prose with no executable consumer.
