"""Structural checks for the LPAR ISO installation recipe."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import click
from typer.main import get_command

from hmc_mcp import cli

ROOT = Path(__file__).resolve().parents[2]
RECIPE = ROOT / "docs" / "recipes" / "lpar-iso-install.md"
RECIPE_LINK = "(recipes/lpar-iso-install.md)"
SHELL_BLOCK = re.compile(r"```(?:bash|sh)\n(.*?)```", re.DOTALL)
NUMERIC_VALUES = {
    "$MIN_MEMORY_MIB": "4096",
    "$MEMORY_MIB": "8192",
    "$MAX_MEMORY_MIB": "16384",
    "$PROCESSING_UNITS": "2.0",
    "$VCPUS": "2",
    "$VLAN_ID": "100",
    "$VIRTUAL_SWITCH_ID": "0",
    "$VIOS_PARTITION_ID": "2",
    "$VIOS_SLOT": "20",
    "$VIOS_ID": "2",
    "$DISK_CAPACITY_MIB": "51200",
    "$MEDIA_REPO_SIZE_MIB": "20480",
    "$POWER_TIMEOUT_SECONDS": "900",
    "$POLL_INTERVAL_SECONDS": "5",
    "$CAPTURE_SECONDS": "30",
    "$CAPTURE_BYTES": "65536",
    "$IDLE_TIMEOUT_SECONDS": "10",
}
EXPECTED_COMMANDS = {
    ("adapters", "add-network"),
    ("adapters", "add-vscsi"),
    ("adapters", "list"),
    ("config", "show"),
    ("config", "list"),
    ("console", "info"),
    ("jobs", "show"),
    ("lpars", "capture-console"),
    ("lpars", "clear-boot-order"),
    ("lpars", "create"),
    ("lpars", "delete"),
    ("lpars", "get-description"),
    ("lpars", "list"),
    ("lpars", "power-off"),
    ("lpars", "power-on"),
    ("lpars", "read-boot-order"),
    ("lpars", "set-boot-order"),
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
    ("systems", "list"),
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
            if tokens and tokens[0] == "hmc-mcp":
                commands.append([NUMERIC_VALUES.get(token, token) for token in tokens])
    return commands


def _parse_command(tokens: list[str]) -> tuple[str, ...]:
    command = get_command(cli.app)
    context = click.Context(command, info_name="hmc-mcp")
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

    assert commands, "recipe has no hmc-mcp commands in shell blocks"
    assert {_parse_command(command) for command in commands} == EXPECTED_COMMANDS


def test_recipe_is_linked_from_both_cli_navigation_pages() -> None:
    for relative_path in ("docs/cli.md", "docs/index.md"):
        contents = (ROOT / relative_path).read_text(encoding="utf-8")
        assert contents.count(RECIPE_LINK) == 1, relative_path
