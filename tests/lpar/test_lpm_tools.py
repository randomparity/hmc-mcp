"""Tool-layer tests for the Live Partition Mobility MCP tools.

The job XML builders and client methods are covered in test_lpm.py; these
tests call the actual ``@mcp.tool`` functions in ``server_lpm`` against the
respx ``mock_hmc`` router so the argument->URL and argument->XML mapping in
the tool bodies is exercised — the layer the client tests skip.
"""

import httpx
import pytest
from unittest.mock import ANY, AsyncMock, patch

from hmc_mcp.client import HMCError
from hmc_mcp.server import (
    hmc_migrate_abort_lpar,
    hmc_migrate_lpar,
    hmc_migrate_recover_lpar,
    hmc_migrate_validate_lpar,
    hmc_remote_restart_lpar,
)

from conftest import JOB_ENTRY

LPAR_UUID = "00000000-0000-0000-0000-000000000002"
TARGET_SYSTEM_UUID = "00000000-0000-0000-0000-000000000001"


def _hmc_env(monkeypatch) -> None:
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


def _job_route(router, operation: str):
    return router.put(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/do/{operation}"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))


def test_migrate_lpar_submits_job(monkeypatch, mock_hmc):
    """hmc_migrate_lpar PUTs a Migrate job with the target system."""
    _hmc_env(monkeypatch)
    route = _job_route(mock_hmc, "Migrate")
    result = hmc_migrate_lpar(
        LPAR_UUID, "vrml12-fsp", target_profile_name="prof1", wait_time=60
    )
    body = route.calls.last.request.content.decode()
    assert "Migrate</OperationName>" in body
    assert "TargetManagedSystemName" in body and "vrml12-fsp" in body
    assert "TargetProfileName" in body and "prof1" in body
    assert "WaitTime" in body and "60" in body
    assert result["Resource"]["JobID"] == "job-uuid-999"


def test_migrate_lpar_resolves_target_system_uuid(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    route = _job_route(mock_hmc, "Migrate")
    resolver = AsyncMock(return_value="vrml12-fsp")

    with patch("hmc_mcp.operations_lpm.resolve_system_name", new=resolver):
        hmc_migrate_lpar(LPAR_UUID, TARGET_SYSTEM_UUID)

    resolver.assert_awaited_once_with(ANY, TARGET_SYSTEM_UUID)
    body = route.calls.last.request.content.decode()
    assert "TargetManagedSystemName" in body and "vrml12-fsp" in body


def test_migrate_validate_lpar_submits_job(monkeypatch, mock_hmc):
    """hmc_migrate_validate_lpar PUTs a MigrateValidate job."""
    _hmc_env(monkeypatch)
    route = _job_route(mock_hmc, "MigrateValidate")
    hmc_migrate_validate_lpar(LPAR_UUID, "vrml12-fsp")
    body = route.calls.last.request.content.decode()
    assert "MigrateValidate</OperationName>" in body
    assert "vrml12-fsp" in body


def test_migrate_abort_lpar_submits_job(monkeypatch, mock_hmc):
    """hmc_migrate_abort_lpar PUTs a MigrateAbort job."""
    _hmc_env(monkeypatch)
    route = _job_route(mock_hmc, "MigrateAbort")
    hmc_migrate_abort_lpar(LPAR_UUID)
    assert "MigrateAbort</OperationName>" in route.calls.last.request.content.decode()


def test_migrate_recover_lpar_submits_job(monkeypatch, mock_hmc):
    """hmc_migrate_recover_lpar PUTs a MigrateRecover job."""
    _hmc_env(monkeypatch)
    route = _job_route(mock_hmc, "MigrateRecover")
    hmc_migrate_recover_lpar(LPAR_UUID)
    assert "MigrateRecover</OperationName>" in route.calls.last.request.content.decode()


def test_remote_restart_lpar_submits_job(monkeypatch, mock_hmc):
    """hmc_remote_restart_lpar PUTs a RemoteRestart job with the target."""
    _hmc_env(monkeypatch)
    route = _job_route(mock_hmc, "RemoteRestart")
    hmc_remote_restart_lpar(LPAR_UUID, "vrml12-fsp")
    body = route.calls.last.request.content.decode()
    assert "RemoteRestart</OperationName>" in body
    assert "vrml12-fsp" in body


def test_migrate_lpar_error_propagates(monkeypatch, mock_hmc):
    """A non-2xx job submission surfaces as HMCError naming the failing PUT."""
    _hmc_env(monkeypatch)
    mock_hmc.put(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/do/Migrate").mock(
        return_value=httpx.Response(500, text="<error>boom</error>")
    )
    with pytest.raises(HMCError) as exc_info:
        hmc_migrate_lpar(LPAR_UUID, "vrml12-fsp")
    assert exc_info.value.status_code == 500
    assert "do/Migrate" in str(exc_info.value)


# ---------------------------------------------------------------------- #
# wait=True path: migrate_lpar blocks until job reaches terminal state
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


def test_migrate_lpar_wait_true_polls_to_completion(monkeypatch, mock_hmc):
    """hmc_migrate_lpar(wait=True) submits the job then polls until COMPLETED."""
    _hmc_env(monkeypatch)
    submit_route = _job_route(mock_hmc, "Migrate")
    poll_route = mock_hmc.get("/rest/api/uom/Job/job-uuid-999").mock(
        return_value=httpx.Response(200, text=JOB_ENTRY_COMPLETED)
    )
    result = hmc_migrate_lpar(
        LPAR_UUID, "vrml12-fsp", wait=True, timeout_seconds=60, poll_interval=1
    )
    assert submit_route.called
    assert poll_route.called
    assert result["Resource"]["Status"] == "COMPLETED"


def test_migrate_lpar_wait_false_returns_submitted_job(monkeypatch, mock_hmc):
    """hmc_migrate_lpar(wait=False) returns the submitted job entry without polling."""
    _hmc_env(monkeypatch)
    submit_route = _job_route(mock_hmc, "Migrate")
    poll_route = mock_hmc.get("/rest/api/uom/Job/job-uuid-999").mock(
        return_value=httpx.Response(200, text=JOB_ENTRY_COMPLETED)
    )
    result = hmc_migrate_lpar(LPAR_UUID, "vrml12-fsp", wait=False)
    assert submit_route.called
    assert not poll_route.called
    assert result["Resource"]["JobID"] == "job-uuid-999"


def test_migrate_validate_wait_true_polls_to_completion(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    submit_route = _job_route(mock_hmc, "MigrateValidate")
    poll_route = mock_hmc.get("/rest/api/uom/Job/job-uuid-999").mock(
        return_value=httpx.Response(200, text=JOB_ENTRY_COMPLETED)
    )

    result = hmc_migrate_validate_lpar(
        LPAR_UUID, "vrml12-fsp", wait=True, timeout_seconds=60, poll_interval=1
    )

    assert submit_route.called
    assert poll_route.called
    assert result["Resource"]["Status"] == "COMPLETED"
