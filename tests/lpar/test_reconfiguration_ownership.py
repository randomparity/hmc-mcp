"""Shared ownership boundary for ordinary LPAR reconfiguration operations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock

import pytest

from hmc_mcp.operations.adapters import (
    add_network_adapter,
    add_vfc_adapter,
    add_vscsi_adapter,
    delete_adapter,
)
from hmc_mcp.operations.lpm import (
    abort_lpar_migration,
    migrate_lpar,
    recover_lpar_migration,
    remote_restart_lpar,
)
from hmc_mcp.operations.lpar.provision import ProvisionStorage, attach_disk_to_lpar
from hmc_mcp.operations.storage import (
    map_storage,
    mount_optical_media,
    unmount_optical_media,
)

LPAR = "11111111-1111-4111-8111-111111111111"
VIOS = "22222222-2222-4222-8222-222222222222"
VG = "33333333-3333-4333-8333-333333333333"

Operation = Callable[[AsyncMock], Awaitable[object]]


CASES: tuple[tuple[str, Operation], ...] = (
    (
        "hmc_mcp.operations.adapters.resolve_and_authorize_lpar_mutation",
        lambda hmc: add_network_adapter(hmc, None, LPAR, 100),
    ),
    (
        "hmc_mcp.operations.adapters.resolve_and_authorize_lpar_mutation",
        lambda hmc: add_vscsi_adapter(hmc, None, LPAR, 2, 10),
    ),
    (
        "hmc_mcp.operations.adapters.resolve_and_authorize_lpar_mutation",
        lambda hmc: add_vfc_adapter(hmc, None, LPAR, 2, 10),
    ),
    (
        "hmc_mcp.operations.adapters.resolve_and_authorize_lpar_mutation",
        lambda hmc: delete_adapter(
            hmc, None, LPAR, "ClientNetworkAdapter", "adapter"
        ),
    ),
    (
        "hmc_mcp.operations.storage.resolve_and_authorize_lpar_mutation",
        lambda hmc: map_storage(
            hmc,
            None,
            VIOS,
            LPAR,
            kind="VirtualDisk",
            storage_name="disk1",
        ),
    ),
    (
        "hmc_mcp.operations.storage.resolve_and_authorize_lpar_mutation",
        lambda hmc: mount_optical_media(
            hmc, None, VIOS, LPAR, media_name="aix.iso"
        ),
    ),
    (
        "hmc_mcp.operations.storage.resolve_and_authorize_lpar_mutation",
        lambda hmc: unmount_optical_media(
            hmc, None, VIOS, LPAR, media_name="aix.iso"
        ),
    ),
    (
        "hmc_mcp.operations.lpar.provision.resolve_and_authorize_lpar_mutation",
        lambda hmc: attach_disk_to_lpar(
            hmc,
            None,
            LPAR,
            ProvisionStorage(VIOS, "disk1", vg_uuid=VG),
            capacity_mib=1024,
            vios_partition_id=2,
            vios_slot=10,
        ),
    ),
    (
        "hmc_mcp.operations.lpm.resolve_and_authorize_lpar_mutation",
        lambda hmc: migrate_lpar(hmc, None, LPAR, "target", validate_first=False),
    ),
    (
        "hmc_mcp.operations.lpm.resolve_and_authorize_lpar_mutation",
        lambda hmc: abort_lpar_migration(hmc, None, LPAR),
    ),
    (
        "hmc_mcp.operations.lpm.resolve_and_authorize_lpar_mutation",
        lambda hmc: recover_lpar_migration(hmc, None, LPAR),
    ),
    (
        "hmc_mcp.operations.lpm.resolve_and_authorize_lpar_mutation",
        lambda hmc: remote_restart_lpar(hmc, "source", LPAR, "cleanup"),
    ),
)


@pytest.mark.parametrize(("guard_path", "operation"), CASES)
@pytest.mark.asyncio
async def test_foreign_owner_stops_reconfiguration_before_first_write(
    monkeypatch: pytest.MonkeyPatch, guard_path: str, operation: Operation
) -> None:
    guard = AsyncMock(side_effect=PermissionError("foreign owner"))
    monkeypatch.setattr(guard_path, guard)
    hmc = AsyncMock()
    hmc.list_volume_groups.return_value = [{"UUID": VG}]

    with pytest.raises(PermissionError, match="foreign owner"):
        await operation(hmc)

    guard.assert_awaited_once()
    for method in (
        "add_network_adapter",
        "add_vscsi_adapter",
        "add_vfc_adapter",
        "delete_adapter",
        "map_storage_to_lpar",
        "create_optical_mapping",
        "delete_optical_mapping",
        "create_virtual_disk",
        "lpar_migrate",
        "lpar_migrate_abort",
        "lpar_migrate_recover",
        "lpar_remote_restart",
    ):
        getattr(hmc, method).assert_not_awaited()
