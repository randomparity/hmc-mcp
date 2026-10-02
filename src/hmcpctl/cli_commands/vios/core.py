"""CLI commands for Virtual I/O Servers."""

from __future__ import annotations

from dataclasses import asdict

import typer

from ...jobs import validate_wait_timing
from ...operations.partition_state import PartitionState
from ...operations.vios.core import list_vios, power_vios
from ..output import (
    VerbatimTable,
    console,
    first_field,
    output,
    print_json,
    report_unreadable,
)
from ..runtime import with_client


def vios_list(
    system: str | None = typer.Option(
        None, "--system", "-s", help="Restrict to this managed system name or UUID"
    ),
    state: PartitionState | None = typer.Option(
        None, "--state", help="Filter by PartitionState"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List Virtual I/O Servers."""

    listing = with_client(lambda hmc: list_vios(hmc, system, state))
    report_unreadable(listing)

    table = None
    if not as_json:
        table = VerbatimTable(title="Virtual I/O Servers")
        for col in ("Name", "ID", "UUID", "State", "Version"):
            table.add_column(col)
        for v in listing.entries:
            table.add_row(
                first_field(v, "PartitionName"),
                first_field(v, "PartitionID"),
                v.get("UUID") or "-",
                first_field(v, "PartitionState"),
                first_field(v, "IOSLevel", "VIOSVersion", default="-"),
            )
    output(asdict(listing), as_json, table, "No VIOS found")


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
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """Power on a VIOS (submits a PowerOn job)."""
    validate_wait_timing(wait, timeout, interval)
    if not yes and not typer.confirm(f"Really PowerOn VIOS {name_or_uuid}?"):
        raise typer.Abort()

    job = with_client(
        lambda hmc: power_vios(
            hmc,
            name_or_uuid,
            system_name_or_uuid=system,
            power_on=True,
            wait=wait,
            timeout_seconds=timeout,
            poll_interval=interval,
        )
    )

    console.print(f"Submitted PowerOn for {name_or_uuid}", style="green", markup=False)
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
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """Power off a VIOS (submits a PowerOff job)."""
    validate_wait_timing(wait, timeout, interval)
    op = "Immediate PowerOff" if immediate else "PowerOff"
    if not yes and not typer.confirm(f"Really {op} VIOS {name_or_uuid}?"):
        raise typer.Abort()

    job = with_client(
        lambda hmc: power_vios(
            hmc,
            name_or_uuid,
            system_name_or_uuid=system,
            power_on=False,
            immediate=immediate,
            wait=wait,
            timeout_seconds=timeout,
            poll_interval=interval,
        )
    )

    console.print(f"Submitted {op} for {name_or_uuid}", style="green", markup=False)
    print_json(job)


def register_commands(group: typer.Typer) -> None:
    """Register this module’s commands on *group*."""
    group.command("list")(vios_list)
    group.command("power-on")(vios_power_on)
    group.command("power-off")(vios_power_off)
