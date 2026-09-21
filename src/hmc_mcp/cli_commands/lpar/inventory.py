"""LPAR inventory CLI commands."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

import typer
from rich.table import Table

from ...operations.inventory.composite import fetch_lpar_summary
from ...operations.lpar.core import (
    get_lpar,
    get_lpar_state,
    list_lpars,
)
from ...operations.partition_state import PartitionState
from ...resource_identity import ResourceNotFoundError
from ..output import console, first_field, output, partition_not_found, print_json
from ..runtime import with_client


def lpars_summary(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """One-call summary: state, RMC, memory/CPU, OS details, adapter count, description."""

    summary = asdict(
        with_client(lambda hmc: fetch_lpar_summary(hmc, None, name_or_uuid))
    )

    if as_json:
        print_json(summary)
        return
    _render_lpar_summary(summary, name_or_uuid)


def _render_lpar_summary(summary: Mapping[str, Any], name_or_uuid: str) -> None:
    """Render a fetched LPAR summary as a terminal table."""
    table = Table(title=f"LPAR Summary: {summary.get('name') or name_or_uuid}")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="green")
    for property_name, value in _summary_rows(summary):
        table.add_row(property_name, value)
    console.print(table)


def _summary_rows(summary: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Return the stable display rows for an LPAR summary."""
    return [
        ("UUID", summary.get("uuid") or "-"),
        ("Name", summary.get("name") or "-"),
        ("State", summary.get("state") or "-"),
        ("RMC State", summary.get("rmc_state") or "-"),
        ("Type", summary.get("partition_type") or "-"),
        ("Partition ID", _value_or_missing(summary, "partition_id")),
        ("Current Memory (MiB)", _value_or_missing(summary, "current_memory_mib")),
        ("Desired Memory (MiB)", _value_or_missing(summary, "desired_memory_mib")),
        ("Current Proc Units", _value_or_missing(summary, "current_proc_units")),
        ("Desired Proc Units", _value_or_missing(summary, "desired_proc_units")),
        ("Desired vCPUs", _value_or_missing(summary, "desired_vcpus")),
        ("Dedicated Procs", _value_or_missing(summary, "dedicated_procs")),
        ("OS Version", summary.get("os_version") or "-"),
        ("OS Type", summary.get("os_type") or "-"),
        (
            "Client Network Adapters",
            str(summary.get("client_network_adapter_count", 0)),
        ),
        ("Description", summary.get("description") or "-"),
    ]


def _value_or_missing(summary: Mapping[str, Any], key: str) -> str:
    """Render an optional summary field without treating zero as missing."""
    value = summary.get(key)
    return "-" if value is None else str(value)


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

    lpars = with_client(lambda hmc: list_lpars(hmc, system, state))

    table = None
    if not as_json:
        table = Table(title="Logical Partitions")
        for col in ("Name", "ID", "UUID", "State", "Type", "OS", "RMC"):
            table.add_column(col)
        for lpar in lpars:
            table.add_row(
                first_field(lpar, "PartitionName"),
                first_field(lpar, "PartitionID"),
                lpar.get("UUID") or "-",
                first_field(lpar, "PartitionState"),
                first_field(lpar, "PartitionType"),
                first_field(lpar, "OperatingSystemVersion", default="-"),
                first_field(lpar, "ResourceMonitoringControlState", "RMCState"),
            )
    output(lpars, as_json, table, "No logical partitions found")


def lpars_show(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    as_json: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """Show one LPAR, looked up by name (exact) or by UUID."""

    lpar = with_client(lambda hmc: get_lpar(hmc, name_or_uuid))

    if lpar is None:
        partition_not_found(name_or_uuid)
    print_json(lpar)


def lpars_state(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
) -> None:
    """Print just the current state of an LPAR."""

    async def state_or_none(hmc):
        try:
            return await get_lpar_state(hmc, name_or_uuid)
        except ResourceNotFoundError:
            return None

    state = with_client(state_or_none)

    if state is None:
        partition_not_found(name_or_uuid)
    console.print(state)


def register_commands(group: typer.Typer) -> None:
    """Register this module’s commands on *group*."""
    group.command("summary")(lpars_summary)
    group.command("list")(lpars_list)
    group.command("show")(lpars_show)
    group.command("state")(lpars_state)
