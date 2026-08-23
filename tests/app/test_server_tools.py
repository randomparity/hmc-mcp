"""Tool-layer tests for REST-backed and SSH-backed MCP tools.

The client layer (``HMCClient`` + respx) is covered in the domain test dirs;
these tests call the actual ``@mcp.tool`` functions so the argument-mapping
and XML-building logic in the tool bodies is exercised — the layer the
client tests skip.  REST tools run against the respx ``mock_hmc`` router
with env-configured credentials; ``hmc_run_command`` patches the SSH
boundary (``asyncssh.connect``) like the vNIC tests do.
"""

from __future__ import annotations

from dataclasses import asdict
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import httpx
import pytest

from hmc_mcp.errors import HMCError
from hmc_mcp.documents import LparResources
from hmc_mcp.server import (
    hmc_create_lpar,
    hmc_delete_lpar,
    hmc_get_lpar,
    hmc_get_job,
    hmc_get_available_hmc_ptfs,
    hmc_update_console_software,
    hmc_modify_lpar,
    hmc_rename_lpar,
    hmc_power_off_lpar,
    hmc_power_on_lpar,
    hmc_list_recent_jobs,
    hmc_run_command,
    hmc_update_firmware,
    hmc_vios_update,
    hmc_wait_for_job,
)

from conftest import JOB_ENTRY

SYSTEM_UUID = "00000000-0000-0000-0000-000000000001"
LPAR_UUID = "00000000-0000-0000-0000-000000000002"
VIOS_UUID = "00000000-0000-0000-0000-000000000003"
MC_UUID = "mc-uuid-0001"
CONSOLE_SOURCE = {
    "MediaType": "NFS",
    "ServerHostOrIP": "repo.example.com",
    "Directory": "/images/hmc",
    "RestartConsole": "False",
}
VIOS_UPDATE_SOURCE = {
    "ResourceType": "NFS",
    "ServerHostOrIP": "repo.example.com",
    "RemoteDirectory": "/images/vios",
}
VIOS_UPGRADE_SOURCE = {
    "ResourceType": "NFS",
    "ServerHostOrIP": "repo.example.com",
    "RemoteDirectory": "/images/vios",
    "Disks": "hdisk1",
}

# A single-LPAR Atom feed; {name} is the partition name.
LPAR_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:00000000-0000-0000-0000-000000000002</id>
    <title>LogicalPartition:{name}</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <PartitionName>{name}</PartitionName>
        <PartitionState>running</PartitionState>
      </LogicalPartition>
    </content>
  </entry>
</feed>
"""

EMPTY_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom"/>
"""

REPO = {"type": "nfs", "host": "repo.example.com", "path": "/images/hmc"}


def _hmc_env(monkeypatch) -> None:
    """Set env vars so HMCConfig() succeeds inside the tool."""
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


def _make_ssh_mock(stdout: str = "") -> MagicMock:
    """Return a minimal asyncssh connection mock."""
    result = MagicMock()
    result.stdout = stdout

    conn = AsyncMock()
    conn.run = AsyncMock(return_value=result)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    return conn


# ---------------------------------------------------------------------- #
# hmc_run_command (SSH passthrough)
# ---------------------------------------------------------------------- #


def test_run_command_passes_cmd_through(monkeypatch):
    """hmc_run_command runs the exact command over SSH and returns stdout."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("lpar1  running\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_run_command("lssyscfg -r lpar -m server1")

    called_cmd = conn_mock.run.call_args[0][0]
    assert called_cmd == "lssyscfg -r lpar -m server1"
    assert result == "lpar1  running\n"


# ---------------------------------------------------------------------- #
# hmc_get_job / hmc_list_lpars name-lookup (REST reads)
# ---------------------------------------------------------------------- #


def test_get_job_parses_entry(monkeypatch, mock_hmc):
    """hmc_get_job returns the parsed job resource dict."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/Job/job-uuid-999").mock(
        return_value=httpx.Response(200, text=JOB_ENTRY)
    )
    result = hmc_get_job("job-uuid-999")
    assert result["Resource"]["JobID"] == "job-uuid-999"
    assert result["Resource"]["Status"] == "RUNNING"


