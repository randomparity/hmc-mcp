"""MCP tools that run HMC CLI commands over SSH (no REST equivalent)."""

from __future__ import annotations

from typing import Any

from ._app import (
    _DESTRUCTIVE,
    _READ_ONLY,
    _ssh_with_client,
    mcp,
)

from .ssh_commands import (
    PciClass,
    validate_lpar_description,
    get_lpar_description,
    get_lpar_msp,
    get_lpar_proc_compat,
    get_proc_compat_modes,
    list_io_slots,
    list_memory_pools,
    remove_memory_pool,
    set_lpar_description,
    set_lpar_msp,
    set_lpar_proc_compat,
)
from .operations_lpar import authorize_lpar_mutation
from .client import HMCClient


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_lpar_description(
    system_name_or_uuid: str, lpar_name_or_uuid: str, profile: str | None = None
) -> str:
    """Return an LPAR's CLI-only description, resolving names or UUIDs."""
    return _ssh_with_client(
        lambda config, system_name, lpar_name: get_lpar_description(
            config, system_name, lpar_name
        ),
        system_name_or_uuid=system_name_or_uuid,
        lpar_name_or_uuid=lpar_name_or_uuid,
        profile=profile,
    )


@mcp.tool
def hmc_set_lpar_description(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    description: str,
    ownership_override: bool = False,
    profile: str | None = None,
) -> str:
    """Set an LPAR's CLI-only description after validating printable ASCII.

    The current description's ownership token is enforced before overwrite.
    Foreign-owned or malformed tokens are rejected. Set ownership_override=True
    only after explicit operator approval.

    WARNING: This changes LPAR configuration on the selected HMC.
    """
    validate_lpar_description(description)
    async def _set(config, system_name, lpar_name):
        hmc = HMCClient(config)
        await authorize_lpar_mutation(
            hmc,
            system_name,
            lpar_name,
            ownership_override=ownership_override,
        )
        return await set_lpar_description(config, system_name, lpar_name, description)

    return _ssh_with_client(
        _set,
        system_name_or_uuid=system_name_or_uuid,
        lpar_name_or_uuid=lpar_name_or_uuid,
        profile=profile,
    )


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_lpar_msp(
    system_name_or_uuid: str, lpar_name_or_uuid: str, profile: str | None = None
) -> bool:
    """Return an LPAR's CLI-only Migratable Service Partition flag."""
    return _ssh_with_client(
        lambda config, system_name, lpar_name: get_lpar_msp(
            config, system_name, lpar_name
        ),
        system_name_or_uuid=system_name_or_uuid,
        lpar_name_or_uuid=lpar_name_or_uuid,
        profile=profile,
    )


@mcp.tool
def hmc_set_lpar_msp(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    enabled: bool,
    profile: str | None = None,
) -> str:
    """Set a VIOS partition's Migratable Service Partition flag.

    The command rejects non-VIOS partitions. WARNING: this changes LPAR
    configuration on the selected HMC.
    """
    return _ssh_with_client(
        lambda config, system_name, lpar_name: set_lpar_msp(
            config, system_name, lpar_name, enabled
        ),
        system_name_or_uuid=system_name_or_uuid,
        lpar_name_or_uuid=lpar_name_or_uuid,
        profile=profile,
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
def hmc_get_lpar_proc_compat(
    system_name_or_uuid: str, lpar_name_or_uuid: str, profile: str | None = None
) -> dict[str, str]:
    """Return an LPAR's desired and current processor compatibility modes."""
    return _ssh_with_client(
        lambda config, system_name, lpar_name: get_lpar_proc_compat(
            config, system_name, lpar_name
        ),
        system_name_or_uuid=system_name_or_uuid,
        lpar_name_or_uuid=lpar_name_or_uuid,
        profile=profile,
    )


@mcp.tool
def hmc_set_lpar_proc_compat(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    mode: str,
    profile: str | None = None,
) -> str:
    """Set an LPAR's processor compatibility mode.

    WARNING: This changes LPAR configuration on the selected HMC.
    """
    return _ssh_with_client(
        lambda config, system_name, lpar_name: set_lpar_proc_compat(
            config, system_name, lpar_name, mode
        ),
        system_name_or_uuid=system_name_or_uuid,
        lpar_name_or_uuid=lpar_name_or_uuid,
        profile=profile,
    )


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_io_slots(
    system_name_or_uuid: str,
    pci_class: PciClass = "all",
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """List physical I/O slots, optionally filtered by PCI class.

    pci_class filters by PCI class:
      - ``"all"``   — return every slot (default)
      - ``"eth"``   — Ethernet adapters (PCI class 0200)
      - ``"sas"``   — SAS/SCSI adapters (PCI class 0104)
      - ``"san"``   — Fibre Channel / SAN adapters (PCI class 0C04)
      - ``"nvme"``  — NVMe adapters (PCI class 0108)
    """
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
    """Remove an empty shared memory pool.

    The command rejects missing pools and pools with assigned LPARs. WARNING:
    removal is immediate and permanent; confirm the system and pool first.
    """
    return _ssh_with_client(
        lambda config, system_name, _: remove_memory_pool(
            config, system_name, pool_name
        ),
        system_name_or_uuid=system_name_or_uuid,
        profile=profile,
    )
