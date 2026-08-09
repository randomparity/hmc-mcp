"""Tests for HMC/VIOS/firmware update and upgrade tools."""

import httpx
import pytest

from conftest import JOB_ENTRY, make_config

from hmc_mcp.client import HMCClient
from hmc_mcp.jobs import (
    firmware_update_job,
    hmc_update_job,
    hmc_upgrade_job,
    vios_update_job,
    vios_upgrade_job,
)


# ---------------------------------------------------------------------- #
# Job builder unit tests
# ---------------------------------------------------------------------- #

REPO_NFS = {"type": "nfs", "host": "repo.example.com", "path": "/images/hmc"}


def test_hmc_update_job_xml():
    xml = hmc_update_job(REPO_NFS)
    assert "Update" in xml
    assert "ManagementConsole" in xml
    assert "nfs" in xml


def test_hmc_upgrade_job_xml():
    xml = hmc_upgrade_job(REPO_NFS)
    assert "Upgrade" in xml
    assert "ManagementConsole" in xml


def test_vios_update_job_xml():
    xml = vios_update_job(REPO_NFS)
    assert "Update" in xml
    assert "VirtualIOServer" in xml


def test_vios_upgrade_job_xml():
    xml = vios_upgrade_job(REPO_NFS)
    assert "Upgrade" in xml
    assert "VirtualIOServer" in xml


def test_firmware_update_job_xml():
    xml = firmware_update_job({"type": "disk"})
    assert "UpdateFirmware" in xml
    assert "ManagedSystem" in xml


def test_repository_params_none_values_excluded():
    """None values in the repository dict must not appear as job parameters."""
    xml = hmc_update_job({"type": "nfs", "host": None, "path": "/images"})
    assert "host" not in xml
    assert "/images" in xml


def test_repository_params_unknown_key_rejected():
    """A misspelled key must fail fast instead of reaching the HMC."""
    with pytest.raises(ValueError, match="Unknown repository key.*hst"):
        hmc_update_job({"type": "nfs", "hst": "repo.example.com", "path": "/images"})


def test_repository_params_missing_type_rejected():
    """A repository dict without 'type' must fail fast, not build a job."""
    with pytest.raises(ValueError, match="missing 'type'"):
        hmc_update_job({"host": "repo.example.com", "path": "/images"})


def test_repository_params_unknown_type_rejected():
    with pytest.raises(ValueError, match="Unknown repository type"):
        hmc_update_job({"type": "ftp", "host": "repo.example.com", "path": "/images"})


def test_repository_params_missing_required_key_rejected():
    """nfs requires host+path; a missing one must fail fast."""
    with pytest.raises(ValueError, match="requires key.*host"):
        hmc_update_job({"type": "nfs", "path": "/images"})


def test_repository_params_disk_requires_nothing():
    xml = hmc_update_job({"type": "disk"})
    assert "Update" in xml


# ---------------------------------------------------------------------- #
# Client integration tests (respx-mocked)
# ---------------------------------------------------------------------- #

HMC_UUID = "hmc-console-uuid"
VIOS_UUID = "vios-uuid-111"
SYS_UUID = "sys-uuid-222"

CONSOLE_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:{uuid}</id>
  <title>ManagementConsole</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <ManagementConsole xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <VersionInfo>V10R1M1010</VersionInfo>
    </ManagementConsole>
  </content>
</entry>
""".format(uuid=HMC_UUID)


@pytest.mark.asyncio
async def test_hmc_update_hmc(mock_hmc):
    route = mock_hmc.put(
        f"/rest/api/uom/ManagementConsole/{HMC_UUID}/do/Update"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))

    async with HMCClient(make_config()) as hmc:
        job = await hmc.submit_job(
            f"/rest/api/uom/ManagementConsole/{HMC_UUID}/do/Update",
            hmc_update_job(REPO_NFS),
        )

    assert route.called
    assert job is not None


@pytest.mark.asyncio
async def test_hmc_upgrade_hmc(mock_hmc):
    route = mock_hmc.put(
        f"/rest/api/uom/ManagementConsole/{HMC_UUID}/do/Upgrade"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))

    async with HMCClient(make_config()) as hmc:
        job = await hmc.submit_job(
            f"/rest/api/uom/ManagementConsole/{HMC_UUID}/do/Upgrade",
            hmc_upgrade_job({"type": "disk"}),
        )

    assert route.called
    assert job is not None


@pytest.mark.asyncio
async def test_hmc_get_available_hmc_ptfs(mock_hmc):
    mock_hmc.get(
        f"/rest/api/uom/ManagementConsole/{HMC_UUID}",
        params={"group": "SoftwareUpdate"},
    ).mock(return_value=httpx.Response(200, text=CONSOLE_ENTRY))

    async with HMCClient(make_config()) as hmc:
        result = await hmc.get_uom("ManagementConsole", HMC_UUID, group="SoftwareUpdate")

    assert result is not None


@pytest.mark.asyncio
async def test_hmc_update_vios(mock_hmc):
    route = mock_hmc.put(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/do/Update"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))

    async with HMCClient(make_config()) as hmc:
        job = await hmc.submit_job(
            f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/do/Update",
            vios_update_job(REPO_NFS),
        )

    assert route.called
    assert job is not None
    body = route.calls.last.request.content.decode()
    assert "Update" in body
    assert "VirtualIOServer" in body


@pytest.mark.asyncio
async def test_hmc_upgrade_vios(mock_hmc):
    route = mock_hmc.put(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/do/Upgrade"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))

    async with HMCClient(make_config()) as hmc:
        job = await hmc.submit_job(
            f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/do/Upgrade",
            vios_upgrade_job({"type": "sftp", "host": "sftp.example.com", "path": "/vios"}),
        )

    assert route.called
    assert job is not None


@pytest.mark.asyncio
async def test_hmc_update_firmware(mock_hmc):
    route = mock_hmc.put(
        f"/rest/api/uom/ManagedSystem/{SYS_UUID}/do/UpdateFirmware"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))

    async with HMCClient(make_config()) as hmc:
        job = await hmc.submit_job(
            f"/rest/api/uom/ManagedSystem/{SYS_UUID}/do/UpdateFirmware",
            firmware_update_job({"type": "disk"}),
        )

    assert route.called
    assert job is not None
    body = route.calls.last.request.content.decode()
    assert "UpdateFirmware" in body
    assert "ManagedSystem" in body
