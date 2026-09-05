"""Shared ownership boundary for ordinary LPAR reconfiguration operations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from hmc_mcp import cli
from hmc_mcp.cli_commands.lpar import modify as cli_modify
from hmc_mcp.config import HMCConfig
from hmc_mcp.documents import LparResources
from hmc_mcp.operations.adapters import (
    add_network_adapter,
    add_vfc_adapter,
    add_vscsi_adapter,
    delete_adapter,
)
from hmc_mcp.operations.lpar.configuration import (
    configure_lpar_msp,
    configure_lpar_processor_compatibility,
    synchronize_lpar_profile,
)
from hmc_mcp.operations.lpar.provision import ProvisionStorage, attach_disk_to_lpar
from hmc_mcp.operations.lpm import (
    LpmMigrationRequest,
    RemoteRestartRequest,
    abort_lpar_migration,
    migrate_lpar,
    recover_lpar_migration,
    remote_restart_lpar,
)
from hmc_mcp.operations.storage import (
    map_storage,
    mount_optical_media,
    unmount_optical_media,
)
from hmc_mcp.server_tools.lpar import configuration as server_configuration
from hmc_mcp.server_tools.lpar import lifecycle as server_lifecycle
from hmc_mcp.server_tools.lpar import profiles as server_profiles

LPAR = "11111111-1111-4111-8111-111111111111"
VIOS = "22222222-2222-4222-8222-222222222222"
VG = "33333333-3333-4333-8333-333333333333"
SYSTEM_NAME = "frame1"
SYSTEM_UUID = "44444444-4444-4444-8444-444444444444"
LPAR_NAME = "client1"
FOREIGN_OWNER = "[hmc-mcp owner:another-agent created:2026-08-14]"

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
            VIOS,
            LPAR,
            system_name_or_uuid=None,
            kind="VirtualDisk",
            storage_name="disk1",
        ),
    ),
    (
        "hmc_mcp.operations.storage.resolve_and_authorize_lpar_mutation",
        lambda hmc: mount_optical_media(
                hmc, VIOS, LPAR, media_name="aix.iso"
        ),
    ),
    (
        "hmc_mcp.operations.storage.resolve_and_authorize_lpar_mutation",
        lambda hmc: unmount_optical_media(
                hmc, VIOS, LPAR, media_name="aix.iso"
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
        lambda hmc: migrate_lpar(
            hmc, None, LPAR, LpmMigrationRequest("target"), validate_first=False
        ),
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
        lambda hmc: remote_restart_lpar(
            hmc, "source", LPAR, RemoteRestartRequest("cleanup")
        ),
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
        "delete_storage_mapping",
        "create_virtual_disk",
        "lpar_migrate",
        "lpar_migrate_abort",
        "lpar_migrate_recover",
        "lpar_remote_restart",
    ):
        getattr(hmc, method).assert_not_awaited()


def _real_guard_hmc() -> AsyncMock:
    """Configure the client double for the real ownership guard path."""
    hmc = AsyncMock()
    hmc.config = HMCConfig.from_mapping(
        {
            "host": "hmc.test",
            "user": "u",
            "password": "p",
            "agent_id": "my-agent",
        }
    )
    hmc.get_managed_system.return_value = {"Resource": {"SystemName": SYSTEM_NAME}}
    hmc.get_logical_partition.return_value = {
        "Resource": {"PartitionName": LPAR_NAME}
    }
    hmc.list_logical_partitions.return_value = [{"UUID": LPAR}]
    hmc.list_optical_mappings.return_value = [
        {
            "UUID": "mapping-uuid",
            "Storage": {"VirtualOpticalMedia": {"MediaName": "aix.iso"}},
        }
    ]
    return hmc


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "write_method"),
    [
        (mount_optical_media, "create_optical_mapping"),
        (unmount_optical_media, "delete_storage_mapping"),
    ],
)
async def test_real_guard_rejects_foreign_optical_owner_before_write(
    operation: Operation, write_method: str
) -> None:
    """A foreign token blocks both optical mutations before their HMC write."""
    hmc = _real_guard_hmc()

    with patch(
        "hmc_mcp.operations.ownership.get_lpar_description",
        new=AsyncMock(return_value=FOREIGN_OWNER),
    ), pytest.raises(PermissionError, match="ownership_override=true"):
            await operation(
                hmc, VIOS, LPAR, media_name="aix.iso", system_name_or_uuid=SYSTEM_UUID
            )

    getattr(hmc, write_method).assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "write_method"),
    [
        (mount_optical_media, "create_optical_mapping"),
        (unmount_optical_media, "delete_storage_mapping"),
    ],
)
async def test_optical_ownership_override_bypasses_read_and_writes(
    operation: Operation, write_method: str
) -> None:
    """An approved override skips the ownership read and reaches the write."""
    hmc = _real_guard_hmc()

    with patch(
        "hmc_mcp.operations.ownership.get_lpar_description",
        new=AsyncMock(return_value=FOREIGN_OWNER),
    ) as read:
        await operation(
            hmc,
                VIOS,
                LPAR,
                media_name="aix.iso",
                system_name_or_uuid=SYSTEM_UUID,
            ownership_override=True,
        )

    read.assert_not_awaited()
    getattr(hmc, write_method).assert_awaited_once()


def test_mcp_resource_modify_rejects_foreign_owner_before_hmc_write() -> None:
    """The MCP resource-only leg uses the real guard before its POST."""
    hmc = _real_guard_hmc()
    with (
        patch(
            "hmc_mcp.operations.ownership.get_lpar_description",
            new=AsyncMock(return_value=FOREIGN_OWNER),
        ),
        patch(
            "hmc_mcp.server_tools.lpar.lifecycle.with_client",
            side_effect=lambda fn, **_: asyncio.run(fn(hmc)),
        ),
        pytest.raises(PermissionError, match="ownership_override=true"),
    ):
        server_lifecycle.hmc_modify_lpar(
            LPAR,
            resources=LparResources(desired_memory=8192),
            system_name_or_uuid=SYSTEM_UUID,
        )

    hmc.modify_logical_partition.assert_not_awaited()


def test_cli_resource_modify_rejects_foreign_owner_before_hmc_write() -> None:
    """The CLI resource-only leg uses the real guard before its POST."""
    hmc = _real_guard_hmc()
    context = AsyncMock()
    context.__aenter__.return_value = hmc
    with (
        patch(
            "hmc_mcp.operations.ownership.get_lpar_description",
            new=AsyncMock(return_value=FOREIGN_OWNER),
        ),
        patch.object(cli_modify, "client", return_value=context),
        patch.object(cli_modify, "run", side_effect=lambda fn: asyncio.run(fn())),
    ):
        result = CliRunner().invoke(
            cli.app,
            [
                "lpars",
                "modify",
                LPAR,
                "--system",
                SYSTEM_UUID,
                "--mem",
                "8192",
                "--yes",
            ],
        )
        assert result.exit_code != 0
        assert result.exception is not None
        assert "ownership_override=true" in str(result.exception)

    hmc.modify_logical_partition.assert_not_awaited()


def test_resource_modify_override_skips_ownership_read_and_writes() -> None:
    """An approved resource override reaches the shared operation's POST."""
    hmc = _real_guard_hmc()
    hmc.modify_logical_partition.return_value = {"Resource": {"PartitionName": LPAR_NAME}}
    with patch(
        "hmc_mcp.operations.ownership.get_lpar_description",
        new=AsyncMock(return_value=FOREIGN_OWNER),
    ) as read, patch(
        "hmc_mcp.server_tools.lpar.lifecycle.with_client",
        side_effect=lambda fn, **_: asyncio.run(fn(hmc)),
    ):
        server_lifecycle.hmc_modify_lpar(
            LPAR,
            resources=LparResources(desired_memory=8192),
            ownership_override=True,
        )

    read.assert_not_awaited()
    hmc.modify_logical_partition.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "write_name", "args"),
    [
        (configure_lpar_msp, "set_lpar_msp", (True,)),
        (configure_lpar_processor_compatibility, "set_lpar_proc_compat", ("POWER9",)),
        (synchronize_lpar_profile, "sync_lpar_profile", ()),
    ],
)
async def test_config_operations_reject_foreign_owner_before_ssh_write(
    operation: Operation, write_name: str, args: tuple[object, ...]
) -> None:
    """Each extracted configuration mutation checks ownership before its write."""
    hmc = _real_guard_hmc()
    write = AsyncMock(return_value="ok")

    with (
        patch(
            "hmc_mcp.operations.ownership.get_lpar_description",
            new=AsyncMock(return_value=FOREIGN_OWNER),
        ),
        patch(f"hmc_mcp.operations.lpar.configuration.{write_name}", new=write),
        pytest.raises(PermissionError, match="ownership_override=true"),
    ):
        await operation(hmc, SYSTEM_UUID, LPAR, *args)

    write.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "write_name", "args"),
    [
        (configure_lpar_msp, "set_lpar_msp", (True,)),
        (configure_lpar_processor_compatibility, "set_lpar_proc_compat", ("POWER9",)),
        (synchronize_lpar_profile, "sync_lpar_profile", ()),
    ],
)
async def test_config_operations_override_skips_ownership_read(
    operation: Operation, write_name: str, args: tuple[object, ...]
) -> None:
    """An approved override bypasses the ownership read and reaches each write."""
    hmc = _real_guard_hmc()
    write = AsyncMock(return_value="ok")

    with (
        patch(
            "hmc_mcp.operations.ownership.get_lpar_description",
            new=AsyncMock(return_value=FOREIGN_OWNER),
        ) as read,
        patch(f"hmc_mcp.operations.lpar.configuration.{write_name}", new=write),
    ):
        await operation(hmc, SYSTEM_UUID, LPAR, *args, ownership_override=True)

    read.assert_not_awaited()
    write.assert_awaited_once()


