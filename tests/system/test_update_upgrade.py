"""Tests for HMC/VIOS/firmware update and upgrade tools."""

import httpx
import pytest

from conftest import JOB_ENTRY, make_config

from hmc_mcp.client import HMCClient
from hmc_mcp.jobs import (
    _REPOSITORY_TYPES,
    _REQUIRED_KEYS,
    VIOSUpdateSource,
    VIOSUpgradeSource,
    update_firmware_job,
    update_hmc_job,
    update_vios_job,
    upgrade_vios_job,
)


# ---------------------------------------------------------------------- #
# Job builder unit tests
# ---------------------------------------------------------------------- #

REPO_NFS = {"type": "nfs", "host": "repo.example.com", "path": "/images/hmc"}
VIOS_UPDATE_NFS: VIOSUpdateSource = {
    "ResourceType": "NFS",
    "ServerHostOrIP": "repo.example.com",
    "RemoteDirectory": "/images/vios",
    "RestartVIOS": "false",
}
VIOS_UPGRADE_NFS: VIOSUpgradeSource = {
    "ResourceType": "NFS",
    "ServerHostOrIP": "repo.example.com",
    "RemoteDirectory": "/images/vios",
    "Disks": "hdisk1,hdisk2",
}


def test_update_hmc_job_xml():
    xml = update_hmc_job({"MediaType": "NFS", "ServerHostOrIP": "repo.example.com"})
    assert "UpdateManagementConsole" in xml
    assert "ManagementConsole" in xml
    assert "MediaType" in xml


def test_update_hmc_job_requires_media_type():
    with pytest.raises(ValueError, match="missing required 'MediaType'"):
        update_hmc_job({"ServerHostOrIP": "repo.example.com"})


def test_update_hmc_job_rejects_unknown_parameter():
    with pytest.raises(ValueError, match="Unknown console update parameter.*type"):
        update_hmc_job({"MediaType": "NFS", "type": "nfs"})


def test_update_vios_job_xml():
    xml = update_vios_job(VIOS_UPDATE_NFS)

    assert '<OperationName kb="ROR" kxe="false">UpdateVIOS</OperationName>' in xml
    assert '<GroupName kb="ROR" kxe="false">VirtualIOServer</GroupName>' in xml
    assert '<ParameterName kb="ROR" kxe="false">ResourceType</ParameterName>' in xml
    assert '<ParameterName kb="ROR" kxe="false">RestartVIOS</ParameterName>' in xml


def test_upgrade_vios_job_xml():
    xml = upgrade_vios_job(VIOS_UPGRADE_NFS)

    assert '<OperationName kb="ROR" kxe="false">UpgradeVIOS</OperationName>' in xml
    assert '<GroupName kb="ROR" kxe="false">VirtualIOServer</GroupName>' in xml
    assert '<ParameterName kb="ROR" kxe="false">ResourceType</ParameterName>' in xml
    assert '<ParameterName kb="ROR" kxe="false">Disks</ParameterName>' in xml


def test_update_firmware_job_xml():
    xml = update_firmware_job({"type": "disk"})
    assert "UpdateFirmware" in xml
    assert "ManagedSystem" in xml


def test_vios_params_none_values_excluded():
    source: VIOSUpdateSource = {
        "ResourceType": "NFS",
        "ServerHostOrIP": "repo.example.com",
        "RemoteDirectory": "/images",
        "Name": None,  # type: ignore[typeddict-item]
    }
    xml = update_vios_job(source)

    assert '<ParameterName kb="ROR" kxe="false">Name</ParameterName>' not in xml


def test_vios_update_unknown_parameter_rejected():
    with pytest.raises(ValueError, match="Unknown UpdateVIOS parameter.*type"):
        update_vios_job({"ResourceType": "NFS", "type": "nfs"})  # type: ignore[typeddict-unknown-key]


@pytest.mark.parametrize("builder", [update_vios_job, upgrade_vios_job])
def test_vios_source_requires_resource_type(builder):
    with pytest.raises(ValueError, match="missing required 'ResourceType'"):
        builder({"Name": "image"})


def test_vios_update_rejects_upgrade_parameter():
    with pytest.raises(ValueError, match="Unknown UpdateVIOS parameter.*Disks"):
        update_vios_job({"ResourceType": "NFS", "Disks": "hdisk1"})  # type: ignore[typeddict-unknown-key]


