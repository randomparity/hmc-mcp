"""CLI commands for the HMC itself (console info).
"""

from __future__ import annotations

import json

import typer

from .app import (
    _print_json,
    _resource,
    _with_client,
    console,
    console_app,
    err_console,
)



@console_app.command("info")
def console_info(as_json: bool = typer.Option(False, "--json")) -> None:
    """Show HMC version and network info (connectivity check)."""

    info = _with_client(lambda hmc: hmc.get_console_info())

    if info is None:
        err_console.print("[yellow]No ManagementConsole data returned[/yellow]")
        return
    if as_json:
        _print_json(info)
        return
    res = _resource(info)
    console.print(f"[bold]HMC[/bold] {info.get('link') or ''}")
    for key in ("VersionInfo", "ManagementConsoleName", "MachineTypeModelSerialNumber", "NetworkInfo"):
        if key in res:
            console.print(f"  {key}: {json.dumps(res[key], default=str)}")