def test_get_job_empty_returns_none(monkeypatch, mock_hmc):
    """hmc_get_job returns None when the server returns no content."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/Job/job-uuid-999").mock(
        return_value=httpx.Response(204)
    )
    assert hmc_get_job("job-uuid-999") is None


def test_lpars_by_name(monkeypatch, mock_hmc):
    """hmc_list_lpars resolves a non-UUID selector as a PartitionName."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/LogicalPartition/search/(PartitionName==aixprod)").mock(
        return_value=httpx.Response(200, text=LPAR_FEED.format(name="aixprod"))
    )
    result = hmc_get_lpar("aixprod")
    assert result["UUID"] == LPAR_UUID
    assert result["Resource"]["PartitionName"] == "aixprod"


def test_lpars_name_not_found_returns_none(monkeypatch, mock_hmc):
    """hmc_list_lpars returns None when a partition name matches nothing."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/LogicalPartition/search/(PartitionName==ghost)").mock(
        return_value=httpx.Response(200, text=EMPTY_FEED)
    )
    assert hmc_get_lpar("ghost") is None


# ---------------------------------------------------------------------- #
# hmc_power_on_lpar / hmc_power_off_lpar (job submission)
# ---------------------------------------------------------------------- #


def test_power_on_lpar_submits_job(monkeypatch, mock_hmc):
    """hmc_power_on_lpar PUTs a PowerOn job to the do/ path."""
    _hmc_env(monkeypatch)
    # Mock the precondition state check (not activated → proceed with PowerOn).
    mock_hmc.get(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/quick/PartitionState"
    ).mock(return_value=httpx.Response(200, text="not activated"))
    route = mock_hmc.put(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/do/PowerOn").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    result = hmc_power_on_lpar(LPAR_UUID)
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "PowerOn</OperationName>" in body
    assert result.already_running is False
    assert result.job["Resource"]["JobID"] == "job-uuid-999"
    assert result.message is None


def test_power_off_lpar_submits_job(monkeypatch, mock_hmc):
    """hmc_power_off_lpar PUTs a PowerOff job with the immediate flag."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/do/PowerOff"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    hmc_power_off_lpar(LPAR_UUID, immediate=True)
    body = route.calls.last.request.content.decode()
    assert "PowerOff</OperationName>" in body
    assert '<ParameterName kb="ROR" kxe="false">immediate</ParameterName>' in body
    assert '<ParameterValue kb="CUR" kxe="false">true</ParameterValue>' in body


# ---------------------------------------------------------------------- #
# hmc_create_lpar / hmc_modify_lpar (argument mapping -> XML)
# ---------------------------------------------------------------------- #


