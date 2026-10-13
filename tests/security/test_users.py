"""UOM UserProfile and role resource contracts."""

import httpx
import pytest
from conftest import live_fixture, make_config

from hmcpctl.client.core import HMCClient
from hmcpctl.documents.access import build_hmc_user_document
from hmcpctl.xmlutil import leaf_text

CONSOLE = "console-1"
PROFILE = "profile-1"

USER_FEED = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>urn:uuid:profile-1</id><title>UserProfile:alice</title>
<content type="application/vnd.ibm.powervm.uom+xml"><UserProfile
xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
<UserID>alice</UserID><AuthenticationType ksv="V1_17_0" kb="CUD" kxe="false">local</AuthenticationType>
</UserProfile></content></entry><entry><id>urn:uuid:profile-2</id>
<content><UserProfile xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
<UserID>directory</UserID><AuthenticationType ksv="V1_17_0" kb="CUD" kxe="false">ldap</AuthenticationType>
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
        associated_task_role="role&1",
        associated_resource_roles=["role<2"],
        password_expiry=30,
        verify_session_timeout=15,
        idle_session_timeout=15,
        user_inactivity=60,
        minimum_password_age=1,
        allow_ssh_remote_access=False,
    )
    assert "UserProfile" in xml
    assert "HmcUser" not in xml
    assert "a&amp;b" in xml and "&lt;&amp;" in xml
    assert ">role&amp;1<" in xml and ">role&lt;2<" in xml
    assert "AssociatedTaskRole" in xml and "AssociatedResourceRoles" in xml
    assert ">30</PasswordExpiry>" in xml
    assert ">15</VerifySessionTimeout>" in xml
    assert ">15</IdleSessionTimeout>" in xml
    assert ">60</UserInactivity>" in xml
    assert ">1</MinimumPasswordAge>" in xml
    assert ">false</AllowSSHRemoteAccess>" in xml


# The UserProfile.Group sequence of the V10R3 schema, GET
# /rest/api/web/schema/UserProfile.xsd (private capture 2026-10-13-q1414). Every
# element is optional, but those present must come in this order: a create that
# sent RemoteUserID after AssociatedTaskRole failed REST0001 (#1414).
_SCHEMA_SEQUENCE = (
    "UserID",
    "UserProfileUUID",
    "UserDescription",
    "IsPredefinedUser",
    "AuthenticationType",
    "UserProfilePassword",
    "PasswordExpiry",
    "RemoteUserID",
    "AssociatedTaskRole",
    "AssociatedResourceRoles",
    "SessionTimeout",
    "VerifySessionTimeout",
    "IdleSessionTimeout",
    "UserInactivity",
    "MinimumPasswordAge",
    "AllowWebRemoteAccess",
    "AllowSSHRemoteAccess",
    "IsPasswordPolicyEnabled",
)


def _profile_children(xml: str) -> list[str]:
    from xml.etree import ElementTree as ET

    root = ET.fromstring(xml)
    (profile,) = (e for e in root.iter() if e.tag.endswith("}UserProfile"))
    return [child.tag.rsplit("}", 1)[-1] for child in profile]


@pytest.mark.parametrize("fixture", ["rest-user-profile", "rest-user-profile-kerberos"])
def test_captured_user_profiles_follow_the_schema_sequence(fixture) -> None:
    """Both captured profiles, local and Kerberos, serve their children in the
    schema's order, so the sequence above is the one the HMC writes too."""
    served = _profile_children(live_fixture(fixture)["body"])
    assert served[0] == "Metadata"
    assert served[1:] == [name for name in _SCHEMA_SEQUENCE if name in served]


def test_user_profile_builder_places_remote_user_id_where_the_hmc_expects_it():
    """The refused create sent RemoteUserID after AssociatedTaskRole; at that point
    the HMC expected only elements the schema puts after AssociatedTaskRole."""
    message = live_fixture("rest-user-create-remote-user-id-refused")["body"]
    expected = _SCHEMA_SEQUENCE[_SCHEMA_SEQUENCE.index("AssociatedTaskRole") + 1 :]
    assert "REST0001" in message and "RemoteUserID" in message
    assert all(f":{name}" in message for name in expected)

    children = _profile_children(
        build_hmc_user_document(
            user_id="u",
            authentication_type="Kerberos",
            associated_task_role="hmcviewer",
            remote_user_id="primary/instance@EXAMPLE.TEST",
        )
    )
    assert children.index("RemoteUserID") < children.index("AssociatedTaskRole")


def test_user_profile_builder_follows_the_documented_shape() -> None:
    """ADR 0202: roles are names, and children follow the schema's order."""
    from xml.etree import ElementTree as ET

    xml = build_hmc_user_document(
        user_id="u",
        authentication_type="Local",
        password="p",
        description="d",
        associated_task_role="hmcviewer",
        associated_resource_roles=["AllSystemResources"],
        password_expiry=30,
        session_timeout=1,
        verify_session_timeout=15,
        idle_session_timeout=2,
        user_inactivity=3,
        minimum_password_age=4,
        allow_web_remote_access=False,
        allow_ssh_remote_access=False,
        remote_user_id="r",
    )
    children = [child.tag.rsplit("}", 1)[-1] for child in ET.fromstring(xml)]
    sent = [name for name in _SCHEMA_SEQUENCE if name in children]
    assert children == ["Metadata", *sent]
    assert len(sent) == 15
    assert "href=" not in xml
    assert (
        '<AssociatedTaskRole ksv="V1_17_0" kb="CUD" kxe="false">hmcviewer</AssociatedTaskRole>'
        in xml
    )
    assert (
        '<AssociatedResourceRoles ksv="V1_17_0" kb="CUD" kxe="false" schemaVersion="V1_0">'
        "<Metadata><Atom/></Metadata>"
        '<AssociatedResourceRole ksv="V1_17_0" kb="CUD" kxe="false">AllSystemResources'
        "</AssociatedResourceRole></AssociatedResourceRoles>"
    ) in xml


