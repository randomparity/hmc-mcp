"""MCP tools for PCIe and SR-IOV configuration."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .._app import (
    serialize_tool_result,
    with_client,
)
from ..operations.pcie import (
    SriovMode,
    assign_sriov_logical_port,
    set_sriov_adapter_mode,
    unassign_sriov_logical_port,
)
from ..tool_registry import tool_module

tool, register_tools, tool_security = tool_module()


@tool(effect="mutate", operation="sriov.set_mode", target_kind="managed_system")
def hmc_set_sriov_adapter_mode(
    system_name_or_uuid: str,
    adapter_id: str,
    mode: SriovMode,
    profile: str | None = None,
) -> str:
    """Verify that a physical adapter is already in the requested mode.

    The system may be given by CLI name or by UUID; a UUID is resolved to
    its CLI name via REST (falling back to an lssyscfg lookup over SSH when
    the REST API is unreachable) before the command runs.

    ``adapter_id`` is the physical adapter identifier as reported by
    ``hmc_list_io_slots``.

    ``mode`` must be one of:
      - ``"sriov"``      — enable SR-IOV mode (shared virtual functions)
      - ``"dedicated"``  — disable SR-IOV, use as a dedicated (passthrough) adapter

    Mode transitions are not admitted by the available same-family evidence and
    fail closed without mutation.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        adapter_id: Physical adapter ID returned by ``hmc_list_io_slots``.
        mode: ``sriov`` for shared virtual functions or ``dedicated`` for
            passthrough use.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    return with_client(
        lambda hmc: set_sriov_adapter_mode(hmc, system_name_or_uuid, adapter_id, mode),
        profile=profile,
    )


@tool(effect="mutate", operation="sriov.assign_logical_port", target_kind="lpar")
def hmc_assign_sriov_logical_port(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    adapter_id: str,
    physical_port_id: str,
    logical_port_id: str,
    capacity_percent: float,
    profile_name: str,
    ownership_override: bool = False,
    profile: str | None = None,
) -> dict[str, Any]:
    """Assign an evidence-backed Ethernet SR-IOV logical port.

    Args:
        system_name_or_uuid: Managed system name or UUID.
        lpar_name_or_uuid: Target partition name or UUID.
        adapter_id: Normalized SR-IOV adapter ID.
        physical_port_id: Normalized parent physical-port ID.
        logical_port_id: Normalized unconfigured logical-port ID.
        capacity_percent: Requested percentage capacity from 1 through 100.
        profile_name: Exact profile whose unchanged state is verified.
        ownership_override: Permit a separately approved ADR 0011 ownership override.
        profile: TOML connection profile name.
    """

    async def _go(hmc):
        return serialize_tool_result(
            await assign_sriov_logical_port(
                hmc,
                system_name_or_uuid,
                lpar_name_or_uuid,
                adapter_id,
                physical_port_id,
                logical_port_id,
                Decimal(str(capacity_percent)),
                profile_name=profile_name,
                ownership_override=ownership_override,
            )
        )

    return with_client(_go, profile=profile)


@tool(effect="mutate", operation="sriov.unassign_logical_port", target_kind="lpar")
def hmc_unassign_sriov_logical_port(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    adapter_id: str,
    physical_port_id: str,
    logical_port_id: str,
    profile_name: str,
    ownership_override: bool = False,
    profile: str | None = None,
) -> dict[str, Any]:
    """Unassign a profile logical port on a Not Activated LPAR.

    Args:
        system_name_or_uuid: Managed system name or UUID.
        lpar_name_or_uuid: Target partition name or UUID.
        profile_name: Exact partition profile to update and verify.
        adapter_id: Normalized SR-IOV adapter ID.
        physical_port_id: Normalized parent physical-port ID.
        logical_port_id: Normalized logical-port ID to remove.
        ownership_override: Permit a separately approved ADR 0011 ownership override.
        profile: TOML connection profile name.
    """

    async def _go(hmc):
        return serialize_tool_result(
            await unassign_sriov_logical_port(
                hmc,
                system_name_or_uuid,
                lpar_name_or_uuid,
                adapter_id,
                physical_port_id,
                logical_port_id,
                profile_name=profile_name,
                ownership_override=ownership_override,
            )
        )

    return with_client(_go, profile=profile)
