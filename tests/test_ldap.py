"""Tests for HMC LDAP configuration tools (list/configure/remove)."""

import httpx
import pytest

from hmc_mcp.client import HMCClient, HMCError
from hmc_mcp.templates import build_ldap_config_document
from hmc_mcp.config import HMCConfig as _HMCConfig


def make_config(**kw) -> _HMCConfig:
    return _HMCConfig(host="hmc.test", user="hscroot", password="abc123", verify_ssl=False, **kw)


BASE = "https://hmc.test:12443"

# Minimal XML responses for LDAP endpoints
LDAP_CONFIG_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:ibm:hmc:ldap:config</id>
    <title>HmcLdapServer</title>
    <content type="application/vnd.ibm.powervm.web+xml">
      <HmcLdapServer xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/">
        <LdapServerUrl>ldap://ldap.example.com</LdapServerUrl>
        <BaseDN>dc=example,dc=com</BaseDN>
      </HmcLdapServer>
    </content>
  </entry>
</feed>
"""

LDAP_CONFIG_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<HmcLdapServer xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/" schemaVersion="V1_0">
  <Metadata><Atom/></Metadata>
  <LdapServerUrl>ldap://ldap.example.com</LdapServerUrl>
  <BaseDN>dc=example,dc=com</BaseDN>
  <BindDN>cn=admin,dc=example,dc=com</BindDN>
</HmcLdapServer>
"""


# ------------------------------------------------------------------ #
# XML builder unit tests
# ------------------------------------------------------------------ #

def test_build_ldap_config_document_full():
    xml = build_ldap_config_document(
        server_url="ldap://ldap.example.com",
        base_dn="dc=example,dc=com",
        bind_dn="cn=admin,dc=example,dc=com",
        bind_pw="secret",
        search_filter="(objectClass=person)",
        hmc_groups="HMCAdmins",
        group_member_attributes="member",
    )
    assert "<LdapServerUrl" in xml and "ldap://ldap.example.com" in xml
    assert "<BaseDN" in xml and "dc=example,dc=com" in xml
    assert "<BindDN" in xml and "cn=admin,dc=example,dc=com" in xml
    assert "<BindPw" in xml and "secret" in xml
    assert "<SearchFilter" in xml and "(objectClass=person)" in xml
    assert "<HmcGroups" in xml and "HMCAdmins" in xml
    assert "<GroupMemberAttributes" in xml and "member" in xml
    assert "HmcLdapServer" in xml


def test_build_ldap_config_document_partial():
    xml = build_ldap_config_document(
        server_url="ldaps://ldap.corp.com:636",
        base_dn="ou=users,dc=corp,dc=com",
    )
    assert "<LdapServerUrl" in xml and "ldaps://ldap.corp.com:636" in xml
    assert "<BaseDN" in xml and "ou=users,dc=corp,dc=com" in xml
    assert "BindDN" not in xml   # not passed
    assert "BindPw" not in xml   # not passed


def test_build_ldap_config_document_minimal():
    xml = build_ldap_config_document(server_url="ldap://localhost")
    assert "ldap://localhost" in xml
    assert "HmcLdapServer" in xml


# ------------------------------------------------------------------ #
# Client-level tests (respx-mocked HTTP)
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_list_ldap_config(mock_hmc):
    mock_hmc.get("/rest/api/web/HmcLdapServer").mock(
        return_value=httpx.Response(200, text=LDAP_CONFIG_FEED)
    )
    async with HMCClient(make_config()) as hmc:
        xml = await hmc.list_ldap_config()
    assert "ldap://ldap.example.com" in xml
    assert "dc=example,dc=com" in xml


@pytest.mark.asyncio
async def test_list_ldap_config_empty(mock_hmc):
    mock_hmc.get("/rest/api/web/HmcLdapServer").mock(
        return_value=httpx.Response(204)
    )
    async with HMCClient(make_config()) as hmc:
        xml = await hmc.list_ldap_config()
    assert xml == ""


@pytest.mark.asyncio
async def test_configure_ldap(mock_hmc):
    route = mock_hmc.post("/rest/api/web/HmcLdapServer").mock(
        return_value=httpx.Response(200, text=LDAP_CONFIG_ENTRY)
    )
    ldap_xml = build_ldap_config_document(
        server_url="ldap://ldap.example.com",
        base_dn="dc=example,dc=com",
        bind_dn="cn=admin,dc=example,dc=com",
        bind_pw="secret",
    )
    async with HMCClient(make_config()) as hmc:
        xml = await hmc.configure_ldap(ldap_xml)
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "ldap://ldap.example.com" in body
    assert "dc=example,dc=com" in body
    assert "LdapServerUrl" in xml


@pytest.mark.asyncio
async def test_remove_ldap_config_ldap(mock_hmc):
    route = mock_hmc.post("/rest/api/web/HmcLdapServer").mock(
        return_value=httpx.Response(200, text=LDAP_CONFIG_ENTRY)
    )
    async with HMCClient(make_config()) as hmc:
        await hmc.remove_ldap_config("ldap")
    assert route.called
    url_str = str(route.calls.last.request.url)
    assert "Remove=ldap" in url_str


@pytest.mark.asyncio
async def test_remove_ldap_config_binddn(mock_hmc):
    route = mock_hmc.post("/rest/api/web/HmcLdapServer").mock(
        return_value=httpx.Response(200, text="<ok/>")
    )
    async with HMCClient(make_config()) as hmc:
        await hmc.remove_ldap_config("binddn")
    assert route.called
    url_str = str(route.calls.last.request.url)
    assert "Remove=binddn" in url_str


@pytest.mark.asyncio
async def test_list_ldap_config_error_raises(mock_hmc):
    mock_hmc.get("/rest/api/web/HmcLdapServer").mock(
        return_value=httpx.Response(403, text="<error>forbidden</error>")
    )
    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError) as exc_info:
            await hmc.list_ldap_config()
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_configure_ldap_error_raises(mock_hmc):
    mock_hmc.post("/rest/api/web/HmcLdapServer").mock(
        return_value=httpx.Response(500, text="<error>internal error</error>")
    )
    ldap_xml = build_ldap_config_document(server_url="ldap://bad.host")
    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError) as exc_info:
            await hmc.configure_ldap(ldap_xml)
    assert exc_info.value.status_code == 500
