"""CLI commands for VIOS storage and the virtual media repository."""

from __future__ import annotations


import typer
from dataclasses import asdict
from rich.table import Table

from .documents import StorageKind

from .cli_app import (
    _client,
    _first_field,
    _output,
    _print_json,
    _run,
    _with_client,
    _usage_error,
    console,
    storage_app,
)
from .operations_storage import (
    create_media_repository,
    create_optical_media,
    create_virtual_disk,
    create_volume_group,
    delete_media_repository,
    list_volume_groups,
    map_storage,
)
from .operations_provision import ProvisionStorage, attach_disk_to_lpar


@storage_app.command("list-vgs")
def storage_list_vgs(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List Volume Groups on a VIOS (free space, PVs, virtual disks)."""

    vgs = _with_client(lambda hmc: list_volume_groups(hmc, vios))

    table = None
    if not as_json:
        table = Table(title=f"Volume Groups on {vios}")
        for col in ("Name", "UUID", "Free (MiB)", "Capacity (MiB)"):
            table.add_column(col)
        for v in vgs:
            table.add_row(
                _first_field(v, "GroupName"),
                v.get("UUID") or "-",
                _first_field(v, "FreeSpace", "FreeSpaceInMBytes"),
                _first_field(v, "GroupCapacity", "Capacity"),
            )
    _output(vgs, as_json, table, "No volume groups found")


@storage_app.command("create-vg")
def storage_create_vg(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    name: str = typer.Option(..., "--name", "-n", help="Volume Group name"),
    pvs: str = typer.Option(
        ..., "--pvs", help="Comma-separated physical volumes, e.g. hdisk10,hdisk11"
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Create a Volume Group on a VIOS from physical volumes."""
    pv_list = [p.strip() for p in pvs.split(",") if p.strip()]
    if not pv_list:
        _usage_error("Provide at least one physical volume via --pvs")
    if not yes and not typer.confirm(
        f"Create VG '{name}' from {pv_list} on VIOS {vios}?"
    ):
        raise typer.Abort()

    vg = _with_client(lambda hmc: create_volume_group(hmc, vios, name, pv_list))

    console.print(f"[green]Created Volume Group '{name}'[/green]")
    _print_json(vg)


@storage_app.command("create-disk")
def storage_create_disk(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    vg: str = typer.Option(..., "--vg", help="Volume Group UUID"),
    name: str = typer.Option(..., "--name", "-n", help="Virtual disk name"),
    size: int = typer.Option(..., "--size", help="Size in MiB"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Create a Virtual Disk (logical volume) in a Volume Group."""
    if not yes and not typer.confirm(
        f"Create {size} MiB virtual disk '{name}' in VG {vg}?"
    ):
        raise typer.Abort()

    disk = _with_client(lambda hmc: create_virtual_disk(hmc, vios, vg, name, size))

    console.print(f"[green]Created virtual disk '{name}' ({size} MiB)[/green]")
    _print_json(disk)


@storage_app.command("attach-disk")
def storage_attach_disk(
    lpar: str = typer.Argument(..., help="Target LPAR name or UUID"),
    vios: str = typer.Option(..., "--vios", help="VIOS UUID"),
    vg: str = typer.Option(..., "--vg", help="Volume Group UUID"),
    name: str = typer.Option(..., "--name", "-n", help="New virtual disk name"),
    size: int = typer.Option(..., "--size", help="Disk size in MiB"),
    vios_id: int = typer.Option(..., "--vios-id", help="VIOS partition ID"),
    vios_slot: int = typer.Option(..., "--vios-slot", help="VIOS server slot"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate without mutation"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Create a virtual disk and attach it to an existing LPAR."""
    if (
        not dry_run
        and not yes
        and not typer.confirm(
            f"Create {size} MiB disk '{name}' and attach it to LPAR '{lpar}'?"
        )
    ):
        raise typer.Abort()

    result = _with_client(
        lambda hmc: attach_disk_to_lpar(
            hmc,
            lpar,
            ProvisionStorage(vios, name, vg_uuid=vg),
            capacity_mb=size,
            vios_partition_id=vios_id,
            vios_slot=vios_slot,
            dry_run=dry_run,
        )
    )
    if as_json:
        _print_json(asdict(result))
        if not dry_run and not result.workflow_completed:
            raise typer.Exit(1)
        return
    if dry_run:
        console.print("[yellow]DRY RUN — preconditions validated[/yellow]")
        return
    if result.workflow_completed:
        console.print(f"[green]Attached virtual disk '{name}' to {lpar}[/green]")
        return

    console.print("[yellow]Disk attachment incomplete[/yellow]")
    table = Table(title=f"Attach-disk steps: {name}")
    table.add_column("Step")
    table.add_column("Status")
    table.add_column("Detail")
    for step in result.steps:
        table.add_row(
            str(step.get("step", "-")),
            str(step.get("status", "-")),
            str(step.get("result", "")),
        )
    console.print(table)
    raise typer.Exit(1)


@storage_app.command("map")
def storage_map(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    lpar: str = typer.Option(..., "--lpar", help="Target LPAR name or UUID"),
    disk: str = typer.Option(..., "--disk", help="Storage name (DiskName or hdiskN)"),
    kind: StorageKind = typer.Option(
        "VirtualDisk", "--kind", help="VirtualDisk or PhysicalVolume"
    ),
    target: str | None = typer.Option(
        None, "--target", help="Pin the vtscsi device name"
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Map backing storage to an LPAR via a vSCSI mapping on a VIOS."""
    if not yes and not typer.confirm(
        f"Map {kind} '{disk}' on VIOS {vios} to LPAR '{lpar}'?"
    ):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await map_storage(hmc, vios, kind, disk, lpar, target)

    lpar_uuid, result = _run(_go)

    console.print(f"[green]Mapped '{disk}'[/green] to {lpar_uuid}")
    _print_json(result)


@storage_app.command("create-media-repo")
def storage_create_media_repo(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    vg: str = typer.Argument(..., help="Volume Group UUID"),
    size_mb: int = typer.Option(..., "--size-mb", help="Repository size in MB"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Create the Virtual Media Repository (VMLibrary) on a volume group."""
    if not yes and not typer.confirm(
        f"Create {size_mb} MB media repository on VG {vg} (VIOS {vios})?"
    ):
        raise typer.Abort()

    result = _with_client(lambda hmc: create_media_repository(hmc, vios, vg, size_mb))

    console.print(f"[green]Created media repository on {vg}[/green]")
    _print_json(result)


@storage_app.command("create-media")
def storage_create_media(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    vg: str = typer.Argument(..., help="Volume Group UUID"),
    name: str = typer.Option(
        ..., "--name", "-n", help="Media file name (e.g. aix.iso)"
    ),
    size_mb: int = typer.Option(..., "--size-mb", help="Media size in MB"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Create a blank optical media (ISO container) in the media repository."""
    if not yes and not typer.confirm(
        f"Create media '{name}' ({size_mb} MB) on VG {vg} (VIOS {vios})?"
    ):
        raise typer.Abort()

    result = _with_client(
        lambda hmc: create_optical_media(hmc, vios, vg, name, size_mb)
    )

    console.print(f"[green]Created media '{name}' on {vg}[/green]")
    _print_json(result)


@storage_app.command("delete-media-repo")
def storage_delete_media_repo(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    vg: str = typer.Argument(..., help="Volume Group UUID"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete the Virtual Media Repository from a volume group."""
    if not yes and not typer.confirm(
        f"Delete media repository on VG {vg} (VIOS {vios})?"
    ):
        raise typer.Abort()

    _with_client(lambda hmc: delete_media_repository(hmc, vios, vg))

    console.print(f"[green]Deleted media repository on {vg}[/green]")
