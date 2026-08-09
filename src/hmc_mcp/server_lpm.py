"""MCP tools for Live Partition Mobility (LPM).
"""

from __future__ import annotations

from typing import Any

from ._app import (
    _DESTRUCTIVE,
    mcp,
    with_client,
)


@mcp.tool
def hmc_migrate_lpar(
    lpar_uuid: str,
    target_system: str,
    target_profile_name: str | None = None,
    wait_time: int | None = None,
) -> dict[str, Any] | None:
    """Live-migrate (LPM) an LPAR to another managed system.

    Submits a Migrate job. target_system is the target managed-system name.
    Optionally pin the target profile / wait time. Poll hmc_get_job for status.
    Run hmc_migrate_validate_lpar first to pre-check.
    """

    return with_client(
        lambda hmc: hmc.lpar_migrate(
            lpar_uuid, target_system, target_profile_name, wait_time=wait_time
        )
    )


@mcp.tool
def hmc_migrate_validate_lpar(
    lpar_uuid: str,
    target_system: str,
    target_profile_name: str | None = None,
    wait_time: int | None = None,
) -> dict[str, Any] | None:
    """Validate whether an LPM migration of an LPAR to target_system would succeed."""

    return with_client(
        lambda hmc: hmc.lpar_migrate_validate(
            lpar_uuid, target_system, target_profile_name, wait_time=wait_time
        )
    )


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_migrate_abort_lpar(lpar_uuid: str) -> dict[str, Any] | None:
    """Abort an in-progress LPM migration of an LPAR."""

    return with_client(lambda hmc: hmc.lpar_migrate_abort(lpar_uuid))


@mcp.tool
def hmc_migrate_recover_lpar(lpar_uuid: str) -> dict[str, Any] | None:
    """Recover an LPAR after a failed LPM migration."""

    return with_client(lambda hmc: hmc.lpar_migrate_recover(lpar_uuid))


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_remote_restart_lpar(lpar_uuid: str, target_system: str) -> dict[str, Any] | None:
    """Remote-restart a failed LPAR on another managed system."""

    return with_client(lambda hmc: hmc.lpar_remote_restart(lpar_uuid, target_system))


