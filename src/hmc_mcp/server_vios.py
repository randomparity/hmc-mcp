"""MCP tools for VIOS lifecycle, NIM install, and backup/restore.
"""

from __future__ import annotations

import re
import shlex
from typing import Any, Literal

from ._app import (
    _DESTRUCTIVE,
    _READ_ONLY,
    _run,
    mcp,
)

from .client import HMCError
from .common import client_from_env
from .jobs import install_lpar_job, install_vios_job
from .ssh import run_hmc_cli
from .documents import build_vios_document



@mcp.tool
def hmc_create_vios(
    system_uuid: str,
    name: str,
    min_memory: int = 512,
    desired_memory: int = 4096,
    max_memory: int = 8192,
    desired_vcpus: int = 2,
    min_vcpus: int = 1,
    max_vcpus: int = 4,
    desired_procs: float = 0.5,
    min_procs: float = 0.1,
    max_procs: float = 1.0,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Create a new Virtual IO Server (VIOS) partition on a managed system.

    system_uuid is the target managed system (find it with hmc_systems).
    Memory values are in MiB; procs are shared processing units (fractional
    ok). The VIOS is created powered off with default settings — install the
    OS with hmc_install_vios before using it as a storage/network server.
    This creates a real partition — confirm name/system_uuid before calling.
    """
    xml = build_vios_document(
        name=name,
        min_memory=min_memory,
        desired_memory=desired_memory,
        max_memory=max_memory,
        desired_vcpus=desired_vcpus,
        min_vcpus=min_vcpus,
        max_vcpus=max_vcpus,
        desired_procs=desired_procs,
        min_procs=min_procs,
        max_procs=max_procs,
    )

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.create_logical_partition(system_uuid, xml)
    return _run(_go)


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_delete_vios(vios_uuid: str, profile: str | None = None) -> str:
    """Delete (destroy) a VIOS partition by UUID.

    The VIOS must be powered off first (use hmc_power_off_vios and confirm
    with hmc_lpars(vios_uuid=..., state_only=True)). This tool refuses to
    delete a VIOS whose current state is anything other than 'not activated',
    matching the precondition check pattern used by hmc_remove_memory_pool.
    This permanently removes the VIOS and its profiles from the HMC — it is
    irreversible. Confirm the UUID with hmc_vios before calling. Returns a
    confirmation string (immediate delete — no job to poll).

    Raises:
        HMCError: If the VIOS state is not 'not activated' (HTTP 409).
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            state = await hmc.get_quick_property(
                "LogicalPartition", vios_uuid, "PartitionState"
            )
            if state != "not activated":
                raise HMCError(
                    f"Cannot delete VIOS {vios_uuid} — current state is "
                    f"{state!r}; it must be 'not activated' to delete. Power it "
                    "off (hmc_power_off_vios) and confirm with "
                    "hmc_lpars(lpar_uuid=..., state_only=True) before retrying.",
                    status_code=409,
                )
            await hmc.delete_logical_partition(vios_uuid)
            return f"Deleted VIOS {vios_uuid}"

    return _run(_go)


@mcp.tool
def hmc_install_vios(
    vios_uuid: str,
    nim_ip: str,
    nim_gateway: str,
    nim_subnetmask: str,
    vios_ip: str,
    vlan_id: str = "0",
    timeout: int = 60,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Submit a NIM-based VIOS installation job.

    vios_uuid is the UUID of an existing (powered-off) VIOS partition. The
    VIOS will PXE-boot from the NIM server at nim_ip to install its OS.
    nim_gateway and nim_subnetmask define the network for the NIM install
    boot; vios_ip is the IP address the VIOS uses during the NIM install;
    vlan_id is the VLAN tag for the install network (use "0" for untagged).
    timeout is the job timeout in minutes (default 60). Returns the submitted
    job — poll hmc_get_job for status.

    Set wait=True to block until the job reaches a terminal state.
    """
    job_xml = install_vios_job(nim_ip, nim_gateway, nim_subnetmask, vios_ip, vlan_id, timeout)

    async def _go():
        async with client_from_env(profile) as hmc:
            job = await hmc.submit_job(
                f"/rest/api/uom/VirtualIOServer/{vios_uuid}/do/InstallVIOS",
                job_xml,
            )
            if not wait or job is None:
                return job
            job_uuid = job.get("UUID") or (job.get("Resource") or {}).get("JobID")
            if not job_uuid:
                return job
            return await hmc.wait_for_job(
                job_uuid, timeout_seconds, poll_interval, job_href=job.get("link")
            )

    return _run(_go)


@mcp.tool
def hmc_install_lpar_os(
    lpar_uuid: str,
    nim_ip: str,
    nim_gateway: str,
    nim_subnetmask: str,
    lpar_ip: str,
    vlan_id: str = "0",
    timeout: int = 60,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Submit a NIM-based LPAR OS installation job.

    lpar_uuid is the UUID of an existing (powered-off) LPAR. The LPAR will
    PXE-boot from the NIM server at nim_ip to install its OS.
    nim_gateway and nim_subnetmask define the network for the NIM install
    boot; lpar_ip is the IP address the LPAR uses during the NIM install;
    vlan_id is the VLAN tag for the install network (use "0" for untagged).
    timeout is the job timeout in minutes (default 60). Returns the submitted
    job — poll hmc_get_job for status.

    Set wait=True to block until the job reaches a terminal state.
    """
    job_xml = install_lpar_job(nim_ip, nim_gateway, nim_subnetmask, lpar_ip, vlan_id, timeout)

    async def _go():
        async with client_from_env(profile) as hmc:
            job = await hmc.submit_job(
                f"/rest/api/uom/LogicalPartition/{lpar_uuid}/do/InstallLPAR",
                job_xml,
            )
            if not wait or job is None:
                return job
            job_uuid = job.get("UUID") or (job.get("Resource") or {}).get("JobID")
            if not job_uuid:
                return job
            return await hmc.wait_for_job(
                job_uuid, timeout_seconds, poll_interval, job_href=job.get("link")
            )

    return _run(_go)



BackupType = Literal["vios", "viosioconfig", "ssp"]
_VALID_BACKUP_TYPES: frozenset[BackupType] = frozenset({"vios", "viosioconfig", "ssp"})


def _parse_lsviosbackup_output(text: str) -> list[dict[str, str]]:
    """Parse ``lsviosbackup`` fixed-width table output into a list of dicts.

    The first non-empty line is the header; each subsequent non-empty line is
    a backup row. Columns are separated by two or more spaces. Rows with fewer
    values than headers are padded with empty strings so callers can tell a
    missing field from an empty one.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    headers = [h for h in re.split(r"\s{2,}", lines[0]) if h]
    results = []
    for line in lines[1:]:
        values = [v for v in re.split(r"\s{2,}", line) if v]
        row = dict(zip(headers, values))
        for header in headers[len(values):]:
            row[header] = ""
        results.append(row)
    return results


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_vios_backups(vios_uuid: str) -> list[dict[str, str]]:
    """List existing VIOS backups for a given VIOS UUID.

    Runs ``lsviosbackup -id <vios_uuid>`` on the HMC via SSH and parses the
    fixed-width table into a list of dicts keyed by the output header
    (BackupName, Date, Type). Find vios_uuid with hmc_vios.    """
    output = _run(lambda: run_hmc_cli(f"lsviosbackup -id {shlex.quote(vios_uuid)}"))
    return _parse_lsviosbackup_output(output)


@mcp.tool
def hmc_backup_vios(
    vios_uuid: str, backup_type: BackupType = "vios"
) -> str:
    """Create a VIOS backup via the HMC CLI.

    Runs ``chviosbackup -id <vios_uuid> -operation backup -type <backup_type>``
    on the HMC via SSH. vios_uuid is the VIOS UUID (from hmc_vios).

    backup_type must be one of:
      - ``vios``       — full VIOS configuration backup (default)
      - ``viosioconfig`` — I/O configuration backup
      - ``ssp``        — Shared Storage Pool (cluster) backup

    Returns the raw HMC CLI output. Poll hmc_list_vios_backups to confirm
    the backup was created.
    """
    if backup_type not in _VALID_BACKUP_TYPES:
        raise ValueError(
            f"Invalid backup_type {backup_type!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_BACKUP_TYPES))}"
        )
    cmd = f"chviosbackup -id {shlex.quote(vios_uuid)} -operation backup -type {shlex.quote(backup_type)}"
    return _run(lambda: run_hmc_cli(cmd))


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_restore_vios(vios_uuid: str, backup_name: str) -> str:
    """Restore a VIOS from a named backup via the HMC CLI.

    Runs ``chviosbackup -id <vios_uuid> -operation restore -file <backup_name>``
    on the HMC via SSH. vios_uuid is the VIOS UUID (from hmc_vios);
    backup_name is the backup file name as listed by hmc_list_vios_backups.

    WARNING: Restoring overwrites the current VIOS configuration. Confirm
    the vios_uuid and backup_name before calling.

    Returns the raw HMC CLI output.
    """
    cmd = f"chviosbackup -id {shlex.quote(vios_uuid)} -operation restore -file {shlex.quote(backup_name)}"
    return _run(lambda: run_hmc_cli(cmd))


