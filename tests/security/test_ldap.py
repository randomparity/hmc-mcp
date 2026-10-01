"""ManagementConsole RemoteAccess LDAP and Kerberos contracts."""

import httpx
import pytest
from conftest import live_fixture, make_config
from defusedxml import ElementTree as DET

from hmcpctl.client.core import HMCClient
from hmcpctl.documents import build_remote_access_document
from hmcpctl.errors import HMCError

CONSOLE = "console-1"
PATH = f"/rest/api/uom/ManagementConsole/{CONSOLE}?group=RemoteAccess"
# The captured V10R3 ManagementConsole feed. Its RemoteAccess fields sit in
# `LdapConfiguration` and `KerberosConfiguration`, as the reference documents them
# (docs/refs/hmc-rest-api-p10/user-management/199-ldap.md:98-118, 198-kerberos.md:103-124).
REMOTE_ACCESS = live_fixture("rest-management-console")["body"]
UOM_CONSOLE = "application/vnd.ibm.powervm.uom+xml; type=ManagementConsole"


def _child(node, *names: str):
    for name in names:
        node = next(child for child in node if child.tag.rsplit("}", 1)[-1] == name)
    return node


def _names(node) -> set[str]:
    return {child.tag.rsplit("}", 1)[-1] for child in node}


def test_remote_access_builder_sets_clears_and_escapes() -> None:
    xml = build_remote_access_document(
        {"LdapEnabled": True, "BindPassword": "<&", "ClockSkew": 300},
        ["SecondaryLdapUri"],
    )
    assert "ManagementConsole" in xml and "HmcLdapServer" not in xml
    assert "&lt;&amp;" in xml
    document = DET.fromstring(xml)
    assert _child(document, "LdapConfiguration", "LdapEnabled").text == "true"
    assert _child(document, "LdapConfiguration", "SecondaryLdapUri").text is None
    assert _child(document, "KerberosConfiguration", "ClockSkew").text == "300"
    assert "LdapEnabled" not in _names(document)


def test_remote_access_builder_covers_documented_kerberos_names() -> None:
    xml = build_remote_access_document(
        {"KerberosEnabled": True, "kerberosRemoteUserId": "directory-user"}
    )
    assert ">true</KerberosEnabled>" in xml
    assert ">directory-user</kerberosRemoteUserId>" in xml


@pytest.mark.parametrize(
    ("values", "clears", "message"),
    [
        ({}, [], "at least one"),
        ({"NoSuchField": "x"}, [], "Unknown"),
        ({"DefaultRealm": "x"}, ["DefaultRealm"], "both set and cleared"),
        # Nested KDC entries, not scalar fields (198-kerberos.md:112-121).
        ({"Realm": "x"}, [], "Unknown"),
        ({}, ["RealmConfig"], "Unknown"),
    ],
)
def test_remote_access_builder_rejects_invalid_updates(values, clears, message) -> None:
    with pytest.raises(ValueError, match=message):
        build_remote_access_document(values, clears)


@pytest.mark.asyncio
async def test_remote_access_get_merge_and_post_preserve_unmodified_fields(
    mock_hmc,
) -> None:
    get_route = mock_hmc.get(PATH).mock(
        return_value=httpx.Response(200, text=REMOTE_ACCESS)
    )
    post_route = mock_hmc.post(PATH).mock(
        return_value=httpx.Response(200, text=REMOTE_ACCESS)
    )
    async with HMCClient(make_config()) as hmc:
        result = await hmc.get_remote_access(CONSOLE)
        updated = await hmc.configure_remote_access(
            CONSOLE, {"LdapEnabled": False}, ["KerberosAuthenticationEnabled"]
        )
    assert result["Resource"]["LdapConfiguration"]["SearchScope"]["text"] == "one"
    assert updated is not None
    assert get_route.called and post_route.called
    assert get_route.calls[0].request.headers["accept"] == UOM_CONSOLE
    assert (
        "type=ManagementConsole" in post_route.calls[0].request.headers["content-type"]
    )
    posted = DET.fromstring(post_route.calls[0].request.content)
    ldap = _child(posted, "LdapConfiguration")
    assert _child(ldap, "LdapEnabled").text == "false"
    assert _child(ldap, "SearchScope").text == "one"
    assert _child(ldap, "KerberosAuthenticationEnabled").text is None
    assert not _names(posted) & {"LdapEnabled", "KerberosAuthenticationEnabled"}
    assert sum(child.tag.endswith("LdapConfiguration") for child in posted) == 1


