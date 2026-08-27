"""CLI commands for Virtual I/O Servers."""

from __future__ import annotations


import typer
from rich.table import Table

from .app import (
    _first_field,
    _output,
    _print_json,
    _run,
    _client,
    _with_client,
    console,
    vios_app,
)
from ..jobs import validate_wait_timing
from ..operations.vios import power_vios
from ..operations.lpar import PartitionState


@vios_app.command("list")
def vios_list(
    system: str | None = typer.Option(
        None, "--system", "-s", help="Restrict to this managed system UUID"
    ),
    state: PartitionState | None = typer.Option(
        None, "--state", help="Filter by PartitionState (server-side search)"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List Virtual I/O Servers."""

    if state is not None:
        vios = _with_client(
            lambda hmc: hmc.search_uom("VirtualIOServer", "PartitionState", state)
        )
    else:
        vios = _with_client(lambda hmc: hmc.list_vios(system))

    table = None
    if not as_json:
        table = Table(title="Virtual I/O Servers")
        for col in ("Name", "ID", "UUID", "State", "Version"):
            table.add_column(col)
        for v in vios:
            table.add_row(
                _first_field(v, "PartitionName"),
                _first_field(v, "PartitionID"),
                v.get("UUID") or "-",
                _first_field(v, "PartitionState"),
                _first_field(v, "IOSLevel", "VIOSVersion", default="-"),
            )
    _output(vios, as_json, table, "No VIOS found")


@vios_app.command("power-on")
def vios_power_on(
    name_or_uuid: str = typer.Argument(..., help="VIOS name or UUID"),
    wait: bool = typer.Option(
        False, "--wait/--no-wait", help="Wait for job completion"
    ),
    timeout: int = typer.Option(300, "--timeout", help="Seconds to wait (with --wait)"),
    interval: int = typer.Option(
        5, "--interval", help="Poll interval seconds (with --wait)"
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Power on a VIOS (submits a PowerOn job)."""
    validate_wait_timing(wait, timeout, interval)
    if not yes and not typer.confirm(f"Really PowerOn VIOS {name_or_uuid}?"):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await power_vios(
                hmc,
                None,
                name_or_uuid,
                on=True,
                wait=wait,
                timeout_seconds=timeout,
                poll_interval=interval,
            )

    job = _run(_go)

    console.print(f"[green]Submitted PowerOn for {name_or_uuid}[/green]")
    _print_json(job)


@vios_app.command("power-off")
def vios_power_off(
    name_or_uuid: str = typer.Argument(..., help="VIOS name or UUID"),
    immediate: bool = typer.Option(False, "--immediate"),
    wait: bool = typer.Option(
        False, "--wait/--no-wait", help="Wait for job completion"
    ),
    timeout: int = typer.Option(300, "--timeout", help="Seconds to wait (with --wait)"),
    interval: int = typer.Option(
        5, "--interval", help="Poll interval seconds (with --wait)"
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Power off a VIOS (submits a PowerOff job)."""
    validate_wait_timing(wait, timeout, interval)
    op = "Immediate PowerOff" if immediate else "PowerOff"
    if not yes and not typer.confirm(f"Really {op} VIOS {name_or_uuid}?"):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await power_vios(
                hmc,
                None,
                name_or_uuid,
                on=False,
                immediate=immediate,
                wait=wait,
                timeout_seconds=timeout,
                poll_interval=interval,
            )

    job = _run(_go)

    console.print(f"[green]Submitted {op} for {name_or_uuid}[/green]")
    _print_json(job)
