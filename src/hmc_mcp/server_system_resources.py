"""MCP tools for managed-system resources exposed only by the HMC CLI."""

from __future__ import annotations

from .tool_registry import tool_module

from typing import Any

from ._app import _ssh_with_client
from .ssh_commands import (
    PciClass,
    get_proc_compat_modes,
    list_io_slots,
    list_memory_pools,
    remove_memory_pool,
)


tool, register_tools, tool_security = tool_module()


@tool(effect="read", operation="system.get_proc_compat_modes", target_kind="managed_system")
def hmc_get_proc_compat_modes(
    system_name_or_uuid: str, profile: str | None = None
) -> list[str]:
    """List processor compatibility modes supported by a managed system.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return _ssh_with_client(
        lambda config, system_name, _: get_proc_compat_modes(config, system_name),
        system_name_or_uuid=system_name_or_uuid,
        profile=profile,
    )


@tool(effect="read", operation="io_slot.list", target_kind="managed_system")
def hmc_list_io_slots(
    system_name_or_uuid: str,
    pci_class: PciClass = "all",
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """List physical I/O slots, optionally filtered by PCI class.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        pci_class: ``all``, ``network``, ``storage``, or ``other`` slot class.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return _ssh_with_client(
        lambda config, system_name, _: list_io_slots(config, system_name, pci_class),
        system_name_or_uuid=system_name_or_uuid,
        profile=profile,
    )


@tool(effect="read", operation="memory_pool.list", target_kind="managed_system")
def hmc_list_memory_pools(
    system_name_or_uuid: str, profile: str | None = None
) -> list[dict[str, Any]]:
    """List shared memory pools and their assigned LPARs.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return _ssh_with_client(
        lambda config, system_name, _: list_memory_pools(config, system_name),
        system_name_or_uuid=system_name_or_uuid,
        profile=profile,
    )


@tool(effect="destructive", operation="memory_pool.remove", target_kind="managed_system")
def hmc_remove_memory_pool(
    system_name_or_uuid: str, pool_name: str, profile: str | None = None
) -> str:
    """Remove an empty shared memory pool after server-side validation.

    The pool must have no assigned partitions; inspect assignments with
    ``hmc_list_memory_pools`` before calling.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        pool_name: Exact empty shared-memory-pool name to remove.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return _ssh_with_client(
        lambda config, system_name, _: remove_memory_pool(
            config, system_name, pool_name
        ),
        system_name_or_uuid=system_name_or_uuid,
        profile=profile,
    )
