"""Tool-layer tests for REST-backed and SSH-backed MCP tools.

The client layer (``HMCClient`` + respx) is covered in the domain test dirs;
these tests call the actual ``@mcp.tool`` functions so the argument-mapping
and XML-building logic in the tool bodies is exercised — the layer the
client tests skip.  REST tools run against the respx ``mock_hmc`` router
with env-configured credentials; ``hmc_run_command`` patches the SSH
boundary (``asyncssh.connect``) like the vNIC tests do.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from hmc_mcp.server import (
    hmc_create_lpar,
    hmc_get_job,
    hmc_get_available_hmc_ptfs,
    hmc_lpars,
    hmc_modify_lpar,
    hmc_power_off_lpar,
    hmc_power_on_lpar,
    hmc_recent_jobs,
    hmc_run_command,
    hmc_update_firmware,
    hmc_update_hmc,
    hmc_update_vios,
    hmc_upgrade_hmc,
    hmc_upgrade_vios,
)

from conftest import JOB_ENTRY

SYSTEM_UUID = "sys-uuid-0001"
LPAR_UUID = "lpar-uuid-0001"
VIOS_UUID = "vios-uuid-0001"
MC_UUID = "mc-uuid-0001"

# A single-LPAR Atom feed; {name} is the partition name.
LPAR_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:lpar-uuid-0001</id>
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

# A two-job Atom feed for hmc_recent_jobs tests.
JOB_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
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
# hmc_get_job (REST reads)
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


# ---------------------------------------------------------------------- #
# hmc_recent_jobs
# ---------------------------------------------------------------------- #


def test_recent_jobs_returns_list(monkeypatch, mock_hmc):
    """hmc_recent_jobs returns a list of parsed job entries."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/Job").mock(
        return_value=httpx.Response(200, text=JOB_FEED)
    )
    result = hmc_recent_jobs()
    assert len(result) == 2
    assert result[0]["Resource"]["JobID"] == "job-uuid-001"
    assert result[1]["Resource"]["Status"] == "RUNNING"


def test_recent_jobs_respects_limit(monkeypatch, mock_hmc):
    """hmc_recent_jobs truncates to the requested limit."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/Job").mock(
        return_value=httpx.Response(200, text=JOB_FEED)
    )
    result = hmc_recent_jobs(limit=1)
    assert len(result) == 1
    assert result[0]["Resource"]["JobID"] == "job-uuid-001"


def test_recent_jobs_empty_feed(monkeypatch, mock_hmc):
    """hmc_recent_jobs returns an empty list when the HMC has no jobs."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/Job").mock(
        return_value=httpx.Response(200, text=EMPTY_FEED)
    )
    assert hmc_recent_jobs() == []


def test_find_lpar_by_name(monkeypatch, mock_hmc):
    """hmc_lpars(name=...) searches by PartitionName and returns the entry."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/LogicalPartition/search/(PartitionName==aixprod)").mock(
        return_value=httpx.Response(200, text=LPAR_FEED.format(name="aixprod"))
    )
    result = hmc_lpars(name="aixprod")
    assert result["UUID"] == LPAR_UUID
    assert result["Resource"]["PartitionName"] == "aixprod"


def test_find_lpar_not_found_returns_none(monkeypatch, mock_hmc):
    """hmc_lpars(name=...) returns None when the search matches nothing."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/LogicalPartition/search/(PartitionName==ghost)").mock(
        return_value=httpx.Response(200, text=EMPTY_FEED)
    )
    assert hmc_lpars(name="ghost") is None


# ---------------------------------------------------------------------- #
# hmc_power_on_lpar / hmc_power_off_lpar (job submission)
# ---------------------------------------------------------------------- #


def test_power_on_lpar_submits_job(monkeypatch, mock_hmc):
    """hmc_power_on_lpar PUTs a PowerOn job to the do/ path."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/do/PowerOn").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    result = hmc_power_on_lpar(LPAR_UUID)
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "PowerOn</OperationName>" in body
    assert result["Resource"]["JobID"] == "job-uuid-999"


def test_power_off_lpar_submits_job(monkeypatch, mock_hmc):
    """hmc_power_off_lpar PUTs a PowerOff job with the immediate flag."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/do/PowerOff").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
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
    route = mock_hmc.put(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition").mock(
        return_value=httpx.Response(201, text=LPAR_FEED.format(name="newlpar"))
    )
    result = hmc_create_lpar(
        system_uuid=SYSTEM_UUID,
        name="newlpar",
        min_memory=512,
        desired_memory=2048,
        max_memory=4096,
        desired_procs=0.5,
        desired_vcpus=2,
    )
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "newlpar</PartitionName>" in body
    assert '<DesiredMemory kb="CUD" kxe="false">2048</DesiredMemory>' in body
    assert '<MinimumMemory kb="CUD" kxe="false">512</MinimumMemory>' in body
    assert '<DesiredProcessingUnits kb="CUD" kxe="false">0.5</DesiredProcessingUnits>' in body
    assert '<DesiredVirtualProcessors kb="CUD" kxe="false">2</DesiredVirtualProcessors>' in body
    assert result["Resource"]["PartitionName"] == "newlpar"


