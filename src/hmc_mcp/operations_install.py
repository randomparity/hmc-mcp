"""Presentation-neutral OS-install operations over the HMC ``installios`` CLI."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .client import HMCClient
from .common import (
    is_uuid,
    resolve_lpar_uuid,
    resolve_system_name,
    resolve_system_uuid,
    resolve_vios_uuid,
)
from .ssh_commands import (
    _ssh_lpar_name,
    build_installios_command,
    run_installios,
    validate_install_source,
    validate_ipv4_address,
    validate_ipv4_subnet_mask,
    validate_mac_address,
    validate_vlan_id,
)

_TargetResolver = Callable[..., Awaitable[str]]


async def _submit_install(
    hmc: HMCClient,
    target_name_or_uuid: str,
    system_name_or_uuid: str,
    resolve_target_uuid: _TargetResolver,
    *,
    install_source: str,
    client_ip: str,
    subnet_mask: str,
    gateway: str,
    profile_name: str,
    vlan_id: str,
    mac_address: str | None,
) -> dict[str, Any]:
    """Resolve one install target's CLI names and detach ``installios`` on it."""
    validate_install_source(install_source)
    validate_ipv4_address(client_ip)
    validate_ipv4_subnet_mask(subnet_mask)
    validate_ipv4_address(gateway)
    validate_vlan_id(vlan_id)
    if mac_address is not None:
        validate_mac_address(mac_address)

    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    target_uuid = await resolve_target_uuid(
        hmc, target_name_or_uuid, system_name_or_uuid=system_uuid
    )
    system_name = (
        system_name_or_uuid
        if not is_uuid(system_name_or_uuid)
        else await resolve_system_name(hmc, system_uuid)
    )
    partition_name = (
        target_name_or_uuid
        if not is_uuid(target_name_or_uuid)
        else await _ssh_lpar_name(hmc.config, target_uuid, system_name)
    )

    command, log_path = build_installios_command(
        install_source=install_source,
        client_ip=client_ip,
        subnet_mask=subnet_mask,
        gateway=gateway,
        system_name=system_name,
        partition_name=partition_name,
        profile_name=profile_name,
        vlan_id=vlan_id,
        mac_address=mac_address,
    )
    pid = await run_installios(hmc.config, command)
    return {
        "system": system_name,
        "partition": partition_name,
        "pid": pid,
        "log_path": log_path,
        "message": (
            "installios submitted and detached; no HMC job exists on this "
            f"path. Monitor PID {pid} via {log_path} or the partition "
            "console; run 'installios -u' on the HMC to clean up a failed "
            "install."
        ),
    }


