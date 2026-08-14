"""Direct boundary tests for LPAR, LPM, and network client mixins."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from hmc_mcp.client_lpars import LparsMixin
from hmc_mcp.client_lpm import LpmMixin
from hmc_mcp.client_network import NetworkMixin
from hmc_mcp.client_templates import TemplatesMixin
from hmc_mcp.config import HMCConfig
from hmc_mcp.errors import HMCError


class LparsHarness(LparsMixin):
    def __init__(self) -> None:
        self._get = AsyncMock(return_value="")
        self._put = AsyncMock(return_value="")
        self._post = AsyncMock(return_value="")
        self._delete = AsyncMock(return_value=None)
        self.list_uom = AsyncMock(return_value=[])
        self.get_uom = AsyncMock(return_value=None)
        self.search_uom = AsyncMock(return_value=[])


class LpmHarness(LpmMixin):
    def __init__(self) -> None:
        self.submit_job = AsyncMock(return_value={"UUID": "job"})


class NetworkHarness(NetworkMixin):
    def __init__(self) -> None:
        self.config = HMCConfig(host="hmc.test", user="user", password="password")
        self._get = AsyncMock(return_value="")
        self._put = AsyncMock(return_value="")
        self._delete = AsyncMock(return_value=None)


class TemplatesHarness(TemplatesMixin):
    def __init__(self, response: httpx.Response) -> None:
        self._request = AsyncMock(return_value=response)
        self.submit_job = AsyncMock(return_value={"UUID": "job-1"})
        self._session_token = "session-token"


@pytest.mark.asyncio
async def test_lpar_mixin_routes_scoped_and_global_reads():
    client = LparsHarness()

    assert await client.list_logical_partitions("system-1") == []
    assert await client.list_logical_partitions() == []
    assert await client.get_logical_partition("lpar-1") is None
    assert await client.find_partition_by_name("aix1") is None

    client._get.assert_awaited_once_with(
        "/rest/api/uom/ManagedSystem/system-1/LogicalPartition",
        "LogicalPartition",
    )
    client.list_uom.assert_awaited_once_with("LogicalPartition")
    client.get_uom.assert_awaited_once_with("LogicalPartition", "lpar-1")
    client.search_uom.assert_awaited_once_with(
        "LogicalPartition", "PartitionName", "aix1"
    )


@pytest.mark.asyncio
async def test_lpar_mixin_writes_use_schema_compatible_paths():
    client = LparsHarness()

    assert await client.create_logical_partition("system-1", "<lpar/>") is None
    assert await client.modify_logical_partition("lpar-1", "<update/>") is None
    await client.delete_logical_partition("lpar-1")

    client._put.assert_awaited_once_with(
        "/rest/api/uom/ManagedSystem/system-1/LogicalPartition",
        "<lpar/>",
        resource_type="LogicalPartition",
        include_schema_version=False,
    )
    client._post.assert_awaited_once_with(
        "/rest/api/uom/LogicalPartition/lpar-1",
        "<update/>",
        resource_type="LogicalPartition",
    )
    client._delete.assert_awaited_once_with("/rest/api/uom/LogicalPartition/lpar-1")


@pytest.mark.asyncio
async def test_lpm_mixin_submits_each_operation_to_lpar_endpoint():
    client = LpmHarness()

    await client.lpar_migrate("lpar-1", "target-system")
    await client.lpar_migrate_validate("lpar-1", "target-system")
    await client.lpar_migrate_abort("lpar-1")
    await client.lpar_migrate_recover("lpar-1")
    await client.lpar_remote_restart("lpar-1", "target-system")

    operations = [
        call.args[0].rsplit("/", 1)[-1] for call in client.submit_job.await_args_list
    ]
    assert operations == [
        "Migrate",
        "MigrateValidate",
        "MigrateAbort",
        "MigrateRecover",
        "RemoteRestart",
    ]
    assert all(
        "target-system" in call.args[1]
        for call in client.submit_job.await_args_list[:2]
    )


@pytest.mark.asyncio
async def test_network_mixin_routes_empty_feeds_and_delete():
    client = NetworkHarness()

    assert await client.list_virtual_switches("system-1") == []
    assert await client.list_virtual_networks("system-1") == []
    assert await client.list_network_bridges("system-1") == []
    assert await client.create_virtual_network("system-1", "net", 100, 0) is None
    await client.delete_virtual_network("system-1", "network-1")

    assert [call.args[1] for call in client._get.await_args_list] == [
        "VirtualSwitch",
        "VirtualNetwork",
        "NetworkBridge",
    ]
    client._put.assert_awaited_once()
    assert client._put.await_args.kwargs == {
        "resource_type": "VirtualNetwork",
        "include_schema_version": False,
    }
    client._delete.assert_awaited_once_with(
        "/rest/api/uom/ManagedSystem/system-1/VirtualNetwork/network-1"
    )


@pytest.mark.asyncio
async def test_templates_mixin_handles_http_response_contracts():
    client = TemplatesHarness(httpx.Response(204))

    assert await client.list_partition_templates() == []
    client._request.assert_awaited_once_with(
        "GET",
        "/rest/api/templates/PartitionTemplate",
        headers={
            "Accept": f"{client.TEMPLATES_MEDIA}; type=PartitionTemplate",
        },
    )

    client._request.return_value = httpx.Response(503, text="unavailable")
    with pytest.raises(HMCError, match="GET /rest/api/templates/PartitionTemplate failed"):
        await client.list_partition_templates()


@pytest.mark.asyncio
async def test_templates_mixin_routes_deployment_job():
    client = TemplatesHarness(httpx.Response(200))

    assert await client.deploy_partition_template("draft-1", "system-1") == {
        "UUID": "job-1"
    }
    path, document = client.submit_job.await_args.args
    assert path == "/rest/api/templates/PartitionTemplate/draft-1/do/deploy"
    assert "system-1" in document
    assert "session-token" in document
