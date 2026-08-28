"""Guarded SSH-backed LPAR configuration mutations."""

from __future__ import annotations

from ...client import HMCClient
from ...ssh.profiles import set_lpar_msp, set_lpar_proc_compat, sync_lpar_profile
from .core import ProcessorCompatibilityMode
from hmc_mcp.operations.ownership import resolve_and_authorize_lpar_names


async def synchronize_lpar_profile(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    *,
    ownership_override: bool = False,
) -> str:
    """Authorize and synchronize an LPAR's active configuration to its profile."""
    system_name, lpar_name = await resolve_and_authorize_lpar_names(
        hmc,
        system_name_or_uuid,
        lpar_name_or_uuid,
        ownership_override=ownership_override,
    )
    return await sync_lpar_profile(hmc.config, system_name, lpar_name)


async def configure_lpar_msp(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    enabled: bool,
    *,
    ownership_override: bool = False,
) -> str:
    """Authorize and set an LPAR's migratable-service-partition flag."""
    system_name, lpar_name = await resolve_and_authorize_lpar_names(
        hmc,
        system_name_or_uuid,
        lpar_name_or_uuid,
        ownership_override=ownership_override,
    )
    return await set_lpar_msp(hmc.config, system_name, lpar_name, enabled)


async def configure_lpar_processor_compatibility(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    mode: ProcessorCompatibilityMode,
    *,
    ownership_override: bool = False,
) -> str:
    """Authorize and set an LPAR's processor compatibility mode."""
    system_name, lpar_name = await resolve_and_authorize_lpar_names(
        hmc,
        system_name_or_uuid,
        lpar_name_or_uuid,
        ownership_override=ownership_override,
    )
    return await set_lpar_proc_compat(hmc.config, system_name, lpar_name, mode)