async def install_lpar_os(
    hmc: HMCClient,
    lpar_name_or_uuid: str,
    system_name_or_uuid: str,
    *,
    install_source: str,
    client_ip: str,
    subnet_mask: str,
    gateway: str,
    profile_name: str = "default",
    vlan_id: str = "0",
    mac_address: str | None = None,
) -> dict[str, Any]:
    """Detach an ``installios`` OS install onto an existing partition.

    Drives the HMC command line over SSH, not the REST API: the ``InstallLPAR``
    REST job does not exist on any surveyed HMC (ADR 0069), and ``installios``
    has no REST equivalent (ADR 0070). The IBM man page scopes ``installios``
    to Virtual I/O Server images, so a general AIX or Linux NIM install stays
    on the NIM master and is out of scope here.

    Semantics are submit-and-detach. The install is a full network
    installation that outlives one SSH session, so the operation launches
    ``installios`` under ``nohup`` with stdin closed and returns as soon as the
    HMC reports the backgrounded PID. There is no HMC job on this path and
    nothing to poll: the returned mapping is the detach handle, carrying the
    resolved ``system`` and ``partition`` names, the remote ``pid``, the
    ``log_path`` the install writes to, and a ``message`` restating both. Track
    progress through that log or the partition console, then confirm the
    outcome with the partition state operations. Clean up a failed install with
    ``installios -u`` on the HMC before retrying.

    Requires hmcsuperadmin-level HMC authority (e.g. hscroot) and a powered-off
    target partition that already exists with a profile.

    Args:
        hmc: Connected client; its configuration also carries the SSH
            credentials the CLI bridge submits with.
        lpar_name_or_uuid: Powered-off partition name or UUID.
        system_name_or_uuid: Managed-system name or UUID hosting the
            partition; ``installios -s`` needs it explicitly.
        install_source: Where the install image comes from (``installios
            -d``): a device path such as ``/dev/cdrom`` or an ``lsmediadev``
            USB device, an absolute path on the HMC to a ``backupios``
            nim_resources tarball or VIOS ISO, or ``server:/path`` for an
            NFS-served backup. Under CLI semantics the HMC itself serves the
            image, so there is no external NIM-server address to prepare.
        client_ip: IPv4 address assigned to the partition during installation
            (``-i``).
        subnet_mask: IPv4 subnet mask for the partition's install-time network
            interface (``-S``).
        gateway: IPv4 gateway used during installation (``-g``).
        profile_name: Partition profile holding the install resources (``-r``).
        vlan_id: Install-network VLAN tag identifier (``-V``); ``"0"`` for
            untagged traffic.
        mac_address: Optional client MAC address (``-m``). When omitted,
            ``installios`` discovers it, which can time out on some networks.

    Raises:
        ValueError: If an argument cannot be part of an ``installios``
            invocation, or if a name resolves to no partition or system. Both
            are raised before anything is submitted.
        HMCCLIError: If the SSH submission fails or reports no PID.
    """
    return await _submit_install(
        hmc,
        lpar_name_or_uuid,
        system_name_or_uuid,
        resolve_lpar_uuid,
        install_source=install_source,
        client_ip=client_ip,
        subnet_mask=subnet_mask,
        gateway=gateway,
        profile_name=profile_name,
        vlan_id=vlan_id,
        mac_address=mac_address,
    )


async def install_vios(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    system_name_or_uuid: str,
    *,
    install_source: str,
    client_ip: str,
    subnet_mask: str,
    gateway: str,
    profile_name: str = "default",
    vlan_id: str = "0",
    mac_address: str | None = None,
) -> dict[str, Any]:
    """Detach an ``installios`` VIOS install onto an existing VIOS partition.

    Identical mechanism, contract, and return value to
    :func:`install_lpar_os` — see it for the submit-and-detach semantics, the
    detach handle's fields, and the ``installios`` argument grammar. This
    operation differs only in resolving its target as a Virtual I/O Server
    partition rather than a logical partition.

    Args:
        hmc: Connected client; its configuration also carries the SSH
            credentials the CLI bridge submits with.
        vios_name_or_uuid: Powered-off VIOS partition name or UUID.
        system_name_or_uuid: Managed-system name or UUID hosting the VIOS;
            ``installios -s`` needs it explicitly.
        install_source: Install-image source for ``installios -d``.
        client_ip: IPv4 address assigned to the VIOS during installation
            (``-i``).
        subnet_mask: IPv4 subnet mask for the VIOS's install-time network
            interface (``-S``).
        gateway: IPv4 gateway used during installation (``-g``).
        profile_name: Partition profile holding the install resources (``-r``).
        vlan_id: Install-network VLAN tag identifier (``-V``); ``"0"`` for
            untagged traffic.
        mac_address: Optional client MAC address (``-m``).

    Raises:
        ValueError: If an argument cannot be part of an ``installios``
            invocation, or if a name resolves to no VIOS or system. Both are
            raised before anything is submitted.
        HMCCLIError: If the SSH submission fails or reports no PID.
    """
    return await _submit_install(
        hmc,
        vios_name_or_uuid,
        system_name_or_uuid,
        resolve_vios_uuid,
        install_source=install_source,
        client_ip=client_ip,
        subnet_mask=subnet_mask,
        gateway=gateway,
        profile_name=profile_name,
        vlan_id=vlan_id,
        mac_address=mac_address,
    )