def test_create_lpar_builds_xml(monkeypatch, mock_hmc):
    """hmc_create_lpar maps its arguments into the LogicalPartition document."""
    _hmc_env(monkeypatch)
    # Mock name-collision check (no existing LPAR with this name).
    mock_hmc.get("/rest/api/uom/LogicalPartition/search/(PartitionName==newlpar)").mock(
        return_value=httpx.Response(200, text=EMPTY_FEED)
    )
    route = mock_hmc.put(
        f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition"
    ).mock(return_value=httpx.Response(201, text=LPAR_FEED.format(name="newlpar")))
    # system name resolution for stamp (REST-first: get_managed_system)
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(
            200,
            text=f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:{SYSTEM_UUID}</id>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <SystemName>server1</SystemName>
    </ManagedSystem>
  </content>
</entry>""",
        )
    )
    # stamp_lpar_ownership calls set_lpar_description over SSH;
    # patch stamp to avoid needing a live SSH server in this XML-building test.
    with patch(
        "hmc_mcp.operations_lpar.stamp_lpar_ownership",
        new=AsyncMock(return_value="[hmc-mcp owner:hmc-mcp created:2026-01-01]"),
    ):
        result = hmc_create_lpar(
            system_name_or_uuid=SYSTEM_UUID,
            name="newlpar",
            resources=LparResources(
                min_memory=512,
                desired_memory=2048,
                max_memory=4096,
                desired_procs=0.5,
                desired_vcpus=2,
            ),
        )
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "newlpar</PartitionName>" in body
    assert '<DesiredMemory kb="CUD" kxe="false">2048</DesiredMemory>' in body
    assert '<MinimumMemory kb="CUD" kxe="false">512</MinimumMemory>' in body
    assert (
        '<DesiredProcessingUnits kb="CUD" kxe="false">0.5</DesiredProcessingUnits>'
        in body
    )
    assert (
        '<DesiredVirtualProcessors kb="CUD" kxe="false">2</DesiredVirtualProcessors>'
        in body
    )
    # result is now wrapped: {"lpar": <entry>, "ownership_stamped": ..., "warnings": []}
    assert result.lpar["Resource"]["PartitionName"] == "newlpar"
    assert result.ownership_stamped is True
    assert result.warnings == ()


def test_create_lpar_dedicated_uses_whole_cpus(monkeypatch, mock_hmc):
    """dedicated=True emits DedicatedProcessorConfiguration with int CPUs."""
    _hmc_env(monkeypatch)
    # Mock name-collision check (no existing LPAR with this name).
    mock_hmc.get("/rest/api/uom/LogicalPartition/search/(PartitionName==ded)").mock(
        return_value=httpx.Response(200, text=EMPTY_FEED)
    )
    route = mock_hmc.put(
        f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition"
    ).mock(return_value=httpx.Response(201, text=LPAR_FEED.format(name="ded")))
    with (
        patch(
            "hmc_mcp.operations_lpar.stamp_lpar_ownership",
            new=AsyncMock(return_value="tok"),
        ),
        patch(
            "hmc_mcp.operations_lpar._system_name",
            new=AsyncMock(return_value="sys1"),
        ),
    ):
        hmc_create_lpar(
            system_name_or_uuid=SYSTEM_UUID,
            name="ded",
            resources=LparResources(dedicated=True, desired_procs=2.0, max_procs=4.0),
        )
    body = route.calls.last.request.content.decode()
    assert "<DedicatedProcessorConfiguration" in body
    assert '<DesiredProcessors kb="CUD" kxe="false">2</DesiredProcessors>' in body
    assert (
        '<HasDedicatedProcessors kb="CUD" kxe="false">true</HasDedicatedProcessors>'
        in body
    )


def test_modify_lpar_builds_resource_xml(monkeypatch, mock_hmc):
    """hmc_modify_lpar emits only the fields passed; unchanged fields are omitted."""
    _hmc_env(monkeypatch)
    route = mock_hmc.post(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(200, text=LPAR_FEED.format(name="owned-lpar"))
    )
    result = hmc_modify_lpar(
        LPAR_UUID,
        resources=LparResources(desired_memory=8192),
    )
    body = route.calls.last.request.content.decode()
    assert "PartitionName" not in body
    assert '<DesiredMemory kb="CUD" kxe="false">8192</DesiredMemory>' in body
    # A modify document carries only what changed.
    assert "MaximumMemory" not in body
    assert "ProcessingUnits" not in body
    assert result["Resource"]["PartitionName"] == "owned-lpar"


def test_rename_lpar_authorizes_and_writes_name(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    route = mock_hmc.post(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(200, text=LPAR_FEED.format(name="renamed"))
    )
    guard = AsyncMock()
    with (
        patch(
            "hmc_mcp.operations_lpar.resolve_lpar_ownership_names",
            new=AsyncMock(return_value=("system-1", "owned-lpar")),
        ),
        patch("hmc_mcp.operations_lpar.authorize_lpar_mutation", new=guard),
    ):
        result = hmc_rename_lpar(
            SYSTEM_UUID,
            LPAR_UUID,
            "renamed",
            ownership_override=True,
        )

    body = route.calls.last.request.content.decode()
    assert "renamed</PartitionName>" in body
    guard.assert_awaited_once_with(
        ANY, "system-1", "owned-lpar", ownership_override=True
    )
    assert result["Resource"]["PartitionName"] == "renamed"


def test_foreign_owned_rename_issues_no_write(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    mock_hmc.get(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(200, text=LPAR_FEED.format(name="owned-lpar"))
    )
    write = mock_hmc.post(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}")
    with (
        patch(
            "hmc_mcp.operations_lpar.resolve_lpar_ownership_names",
            new=AsyncMock(return_value=("system-1", "owned-lpar")),
        ),
        patch(
            "hmc_mcp.operations_lpar.authorize_lpar_mutation",
            new=AsyncMock(side_effect=PermissionError("foreign owner")),
        ),
        pytest.raises(PermissionError, match="foreign owner"),
    ):
        hmc_rename_lpar(SYSTEM_UUID, LPAR_UUID, "renamed")
    assert not write.called


def test_foreign_owned_delete_issues_no_write(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    mock_hmc.get(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(200, text=LPAR_FEED.format(name="owned-lpar"))
    )
    write = mock_hmc.delete(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}")
    with (
        patch(
            "hmc_mcp.operations_lpar.resolve_lpar_ownership_names",
            new=AsyncMock(return_value=("system-1", "owned-lpar")),
        ),
        patch(
            "hmc_mcp.operations_lpar.authorize_lpar_mutation",
            new=AsyncMock(side_effect=PermissionError("foreign owner")),
        ),
        pytest.raises(PermissionError, match="foreign owner"),
    ):
        hmc_delete_lpar(SYSTEM_UUID, LPAR_UUID)
    assert not write.called


# ---------------------------------------------------------------------- #
# Update / upgrade tools (job submission)
# ---------------------------------------------------------------------- #


def test_hmc_update_kind_update(monkeypatch, mock_hmc):
    """Console updates use IBM's documented operation and parameters."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(
        f"/rest/api/uom/ManagementConsole/{MC_UUID}/do/UpdateManagementConsole"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    hmc_update_console_software(MC_UUID, CONSOLE_SOURCE, kind="update")
    body = route.calls.last.request.content.decode()
    assert "UpdateManagementConsole</OperationName>" in body
    assert "MediaType</ParameterName>" in body
    assert "ServerHostOrIP</ParameterName>" in body
    assert "repo.example.com" in body
    assert "/images/hmc" in body


def test_hmc_update_kind_upgrade(monkeypatch, mock_hmc):
    """A nonexistent single-job console upgrade is refused before I/O."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(f"/rest/api/uom/ManagementConsole/{MC_UUID}/do/Upgrade").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    with pytest.raises(ValueError, match="SaveUpgradeData"):
        hmc_update_console_software(MC_UUID, CONSOLE_SOURCE, kind="upgrade")
    assert not route.called


def test_hmc_update_default_kind_is_update(monkeypatch, mock_hmc):
    """hmc_update_console_software defaults to kind='update' when kind is omitted."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(
        f"/rest/api/uom/ManagementConsole/{MC_UUID}/do/UpdateManagementConsole"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    hmc_update_console_software(MC_UUID, CONSOLE_SOURCE)
    assert route.calls.last.request.url.path.endswith("/do/UpdateManagementConsole")


def test_hmc_update_encodes_console_uuid_as_one_path_segment(monkeypatch, mock_hmc):
    """A selector cannot redirect the authorized update to another operation."""
    _hmc_env(monkeypatch)
    hostile_uuid = "allowed/do/ShutdownHMC?ignored="
    route = mock_hmc.put(
        "/rest/api/uom/ManagementConsole/allowed%2Fdo%2FShutdownHMC%3Fignored%3D"
        "/do/UpdateManagementConsole"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))

    hmc_update_console_software(hostile_uuid, CONSOLE_SOURCE)

    assert route.called


