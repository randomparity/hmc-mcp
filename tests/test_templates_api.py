"""Tests for Template Library (query + deploy) — /rest/api/templates/."""

import httpx
import pytest

from conftest import JOB_ENTRY, make_config

from hmc_mcp.client import HMCClient
from hmc_mcp.jobs import partition_template_deploy_job

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

TEMPLATE_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:tmpl-uuid-1</id>
  <title>PartitionTemplate:aix-gold</title>
  <content type="application/vnd.ibm.powervm.templates+xml">
    <PartitionTemplate xmlns="http://www.ibm.com/xmlns/systems/power/firmware/templates/mc/2012_10/">
      <templateName>aix-gold</templateName>
    </PartitionTemplate>
  </content>
</entry>
"""


def test_deploy_job():
    xml = partition_template_deploy_job("sys-uuid", "draft-uuid", "memento-1")
    assert "Deploy" in xml
    assert "TargetUuid" in xml and "sys-uuid" in xml
    assert "TemplateUuid" in xml and "draft-uuid" in xml
    assert "K_X_API_SESSION_MEMENTO" in xml and "memento-1" in xml


@pytest.mark.asyncio
async def test_list_partition_templates(mock_hmc):
    mock_hmc.get("/rest/api/templates/PartitionTemplate").mock(
        return_value=httpx.Response(200, text=TEMPLATE_FEED)
    )
    async with HMCClient(make_config()) as hmc:
        templates = await hmc.list_partition_templates()
    assert len(templates) == 1
    assert templates[0]["ResourceType"] == "PartitionTemplate"


@pytest.mark.asyncio
async def test_get_partition_template(mock_hmc):
    mock_hmc.get("/rest/api/templates/PartitionTemplate/tmpl-uuid-1").mock(
        return_value=httpx.Response(200, text=TEMPLATE_ENTRY)
    )
    async with HMCClient(make_config()) as hmc:
        t = await hmc.get_partition_template("tmpl-uuid-1")
    assert t is not None
    assert t["Resource"]["templateName"] == "aix-gold"


@pytest.mark.asyncio
async def test_deploy_partition_template(mock_hmc):
    route = mock_hmc.post(
        "/rest/api/templates/PartitionTemplate/draft-uuid/do/deploy"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    async with HMCClient(make_config()) as hmc:
        job = await hmc.deploy_partition_template("draft-uuid", "sys-uuid")
    body = route.calls.last.request.content.decode()
    assert "Deploy" in body and "TargetUuid" in body and "sys-uuid" in body
    assert job is not None and job["Resource"]["JobID"] == "job-uuid-999"