@pytest.mark.asyncio
async def test_remote_access_merge_places_kerberos_fields_in_their_container(
    mock_hmc,
) -> None:
    mock_hmc.get(PATH).mock(return_value=httpx.Response(200, text=REMOTE_ACCESS))
    post_route = mock_hmc.post(PATH).mock(return_value=httpx.Response(202, text=""))
    async with HMCClient(make_config()) as hmc:
        await hmc.configure_remote_access(
            CONSOLE, {"KerberosEnabled": True, "ClockSkew": 120}, []
        )
    posted = DET.fromstring(post_route.calls[0].request.content)
    kerberos = _child(posted, "KerberosConfiguration")
    assert _child(kerberos, "KerberosEnabled").text == "true"
    assert _child(kerberos, "ClockSkew").text == "120"
    assert not _names(posted) & {"KerberosEnabled", "ClockSkew"}


@pytest.mark.asyncio
async def test_remote_access_read_avoids_the_web_media_type_v10r3_refuses(
    mock_hmc,
) -> None:
    """V10R3 answers the documented web+xml Accept with an HTML 406 page."""
    refused = live_fixture("rest-remote-access-406")

    def answer(request: httpx.Request) -> httpx.Response:
        if "web+xml" in request.headers["accept"]:
            return httpx.Response(refused["status"], text=refused["body"])
        return httpx.Response(200, text=REMOTE_ACCESS)

    mock_hmc.get(PATH).mock(side_effect=answer)
    async with HMCClient(make_config()) as hmc:
        result = await hmc.get_remote_access(CONSOLE)
    assert result is not None
    assert result["Resource"]["KerberosConfiguration"]["KerberosEnabled"]["text"] == (
        "false"
    )


@pytest.mark.asyncio
async def test_remote_access_empty_responses_are_none(mock_hmc) -> None:
    mock_hmc.get(PATH).mock(return_value=httpx.Response(200, text=REMOTE_ACCESS))
    mock_hmc.post(PATH).mock(return_value=httpx.Response(202, text=""))
    async with HMCClient(make_config()) as hmc:
        assert (
            await hmc.configure_remote_access(CONSOLE, {"LdapEnabled": True}, [])
            is None
        )


@pytest.mark.asyncio
async def test_remote_access_get_failure_does_not_post(mock_hmc) -> None:
    get_route = mock_hmc.get(PATH).mock(return_value=httpx.Response(503, text="down"))
    post_route = mock_hmc.post(PATH).mock(return_value=httpx.Response(200))
    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError, match="GET"):
            await hmc.configure_remote_access(CONSOLE, {"LdapEnabled": True}, [])
    assert get_route.called
    assert not post_route.called


@pytest.mark.asyncio
async def test_remote_access_empty_get_is_appliance_error(mock_hmc) -> None:
    get_route = mock_hmc.get(PATH).mock(return_value=httpx.Response(200, text=""))
    post_route = mock_hmc.post(PATH).mock(return_value=httpx.Response(200))
    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError) as exc_info:
            await hmc.configure_remote_access(CONSOLE, {"LdapEnabled": True}, [])
    assert PATH in str(exc_info.value)
    assert "(HTTP 200)" in str(exc_info.value)
    assert exc_info.value.status_code == 200
    assert exc_info.value.body is None
    assert get_route.called
    assert not post_route.called