def test_vios_update_kind_update(monkeypatch, mock_hmc):
    """The update kind submits the documented UpdateVIOS job."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/do/UpdateVIOS"
    ).mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    hmc_vios_update(VIOS_UUID, VIOS_UPDATE_SOURCE, kind="update")
    body = route.calls.last.request.content.decode()
    assert "UpdateVIOS</OperationName>" in body
    assert "repo.example.com" in body


def test_vios_update_kind_upgrade(monkeypatch, mock_hmc):
    """The upgrade kind submits the documented UpgradeVIOS job."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/do/UpgradeVIOS"
    ).mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    hmc_vios_update(VIOS_UUID, VIOS_UPGRADE_SOURCE, kind="upgrade")
    body = route.calls.last.request.content.decode()
    assert "UpgradeVIOS</OperationName>" in body
    assert "repo.example.com" in body


def test_vios_update_default_kind_is_update(monkeypatch, mock_hmc):
    """hmc_vios_update defaults to kind='update' when kind is omitted."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/do/UpdateVIOS"
    ).mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    hmc_vios_update(VIOS_UUID, VIOS_UPDATE_SOURCE)
    assert route.calls.last.request.url.path.endswith("/do/UpdateVIOS")


def test_vios_update_encodes_uuid_as_one_path_segment(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    hostile_uuid = "allowed/do/Shutdown?ignored="
    monkeypatch.setattr(
        "hmc_mcp.server_updates.resolve_vios_uuid",
        AsyncMock(return_value=hostile_uuid),
    )
    route = mock_hmc.put(
        "/rest/api/uom/VirtualIOServer/allowed%2Fdo%2FShutdown%3Fignored%3D"
        "/do/UpdateVIOS"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))

    hmc_vios_update(hostile_uuid, VIOS_UPDATE_SOURCE)

    assert route.called


@pytest.mark.parametrize(
    ("source", "kind", "message"),
    [
        ({"Name": "image"}, "update", "ResourceType"),
        ({"ResourceType": "NFS", "unknown": "x"}, "update", "unknown"),
        ({"ResourceType": "NFS", "Disks": "hdisk1"}, "update", "Disks"),
        ({"ResourceType": "NFS", "RestartVIOS": "false"}, "upgrade", "RestartVIOS"),
        ({"ResourceType": "IBMWebsite"}, "upgrade", "IBMWebsite"),
        ({"ResourceType": "NFS"}, "update", "RemoteDirectory"),
        ({"ResourceType": "HMC", "Name": "image"}, "upgrade", "Disks"),
    ],
)
def test_vios_invalid_source_fails_before_client(monkeypatch, source, kind, message):
    def fail_client(_profile):
        raise AssertionError("client created")

    monkeypatch.setattr("hmc_mcp.server_updates.client_from_env", fail_client)

    with pytest.raises(ValueError, match=message):
        hmc_vios_update(VIOS_UUID, source, kind=kind)


def _vios_job_with_stdout(status="COMPLETED", top_level=None):
    job = {
        "Resource": {
            "Status": status,
            "Results": {
                "JobParameter": {
                    "ParameterName": "stdOut",
                    "ParameterValue": "  install log  ",
                }
            },
        }
    }
    if top_level is not None:
        job["stdOut"] = top_level
    return job


def test_vios_waited_terminal_result_projects_stdout(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    raw = _vios_job_with_stdout()
    monkeypatch.setattr(
        "hmc_mcp.server_updates._update_op", AsyncMock(return_value=raw)
    )

    result = hmc_vios_update(VIOS_UUID, VIOS_UPDATE_SOURCE, wait=True)

    assert result["stdOut"] == "install log"
    assert result is not raw
    assert "stdOut" not in raw
    assert result["Resource"] is raw["Resource"]


@pytest.mark.parametrize(
    ("wait", "job"),
    [
        (False, _vios_job_with_stdout()),
        (True, _vios_job_with_stdout(status="RUNNING")),
    ],
)
def test_vios_stdout_is_not_projected_without_terminal_wait(
    monkeypatch, mock_hmc, wait, job
):
    _hmc_env(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.server_updates._update_op", AsyncMock(return_value=job)
    )

    result = hmc_vios_update(VIOS_UUID, VIOS_UPDATE_SOURCE, wait=wait)

    assert result is job
    assert "stdOut" not in result


def test_vios_stdout_does_not_overwrite_raw_top_level_value(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    raw = _vios_job_with_stdout(top_level="raw value")
    monkeypatch.setattr(
        "hmc_mcp.server_updates._update_op", AsyncMock(return_value=raw)
    )

    result = hmc_vios_update(VIOS_UUID, VIOS_UPDATE_SOURCE, wait=True)

    assert result is raw
    assert result["stdOut"] == "raw value"


def test_hmc_update_invalid_kind_raises(monkeypatch, mock_hmc):
    """hmc_update_console_software raises ValueError for an unknown kind, never reaching the HMC."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(f"/rest/api/uom/ManagementConsole/{MC_UUID}/do/Invalid")
    with pytest.raises(ValueError, match="Unknown kind"):
        hmc_update_console_software(MC_UUID, REPO, kind="invalid")  # type: ignore[arg-type]
    assert not route.called


