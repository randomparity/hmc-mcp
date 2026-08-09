"""MCP tools for virtual networks, FC/SEA adapters, SR-IOV mode, and vNICs.
"""

from __future__ import annotations

import shlex
from typing import Any

from ._app import (
    _DESTRUCTIVE,
    _READ_ONLY,
    _lpar_name,
    _run,
    _system_name,
    mcp,
    with_client,
)

from .common import client_from_env
from .config import HMCConfig
from .ssh import (
    HMCCLIError,
    list_fc_ports,
    list_sea_adapters,
    list_vnics,
    run_hmc_cli,
)



@mcp.tool(annotations=_READ_ONLY)
def hmc_list_virtual_switches(system_uuid: str) -> list[dict[str, Any]]:
    """List VirtualSwitches on a managed system (names, SwitchIDs, mode).

    The SwitchID is what hmc_create_virtual_network and hmc_add_network_adapter
    reference.
    """

    return with_client(lambda hmc: hmc.list_virtual_switches(system_uuid))


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_virtual_networks(system_uuid: str) -> list[dict[str, Any]]:
    """List Virtual Networks (VLANs) on a managed system."""

    return with_client(lambda hmc: hmc.list_virtual_networks(system_uuid))


@mcp.tool
def hmc_create_virtual_network(
    system_uuid: str,
    name: str,
    vlan_id: int,
    vswitch_id: int,
    tagged: bool = False,
) -> dict[str, Any] | None:
    """Create a Virtual Network (VLAN) on a managed system.

    vswitch_id is the numeric SwitchID of the backing VirtualSwitch (see
    hmc_list_virtual_switches). tagged sets whether bridged traffic keeps the
    VLAN tag.
    """

    return with_client(
        lambda hmc: hmc.create_virtual_network(
            system_uuid, name, vlan_id, vswitch_id, tagged=tagged
        )
    )


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_delete_virtual_network(system_uuid: str, network_uuid: str) -> str:
    """Delete a Virtual Network from a managed system.

    Note: a network referenced by a NetworkBridge, or equal to a trunk
    adapter's PVID, cannot be deleted until the bridge is removed. Returns a
    confirmation string (immediate delete — no job to poll).
    """

    with_client(lambda hmc: hmc.delete_virtual_network(system_uuid, network_uuid))
    return f"Deleted VirtualNetwork {network_uuid} from {system_uuid}"


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_network_bridges(system_uuid: str) -> list[dict[str, Any]]:
    """List NetworkBridges (Shared Ethernet Adapters) on a managed system."""

    return with_client(lambda hmc: hmc.list_network_bridges(system_uuid))


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_fc_ports(system_uuid: str, lpar_uuid: str | None = None) -> list[dict]:
    """List Virtual Fibre Channel (NPIV) adapters for a managed system via the HMC CLI.

    Runs ``lshwres -r virtualio --rsubtype fc --level lpar -m <system_name>``
    on the HMC via SSH and returns parsed dicts with fields including
    lpar_name, slot_num, wwpns, and remote_lpar_id.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs. Pass lpar_uuid to restrict results to a single
    partition. Use hmc_list_systems to find system_uuid and hmc_list_lpars
    to find lpar_uuid.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid) if lpar_uuid else None
        return await list_fc_ports(HMCConfig(), system_name, lpar_name)

    return _run(_go())


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_sea_adapters(system_uuid: str, lpar_uuid: str | None = None) -> list[dict]:
    """List Shared Ethernet Adapter (SEA) virtual Ethernet ports via the HMC CLI.

    Runs ``lshwres -r virtualio --rsubtype eth --level lpar -m <system_name>
    -F lpar_name,port_vlan_id,vswitch,state,trunk_priority`` on the HMC via
    SSH and returns parsed dicts with those five fields.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs. Pass lpar_uuid to restrict results to a single
    partition. Use hmc_list_systems to find system_uuid and hmc_list_lpars
    to find lpar_uuid.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid) if lpar_uuid else None
        return await list_sea_adapters(HMCConfig(), system_name, lpar_name)

    return _run(_go())



_VALID_SRIOV_MODES = {"sriov", "dedicated"}


