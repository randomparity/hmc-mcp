"""Direct contract tests for the cluster client mixin."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from hmc_mcp.client.client_cluster import ClusterMixin


class ClusterHarness(ClusterMixin):
    def __init__(self) -> None:
        self.list_uom = AsyncMock(return_value=[])
        self.get_uom = AsyncMock(return_value={"UUID": "resource"})
        self.submit_job = AsyncMock(return_value={"UUID": "job"})


@pytest.mark.asyncio
async def test_cluster_reads_use_expected_uom_resources():
    client = ClusterHarness()

    assert await client.list_clusters() == []
    assert await client.get_cluster("cluster-1") == {"UUID": "resource"}
    assert await client.list_shared_storage_pools() == []
    assert await client.get_shared_storage_pool("ssp-1") == {"UUID": "resource"}

    assert client.list_uom.await_args_list[0].args == ("Cluster",)
    assert client.get_uom.await_args_list[0].args == ("Cluster", "cluster-1")
    assert client.list_uom.await_args_list[1].args == ("SharedStoragePool",)
    assert client.get_uom.await_args_list[1].args == ("SharedStoragePool", "ssp-1")


@pytest.mark.asyncio
async def test_create_logical_unit_submits_cluster_job_document():
    client = ClusterHarness()

    result = await client.create_logical_unit(
        "cluster-1",
        "data",
        50,
        "THICK",
        "VirtualIO_Disk",
        "source-udid",
    )

    assert result == {"UUID": "job"}
    path, document = client.submit_job.await_args.args
    assert path == "/rest/api/uom/Cluster/cluster-1/do/CreateLogicalUnit"
    for name, value in (
        ("LUName", "data"),
        ("LUSize", "50"),
        ("LUType", "THICK"),
        ("ClonedFrom", "source-udid"),
    ):
        assert f">{name}</ParameterName>" in document
        assert f">{value}</ParameterValue>" in document


@pytest.mark.asyncio
async def test_delete_logical_unit_submits_cluster_job_document():
    client = ClusterHarness()

    result = await client.delete_logical_unit("cluster-1", "lu-udid")

    assert result == {"UUID": "job"}
    path, document = client.submit_job.await_args.args
    assert path == "/rest/api/uom/Cluster/cluster-1/do/DeleteLogicalUnit"
    assert ">LogicalUnitUDID</ParameterName>" in document
    assert ">lu-udid</ParameterValue>" in document