def test_create_lpar_dedicated_uses_whole_cpus(monkeypatch, mock_hmc):
    """dedicated=True emits DedicatedProcessorConfiguration with int CPUs."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition").mock(
        return_value=httpx.Response(201, text=LPAR_FEED.format(name="ded"))
    )
    hmc_create_lpar(
        system_uuid=SYSTEM_UUID,
        name="ded",
        dedicated=True,
        desired_procs=2.0,
        max_procs=4.0,
    )
    body = route.calls.last.request.content.decode()
    assert "<DedicatedProcessorConfiguration" in body
    assert '<DesiredProcessors kb="CUD" kxe="false">2</DesiredProcessors>' in body
    assert '<HasDedicatedProcessors kb="CUD" kxe="false">true</HasDedicatedProcessors>' in body


def test_modify_lpar_builds_xml(monkeypatch, mock_hmc):
    """hmc_modify_lpar emits only the fields passed; unchanged fields are omitted."""
    _hmc_env(monkeypatch)
    route = mock_hmc.post(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(200, text=LPAR_FEED.format(name="renamed"))
    )
    result = hmc_modify_lpar(LPAR_UUID, name="renamed", desired_memory=8192)
    body = route.calls.last.request.content.decode()
    assert "renamed</PartitionName>" in body
    assert '<DesiredMemory kb="CUD" kxe="false">8192</DesiredMemory>' in body
    # A modify document carries only what changed.
    assert "MaximumMemory" not in body
    assert "ProcessingUnits" not in body
    assert result["Resource"]["PartitionName"] == "renamed"


# ---------------------------------------------------------------------- #
# Update / upgrade tools (job submission)
# ---------------------------------------------------------------------- #


def test_update_hmc_submits_job(monkeypatch, mock_hmc):
    """hmc_update_hmc PUTs an Update job with the repository parameters."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(f"/rest/api/uom/ManagementConsole/{MC_UUID}/do/Update").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    hmc_update_hmc(MC_UUID, REPO)
    body = route.calls.last.request.content.decode()
    assert "Update</OperationName>" in body
    assert "repo.example.com" in body
    assert "/images/hmc" in body


def test_upgrade_hmc_submits_job(monkeypatch, mock_hmc):
    """hmc_upgrade_hmc PUTs an Upgrade job to the ManagementConsole."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(f"/rest/api/uom/ManagementConsole/{MC_UUID}/do/Upgrade").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    hmc_upgrade_hmc(MC_UUID, REPO)
    body = route.calls.last.request.content.decode()
    assert "Upgrade</OperationName>" in body
    assert "repo.example.com" in body


def test_update_vios_submits_job(monkeypatch, mock_hmc):
    """hmc_update_vios PUTs an Update job to the VirtualIOServer."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/do/Update").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    hmc_update_vios(VIOS_UUID, REPO)
    body = route.calls.last.request.content.decode()
    assert "Update</OperationName>" in body
    assert "repo.example.com" in body


def test_upgrade_vios_submits_job(monkeypatch, mock_hmc):
    """hmc_upgrade_vios PUTs an Upgrade job to the VirtualIOServer."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/do/Upgrade").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    hmc_upgrade_vios(VIOS_UUID, REPO)
    body = route.calls.last.request.content.decode()
    assert "Upgrade</OperationName>" in body
    assert "repo.example.com" in body


def test_update_firmware_submits_job(monkeypatch, mock_hmc):
    """hmc_update_firmware PUTs an UpdateFirmware job to the ManagedSystem."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/do/UpdateFirmware").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    hmc_update_firmware(SYSTEM_UUID, REPO)
    body = route.calls.last.request.content.decode()
    assert "UpdateFirmware</OperationName>" in body
    assert "repo.example.com" in body


def test_list_available_hmc_ptfs(monkeypatch, mock_hmc):
    """hmc_get_available_hmc_ptfs GETs the SoftwareUpdate group."""
    _hmc_env(monkeypatch)
    route = mock_hmc.get(f"/rest/api/uom/ManagementConsole/{MC_UUID}?group=SoftwareUpdate").mock(
        return_value=httpx.Response(200, text=JOB_ENTRY)
    )
    result = hmc_get_available_hmc_ptfs(MC_UUID)
    assert route.called
    assert result["Resource"]["JobID"] == "job-uuid-999"


# ---------------------------------------------------------------------- #
# hmc_wait_for_job
# ---------------------------------------------------------------------- #

from hmc_mcp.server import hmc_wait_for_job  # noqa: E402


COMPLETED_JOB_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
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


def test_wait_for_job_completes_immediately(monkeypatch, mock_hmc):
    """hmc_wait_for_job returns once the job reaches COMPLETED."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/Job/job-uuid-999").mock(
        return_value=httpx.Response(200, text=COMPLETED_JOB_ENTRY)
    )
    result = hmc_wait_for_job("job-uuid-999", timeout_seconds=30, poll_interval=1)
    assert result is not None
    assert result["Resource"]["Status"] == "COMPLETED"


def test_wait_for_job_timeout_returns_last_seen(monkeypatch, mock_hmc):
    """hmc_wait_for_job returns the last-seen entry after timeout."""
    _hmc_env(monkeypatch)
    # Always return RUNNING — never reaches terminal status
    mock_hmc.get("/rest/api/uom/Job/job-uuid-999").mock(
        return_value=httpx.Response(200, text=JOB_ENTRY)  # Status=RUNNING
    )
    result = hmc_wait_for_job("job-uuid-999", timeout_seconds=1, poll_interval=0)
    assert result is not None
    assert result["Resource"]["Status"] == "RUNNING"
