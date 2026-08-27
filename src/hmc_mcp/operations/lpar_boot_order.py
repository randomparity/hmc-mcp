"""LPAR boot-order read and mutation operations."""

from __future__ import annotations

import logging
from typing import Any

from ..client import HMCClient
from ..documents import (
    BOOT_DEVICE_SELECTORS,
    build_boot_order_document,
    build_clear_boot_order_document,
)
from ..errors import HMCError
from ..resource_identity import resolve_system_uuid
from .lpar_errors import translate_lpar_write_error
from .lpar_ownership import authorize_lpar_mutation, resolve_lpar_ownership_names

_logger = logging.getLogger(__name__)

async def read_lpar_boot_order(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_uuid: str,
) -> dict[str, Any]:
    """Read an LPAR's boot order state (pending and current).

    Returns the boot device order for the LPAR, including both the pending
    boot string (next boot) and the current boot device list.

    Args:
        hmc: HMC client instance.
        system_name_or_uuid: CLI name or UUID of the system.
        lpar_uuid: UUID of the LPAR.

    Returns:
        Dictionary with boot order information containing:
        - lpar_uuid: UUID of the LPAR
        - lpar_name: Name of the LPAR
        - pending_boot_string: The PendingBootString for the next boot
        - boot_device_list: The current BootDeviceList
        - last_booted_device_string: The device used on last boot

    Raises:
        ValueError: If the LPAR cannot be resolved or found.
    """
    lpar = await hmc.get_logical_partition(lpar_uuid)
    if not lpar:
        raise ValueError(f"LPAR {lpar_uuid!r} not found")

    resource = lpar.get("Resource") or {}
    boot_list_info = resource.get("BootListInformation") or {}

    return {
        "lpar_uuid": lpar_uuid,
        "lpar_name": resource.get("PartitionName"),
        "pending_boot_string": boot_list_info.get("PendingBootString"),
        "boot_device_list": boot_list_info.get("BootDeviceList"),
        "last_booted_device_string": boot_list_info.get("LastBootedDeviceString"),
    }


async def set_lpar_boot_order(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_uuid: str,
    devices: list[str],
    *,
    ownership_override: bool = False,
) -> dict[str, Any] | None:
    """Set an LPAR's boot order to a validated device selector list.

    Sets the PendingBootString to an ordered list of boot device selectors.
    Changes take effect on the next LPAR activation (no reboot required).

    Args:
        hmc: HMC client instance.
        system_name_or_uuid: CLI name or UUID of the system.
        lpar_uuid: UUID of the LPAR.
        devices: Ordered list of boot device selectors (cd, disk, network).
        ownership_override: If True, skip ownership token validation.

    Returns:
        Updated LPAR resource if successful, None otherwise.

    Raises:
        ValueError: If device selectors are invalid or LPAR cannot be resolved.
    """
    # Validate device selectors
    for device in devices:
        if device not in BOOT_DEVICE_SELECTORS:
            raise ValueError(
                f"Invalid boot device selector: {device!r}. "
                f"Must be one of: {BOOT_DEVICE_SELECTORS}"
            )

    if not devices:
        raise ValueError("Boot order must contain at least one device")

    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    system_name, lpar_name = await resolve_lpar_ownership_names(
        hmc, system_uuid, system_name_or_uuid, lpar_uuid
    )
    await authorize_lpar_mutation(
        hmc, system_name, lpar_name, ownership_override=ownership_override
    )

    xml = build_boot_order_document(devices)
    try:
        updated = await hmc.modify_logical_partition(lpar_uuid, xml)
    except HMCError as exc:
        translate_lpar_write_error(exc)
        raise

    _logger.info(
        "Set boot order for LPAR %s (%s) to: %s",
        lpar_name,
        lpar_uuid,
        ", ".join(devices),
    )

    return updated


async def clear_lpar_boot_order(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_uuid: str,
    *,
    ownership_override: bool = False,
) -> dict[str, Any] | None:
    """Clear an LPAR's boot order (restore HMC defaults).

    Clears the PendingBootString, restoring the default boot behavior.
    Changes take effect on the next LPAR activation (no reboot required).

    Args:
        hmc: HMC client instance.
        system_name_or_uuid: CLI name or UUID of the system.
        lpar_uuid: UUID of the LPAR.
        ownership_override: If True, skip ownership token validation.

    Returns:
        Updated LPAR resource if successful, None otherwise.

    Raises:
        ValueError: If LPAR cannot be resolved.
    """
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    system_name, lpar_name = await resolve_lpar_ownership_names(
        hmc, system_uuid, system_name_or_uuid, lpar_uuid
    )
    await authorize_lpar_mutation(
        hmc, system_name, lpar_name, ownership_override=ownership_override
    )

    xml = build_clear_boot_order_document()
    try:
        updated = await hmc.modify_logical_partition(lpar_uuid, xml)
    except HMCError as exc:
        translate_lpar_write_error(exc)
        raise

    _logger.info(
        "Cleared boot order for LPAR %s (%s) (restored defaults)",
        lpar_name,
        lpar_uuid,
    )

    return updated