def test_vios_update_invalid_kind_raises(monkeypatch, mock_hmc):
    """hmc_vios_update raises ValueError for an unknown kind, never reaching the HMC."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/do/Invalid")
    with pytest.raises(ValueError, match="Unknown kind"):
        hmc_vios_update(VIOS_UUID, REPO, kind="invalid")  # type: ignore[arg-type]
    assert not route.called


def test_update_firmware_submits_job(monkeypatch, mock_hmc):
    """hmc_update_firmware PUTs an UpdateFirmware job to the ManagedSystem."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(
        f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/do/UpdateFirmware"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    hmc_update_firmware(SYSTEM_UUID, REPO)
    body = route.calls.last.request.content.decode()
    assert "UpdateFirmware</OperationName>" in body
    assert "repo.example.com" in body


def test_hmc_update_wait_true_polls_to_completion(monkeypatch, mock_hmc):
    """hmc_update_console_software(wait=True) submits the job then polls until COMPLETED."""
    _hmc_env(monkeypatch)
    submit_route = mock_hmc.put(
        f"/rest/api/uom/ManagementConsole/{MC_UUID}/do/UpdateManagementConsole"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    poll_route = mock_hmc.get("/rest/api/uom/Job/job-uuid-999").mock(
        return_value=httpx.Response(200, text=JOB_ENTRY_COMPLETED)
    )
    result = hmc_update_console_software(
        MC_UUID, CONSOLE_SOURCE, wait=True, timeout_seconds=60, poll_interval=1
    )
    assert submit_route.called
    assert poll_route.called
    assert result["Resource"]["Status"] == "COMPLETED"


def test_list_available_hmc_ptfs(monkeypatch, mock_hmc):
    """hmc_get_available_hmc_ptfs GETs the SoftwareUpdate group."""
    _hmc_env(monkeypatch)
    route = mock_hmc.get(
        f"/rest/api/uom/ManagementConsole/{MC_UUID}?group=SoftwareUpdate"
    ).mock(return_value=httpx.Response(200, text=JOB_ENTRY))
    result = hmc_get_available_hmc_ptfs(MC_UUID)
    assert route.called
    assert result["Resource"]["JobID"] == "job-uuid-999"


def test_list_available_hmc_ptfs_unsupported(monkeypatch, mock_hmc):
    """hmc_get_available_hmc_ptfs converts HTTP 400 REST0026 to actionable error."""
    _hmc_env(monkeypatch)
    error_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<HttpErrorResponseResult><Message>REST0026 Unknown extended attribute group SoftwareUpdate</Message>"
        "</HttpErrorResponseResult>"
    )
    mock_hmc.get(
        f"/rest/api/uom/ManagementConsole/{MC_UUID}?group=SoftwareUpdate"
    ).mock(return_value=httpx.Response(400, text=error_xml))
    with pytest.raises(
        HMCError,
        match="SoftwareUpdate attribute group not supported on this HMC version",
    ):
        hmc_get_available_hmc_ptfs(MC_UUID)


