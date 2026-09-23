"""CLI commands for the HMC itself (console info)."""

from __future__ import annotations

import json

import typer

from .output import _resource, console, err_console, print_json
from .runtime import with_client


def console_info(as_json: bool = typer.Option(False, "--json")) -> None:
    """Show HMC version and network info (connectivity check)."""

    info = with_client(lambda hmc: hmc.get_console_info())

    if info is None:
        err_console.print("[yellow]No ManagementConsole data returned[/yellow]")
        return
    if as_json:
        print_json(info)
        return
    res = _resource(info)
    console.print(f"[bold]HMC[/bold] {info.get('link') or ''}")
    for key in (
        "VersionInfo",
        "ManagementConsoleName",
        "MachineTypeModelSerialNumber",
        "NetworkInfo",
    ):
        if key in res:
            console.print(f"  {key}: {json.dumps(res[key], default=str)}")


def register_commands(group: typer.Typer) -> None:
    """Register this module’s commands on *group*."""
    group.command("info")(console_info)
