"""CLI commands for VIOS storage and the virtual media repository."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import typer
from rich.text import Text

from hmcpctl.client.core import HMCClient

from ...documents.storage import StorageKind, validate_virtual_disk
from ...operations.lpar.profile_sync import ChangeLocation
from ...operations.lpar.provision import (
    AttachDiskResult,
    ProvisionStorage,
    attach_disk_to_lpar,
)
from ...operations.storage.resources import (
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
    mount_optical_media,
    resolve_volume_group_uuid,
    unmount_optical_media,
    upload_iso,
)
from ...resource_identity import resolve_vios_uuid
from ..output import (
    InventoryFormat,
    VerbatimTable,
    console,
    print_inventory,
    print_json,
    resolve_inventory_format,
    terminal_field,
    usage_error,
)
from ..runtime import with_client


def storage_list_vgs(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output JSON"),
    format: InventoryFormat | None = typer.Option(
        None,
        "--format",
        help="Output format (default: table on TTY, tsv when redirected)",
    ),
) -> None:
    """List Volume Groups on a VIOS (free space, PVs, virtual disks)."""
    selected = resolve_inventory_format(as_json, format)
    vgs = with_client(
        lambda hmc: list_volume_groups(hmc, vios, system_name_or_uuid=system)
    )
    print_inventory(
        [asdict(vg) for vg in vgs],
        selected,
        ("Name", "UUID", "Free (GiB)", "Capacity (GiB)", "Diagnostic"),
        [
            (v.name, v.uuid, v.free_space_gib, v.capacity_gib, v.free_space_diagnostic)
            for v in vgs
        ],
        title=f"Volume Groups on {vios}",
        empty_msg="No volume groups found",
    )


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
        f"Create VG '{terminal_field(name)}' from {terminal_field(pv_list)} on VIOS "
        f"{terminal_field(vios)}?"
    ):
        raise typer.Abort()

    vg = with_client(
        lambda hmc: create_volume_group(
            hmc, vios, name, pv_list, system_name_or_uuid=system
        )
    )

    console.print(
        f"Created Volume Group '{terminal_field(name)}'", style="green", markup=False
    )
    print_json(vg)


def storage_create_disk(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    vg: str = typer.Option(..., "--vg", help="Volume Group name or UUID"),
    name: str = typer.Option(
        ..., "--name", "-n", help="Virtual disk name (at most 15 characters)"
    ),
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
        f"Create {terminal_field(capacity_mib)} MiB virtual disk '"
        f"{terminal_field(name)}' in VG {terminal_field(vg)}?"
    ):
        raise typer.Abort()

    disk = with_client(
        lambda hmc: create_virtual_disk(
            hmc, vios, vg, name, capacity_mib=capacity_mib, system_name_or_uuid=system
        )
    )

    console.print(
        f"Created virtual disk '{terminal_field(name)}' ({terminal_field(capacity_mib)} MiB)",
        style="green",
        markup=False,
    )
    print_json(disk)


def storage_delete_disk(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    vg: str = typer.Option(..., "--vg", help="Volume Group name or UUID"),
    name: str = typer.Option(..., "--name", "-n", help="Virtual disk name"),
    yes: bool = typer.Option(False, "--yes", "-y"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """Delete a Virtual Disk from a Volume Group.

    Validates that the disk is not mapped to any LPAR before deletion.
    """
    if not yes and not typer.confirm(
        f"Delete virtual disk '{terminal_field(name)}' from VG {terminal_field(vg)}?"
    ):
        raise typer.Abort()

    disk = with_client(
        lambda hmc: delete_virtual_disk(hmc, vios, vg, name, system_name_or_uuid=system)
    )

    console.print(
        f"Deleted virtual disk '{terminal_field(name)}'", style="green", markup=False
    )
    print_json(disk)


def storage_attach_disk(
    lpar: str = typer.Argument(..., help="Target LPAR name or UUID"),
    vios: str = typer.Option(..., "--vios", help="VIOS name or UUID"),
    vg: str = typer.Option(..., "--vg", help="Volume Group name or UUID"),
    name: str = typer.Option(
        ..., "--name", "-n", help="New virtual disk name (at most 15 characters)"
    ),
    capacity_mib: int = typer.Option(
        ..., "--capacity-mib", help="Disk capacity in MiB"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate without mutation"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    ownership_override: bool = typer.Option(False, "--ownership-override"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """Create a virtual disk and attach it to an existing LPAR."""
    if (
        not dry_run
        and not yes
        and not typer.confirm(
            f"Create {terminal_field(capacity_mib)} MiB disk '{terminal_field(name)}"
            f"' and attach it to LPAR '{terminal_field(lpar)}'?"
        )
    ):
        raise typer.Abort()

    async def _go(hmc: HMCClient) -> AttachDiskResult:
        validate_virtual_disk(name, capacity_mib)
        vios_uuid = await resolve_vios_uuid(hmc, vios, system_name_or_uuid=system)
        vg_uuid = await resolve_volume_group_uuid(hmc, vios_uuid, vg)
        return await attach_disk_to_lpar(
            hmc,
            system,
            lpar,
            ProvisionStorage(vios_uuid, name, vg_uuid=vg_uuid),
            capacity_mib=capacity_mib,
            dry_run=dry_run,
            ownership_override=ownership_override,
        )

    result = with_client(_go)
    if as_json:
        print_json(asdict(result))
        if not dry_run and not result.workflow_completed:
            raise typer.Exit(1)
        return
    if dry_run:
        console.print("[yellow]DRY RUN — preconditions validated[/yellow]")
        return
    if result.workflow_completed:
        console.print(
            f"Attached virtual disk '{terminal_field(name)}' to {terminal_field(lpar)}",
            style="green",
            markup=False,
        )
        _print_attach_location(result)
        return

    console.print("[yellow]Disk attachment incomplete[/yellow]")
    table = VerbatimTable(title=f"Attach-disk steps: {name}")
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
    _print_attach_location(result)
    raise typer.Exit(1)


def _print_attach_location(result: AttachDiskResult) -> None:
    for warning in result.warnings:
        console.print(
            f"Warning: {terminal_field(warning)}", style="yellow", markup=False
        )
    if result.change_location is not None:
        console.print(terminal_field(result.change_location.summary()), markup=False)


def storage_map(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    lpar: str = typer.Option(..., "--lpar", help="Target LPAR name or UUID"),
    disk: str = typer.Option(
        ..., "--name", "-n", help="Storage name (DiskName or hdiskN)"
    ),
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
        f"Map {terminal_field(kind)} '{terminal_field(disk)}' on VIOS "
        f"{terminal_field(vios)} to LPAR '{terminal_field(lpar)}'?"
    ):
        raise typer.Abort()

    result: StorageMapResult = with_client(
        lambda hmc: map_storage(
            hmc,
            vios,
            lpar,
            system_name_or_uuid=system,
            kind=kind,
            storage_name=disk,
            target=target,
            ownership_override=ownership_override,
        )
    )

    console.print(
        Text.assemble(
            (f"Mapped '{terminal_field(disk)}'", "green"),
            f" to {terminal_field(result.lpar_uuid)}",
        )
    )
    print_json(asdict(result))
    console.print(terminal_field(result.change_location.summary()), markup=False)


def storage_create_media_repo(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    vg: str = typer.Option(..., "--vg", help="Volume Group name or UUID"),
    size_mib: int = typer.Option(
        ...,
        "--size-mib",
        help="Repository size in MiB, a multiple of 1024 (sent to the HMC as whole GiB)",
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """Create the Virtual Media Repository (VMLibrary) on a volume group."""
    if not yes and not typer.confirm(
        f"Create {terminal_field(size_mib)} MiB media repository on VG "
        f"{terminal_field(vg)} (VIOS {terminal_field(vios)})?"
    ):
        raise typer.Abort()

    result = with_client(
        lambda hmc: create_media_repository(
            hmc, vios, vg, size_mib, system_name_or_uuid=system
        )
    )

    console.print(
        f"Created media repository on {terminal_field(vg)}", style="green", markup=False
    )
    print_json(result)


def storage_create_media(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    vg: str = typer.Option(..., "--vg", help="Volume Group name or UUID"),
    name: str = typer.Option(
        ..., "--name", "-n", help="Media file name (e.g. aix.iso)"
    ),
    size_mib: int = typer.Option(
        ...,
        "--size-mib",
        help="Media size in MiB, a multiple of 1024 (sent to the HMC as whole GiB)",
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """Create a blank optical media (ISO container) in the media repository."""
    if not yes and not typer.confirm(
        f"Create media '{terminal_field(name)}' ({terminal_field(size_mib)}"
        f" MiB) on VG {terminal_field(vg)} (VIOS {terminal_field(vios)})?"
    ):
        raise typer.Abort()

    result = with_client(
        lambda hmc: create_optical_media(
            hmc, vios, vg, name, size_mib, system_name_or_uuid=system
        )
    )

    console.print(
        f"Created media '{terminal_field(name)}' on {terminal_field(vg)}",
        style="green",
        markup=False,
    )
    print_json(result)


def storage_delete_media_repo(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    vg: str = typer.Option(..., "--vg", help="Volume Group name or UUID"),
    yes: bool = typer.Option(False, "--yes", "-y"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """Delete the Virtual Media Repository from a volume group."""
    if not yes and not typer.confirm(
        f"Delete media repository on VG {terminal_field(vg)} (VIOS {terminal_field(vios)})?"
    ):
        raise typer.Abort()

    with_client(
        lambda hmc: delete_media_repository(hmc, vios, vg, system_name_or_uuid=system)
    )
    console.print(
        f"Deleted media repository on {terminal_field(vg)}", style="green", markup=False
    )


def storage_delete_media(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    vg: str = typer.Option(..., "--vg", help="Volume Group name or UUID"),
    media_name: str = typer.Option(
        ..., "--name", "-n", help="ISO image name to delete"
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """Delete an ISO image from the media repository."""
    if not yes and not typer.confirm(
        f"Delete media '{terminal_field(media_name)}' on VG {terminal_field(vg)}"
        f" (VIOS {terminal_field(vios)})?"
    ):
        raise typer.Abort()

    with_client(
        lambda hmc: delete_optical_media(
            hmc, vios, vg, media_name, system_name_or_uuid=system
        )
    )
    console.print(
        f"Deleted media '{terminal_field(media_name)}' on {terminal_field(vg)}",
        style="green",
        markup=False,
    )


def storage_get_media_repo(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    vg: str = typer.Option(..., "--vg", help="Volume Group name or UUID"),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output JSON"),
    format: InventoryFormat | None = typer.Option(
        None,
        "--format",
        help="Output format (default: table on TTY, tsv when redirected)",
    ),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """Get the Virtual Media Repository (RepositorySize is in GiB)."""
    selected = resolve_inventory_format(as_json, format)
    result = with_client(
        lambda hmc: get_media_repository(hmc, vios, vg, system_name_or_uuid=system)
    )
    print_inventory(
        result,
        selected,
        title="Media Repository (RepositorySize in GiB)",
        empty_msg="No media repository found",
    )


def storage_list_optical_media(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    vg: str = typer.Option(..., "--vg", help="Volume Group name or UUID"),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output JSON"),
    format: InventoryFormat | None = typer.Option(
        None,
        "--format",
        help="Output format (default: table on TTY, tsv when redirected)",
    ),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """List Virtual Optical Media in the Virtual Media Repository."""
    selected = resolve_inventory_format(as_json, format)
    media = with_client(
        lambda hmc: list_optical_media(hmc, vios, vg, system_name_or_uuid=system)
    )
    print_inventory(
        [asdict(item) for item in media],
        selected,
        ("Media Name", "Size (MiB)"),
        [(item.name, item.size_mib) for item in media],
        title=f"Optical Media in VG {vg}",
        empty_msg="No optical media found",
    )


def storage_mount_optical_media(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    lpar: str = typer.Option(..., "--lpar", help="Target LPAR name or UUID"),
    media_name: str = typer.Option(..., "--name", "-n", help="ISO image name to mount"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
    target_device: str | None = typer.Option(
        None, "--target-device", help="Pin the vtscsi target device name"
    ),
    ownership_override: bool = typer.Option(
        False,
        "--ownership-override",
        help="Bypass LPAR ownership protection after operator approval",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Mount an ISO from a VIOS media repository to an LPAR.

    A partition not activated since its profile changed refuses the mount with
    REST0269: activate or apply a profile once first. A refused mount can still
    leave a VIOS server adapter with no mapping; the error names the lshwres
    listing that shows it.
    """
    target = f"; target device {target_device}" if target_device else ""
    selected_system = f" in managed system {system}" if system else ""
    if not yes and not typer.confirm(
        f"Mount optical media '{terminal_field(media_name)}' from VIOS "
        f"{terminal_field(vios)} to LPAR {terminal_field(lpar)}"
        f"{terminal_field(selected_system)}{terminal_field(target)}?"
    ):
        raise typer.Abort()

    result = with_client(
        lambda hmc: mount_optical_media(
            hmc,
            vios,
            lpar,
            media_name=media_name,
            target_device=target_device,
            ownership_override=ownership_override,
            system_name_or_uuid=system,
        )
    )
    console.print(
        f"Mounted optical media '{terminal_field(media_name)}' on LPAR {terminal_field(lpar)}",
        style="green",
        markup=False,
    )
    print_json(result.resource)
    console.print(terminal_field(result.change_location.summary()), markup=False)


