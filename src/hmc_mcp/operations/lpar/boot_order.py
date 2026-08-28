"""LPAR boot-order read and mutation operations."""

from __future__ import annotations

import logging
from typing import Any

from hmc_mcp.client.core import HMCClient
from ...documents import (
    BOOT_DEVICE_SELECTORS,
    build_boot_order_document,
    build_clear_boot_order_document,
)
from ...errors import HMCError
from ...resource_identity import resolve_lpar_uuid
from .errors import translate_lpar_write_error
from hmc_mcp.operations.ownership import resolve_and_authorize_lpar_mutation

_logger = logging.getLogger(__name__)

async def read_lpar_boot_order(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
) -> dict[str, Any]:
    """Read current, pending, and last-used boot-device state for an LPAR."""
    lpar_uuid = await resolve_lpar_uuid(
        hmc, lpar_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
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
    lpar_name_or_uuid: str,
    devices: list[str],
    *,
    ownership_override: bool = False,
) -> dict[str, Any] | None:
    """Set the pending boot order used on the LPAR's next activation."""
    for device in devices:
        if device not in BOOT_DEVICE_SELECTORS:
            raise ValueError(
                f"Invalid boot device selector: {device!r}. "
                f"Must be one of: {BOOT_DEVICE_SELECTORS}"
            )

    if not devices:
        raise ValueError("Boot order must contain at least one device")

    lpar_uuid = await resolve_and_authorize_lpar_mutation(
        hmc,
        system_name_or_uuid,
        lpar_name_or_uuid,
        ownership_override=ownership_override,
    )

    xml = build_boot_order_document(devices)
    try:
        updated = await hmc.modify_logical_partition(lpar_uuid, xml)
    except HMCError as exc:
        translate_lpar_write_error(exc)
        raise

    _logger.info(
        "Set boot order for LPAR %s (%s) to: %s",
        lpar_name_or_uuid,
        lpar_uuid,
        ", ".join(devices),
    )

    return updated


async def clear_lpar_boot_order(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    *,
    ownership_override: bool = False,
) -> dict[str, Any] | None:
    """Restore the HMC default boot order on the LPAR's next activation."""
    lpar_uuid = await resolve_and_authorize_lpar_mutation(
        hmc,
        system_name_or_uuid,
        lpar_name_or_uuid,
        ownership_override=ownership_override,
    )

    xml = build_clear_boot_order_document()
    try:
        updated = await hmc.modify_logical_partition(lpar_uuid, xml)
    except HMCError as exc:
        translate_lpar_write_error(exc)
        raise

    _logger.info(
        "Cleared boot order for LPAR %s (%s) (restored defaults)",
        lpar_name_or_uuid,
        lpar_uuid,
    )

    return updated
