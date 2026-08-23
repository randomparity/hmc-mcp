"""ManagementConsole RemoteAccess LDAP and Kerberos contracts."""

import httpx
import pytest

from conftest import make_config

from hmc_mcp.client import HMCClient
from hmc_mcp.documents import build_remote_access_document

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
async def test_remote_access_get_and_post_use_grouped_uom_path(mock_hmc) -> None:
    get_route = mock_hmc.get(PATH).mock(
        return_value=httpx.Response(200, text=REMOTE_ACCESS)
    )
    post_route = mock_hmc.post(PATH).mock(
        return_value=httpx.Response(200, text=REMOTE_ACCESS)
    )
    async with HMCClient(make_config()) as hmc:
        result = await hmc.get_remote_access(CONSOLE)
        updated = await hmc.configure_remote_access(
            CONSOLE, build_remote_access_document({"LdapEnabled": True})
        )
    assert result["Resource"]["PrimaryLdapUri"] == "ldaps://directory"
    assert updated is not None
    assert get_route.called and post_route.called
    assert (
        "type=ManagementConsole" in post_route.calls[0].request.headers["content-type"]
    )


@pytest.mark.asyncio
async def test_remote_access_empty_responses_are_none(mock_hmc) -> None:
    mock_hmc.get(PATH).mock(return_value=httpx.Response(204))
    mock_hmc.post(PATH).mock(return_value=httpx.Response(202, text=""))
    async with HMCClient(make_config()) as hmc:
        assert await hmc.get_remote_access(CONSOLE) is None
        assert (
            await hmc.configure_remote_access(CONSOLE, "<ManagementConsole/>") is None
        )
