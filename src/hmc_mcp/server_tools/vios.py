"""MCP tools for VIOS lifecycle, NIM install, and backup/restore."""

from __future__ import annotations

from ..tool_registry import tool_module

from typing import Any

from .._app import (
    run_sync,
)

from ..config import build_config
from ..client.client_factory import client_from_env
from ..operations.install import (
    install_lpar_os,
    install_vios,
    validate_install_request,
)
from ..documents import LparResources, VIOS_DEFAULT_RESOURCES
from ..operations.vios import (
    BackupType,
    RestoreBackupType,
    _create_vios,
    _delete_vios,
    backup_vios,
    list_vios_backups,
    power_vios,
    restore_vios,
)


tool, register_tools, tool_security = tool_module()


@tool(effect="mutate", operation="vios.create", target_kind="managed_system")
def hmc_create_vios(
    system_name_or_uuid: str,
    name: str,
    resources: LparResources = VIOS_DEFAULT_RESOURCES,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Create a new Virtual IO Server (VIOS) partition on a managed system.

    system_name_or_uuid is the target managed system name or UUID.
    Memory values are in MiB; procs are shared processing units (fractional
    ok). The VIOS is created powered off with default settings — install the
    OS with hmc_install_vios before using it as a storage/network server.
    This creates a real partition — confirm its name and system before calling.

    Args:
        system_name_or_uuid: Target managed-system name or UUID.
        name: Name for the new VIOS partition.
        resources: Memory and processor settings for the VIOS.
        profile: Optional TOML profile name; uses environment defaults when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await _create_vios(hmc, system_name_or_uuid, name, resources)

    return run_sync(_go)


@tool(effect="destructive", operation="vios.delete", target_kind="vios")
def hmc_delete_vios(
    vios_name_or_uuid: str,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
) -> str:
    """Delete (destroy) a VIOS partition by name or UUID.

    The VIOS must be powered off first (use hmc_power_off_vios and confirm
    with hmc_get_lpar_state). This tool refuses to
    delete a VIOS whose current state is anything other than 'not activated',
    matching the precondition check pattern used by hmc_remove_memory_pool.
    This permanently removes the VIOS and its profiles from the HMC — it is
    irreversible. Confirm the target with hmc_list_vios before calling. Returns a
    confirmation string (immediate delete — no job to poll).

    system_name_or_uuid disambiguates duplicate VIOS names; it is ignored when
    vios_name_or_uuid is already a UUID.

    Raises:
        HMCError: If the VIOS state is not 'not activated' (HTTP 409).

    Args:
        vios_name_or_uuid: VIOS partition name or UUID.
        profile: Optional TOML profile name; uses environment defaults when omitted.
        system_name_or_uuid: Optional managed system used to disambiguate a VIOS name.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await _delete_vios(hmc, vios_name_or_uuid, system_name_or_uuid)

    return run_sync(_go)


@tool(effect="destructive", operation="vios.install", target_kind="vios")
def hmc_install_vios(
    vios_name_or_uuid: str,
    system_name_or_uuid: str,
    install_source: str,
    vios_ip: str,
    nim_subnetmask: str,
    nim_gateway: str,
    profile_name: str = "default",
    vlan_id: str = "0",
    mac_address: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Install a VIOS onto an existing partition via the HMC ``installios`` CLI.

    This tool drives the HMC command line over SSH, not the REST API: the
    ``InstallVIOS`` REST job this operation once targeted does not exist on any
    surveyed HMC (ADR 0069), and ``installios`` has no REST equivalent (the
    grammar is recorded in ADR 0070).

    Semantics are submit-and-detach. The install is a full NIM network
    installation that typically runs far longer than one SSH session; the tool
    launches ``installios`` in the background on the HMC (``nohup``, stdin
    closed) and returns as soon as the process is submitted, reporting the
    remote PID and the log path (``/tmp/hmc-mcp-installios-<partition>.log``).
    It cannot report the install's progress or outcome. There is no HMC job on
    this path — hmc_get_job / hmc_wait_for_job do not apply. Monitor the
    install through the partition's console (mkvterm) or the log file, then
    confirm with the partition state tools. If an install fails mid-flight,
    clean up the NIM resources with ``installios -u`` in an SSH session before
    retrying.

    Requires hmcsuperadmin-level HMC authority (e.g. hscroot). The target must
    be a powered-off VIOS partition that already exists with a profile.

    Args:
        vios_name_or_uuid: Powered-off VIOS partition name or UUID.
        system_name_or_uuid: Managed-system name or UUID hosting the VIOS;
            ``installios -s`` needs it explicitly.
        install_source: Where the install image comes from (``installios
            -d``): a device path such as ``/dev/cdrom`` or an ``lsmediadev``
            USB device, an absolute path on the HMC to a ``backupios``
            nim_resources tarball or VIOS ISO, or ``server:/path`` for an
            NFS-served backup. Replaces the retired ``nim_ip`` parameter:
            under CLI semantics the HMC itself serves the image, so there is
            no external NIM-server address.
        vios_ip: IPv4 address assigned to the VIOS during installation
            (``-i``); unchanged from the REST-era parameter.
        nim_subnetmask: IPv4 subnet mask for the VIOS's install-time network
            interface (``-S``); now configures the client side, not a remote
            NIM server.
        nim_gateway: IPv4 gateway used during installation (``-g``); same
            client-side semantics as ``nim_subnetmask``.
        profile_name: Partition profile holding the install resources
            (``-r``); defaults to ``default``.
        vlan_id: Install-network VLAN tag identifier (``-V``); ``"0"`` for
            untagged traffic.
        mac_address: Optional client MAC address (``-m``). When omitted,
            ``installios`` discovers it, which can time out on some networks.
        profile: Optional TOML profile name; uses environment defaults when
            omitted.
    """
    # install_vios validates too, but only once its client exists. Calling the
    # same list here rejects a malformed argument before an HMC session opens.
    validate_install_request(
        install_source=install_source,
        client_ip=vios_ip,
        subnet_mask=nim_subnetmask,
        gateway=nim_gateway,
        profile_name=profile_name,
        vlan_id=vlan_id,
        mac_address=mac_address,
    )

    async def _go():
        async with client_from_env(profile) as hmc:
            return await install_vios(
                hmc,
                system_name_or_uuid,
                vios_name_or_uuid,
                install_source=install_source,
                client_ip=vios_ip,
                subnet_mask=nim_subnetmask,
                gateway=nim_gateway,
                profile_name=profile_name,
                vlan_id=vlan_id,
                mac_address=mac_address,
            )

    # `install_*` returns an `InstallHandle`, and a `TypedDict` is not assignable
    # to `dict[str, Any]`. Widen here rather than narrowing this tool's return
    # annotation, which would move the derived MCP output schema.
    return dict(run_sync(_go))


@tool(effect="destructive", operation="lpar.install_os", target_kind="lpar")
def hmc_install_lpar_os(
    lpar_name_or_uuid: str,
    system_name_or_uuid: str,
    install_source: str,
    lpar_ip: str,
    nim_subnetmask: str,
    nim_gateway: str,
    profile_name: str = "default",
    vlan_id: str = "0",
    mac_address: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Install an OS image onto a partition via the HMC ``installios`` CLI.

    This tool drives the HMC command line over SSH, not the REST API: the
    ``InstallLPAR`` REST job this operation once targeted does not exist on any
    surveyed HMC (ADR 0069), and ``installios`` has no REST equivalent (the
    grammar is recorded in ADR 0070).

    Note the engine's scope: the IBM man page defines ``installios`` as the
    Virtual I/O Server installer and requires the ``-p`` partition to be of
    type Virtual I/O Server. This bridge therefore installs VIOS images; a
    general AIX/Linux NIM install stays on the NIM master and is out of scope
    here (ADR 0069 records why the HMC alone cannot drive it).

    Semantics are submit-and-detach. The install is a full NIM network
    installation that typically runs far longer than one SSH session; the tool
    launches ``installios`` in the background on the HMC (``nohup``, stdin
    closed) and returns as soon as the process is submitted, reporting the
    remote PID and the log path (``/tmp/hmc-mcp-installios-<partition>.log``).
    It cannot report the install's progress or outcome. There is no HMC job on
    this path — hmc_get_job / hmc_wait_for_job do not apply. Monitor the
    install through the partition's console (mkvterm) or the log file, then
    confirm with the partition state tools. If an install fails mid-flight,
    clean up the NIM resources with ``installios -u`` in an SSH session before
    retrying.

    Requires hmcsuperadmin-level HMC authority (e.g. hscroot). The target must
    be a powered-off partition that already exists with a profile.

    Args:
        lpar_name_or_uuid: Powered-off partition name or UUID.
        system_name_or_uuid: Managed-system name or UUID hosting the
            partition; ``installios -s`` needs it explicitly.
        install_source: Where the install image comes from (``installios
            -d``): a device path such as ``/dev/cdrom`` or an ``lsmediadev``
            USB device, an absolute path on the HMC to a ``backupios``
            nim_resources tarball or VIOS ISO, or ``server:/path`` for an
            NFS-served backup. Replaces the retired ``nim_ip`` parameter:
            under CLI semantics the HMC itself serves the image, so there is
            no external NIM-server address.
        lpar_ip: IPv4 address assigned to the partition during installation
            (``-i``); unchanged from the REST-era parameter.
        nim_subnetmask: IPv4 subnet mask for the partition's install-time
            network interface (``-S``); now configures the client side, not a
            remote NIM server.
        nim_gateway: IPv4 gateway used during installation (``-g``); same
            client-side semantics as ``nim_subnetmask``.
        profile_name: Partition profile holding the install resources
            (``-r``); defaults to ``default``.
        vlan_id: Install-network VLAN tag identifier (``-V``); ``"0"`` for
            untagged traffic.
        mac_address: Optional client MAC address (``-m``). When omitted,
            ``installios`` discovers it, which can time out on some networks.
        profile: Optional TOML profile name; uses environment defaults when
            omitted.
    """
    # install_lpar_os validates too, but only once its client exists. Calling
    # the same list here rejects a malformed argument before a session opens.
    validate_install_request(
        install_source=install_source,
        client_ip=lpar_ip,
        subnet_mask=nim_subnetmask,
        gateway=nim_gateway,
        profile_name=profile_name,
        vlan_id=vlan_id,
        mac_address=mac_address,
    )

    async def _go():
        async with client_from_env(profile) as hmc:
            return await install_lpar_os(
                hmc,
                system_name_or_uuid,
                lpar_name_or_uuid,
                install_source=install_source,
                client_ip=lpar_ip,
                subnet_mask=nim_subnetmask,
                gateway=nim_gateway,
                profile_name=profile_name,
                vlan_id=vlan_id,
                mac_address=mac_address,
            )

    # `install_*` returns an `InstallHandle`, and a `TypedDict` is not assignable
    # to `dict[str, Any]`. Widen here rather than narrowing this tool's return
    # annotation, which would move the derived MCP output schema.
    return dict(run_sync(_go))


@tool(effect="read", operation="vios.list_backups", target_kind="vios")
def hmc_list_vios_backups(
    vios_name_or_uuid: str, profile: str | None = None
) -> list[dict[str, str]]:
    """List a VIOS backup catalog with the supported ``lsviosbk`` command.

    Resolves the required VIOS selector to a UUID, requests the explicit
    ``name,type`` CSV projection, and validates its header and every row.
    Requires HMC V10 or newer; no older-HMC fallback is provided.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID.
        profile: Optional TOML profile name; uses environment defaults when omitted.

    Raises:
        ValueError: If the ``lsviosbk`` CSV is malformed.
    """
    return run_sync(
        lambda: list_vios_backups(build_config(profile=profile), vios_name_or_uuid)
    )


@tool(
    effect="mutate",
    operation="vios.backup",
    target_kind="vios",
    # An SSP backup can cover the cluster and associated nodes beyond this VIOS.
    exhaustive_targets=False,
)
def hmc_backup_vios(
    system_name_or_uuid: str,
    vios_name_or_uuid: str,
    *,
    backup_name: str,
    backup_type: BackupType = "vios",
    profile: str | None = None,
) -> str:
    """Create a named VIOS backup with the supported ``mkviosbk`` command.

    Both managed-system and VIOS selectors are required metadata, but an SSP
    backup can affect its wider cluster, so access policy requires
    ``targets = "all-targets"``. ``backup_type`` is limited to ``vios``,
    ``viosioconfig``, or ``ssp`` and defaults to ``vios``. The backup name must
    identify one catalog entry, not a path or option. Returns the raw HMC CLI
    output. Requires HMC V10 or newer; no older-HMC fallback is provided.

    Args:
        system_name_or_uuid: Managed system name or UUID. UUIDs resolve to MTMS.
        vios_name_or_uuid: VIOS partition name or UUID.
        backup_name: Name for the new backup catalog entry.
        backup_type: Backup kind: vios, viosioconfig, or ssp.
        profile: Optional TOML profile name; uses environment defaults when omitted.

    Raises:
        ValueError: If the backup type or catalog name is invalid, or a selector
            cannot be resolved to the required CLI identity.
    """
    return run_sync(
        lambda: backup_vios(
            build_config(profile=profile),
            system_name_or_uuid,
            vios_name_or_uuid,
            backup_name=backup_name,
            backup_type=backup_type,
        )
    )


@tool(
    effect="destructive",
    operation="vios.restore",
    target_kind="vios",
    # An SSP restore can affect the cluster beyond the selected VIOS (#282).
    exhaustive_targets=False,
)
def hmc_restore_vios(
    system_name_or_uuid: str,
    vios_name_or_uuid: str,
    backup_name: str,
    *,
    backup_type: RestoreBackupType,
    restart_if_required: bool = False,
    profile: str | None = None,
) -> str:
    """Restore a catalog entry with the supported ``rstviosbk`` command.

    Both managed-system and VIOS selectors are required. ``backup_type`` must
    be ``viosioconfig`` or ``ssp``; full-image ``vios`` restore is unsupported.
    When ``restart_if_required`` is true, ``-r`` authorizes a VIOS restart only
    after a failed restore attempt. The catalog name must not be a path, option,
    empty, or padded value. Returns the raw HMC CLI output.
    Requires HMC V10 or newer; no older-HMC fallback is provided.

    Args:
        system_name_or_uuid: Managed system name or UUID. UUIDs resolve to MTMS.
        vios_name_or_uuid: VIOS partition name or UUID.
        backup_name: Backup catalog name returned by hmc_list_vios_backups.
        backup_type: Restore kind: viosioconfig or ssp.
        restart_if_required: Append ``-r`` to authorize a conditional restart.
        profile: Optional TOML profile name; uses environment defaults when omitted.

    Raises:
        ValueError: If the restore type or catalog name is invalid, or a selector
            cannot be resolved to the required CLI identity.
    """
    return run_sync(
        lambda: restore_vios(
            build_config(profile=profile),
            system_name_or_uuid,
            vios_name_or_uuid,
            backup_name,
            backup_type=backup_type,
            restart_if_required=restart_if_required,
        )
    )


@tool(effect="mutate", operation="vios.power_on", target_kind="vios")
def hmc_power_on_vios(
    vios_name_or_uuid: str,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Power on a VIOS, optionally waiting for a normalized job outcome.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID.
        wait: Wait for the power job's terminal outcome when true.
        timeout_seconds: Maximum client-side wait in seconds.
        poll_interval: Seconds between job-status requests while waiting.
        profile: Optional TOML profile name; uses environment defaults when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await power_vios(
                hmc,
                vios_name_or_uuid,
                on=True,
                wait=wait,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )

    return run_sync(_go)


@tool(effect="destructive", operation="vios.power_off", target_kind="vios")
def hmc_power_off_vios(
    vios_name_or_uuid: str,
    immediate: bool = False,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
) -> dict[str, Any] | None:
    """Power off a VIOS, optionally scoped by system and waiting for completion.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID.
        immediate: Request immediate shutdown instead of an orderly shutdown.
        wait: Wait for the power job's terminal outcome when true.
        timeout_seconds: Maximum client-side wait in seconds.
        poll_interval: Seconds between job-status requests while waiting.
        profile: Optional TOML profile name; uses environment defaults when omitted.
        system_name_or_uuid: Optional managed system used to disambiguate a VIOS name.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await power_vios(
                hmc,
                vios_name_or_uuid,
                on=False,
                system_name_or_uuid=system_name_or_uuid,
                immediate=immediate,
                wait=wait,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )

    return run_sync(_go)
