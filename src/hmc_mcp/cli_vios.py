"""CLI commands for Virtual I/O Servers.
"""

from __future__ import annotations


import typer
from rich.table import Table

from .cli_app import (
    _first_field,
    _output,
    _print_json,
    _run,
    _client,
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
                _first_field(v, "PartitionName"),
                _first_field(v, "PartitionID"),
                v.get("UUID") or "-",
                _first_field(v, "PartitionState"),
                _first_field(v, "IOSLevel", "VIOSVersion", default="-"),
            )
    _output(vios, as_json, table, "No VIOS found")


@vios_app.command("power-on")
def vios_power_on(
    uuid: str = typer.Argument(..., help="VIOS UUID"),
    wait: bool = typer.Option(False, "--wait/--no-wait", help="Wait for job completion"),
    timeout: int = typer.Option(300, "--timeout", help="Seconds to wait (with --wait)"),
    interval: int = typer.Option(5, "--interval", help="Poll interval seconds (with --wait)"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Power on a VIOS (submits a PowerOn job)."""
    if not yes and not typer.confirm(f"Really PowerOn VIOS {uuid}?"):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            job = await hmc.power_on_vios(uuid)
            if wait and job is not None:
                job_uuid = job.get("UUID") or (job.get("Resource") or {}).get("JobID")
                if job_uuid:
                    job = await hmc.wait_for_job(job_uuid, timeout, interval)
            return job
    job = _run(_go)

    console.print(f"[green]Submitted PowerOn for {uuid}[/green]")
    _print_json(job)


@vios_app.command("power-off")
def vios_power_off(
    uuid: str = typer.Argument(..., help="VIOS UUID"),
    immediate: bool = typer.Option(False, "--immediate"),
    wait: bool = typer.Option(False, "--wait/--no-wait", help="Wait for job completion"),
    timeout: int = typer.Option(300, "--timeout", help="Seconds to wait (with --wait)"),
    interval: int = typer.Option(5, "--interval", help="Poll interval seconds (with --wait)"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Power off a VIOS (submits a PowerOff job)."""
    op = "Immediate PowerOff" if immediate else "PowerOff"
    if not yes and not typer.confirm(f"Really {op} VIOS {uuid}?"):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            job = await hmc.power_off_vios(uuid, immediate=immediate)
            if wait and job is not None:
                job_uuid = job.get("UUID") or (job.get("Resource") or {}).get("JobID")
                if job_uuid:
                    job = await hmc.wait_for_job(job_uuid, timeout, interval)
            return job
    job = _run(_go)

    console.print(f"[green]Submitted {op} for {uuid}[/green]")
    _print_json(job)


