"""Shared LPAR creation and ownership operations."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypedDict

from .client import HMCClient
from .documents import LparResources, PartitionType
from .errors import HMCError
from .ssh import HMCCLIError
from .ssh_commands import _ssh_system_name, create_lpar_via_cli, stamp_lpar_ownership

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LparCreation:
    """Inputs needed by both REST and CLI LPAR creation paths."""

    name: str
    partition_type: PartitionType
    resources: LparResources
    max_virtual_slots: int | None = None


class LparCreationResult(TypedDict):
    """Result shared by direct creation and provisioning workflows."""

    lpar: dict[str, Any] | None
    ownership_stamped: bool | None
    warnings: list[str]


async def _system_name(hmc, system_uuid: str, fallback: str) -> str:
    try:
        system = await hmc.get_managed_system(system_uuid)
        name = ((system or {}).get("Resource") or {}).get("SystemName")
        if name:
            return name
    except Exception as exc:
        _logger.debug("REST system-name lookup failed for %s: %s", system_uuid, exc)
    try:
        return await _ssh_system_name(hmc.config, system_uuid)
    except Exception:
        return fallback


async def _stamp_ownership(
    hmc, system_uuid: str, system_fallback: str, created_lpar: dict[str, Any]
) -> tuple[bool | None, list[str]]:
    confirmed_name = (created_lpar.get("Resource") or {}).get("PartitionName")
    if not confirmed_name:
        return None, ["ownership stamp skipped: create result has no partition name"]

    system_name = await _system_name(hmc, system_uuid, system_fallback)
    if system_name == system_uuid:
        return None, [
            f"ownership stamp skipped for LPAR {confirmed_name!r}: "
            "could not resolve the managed-system name"
        ]

    token = await stamp_lpar_ownership(
        hmc.config, system_name, confirmed_name, agent_id=hmc.config.agent_id
    )
    if token is not None:
        return True, []
    _logger.warning(
        "ownership stamp failed for LPAR %r on %r", confirmed_name, system_name
    )
    return False, [f"ownership stamp failed for LPAR {confirmed_name!r}"]


async def create_and_stamp_lpar(
    hmc: HMCClient,
    system_uuid: str,
    system_name_or_uuid: str,
    creation: LparCreation,
    document: str,
) -> LparCreationResult:
    """Create an LPAR with CLI fallback and best-effort ownership stamping."""
    system_name: str | None = None
    try:
        created_lpar = await hmc.create_logical_partition(system_uuid, document)
    except HMCError as exc:
        if exc.status_code != 406:
            raise
        try:
            system_name = await _ssh_system_name(hmc.config, system_uuid)
        except HMCCLIError:
            system_name = system_name_or_uuid
        resources = creation.resources
        await create_lpar_via_cli(
            hmc.config,
            system_name=system_name,
            name=creation.name,
            partition_type=creation.partition_type,
            resources=resources,
            max_virtual_slots=creation.max_virtual_slots,
        )
        created_lpar = await hmc.find_partition_by_name(creation.name)

    if created_lpar is None:
        return {
            "lpar": None,
            "ownership_stamped": None,
            "warnings": [
                f"ownership stamp skipped for LPAR {creation.name!r}: "
                "create returned no LPAR body"
            ],
        }
    ownership_stamped, warnings = await _stamp_ownership(
        hmc, system_uuid, system_name or system_name_or_uuid, created_lpar
    )
    return {
        "lpar": created_lpar,
        "ownership_stamped": ownership_stamped,
        "warnings": warnings,
    }
