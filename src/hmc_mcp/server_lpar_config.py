"""MCP tools for LPAR configuration exposed only by the HMC CLI."""

from __future__ import annotations

from .tool_registry import tool_module

from ._app import (
    _READ_ONLY,
    _ssh_with_client,
)

from .ssh_commands import (
    validate_lpar_description,
    get_lpar_description,
    get_lpar_msp,
    get_lpar_proc_compat,
    set_lpar_description,
    set_lpar_msp,
    set_lpar_proc_compat,
)
from .operations_lpar import authorize_lpar_mutation
from .client import HMCClient
from typing import Literal


tool, register_tools = tool_module()

ProcessorCompatibilityMode = Literal[
    "default",
    "POWER5",
    "POWER6",
    "POWER6+",
    "POWER7",
    "POWER8",
    "POWER9_Base",
    "POWER9",
    "POWER10",
    "POWER11",
]
PROCESSOR_COMPATIBILITY_MODES: frozenset[ProcessorCompatibilityMode] = frozenset(
    {
        "default",
        "POWER5",
        "POWER6",
        "POWER6+",
        "POWER7",
        "POWER8",
        "POWER9_Base",
        "POWER9",
        "POWER10",
        "POWER11",
    }
)


@tool(annotations=_READ_ONLY)
def hmc_get_lpar_description(
    system_name_or_uuid: str, lpar_name_or_uuid: str, profile: str | None = None
) -> str:
    """Return an LPAR's CLI-only description, resolving names or UUIDs.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        lpar_name_or_uuid: Partition name or UUID from ``hmc_list_lpars``.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return _ssh_with_client(
        lambda config, system_name, lpar_name: get_lpar_description(
            config, system_name, lpar_name
        ),
        system_name_or_uuid=system_name_or_uuid,
        lpar_name_or_uuid=lpar_name_or_uuid,
        profile=profile,
    )


@tool
def hmc_set_lpar_description(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    description: str,
    ownership_override: bool = False,
    profile: str | None = None,
) -> str:
    """Set an LPAR's CLI-only description after validating printable ASCII.

    The current description's ownership token is enforced before overwrite.
    Foreign-owned or malformed tokens are rejected. Set ownership_override=True
    only after explicit operator approval.

    WARNING: This changes LPAR configuration on the selected HMC.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        lpar_name_or_uuid: Partition name or UUID from ``hmc_list_lpars``.
        description: New printable-ASCII partition description.
        ownership_override: Permit overwriting a foreign or malformed ownership token
            only after explicit operator approval.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    validate_lpar_description(description)

    async def _set(config, system_name, lpar_name):
        hmc = HMCClient(config)
        await authorize_lpar_mutation(
            hmc,
            system_name,
            lpar_name,
            ownership_override=ownership_override,
        )
        return await set_lpar_description(config, system_name, lpar_name, description)

    return _ssh_with_client(
        _set,
        system_name_or_uuid=system_name_or_uuid,
        lpar_name_or_uuid=lpar_name_or_uuid,
        profile=profile,
    )


@tool(annotations=_READ_ONLY)
def hmc_get_lpar_msp(
    system_name_or_uuid: str, lpar_name_or_uuid: str, profile: str | None = None
) -> bool:
    """Return an LPAR's CLI-only Migratable Service Partition flag.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        lpar_name_or_uuid: Partition name or UUID from ``hmc_list_lpars``.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return _ssh_with_client(
        lambda config, system_name, lpar_name: get_lpar_msp(
            config, system_name, lpar_name
        ),
        system_name_or_uuid=system_name_or_uuid,
        lpar_name_or_uuid=lpar_name_or_uuid,
        profile=profile,
    )


@tool
def hmc_set_lpar_msp(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    enabled: bool,
    profile: str | None = None,
) -> str:
    """Set a VIOS partition's Migratable Service Partition flag.

    The command rejects non-VIOS partitions. WARNING: this changes LPAR
    configuration on the selected HMC.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        lpar_name_or_uuid: VIOS partition name or UUID from ``hmc_list_vios``.
        enabled: Whether to enable the Migratable Service Partition flag.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return _ssh_with_client(
        lambda config, system_name, lpar_name: set_lpar_msp(
            config, system_name, lpar_name, enabled
        ),
        system_name_or_uuid=system_name_or_uuid,
        lpar_name_or_uuid=lpar_name_or_uuid,
        profile=profile,
    )


@tool(annotations=_READ_ONLY)
def hmc_get_lpar_proc_compat(
    system_name_or_uuid: str, lpar_name_or_uuid: str, profile: str | None = None
) -> dict[str, str]:
    """Return an LPAR's desired and current processor compatibility modes.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        lpar_name_or_uuid: Partition name or UUID from ``hmc_list_lpars``.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return _ssh_with_client(
        lambda config, system_name, lpar_name: get_lpar_proc_compat(
            config, system_name, lpar_name
        ),
        system_name_or_uuid=system_name_or_uuid,
        lpar_name_or_uuid=lpar_name_or_uuid,
        profile=profile,
    )


@tool
def hmc_set_lpar_proc_compat(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    mode: ProcessorCompatibilityMode,
    profile: str | None = None,
) -> str:
    """Set an LPAR's processor compatibility mode.

    WARNING: This changes LPAR configuration on the selected HMC.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        lpar_name_or_uuid: Partition name or UUID from ``hmc_list_lpars``.
        mode: Desired mode supported by the system; enumerate legal values with
            ``hmc_get_proc_compat_modes``.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return _ssh_with_client(
        lambda config, system_name, lpar_name: set_lpar_proc_compat(
            config, system_name, lpar_name, mode
        ),
        system_name_or_uuid=system_name_or_uuid,
        lpar_name_or_uuid=lpar_name_or_uuid,
        profile=profile,
    )
