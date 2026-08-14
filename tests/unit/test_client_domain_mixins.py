"""Direct boundary tests for LPAR, LPM, and network client mixins."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from hmc_mcp.client_lpars import LparsMixin
from hmc_mcp.client_lpm import LpmMixin
from hmc_mcp.client_network import NetworkMixin
from hmc_mcp.client_storage import StorageMixin
from hmc_mcp.client_systems import SystemsMixin
from hmc_mcp.client_resolution import MAX_PARENT_DISCOVERY_SYSTEMS
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
        self.list_managed_systems = AsyncMock(return_value=[])
        self.get_managed_system = AsyncMock(
            return_value=_entry("sys-a", "system-a", "ManagedSystem")
        )


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


class StorageHarness(StorageMixin):
    def __init__(self) -> None:
        self.config = HMCConfig(host="hmc.test", user="user", password="password")
        self._get = AsyncMock(return_value="")
        self._put = AsyncMock(return_value="")
        self._post = AsyncMock(return_value="")


class SystemsHarness(SystemsMixin):
    def __init__(self) -> None:
        self.list_uom = AsyncMock(return_value=[])
        self.get_uom = AsyncMock(return_value=None)
        self.search_uom = AsyncMock(return_value=[])
        self._get = AsyncMock(return_value="")
        self._post = AsyncMock(return_value="")
        self.submit_job = AsyncMock(return_value={"UUID": "job-1"})


def _entry(uuid: str, name: str, resource_type: str) -> dict:
    name_key = "SystemName" if resource_type == "ManagedSystem" else "PartitionName"
    return {
        "UUID": uuid,
        "ResourceType": resource_type,
        "Resource": {name_key: name},
    }


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
async def test_lpar_finder_rejects_ambiguous_names_with_parent_systems():
    client = LparsHarness()
    lpar_a = _entry("lpar-a", "shared", "LogicalPartition")
    lpar_b = _entry("lpar-b", "shared", "LogicalPartition")
    client.search_uom.return_value = [lpar_b, lpar_a]
    client.list_managed_systems.return_value = [
        _entry("sys-b", "system-b", "ManagedSystem"),
        _entry("sys-a", "system-a", "ManagedSystem"),
    ]
    client.list_logical_partitions = AsyncMock(
        side_effect=lambda uuid: [lpar_a] if uuid == "sys-a" else [lpar_b]
    )

    with pytest.raises(ValueError) as exc_info:
        await client.find_partition_by_name("shared")

    message = str(exc_info.value)
    assert message.index("system-a") < message.index("system-b")
    assert all(value in message for value in ("lpar-a", "lpar-b", "sys-a", "sys-b"))


@pytest.mark.asyncio
async def test_lpar_finder_requires_exactly_one_parent_per_candidate():
    client = LparsHarness()
    lpar_a = _entry("lpar-a", "shared", "LogicalPartition")
    lpar_b = _entry("lpar-b", "shared", "LogicalPartition")
    client.search_uom.return_value = [lpar_a, lpar_b]
    client.list_managed_systems.return_value = [
        _entry("sys-a", "system-a", "ManagedSystem"),
        _entry("sys-b", "system-b", "ManagedSystem"),
    ]
    client.list_logical_partitions = AsyncMock(return_value=[lpar_a, lpar_b])

    with pytest.raises(ValueError, match="exactly one managed system"):
        await client.find_partition_by_name("shared")


@pytest.mark.asyncio
async def test_lpar_finder_propagates_parent_discovery_failure():
    client = LparsHarness()
    client.search_uom.return_value = [
        _entry("lpar-a", "shared", "LogicalPartition"),
        _entry("lpar-b", "shared", "LogicalPartition"),
    ]
    client.list_managed_systems.side_effect = HMCError("inventory unavailable")

    with pytest.raises(HMCError, match="inventory unavailable"):
        await client.find_partition_by_name("shared")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parent",
    [
        {"Resource": {"SystemName": "system-a"}},
        {"UUID": "sys-a", "Resource": {}},
    ],
)
async def test_lpar_finder_rejects_incomplete_parent_metadata(parent):
    client = LparsHarness()
    client.search_uom.return_value = [
        _entry("lpar-a", "shared", "LogicalPartition"),
        _entry("lpar-b", "shared", "LogicalPartition"),
    ]
    client.list_managed_systems.return_value = [parent]

    with pytest.raises(ValueError, match="cannot identify managed system"):
        await client.find_partition_by_name("shared")


@pytest.mark.asyncio
@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("candidate_ids", [[None, "lpar-b"], ["lpar-a", "lpar-a"]])
async def test_lpar_finder_rejects_invalid_candidate_ids(scoped, candidate_ids):
    client = LparsHarness()
    candidates = [_entry(uuid, "shared", "LogicalPartition") for uuid in candidate_ids]
    client.search_uom.return_value = candidates
    client.list_logical_partitions = AsyncMock(return_value=candidates)

    with pytest.raises(ValueError, match="candidate UUID metadata"):
        await client.find_partition_by_name(
            "shared", system_uuid="sys-a" if scoped else None
        )


@pytest.mark.asyncio
async def test_lpar_parent_discovery_has_request_budget():
    client = LparsHarness()
    client.search_uom.return_value = [
        _entry("lpar-a", "shared", "LogicalPartition"),
        _entry("lpar-b", "shared", "LogicalPartition"),
    ]
    client.list_managed_systems.return_value = [
        _entry(f"sys-{index}", f"system-{index}", "ManagedSystem")
        for index in range(MAX_PARENT_DISCOVERY_SYSTEMS + 1)
    ]

    with pytest.raises(ValueError, match="supply managed-system scope"):
        await client.find_partition_by_name("shared")


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 2])
async def test_lpar_finder_scoped_zero_one_many(count):
    client = LparsHarness()
    matches = [
        _entry(f"lpar-{index}", "shared", "LogicalPartition") for index in range(count)
    ]
    client.list_logical_partitions = AsyncMock(
        return_value=[*matches, _entry("other", "other", "LogicalPartition")]
    )
    client.get_managed_system.return_value = _entry(
        "sys-a", "system-a", "ManagedSystem"
    )

    if count == 2:
        with pytest.raises(ValueError, match="lpar-0.*system-a.*lpar-1"):
            await client.find_partition_by_name("shared", system_uuid="sys-a")
    else:
        expected = matches[0] if matches else None
        assert (
            await client.find_partition_by_name("shared", system_uuid="sys-a")
            == expected
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("parent", [None, {"UUID": "sys-a", "Resource": {}}])
async def test_lpar_scoped_ambiguity_requires_parent_name(parent):
    client = LparsHarness()
    client.list_logical_partitions = AsyncMock(
        return_value=[
            _entry("lpar-a", "shared", "LogicalPartition"),
            _entry("lpar-b", "shared", "LogicalPartition"),
        ]
    )
    client.get_managed_system.return_value = parent

    with pytest.raises(ValueError, match="cannot identify managed system sys-a"):
        await client.find_partition_by_name("shared", system_uuid="sys-a")


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
    with pytest.raises(
        HMCError, match="GET /rest/api/templates/PartitionTemplate failed"
    ):
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


@pytest.mark.asyncio
async def test_storage_mixin_routes_schema_sensitive_operations():
    client = StorageHarness()

    assert client.get_lpar_link("lpar-1").endswith("/LogicalPartition/lpar-1")
    assert await client.list_volume_groups("vios-1") == []
    assert await client.create_virtual_disk("vios-1", "vg-1", "disk", 1024) is None

    client._get.assert_awaited_once_with(
        "/rest/api/uom/VirtualIOServer/vios-1/VolumeGroup",
        "VolumeGroup",
        include_schema_version=False,
    )
    client._post.assert_awaited_once()
    assert client._post.await_args.kwargs == {
        "resource_type": "VolumeGroup",
        "include_schema_version": False,
    }


@pytest.mark.asyncio
async def test_systems_mixin_routes_inventory_and_power_jobs():
    client = SystemsHarness()

    assert await client.list_managed_systems() == []
    assert await client.get_managed_system("system-1") is None
    assert await client.find_vios_by_name("vios-a") is None
    assert await client.power_off_system("system-1", immediate=True) == {
        "UUID": "job-1"
    }

    client.list_uom.assert_awaited_once_with("ManagedSystem")
    client.get_uom.assert_awaited_once_with("ManagedSystem", "system-1")
    client.search_uom.assert_awaited_once_with(
        "VirtualIOServer", "PartitionName", "vios-a"
    )
    assert client.submit_job.await_args.args[0].endswith("/system-1/do/PowerOff")


@pytest.mark.asyncio
async def test_system_finder_rejects_ambiguous_names():
    client = SystemsHarness()
    client.search_uom.return_value = [
        _entry("sys-b", "duplicate", "ManagedSystem"),
        _entry("sys-a", "duplicate", "ManagedSystem"),
    ]

    with pytest.raises(ValueError, match="sys-a.*sys-b"):
        await client.find_system_by_name("duplicate")


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate_ids", [[None, "sys-b"], ["sys-a", "sys-a"]])
async def test_system_finder_rejects_invalid_candidate_ids(candidate_ids):
    client = SystemsHarness()
    client.search_uom.return_value = [
        _entry(uuid, "duplicate", "ManagedSystem") for uuid in candidate_ids
    ]

    with pytest.raises(ValueError, match="candidate UUID metadata"):
        await client.find_system_by_name("duplicate")


@pytest.mark.asyncio
async def test_vios_finder_rejects_ambiguous_names_with_parent_systems():
    client = SystemsHarness()
    vios_a = _entry("vios-a", "shared", "VirtualIOServer")
    vios_b = _entry("vios-b", "shared", "VirtualIOServer")
    client.search_uom.return_value = [vios_b, vios_a]
    client.list_managed_systems = AsyncMock(
        return_value=[
            _entry("sys-a", "system-a", "ManagedSystem"),
            _entry("sys-b", "system-b", "ManagedSystem"),
        ]
    )
    client.list_vios = AsyncMock(
        side_effect=lambda uuid: [vios_a] if uuid == "sys-a" else [vios_b]
    )

    with pytest.raises(ValueError) as exc_info:
        await client.find_vios_by_name("shared")

    message = str(exc_info.value)
    assert message.index("system-a") < message.index("system-b")
    assert all(value in message for value in ("vios-a", "vios-b", "sys-a", "sys-b"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parent",
    [
        {"Resource": {"SystemName": "system-a"}},
        {"UUID": "sys-a", "Resource": {}},
    ],
)
async def test_vios_finder_rejects_incomplete_parent_metadata(parent):
    client = SystemsHarness()
    client.search_uom.return_value = [
        _entry("vios-a", "shared", "VirtualIOServer"),
        _entry("vios-b", "shared", "VirtualIOServer"),
    ]
    client.list_managed_systems = AsyncMock(return_value=[parent])

    with pytest.raises(ValueError, match="cannot identify managed system"):
        await client.find_vios_by_name("shared")


@pytest.mark.asyncio
@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("candidate_ids", [[None, "vios-b"], ["vios-a", "vios-a"]])
async def test_vios_finder_rejects_invalid_candidate_ids(scoped, candidate_ids):
    client = SystemsHarness()
    candidates = [_entry(uuid, "shared", "VirtualIOServer") for uuid in candidate_ids]
    client.search_uom.return_value = candidates
    client.list_vios = AsyncMock(return_value=candidates)

    with pytest.raises(ValueError, match="candidate UUID metadata"):
        await client.find_vios_by_name(
            "shared", system_uuid="sys-a" if scoped else None
        )


@pytest.mark.asyncio
async def test_vios_parent_discovery_has_request_budget():
    client = SystemsHarness()
    client.search_uom.return_value = [
        _entry("vios-a", "shared", "VirtualIOServer"),
        _entry("vios-b", "shared", "VirtualIOServer"),
    ]
    client.list_managed_systems = AsyncMock(
        return_value=[
            _entry(f"sys-{index}", f"system-{index}", "ManagedSystem")
            for index in range(MAX_PARENT_DISCOVERY_SYSTEMS + 1)
        ]
    )

    with pytest.raises(ValueError, match="supply managed-system scope"):
        await client.find_vios_by_name("shared")


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 2])
async def test_vios_finder_scoped_zero_one_many(count):
    client = SystemsHarness()
    matches = [
        _entry(f"vios-{index}", "shared", "VirtualIOServer") for index in range(count)
    ]
    client.list_vios = AsyncMock(
        return_value=[*matches, _entry("other", "other", "VirtualIOServer")]
    )
    client.get_managed_system = AsyncMock(
        return_value=_entry("sys-a", "system-a", "ManagedSystem")
    )

    if count == 2:
        with pytest.raises(ValueError, match="vios-0.*system-a.*vios-1"):
            await client.find_vios_by_name("shared", system_uuid="sys-a")
    else:
        expected = matches[0] if matches else None
        assert await client.find_vios_by_name("shared", system_uuid="sys-a") == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("parent", [None, {"UUID": "sys-a", "Resource": {}}])
async def test_vios_scoped_ambiguity_requires_parent_name(parent):
    client = SystemsHarness()
    client.list_vios = AsyncMock(
        return_value=[
            _entry("vios-a", "shared", "VirtualIOServer"),
            _entry("vios-b", "shared", "VirtualIOServer"),
        ]
    )
    client.get_managed_system = AsyncMock(return_value=parent)

    with pytest.raises(ValueError, match="cannot identify managed system sys-a"):
        await client.find_vios_by_name("shared", system_uuid="sys-a")
