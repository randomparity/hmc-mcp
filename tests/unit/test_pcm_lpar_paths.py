"""Logical-partition PCM path and unsupported-endpoint contracts."""

import inspect
from unittest.mock import AsyncMock

import httpx
import pytest

from hmc_mcp.client import HMCClient
from hmc_mcp.operations_pcm import (
    get_pcm_preferences,
    resolve_pcm_resource,
    set_pcm_preferences,
)
from hmc_mcp.server import (
    hmc_aggregated_metric_links,
    hmc_aggregated_metrics,
    hmc_processed_metric_links,
    hmc_processed_metrics,
)

from conftest import make_config

SYSTEM_UUID = "00000000-0000-0000-0000-000000000001"
LPAR_UUID = "00000000-0000-0000-0000-000000000002"
FEED = """<feed xmlns="http://www.w3.org/2005/Atom"></feed>"""


@pytest.mark.asyncio
async def test_processed_lpar_path_is_nested_under_owning_system(mock_hmc):
    route = mock_hmc.get(
        f"/rest/api/pcm/ManagedSystem/{SYSTEM_UUID}/"
        f"LogicalPartition/{LPAR_UUID}/ProcessedMetrics"
    ).mock(return_value=httpx.Response(200, text=FEED))

    async with HMCClient(make_config()) as hmc:
        assert await hmc.get_processed_metric_links(
            "LogicalPartition",
            LPAR_UUID,
            "2026-08-07T11:00:00Z",
            system_uuid=SYSTEM_UUID,
        ) == []

    assert route.called


@pytest.mark.asyncio
async def test_resolve_pcm_lpar_scopes_name_to_owning_system():
    hmc = AsyncMock()
    hmc.find_system_by_name.return_value = {"UUID": SYSTEM_UUID}
    hmc.find_partition_by_name.return_value = {"UUID": LPAR_UUID}

    target = await resolve_pcm_resource(
        hmc, "LogicalPartition", "aix1", system_name_or_uuid="server1"
    )

    assert target.resource_uuid == LPAR_UUID
    assert target.system_uuid == SYSTEM_UUID
    hmc.find_partition_by_name.assert_awaited_once_with(
        "aix1", system_uuid=SYSTEM_UUID
    )


@pytest.mark.asyncio
async def test_lpar_metrics_require_owning_system():
    with pytest.raises(ValueError, match="system_name_or_uuid"):
        await resolve_pcm_resource(AsyncMock(), "LogicalPartition", LPAR_UUID)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [get_pcm_preferences, set_pcm_preferences])
async def test_lpar_preferences_are_rejected_before_client_call(operation):
    hmc = AsyncMock()
    args = (hmc, "LogicalPartition", LPAR_UUID)
    if operation is set_pcm_preferences:
        args += ({"LongTermMonitorEnabled": True},)

    with pytest.raises(ValueError, match="ManagedSystem"):
        await operation(*args)

    hmc.get_pcm_preferences.assert_not_awaited()
    hmc.set_pcm_preferences.assert_not_awaited()


@pytest.mark.asyncio
async def test_ltm_rejects_lpar_before_request():
    hmc = AsyncMock()

    with pytest.raises(ValueError, match="ManagedSystem"):
        await HMCClient.get_ltm_metric_links(
            hmc, "LogicalPartition", LPAR_UUID, "2026-08-07T11:00:00Z"
        )

    hmc._metrics_links.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get_pcm_preferences", "set_pcm_preferences"])
async def test_direct_client_rejects_lpar_preferences_before_request(method):
    hmc = AsyncMock()
    args = ("LogicalPartition", LPAR_UUID)
    kwargs = {"LongTermMonitorEnabled": True} if method.startswith("set") else {}

    with pytest.raises(ValueError, match="ManagedSystem"):
        await getattr(HMCClient, method)(hmc, *args, **kwargs)

    hmc._get.assert_not_awaited()
    hmc._post_pcm.assert_not_awaited()


@pytest.mark.parametrize(
    "tool",
    [
        hmc_processed_metrics,
        hmc_processed_metric_links,
        hmc_aggregated_metrics,
        hmc_aggregated_metric_links,
    ],
)
def test_metric_tools_preserve_positional_profile_slot(tool):
    parameters = list(inspect.signature(tool).parameters)

    assert parameters.index("profile") < parameters.index("system_name_or_uuid")
