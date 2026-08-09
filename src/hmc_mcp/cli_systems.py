"""CLI commands for managed systems.
"""

from __future__ import annotations


import typer
from rich.table import Table

from .cli_app import (
    _first_field,
    _output,
    _print_json,
    _with_client,
    _resolve_system_uuid,
    console,
    err_console,
    systems_app,
)
from .server_system import hmc_capacity_report, hmc_find_placement


@systems_app.command("list")
def systems_list(as_json: bool = typer.Option(False, "--json")) -> None:
    """List managed systems."""

    systems = _with_client(lambda hmc: hmc.list_managed_systems())

    table = None
    if not as_json:
        table = Table(title="Managed Systems")
        for col in ("Name", "UUID", "State", "MTMS", "IP Address"):
            table.add_column(col)
        for s in systems:
            table.add_row(
                _first_field(s, "SystemName"),
                s.get("UUID") or "-",
                _first_field(s, "State"),
                _first_field(s, "MachineTypeModelSerialNumber", "MTMS"),
                _first_field(s, "IPAddress", "PrimaryIPAddress"),
            )
    _output(systems, as_json, table, "No managed systems found")


@systems_app.command("show")
def systems_show(
    name_or_uuid: str = typer.Argument(..., help="Managed system UUID or SystemName"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show full details of one managed system (accepts name or UUID)."""

    async def _get(hmc):
        resolved = await _resolve_system_uuid(hmc, name_or_uuid)
        if resolved is None:
            return None
        return await hmc.get_managed_system(resolved)

    system = _with_client(_get)

    if system is None:
        err_console.print(f"[yellow]System {name_or_uuid!r} not found[/yellow]")
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

    job = _with_client(lambda hmc: hmc.power_on_system(uuid))

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

    job = _with_client(lambda hmc: hmc.power_off_system(uuid, immediate=immediate))

    console.print(f"[green]Submitted {op} for {uuid}[/green]")
    _print_json(job)


@systems_app.command("capacity")
def systems_capacity() -> None:
    """Report per-system memory and CPU capacity (best-effort from LPAR desired resources)."""

    report = hmc_capacity_report()
    if not report:
        console.print("[yellow]No managed systems found[/yellow]")
        return
    table = Table(title="Capacity Report")
    for col in ("System", "State", "Mem Total", "Mem Assigned", "Mem Free",
                "Proc Total", "Proc Assigned", "LPARs (running/total)"):
        table.add_column(col)
    for row in report:
        table.add_row(
            row["system_name"],
            row["state"],
            str(row["total_memory_mb"]),
            str(row["assigned_memory_mb"]),
            str(row["free_memory_mb"]),
            str(row["total_proc_units"]),
            str(row["assigned_proc_units"]),
            f"{row['running_lpars']}/{row['total_lpars']}",
        )
    console.print(table)


@systems_app.command("find-placement")
def systems_find_placement(
    memory: int = typer.Argument(..., help="Required memory in MiB (e.g. 4096)"),
    procs: float = typer.Option(0.5, "--procs", "-p", help="Required processing units"),
) -> None:
    """Find systems with enough free memory and CPU to host a new LPAR."""

    candidates = hmc_find_placement(desired_memory_mb=memory, desired_proc_units=procs)
    if not candidates:
        console.print(
            f"[yellow]No system has >= {memory} MiB free and >= {procs} proc units available[/yellow]"
        )
        return
    table = Table(title=f"Placement candidates for {memory} MiB / {procs} proc units")
    for col in ("System", "State", "Free Mem (MiB)", "Free Proc", "LPARs (running/total)"):
        table.add_column(col)
    for row in candidates:
        free_procs = round(row["total_proc_units"] - row["assigned_proc_units"], 2)
        table.add_row(
            row["system_name"],
            row["state"],
            str(row["free_memory_mb"]),
            str(free_procs),
            f"{row['running_lpars']}/{row['total_lpars']}",
        )
    console.print(table)
