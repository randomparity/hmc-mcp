"""ManagementConsole RemoteAccess LDAP and Kerberos contracts."""

import httpx
import pytest
from conftest import make_config
from defusedxml import ElementTree as DET

from hmc_mcp.client.core import HMCClient
from hmc_mcp.documents import build_remote_access_document
from hmc_mcp.errors import HMCError

CONSOLE = "console-1"
PATH = f"/rest/api/uom/ManagementConsole/{CONSOLE}?group=RemoteAccess"
REMOTE_ACCESS = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>urn:uuid:console-1</id>
<content><ManagementConsole xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
<LdapEnabled>true</LdapEnabled><PrimaryLdapUri>ldaps://directory</PrimaryLdapUri>
<KerberosAuthenticationEnabled>false</KerberosAuthenticationEnabled>
</ManagementConsole></content></entry></feed>"""


def test_remote_access_builder_sets_clears_and_escapes() -> None:
    xml = build_remote_access_document(
        {"LdapEnabled": True, "BindPassword": "<&", "ClockSkew": 300},
        ["SecondaryLdapUri"],
    )
    assert "ManagementConsole" in xml and "HmcLdapServer" not in xml
    assert ">true</LdapEnabled>" in xml
    assert "&lt;&amp;" in xml
    assert '<SecondaryLdapUri kb="CUR" kxe="false"/>' in xml


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
        ({"Realm": "x"}, ["Realm"], "both set and cleared"),
    ],
)
def test_remote_access_builder_rejects_invalid_updates(values, clears, message) -> None:
    with pytest.raises(ValueError, match=message):
        build_remote_access_document(values, clears)


@pytest.mark.asyncio
async def test_remote_access_get_merge_and_post_preserve_unmodified_fields(mock_hmc) -> None:
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
    assert result["Resource"]["PrimaryLdapUri"] == "ldaps://directory"
    assert updated is not None
    assert get_route.called and post_route.called
    assert (
        get_route.calls[0].request.headers["accept"]
        == "application/vnd.ibm.powervm.web+xml; type=ManagementConsole"
    )
    assert (
        "type=ManagementConsole" in post_route.calls[0].request.headers["content-type"]
    )
    posted = DET.fromstring(post_route.calls[0].request.content)
    fields = {node.tag.rsplit("}", 1)[-1]: node.text for node in posted}
    assert fields["LdapEnabled"] == "false"
    assert fields["PrimaryLdapUri"] == "ldaps://directory"
    assert fields["KerberosAuthenticationEnabled"] is None


@pytest.mark.asyncio
async def test_remote_access_empty_responses_are_none(mock_hmc) -> None:
    mock_hmc.get(PATH).mock(return_value=httpx.Response(200, text=REMOTE_ACCESS))
    mock_hmc.post(PATH).mock(return_value=httpx.Response(202, text=""))
    async with HMCClient(make_config()) as hmc:
        assert await hmc.configure_remote_access(CONSOLE, {"LdapEnabled": True}, []) is None


@pytest.mark.asyncio
async def test_remote_access_get_failure_does_not_post(mock_hmc) -> None:
    get_route = mock_hmc.get(PATH).mock(return_value=httpx.Response(503, text="down"))
    post_route = mock_hmc.post(PATH).mock(return_value=httpx.Response(200))
    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError, match="GET"):
            await hmc.configure_remote_access(CONSOLE, {"LdapEnabled": True}, [])
    assert get_route.called
    assert not post_route.called
