"""LPAR inventory CLI commands."""

from __future__ import annotations

from dataclasses import asdict

import typer
from rich.table import Table

from ..operations.composite import lpar_summary
from ..operations.lpar.core import (
    PartitionState,
    get_lpar,
    get_lpar_state,
    list_lpars,
)
from .runtime import _client, _run, _with_client
from .output import _first_field, _output, _partition_not_found, _print_json, console


def lpars_summary(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """One-call summary: state, RMC, memory/CPU, OS details, adapter count, description."""

    async def _go():
        async with _client() as hmc:
            return await lpar_summary(hmc, None, name_or_uuid)

    summary = asdict(_run(_go))

    if as_json:
        _print_json(summary)
        return

    table = Table(title=f"LPAR Summary: {summary.get('name') or name_or_uuid}")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="green")

    def value_or_missing(key: str) -> str:
        value = summary.get(key)
        return "-" if value is None else str(value)

    rows = [
        ("UUID", summary.get("uuid") or "-"),
        ("Name", summary.get("name") or "-"),
        ("State", summary.get("state") or "-"),
        ("RMC State", summary.get("rmc_state") or "-"),
        ("Type", summary.get("partition_type") or "-"),
        ("Partition ID", value_or_missing("partition_id")),
        ("Current Memory (MiB)", value_or_missing("current_memory_mb")),
        ("Desired Memory (MiB)", value_or_missing("desired_memory_mb")),
        ("Current Proc Units", value_or_missing("current_proc_units")),
        ("Desired Proc Units", value_or_missing("desired_proc_units")),
        ("Desired vCPUs", value_or_missing("desired_vcpus")),
        ("Dedicated Procs", value_or_missing("dedicated_procs")),
        ("OS Version", summary.get("os_version") or "-"),
        ("OS Type", summary.get("os_type") or "-"),
        (
            "Client Network Adapters",
            str(summary.get("client_network_adapter_count", 0)),
        ),
        ("Description", summary.get("description") or "-"),
    ]
    for prop, val in rows:
        table.add_row(prop, val)
    console.print(table)


def lpars_list(
    system: str | None = typer.Option(
        None, "--system", "-s", help="Restrict to this managed system name or UUID"
    ),
    state: PartitionState | None = typer.Option(
        None, "--state", help="Filter by PartitionState (server-side search)"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List logical partitions."""

    lpars = _with_client(lambda hmc: list_lpars(hmc, system, state))

    table = None
    if not as_json:
        table = Table(title="Logical Partitions")
        for col in ("Name", "ID", "UUID", "State", "Type", "OS", "RMC"):
            table.add_column(col)
        for lpar in lpars:
            table.add_row(
                _first_field(lpar, "PartitionName"),
                _first_field(lpar, "PartitionID"),
                lpar.get("UUID") or "-",
                _first_field(lpar, "PartitionState"),
                _first_field(lpar, "PartitionType"),
                _first_field(lpar, "OperatingSystemVersion", default="-"),
                _first_field(lpar, "ResourceMonitoringControlState", "RMCState"),
            )
    _output(lpars, as_json, table, "No logical partitions found")


def lpars_show(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    as_json: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """Show one LPAR, looked up by name (exact) or by UUID."""

    lpar = _with_client(lambda hmc: get_lpar(hmc, name_or_uuid))

    if lpar is None:
        _partition_not_found(name_or_uuid)
    _print_json(lpar)


def lpars_state(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
) -> None:
    """Print just the current state of an LPAR."""

    async def _go():
        async with _client() as hmc:
            try:
                return await get_lpar_state(hmc, name_or_uuid)
            except ValueError as exc:
                if str(exc).startswith("No LPAR named "):
                    return None
                raise

    state = _run(_go)

    if state is None:
        _partition_not_found(name_or_uuid)
    console.print(state)


def register_commands(group: typer.Typer) -> None:
    """Register this module’s commands on *group*."""
    group.command("summary")(lpars_summary)
    group.command("list")(lpars_list)
    group.command("show")(lpars_show)
    group.command("state")(lpars_state)
