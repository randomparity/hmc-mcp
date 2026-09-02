"""CLI commands for Virtual I/O Servers."""

from __future__ import annotations

import typer
from rich.table import Table

from ..jobs import validate_wait_timing
from ..operations.partition_state import PartitionState
from ..operations.vios import list_vios, power_vios
from .output import console, first_field, output, print_json
from .runtime import client, run, with_client


def vios_list(
    system: str | None = typer.Option(
        None, "--system", "-s", help="Restrict to this managed system name or UUID"
    ),
    state: PartitionState | None = typer.Option(
        None, "--state", help="Filter by PartitionState (server-side search)"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List Virtual I/O Servers."""

    vios = with_client(lambda hmc: list_vios(hmc, system, state))

    table = None
    if not as_json:
        table = Table(title="Virtual I/O Servers")
        for col in ("Name", "ID", "UUID", "State", "Version"):
            table.add_column(col)
        for v in vios:
            table.add_row(
                first_field(v, "PartitionName"),
                first_field(v, "PartitionID"),
                v.get("UUID") or "-",
                first_field(v, "PartitionState"),
                first_field(v, "IOSLevel", "VIOSVersion", default="-"),
            )
    output(vios, as_json, table, "No VIOS found")


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
        async with client() as hmc:
            return await power_vios(
                hmc,
                None,
                name_or_uuid,
                power_on=True,
                wait=wait,
                timeout_seconds=timeout,
                poll_interval=interval,
            )

    job = run(_go)

    console.print(f"[green]Submitted PowerOn for {name_or_uuid}[/green]")
    print_json(job)


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
        async with client() as hmc:
            return await power_vios(
                hmc,
                None,
                name_or_uuid,
                power_on=False,
                immediate=immediate,
                wait=wait,
                timeout_seconds=timeout,
                poll_interval=interval,
            )

    job = run(_go)

    console.print(f"[green]Submitted {op} for {name_or_uuid}[/green]")
    print_json(job)


def register_commands(group: typer.Typer) -> None:
    """Register this module’s commands on *group*."""
    group.command("list")(vios_list)
    group.command("power-on")(vios_power_on)
    group.command("power-off")(vios_power_off)
