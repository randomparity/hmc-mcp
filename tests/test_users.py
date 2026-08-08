"""Tests for HMC user management tools (list/get/create/modify/delete)."""

import httpx
import pytest

from hmc_mcp.client import HMCClient, HMCError
from hmc_mcp.config import HMCConfig as _HMCConfig
from hmc_mcp.server import (
    hmc_create_user,
    hmc_delete_user,
    hmc_get_user,
    hmc_list_users,
    hmc_modify_user,
)
from hmc_mcp.templates import build_hmc_user_document


def make_config(**kw) -> _HMCConfig:
    return _HMCConfig(host="hmc.test", user="hscroot", password="abc123", verify_ssl=False, **kw)

BASE = "https://hmc.test:12443"

# Minimal XML responses for user endpoints
USER_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:ibm:hmc:user:hscroot</id>
    <title>HmcUser:hscroot</title>
    <content type="application/vnd.ibm.powervm.web+xml">
      <HmcUser xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/">
        <UserID>hscroot</UserID>
        <TaskRole>hmcsuperadmin</TaskRole>
        <IsEnabled>true</IsEnabled>
      </HmcUser>
    </content>
  </entry>
  <entry>
    <id>urn:ibm:hmc:user:operator1</id>
    <title>HmcUser:operator1</title>
    <content type="application/vnd.ibm.powervm.web+xml">
      <HmcUser xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/">
        <UserID>operator1</UserID>
        <TaskRole>hmcoperator</TaskRole>
        <IsEnabled>true</IsEnabled>
      </HmcUser>
    </content>
  </entry>
</feed>
"""

USER_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<HmcUser xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/" schemaVersion="V1_0">
  <Metadata><Atom/></Metadata>
  <UserID>hscroot</UserID>
  <TaskRole>hmcsuperadmin</TaskRole>
  <Description>Default superadmin</Description>
  <IsEnabled>true</IsEnabled>
</HmcUser>
"""

CREATED_USER = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<HmcUser xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/" schemaVersion="V1_0">
  <Metadata><Atom/></Metadata>
  <UserID>newop</UserID>
  <TaskRole>hmcoperator</TaskRole>
  <IsEnabled>true</IsEnabled>
