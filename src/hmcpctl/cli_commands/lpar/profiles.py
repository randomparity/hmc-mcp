"""CLI commands for LPAR profile-backed boot configuration."""

from __future__ import annotations

import typer
from rich.markup import escape

from ...documents import join_boot_device_paths
from ...operations.lpar.boot_order import (
    clear_lpar_boot_order,
    read_lpar_boot_order,
    set_lpar_boot_order,
)
from ..output import console, print_json
from ..runtime import with_client


def lpars_read_boot_order(
    system_name: str = typer.Argument(..., help="Managed system name"),
    lpar_name_or_uuid: str = typer.Argument(..., help="Logical partition name or UUID"),
) -> None:
    """Read current, pending, and last-used boot-device state for an LPAR.

    Example:
        lpars read-boot-order system1 aaaa0000-0000-0000-0000-000000000001
    """
    result = with_client(
        lambda hmc: read_lpar_boot_order(
            hmc,
            system_name_or_uuid=system_name,
            lpar_name_or_uuid=lpar_name_or_uuid,
        )
    )

    print_json(result)


def lpars_set_boot_order(
    system_name: str = typer.Argument(..., help="Managed system name"),
    lpar_name_or_uuid: str = typer.Argument(..., help="Logical partition name or UUID"),
    devices: list[str] = typer.Argument(
        ...,
        help=(
            "Open Firmware device paths, first to last, as read-boot-order reports them. "
            "A never-booted partition reports none: take the path from SMS or leave the "
            "boot order unset"
        ),
    ),
    *,
    ownership_override: bool = typer.Option(
        False, "--ownership-override", help="Skip ownership token validation"
    ),
) -> None:
    """Set the pending boot order used on the LPAR's next activation.

    Example:
        lpars set-boot-order system1 lpar-uuid-123 /vdevice/v-scsi@30000002/disk@8100000000000000
    """
    try:
        boot_string = join_boot_device_paths(devices)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    result = with_client(
        lambda hmc: set_lpar_boot_order(
            hmc,
            system_name_or_uuid=system_name,
            lpar_name_or_uuid=lpar_name_or_uuid,
            devices=devices,
            ownership_override=ownership_override,
        )
    )

    console.print(f"[green]Boot order set to: {escape(boot_string)}[/green]")
    print_json(result)


def lpars_clear_boot_order(
    system_name: str = typer.Argument(..., help="Managed system name"),
    lpar_name_or_uuid: str = typer.Argument(..., help="Logical partition name or UUID"),
    *,
    ownership_override: bool = typer.Option(
        False, "--ownership-override", help="Skip ownership token validation"
    ),
) -> None:
    """Restore the HMC default boot order on the LPAR's next activation.

    Example:
        lpars clear-boot-order system1 aaaa0000-0000-0000-0000-000000000001
    """
    result = with_client(
        lambda hmc: clear_lpar_boot_order(
            hmc,
            system_name_or_uuid=system_name,
            lpar_name_or_uuid=lpar_name_or_uuid,
            ownership_override=ownership_override,
        )
    )

    console.print("[green]Boot order cleared (restored defaults)[/green]")
    print_json(result)


def register_commands(group: typer.Typer) -> None:
    """Register this module’s commands on *group*."""
    group.command("read-boot-order")(lpars_read_boot_order)
    group.command("set-boot-order")(lpars_set_boot_order)
    group.command("clear-boot-order")(lpars_clear_boot_order)
