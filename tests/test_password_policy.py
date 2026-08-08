"""Tests for HMC password policy tools (list/create/modify/delete)."""

import httpx
import pytest

from hmc_mcp.client import HMCClient, HMCError
from hmc_mcp.templates import build_password_policy_document
from hmc_mcp.config import HMCConfig as _HMCConfig


def make_config(**kw) -> _HMCConfig:
    return _HMCConfig(host="hmc.test", user="hscroot", password="abc123", verify_ssl=False, **kw)


BASE = "https://hmc.test:12443"

# Minimal XML responses for password policy endpoints
POLICY_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:ibm:hmc:policy:StrongPolicy</id>
    <title>HmcPasswordPolicy:StrongPolicy</title>
    <content type="application/vnd.ibm.powervm.web+xml">
      <HmcPasswordPolicy xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/">
        <PolicyName>StrongPolicy</PolicyName>
        <MaxPasswordAge>90</MaxPasswordAge>
        <MinPasswordLength>12</MinPasswordLength>
      </HmcPasswordPolicy>
    </content>
  </entry>
  <entry>
    <id>urn:ibm:hmc:policy:DefaultPolicy</id>
    <title>HmcPasswordPolicy:DefaultPolicy</title>
    <content type="application/vnd.ibm.powervm.web+xml">
      <HmcPasswordPolicy xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/">
        <PolicyName>DefaultPolicy</PolicyName>
        <MaxPasswordAge>0</MaxPasswordAge>
        <MinPasswordLength>8</MinPasswordLength>
      </HmcPasswordPolicy>
    </content>
  </entry>
</feed>
"""

STATUS_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:ibm:hmc:policy:status</id>
    <title>HmcPasswordPolicyStatus</title>
    <content type="application/vnd.ibm.powervm.web+xml">
      <HmcPasswordPolicyStatus xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/">
        <ActivePolicyName>StrongPolicy</ActivePolicyName>
      </HmcPasswordPolicyStatus>
    </content>
  </entry>
</feed>
"""

CREATED_POLICY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<HmcPasswordPolicy xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/" schemaVersion="V1_0">
  <Metadata><Atom/></Metadata>
  <PolicyName>StrongPolicy</PolicyName>
  <MaxPasswordAge>90</MaxPasswordAge>
  <MinPasswordLength>12</MinPasswordLength>
</HmcPasswordPolicy>
"""


# ------------------------------------------------------------------ #
# XML builder unit tests
# ------------------------------------------------------------------ #

def test_build_password_policy_document_create():
    xml = build_password_policy_document(
        policy_name="StrongPolicy",
        pwage=90,
        min_length=12,
        min_digits=2,
        min_uppercase=1,
        min_lowercase=1,
        min_special=1,
        hist_size=5,
        warn_pwage=14,
        min_pwage=1,
    )
    assert "<PolicyName" in xml and "StrongPolicy" in xml
    assert "<MaxPasswordAge" in xml and ">90<" in xml
    assert "<MinPasswordLength" in xml and ">12<" in xml
    assert "<MinNumericChars" in xml and ">2<" in xml
    assert "<MinUpperCaseChars" in xml and ">1<" in xml
    assert "<MinLowerCaseChars" in xml and ">1<" in xml
    assert "<MinSpecialChars" in xml and ">1<" in xml
    assert "<PasswordHistorySize" in xml and ">5<" in xml
    assert "<PasswordExpirationWarning" in xml and ">14<" in xml
    assert "<MinPasswordAge" in xml and ">1<" in xml
    assert "HmcPasswordPolicy" in xml


def test_build_password_policy_document_partial():
    xml = build_password_policy_document(pwage=60, min_length=10)
    assert "<MaxPasswordAge" in xml and ">60<" in xml
    assert "<MinPasswordLength" in xml and ">10<" in xml
    assert "PolicyName" not in xml  # not passed


def test_build_password_policy_document_minimal():
    xml = build_password_policy_document(policy_name="Minimal")
    assert "Minimal" in xml
    assert "HmcPasswordPolicy" in xml


# ------------------------------------------------------------------ #
# Client-level tests (respx-mocked HTTP)
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_list_password_policies_default(mock_hmc):
    mock_hmc.get("/rest/api/web/HmcPasswordPolicy").mock(
        return_value=httpx.Response(200, text=POLICY_FEED)
    )
    async with HMCClient(make_config()) as hmc:
        xml = await hmc.list_password_policies()
    assert "StrongPolicy" in xml
    assert "DefaultPolicy" in xml


@pytest.mark.asyncio
async def test_list_password_policies_status_filter(mock_hmc):
    route = mock_hmc.get("/rest/api/web/HmcPasswordPolicy").mock(
        return_value=httpx.Response(200, text=STATUS_FEED)
    )
    async with HMCClient(make_config()) as hmc:
        xml = await hmc.list_password_policies("status")
    assert "PolicyType=status" in str(route.calls.last.request.url)
    assert "ActivePolicyName" in xml


@pytest.mark.asyncio
async def test_create_password_policy(mock_hmc):
    route = mock_hmc.post("/rest/api/web/HmcPasswordPolicy").mock(
        return_value=httpx.Response(201, text=CREATED_POLICY)
    )
    policy_xml = build_password_policy_document(
        policy_name="StrongPolicy", pwage=90, min_length=12
    )
    async with HMCClient(make_config()) as hmc:
        xml = await hmc.create_password_policy(policy_xml)
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "StrongPolicy" in body
    assert "StrongPolicy" in xml


@pytest.mark.asyncio
async def test_modify_password_policy(mock_hmc):
    route = mock_hmc.post("/rest/api/web/HmcPasswordPolicy/StrongPolicy").mock(
        return_value=httpx.Response(200, text=CREATED_POLICY)
    )
    policy_xml = build_password_policy_document(pwage=180, min_length=14)
    async with HMCClient(make_config()) as hmc:
        await hmc.modify_password_policy("StrongPolicy", policy_xml)
    assert route.called
    body = route.calls.last.request.content.decode()
    assert ">180<" in body
    assert ">14<" in body


@pytest.mark.asyncio
async def test_delete_password_policy(mock_hmc):
    route = mock_hmc.delete("/rest/api/web/HmcPasswordPolicy/StrongPolicy").mock(
        return_value=httpx.Response(204)
    )
    async with HMCClient(make_config()) as hmc:
        await hmc.delete_password_policy("StrongPolicy")
    assert route.called


@pytest.mark.asyncio
async def test_list_password_policies_error_raises(mock_hmc):
    mock_hmc.get("/rest/api/web/HmcPasswordPolicy").mock(
        return_value=httpx.Response(403, text="<error>forbidden</error>")
    )
    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError) as exc_info:
            await hmc.list_password_policies()
    assert exc_info.value.status_code == 403