def test_vios_upgrade_rejects_update_parameter():
    with pytest.raises(ValueError, match="Unknown UpgradeVIOS parameter.*RestartVIOS"):
        upgrade_vios_job({"ResourceType": "NFS", "RestartVIOS": "false"})  # type: ignore[typeddict-unknown-key]


def test_vios_upgrade_rejects_ibm_website():
    with pytest.raises(ValueError, match="Invalid UpgradeVIOS ResourceType"):
        upgrade_vios_job({"ResourceType": "IBMWebsite"})  # type: ignore[typeddict-item]


@pytest.mark.parametrize(
    ("builder", "source", "missing"),
    [
        (update_vios_job, {"ResourceType": "HMC"}, "Name"),
        (update_vios_job, {"ResourceType": "NFS"}, "RemoteDirectory.*ServerHostOrIP"),
        (update_vios_job, {"ResourceType": "SFTP"}, "RemoteDirectory.*ServerHostOrIP"),
        (update_vios_job, {"ResourceType": "USB"}, "USBDevice"),
        (upgrade_vios_job, {"ResourceType": "HMC", "Name": "image"}, "Disks"),
        (upgrade_vios_job, {"ResourceType": "USB", "Disks": "hdisk1"}, "USBDevice"),
    ],
)
def test_vios_source_requires_resource_specific_parameters(builder, source, missing):
    with pytest.raises(ValueError, match=missing):
        builder(source)


@pytest.mark.parametrize(
    ("builder", "source"),
    [
        (update_vios_job, {**VIOS_UPDATE_NFS, "SaveFile": "true", "Name": None}),
        (upgrade_vios_job, {**VIOS_UPGRADE_NFS, "SaveFile": "true", "Name": None}),
    ],
)
def test_vios_save_file_requires_usable_name(builder, source):

    with pytest.raises(ValueError, match="SaveFile='true'.*Name"):
        builder(source)


def test_repository_types_cover_required_key_sets():
    """Every RepositoryType must have a required-key set, and vice versa.

    Pins the annotation (RepositoryType Literal) to the enforcement map
    (_REQUIRED_KEYS) so adding a repository type is a single edit.
    """
    assert set(_REQUIRED_KEYS) == _REPOSITORY_TYPES


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
async def test_hmc_update_console_software_update(mock_hmc):
    """Client layer: documented update job reaches ManagementConsole."""
    path = f"/rest/api/uom/ManagementConsole/{HMC_UUID}/do/UpdateManagementConsole"
    route = mock_hmc.put(path).mock(return_value=httpx.Response(202, text=JOB_ENTRY))

    async with HMCClient(make_config()) as hmc:
        job = await hmc.submit_job(
            path,
            update_hmc_job({"MediaType": "NFS", "ServerHostOrIP": "repo.example.com"}),
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
        result = await hmc.get_uom(
            "ManagementConsole", HMC_UUID, group="SoftwareUpdate"
        )

    assert result is not None


@pytest.mark.asyncio
async def test_hmc_vios_update_update(mock_hmc):
    """Client layer: UpdateVIOS reaches its documented operation path."""
    path = f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/do/UpdateVIOS"
    route = mock_hmc.put(path).mock(return_value=httpx.Response(202, text=JOB_ENTRY))

    async with HMCClient(make_config()) as hmc:
        job = await hmc.submit_job(
            path,
            update_vios_job(VIOS_UPDATE_NFS),
        )

    assert route.called
    assert job is not None
    body = route.calls.last.request.content.decode()
    assert "UpdateVIOS" in body
    assert "VirtualIOServer" in body


@pytest.mark.asyncio
async def test_hmc_vios_update_upgrade(mock_hmc):
    """Client layer: UpgradeVIOS reaches its documented operation path."""
    path = f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/do/UpgradeVIOS"
    route = mock_hmc.put(path).mock(return_value=httpx.Response(202, text=JOB_ENTRY))

    async with HMCClient(make_config()) as hmc:
        job = await hmc.submit_job(
            path,
            upgrade_vios_job(VIOS_UPGRADE_NFS),
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
            update_firmware_job({"type": "disk"}),
        )

    assert route.called
    assert job is not None
    body = route.calls.last.request.content.decode()
    assert "UpdateFirmware" in body
    assert "ManagedSystem" in body
