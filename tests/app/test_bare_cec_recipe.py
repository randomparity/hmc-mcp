"""Structural checks for the bare-CEC LPAR recipe.

The parser follows PR #777's ISO recipe test; it is copied rather than imported
because that test is not on main.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import click
from typer.main import get_command

from hmcpctl import cli

ROOT = Path(__file__).resolve().parents[2]
RECIPE = ROOT / "docs" / "recipes" / "bare-cec-lpar.md"
RECIPE_LINK = "(recipes/bare-cec-lpar.md)"
SHELL_BLOCK = re.compile(r"```(?:bash|sh)\n(.*?)```", re.DOTALL)
SHELL_OPERATORS = ("|", ";", "&", "<", ">", "`", "$(")
IDENTITY_READBACK = ("lpars", "get-description")
DESTRUCTIVE = {("network", "unassign-dedicated-pcie-slot"), ("lpars", "delete")}
EXPECTED_COMMANDS = {
    ("config", "show"),
    ("jobs", "show"),
    ("lpars", "capture-console"),
    ("lpars", "create"),
    ("lpars", "delete"),
    ("lpars", "get-description"),
    ("lpars", "power-off"),
    ("lpars", "power-on"),
    ("lpars", "refcodes"),
    ("lpars", "show"),
    ("lpars", "state"),
    ("network", "assign-dedicated-pcie-slot"),
    ("network", "list-dedicated-pcie-slots"),
    ("network", "unassign-dedicated-pcie-slot"),
    ("systems", "show"),
}


def _logical_shell_lines(block: str) -> list[str]:
    lines: list[str] = []
    pending = ""
    for raw_line in block.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pending = f"{pending} {stripped}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        lines.append(pending)
        pending = ""
    assert not pending, "shell block ends with an unfinished continuation"
    return lines


def _recipe_lines(markdown: str) -> list[str]:
    return [
        line
        for block in SHELL_BLOCK.findall(markdown)
        for line in _logical_shell_lines(block)
    ]


def _recipe_commands(markdown: str) -> list[list[str]]:
    commands: list[list[str]] = []
    for line in _recipe_lines(markdown):
        tokens = shlex.split(line, comments=True, posix=True)
        if tokens and tokens[0] == "hmcpctl":
            commands.append(tokens)
    return commands


def _parse_command(tokens: list[str]) -> tuple[str, ...]:
    command = get_command(cli.app)
    context = click.Context(command, info_name="hmcpctl")
    remaining = tokens[1:]
    path: list[str] = []

    while hasattr(command, "resolve_command"):
        name, command, remaining = command.resolve_command(context, remaining)
        path.append(name)
        if hasattr(command, "resolve_command"):
            context = click.Context(command, info_name=name, parent=context)

    with command.make_context(path[-1], remaining, parent=context):
        pass
    return tuple(path)


def _recipe_paths() -> list[tuple[str, ...]]:
    commands = _recipe_commands(RECIPE.read_text(encoding="utf-8"))
    assert commands, "recipe has no hmcpctl commands in bash blocks"
    return [_parse_command(command) for command in commands]


def test_recipe_commands_match_the_installed_cli_contract() -> None:
    assert set(_recipe_paths()) == EXPECTED_COMMANDS


def test_every_hmcpctl_line_is_a_bare_command_the_parser_sees() -> None:
    for line in _recipe_lines(RECIPE.read_text(encoding="utf-8")):
        if "hmcpctl" not in line:
            continue
        assert line.startswith("hmcpctl "), line
        assert not any(operator in line for operator in SHELL_OPERATORS), line


def test_recipe_never_overrides_ownership() -> None:
    for command in _recipe_commands(RECIPE.read_text(encoding="utf-8")):
        assert "--ownership-override" not in command, command


def test_unassign_and_delete_directly_follow_the_identity_readback() -> None:
    paths = _recipe_paths()
    destructive = [index for index, path in enumerate(paths) if path in DESTRUCTIVE]

    assert {paths[index] for index in destructive} == DESTRUCTIVE
    for index in destructive:
        assert index > 0 and paths[index - 1] == IDENTITY_READBACK, paths[index]


def test_recipe_is_linked_from_both_cli_navigation_pages() -> None:
    for relative_path in ("docs/cli.md", "docs/index.md"):
        contents = (ROOT / relative_path).read_text(encoding="utf-8")
        assert contents.count(RECIPE_LINK) == 1, relative_path