# ---------------------------------------------------------------------- #
# hmc_list_recent_jobs (job list)
# ---------------------------------------------------------------------- #

JOB_FEED_2 = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:job-uuid-001</id>
    <title>Job</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <Job xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <JobID>job-uuid-001</JobID>
        <Status>COMPLETED</Status>
      </Job>
    </content>
  </entry>
  <entry>
    <id>urn:uuid:job-uuid-002</id>
    <title>Job</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <Job xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <JobID>job-uuid-002</JobID>
        <Status>RUNNING</Status>
      </Job>
    </content>
  </entry>
</feed>
"""


def test_recent_jobs_parses_feed(monkeypatch, mock_hmc):
    """hmc_list_recent_jobs returns a list of parsed job dicts from the feed."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/Job").mock(
        return_value=httpx.Response(200, text=JOB_FEED_2)
    )
    result = hmc_list_recent_jobs()
    assert isinstance(result, list)
    assert len(result) == 2
    job_ids = {j["Resource"]["JobID"] for j in result}
    assert job_ids == {"job-uuid-001", "job-uuid-002"}


def test_recent_jobs_limit_truncates(monkeypatch, mock_hmc):
    """hmc_list_recent_jobs(limit=1) returns only the first 1 entry."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/Job").mock(
        return_value=httpx.Response(200, text=JOB_FEED_2)
    )
    result = hmc_list_recent_jobs(limit=1)
    assert len(result) == 1
    assert result[0]["Resource"]["JobID"] == "job-uuid-001"


def test_recent_jobs_zero_limit_still_fetches_and_parses(monkeypatch, mock_hmc):
    """A zero cap does not skip the HMC request or feed parsing."""
    _hmc_env(monkeypatch)
    route = mock_hmc.get("/rest/api/uom/Job").mock(
        return_value=httpx.Response(200, text=JOB_FEED_2)
    )

    assert hmc_list_recent_jobs(limit=0) == []
    assert route.called


def test_recent_jobs_rejects_negative_limit_before_request(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    route = mock_hmc.get("/rest/api/uom/Job")

    with pytest.raises(ValueError, match="limit must be greater"):
        hmc_list_recent_jobs(limit=-1)

    assert not route.called


def test_recent_jobs_empty_feed(monkeypatch, mock_hmc):
    """hmc_list_recent_jobs returns an empty list when the HMC has no jobs."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/Job").mock(
        return_value=httpx.Response(200, text=EMPTY_FEED)
    )
    result = hmc_list_recent_jobs()
    assert result == []


# hmc_wait_for_job
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

JOB_ENTRY_FAILED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:job-uuid-999</id>
  <title>Job</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <Job xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <JobID>job-uuid-999</JobID>
      <Status>FAILED</Status>
      <Results>
        <JobParameter>
          <ParameterName>result</ParameterName>
          <ParameterValue>Power-on was rejected</ParameterValue>
        </JobParameter>
      </Results>
    </Job>
  </content>
