"""Tests for managed-system and VIOS power jobs."""

import httpx
import pytest

from conftest import JOB_ENTRY, make_config

from hmc_mcp.client import HMCClient
from hmc_mcp.jobs import (
    power_off_system_job,
    power_off_vios_job,
    power_on_system_job,
    power_on_vios_job,
)


def test_system_power_jobs():
    assert "PowerOn" in power_on_system_job() and "ManagedSystem" in power_on_system_job()
    assert "PowerOff" in power_off_system_job() and "ManagedSystem" in power_off_system_job()


def test_vios_power_jobs():
    on = power_on_vios_job()
    off = power_off_vios_job()
    assert "PowerOn" in on and "VirtualIOServer" in on
    assert "PowerOff" in off and "VirtualIOServer" in off


@pytest.mark.asyncio
async def test_power_on_system(mock_hmc):
    route = mock_hmc.put("/rest/api/uom/ManagedSystem/sys-uuid/do/PowerOn").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    async with HMCClient(make_config()) as hmc:
        job = await hmc.power_on_system("sys-uuid")
    assert route.called
    assert job is not None


@pytest.mark.asyncio
async def test_power_off_system(mock_hmc):
    route = mock_hmc.put("/rest/api/uom/ManagedSystem/sys-uuid/do/PowerOff").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    async with HMCClient(make_config()) as hmc:
        await hmc.power_off_system("sys-uuid", immediate=True)
    body = route.calls.last.request.content.decode()
    assert "PowerOff" in body and "immediate" in body


@pytest.mark.asyncio
async def test_power_on_vios(mock_hmc):
    route = mock_hmc.put("/rest/api/uom/VirtualIOServer/vios-uuid/do/PowerOn").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    async with HMCClient(make_config()) as hmc:
        await hmc.power_on_vios("vios-uuid")
    assert route.called


@pytest.mark.asyncio
async def test_power_off_vios(mock_hmc):
    route = mock_hmc.put("/rest/api/uom/VirtualIOServer/vios-uuid/do/PowerOff").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    async with HMCClient(make_config()) as hmc:
        await hmc.power_off_vios("vios-uuid")
    assert route.called
