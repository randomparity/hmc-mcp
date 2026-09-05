"""MCP tools for LPAR boot order and ownership."""

from __future__ import annotations

from typing import Any

from hmc_mcp.operations.ownership import list_lpar_ownership

from ..._app import with_client
from ...operations.lpar.boot_order import (
    clear_lpar_boot_order,
    read_lpar_boot_order,
    set_lpar_boot_order,
)
from ...tool_registry import tool_module

tool, register_tools, tool_security = tool_module()

# LPAR Boot Order Tools


@tool(effect="read", operation="boot_order.read", target_kind="lpar")
def hmc_read_lpar_boot_order(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    profile: str | None = None,
) -> dict[str, Any]:
    """Read current, pending, and last-used boot-device state for an LPAR.

    Args:
        system_name_or_uuid: CLI name or UUID of the managed system.
        lpar_name_or_uuid: Name or UUID of the logical partition.
        profile: Configured HMC profile, or the default when omitted.
    """

    async def read_boot_order(hmc) -> dict[str, Any]:
        result = await read_lpar_boot_order(
            hmc,
            system_name_or_uuid=system_name_or_uuid,
            lpar_name_or_uuid=lpar_name_or_uuid,
        )
        return result

    return with_client(read_boot_order, profile=profile)


@tool(effect="mutate", operation="boot_order.set", target_kind="lpar")
def hmc_set_lpar_boot_order(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    devices: list[str],
    *,
    ownership_override: bool = False,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Set the pending boot order used on the LPAR's next activation.

    Args:
        system_name_or_uuid: CLI name or UUID of the managed system.
        lpar_name_or_uuid: Name or UUID of the logical partition.
        devices: Boot device selectors in first-to-last order.
        ownership_override: Skip ownership-token validation when true.
        profile: Configured HMC profile, or the default when omitted.
    """

    async def set_boot_order(hmc) -> dict[str, Any] | None:
        result = await set_lpar_boot_order(
            hmc,
            system_name_or_uuid=system_name_or_uuid,
            lpar_name_or_uuid=lpar_name_or_uuid,
            devices=devices,
            ownership_override=ownership_override,
        )
        return result

    return with_client(set_boot_order, profile=profile)


@tool(effect="mutate", operation="boot_order.clear", target_kind="lpar")
def hmc_clear_lpar_boot_order(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    *,
    ownership_override: bool = False,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Restore the HMC default boot order on the LPAR's next activation.

    Args:
        system_name_or_uuid: CLI name or UUID of the managed system.
        lpar_name_or_uuid: Name or UUID of the logical partition.
        ownership_override: Skip ownership-token validation when true.
        profile: Configured HMC profile, or the default when omitted.
    """

    async def clear_boot_order(hmc) -> dict[str, Any] | None:
        result = await clear_lpar_boot_order(
            hmc,
            system_name_or_uuid=system_name_or_uuid,
            lpar_name_or_uuid=lpar_name_or_uuid,
            ownership_override=ownership_override,
        )
        return result

    return with_client(clear_boot_order, profile=profile)


@tool(effect="read", operation="lpar.list_ownership", target_kind="managed_system")
def hmc_list_lpar_ownership(
    system_name_or_uuid: str | None = None,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """Read parsed ownership for every LPAR on a system in one REST call.

    Parses the advisory ADR 0011 ownership token out of each partition's
    description via the bulk list feed, so one request covers the whole system
    (#375). Every partition is returned: ``owned`` partitions carry the
    ``owner`` agent id; a description with no well-formed stamp is reported
    with ``unparsed=True``; a partition with no description at all has
    ``description=None`` — the three facts stay distinct for reconciliation.

    Args:
        system_name_or_uuid: Optional SystemName or UUID whose partitions to
            read; omitted reads the fleet-wide LogicalPartition feed in one
            call (entries then carry no parent-system attribution).
        profile: Optional configured HMC profile name; uses the default when
            omitted.
    """

    return with_client(
        lambda hmc: list_lpar_ownership(hmc, system_name_or_uuid),
        profile=profile,
    )

