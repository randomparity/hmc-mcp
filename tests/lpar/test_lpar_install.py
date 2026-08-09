"""Tests for LPAR OS install tool: install_lpar_job + hmc_install_lpar_os."""

import httpx
import pytest

from conftest import JOB_ENTRY, make_config

from hmc_mcp.client import HMCClient
from hmc_mcp.jobs import install_lpar_job

BASE = "https://hmc.test:12443"


# ---------------------------------------------------------------------- #
# Unit: install_lpar_job
# ---------------------------------------------------------------------- #


def test_install_lpar_job_xml():
    xml = install_lpar_job(
        nim_ip="192.168.1.10",
        nim_gateway="192.168.1.1",
        nim_subnetmask="255.255.255.0",
        lpar_ip="192.168.1.30",
        vlan_id="100",
        timeout=90,
    )
    assert "InstallLPAR" in xml
    assert "LogicalPartition" in xml
    assert "192.168.1.10" in xml
    assert "192.168.1.1" in xml
    assert "255.255.255.0" in xml
    assert "192.168.1.30" in xml
    assert "100" in xml
    assert "90" in xml


def test_install_lpar_job_default_timeout():
    xml = install_lpar_job(
        nim_ip="10.0.0.1",
        nim_gateway="10.0.0.254",
        nim_subnetmask="255.255.255.0",
        lpar_ip="10.0.0.5",
        vlan_id="0",
    )
    assert "60" in xml  # default timeout


# ---------------------------------------------------------------------- #
# Integration: client submit_job via respx mock
# ---------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_install_lpar(mock_hmc):
    """hmc_install_lpar_os: PUT a JobRequest to the InstallLPAR do/ endpoint."""
    route = mock_hmc.put(
        "/rest/api/uom/LogicalPartition/lpar-uuid-001/do/InstallLPAR"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))

    async with HMCClient(make_config()) as hmc:
        job_xml = install_lpar_job(
            nim_ip="192.168.1.10",
            nim_gateway="192.168.1.1",
            nim_subnetmask="255.255.255.0",
            lpar_ip="192.168.1.30",
            vlan_id="100",
        )
        job = await hmc.submit_job(
            "/rest/api/uom/LogicalPartition/lpar-uuid-001/do/InstallLPAR",
            job_xml,
        )

    assert route.called
    body = route.calls.last.request.content.decode()
    assert "InstallLPAR" in body
    assert "192.168.1.10" in body
    assert job is not None
