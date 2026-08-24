"""Tests for HMC/VIOS/firmware update and upgrade tools."""

import httpx
import pytest
from pydantic import ValidationError

from conftest import JOB_ENTRY, make_config

from hmc_mcp.client import HMCClient
from hmc_mcp.errors import HMCError
from hmc_mcp.jobs import (
    IOAdapterUpdate,
    PlatformUpdateParameter,
    SRIOVAdapterUpdate,
    SystemFirmwareUpdate,
    VIOSPlatformUpdate,
    VIOSUpdateSource,
    VIOSUpgradeSource,
    build_job_request,
    job_outcome,
    platform_update_job,
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


def test_platform_update_job_uses_nested_native_json() -> None:
    parameters = PlatformUpdateParameter(
        SystemFirmwareUpdate=SystemFirmwareUpdate(
            UpdateType="NoUpdate",
            UpdateOrder=3,
            SRIOVAdapterUpdate=[
                SRIOVAdapterUpdate(AdapterID="1", SubType="adapterdriver,adapter")
            ],
        ),
        VIOSUpdate=[
            VIOSPlatformUpdate(
                UpdateType="Update",
                VIOSName="vios1",
                UpdateOrder=1,
                Name="install_img",
                ResourceType="HMC",
                IOAdapterUpdate=[
                    IOAdapterUpdate(Id="009", Device="nvme0", Repository="DISK")
                ],
            )
        ],
    )

    assert platform_update_job(parameters) == {
        "JobRequest": {
            "RequestedOperation": {
                "OperationName": "PlatformUpdate",
                "GroupName": "ManagedSystem",
            },
            "JobParameters": {
                "JobParameter": [
                    {
                        "ParameterName": "PlatformUpdateParameter",
                        "ParameterValue": {
                            "SystemFirmwareUpdate": {
                                "UpdateType": "NoUpdate",
                                "UpdateOrder": 3,
                                "SRIOVAdapterUpdate": [
                                    {
                                        "AdapterID": "1",
                                        "SubType": "adapterdriver,adapter",
                                    }
                                ],
                            },
                            "VIOSUpdate": [
                                {
                                    "UpdateType": "Update",
                                    "VIOSName": "vios1",
                                    "UpdateOrder": 1,
                                    "Name": "install_img",
                                    "ResourceType": "HMC",
                                    "IOAdapterUpdate": [
                                        {
                                            "Id": "009",
                                            "Device": "nvme0",
                                            "Repository": "DISK",
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                ]
            },
        }
    }


@pytest.mark.parametrize(
    "subtype", ["adapterdriver", "Adapter", "adapterdriver,adapter"]
)
def test_platform_update_accepts_documented_sriov_subtypes(subtype: str) -> None:
    item = SRIOVAdapterUpdate(AdapterID="1", SubType=subtype)  # type: ignore[arg-type]
    assert item.SubType == subtype


@pytest.mark.parametrize("subtype", ["adapter", "AdapterDriver", "ADAPTER"])
def test_platform_update_rejects_undocumented_sriov_subtypes(subtype: str) -> None:
    with pytest.raises(ValidationError):
        SRIOVAdapterUpdate(AdapterID="1", SubType=subtype)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "repository", ["MOUNTPOINT", "SFTP", "USB", "IBMWebsite", "DISK", "disk"]
)
def test_platform_update_accepts_documented_io_repositories(repository: str) -> None:
    item = IOAdapterUpdate(Id="1", Device="nvme0", Repository=repository)  # type: ignore[arg-type]
    assert item.Repository == repository


@pytest.mark.parametrize("resource_type", ["HMC", "NFS", "SFTP", "USB", "IBMWebsite"])
def test_platform_update_accepts_documented_vios_resources(resource_type: str) -> None:
    item = VIOSPlatformUpdate(
        UpdateType="Update",
        VIOSName="vios1",
        ResourceType=resource_type,  # type: ignore[arg-type]
    )
    assert item.ResourceType == resource_type


def test_platform_update_allows_documented_no_update_adapter_only_shape() -> None:
    parameters = PlatformUpdateParameter(
        VIOSUpdate=[
            VIOSPlatformUpdate(
                UpdateType="NoUpdate",
                VIOSName="vios1",
                IOAdapterUpdate=[
                    IOAdapterUpdate(Id="1", Device="nvme0", Repository="disk")
                ],
            )
        ]
    )
    assert parameters.VIOSUpdate is not None


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {
            "SystemFirmwareUpdate": {
                "UpdateType": "NoUpdate",
                "UpdateOrder": 1,
            }
        },
        {"VIOSUpdate": [{"UpdateType": "NoUpdate", "VIOSName": "vios1"}]},
        {
            "VIOSUpdate": [
                {
                    "UpdateType": "NoUpdate",
                    "VIOSName": "vios1",
                    "IOAdapterUpdate": [],
                }
            ]
        },
    ],
)
def test_platform_update_rejects_semantic_noop(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="at least one"):
        PlatformUpdateParameter.model_validate(kwargs)


def test_platform_update_requires_resource_for_vios_update() -> None:
    with pytest.raises(ValidationError, match="ResourceType"):
        VIOSPlatformUpdate(UpdateType="update", VIOSName="vios1")


@pytest.mark.parametrize(
    ("model", "value"),
    [
        (PlatformUpdateParameter, {"unexpected": True}),
        (
            SystemFirmwareUpdate,
            {"UpdateType": "Update", "UpdateOrder": 1, "unexpected": True},
        ),
        (SRIOVAdapterUpdate, {"AdapterID": "1", "SubType": "Adapter", "bad": 1}),
        (
            VIOSPlatformUpdate,
            {
                "UpdateType": "Update",
                "VIOSName": "vios1",
                "ResourceType": "HMC",
                "unexpected": True,
            },
        ),
        (
            IOAdapterUpdate,
            {"Id": "1", "Device": "nvme0", "Repository": "DISK", "bad": 1},
        ),
    ],
)
def test_platform_update_models_reject_unknown_keys(model, value) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        model.model_validate(value)


@pytest.mark.parametrize(
    ("model", "value"),
    [
        (SRIOVAdapterUpdate, {"AdapterID": "", "SubType": "Adapter"}),
        (SRIOVAdapterUpdate, {"AdapterID": "   ", "SubType": "Adapter"}),
        (IOAdapterUpdate, {"Id": "", "Device": "nvme0", "Repository": "disk"}),
        (IOAdapterUpdate, {"Id": "   ", "Device": "nvme0", "Repository": "disk"}),
        (IOAdapterUpdate, {"Id": "1", "Device": "", "Repository": "disk"}),
        (IOAdapterUpdate, {"Id": "1", "Device": "   ", "Repository": "disk"}),
        (
            VIOSPlatformUpdate,
            {"UpdateType": "Update", "VIOSName": "", "ResourceType": "HMC"},
        ),
        (
            VIOSPlatformUpdate,
            {"UpdateType": "Update", "VIOSName": "   ", "ResourceType": "HMC"},
        ),
        (
            VIOSPlatformUpdate,
            {
                "UpdateType": "Update",
                "VIOSName": "vios1",
                "ResourceType": "HMC",
                "Name": "",
            },
        ),
        (
            VIOSPlatformUpdate,
            {
                "UpdateType": "Update",
                "VIOSName": "vios1",
                "ResourceType": "HMC",
                "Name": "   ",
            },
        ),
    ],
)
def test_platform_update_models_reject_blank_identifiers(model, value) -> None:
    with pytest.raises(ValidationError, match="at least 1 character|pattern"):
        model.model_validate(value)


def test_platform_update_models_do_not_coerce_update_order() -> None:
    with pytest.raises(ValidationError, match="valid integer"):
        SystemFirmwareUpdate.model_validate(
            {"UpdateType": "Update", "UpdateOrder": "1"}
        )


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
    path = f"/rest/api/uom/ManagementConsole/{HMC_UUID}/do/ListManagementConsoleUpdates"
    route = mock_hmc.put(path).mock(return_value=httpx.Response(202, text=JOB_ENTRY))

    async with HMCClient(make_config()) as hmc:
        result = await hmc.submit_job(
            path, build_job_request("ListManagementConsoleUpdates", "ManagementConsole")
        )

    assert route.called
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
async def test_submit_platform_update_normalizes_documented_response(mock_hmc):
    route = mock_hmc.put(
        f"/rest/api/uom/ManagedSystem/{SYS_UUID}/do/PlatformUpdate"
    ).mock(
        return_value=httpx.Response(
            202,
            json={
                "id": "platform-job",
                "content": {
                    "JobResponse": {
                        "Status": "COMPLETED_WITH_ERROR",
                        "Result": [
                            {
                                "ParameterName": "result",
                                "ParameterValue": "firmware failed",
                            }
                        ],
                    }
                },
                "selfLink": None,
            },
        )
    )

    async with HMCClient(make_config()) as hmc:
        job = await hmc.submit_platform_update(
            SYS_UUID,
            platform_update_job(
                PlatformUpdateParameter(
                    SystemFirmwareUpdate=SystemFirmwareUpdate(
                        UpdateType="Update", UpdateOrder=1
                    )
                )
            ),
        )

    assert route.called
    assert route.calls.last.request.headers["Content-Type"] == (
        "application/vnd.ibm.powervm.web+json; type=JobRequest"
    )
    assert route.calls.last.request.headers["Accept"] == "application/json"
    assert job == {
        "UUID": "platform-job",
        "Resource": {
            "Status": "COMPLETED_WITH_ERROR",
            "Results": {
                "JobParameter": [
                    {
                        "ParameterName": "result",
                        "ParameterValue": "firmware failed",
                    }
                ]
            },
        },
    }
    assert job_outcome("platform-job", job).error == "firmware failed"


@pytest.mark.asyncio
async def test_submit_platform_update_quotes_uuid(mock_hmc):
    route = mock_hmc.put(
        "/rest/api/uom/ManagedSystem/allowed%2Fdo%2FShutdownHMC%3Fignored%3D"
        "/do/PlatformUpdate"
    ).mock(return_value=httpx.Response(204))

    async with HMCClient(make_config()) as hmc:
        result = await hmc.submit_platform_update(
            "allowed/do/ShutdownHMC?ignored=", {"JobRequest": {}}
        )

    assert route.called
    assert result is None


@pytest.mark.asyncio
async def test_submit_platform_update_sanitizes_non_success(mock_hmc):
    echoed_value = "private-image-name"
    mock_hmc.put(f"/rest/api/uom/ManagedSystem/{SYS_UUID}/do/PlatformUpdate").mock(
        return_value=httpx.Response(400, text=f"invalid {echoed_value}")
    )

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError) as raised:
            await hmc.submit_platform_update(SYS_UUID, {"sentinel": echoed_value})

    assert raised.value.status_code == 400
    assert echoed_value not in str(raised.value)
    assert raised.value.body is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ([], "root"),
        ({"id": 7, "content": {"JobResponse": {"Status": "COMPLETED"}}}, "id"),
        ({"id": "j", "content": []}, "content"),
        ({"id": "j", "content": {"JobResponse": []}}, "JobResponse"),
        ({"id": "j", "content": {"JobResponse": {"Status": []}}}, "Status"),
        (
            {
                "id": "j",
                "content": {"JobResponse": {"Status": "COMPLETED"}},
                "selfLink": 9,
            },
            "selfLink",
        ),
        (
            {
                "id": "j",
                "content": {"JobResponse": {"Status": "COMPLETED", "Result": {}}},
            },
            "Result",
        ),
        (
            {
                "id": "j",
                "content": {"JobResponse": {"Status": "COMPLETED", "Result": [3]}},
            },
            "Result",
        ),
    ],
)
async def test_submit_platform_update_rejects_malformed_success(
    mock_hmc, payload, field
):
    mock_hmc.put(f"/rest/api/uom/ManagedSystem/{SYS_UUID}/do/PlatformUpdate").mock(
        return_value=httpx.Response(202, json=payload)
    )
    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError, match=field):
            await hmc.submit_platform_update(SYS_UUID, {"JobRequest": {}})