@pytest.mark.parametrize(
    ("tool", "operation", "args"),
    [
        (
            server_configuration.hmc_set_lpar_msp,
            "configure_lpar_msp",
            (SYSTEM_UUID, LPAR, True),
        ),
        (
            server_configuration.hmc_set_lpar_proc_compat,
            "configure_lpar_processor_compatibility",
            (SYSTEM_UUID, LPAR, "POWER9"),
        ),
        (
            server_profiles.hmc_sync_lpar_profile,
            "synchronize_lpar_profile",
            (SYSTEM_UUID, LPAR),
        ),
    ],
)
def test_mcp_config_tools_forward_ownership_override(
    monkeypatch: pytest.MonkeyPatch, tool, operation: str, args: tuple[object, ...]
) -> None:
    """MCP wrappers expose and forward the explicit ownership override."""
    target_module = (
        server_profiles
        if tool is server_profiles.hmc_sync_lpar_profile
        else server_configuration
    )
    delegated = AsyncMock(return_value="ok")
    monkeypatch.setattr(target_module, operation, delegated)

    async def invoke(fn, *, profile=None):
        return await fn(AsyncMock())

    monkeypatch.setattr(
        target_module, "with_client", lambda fn, **kwargs: asyncio.run(invoke(fn))
    )
    result = tool(*args, ownership_override=True)

    assert result == "ok"
    delegated.assert_awaited_once()
    assert delegated.await_args.kwargs["ownership_override"] is True


@pytest.mark.asyncio
async def test_delete_adapter_returns_deleted_adapter_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = AsyncMock(return_value=LPAR)
    monkeypatch.setattr(
        "hmc_mcp.operations.adapters.resolve_and_authorize_lpar_mutation", guard
    )
    hmc = AsyncMock()

    result = await delete_adapter(
        hmc, None, LPAR, "ClientNetworkAdapter", "adapter-uuid"
    )

    assert result == "adapter-uuid"
    hmc.delete_adapter.assert_awaited_once_with(
        LPAR, "ClientNetworkAdapter", "adapter-uuid"
    )