</entry>
"""

JOB_ENTRY_EXCEPTION = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:job-uuid-999</id>
  <title>Job</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <Job xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <JobID>job-uuid-999</JobID>
      <Status>EXCEPTION</Status>
      <ResponseException>
        <Message>HMC job raised an exception</Message>
      </ResponseException>
    </Job>
  </content>
</entry>
"""

JOB_RESPONSE_ERROR_DATA = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:job-uuid-999</id>
  <title>JobResponse</title>
  <content type="application/vnd.ibm.powervm.web+xml; type=JobResponse">
    <JobResponse xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/">
      <JobID>job-uuid-999</JobID>
      <Status>COMPLETED_WITH_ERROR</Status>
      <Results>
        <JobParameter>
          <ParameterName>ErrorData</ParameterName>
          <ParameterValue>HSCL3205 The partition entered an error state</ParameterValue>
        </JobParameter>
      </Results>
    </JobResponse>
  </content>
</entry>
"""

JOB_ENTRY_EMPTY_RESOURCE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:job-uuid-999</id>
  <title>Job</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <Job xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/"/>
  </content>
</entry>
"""

JOB_OUTCOME_KEYS = {"job_id", "status", "timed_out", "error", "job"}


def test_wait_for_job_immediate_completed(monkeypatch, mock_hmc):
    """hmc_wait_for_job returns immediately when the first poll is COMPLETED."""
    _hmc_env(monkeypatch)
    route = mock_hmc.get("/rest/api/uom/Job/job-uuid-999").mock(
        return_value=httpx.Response(200, text=JOB_ENTRY_COMPLETED)
    )
    result = hmc_wait_for_job("job-uuid-999")
    assert route.called
    assert set(asdict(result)) == JOB_OUTCOME_KEYS
    assert result.job_id == "job-uuid-999"
    assert result.status == "COMPLETED"
    assert result.timed_out is False
    assert result.error is None
    assert result.job["Resource"]["Status"] == "COMPLETED"


@pytest.mark.parametrize(
    ("response", "status", "error"),
    [
        (JOB_ENTRY_FAILED, "FAILED", "Power-on was rejected"),
        (JOB_ENTRY_EXCEPTION, "EXCEPTION", "HMC job raised an exception"),
        (
            JOB_RESPONSE_ERROR_DATA,
            "COMPLETED_WITH_ERROR",
            "HSCL3205 The partition entered an error state",
        ),
    ],
)
def test_wait_for_job_surfaces_terminal_failure(
    monkeypatch, mock_hmc, response, status, error
):
    _hmc_env(monkeypatch)
    monkeypatch.setenv("HMC_VERIFY_SSL", "true")
    mock_hmc.get("/rest/api/uom/Job/job-uuid-999").mock(
        return_value=httpx.Response(200, text=response)
    )

    result = hmc_wait_for_job("job-uuid-999")

    assert set(asdict(result)) == JOB_OUTCOME_KEYS
    assert result.job_id == "job-uuid-999"
    assert result.status == status
    assert result.timed_out is False
    assert result.error == error
    assert result.job["Resource"]["Status"] == status


def test_wait_for_job_timeout_is_explicit(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/Job/job-uuid-999").mock(
        return_value=httpx.Response(200, text=JOB_ENTRY)  # Status=RUNNING
    )
    # timeout=0 means the deadline is already past after the first poll
    result = hmc_wait_for_job("job-uuid-999", timeout_seconds=0, poll_interval=1)
    assert set(asdict(result)) == JOB_OUTCOME_KEYS
    assert result.job_id == "job-uuid-999"
    assert result.status == "RUNNING"
    assert result.timed_out is True
    assert result.error is None
    assert result.job["Resource"]["Status"] == "RUNNING"


