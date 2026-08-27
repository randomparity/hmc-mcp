"""Presentation-neutral OS-install operations over the HMC ``installios`` CLI."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

# Not `typing.TypedDict`: pydantic refuses one on Python < 3.12, which is inside
# this package's supported range, and `InstallHandle` is a facade export a
# consumer may put in a `TypeAdapter` or a response model. Same reason `jobs.py`
# imports it here.
from typing_extensions import TypedDict

from .. import audit
from ..client import HMCClient
from ..resource_identity import is_uuid, resolve_lpar_uuid, resolve_system_name, resolve_system_uuid, resolve_vios_uuid
from ..ssh.lpar import _ssh_lpar_name
from ..ssh.install import (
    build_installios_command,
    run_installios,
    validate_hmc_name,
    validate_install_source,
    validate_ipv4_address,
    validate_ipv4_subnet_mask,
    validate_mac_address,
    validate_vlan_id,
)

_logger = logging.getLogger(__name__)

_TargetResolver = Callable[..., Awaitable[str]]


class InstallHandle(TypedDict):
    """What a detached ``installios`` submission leaves the caller to work with.

    Every key is composed by this package and none is read back from the HMC, so
    no firmware level can vary the shape — this is a package-owned contract, not
    one of ADR 0029's opaque HMC resource payloads. Naming it here is what puts
    the five keys inside the frozen signature digest.

    There is no HMC job on this path (ADR 0069/0070), so ``pid`` and
    ``log_path`` are the only handles on an install in flight.
    """

    system: str
    """Resolved managed-system name the install was submitted against."""

    partition: str
    """Resolved partition name ``installios -p`` received."""

    pid: int
    """PID of the detached ``installios`` process on the HMC."""

    log_path: str
    """HMC-side path the install writes to. Keyed on the partition name alone,
    so it is not unique per managed system — see :func:`install_lpar_os`."""

    message: str
    """Operator-facing restatement of ``pid`` and ``log_path`` with the cleanup
    command a failed install needs."""


def validate_install_request(
    *,
    install_source: str,
    client_ip: str,
    subnet_mask: str,
    gateway: str,
    profile_name: str,
    vlan_id: str,
    mac_address: str | None,
) -> None:
    """Reject an install request that cannot become an ``installios`` command.

    One list, two call sites. :func:`_submit_install` calls it so a facade
    caller — who reaches no tool body — is covered; the MCP tools call it
    *before* opening a client, which the operation cannot do because its client
    is already an argument. ``build_installios_command`` keeps its own
    independent copy as the injection boundary, trusting neither.

    Synchronous, so ADR 0029's selection rule leaves it outside the facade.
    """
    validate_install_source(install_source)
    validate_ipv4_address(client_ip)
    validate_ipv4_subnet_mask(subnet_mask)
    validate_ipv4_address(gateway)
    validate_vlan_id(vlan_id)
    validate_hmc_name(profile_name, "profile_name")
    if mac_address is not None:
        validate_mac_address(mac_address)


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
) -> InstallHandle:
    """Resolve one install target's CLI names and detach ``installios`` on it."""
    validate_install_request(
        install_source=install_source,
        client_ip=client_ip,
        subnet_mask=subnet_mask,
        gateway=gateway,
        profile_name=profile_name,
        vlan_id=vlan_id,
        mac_address=mac_address,
    )

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
    # The record precedes the irreversible submit, because a submit that raises
    # cannot say whether anything was submitted. It goes on the reserved audit
    # logger rather than this module's, which nothing configures (ADR 0102).
    audit.record_install_attempted(
        system=system_name,
        partition=partition_name,
        log_path=log_path,
        host=hmc.config.host,
        agent_id=hmc.config.agent_id or "hmc-mcp",
    )
    pid = await run_installios(hmc.config, command)
    # Stays on the module logger: the PID it adds is already in the returned
    # handle, so this is a convenience for an embedder that configures the
    # hmc_mcp namespace, not part of the audit trail (ADR 0102).
    _logger.info(
        "Detached installios on %s/%s: pid %s, log %s",
        system_name,
        partition_name,
        pid,
        log_path,
    )
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
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    *,
    install_source: str,
    client_ip: str,
    subnet_mask: str,
    gateway: str,
    profile_name: str = "default",
    vlan_id: str = "0",
    mac_address: str | None = None,
) -> InstallHandle:
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
    nothing to poll: the returned mapping is an :class:`InstallHandle`, carrying
    the resolved ``system`` and ``partition`` names, the remote ``pid``, the
    ``log_path`` the install writes to, and a ``message`` restating both. Track
    progress through that log or the partition console, then confirm the
    outcome with the partition state operations. Clean up a failed install with
    ``installios -u`` on the HMC before retrying.

    Requires hmcsuperadmin-level HMC authority (e.g. hscroot) and a powered-off
    target partition that already exists with a profile.

    Ownership authorization is classified in ADR 0092 §3.4a, which is the
    authoritative record; that row, not this docstring, carries the reasoning.

    **Neither stated precondition is checked here**, and because submission is
    detached the operation cannot observe whether ``installios`` accepted the
    target: a returned handle means the process was backgrounded, nothing more.

    - *Partition type.* ``lpar_name_or_uuid`` resolves through the
      ``LogicalPartition`` feed and a UUID selector is passed through with no
      lookup at all, so an ordinary partition resolves successfully.
      ``installios`` refuses a non-Virtual-I/O-Server ``-p`` on the HMC, and
      that refusal reaches only the install log.
    - *Power state.* Nothing reads ``PartitionState``. What ``installios`` does
      against an activated partition is not recorded anywhere in this
      repository's sources, so an install submitted against a running partition
      has no locally known outcome.

    Both are tracked by #460, which adds one preflight read covering them.

    Submission is not idempotent, and the log path collides more widely than
    the partition. Nothing detects an install already running against the
    target, so a second call submits a second detached process and both write
    the same disk. Separately, the log path is keyed on the **partition name
    alone** — the managed system is not part of it — and the redirect
    truncates, so two same-named partitions on two different managed systems
    behind one HMC share one log file and each destroys the other's only
    diagnostic record. The returned ``log_path`` is therefore not unique per
    system. Serializing per partition name *across every managed system on the
    HMC* is the caller's responsibility.

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
        HMCCLIError: Either from mapping a UUID target to its CLI name over
            SSH — no matching ``lssyscfg`` row, or a transport failure on that
            read — or from the submission itself failing or reporting no PID.
            The exception type alone does not say which, so it does not tell a
            caller whether an ``installios`` was started: a resolution failure
            submits nothing and needs no ``installios -u`` cleanup, while a
            failed submission may. When that distinction matters, resolve the
            target to a name first and pass the name.
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
    system_name_or_uuid: str,
    vios_name_or_uuid: str,
    *,
    install_source: str,
    client_ip: str,
    subnet_mask: str,
    gateway: str,
    profile_name: str = "default",
    vlan_id: str = "0",
    mac_address: str | None = None,
) -> InstallHandle:
    """Detach an ``installios`` VIOS install onto an existing VIOS partition.

    Identical mechanism, contract, and return value to
    :func:`install_lpar_os` — see it for the submit-and-detach semantics, the
    detach handle's fields, the ADR 0092 §3.4a ownership classification, and the
    ``installios`` argument grammar. This operation differs only in resolving
    its target through the ``VirtualIOServer`` feed rather than the
    ``LogicalPartition`` one, so a *name* selector cannot name a logical
    partition; a UUID selector is still passed through without a lookup, so the
    unchecked-precondition caveats and #460 apply to it unchanged — including
    the power-state one, which no selector shape covers. Submission is not
    idempotent here either, and the same partition-name-only log-path collision
    applies across every managed system on the HMC.

    Args:
        hmc: Connected client; its configuration also carries the SSH
            credentials the CLI bridge submits with.
        system_name_or_uuid: Managed-system name or UUID hosting the VIOS;
            ``installios -s`` needs it explicitly.
        vios_name_or_uuid: Powered-off VIOS partition name or UUID.
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
        HMCCLIError: Same two sources as :func:`install_lpar_os` — UUID-to-CLI
            name resolution over SSH, or the submission — and the same
            inability to tell them apart from the exception type.
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