@mcp.tool
def hmc_set_sriov_adapter_mode(
    system_uuid: str,
    adapter_id: str,
    mode: str,
) -> str:
    """Toggle a physical SR-IOV adapter between SR-IOV and dedicated mode.

    Runs ``chhwres -r sriov -m <system_name> -o s --id <adapter_id>
    -a "sriov_adapter_mode=<mode>"`` on the HMC via SSH and returns the raw
    command output.

    The system UUID is resolved to its CLI name via REST before the command
    runs.

    ``adapter_id`` is the physical adapter identifier as reported by
    ``hmc_list_io_slots``.

    ``mode`` must be one of:
      - ``"sriov"``      — enable SR-IOV mode (shared virtual functions)
      - ``"dedicated"``  — disable SR-IOV, use as a dedicated (passthrough) adapter

    WARNING: Changing SR-IOV adapter mode affects all partitions using virtual
    functions on that adapter. Confirm system_uuid and adapter_id before calling.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    if mode not in _VALID_SRIOV_MODES:
        raise ValueError(
            f"Invalid mode {mode!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_SRIOV_MODES))}"
        )

    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
        payload = f"sriov_adapter_mode={mode}"
        cmd = (
            f"chhwres -r sriov -m {shlex.quote(system_name)} -o s --id {shlex.quote(adapter_id)}"
            f" -a {shlex.quote(payload)}"
        )
        return await run_hmc_cli(cmd)

    return _run(_go())




@mcp.tool(annotations=_READ_ONLY)
def hmc_list_vnics(system_uuid: str, lpar_uuid: str) -> list[dict[str, Any]]:
    """List vNICs (SR-IOV-backed Virtual NICs) on an LPAR via the HMC CLI.

    Runs ``lshwres -r virtualio --rsubtype vnic --level lpar -m <system_name>
    --filter lpar_names=<lpar_name>`` on the HMC via SSH and returns one dict
    per vNIC with fields such as ``vnic_id``, ``capacity``, ``vswitch_name``,
    ``port_vlan_id``, and ``backing_devices``.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs. Use ``hmc_list_systems`` to find system_uuid and
    ``hmc_list_lpars`` to find lpar_uuid.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid)
        return await list_vnics(HMCConfig(), system_name, lpar_name)

    return _run(_go())


@mcp.tool
def hmc_add_vnic(
    system_uuid: str,
    lpar_uuid: str,
    capacity: int,
    vswitch_name: str,
    port_vlan_id: int,
    backing_devices: str | None = None,
) -> str:
    """Add a vNIC (SR-IOV-backed Virtual NIC) to an LPAR via the HMC CLI.

    Runs ``chhwres -r virtualio --rsubtype vnic -o a -m <system_name>
    --filter lpar_names=<lpar_name> -a "<attrs>"`` on the HMC via SSH.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs.

    **V1 scope boundary:** Only the following parameters are supported in
    this version: ``capacity``, ``vswitch_name``, ``port_vlan_id``, and
    ``backing_devices`` (optional, opaque string passed verbatim).  Complex
    backing-device topology (multi-adapter failover, per-device SR-IOV
    physical port IDs, capacity weights) is a follow-up and explicitly out
    of scope for v1.

    Returns the raw HMC command output on success.

    WARNING: This modifies the LPAR configuration on the HMC. Confirm
    system_uuid, lpar_uuid, and vswitch_name before calling.  The
    underlying physical adapter must be in SR-IOV mode (see
    ``hmc_set_sriov_adapter_mode``).

    Auth: same env-var configuration as hmc_run_command (see module docstring).

    Raises:
        HMCCLIError: If the HMC command fails, e.g. because the underlying
            SR-IOV adapter is not in SR-IOV mode.
    """
    attrs = f"capacity={capacity},vswitch_name={vswitch_name},port_vlan_id={port_vlan_id}"
    if backing_devices is not None:
        attrs += f",backing_devices={backing_devices}"

    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid)
        cmd = (
            f"chhwres -r virtualio --rsubtype vnic -o a -m {shlex.quote(system_name)}"
            f" --filter lpar_names={shlex.quote(lpar_name)}"
            f" -a {shlex.quote(attrs)}"
        )
        try:
            return await run_hmc_cli(cmd)
        except HMCCLIError as exc:
            raise HMCCLIError(
                f"Failed to add vNIC to '{lpar_name}' on '{system_name}': {exc}. "
                f"Ensure the underlying SR-IOV adapter is in sriov mode "
                f"(see hmc_set_sriov_adapter_mode)."
            ) from exc

    return _run(_go())


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_remove_vnic(system_uuid: str, lpar_uuid: str, vnic_id: str) -> str:
    """Remove a vNIC from an LPAR via the HMC CLI.

    Runs ``chhwres -r virtualio --rsubtype vnic -o r -m <system_name>
    --filter lpar_names=<lpar_name> -a "vnic_id=<vnic_id>"`` on the HMC
    via SSH.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs.

    ``vnic_id`` is the numeric ID as reported by ``hmc_list_vnics``.

    WARNING: This modifies the LPAR configuration on the HMC. Confirm
    system_uuid, lpar_uuid, and vnic_id before calling. Returns the HMC CLI
    output (immediate delete — no job to poll).

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid)
        payload = f"vnic_id={vnic_id}"
        cmd = (
            f"chhwres -r virtualio --rsubtype vnic -o r -m {shlex.quote(system_name)}"
            f" --filter lpar_names={shlex.quote(lpar_name)}"
            f" -a {shlex.quote(payload)}"
        )
        return await run_hmc_cli(cmd)

    return _run(_go())
