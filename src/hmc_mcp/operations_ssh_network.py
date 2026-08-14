"""Selector-aware SSH network workflows shared by MCP and CLI adapters."""

from __future__ import annotations

from typing import Any

from .config import HMCConfig
from .ssh_commands import SriovMode
from .ssh_commands import add_vnic as _add_vnic
from .ssh_commands import list_fc_ports as _list_fc_ports
from .ssh_commands import list_sea_adapters as _list_sea_adapters
from .ssh_commands import list_vnics as _list_vnics
from .ssh_commands import remove_vnic as _remove_vnic
from .ssh_commands import set_sriov_adapter_mode as _set_sriov_adapter_mode
from .ssh_selectors import resolve_ssh_names


async def list_fc_ports(
    config: HMCConfig,
    system: str,
    lpar: str | None = None,
) -> list[dict[str, str]]:
    system_name, lpar_name = await resolve_ssh_names(config, system, lpar)
    assert system_name is not None
    return await _list_fc_ports(config, system_name, lpar_name)


async def list_sea_adapters(
    config: HMCConfig,
    system: str,
    lpar: str | None = None,
) -> list[dict[str, str]]:
    system_name, lpar_name = await resolve_ssh_names(config, system, lpar)
    assert system_name is not None
    return await _list_sea_adapters(config, system_name, lpar_name)


async def set_sriov_adapter_mode(
    config: HMCConfig,
    system: str,
    adapter_id: str,
    mode: SriovMode,
) -> str:
    system_name, _ = await resolve_ssh_names(config, system, None)
    assert system_name is not None
    return await _set_sriov_adapter_mode(config, system_name, adapter_id, mode)


async def list_vnics(
    config: HMCConfig,
    system: str,
    lpar: str,
) -> list[dict[str, Any]]:
    system_name, lpar_name = await resolve_ssh_names(config, system, lpar)
    assert system_name is not None and lpar_name is not None
    return await _list_vnics(config, system_name, lpar_name)


async def add_vnic(
    config: HMCConfig,
    system: str,
    lpar: str,
    capacity: int,
    vswitch_name: str,
    port_vlan_id: int,
    backing_devices: str | None = None,
) -> str:
    system_name, lpar_name = await resolve_ssh_names(config, system, lpar)
    assert system_name is not None and lpar_name is not None
    return await _add_vnic(
        config,
        system_name,
        lpar_name,
        capacity,
        vswitch_name,
        port_vlan_id,
        backing_devices,
    )


async def remove_vnic(
    config: HMCConfig,
    system: str,
    lpar: str,
    vnic_id: str,
) -> str:
    system_name, lpar_name = await resolve_ssh_names(config, system, lpar)
    assert system_name is not None and lpar_name is not None
    return await _remove_vnic(config, system_name, lpar_name, vnic_id)