</HmcUser>
"""


# ------------------------------------------------------------------ #
# XML builder unit tests
# ------------------------------------------------------------------ #

def test_build_hmc_user_document_create():
    xml = build_hmc_user_document(
        username="alice",
        taskrole="hmcoperator",
        password="S3cret!",
        description="Test user",
        pwage=90,
    )
    assert "<UserID" in xml and "alice" in xml
    assert "<TaskRole" in xml and "hmcoperator" in xml
    assert "<Password" in xml and "S3cret!" in xml
    assert "<Description" in xml and "Test user" in xml
    assert "<PasswordAgePolicy" in xml and ">90<" in xml
    assert "IsEnabled" not in xml  # not passed


def test_build_hmc_user_document_modify_enable():
    xml = build_hmc_user_document(enable=False)
    assert "<IsEnabled" in xml and ">false<" in xml
    assert "UserID" not in xml  # not passed


def test_build_hmc_user_document_minimal():
    xml = build_hmc_user_document(username="bob")
    assert "bob" in xml
    assert "HmcUser" in xml


# ------------------------------------------------------------------ #
# Client-level tests (respx-mocked HTTP)
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_list_hmc_users_all(mock_hmc):
    mock_hmc.get("/rest/api/web/HmcUser").mock(
        return_value=httpx.Response(200, text=USER_FEED)
    )
    async with HMCClient(make_config()) as hmc:
        xml = await hmc.list_hmc_users()
    assert "hscroot" in xml
    assert "operator1" in xml


@pytest.mark.asyncio
async def test_list_hmc_users_local_filter(mock_hmc):
    route = mock_hmc.get("/rest/api/web/HmcUser").mock(
        return_value=httpx.Response(200, text=USER_FEED)
    )
    async with HMCClient(make_config()) as hmc:
        await hmc.list_hmc_users("local")
    # Confirm the query param was appended
    assert "UserType=local" in str(route.calls.last.request.url)


@pytest.mark.asyncio
async def test_get_hmc_user(mock_hmc):
    mock_hmc.get("/rest/api/web/HmcUser/hscroot").mock(
        return_value=httpx.Response(200, text=USER_ENTRY)
    )
    async with HMCClient(make_config()) as hmc:
        xml = await hmc.get_hmc_user("hscroot")
    assert "hscroot" in xml
    assert "hmcsuperadmin" in xml


@pytest.mark.asyncio
async def test_create_hmc_user(mock_hmc):
    route = mock_hmc.post("/rest/api/web/HmcUser").mock(
        return_value=httpx.Response(201, text=CREATED_USER)
    )
    user_xml = build_hmc_user_document(
        username="newop", taskrole="hmcoperator", password="P@ss1"
    )
    async with HMCClient(make_config()) as hmc:
        xml = await hmc.create_hmc_user(user_xml)
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "newop" in body and "hmcoperator" in body
    assert "newop" in xml


@pytest.mark.asyncio
async def test_modify_hmc_user(mock_hmc):
    route = mock_hmc.post("/rest/api/web/HmcUser/operator1").mock(
        return_value=httpx.Response(200, text=USER_ENTRY)
    )
    user_xml = build_hmc_user_document(taskrole="hmcviewer", enable=False)
    async with HMCClient(make_config()) as hmc:
        await hmc.modify_hmc_user("operator1", user_xml)
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "hmcviewer" in body
    assert ">false<" in body


@pytest.mark.asyncio
async def test_delete_hmc_user(mock_hmc):
    route = mock_hmc.delete("/rest/api/web/HmcUser/operator1").mock(
        return_value=httpx.Response(204)
    )
    async with HMCClient(make_config()) as hmc:
        await hmc.delete_hmc_user("operator1")
    assert route.called


@pytest.mark.asyncio
async def test_web_get_error_raises(mock_hmc):
    mock_hmc.get("/rest/api/web/HmcUser/nobody").mock(
        return_value=httpx.Response(404, text="<error>not found</error>")
    )
    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError) as exc_info:
            await hmc.get_hmc_user("nobody")
    assert exc_info.value.status_code == 404


# ------------------------------------------------------------------ #
# Server-tool tests (parsed dict returns, not raw XML)
# ------------------------------------------------------------------ #

def _hmc_env(monkeypatch) -> None:
    """Set env vars so HMCConfig() succeeds inside the tool."""
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


def test_hmc_list_users_parses_feed(monkeypatch, mock_hmc):
    """hmc_list_users returns parsed dicts, one per account."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/web/HmcUser").mock(
        return_value=httpx.Response(200, text=USER_FEED)
    )
    result = hmc_list_users()
    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["Resource"]["UserID"] == "hscroot"
    assert result[1]["Resource"]["TaskRole"] == "hmcoperator"


def test_hmc_get_user_parses_entry(monkeypatch, mock_hmc):
    """hmc_get_user returns a single parsed resource dict."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/web/HmcUser/hscroot").mock(
        return_value=httpx.Response(200, text=USER_ENTRY)
    )
    result = hmc_get_user("hscroot")
    assert isinstance(result, dict)
    assert result["ResourceType"] == "HmcUser"
    assert result["Resource"]["UserID"] == "hscroot"


def test_hmc_get_user_empty_body_returns_none(monkeypatch, mock_hmc):
    """hmc_get_user returns None when the server returns no content."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/web/HmcUser/nobody").mock(
        return_value=httpx.Response(204)
    )
    assert hmc_get_user("nobody") is None


def test_hmc_create_user_returns_parsed_dict(monkeypatch, mock_hmc):
    """hmc_create_user returns the created resource dict."""
    _hmc_env(monkeypatch)
    mock_hmc.post("/rest/api/web/HmcUser").mock(
        return_value=httpx.Response(201, text=CREATED_USER)
    )
    result = hmc_create_user("newop", "hmcoperator", "P@ss1")
    assert isinstance(result, dict)
    assert result["Resource"]["UserID"] == "newop"


def test_hmc_modify_user_returns_parsed_dict(monkeypatch, mock_hmc):
    """hmc_modify_user returns the updated resource dict."""
    _hmc_env(monkeypatch)
    mock_hmc.post("/rest/api/web/HmcUser/operator1").mock(
        return_value=httpx.Response(200, text=USER_ENTRY)
    )
    result = hmc_modify_user("operator1", taskrole="hmcviewer", enable=False)
    assert isinstance(result, dict)
    assert result["Resource"]["TaskRole"] == "hmcsuperadmin"


def test_hmc_delete_user_returns_confirmation(monkeypatch, mock_hmc):
    """hmc_delete_user returns a confirmation string, not XML."""
    _hmc_env(monkeypatch)
    mock_hmc.delete("/rest/api/web/HmcUser/operator1").mock(
        return_value=httpx.Response(204)
    )
    assert hmc_delete_user("operator1") == "Deleted HMC user operator1"
