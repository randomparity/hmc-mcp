"""MCP tools for FC, SEA, and vNIC resources."""

from __future__ import annotations

from ..tool_registry import tool_module

from dataclasses import asdict
from decimal import Decimal
import json
from typing import Any

from .._app import (
    with_config,
    with_client,
)

from ..operations.vnic import (
    VnicBackingSelector,
    VnicPartialError,
    add_vnic,
    list_fc_ports,
    list_sea_adapters,
    list_vnics,
    remove_vnic,
)


tool, register_tools, tool_security = tool_module()


@tool(effect="read", operation="network.list_fc_ports", target_kind="managed_system")
def hmc_list_fc_ports(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str | None = None,
    profile: str | None = None,
) -> list[dict[str, str]]:
    """List Virtual Fibre Channel (NPIV) adapters for a managed system via the HMC CLI.

    Runs ``lshwres -r virtualio --rsubtype fc --level lpar -m <system_name>``
    on the HMC via SSH and returns parsed dicts with fields including
    lpar_name, slot_num, wwpns, and remote_lpar_id.

    The system and partition may be given by CLI name or by UUID; UUIDs
    are resolved to their CLI names via REST (falling back to an lssyscfg
    lookup over SSH when the REST API is unreachable) before the command
    runs. Pass lpar_name_or_uuid to restrict results to a single partition.
    Either a CLI name or UUID works; use hmc_list_systems to find a system
    UUID and hmc_list_lpars to find an LPAR UUID.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        lpar_name_or_uuid: Optional partition name or UUID to restrict results.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    return with_config(
        lambda config: list_fc_ports(config, system_name_or_uuid, lpar_name_or_uuid),
        profile=profile,
    )


@tool(effect="read", operation="network.list_sea", target_kind="managed_system")
def hmc_list_sea_adapters(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str | None = None,
    profile: str | None = None,
) -> list[dict[str, str]]:
    """List Shared Ethernet Adapter (SEA) virtual Ethernet ports via the HMC CLI.

    Runs ``lshwres -r virtualio --rsubtype eth --level lpar -m <system_name>
    -F lpar_name,port_vlan_id,vswitch,state,trunk_priority`` on the HMC via
    SSH and returns parsed dicts with those five fields.

    The system and partition may be given by CLI name or by UUID; UUIDs
    are resolved to their CLI names via REST (falling back to an lssyscfg
    lookup over SSH when the REST API is unreachable) before the command
    runs. Pass lpar_name_or_uuid to restrict results to a single partition.
    Either a CLI name or UUID works; use hmc_list_systems to find a system
    UUID and hmc_list_lpars to find an LPAR UUID.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        lpar_name_or_uuid: Optional partition name or UUID to restrict results.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    return with_config(
        lambda config: list_sea_adapters(
            config, system_name_or_uuid, lpar_name_or_uuid
        ),
        profile=profile,
    )


@tool(effect="read", operation="vnic.list", target_kind="lpar")
def hmc_list_vnics(
    system_name_or_uuid: str, lpar_name_or_uuid: str, profile: str | None = None
) -> list[dict[str, Any]]:
    """List vNICs (SR-IOV-backed Virtual NICs) on an LPAR via the HMC CLI.

    Returns the HMC inventory for the selected partition, including slot and
    backing-device readback fields.

    The system and partition may be given by CLI name or by UUID; UUIDs
    are resolved to their CLI names via REST (falling back to an lssyscfg
    lookup over SSH when the REST API is unreachable) before the command
    runs. Use ``hmc_list_systems`` to find a system UUID and
    ``hmc_list_lpars`` to find an LPAR UUID.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        lpar_name_or_uuid: Partition name or UUID from ``hmc_list_lpars``.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    return with_config(
        lambda config: list_vnics(config, system_name_or_uuid, lpar_name_or_uuid),
        profile=profile,
    )


@tool(effect="mutate", operation="vnic.add", target_kind="lpar")
def hmc_add_vnic(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    vios_name: str,
    vios_lpar_id: str,
    adapter_id: str,
    physical_port_id: str,
    capacity_percent: float,
    port_vlan_id: int,
    ownership_override: bool = False,
    profile: str | None = None,
) -> dict[str, Any]:
    """Add a vNIC (SR-IOV-backed Virtual NIC) to an LPAR via the HMC CLI.

    Verifies the selected VIOS, adapter, physical port, capacity, and final
    HMC readback. Returns a structured mutation and reconciliation result.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        lpar_name_or_uuid: Partition name or UUID from ``hmc_list_lpars``.
        vios_name: VIOS partition name from managed-system inventory.
        vios_lpar_id: VIOS partition ID matching ``vios_name``.
        adapter_id: SR-IOV physical adapter identifier.
        physical_port_id: Physical port identifier on ``adapter_id``.
        capacity_percent: Requested backing capacity percentage.
        port_vlan_id: Port VLAN ID assigned to untagged traffic.
        ownership_override: Bypass ownership rejection only after operator approval.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go(hmc):
        try:
            result = await add_vnic(
                hmc,
                system_name_or_uuid,
                lpar_name_or_uuid,
                VnicBackingSelector(
                    vios_name,
                    vios_lpar_id,
                    adapter_id,
                    physical_port_id,
                    Decimal(str(capacity_percent)),
                ),
                port_vlan_id,
                ownership_override=ownership_override,
            )
        except VnicPartialError as exc:
            evidence = json.dumps(asdict(exc.result), default=str)
            raise VnicPartialError(f"{exc}; result={evidence}", exc.result) from exc
        return asdict(result)

    return with_client(_go, profile=profile)


@tool(effect="destructive", operation="vnic.remove", target_kind="lpar")
def hmc_remove_vnic(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    slot_num: str,
    ownership_override: bool = False,
    profile: str | None = None,
) -> dict[str, Any]:
    """Remove a vNIC from an LPAR via the HMC CLI.

    Removes the selected slot after verification and returns a structured
    mutation and reconciliation result.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        lpar_name_or_uuid: Partition name or UUID from ``hmc_list_lpars``.
        slot_num: vNIC slot number returned by ``hmc_list_vnics``.
        ownership_override: Bypass ownership rejection only after operator approval.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go(hmc):
        try:
            result = await remove_vnic(
                hmc,
                system_name_or_uuid,
                lpar_name_or_uuid,
                slot_num,
                ownership_override=ownership_override,
            )
        except VnicPartialError as exc:
            evidence = json.dumps(asdict(exc.result), default=str)
            raise VnicPartialError(f"{exc}; result={evidence}", exc.result) from exc
        return asdict(result)

    return with_client(_go, profile=profile)