def test_wait_for_job_empty_resource_returns_timed_out_shape(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    monkeypatch.setenv("HMC_VERIFY_SSL", "true")
    mock_hmc.get("/rest/api/uom/Job/job-uuid-999").mock(
        return_value=httpx.Response(200, text=JOB_ENTRY_EMPTY_RESOURCE)
    )

    result = hmc_wait_for_job("job-uuid-999", timeout_seconds=0, poll_interval=1)

    assert set(asdict(result)) == JOB_OUTCOME_KEYS
    assert result.job_id == "job-uuid-999"
    assert result.status is None
    assert result.timed_out is True
    assert result.error is None
    assert result.job["Resource"] == ""


# ---------------------------------------------------------------------- #
# hmc_get_job / hmc_wait_for_job — SELF-link-based polling (issue #95)
# ---------------------------------------------------------------------- #

_JOB_OP_HREF = "/rest/api/uom/LogicalPartition/lpar-uuid/do/PowerOn/Job/job-uuid-999"


def test_get_job_with_href_uses_direct_path(monkeypatch, mock_hmc):
    """hmc_get_job(uuid, job_href=...) GETs the exact href, not /uom/Job/{uuid}."""
    _hmc_env(monkeypatch)
    href_route = mock_hmc.get(_JOB_OP_HREF).mock(
        return_value=httpx.Response(200, text=JOB_ENTRY)
    )
    global_route = mock_hmc.get("/rest/api/uom/Job/job-uuid-999").mock(
        return_value=httpx.Response(400, text="Unrecognized root REST type of Job")
    )
    result = hmc_get_job("job-uuid-999", job_href=_JOB_OP_HREF)
    assert href_route.called
    assert not global_route.called
    assert result["Resource"]["JobID"] == "job-uuid-999"


def test_wait_for_job_with_href_uses_direct_path(monkeypatch, mock_hmc):
    """hmc_wait_for_job(uuid, ..., job_href=...) polls the exact href path."""
    _hmc_env(monkeypatch)
    href_route = mock_hmc.get(_JOB_OP_HREF).mock(
        return_value=httpx.Response(200, text=JOB_ENTRY_COMPLETED)
    )
    global_route = mock_hmc.get("/rest/api/uom/Job/job-uuid-999").mock(
        return_value=httpx.Response(400, text="Unrecognized root REST type of Job")
    )
    result = hmc_wait_for_job(
        "job-uuid-999", timeout_seconds=5, poll_interval=1, job_href=_JOB_OP_HREF
    )
    assert href_route.called
    assert not global_route.called
    assert result.status == "COMPLETED"
    assert result.timed_out is False


def test_recent_jobs_unsupported_endpoint_raises_actionable_error(
    monkeypatch, mock_hmc
):
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/Job").mock(
        return_value=httpx.Response(
            400, text="REST000E Unrecognized root REST type of Job"
        )
    )
    with pytest.raises(HMCError, match="hmc_get_job") as exc_info:
        hmc_list_recent_jobs()
    assert exc_info.value.status_code == 400


def test_recent_jobs_unrelated_400_propagates(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/Job").mock(
        return_value=httpx.Response(400, text="REST0123E Invalid filter expression")
    )

    with pytest.raises(HMCError, match="Invalid filter expression"):
        hmc_list_recent_jobs()


_JOB_SELF_LINK = f"https://hmc.test:12443{_JOB_OP_HREF}"

# A job entry that includes a SELF link (as submit_job returns on some HMC builds).
JOB_ENTRY_WITH_LINK = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:job-uuid-999</id>
  <title>Job</title>
  <link rel="SELF" href="{_JOB_SELF_LINK}"/>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <Job xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <JobID>job-uuid-999</JobID>
      <Status>RUNNING</Status>
    </Job>
  </content>
</entry>
"""

JOB_ENTRY_COMPLETED_WITH_LINK = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:job-uuid-999</id>
  <title>Job</title>
  <link rel="SELF" href="{_JOB_SELF_LINK}"/>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <Job xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <JobID>job-uuid-999</JobID>
      <Status>COMPLETED</Status>
    </Job>
  </content>
</entry>
"""


def test_power_on_with_wait_uses_job_self_link(monkeypatch, mock_hmc):
    """hmc_power_on_lpar(wait=True) polls the SELF link from the submitted job entry."""
    _hmc_env(monkeypatch)
    mock_hmc.get(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/quick/PartitionState"
    ).mock(return_value=httpx.Response(200, text="not activated"))
    mock_hmc.put(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/do/PowerOn").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY_WITH_LINK)
    )
    # The SELF link path should be polled, not the global /uom/Job/ path.
    poll_route = mock_hmc.get(_JOB_OP_HREF).mock(
        return_value=httpx.Response(200, text=JOB_ENTRY_COMPLETED_WITH_LINK)
    )
    global_route = mock_hmc.get("/rest/api/uom/Job/job-uuid-999").mock(
        return_value=httpx.Response(400, text="Unrecognized root REST type of Job")
    )
    result = hmc_power_on_lpar(LPAR_UUID, wait=True, poll_interval=1)
    assert poll_route.called
    assert not global_route.called
    assert result.already_running is False
    assert result.job["Resource"]["Status"] == "COMPLETED"
    assert result.message is None
