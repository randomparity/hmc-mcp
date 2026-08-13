"""Tool-layer tests for the partition-template library MCP tools.

The client methods and job builder are covered in test_templates_api.py;
these tests call the actual ``@mcp.tool`` functions in ``server_templates``
against the respx ``mock_hmc`` router so the argument->URL mapping in the
tool bodies is exercised.
"""

import httpx
import pytest

from hmc_mcp.client import HMCError
from hmc_mcp.server import (
    hmc_deploy_partition_template,
    hmc_partition_templates,
)

from conftest import JOB_ENTRY

TEMPLATE_UUID = "tmpl-uuid-1"
TEMPLATE_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:tmpl-uuid-1</id>
    <title>PartitionTemplate:aix-gold</title>
    <content type="application/vnd.ibm.powervm.templates+xml">
      <PartitionTemplate xmlns="http://www.ibm.com/xmlns/systems/power/firmware/templates/mc/2012_10/">
        <templateName>aix-gold</templateName>
      </PartitionTemplate>
    </content>
  </entry>
</feed>
"""


def _hmc_env(monkeypatch) -> None:
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


def test_partition_templates_lists_all(monkeypatch, mock_hmc):
    """hmc_partition_templates() GETs the template library feed."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/templates/PartitionTemplate").mock(
        return_value=httpx.Response(200, text=TEMPLATE_FEED)
    )
    result = hmc_partition_templates()
    assert result[0]["UUID"] == TEMPLATE_UUID
    assert result[0]["Resource"]["templateName"] == "aix-gold"


def test_partition_templates_with_uuid_gets_one(monkeypatch, mock_hmc):
    """hmc_partition_templates(template_uuid=...) GETs one template by UUID."""
    _hmc_env(monkeypatch)
    mock_hmc.get(f"/rest/api/templates/PartitionTemplate/{TEMPLATE_UUID}").mock(
        return_value=httpx.Response(200, text=TEMPLATE_FEED)
    )
    result = hmc_partition_templates(TEMPLATE_UUID)
    assert result["Resource"]["templateName"] == "aix-gold"


def test_partition_templates_with_uuid_error_propagates(monkeypatch, mock_hmc):
    """A non-200 template GET surfaces as HMCError."""
    _hmc_env(monkeypatch)
    mock_hmc.get(f"/rest/api/templates/PartitionTemplate/{TEMPLATE_UUID}").mock(
        return_value=httpx.Response(404, text="<error>not found</error>")
    )
    with pytest.raises(HMCError) as exc_info:
        hmc_partition_templates(TEMPLATE_UUID)
    assert exc_info.value.status_code == 404


def test_partition_templates_list_http_406_not_licensed(monkeypatch, mock_hmc):
    """hmc_partition_templates() returns clear message when templates not licensed (HTTP 406)."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/templates/PartitionTemplate").mock(
        return_value=httpx.Response(406, text="<error>Not supported</error>")
    )
    with pytest.raises(HMCError) as exc_info:
        hmc_partition_templates()
    assert exc_info.value.status_code == 406
    error_msg = str(exc_info.value)
    # The message should be actionable and mention templates specifically, not raw HTTP error
    assert "not licensed" in error_msg.lower()
    assert "partition templates" in error_msg.lower()


def test_partition_templates_get_http_406_not_licensed(monkeypatch, mock_hmc):
    """hmc_partition_templates(template_uuid=...) returns clear message when templates not licensed (HTTP 406)."""
    _hmc_env(monkeypatch)
    mock_hmc.get(f"/rest/api/templates/PartitionTemplate/{TEMPLATE_UUID}").mock(
        return_value=httpx.Response(406, text="<error>Not supported</error>")
    )
    with pytest.raises(HMCError) as exc_info:
        hmc_partition_templates(TEMPLATE_UUID)
    assert exc_info.value.status_code == 406
    error_msg = str(exc_info.value)
    assert "not licensed" in error_msg.lower()
    assert "partition templates" in error_msg.lower()


def test_deploy_partition_template_http_406_not_licensed(monkeypatch, mock_hmc):
    """hmc_deploy_partition_template returns clear message when templates not licensed (HTTP 406)."""
    _hmc_env(monkeypatch)
    mock_hmc.put("/rest/api/templates/PartitionTemplate/draft-uuid/do/deploy").mock(
        return_value=httpx.Response(406, text="<error>Not supported</error>")
    )
    with pytest.raises(HMCError) as exc_info:
        hmc_deploy_partition_template("draft-uuid", "sys-uuid")
    assert exc_info.value.status_code == 406
    error_msg = str(exc_info.value)
    assert "not licensed" in error_msg.lower()
    assert "partition templates" in error_msg.lower()


def test_deploy_partition_template_submits_job(monkeypatch, mock_hmc):
    """hmc_deploy_partition_template PUTs a Deploy job to the draft template."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(
        "/rest/api/templates/PartitionTemplate/draft-uuid/do/deploy"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    result = hmc_deploy_partition_template("draft-uuid", "sys-uuid")
    body = route.calls.last.request.content.decode()
    assert "Deploy</OperationName>" in body
    assert "TargetUuid" in body and "sys-uuid" in body
    assert "K_X_API_SESSION_MEMENTO" in body
    # result is now wrapped: {"job": <job_entry>, "ownership_stamped": None, "warnings": [...]}
    assert result["job"]["Resource"]["JobID"] == "job-uuid-999"
    assert result["ownership_stamped"] is None


# ---------------------------------------------------------------------- #
# wait=True path: deploy blocks until job reaches terminal state
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


def test_deploy_partition_template_wait_true_polls_to_completion(monkeypatch, mock_hmc):
    """hmc_deploy_partition_template(wait=True) submits then polls until COMPLETED."""
    _hmc_env(monkeypatch)
    submit_route = mock_hmc.put(
        "/rest/api/templates/PartitionTemplate/draft-uuid/do/deploy"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    poll_route = mock_hmc.get("/rest/api/uom/Job/job-uuid-999").mock(
        return_value=httpx.Response(200, text=JOB_ENTRY_COMPLETED)
    )
    result = hmc_deploy_partition_template(
        "draft-uuid", "sys-uuid", wait=True, timeout_seconds=60, poll_interval=0
    )
    assert submit_route.called
    assert poll_route.called
    # result is now wrapped: {"job": <job_entry>, "ownership_stamped": None, "warnings": [...]}
    assert result["job"]["Resource"]["Status"] == "COMPLETED"
    assert result["ownership_stamped"] is None
