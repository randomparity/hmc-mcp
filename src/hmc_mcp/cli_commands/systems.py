"""CLI commands for managed systems."""

from __future__ import annotations

from dataclasses import asdict

import typer
from rich.table import Table

from ..jobs import validate_wait_timing
from ..operations.capacity import capacity_report, find_placement
from ..operations.composite import system_summary
from ..operations.health import fleet_health
from ..operations.systems import (
    get_system,
    list_systems,
    power_system,
)
from .runtime import _with_client
from .output import _first_field, _output, _print_json, console, err_console


def systems_health(
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
) -> None:
    """Show exception-only health across the managed estate."""

    result = asdict(_with_client(fleet_health))
    if as_json:
        _print_json(result)
        return
    if not any(result.values()):
        console.print("[green]No fleet health exceptions found[/green]")
    for category in ("systems", "vios", "lpars", "failed_jobs"):
        entries = result[category]
        if not entries:
            continue
        table = Table(title=category.replace("_", " ").title())
        columns = sorted({key for entry in entries for key in entry})
        for column in columns:
            table.add_column(column.replace("_", " ").title())
        for entry in entries:
            table.add_row(*(str(entry.get(column, "-")) for column in columns))
        console.print(table)
    for warning in result["warnings"]:
        err_console.print(f"[yellow]{warning}[/yellow]")


