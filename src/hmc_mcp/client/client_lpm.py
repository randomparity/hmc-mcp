"""HMCClient lpm mixin.

The full client is assembled in :mod:`hmc_mcp.client` by inheriting every
domain mixin; this module only defines methods for lpm.
"""

from __future__ import annotations

from typing import Any

from .client_contracts import LpmClient
from ..jobs import (
    RemoteRestartOperation,
    migrate_abort_lpar_job,
    migrate_lpar_job,
    migrate_recover_lpar_job,
    migrate_validate_lpar_job,
    remote_restart_lpar_job,
)


class LpmMixin:
    # ------------------------------------------------------------------ #
    # Live Partition Mobility (LPM)
    # ------------------------------------------------------------------ #
    async def _lpar_job(
        self: LpmClient, lpar_uuid: str, operation: str, job_xml: str
    ) -> dict[str, Any] | None:
        return await self.submit_job(
            f"/rest/api/uom/LogicalPartition/{lpar_uuid}/do/{operation}", job_xml
        )

    async def lpar_migrate(
        self: LpmClient,
        lpar_uuid: str,
        target_system: str,
        target_profile_name: str | None = None,
        destination_lpar_id: str | None = None,
        shared_proc_pool_id: str | None = None,
        wait_time: int | None = None,
    ) -> dict[str, Any] | None:
        """Migrate (LPM) an LPAR to another managed system."""

        xml = migrate_lpar_job(
            target_system,
            target_profile_name,
            destination_lpar_id,
            shared_proc_pool_id,
            wait_time,
        )
        return await self._lpar_job(lpar_uuid, "Migrate", xml)

    async def lpar_migrate_validate(
        self: LpmClient,
        lpar_uuid: str,
        target_system: str,
        target_profile_name: str | None = None,
        destination_lpar_id: str | None = None,
        shared_proc_pool_id: str | None = None,
        wait_time: int | None = None,
    ) -> dict[str, Any] | None:
        """Validate whether an LPM migration would succeed."""

        xml = migrate_validate_lpar_job(
            target_system,
            target_profile_name,
            destination_lpar_id,
            shared_proc_pool_id,
            wait_time,
        )
        return await self._lpar_job(lpar_uuid, "MigrateValidate", xml)

    async def lpar_migrate_abort(
        self: LpmClient, lpar_uuid: str
    ) -> dict[str, Any] | None:
        """Abort an in-progress LPM migration."""

        return await self._lpar_job(lpar_uuid, "MigrateAbort", migrate_abort_lpar_job())

    async def lpar_migrate_recover(
        self: LpmClient, lpar_uuid: str
    ) -> dict[str, Any] | None:
        """Recover an LPAR after a failed LPM migration."""

        return await self._lpar_job(
            lpar_uuid, "MigrateRecover", migrate_recover_lpar_job()
        )

    async def lpar_remote_restart(
        self: LpmClient,
        lpar_uuid: str,
        operation: RemoteRestartOperation,
        managed_system: str,
        *,
        target_managed_system: str | None = None,
        target_managed_system_uuid: str | None = None,
        use_current_data: bool = False,
        retain_devices: bool = False,
    ) -> dict[str, Any] | None:
        """Submit an explicit RemoteRestart operation for a failed LPAR."""
        xml = remote_restart_lpar_job(
            operation,
            managed_system,
            lpar_uuid,
            target_managed_system=target_managed_system,
            target_managed_system_uuid=target_managed_system_uuid,
            use_current_data=use_current_data,
            retain_devices=retain_devices,
        )
        return await self._lpar_job(lpar_uuid, "RemoteRestart", xml)
