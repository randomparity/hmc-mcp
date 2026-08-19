"""MCP tools for LPAR profile backup/restore/sync and I/O slot assignment."""

from __future__ import annotations

from .tool_registry import tool_module

from ._app import (
    _ssh_with_client,
)

from .ssh_commands import (
    assign_profile_io_slot,
    backup_lpar_profiles,
    restore_lpar_profiles,
    sync_lpar_profile,
)


# destructive because force=True silently overwrites an existing backup file on the HMC
tool, register_tools, tool_security = tool_module()


@tool(effect="destructive", operation="lpar_profile.backup", target_kind="managed_system")
def hmc_backup_lpar_profiles(
    system_name_or_uuid: str,
    file_path: str,
    force: bool = False,
    profile: str | None = None,
) -> str:
    """Backup all LPAR profiles on a Power system via the HMC CLI.

    Runs ``bkprofdata -m <system_name> -f <file_path>`` on the HMC via SSH
    and returns the raw command output.

    The system may be given by CLI name or by UUID; a UUID is resolved to
    its CLI name via REST (falling back to an lssyscfg lookup over SSH when
    the REST API is unreachable) before the command runs.

    **IMPORTANT:** file_path is on the HMC filesystem, not the local machine.
    The backup file will be created at that path on the HMC host.

    Args:
        system_name_or_uuid: The name or UUID of the managed system (Power server).
        file_path: Path on the HMC filesystem where the backup file will be saved.
        force: When True, passes ``--force`` to ``bkprofdata`` so that an
            existing file at ``file_path`` is overwritten. Defaults to False.
        profile: optional TOML profile name; when omitted the env-default HMC is used.

    Returns:
        The raw HMC CLI output.

    Raises:
        ValueError: if ``file_path`` is empty or whitespace-only.
    """
    if not file_path or not file_path.strip():
        raise ValueError("file_path must not be empty")
    return _ssh_with_client(
        lambda config, system_name, _: backup_lpar_profiles(
            config, system_name, file_path, force=force
        ),
        system_name_or_uuid=system_name_or_uuid,
        profile=profile,
    )


@tool(effect="destructive", operation="lpar_profile.restore", target_kind="managed_system")
def hmc_restore_lpar_profiles(
    system_name_or_uuid: str, file_path: str, profile: str | None = None
) -> str:
    """Restore LPAR profiles from a backup file via the HMC CLI.

    Runs ``rstprofdata -m <system_name> -f <file_path>`` on the HMC via SSH
    and returns the raw command output.

    The system may be given by CLI name or by UUID; a UUID is resolved to
    its CLI name via REST (falling back to an lssyscfg lookup over SSH when
    the REST API is unreachable) before the command runs.

    **IMPORTANT:** file_path is on the HMC filesystem, not the local machine.
    The backup file must already exist at that path on the HMC host.

    WARNING: Restoring overwrites the current LPAR profile configuration.
    Confirm the system_name_or_uuid and file_path before calling.

    Args:
        system_name_or_uuid: The name or UUID of the managed system (Power server).
        file_path: Path on the HMC filesystem where the backup file is located.
        profile: optional TOML profile name; when omitted the env-default HMC is used.

    Returns:
        The raw HMC CLI output."""
    return _ssh_with_client(
        lambda config, system_name, _: restore_lpar_profiles(
            config, system_name, file_path
        ),
        system_name_or_uuid=system_name_or_uuid,
        profile=profile,
    )


@tool(effect="destructive", operation="lpar_profile.sync", target_kind="lpar")
def hmc_sync_lpar_profile(
    system_name_or_uuid: str, lpar_name_or_uuid: str, profile: str | None = None
) -> str:
    """Sync an LPAR's running configuration back to its current profile.

    Runs ``chsyscfg -r lpar -m <system_name> -i "name=<lpar_name>,sync_curr_profile=1"``
    on the HMC via SSH and returns the raw command output.

    This operation saves the LPAR's current running configuration to its
    current named profile, overwriting the previous profile definition.

    WARNING: Overwrites the current profile definition. Confirm the
    system_name_or_uuid and lpar_name_or_uuid before calling.

    The system and partition may be given by CLI name or by UUID; UUIDs
    are resolved to their CLI names via REST (falling back to an lssyscfg
    lookup over SSH when the REST API is unreachable) before the command
    runs.

    Args:
        system_name_or_uuid: The name or UUID of the managed system (Power server).
        lpar_name_or_uuid: The name or UUID of the logical partition to sync.
        profile: optional TOML profile name; when omitted the env-default HMC is used.

    Returns:
        The raw HMC CLI output."""
    return _ssh_with_client(
        lambda config, system_name, lpar_name: sync_lpar_profile(
            config, system_name, lpar_name
        ),
        system_name_or_uuid=system_name_or_uuid,
        lpar_name_or_uuid=lpar_name_or_uuid,
        profile=profile,
    )


@tool(effect="mutate", operation="lpar_profile.assign_io_slot", target_kind="lpar")
def hmc_assign_profile_io_slot(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    profile_name: str,
    drc_index: str,
    profile: str | None = None,
) -> str:
    """Add a physical I/O slot DRC index to an LPAR's profile.

    Runs ``chsyscfg -r prof -m <system_name> -i "name=<profile_name>,io_slots+=<drc_index>//0,lpar_name=<lpar_name>" --force``
    on the HMC via SSH and returns the raw command output.

    This operation appends the specified physical I/O slot to the profile's
    I/O slot list. Use --force to override any conflicts.

    The system and partition may be given by CLI name or by UUID; UUIDs
    are resolved to their CLI names via REST (falling back to an lssyscfg
    lookup over SSH when the REST API is unreachable) before the command
    runs.

    Args:
        system_name_or_uuid: The name or UUID of the managed system (Power server).
        lpar_name_or_uuid: The name or UUID of the logical partition to assign the slot to.
        profile_name: The name of the profile to modify.
        drc_index: The DRC (Dynamic Reconfiguration Connector) index of the physical I/O slot.
        profile: optional TOML profile name; when omitted the env-default HMC is used.

    Returns:
        The raw HMC CLI output."""
    return _ssh_with_client(
        lambda config, system_name, lpar_name: assign_profile_io_slot(
            config, system_name, lpar_name, profile_name, drc_index
        ),
        system_name_or_uuid=system_name_or_uuid,
        lpar_name_or_uuid=lpar_name_or_uuid,
        profile=profile,
    )
