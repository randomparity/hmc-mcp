"""Render ``--help`` for every CLI group the Typer app registers.

`just verify` used to name six of the fifteen groups by hand, so nine of them had
their help pages rendered by nothing. The gap is narrower than a load check: the
root ``hmc-mcp --help`` already builds the whole command tree, so a group whose
module fails to import, or whose parameters Typer cannot convert, fails there
already. What only ``<group> --help`` renders is that group's own page -- its
commands' short helps and its option panel -- and Typer's default
``rich_markup_mode="rich"`` turns a stray tag in any of those strings into a
``MarkupError`` raised at render time, in that group alone.

The list comes from the built command tree rather than from this file, so
registering a group in ``cli_app`` is all it takes to have it smoke-loaded.
Rendering happens in-process: fifteen subprocesses would cost about fifteen
seconds per run, and `just verify` invokes the installed ``hmc-mcp`` console
script for the root help alongside this script, so the entry point stays covered.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from collections.abc import Iterator

import typer
from typer.testing import CliRunner

from hmc_mcp.cli import app


def _subgroups(command: object, prefix: tuple[str, ...]) -> Iterator[tuple[str, ...]]:
    """Yield the path of every sub-group under *command*, depth first.

    Sub-groups are told apart from plain commands by carrying a command table of
    their own -- ``isinstance`` against ``click.Group`` is wrong here, because
    Typer's classes derive from its own vendored click shim rather than from the
    installed click.
    """
    for name, sub in getattr(command, "commands", {}).items():
        if hasattr(sub, "commands"):
            path = (*prefix, name)
            yield path
            yield from _subgroups(sub, path)


def group_paths(cli: typer.Typer) -> list[tuple[str, ...]]:
    """Every group of *cli*, as the words an operator would type to reach it.

    Read off the built command tree, not ``cli.registered_groups``: that registry
    holds only the groups added to *this* app, and a group added without an
    explicit ``name`` is named by Typer while the tree is built. The tree has the
    resolved names and the nested groups both, so a group nested under another is
    smoke-loaded without anyone noticing it has to be.
    """
    return sorted(_subgroups(typer.main.get_command(cli), ()))


def _help_problem(
    runner: CliRunner, cli: typer.Typer, path: tuple[str, ...]
) -> str | None:
    """Render ``<path> --help``, returning a report when it fails."""
    result = runner.invoke(cli, [*path, "--help"])
    if result.exit_code == 0:
        return None
    detail = result.output.strip()
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        detail = "".join(traceback.format_exception(result.exception)).strip()
    return f"{' '.join(path)} --help exited {result.exit_code}\n{detail}"


def smoke(cli: typer.Typer) -> tuple[list[tuple[str, ...]], list[str]]:
    """Smoke-load every group's help, returning the paths and any problems.

    An app with no groups is reported as a problem rather than passing on an
    empty loop -- a derived guard that silently covers nothing is worse than the
    hand-written list it replaced.
    """
    paths = group_paths(cli)
    if not paths:
        return paths, ["no CLI groups are registered; nothing was smoke-loaded"]
    runner = CliRunner()
    problems = [_help_problem(runner, cli, path) for path in paths]
    return paths, [problem for problem in problems if problem is not None]


def main(args: list[str] | None = None) -> int:
    """Render every registered group's help, reporting the failures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(args)
    paths, problems = smoke(app)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print(f"Rendered --help for {len(paths)} CLI groups.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
