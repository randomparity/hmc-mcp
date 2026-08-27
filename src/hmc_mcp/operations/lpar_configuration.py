"""Guarded SSH-backed LPAR configuration mutations."""

from __future__ import annotations

from ..client import HMCClient
from ..resource_identity import resolve_lpar_uuid, resolve_system_uuid
from ..ssh.profiles import set_lpar_msp, set_lpar_proc_compat, sync_lpar_profile
from hmc_mcp.operations.lpar import ProcessorCompatibilityMode
from .lpar_ownership import authorize_lpar_mutation, resolve_lpar_ownership_names


async def _authorized_names(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    ownership_override: bool,
) -> tuple[str, str]:
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    lpar_uuid = await resolve_lpar_uuid(
        hmc, lpar_name_or_uuid, system_name_or_uuid=system_uuid
    )
    names = await resolve_lpar_ownership_names(
        hmc, system_uuid, system_name_or_uuid, lpar_uuid
    )
    await authorize_lpar_mutation(hmc, *names, ownership_override=ownership_override)
    return names


async def synchronize_lpar_profile(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    *,
    ownership_override: bool = False,
) -> str:
    """Authorize and synchronize an LPAR's active configuration to its profile."""
    system_name, lpar_name = await _authorized_names(
        hmc, system_name_or_uuid, lpar_name_or_uuid, ownership_override
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
    system_name, lpar_name = await _authorized_names(
        hmc, system_name_or_uuid, lpar_name_or_uuid, ownership_override
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
    system_name, lpar_name = await _authorized_names(
        hmc, system_name_or_uuid, lpar_name_or_uuid, ownership_override
    )
    return await set_lpar_proc_compat(hmc.config, system_name, lpar_name, mode)
