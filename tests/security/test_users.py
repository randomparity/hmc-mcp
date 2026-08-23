"""UOM UserProfile and role resource contracts."""

import httpx
import pytest

from conftest import make_config

from hmc_mcp.client import HMCClient
from hmc_mcp.documents import build_hmc_user_document

CONSOLE = "console-1"
PROFILE = "profile-1"

USER_FEED = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>urn:uuid:profile-1</id><title>UserProfile:alice</title>
<content type="application/vnd.ibm.powervm.uom+xml"><UserProfile
xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
<UserID>alice</UserID><AuthenticationType>Local</AuthenticationType>
</UserProfile></content></entry><entry><id>urn:uuid:profile-2</id>
<content><UserProfile xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
<UserID>directory</UserID><AuthenticationType>LDAP</AuthenticationType>
</UserProfile></content></entry></feed>"""

ROLE_FEED = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>urn:uuid:role-1</id><content><TaskRole
xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
<Name>operator</Name></TaskRole></content></entry></feed>"""


def test_user_profile_builder_uses_documented_fields_and_escapes() -> None:
    xml = build_hmc_user_document(
        user_id="a&b",
        authentication_type="Local",
        password="<&",
        associated_task_role="https://h/TaskRole/1?x=1&y=2",
        associated_resource_roles=["https://h/ResourceRole/2"],
        password_expiry=30,
        verify_session_timeout=True,
        idle_session_timeout=15,
        user_inactivity=60,
        minimum_password_age=1,
        allow_ssh_remote_access=False,
    )
    assert "UserProfile" in xml
    assert "HmcUser" not in xml
    assert "a&amp;b" in xml and "&lt;&amp;" in xml
    assert "AssociatedTaskRole" in xml and "AssociatedResourceRoles" in xml
    assert ">30</PasswordExpiry>" in xml
    assert ">true</VerifySessionTimeout>" in xml
    assert ">15</IdleSessionTimeout>" in xml
    assert ">60</UserInactivity>" in xml
    assert ">1</MinimumPasswordAge>" in xml
    assert ">false</AllowSSHRemoteAccess>" in xml


def test_user_profile_builder_rejects_unknown_authentication_type() -> None:
    with pytest.raises(ValueError, match="authentication_type"):
        build_hmc_user_document(authentication_type="radius")


@pytest.mark.asyncio
async def test_user_profile_crud_uses_nested_uom_paths(mock_hmc) -> None:
    collection = f"/rest/api/uom/ManagementConsole/{CONSOLE}/UserProfile"
    item = f"{collection}/{PROFILE}"
    list_route = mock_hmc.get(collection).mock(
        return_value=httpx.Response(200, text=USER_FEED)
    )
    get_route = mock_hmc.get(item).mock(
        return_value=httpx.Response(200, text=USER_FEED)
    )
    put_route = mock_hmc.put(collection).mock(
        return_value=httpx.Response(201, text=USER_FEED)
    )
    post_route = mock_hmc.post(item).mock(return_value=httpx.Response(200, text=""))
    delete_route = mock_hmc.delete(item).mock(return_value=httpx.Response(204))

    async with HMCClient(make_config()) as hmc:
        assert len(await hmc.list_hmc_users(CONSOLE)) == 2
        assert len(await hmc.list_hmc_users(CONSOLE, "ldap")) == 1
        assert await hmc.get_hmc_user(CONSOLE, PROFILE) is not None
        assert await hmc.create_hmc_user(CONSOLE, "<UserProfile/>") is not None
        assert await hmc.modify_hmc_user(CONSOLE, PROFILE, "<UserProfile/>") is None
        await hmc.delete_hmc_user(CONSOLE, PROFILE)

    assert list_route.call_count == 2
    assert (
        get_route.called
        and put_route.called
        and post_route.called
        and delete_route.called
    )
    assert "type=UserProfile" in put_route.calls[0].request.headers["content-type"]


@pytest.mark.asyncio
async def test_user_filter_rejects_unknown_value_before_io(mock_hmc) -> None:
    async with HMCClient(make_config()) as hmc:
        with pytest.raises(ValueError, match="authentication_type"):
            await hmc.list_hmc_users(CONSOLE, "bogus")
    assert all("UserProfile" not in str(call.request.url) for call in mock_hmc.calls)


@pytest.mark.asyncio
async def test_role_lists_use_documented_child_resources(mock_hmc) -> None:
    task = mock_hmc.get(f"/rest/api/uom/ManagementConsole/{CONSOLE}/TaskRole").mock(
        return_value=httpx.Response(200, text=ROLE_FEED)
    )
    resource = mock_hmc.get(
        f"/rest/api/uom/ManagementConsole/{CONSOLE}/ResourceRole"
    ).mock(return_value=httpx.Response(204))
    async with HMCClient(make_config()) as hmc:
        assert len(await hmc.list_task_roles(CONSOLE)) == 1
        assert await hmc.list_resource_roles(CONSOLE) == []
    assert task.called and resource.called


@pytest.mark.asyncio
async def test_user_path_identifiers_are_encoded_as_single_segments(mock_hmc) -> None:
    route = mock_hmc.get(
        "/rest/api/uom/ManagementConsole/console%2Fother/UserProfile/profile%2Fother"
    ).mock(return_value=httpx.Response(204))
    async with HMCClient(make_config()) as hmc:
        assert await hmc.get_hmc_user("console/other", "profile/other") is None
    assert route.called