def systems_list(
    state: str | None = typer.Option(
        None, "--state", help="Filter by State (server-side search)"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List managed systems."""

    systems = _with_client(lambda hmc: list_systems(hmc, state))

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


def systems_show(
    name_or_uuid: str = typer.Argument(..., help="Managed system name or UUID"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show full details of one managed system (accepts name or UUID)."""
    system = _with_client(lambda hmc: get_system(hmc, name_or_uuid))

    if system is None:
        err_console.print(f"[yellow]System '{name_or_uuid}' not found[/yellow]")
        raise typer.Exit(code=1)
    _print_json(system)


def systems_power_on(
    name_or_uuid: str = typer.Argument(..., help="Managed system name or UUID"),
    wait: bool = typer.Option(
        False, "--wait/--no-wait", help="Wait for job completion"
    ),
    timeout: int = typer.Option(300, "--timeout", help="Seconds to wait (with --wait)"),
    interval: int = typer.Option(
        5, "--interval", help="Poll interval seconds (with --wait)"
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Power on a managed system (submits a PowerOn job)."""
    validate_wait_timing(wait, timeout, interval)
    if not yes and not typer.confirm(f"Really PowerOn system {name_or_uuid}?"):
        raise typer.Abort()

    job = _with_client(
        lambda hmc: power_system(
            hmc,
            name_or_uuid,
            power_on=True,
            wait=wait,
            timeout_seconds=timeout,
            poll_interval=interval,
        )
    )

    console.print(f"[green]Submitted PowerOn for {name_or_uuid}[/green]")
    _print_json(job)


def systems_power_off(
    name_or_uuid: str = typer.Argument(..., help="Managed system name or UUID"),
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
    """Power off a managed system (submits a PowerOff job)."""
    validate_wait_timing(wait, timeout, interval)
    op = "Immediate PowerOff" if immediate else "PowerOff"
    if not yes and not typer.confirm(f"Really {op} system {name_or_uuid}?"):
        raise typer.Abort()

    job = _with_client(
        lambda hmc: power_system(
            hmc,
            name_or_uuid,
            power_on=False,
            immediate=immediate,
            wait=wait,
            timeout_seconds=timeout,
            poll_interval=interval,
        )
    )

    console.print(f"[green]Submitted {op} for {name_or_uuid}[/green]")
    _print_json(job)


def systems_summary(
    name_or_uuid: str = typer.Argument(..., help="Managed system name or UUID"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """One-call summary: state, MTMS, firmware, LPAR counts, free memory/CPU, VIOS count."""

    result = asdict(_with_client(lambda hmc: system_summary(hmc, name_or_uuid)))
    if as_json:
        _print_json(result)
        return

    table = Table(title=f"System Summary: {result.get('name') or name_or_uuid}")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("UUID", result.get("uuid") or "-")
    table.add_row("State", result.get("state") or "-")
    table.add_row("MTMS", result.get("mtms") or "-")
    table.add_row("Firmware", result.get("firmware_version") or "-")
    table.add_row("Total Memory (MiB)", str(result.get("total_memory_mib", 0)))
    table.add_row("Free Memory (MiB)", str(result.get("free_memory_mib", 0)))
    table.add_row("Total Proc Units", str(result.get("total_proc_units", 0.0)))
    table.add_row("Free Proc Units", str(result.get("free_proc_units", 0.0)))
    table.add_row("Total LPARs", str(result.get("lpar_count", 0)))
    for state, count in sorted((result.get("lpar_states") or {}).items()):
        table.add_row(f"  LPARs ({state})", str(count))
    table.add_row("VIOS Count", str(result.get("vios_count", 0)))
    console.print(table)


def systems_capacity(
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """Capacity report: memory/CPU totals and free resources per managed system."""

    report = [asdict(item) for item in _with_client(capacity_report)]
    if as_json:
        _print_json(report)
        return
    if not report:
        err_console.print("[yellow]No managed systems found[/yellow]")
        return
    table = Table(title="System Capacity")
    for col in (
        "System",
        "UUID",
        "Total Mem (MiB)",
        "Assigned Mem (MiB)",
        "Free Mem (MiB)",
        "Total Procs",
        "Assigned Procs",
        "Free Procs",
        "Running LPARs",
        "Total LPARs",
    ):
        table.add_column(col)
    for r in report:
        table.add_row(
            r.get("system_name") or "-",
            r.get("system_uuid") or "-",
            str(r.get("total_memory_mib", 0)),
            str(r.get("assigned_memory_mib", 0)),
            str(r.get("free_memory_mib", 0)),
            str(r.get("total_proc_units", 0.0)),
            str(r.get("assigned_proc_units", 0.0)),
            str(r.get("free_proc_units", 0.0)),
            str(r.get("running_lpars", 0)),
            str(r.get("total_lpars", 0)),
        )
    _output(report, as_json=False, table=table, empty_msg="No managed systems found")


def systems_find_placement(
    memory: int = typer.Argument(..., help="Desired memory in MiB"),
    procs: float = typer.Option(0.5, "--procs", help="Desired processor units"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """Find managed systems with enough free resources for a new LPAR."""

    candidates = [
        asdict(item)
        for item in _with_client(lambda hmc: find_placement(hmc, memory, procs))
    ]
    if as_json:
        _print_json(candidates)
        return
    if not candidates:
        err_console.print("[yellow]No systems with sufficient free capacity[/yellow]")
        return
    table = Table(title="Placement Candidates (sorted by free memory)")
    for col in ("System", "UUID", "Free Mem (MiB)", "Free Procs", "Running LPARs"):
        table.add_column(col)
    for r in candidates:
        table.add_row(
            r.get("system_name") or "-",
            r.get("system_uuid") or "-",
            str(r.get("free_memory_mib", 0)),
            str(r.get("free_proc_units", 0.0)),
            str(r.get("running_lpars", 0)),
        )
    _output(candidates, as_json=False, table=table, empty_msg="No placement candidates")


def register_commands(group: typer.Typer) -> None:
    """Register this module’s commands on *group*."""
    group.command("health")(systems_health)
    group.command("list")(systems_list)
    group.command("show")(systems_show)
    group.command("power-on")(systems_power_on)
    group.command("power-off")(systems_power_off)
    group.command("summary")(systems_summary)
    group.command("capacity")(systems_capacity)
    group.command("find-placement")(systems_find_placement)
