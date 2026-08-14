"""MCP tools for managed-system resources exposed only by the HMC CLI."""

from __future__ import annotations

from typing import Any

from ._app import _DESTRUCTIVE, _READ_ONLY, _ssh_with_client, mcp
from .ssh_commands import (
    PciClass,
    get_proc_compat_modes,
    list_io_slots,
    list_memory_pools,
    remove_memory_pool,
)


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_proc_compat_modes(
    system_name_or_uuid: str, profile: str | None = None
) -> list[str]:
    """List processor compatibility modes supported by a managed system."""
    return _ssh_with_client(
        lambda config, system_name, _: get_proc_compat_modes(config, system_name),
        system_name_or_uuid=system_name_or_uuid,
        profile=profile,
    )


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_io_slots(
    system_name_or_uuid: str,
    pci_class: PciClass = "all",
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """List physical I/O slots, optionally filtered by PCI class."""
    return _ssh_with_client(
        lambda config, system_name, _: list_io_slots(config, system_name, pci_class),
        system_name_or_uuid=system_name_or_uuid,
        profile=profile,
    )


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_memory_pools(
    system_name_or_uuid: str, profile: str | None = None
) -> list[dict[str, Any]]:
    """List shared memory pools and their assigned LPARs."""
    return _ssh_with_client(
        lambda config, system_name, _: list_memory_pools(config, system_name),
        system_name_or_uuid=system_name_or_uuid,
        profile=profile,
    )


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_remove_memory_pool(
    system_name_or_uuid: str, pool_name: str, profile: str | None = None
) -> str:
    """Remove an empty shared memory pool after server-side validation."""
    return _ssh_with_client(
        lambda config, system_name, _: remove_memory_pool(
            config, system_name, pool_name
        ),
        system_name_or_uuid=system_name_or_uuid,
        profile=profile,
    )
