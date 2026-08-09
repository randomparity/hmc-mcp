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
    assert result["Resource"]["JobID"] == "job-uuid-999"
