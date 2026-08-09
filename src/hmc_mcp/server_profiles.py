"""MCP tools for LPAR profile backup/restore/sync and I/O slot assignment.
"""

from __future__ import annotations

import shlex

from ._app import (
    _DESTRUCTIVE,
    _ssh_with_client,
    mcp,
)

from .ssh import run_hmc_command



@mcp.tool
def hmc_backup_lpar_profiles(system_uuid: str, file_path: str) -> str:
    """Backup all LPAR profiles on a Power system via the HMC CLI.

    Runs ``bkprofdata -m <system_name> -f <file_path>`` on the HMC via SSH
    and returns the raw command output.

    The system UUID is resolved to its CLI name via REST before the command
    runs.

    **IMPORTANT:** file_path is on the HMC filesystem, not the local machine.
    The backup file will be created at that path on the HMC host.

    Args:
        system_uuid: The UUID of the managed system (Power server).
        file_path: Path on the HMC filesystem where the backup file will be saved.

    Returns:
        The raw HMC CLI output.    """
    return _ssh_with_client(
        lambda config, system_name, _: run_hmc_command(
            config, f"bkprofdata -m {shlex.quote(system_name)} -f {shlex.quote(file_path)}"
        ),
        system_uuid=system_uuid,
    )


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_restore_lpar_profiles(system_uuid: str, file_path: str) -> str:
    """Restore LPAR profiles from a backup file via the HMC CLI.

    Runs ``rstprofdata -m <system_name> -f <file_path>`` on the HMC via SSH
    and returns the raw command output.

    The system UUID is resolved to its CLI name via REST before the command
    runs.

    **IMPORTANT:** file_path is on the HMC filesystem, not the local machine.
    The backup file must already exist at that path on the HMC host.

    WARNING: Restoring overwrites the current LPAR profile configuration.
    Confirm the system_uuid and file_path before calling.

    Args:
        system_uuid: The UUID of the managed system (Power server).
        file_path: Path on the HMC filesystem where the backup file is located.

    Returns:
        The raw HMC CLI output.    """
    return _ssh_with_client(
        lambda config, system_name, _: run_hmc_command(
            config, f"rstprofdata -m {shlex.quote(system_name)} -f {shlex.quote(file_path)}"
        ),
        system_uuid=system_uuid,
    )


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_sync_lpar_profile(system_uuid: str, lpar_uuid: str) -> str:
    """Sync an LPAR's running configuration back to its current profile.

    Runs ``chsyscfg -r lpar -m <system_name> -i "name=<lpar_name>,sync_curr_profile=1"``
    on the HMC via SSH and returns the raw command output.

    This operation saves the LPAR's current running configuration to its
    current named profile, overwriting the previous profile definition.

    WARNING: Overwrites the current profile definition. Confirm the
    system_uuid and lpar_uuid before calling.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs.

    Args:
        system_uuid: The UUID of the managed system (Power server).
        lpar_uuid: The UUID of the logical partition to sync.

    Returns:
        The raw HMC CLI output.    """
    return _ssh_with_client(
        lambda config, system_name, lpar_name: run_hmc_command(
            config,
            f"chsyscfg -r lpar -m {shlex.quote(system_name)} -i "
            f"{shlex.quote(f'name={lpar_name},sync_curr_profile=1')}",
        ),
        system_uuid=system_uuid,
        lpar_uuid=lpar_uuid,
    )


@mcp.tool
def hmc_assign_profile_io_slot(
    system_uuid: str, lpar_uuid: str, profile_name: str, drc_index: str
) -> str:
    """Add a physical I/O slot DRC index to an LPAR's profile.

    Runs ``chsyscfg -r prof -m <system_name> -i "name=<profile_name>,io_slots+=<drc_index>//0,lpar_name=<lpar_name>" --force``
    on the HMC via SSH and returns the raw command output.

    This operation appends the specified physical I/O slot to the profile's
    I/O slot list. Use --force to override any conflicts.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs.

    Args:
        system_uuid: The UUID of the managed system (Power server).
        lpar_uuid: The UUID of the logical partition to assign the slot to.
        profile_name: The name of the profile to modify.
        drc_index: The DRC (Dynamic Reconfiguration Connector) index of the physical I/O slot.

    Returns:
        The raw HMC CLI output.    """
    return _ssh_with_client(
        lambda config, system_name, lpar_name: run_hmc_command(
            config,
            f"chsyscfg -r prof -m {shlex.quote(system_name)} -i "
            f"{shlex.quote(f'name={profile_name},io_slots+={drc_index}//0,lpar_name={lpar_name}')} --force",
        ),
        system_uuid=system_uuid,
        lpar_uuid=lpar_uuid,
    )

