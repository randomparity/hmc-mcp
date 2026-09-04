"""CLI commands for VIOS storage and the virtual media repository."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import typer
from rich.table import Table

from hmc_mcp.client.core import HMCClient

from ..config import load_profile
from ..documents import StorageKind
from ..operations.lpar.provision import ProvisionStorage, attach_disk_to_lpar
from ..operations.storage import (
    StorageMapResult,
    create_media_repository,
    create_optical_media,
    create_virtual_disk,
    create_volume_group,
    delete_media_repository,
    delete_optical_media,
    delete_virtual_disk,
    detach_storage_mapping,
    get_media_repository,
    list_optical_media,
    list_storage_mappings,
    list_volume_groups,
    map_storage,
    upload_iso,
)
from .output import console, first_field, output, print_json, usage_error
from .runtime import client, run, with_client


def storage_list_vgs(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List Volume Groups on a VIOS (free space, PVs, virtual disks)."""

    vgs = with_client(lambda hmc: list_volume_groups(hmc, vios, system_name_or_uuid=system))

    table = None
    if not as_json:
        table = Table(title=f"Volume Groups on {vios}")
        for col in ("Name", "UUID", "Free (MiB)", "Capacity (MiB)"):
            table.add_column(col)
        for v in vgs:
            table.add_row(
                first_field(v, "GroupName"),
                v.get("UUID") or "-",
                first_field(v, "FreeSpace", "FreeSpaceInMBytes"),
                first_field(v, "GroupCapacity", "Capacity"),
            )
    output(vgs, as_json, table, "No volume groups found")


