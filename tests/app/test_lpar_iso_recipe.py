"""Structural checks for the LPAR ISO installation recipe."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import click
from typer.main import get_command

from hmcpctl import cli

ROOT = Path(__file__).resolve().parents[2]
RECIPE = ROOT / "docs" / "recipes" / "lpar-iso-install.md"
RECIPE_LINK = "(recipes/lpar-iso-install.md)"
SHELL_BLOCK = re.compile(r"```(?:bash|sh)\n(.*?)```", re.DOTALL)
NUMERIC_VALUES = {"$VLAN_ID": "100"}
EXPECTED_COMMANDS = {
    ("adapters", "add-network"),
    ("adapters", "list"),
    ("config", "show"),
    ("config", "list"),
    ("console", "info"),
    ("jobs", "show"),
    ("lpars", "capture-console"),
    ("lpars", "create"),
    ("lpars", "delete"),
    ("lpars", "list"),
    ("lpars", "power-off"),
    ("lpars", "power-on"),
    ("lpars", "show"),
    ("lpars", "state"),
    ("network", "list-networks"),
    ("storage", "create-disk"),
    ("storage", "create-media-repo"),
    ("storage", "delete-disk"),
    ("storage", "delete-media"),
    ("storage", "delete-media-repo"),
    ("storage", "detach-mapping"),
    ("storage", "get-media-repo"),
    ("storage", "list-mappings"),
    ("storage", "list-optical-media"),
    ("storage", "list-vgs"),
    ("storage", "map"),
    ("storage", "mount-optical-media"),
    ("storage", "unmount-optical-media"),
    ("storage", "upload-iso"),
    ("systems", "show"),
    ("vios", "list"),
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


def _recipe_commands(markdown: str) -> list[list[str]]:
    commands: list[list[str]] = []
    for block in SHELL_BLOCK.findall(markdown):
        for line in _logical_shell_lines(block):
            tokens = shlex.split(line, comments=True, posix=True)
            if tokens and tokens[0] == "hmcpctl":
                commands.append([NUMERIC_VALUES.get(token, token) for token in tokens])
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


def test_recipe_commands_match_the_installed_cli_contract() -> None:
    markdown = RECIPE.read_text(encoding="utf-8")
    commands = _recipe_commands(markdown)

    assert commands, "recipe has no hmcpctl commands in shell blocks"
    assert {_parse_command(command) for command in commands} == EXPECTED_COMMANDS


def test_every_hmcpctl_line_is_a_bare_command_the_parser_sees() -> None:
    for block in SHELL_BLOCK.findall(RECIPE.read_text(encoding="utf-8")):
        for line in _logical_shell_lines(block):
            if "hmcpctl" in line:
                assert line.startswith("hmcpctl "), line


def test_recipe_never_overrides_ownership() -> None:
    for command in _recipe_commands(RECIPE.read_text(encoding="utf-8")):
        assert "--ownership-override" not in command, command


def test_recipe_is_linked_from_both_cli_navigation_pages() -> None:
    for relative_path in ("docs/cli.md", "docs/index.md"):
        contents = (ROOT / relative_path).read_text(encoding="utf-8")
        assert contents.count(RECIPE_LINK) == 1, relative_path