def test_user_profile_builder_marks_each_element_as_the_hmc_lists_it() -> None:
    """V10R3 refused a create marking UserID ``kb="CUR"`` with REST0001, enumeration
    ``[COR]``, then one without ``ksv`` on UserID (#1375); the HMC names only the
    first refused facet, so every element's facets are pinned to the captured
    profile."""
    from xml.etree import ElementTree as ET

    def facets_by_tag(xml: str) -> dict[str, tuple[str | None, str | None]]:
        return {
            element.tag.rsplit("}", 1)[-1]: (
                element.attrib.get("ksv"),
                element.attrib.get("kb"),
            )
            for element in ET.fromstring(xml).iter()
            if "kb" in element.attrib
        }

    captured = facets_by_tag(live_fixture("rest-user-profile")["body"])
    captured.update(facets_by_tag(live_fixture("rest-user-profile-kerberos")["body"]))
    built = facets_by_tag(
        build_hmc_user_document(
            user_id="u",
            authentication_type="Local",
            password="p",
            description="d",
            associated_task_role="hmcviewer",
            associated_resource_roles=["AllSystemResources"],
            password_expiry=30,
            session_timeout=1,
            verify_session_timeout=15,
            idle_session_timeout=2,
            user_inactivity=3,
            minimum_password_age=4,
            allow_web_remote_access=False,
            allow_ssh_remote_access=False,
            remote_user_id="r",
        )
    )

    # RemoteUserID's facets come from the Kerberos capture (#1414); a local
    # profile does not serve the element.
    assert built["RemoteUserID"] == ("V1_17_0", "CUD")
    assert built == {tag: captured[tag] for tag in built}
    assert len(built) == 16


def test_user_profile_builder_sends_the_empty_password_element_as_the_hmc_serves_it():
    """V10R3 refuses a modify without UserProfilePassword (REST0344, #1409) and never
    returns the value, so an unchanged password is sent as the empty element the GET
    serves: same facets, no text."""
    from xml.etree import ElementTree as ET

    def password_element(xml: str) -> ET.Element:
        (element,) = (
            e
            for e in ET.fromstring(xml).iter()
            if e.tag.endswith("}UserProfilePassword")
        )
        return element

    xml = build_hmc_user_document(user_id="u")
    served = password_element(live_fixture("rest-user-profile")["body"])
    sent = password_element(xml)

    assert sent.attrib == served.attrib
    assert sent.text is None and served.text is None
    assert '<UserProfilePassword ksv="V1_17_0" kb="CUD" kxe="false"/>' in xml


@pytest.mark.parametrize(
    ("requested", "wire"),
    [("Local", "local"), ("LDAP", "ldap"), ("Kerberos", "kerberos")],
)
def test_user_profile_builder_sends_the_hmcs_lowercase_authentication_type(
    requested, wire
) -> None:
    """V10R3 lists every profile's AuthenticationType in lower case, and refused a
    create carrying ``Local`` with REST0001 (2026-10-06 users arm, #632)."""
    xml = build_hmc_user_document(authentication_type=requested)

    assert (
        f'<AuthenticationType ksv="V1_17_0" kb="CUD" kxe="false">{wire}</AuthenticationType>'
        in xml
    )


def test_user_profile_builder_rejects_unknown_authentication_type() -> None:
    with pytest.raises(ValueError, match="authentication_type"):
        build_hmc_user_document(authentication_type="radius")


def test_user_profile_builder_distinguishes_omitted_and_cleared_roles() -> None:
    omitted = build_hmc_user_document()
    cleared = build_hmc_user_document(
        associated_task_role="", associated_resource_roles=[]
    )
    assert "AssociatedTaskRole" not in omitted
    assert "AssociatedResourceRoles" not in omitted
    assert '<AssociatedTaskRole ksv="V1_17_0" kb="CUD" kxe="false"/>' in cleared
    assert '<AssociatedResourceRoles ksv="V1_17_0" kb="CUD" kxe="false"/>' in cleared


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
async def test_user_read_strips_the_newline_the_hmc_appends_to_remote_user_id(
    mock_hmc,
) -> None:
    """ADR 0076 (#1538): the HMC serves a Kerberos principal with a trailing newline
    the create did not send; the shared parser strips leaf text, so a user read
    returns the principal without it."""
    body = live_fixture("rest-user-profile-kerberos")["body"]
    assert "@example.test\n</RemoteUserID>" in body
    mock_hmc.get(
        f"/rest/api/uom/ManagementConsole/{CONSOLE}/UserProfile/{PROFILE}"
    ).mock(return_value=httpx.Response(200, text=body))
    async with HMCClient(make_config()) as hmc:
        profile = await hmc.get_hmc_user(CONSOLE, PROFILE)

    remote_user_id = leaf_text(profile["Resource"]["RemoteUserID"])
    assert remote_user_id == "hmcpctl-test/user@example.test"


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
