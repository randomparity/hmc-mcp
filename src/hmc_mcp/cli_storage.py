"""CLI commands for VIOS storage and the virtual media repository.
"""

from __future__ import annotations


import typer
from rich.table import Table

from .cli_app import (
    _client,
    _first_field,
    _output,
    _print_json,
    _resolve_uuid,
    _run,
    _with_client,
    console,
    err_console,
    storage_app,
)



@storage_app.command("list-vgs")
def storage_list_vgs(
    vios: str = typer.Argument(..., help="VIOS UUID"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List Volume Groups on a VIOS (free space, PVs, virtual disks)."""

    vgs = _with_client(lambda hmc: hmc.list_volume_groups(vios))

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
    vios: str = typer.Argument(..., help="VIOS UUID"),
    name: str = typer.Option(..., "--name", "-n", help="Volume Group name"),
    pvs: str = typer.Option(..., "--pvs", help="Comma-separated physical volumes, e.g. hdisk10,hdisk11"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Create a Volume Group on a VIOS from physical volumes."""
    pv_list = [p.strip() for p in pvs.split(",") if p.strip()]
    if not pv_list:
        err_console.print("[red]Provide at least one physical volume via --pvs[/red]")
        raise typer.Exit(code=2)
    if not yes and not typer.confirm(f"Create VG '{name}' from {pv_list} on VIOS {vios}?"):
        raise typer.Abort()

    vg = _with_client(lambda hmc: hmc.create_volume_group(vios, name, pv_list))

    console.print(f"[green]Created Volume Group '{name}'[/green]")
    _print_json(vg)


@storage_app.command("create-disk")
def storage_create_disk(
    vios: str = typer.Argument(..., help="VIOS UUID"),
    vg: str = typer.Option(..., "--vg", help="Volume Group UUID"),
    name: str = typer.Option(..., "--name", "-n", help="Virtual disk name"),
    size: int = typer.Option(..., "--size", help="Size in MiB"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Create a Virtual Disk (logical volume) in a Volume Group."""
    if not yes and not typer.confirm(f"Create {size} MiB virtual disk '{name}' in VG {vg}?"):
        raise typer.Abort()

    disk = _with_client(lambda hmc: hmc.create_virtual_disk(vios, vg, name, size))

    console.print(f"[green]Created virtual disk '{name}' ({size} MiB)[/green]")
    _print_json(disk)


@storage_app.command("map")
def storage_map(
    vios: str = typer.Argument(..., help="VIOS UUID"),
    lpar: str = typer.Option(..., "--lpar", help="Target LPAR name or UUID"),
    disk: str = typer.Option(..., "--disk", help="Storage name (DiskName or hdiskN)"),
    kind: str = typer.Option("VirtualDisk", "--kind", help="VirtualDisk or PhysicalVolume"),
    target: str | None = typer.Option(None, "--target", help="Pin the vtscsi device name"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Map backing storage to an LPAR via a vSCSI mapping on a VIOS."""

    async def _go():
        async with _client() as hmc:
            lpar_uuid = await _resolve_uuid(hmc, lpar)
            if lpar_uuid is None:
                return None, None
            if not yes and not typer.confirm(
                f"Map {kind} '{disk}' on VIOS {vios} to LPAR '{lpar}' ({lpar_uuid})?"
            ):
                raise typer.Abort()
            return lpar_uuid, await hmc.map_storage_to_lpar(vios, kind, disk, lpar_uuid, target)

    lpar_uuid, result = _run(_go)

    if lpar_uuid is None:
        err_console.print(f"[yellow]Partition '{lpar}' not found[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"[green]Mapped '{disk}'[/green] to {lpar_uuid}")
    _print_json(result)




@storage_app.command("create-media-repo")
def storage_create_media_repo(
    vios: str = typer.Argument(..., help="VIOS UUID"),
    vg: str = typer.Argument(..., help="Volume Group UUID"),
    size_mb: int = typer.Option(..., "--size-mb", help="Repository size in MB"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Create the Virtual Media Repository (VMLibrary) on a volume group."""
    if not yes and not typer.confirm(f"Create {size_mb} MB media repository on VG {vg} (VIOS {vios})?"):
        raise typer.Abort()

    result = _with_client(lambda hmc: hmc.create_media_repository(vios, vg, size_mb))

    console.print(f"[green]Created media repository on {vg}[/green]")
    _print_json(result)


@storage_app.command("create-media")
def storage_create_media(
    vios: str = typer.Argument(..., help="VIOS UUID"),
    vg: str = typer.Argument(..., help="Volume Group UUID"),
    name: str = typer.Option(..., "--name", "-n", help="Media file name (e.g. aix.iso)"),
    size_mb: int = typer.Option(..., "--size-mb", help="Media size in MB"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Create a blank optical media (ISO container) in the media repository."""
    if not yes and not typer.confirm(f"Create media '{name}' ({size_mb} MB) on VG {vg} (VIOS {vios})?"):
        raise typer.Abort()

    result = _with_client(lambda hmc: hmc.create_optical_media(vios, vg, name, size_mb))

    console.print(f"[green]Created media '{name}' on {vg}[/green]")
    _print_json(result)


@storage_app.command("delete-media-repo")
def storage_delete_media_repo(
    vios: str = typer.Argument(..., help="VIOS UUID"),
    vg: str = typer.Argument(..., help="Volume Group UUID"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete the Virtual Media Repository from a volume group."""
    if not yes and not typer.confirm(f"Delete media repository on VG {vg} (VIOS {vios})?"):
        raise typer.Abort()

    _with_client(lambda hmc: hmc.delete_media_repository(vios, vg))

    console.print(f"[green]Deleted media repository on {vg}[/green]")


