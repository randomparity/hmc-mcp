"""CLI commands for Virtual I/O Servers.
"""

from __future__ import annotations


import typer
from rich.table import Table

from .cli_app import (
    _g,
    _output,
    _print_json,
    _with_client,
    console,
    vios_app,
)


@vios_app.command("list")
def vios_list(
    system: str | None = typer.Option(None, "--system", "-s", help="Restrict to this managed system UUID"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List Virtual I/O Servers."""

    vios = _with_client(lambda hmc: hmc.list_vios(system))

    table = None
    if not as_json:
        table = Table(title="Virtual I/O Servers")
        for col in ("Name", "ID", "UUID", "State", "Version"):
            table.add_column(col)
        for v in vios:
            table.add_row(
                _g(v, "PartitionName"),
                _g(v, "PartitionID"),
                v.get("UUID") or "-",
                _g(v, "PartitionState"),
                _g(v, "IOSLevel", "VIOSVersion", default="-"),
            )
    _output(vios, as_json, table, "No VIOS found")


@vios_app.command("power-on")
def vios_power_on(
    uuid: str = typer.Argument(..., help="VIOS UUID"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Power on a VIOS (submits a PowerOn job)."""
    if not yes and not typer.confirm(f"Really PowerOn VIOS {uuid}?"):
        raise typer.Abort()

    job = _with_client(lambda hmc: hmc.power_on_vios(uuid))

    console.print(f"[green]Submitted PowerOn for {uuid}[/green]")
    _print_json(job)


@vios_app.command("power-off")
def vios_power_off(
    uuid: str = typer.Argument(..., help="VIOS UUID"),
    immediate: bool = typer.Option(False, "--immediate"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Power off a VIOS (submits a PowerOff job)."""
    op = "Immediate PowerOff" if immediate else "PowerOff"
    if not yes and not typer.confirm(f"Really {op} VIOS {uuid}?"):
        raise typer.Abort()

    job = _with_client(lambda hmc: hmc.power_off_vios(uuid, immediate=immediate))

    console.print(f"[green]Submitted {op} for {uuid}[/green]")
    _print_json(job)


