"""CLI commands for managed systems.
"""

from __future__ import annotations


import typer
from rich.table import Table

from .cli_app import (
    _client,
    _g,
    _output,
    _print_json,
    _run,
    console,
    err_console,
    systems_app,
)



@systems_app.command("list")
def systems_list(as_json: bool = typer.Option(False, "--json")) -> None:
    """List managed systems."""

    async def _go():
        async with _client() as hmc:
            return await hmc.list_managed_systems()

    systems = _run(_go)

    table = None
    if not as_json:
        table = Table(title="Managed Systems")
        for col in ("Name", "UUID", "State", "MTMS", "IP Address"):
            table.add_column(col)
        for s in systems:
            table.add_row(
                _g(s, "SystemName"),
                s.get("UUID") or "-",
                _g(s, "State"),
                _g(s, "MachineTypeModelSerialNumber", "MTMS"),
                _g(s, "IPAddress", "PrimaryIPAddress"),
            )
    _output(systems, as_json, table, "No managed systems found")


@systems_app.command("show")
def systems_show(uuid: str = typer.Argument(..., help="Managed system UUID"),
                 as_json: bool = typer.Option(False, "--json")) -> None:
    """Show full details of one managed system."""

    async def _go():
        async with _client() as hmc:
            return await hmc.get_managed_system(uuid)

    system = _run(_go)

    if system is None:
        err_console.print(f"[yellow]System {uuid} not found[/yellow]")
        raise typer.Exit(code=1)
    _print_json(system)


@systems_app.command("power-on")
def systems_power_on(
    uuid: str = typer.Argument(..., help="Managed system UUID"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Power on a managed system (submits a PowerOn job)."""
    if not yes and not typer.confirm(f"Really PowerOn system {uuid}?"):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await hmc.power_on_system(uuid)

    job = _run(_go)

    console.print(f"[green]Submitted PowerOn for {uuid}[/green]")
    _print_json(job)


@systems_app.command("power-off")
def systems_power_off(
    uuid: str = typer.Argument(..., help="Managed system UUID"),
    immediate: bool = typer.Option(False, "--immediate"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Power off a managed system (submits a PowerOff job)."""
    op = "Immediate PowerOff" if immediate else "PowerOff"
    if not yes and not typer.confirm(f"Really {op} system {uuid}?"):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await hmc.power_off_system(uuid, immediate=immediate)

    job = _run(_go)

    console.print(f"[green]Submitted {op} for {uuid}[/green]")
    _print_json(job)


