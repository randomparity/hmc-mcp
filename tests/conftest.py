"""Shared pytest fixtures for the hmc-mcp suite."""

import httpx
import pytest
import respx

from hmc_mcp.config import HMCConfig

BASE = "https://hmc.test:12443"

LOGON_RESPONSE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<LogonResponse xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/">
  <X-API-Session>test-session-token-123</X-API-Session>
</LogonResponse>
"""

JOB_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:job-uuid-999</id>
  <title>Job</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <Job xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <JobID>job-uuid-999</JobID>
      <Status>RUNNING</Status>
    </Job>
  </content>
</entry>
"""


def make_config(**kw) -> HMCConfig:
    return HMCConfig(
        host="hmc.test", user="hscroot", password="abc123", verify_ssl=False, **kw
    )


@pytest.fixture
def mock_hmc():
    """respx router with logon/logoff pre-mocked; add per-test routes."""
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        router.put("/rest/api/web/Logon").mock(
            return_value=httpx.Response(200, text=LOGON_RESPONSE)
        )
        router.delete("/rest/api/web/Logon").mock(return_value=httpx.Response(204))
        yield router