def storage_create_vg(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    name: str = typer.Option(..., "--name", "-n", help="Volume Group name"),
    pvs: str = typer.Option(
        ..., "--pvs", help="Comma-separated physical volumes, e.g. hdisk10,hdisk11"
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """Create a Volume Group on a VIOS from physical volumes."""
    pv_list = [p.strip() for p in pvs.split(",") if p.strip()]
    if not pv_list:
        usage_error("Provide at least one physical volume via --pvs")
    if not yes and not typer.confirm(
        f"Create VG '{name}' from {pv_list} on VIOS {vios}?"
    ):
        raise typer.Abort()

    vg = with_client(
        lambda hmc: create_volume_group(hmc, vios, name, pv_list, system_name_or_uuid=system)
    )

    console.print(f"[green]Created Volume Group '{name}'[/green]")
    print_json(vg)


def storage_create_disk(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    vg: str = typer.Option(..., "--vg", help="Volume Group UUID"),
    name: str = typer.Option(..., "--name", "-n", help="Virtual disk name"),
    capacity_mib: int = typer.Option(
        ..., "--capacity-mib", help="Virtual disk capacity in MiB"
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """Create a Virtual Disk (logical volume) in a Volume Group."""
    if not yes and not typer.confirm(
        f"Create {capacity_mib} MiB virtual disk '{name}' in VG {vg}?"
    ):
        raise typer.Abort()

    disk = with_client(
        lambda hmc: create_virtual_disk(
            hmc, vios, vg, name, capacity_mib=capacity_mib, system_name_or_uuid=system
        )
    )

    console.print(f"[green]Created virtual disk '{name}' ({capacity_mib} MiB)[/green]")
    print_json(disk)


def storage_delete_disk(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    vg: str = typer.Option(..., "--vg", help="Volume Group UUID"),
    name: str = typer.Option(..., "--name", "-n", help="Virtual disk name"),
    yes: bool = typer.Option(False, "--yes", "-y"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """Delete a Virtual Disk from a Volume Group.

    Validates that the disk is not mapped to any LPAR before deletion.
    """
    if not yes and not typer.confirm(f"Delete virtual disk '{name}' from VG {vg}?"):
        raise typer.Abort()

    disk = with_client(
        lambda hmc: delete_virtual_disk(hmc, vios, vg, name, system_name_or_uuid=system)
    )

    console.print(f"[green]Deleted virtual disk '{name}'[/green]")
    print_json(disk)


def storage_attach_disk(
    lpar: str = typer.Argument(..., help="Target LPAR name or UUID"),
    vios: str = typer.Option(..., "--vios", help="VIOS UUID"),
    vg: str = typer.Option(..., "--vg", help="Volume Group UUID"),
    name: str = typer.Option(..., "--name", "-n", help="New virtual disk name"),
    capacity_mib: int = typer.Option(
        ..., "--capacity-mib", help="Disk capacity in MiB"
    ),
    vios_id: int = typer.Option(..., "--vios-id", help="VIOS partition ID"),
    vios_slot: int = typer.Option(..., "--vios-slot", help="VIOS server slot"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate without mutation"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    ownership_override: bool = typer.Option(False, "--ownership-override"),
) -> None:
    """Create a virtual disk and attach it to an existing LPAR."""
    if (
        not dry_run
        and not yes
        and not typer.confirm(
            f"Create {capacity_mib} MiB disk '{name}' and attach it to LPAR '{lpar}'?"
        )
    ):
        raise typer.Abort()

    result = with_client(
        lambda hmc: attach_disk_to_lpar(
            hmc,
            None,
            lpar,
            ProvisionStorage(vios, name, vg_uuid=vg),
            capacity_mib=capacity_mib,
            vios_partition_id=vios_id,
            vios_slot=vios_slot,
            dry_run=dry_run,
            ownership_override=ownership_override,
        )
    )
    if as_json:
        print_json(asdict(result))
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
            step.step,
            step.status,
            "" if step.result is None else str(step.result),
        )
    console.print(table)
    raise typer.Exit(1)


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
    ownership_override: bool = typer.Option(False, "--ownership-override"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """Map backing storage to an LPAR via a vSCSI mapping on a VIOS."""
    if not yes and not typer.confirm(
        f"Map {kind} '{disk}' on VIOS {vios} to LPAR '{lpar}'?"
    ):
        raise typer.Abort()

    async def _go():
        async with client() as hmc:
            return await map_storage(
                hmc,
                vios,
                lpar,
                system_name_or_uuid=system,
                kind=kind,
                storage_name=disk,
                target=target,
                ownership_override=ownership_override,
            )

    result: StorageMapResult = run(_go)

    console.print(f"[green]Mapped '{disk}'[/green] to {result.lpar_uuid}")
    print_json(asdict(result))


def storage_create_media_repo(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    vg: str = typer.Argument(..., help="Volume Group UUID"),
    size_mib: int = typer.Option(..., "--size-mib", help="Repository size in MiB"),
    yes: bool = typer.Option(False, "--yes", "-y"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """Create the Virtual Media Repository (VMLibrary) on a volume group."""
    if not yes and not typer.confirm(
        f"Create {size_mib} MiB media repository on VG {vg} (VIOS {vios})?"
    ):
        raise typer.Abort()

    result = with_client(
        lambda hmc: create_media_repository(hmc, vios, vg, size_mib, system_name_or_uuid=system)
    )

    console.print(f"[green]Created media repository on {vg}[/green]")
    print_json(result)


def storage_create_media(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    vg: str = typer.Argument(..., help="Volume Group UUID"),
    name: str = typer.Option(
        ..., "--name", "-n", help="Media file name (e.g. aix.iso)"
    ),
    size_mib: int = typer.Option(..., "--size-mib", help="Media size in MiB"),
    yes: bool = typer.Option(False, "--yes", "-y"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """Create a blank optical media (ISO container) in the media repository."""
    if not yes and not typer.confirm(
        f"Create media '{name}' ({size_mib} MiB) on VG {vg} (VIOS {vios})?"
    ):
        raise typer.Abort()

    result = with_client(
        lambda hmc: create_optical_media(
            hmc, vios, vg, name, size_mib, system_name_or_uuid=system
        )
    )

    console.print(f"[green]Created media '{name}' on {vg}[/green]")
    print_json(result)


def storage_delete_media_repo(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    vg: str = typer.Argument(..., help="Volume Group UUID"),
    yes: bool = typer.Option(False, "--yes", "-y"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """Delete the Virtual Media Repository from a volume group."""
    if not yes and not typer.confirm(
        f"Delete media repository on VG {vg} (VIOS {vios})?"
    ):
        raise typer.Abort()

    with_client(lambda hmc: delete_media_repository(hmc, vios, vg, system_name_or_uuid=system))
    console.print(f"[green]Deleted media repository on {vg}[/green]")


def storage_delete_media(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    vg: str = typer.Argument(..., help="Volume Group UUID"),
    media_name: str = typer.Argument(..., help="ISO image name to delete"),
    yes: bool = typer.Option(False, "--yes", "-y"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """Delete an ISO image from the media repository."""
    if not yes and not typer.confirm(
        f"Delete media '{media_name}' on VG {vg} (VIOS {vios})?"
    ):
        raise typer.Abort()

    with_client(
        lambda hmc: delete_optical_media(
            hmc, vios, vg, media_name, system_name_or_uuid=system
        )
    )
    console.print(f"[green]Deleted media '{media_name}' on {vg}[/green]")


def storage_get_media_repo(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    vg: str = typer.Argument(..., help="Volume Group UUID"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """Get the Virtual Media Repository (VMLibrary) from a volume group."""
    result = with_client(
        lambda hmc: get_media_repository(hmc, vios, vg, system_name_or_uuid=system)
    )

    if as_json:
        print_json(result)
    elif result:
        console.print(f"[green]Media Repository on VG {vg} (VIOS {vios}):[/green]")
        resource = result.get("Resource", {})
        repo_name = resource.get("RepositoryName", "N/A")
        repo_size = resource.get("RepositorySize", "N/A")
        console.print(f"  Name: {repo_name}")
        console.print(f"  Size: {repo_size} MiB")
    else:
        console.print("[yellow]No media repository found[/yellow]")


def storage_list_optical_media(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    vg: str = typer.Argument(..., help="Volume Group UUID"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """List Virtual Optical Media in the Virtual Media Repository."""
    media_list = with_client(
        lambda hmc: list_optical_media(hmc, vios, vg, system_name_or_uuid=system)
    )

    if as_json:
        print_json(media_list)
    elif media_list:
        console.print(
            f"[green]Optical Media in repository on VG {vg} (VIOS {vios}):[/green]"
        )
        table = Table()
        table.add_column("Media Name", style="cyan")
        table.add_column("Size (MiB)", style="magenta")
        table.add_column("Type", style="green")
        for media in media_list:
            table.add_row(
                media.get("MediaName", "N/A"),
                str(media.get("MediaSize", "N/A")),
                media.get("MediaType", "N/A"),
            )
        console.print(table)
    else:
        console.print("[yellow]No optical media found[/yellow]")


def storage_list_mappings(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    lpar: str | None = typer.Option(
        None, "--lpar", help="Scope to single LPAR by name or UUID"
    ),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output as raw JSON"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """List VirtualSCSIMappings on a VIOS (optionally scoped to an LPAR)."""

    async def _go() -> list[dict[str, Any]]:
        config = load_profile()
        async with HMCClient(config) as hmc:
            return await list_storage_mappings(
                hmc, vios, lpar, system_name_or_uuid=system
            )

    mappings = run(_go)
    if as_json:
        print_json(mappings)
    else:
        table = Table(title=f"Storage Mappings on {vios}")
        table.add_column("Mapping UUID", style="cyan")
        table.add_column("Client LPAR", style="green")
        table.add_column("Backing Storage", style="yellow")
        table.add_column("Type", style="magenta")
        for m in mappings:
            mapping_uuid = m.get("UUID", "")
            storage = m.get("Storage", {})
            client = m.get("AssociatedLogicalPartition", {})
            client_name = client.get("PartitionName", "")

            backing = ""
            stype = ""
            if storage:
                if "PhysicalVolume" in storage:
                    backing = storage["PhysicalVolume"].get("VolumeName", "")
                    stype = "PhysicalVolume"
                elif "VirtualDisk" in storage:
                    backing = storage["VirtualDisk"].get("DiskName", "")
                    stype = "VirtualDisk"

            table.add_row(mapping_uuid, client_name, backing, stype)
        console.print(table)


def storage_detach_mapping(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    mapping_uuid: str = typer.Argument(
        ..., help="Exact UUID shown by storage list-mappings"
    ),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
    ownership_override: bool = typer.Option(
        False,
        "--ownership-override",
        help="Bypass LPAR ownership protection after operator approval",
    ),
    confirm: bool = typer.Option(
        False, "--confirm", "-y", help="Skip confirmation prompt"
    ),
) -> None:
    """Detach a VirtualSCSIMapping while preserving its backing storage."""
    if not confirm:
        typer.confirm(
            f"Delete storage mapping {mapping_uuid} on VIOS {vios}? "
            "The backing storage (PhysicalVolume or VirtualDisk) will be preserved.",
            abort=True,
        )

    async def _go() -> None:
        config = load_profile()
        async with HMCClient(config) as hmc:
            await detach_storage_mapping(
                hmc,
                vios,
                mapping_uuid,
                system_name_or_uuid=system,
                ownership_override=ownership_override,
            )

    run(_go)
    console.print(f"[green]Deleted storage mapping {mapping_uuid}[/green]")


def storage_upload_iso(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    vg: str = typer.Argument(..., help="Volume Group UUID"),
    media_name: str = typer.Argument(
        ..., help="Target name for the ISO in the repository"
    ),
    iso_source: str = typer.Argument(
        ...,
        help="http(s) URL to download the ISO from; its host must be on "
        "HMC_ISO_URL_ALLOWLIST",
    ),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output as raw JSON"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """Upload an ISO to a VIOS media repository via the HMC file broker.

    ISO_SOURCE must be an http(s) URL; a local file path is not accepted. Its host
    must be on HMC_ISO_URL_ALLOWLIST (or iso_url_allowlist in the profile) — with
    no allowlist configured every URL is refused — and redirects are not followed.
    Computes SHA-256 and size before upload, refuses name collisions, and cleans
    up broker resources on every outcome.
    """

    async def _go() -> dict[str, Any]:
        config = load_profile()
        async with HMCClient(config) as hmc:
            return await upload_iso(
                hmc,
                vios,
                vg,
                media_name,
                iso_source,
                system_name_or_uuid=system,
            )

    result = run(_go)

    if as_json:
        print_json(result)
    else:
        console.print(
            f"[green]Upload status: {result.get('status', 'unknown')}[/green]"
        )
        console.print(f"  Media name: {result.get('media_name', 'N/A')}")
        console.print(f"  Size: {result.get('media_size_bytes', 0):,} bytes")
        console.print(f"  SHA-256: {result.get('sha256', 'N/A')}")
        if result.get("media"):
            console.print(f"  Media entry: {result['media'].get('MediaName', 'N/A')}")


def register_commands(group: typer.Typer) -> None:
    """Register this module’s commands on *group*."""
    group.command("list-vgs")(storage_list_vgs)
    group.command("create-vg")(storage_create_vg)
    group.command("create-disk")(storage_create_disk)
    group.command("delete-disk")(storage_delete_disk)
    group.command("attach-disk")(storage_attach_disk)
    group.command("map")(storage_map)
    group.command("create-media-repo")(storage_create_media_repo)
    group.command("create-media")(storage_create_media)
    group.command("delete-media-repo")(storage_delete_media_repo)
    group.command("delete-media")(storage_delete_media)
    group.command("get-media-repo")(storage_get_media_repo)
    group.command("list-optical-media")(storage_list_optical_media)
    group.command("list-mappings")(storage_list_mappings)
    group.command("detach-mapping")(storage_detach_mapping)
    group.command("upload-iso")(storage_upload_iso)
