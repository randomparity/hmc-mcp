"""Tests for LPAR OS install tool: install_lpar_job + hmc_install_lpar_os."""

import httpx
import pytest

from conftest import JOB_ENTRY, make_config

from hmc_mcp.client import HMCClient
from hmc_mcp.jobs import install_lpar_job

BASE = "https://hmc.test"
LPAR_UUID = "11111111-1111-4111-8111-111111111111"


def _lpar_feed(name: str, uuid: str = LPAR_UUID) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><id>urn:uuid:{uuid}</id><content>
    <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <PartitionName>{name}</PartitionName>
    </LogicalPartition>
  </content></entry>
</feed>"""


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
        hmc_timeout_minutes=90,
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
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/do/InstallLPAR"
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
            f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/do/InstallLPAR",
            job_xml,
        )

    assert route.called
    body = route.calls.last.request.content.decode()
    assert "InstallLPAR" in body
    assert "192.168.1.10" in body
    assert job is not None


# ---------------------------------------------------------------------- #
# Tool-layer tests for hmc_install_lpar_os (wait=True/False)
# ---------------------------------------------------------------------- #

JOB_ENTRY_COMPLETED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:job-uuid-999</id>
  <title>Job</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <Job xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <JobID>job-uuid-999</JobID>
      <Status>COMPLETED</Status>
    </Job>
  </content>
</entry>
"""


def _hmc_env(monkeypatch) -> None:
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


def test_install_lpar_os_tool_submits_job(monkeypatch, mock_hmc):
    """hmc_install_lpar_os PUTs a JobRequest to the InstallLPAR do/ endpoint."""
    from hmc_mcp.server import hmc_install_lpar_os

    _hmc_env(monkeypatch)
    route = mock_hmc.put(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/do/InstallLPAR"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    result = hmc_install_lpar_os(
        LPAR_UUID,
        nim_ip="192.168.1.10",
        nim_gateway="192.168.1.1",
        nim_subnetmask="255.255.255.0",
        lpar_ip="192.168.1.30",
        vlan_id="100",
    )
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "InstallLPAR" in body
    assert "192.168.1.10" in body
    assert result["Resource"]["JobID"] == "job-uuid-999"


def test_install_lpar_os_tool_resolves_partition_name(monkeypatch, mock_hmc):
    from hmc_mcp.server import hmc_install_lpar_os

    _hmc_env(monkeypatch)
    search_route = mock_hmc.get(
        "/rest/api/uom/LogicalPartition/search/(PartitionName==aixprod)"
    ).mock(return_value=httpx.Response(200, text=_lpar_feed("aixprod")))
    submit_route = mock_hmc.put(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/do/InstallLPAR"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))

    hmc_install_lpar_os(
        "aixprod",
        nim_ip="192.168.1.10",
        nim_gateway="192.168.1.1",
        nim_subnetmask="255.255.255.0",
        lpar_ip="192.168.1.30",
    )

    assert search_route.called
    assert submit_route.called


def test_install_lpar_os_unknown_name_fails_before_submission(monkeypatch, mock_hmc):
    from hmc_mcp.server import hmc_install_lpar_os

    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/LogicalPartition/search/(PartitionName==missing)").mock(
        return_value=httpx.Response(
            200, text='<feed xmlns="http://www.w3.org/2005/Atom"/>'
        )
    )
    submit_route = mock_hmc.put(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/do/InstallLPAR"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))

    with pytest.raises(ValueError, match="No LPAR named 'missing'"):
        hmc_install_lpar_os(
            "missing",
            nim_ip="192.168.1.10",
            nim_gateway="192.168.1.1",
            nim_subnetmask="255.255.255.0",
            lpar_ip="192.168.1.30",
        )

    assert not submit_route.called


def test_install_lpar_os_wait_true_polls_to_completion(monkeypatch, mock_hmc):
    """hmc_install_lpar_os(wait=True) submits then polls until COMPLETED."""
    from hmc_mcp.server import hmc_install_lpar_os

    _hmc_env(monkeypatch)
    submit_route = mock_hmc.put(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/do/InstallLPAR"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    poll_route = mock_hmc.get("/rest/api/uom/Job/job-uuid-999").mock(
        return_value=httpx.Response(200, text=JOB_ENTRY_COMPLETED)
    )
    result = hmc_install_lpar_os(
        LPAR_UUID,
        nim_ip="192.168.1.10",
        nim_gateway="192.168.1.1",
        nim_subnetmask="255.255.255.0",
        lpar_ip="192.168.1.30",
        wait=True,
        wait_timeout_seconds=60,
        poll_interval=1,
    )
    assert submit_route.called
    assert poll_route.called
    assert result["Resource"]["Status"] == "COMPLETED"
