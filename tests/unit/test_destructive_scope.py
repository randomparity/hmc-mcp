from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from hmc_mcp.operations_lpar import delete_lpar, power_lpar, rename_lpar
from hmc_mcp.operations_vios import power_vios
from hmc_mcp import operations_vios, server_lpars, server_vios


def _client_factory(hmc):
    @asynccontextmanager
    async def factory(_profile=None):
        yield hmc

    return factory


@pytest.mark.asyncio
async def test_delete_lpar_uses_required_system_to_scope_name_resolution():
    hmc = AsyncMock()
    hmc.find_system_by_name.return_value = {"UUID": "system-uuid"}
    hmc.find_partition_by_name.return_value = {"UUID": "lpar-uuid"}
    hmc.get_logical_partition.return_value = {
        "Resource": {"PartitionName": "aix1", "Description": ""}
    }
    hmc.get_managed_system.return_value = {
        "Resource": {"SystemName": "system-name"}
    }
    hmc.get_quick_property.return_value = "not activated"

    assert await delete_lpar(hmc, "system-name", "aix1", ownership_override=True) == "lpar-uuid"

    hmc.find_partition_by_name.assert_awaited_once_with(
        "aix1", system_uuid="system-uuid"
    )


@pytest.mark.asyncio
async def test_rename_lpar_uses_required_system_to_scope_name_resolution():
    hmc = AsyncMock()
    hmc.find_system_by_name.return_value = {"UUID": "system-uuid"}
    hmc.find_partition_by_name.return_value = {"UUID": "lpar-uuid"}
    hmc.get_logical_partition.return_value = {
        "Resource": {"PartitionName": "aix1", "Description": ""}
    }
    hmc.get_managed_system.return_value = {
        "Resource": {"SystemName": "system-name"}
    }

    await rename_lpar(
        hmc,
        "system-name",
        "aix1",
        "renamed",
        ownership_override=True,
    )

    hmc.find_partition_by_name.assert_awaited_once_with(
        "aix1", system_uuid="system-uuid"
    )


@pytest.mark.asyncio
async def test_power_lpar_forwards_optional_system_scope():
    hmc = AsyncMock()
    hmc.find_system_by_name.return_value = {"UUID": "system-uuid"}
    hmc.find_partition_by_name.return_value = {"UUID": "lpar-uuid"}
    hmc.submit_job.return_value = {"UUID": "job-uuid"}

    await power_lpar(
        hmc,
        "aix1",
        power_on=False,
        system_name_or_uuid="system-name",
    )

    hmc.find_partition_by_name.assert_awaited_once_with(
        "aix1", system_uuid="system-uuid"
    )


@pytest.mark.asyncio
async def test_power_vios_forwards_optional_system_scope():
    hmc = AsyncMock()
    hmc.find_system_by_name.return_value = {"UUID": "system-uuid"}
    hmc.find_vios_by_name.return_value = {"UUID": "vios-uuid"}
    hmc.power_off_vios.return_value = {"UUID": "job-uuid"}

    await power_vios(
        hmc,
        "vios1",
        on=False,
        system_name_or_uuid="system-name",
    )

    hmc.find_vios_by_name.assert_awaited_once_with(
        "vios1", system_uuid="system-uuid"
    )


def test_power_off_lpar_tool_forwards_system_scope(monkeypatch):
    hmc = AsyncMock()
    operation = AsyncMock(return_value=AsyncMock(job={"UUID": "job-uuid"}))
    monkeypatch.setattr(server_lpars, "client_from_env", _client_factory(hmc))
    monkeypatch.setattr(server_lpars, "power_lpar", operation)

    server_lpars.hmc_power_off_lpar(
        "aix1", system_name_or_uuid="system-name"
    )

    assert operation.await_args.kwargs["system_name_or_uuid"] == "system-name"


def test_delete_vios_tool_scopes_name_before_mutation(monkeypatch):
    hmc = AsyncMock()
    hmc.find_system_by_name.return_value = {"UUID": "system-uuid"}
    hmc.find_vios_by_name.return_value = {"UUID": "vios-uuid"}
    hmc.get_quick_property.return_value = "not activated"
    monkeypatch.setattr(server_vios, "client_from_env", _client_factory(hmc))

    server_vios.hmc_delete_vios(
        "vios1", system_name_or_uuid="system-name"
    )

    hmc.find_vios_by_name.assert_awaited_once_with(
        "vios1", system_uuid="system-uuid"
    )
    hmc.delete_logical_partition.assert_awaited_once_with("vios-uuid")


def test_restore_vios_tool_forwards_system_scope(monkeypatch):
    hmc = AsyncMock()
    hmc.find_system_by_name.return_value = {"UUID": "system-uuid"}
    hmc.find_vios_by_name.return_value = {"UUID": "vios-uuid"}
    command = AsyncMock(return_value="restored")
    monkeypatch.setattr(server_vios, "client_from_env", _client_factory(hmc))
    monkeypatch.setattr(server_vios, "run_hmc_cli", command)

    assert server_vios.hmc_restore_vios(
        "system-name", "vios1", "backup", "ssp", restart_if_required=False
    ) == "restored"

    hmc.find_vios_by_name.assert_awaited_once_with(
        "vios1", system_uuid="system-uuid"
    )


def test_power_off_vios_tool_forwards_system_scope(monkeypatch):
    hmc = AsyncMock()
    operation = AsyncMock(return_value={"UUID": "job-uuid"})
    monkeypatch.setattr(server_vios, "client_from_env", _client_factory(hmc))
    monkeypatch.setattr(operations_vios, "power_vios", operation)

    server_vios.hmc_power_off_vios(
        "vios1", system_name_or_uuid="system-name"
    )

    assert operation.await_args.kwargs["system_name_or_uuid"] == "system-name"
