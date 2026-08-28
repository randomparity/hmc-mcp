"""CLI commands for LPAR provisioning."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import cast

import typer
from rich.table import Table

from ..documents import (
    PARTITION_TYPES,
    STORAGE_KINDS,
    LparResources,
    StorageKind,
)
from ..operations.lpar.provision import (
    ProvisionNetwork,
    ProvisionStorage,
    provision_lpar,
)
from .app import (
    _client,
    _print_json,
    _run,
    _usage_error,
    console,
)
from .lpars_config import _load_pcie_assignments


def lpars_provision(
    system: str = typer.Option(
        ..., "--system", "-s", help="Target managed system name or UUID"
    ),
    name: str = typer.Option(..., "--name", "-n", help="Name for the new LPAR"),
    port_vlan_id: int = typer.Option(
        ..., "--vlan", help="Port VLAN ID for the network adapter"
    ),
    vios_uuid: str = typer.Option(
        ..., "--vios-uuid", help="UUID of the VIOS for vSCSI / storage"
    ),
    vios_partition_id: int = typer.Option(
        ..., "--vios-partition-id", help="Numeric partition ID of the VIOS"
    ),
    vios_slot: int = typer.Option(
        ..., "--vios-slot", help="Virtual slot number of the VIOS server adapter"
    ),
    storage_name: str = typer.Option(
        ..., "--storage-name", help="VirtualDisk or PhysicalVolume name to map"
    ),
    partition_type: str = typer.Option(
        "AIX/Linux", "--type", help=f"Partition type: {', '.join(PARTITION_TYPES)}"
    ),
    min_memory: int = typer.Option(256, "--min-mem", help="Minimum memory (MiB)"),
    memory: int = typer.Option(4096, "--mem", help="Desired memory (MiB)"),
    max_memory: int = typer.Option(8192, "--max-mem", help="Maximum memory (MiB)"),
    vcpus: int = typer.Option(1, "--vcpus", help="Desired virtual CPUs"),
    max_vcpus: int = typer.Option(2, "--max-vcpus", help="Maximum virtual CPUs"),
    storage_kind: str = typer.Option(
        "VirtualDisk", "--storage-kind", help='"VirtualDisk" or "PhysicalVolume"'
    ),
    vg_uuid: str | None = typer.Option(
        None, "--vg-uuid", help="Volume group UUID to validate (optional)"
    ),
    power_on: bool = typer.Option(
        True, "--power-on/--no-power-on", help="Power on after provisioning"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Validate preconditions only; do not create"
    ),
    pcie_assignments: Path | None = typer.Option(
        None,
        "--pcie-assignments",
        help="JSON file using the declarative LparPcieAssignments schema",
    ),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Provision a new LPAR end-to-end: create, add network adapter, add vSCSI adapter, map storage, power on.

    Always validates preconditions first (name uniqueness, VLAN existence, volume-group existence).
    Pass --dry-run to run precondition checks only without creating anything.
    On partial failure the completed steps are reported as "ok", the failed step as "error",
    and remaining steps as "skipped". No automatic rollback is performed.
    """
    assignments = _load_pcie_assignments(pcie_assignments)

    if partition_type not in PARTITION_TYPES:
        _usage_error(
            f"--type must be one of {', '.join(PARTITION_TYPES)}, got {partition_type!r}"
        )
    if storage_kind not in STORAGE_KINDS:
        _usage_error(
            "--storage-kind must be one of "
            f"{', '.join(sorted(STORAGE_KINDS))}, got {storage_kind!r}"
        )
    if not dry_run and not yes:
        typer.confirm(
            f"Provision LPAR '{name}' on system '{system}' (VLAN {port_vlan_id}, VIOS {vios_uuid})?"
            + (" [DRY RUN]" if dry_run else ""),
            abort=True,
        )

    async def _go():
        async with _client() as hmc:
            return await provision_lpar(
                hmc,
                system_name_or_uuid=system,
                name=name,
                network=ProvisionNetwork(port_vlan_id, vios_partition_id, vios_slot),
                storage=ProvisionStorage(
                    vios_uuid,
                    storage_name,
                    cast(StorageKind, storage_kind),
                    vg_uuid,
                ),
                resources=LparResources(
                    min_memory=min_memory,
                    desired_memory=memory,
                    max_memory=max_memory,
                    desired_vcpus=vcpus,
                    max_vcpus=max_vcpus,
                ),
                partition_type=partition_type,
                power_on=power_on,
                dry_run=dry_run,
                assignments=assignments,
            )

    result = _run(_go)

    if as_json:
        _print_json(asdict(result))
        return

    if dry_run:
        console.print(
            "[yellow]DRY RUN — preconditions validated, no LPAR created[/yellow]"
        )
    elif result.workflow_completed:
        console.print(f"[green]LPAR '{name}' provisioned successfully[/green]")
    elif result.resource_created:
        identity = result.lpar_uuid or "UUID unavailable"
        console.print(
            f"[yellow]LPAR '{name}' was created ({identity}), but provisioning "
            "is incomplete — check step results[/yellow]"
        )
    else:
        console.print(
            f"[yellow]LPAR '{name}' was not created — check step results[/yellow]"
        )

    table = Table(title=f"Provision steps: {name}")
    table.add_column("Step", style="cyan")
    table.add_column("Status", style="green")
    for step in result.steps:
        status = step.status
        style = (
            "green"
            if status == "ok"
            else ("yellow" if status in ("dry_run", "skipped") else "red")
        )
        table.add_row(step.step, f"[{style}]{status}[/{style}]")
    console.print(table)

    if result.warnings:
        for w in result.warnings:
            console.print(f"[yellow]Warning: {w}[/yellow]")


# LPAR Boot Order Commands


def register_commands(group: typer.Typer) -> None:
    """Register this module’s commands on *group*."""
    group.command("provision")(lpars_provision)