def storage_unmount_optical_media(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    lpar: str = typer.Option(..., "--lpar", help="Target LPAR name or UUID"),
    media_name: str = typer.Option(
        ..., "--name", "-n", help="ISO image name to unmount"
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
    """Unmount an ISO mapping while preserving the backing ISO."""
    if not confirm and not typer.confirm(
        f"Unmount optical media '{terminal_field(media_name)}' from LPAR "
        f"{terminal_field(lpar)} on VIOS {terminal_field(vios)}"
        f"? The backing ISO remains available."
    ):
        raise typer.Abort()

    async def _go(hmc: HMCClient) -> ChangeLocation:
        return await unmount_optical_media(
            hmc,
            vios,
            lpar,
            media_name=media_name,
            ownership_override=ownership_override,
            system_name_or_uuid=system,
        )

    location = with_client(_go)
    console.print(
        (
            f"Unmounted optical media '{terminal_field(media_name)}' from LPAR "
            f"{terminal_field(lpar)}; backing ISO remains"
        ),
        style="green",
        markup=False,
    )
    console.print(terminal_field(location.summary()), markup=False)


def storage_list_mappings(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    lpar: str | None = typer.Option(
        None, "--lpar", help="Scope to single LPAR by name or UUID"
    ),
    as_json: bool = typer.Option(False, "--json", "-j", help="Output JSON"),
    format: InventoryFormat | None = typer.Option(
        None,
        "--format",
        help="Output format (default: table on TTY, tsv when redirected)",
    ),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID"
    ),
) -> None:
    """List VirtualSCSIMappings on a VIOS (optionally scoped to an LPAR)."""
    selected = resolve_inventory_format(as_json, format)
    mappings = with_client(
        lambda hmc: list_storage_mappings(hmc, vios, lpar, system_name_or_uuid=system)
    )
    print_inventory(
        [asdict(mapping) for mapping in mappings],
        selected,
        ("Mapping ID", "Client LPAR", "Backing Storage", "Type"),
        [(m.id, m.lpar_uuid, m.backing_name, m.backing_kind) for m in mappings],
        title=f"Storage Mappings on {vios}",
        empty_msg="No storage mappings found",
    )


def storage_detach_mapping(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    mapping_id: str = typer.Argument(
        ...,
        help="Exact mapping ID (e.g. vhost0/vtscsi0) shown by storage list-mappings",
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
            (
                f"Delete storage mapping {terminal_field(mapping_id)} on VIOS "
                f"{terminal_field(vios)}? The backing storage (PhysicalVolume or "
                f"VirtualDisk) will be preserved."
            ),
            abort=True,
        )

    async def _go(hmc: HMCClient) -> ChangeLocation:
        return await detach_storage_mapping(
            hmc,
            vios,
            mapping_id,
            system_name_or_uuid=system,
            ownership_override=ownership_override,
        )

    location = with_client(_go)
    console.print(
        f"Deleted storage mapping {terminal_field(mapping_id)}",
        style="green",
        markup=False,
    )
    console.print(terminal_field(location.summary()), markup=False)


def storage_upload_iso(
    vios: str = typer.Argument(..., help="VIOS name or UUID"),
    vg: str = typer.Option(..., "--vg", help="Volume Group name or UUID"),
    media_name: str = typer.Option(
        ..., "--name", "-n", help="Target name for the ISO in the repository"
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
    """Upload an ISO to a VIOS media repository via the HMC web File API.

    ISO_SOURCE must be an http(s) URL; a local file path is not accepted. Its host
    must be on HMC_ISO_URL_ALLOWLIST (or iso_url_allowlist in the profile) — with
    no allowlist configured every URL is refused — and redirects are not followed.
    Computes the SHA-256 and size of the downloaded bytes before upload, refuses a
    volume group without a media repository and name collisions, reports success
    only once the repository lists the media, and releases the HMC upload handle
    on every outcome.
    """

    async def _go(hmc: HMCClient) -> dict[str, Any]:
        return await upload_iso(
            hmc,
            vios,
            vg,
            media_name,
            iso_source,
            system_name_or_uuid=system,
        )

    result = with_client(_go)

    if as_json:
        print_json(result)
    else:
        console.print(
            f"Upload status: {terminal_field(result.get('status', 'unknown'))}",
            style="green",
            markup=False,
        )
        console.print(
            f"  Media name: {terminal_field(result.get('media_name', 'N/A'))}",
            markup=False,
        )
        console.print(
            f"  Size: {terminal_field(format(result.get('media_size_bytes', 0), ','))} bytes",
            markup=False,
        )
        console.print(
            f"  Source SHA-256: {terminal_field(result.get('source_sha256', 'N/A'))}",
            markup=False,
        )
        if result.get("media"):
            console.print(
                f"  Media entry: {terminal_field(result['media'].get('MediaName', 'N/A'))}",
                markup=False,
            )


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
    group.command("mount-optical-media")(storage_mount_optical_media)
    group.command("unmount-optical-media")(storage_unmount_optical_media)
    group.command("list-mappings")(storage_list_mappings)
    group.command("detach-mapping")(storage_detach_mapping)
    group.command("upload-iso")(storage_upload_iso)
