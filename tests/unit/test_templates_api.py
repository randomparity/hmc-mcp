"""Tests for Template Library (query + deploy) — /rest/api/templates/."""

from xml.etree import ElementTree

import httpx
import pytest
from conftest import JOB_ENTRY, JOB_ID, live_fixture, live_response, make_config

from hmcpctl.client.core import HMCClient
from hmcpctl.errors import HMCTransportError
from hmcpctl.jobs import deploy_partition_template_job

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
    xml = deploy_partition_template_job("draft-uuid", "sys-uuid", "memento-1")
    root = ElementTree.fromstring(xml)
    parameters = {
        parameter[1].text: parameter[2].text
        for parameter in root.iter()
        if parameter.tag.endswith("JobParameter")
    }
    assert "Deploy" in xml
    assert parameters == {
        "K_X_API_SESSION_MEMENTO": "memento-1",
        "TargetUuid": "sys-uuid",
        "TemplateUuid": "draft-uuid",
    }


@pytest.mark.asyncio
async def test_template_transport_failure_uses_shared_hmc_error(mock_hmc):
    mock_hmc.get("/rest/api/templates/PartitionTemplate").mock(
        side_effect=httpx.ReadTimeout("template request timed out")
    )

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCTransportError, match="GET /rest/api/templates"):
            await hmc.list_partition_templates()


@pytest.mark.asyncio
async def test_list_partition_templates(mock_hmc):
    """The captured V10R3 library feed, served for `Accept: application/atom+xml`."""
    path, response = live_response("rest-templates-feed")
    route = mock_hmc.get(path).mock(return_value=response)
    async with HMCClient(make_config()) as hmc:
        templates = await hmc.list_partition_templates()
    assert len(templates) == 7
    assert templates[0]["ResourceType"] == "PartitionTemplateSummary"
    assert templates[0]["Resource"]["partitionTemplateName"] == "QuickStart_lpar_rpa_1"
    assert route.calls[0].request.headers["accept"] == "application/atom+xml"


@pytest.mark.asyncio
async def test_list_partition_templates_avoids_the_typed_accept_v10r3_refuses(
    mock_hmc,
):
    """V10R3 answers the typed templates+xml Accept with an empty HTTP 406."""
    refused = live_fixture("rest-templates-typed-406")
    path, served = live_response("rest-templates-feed")

    def answer(request: httpx.Request) -> httpx.Response:
        if "templates+xml" in request.headers["accept"]:
            return httpx.Response(refused["status"], text=refused["body"])
        return served

    mock_hmc.get(path).mock(side_effect=answer)
    async with HMCClient(make_config()) as hmc:
        assert len(await hmc.list_partition_templates()) == 7


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
    route = mock_hmc.put(
        "/rest/api/templates/PartitionTemplate/draft-uuid/do/deploy"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    async with HMCClient(make_config()) as hmc:
        job = await hmc.deploy_partition_template("draft-uuid", "sys-uuid")
    body = route.calls.last.request.content.decode()
    assert "Deploy" in body
    root = ElementTree.fromstring(body)
    parameters = {
        parameter[1].text: parameter[2].text
        for parameter in root.iter()
        if parameter.tag.endswith("JobParameter")
    }
    assert parameters["K_X_API_SESSION_MEMENTO"]
    assert parameters["TargetUuid"] == "sys-uuid"
    assert parameters["TemplateUuid"] == "draft-uuid"
    assert job is not None and job["Resource"]["JobID"] == JOB_ID
