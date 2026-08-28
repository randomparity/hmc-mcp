"""CLI commands for LPAR profile-backed boot configuration."""

from __future__ import annotations


import typer

from ..documents import (
    BOOT_DEVICE_SELECTORS,
)
from ..operations.lpar.boot_order import (
    clear_lpar_boot_order,
    read_lpar_boot_order,
    set_lpar_boot_order,
)
from .app import (
    _print_json,
    _with_client,
    console,
)


def lpars_read_boot_order(
    system_name: str = typer.Argument(..., help="Managed system name"),
    lpar_uuid: str = typer.Argument(..., help="Logical partition UUID"),
) -> None:
    """Read current, pending, and last-used boot-device state for an LPAR.

    Example:
        lpars read-boot-order system1 aaaa0000-0000-0000-0000-000000000001
    """
    result = _with_client(
        lambda hmc: read_lpar_boot_order(
            hmc,
            system_name_or_uuid=system_name,
            lpar_uuid=lpar_uuid,
        )
    )

    _print_json(result)


def lpars_set_boot_order(
    system_name: str = typer.Argument(..., help="Managed system name"),
    lpar_uuid: str = typer.Argument(..., help="Logical partition UUID"),
    devices: str = typer.Argument(
        ..., help="Ordered boot device list (comma-separated: cd,disk,network)"
    ),
    *,
    ownership_override: bool = typer.Option(
        False, "--ownership-override", help="Skip ownership token validation"
    ),
) -> None:
    """Set the pending boot order used on the LPAR's next activation.

    Example:
        lpars set-boot-order system1 lpar-uuid-123 "network,cd,disk"
    """
    device_list = [d.strip() for d in devices.split(",") if d.strip()]

    for device in device_list:
        if device not in BOOT_DEVICE_SELECTORS:
            raise typer.BadParameter(
                f"Invalid boot device selector: {device!r}. "
                f"Must be one of: {', '.join(BOOT_DEVICE_SELECTORS)}"
            )

    if not device_list:
        raise typer.BadParameter("Boot order must contain at least one device")

    result = _with_client(
        lambda hmc: set_lpar_boot_order(
            hmc,
            system_name_or_uuid=system_name,
            lpar_uuid=lpar_uuid,
            devices=device_list,
            ownership_override=ownership_override,
        )
    )

    console.print(f"[green]Boot order set to: {', '.join(device_list)}[/green]")
    _print_json(result)


def lpars_clear_boot_order(
    system_name: str = typer.Argument(..., help="Managed system name"),
    lpar_uuid: str = typer.Argument(..., help="Logical partition UUID"),
    *,
    ownership_override: bool = typer.Option(
        False, "--ownership-override", help="Skip ownership token validation"
    ),
) -> None:
    """Restore the HMC default boot order on the LPAR's next activation.

    Example:
        lpars clear-boot-order system1 aaaa0000-0000-0000-0000-000000000001
    """
    result = _with_client(
        lambda hmc: clear_lpar_boot_order(
            hmc,
            system_name_or_uuid=system_name,
            lpar_uuid=lpar_uuid,
            ownership_override=ownership_override,
        )
    )

    console.print("[green]Boot order cleared (restored defaults)[/green]")
    _print_json(result)


def register_commands(group: typer.Typer) -> None:
    """Register this module’s commands on *group*."""
    group.command("read-boot-order")(lpars_read_boot_order)
    group.command("set-boot-order")(lpars_set_boot_order)
    group.command("clear-boot-order")(lpars_clear_boot_order)
