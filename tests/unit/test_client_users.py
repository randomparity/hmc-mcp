"""Direct contracts for documented user-client path and response handling."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote

import httpx
import pytest
from conftest import make_config
from test_request_path_safety import _recording_client

from hmc_mcp.client.client_contracts import _MAX_UOM_TYPE_LENGTH
from hmc_mcp.client.client_users import UsersMixin
from hmc_mcp.client.core import HMCClient


def test_user_child_path_escapes_console_identifiers():
    assert UsersMixin._child_path("console/a", "UserProfile") == (
        "/rest/api/uom/ManagementConsole/console%2Fa/UserProfile"
    )


@pytest.mark.asyncio
async def test_list_users_filters_authentication_type_and_rejects_unknown_values():
    client = SimpleNamespace(
        _get=AsyncMock(
            return_value=(
                "<feed xmlns='http://www.w3.org/2005/Atom'><entry><content>"
                "<UserProfile><AuthenticationType>LDAP</AuthenticationType>"
                "</UserProfile></content></entry></feed>"
            )
        ),
        _child_path=UsersMixin._child_path,
        _entries=UsersMixin._entries,
    )

    users = await UsersMixin.list_hmc_users(client, "console/a", "ldap")

    assert users[0]["Resource"]["AuthenticationType"] == "LDAP"
    client._get.assert_awaited_once_with(
        "/rest/api/uom/ManagementConsole/console%2Fa/UserProfile", "UserProfile"
    )
    with pytest.raises(ValueError, match="Invalid authentication_type"):
        await UsersMixin.list_hmc_users(client, "console", "radius")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "child_type",
    [
        # One grammar case and one length case: enough to prove `_child_path`
        # routes through the predicate. The grammar's own table lives with the
        # predicate, in tests/unit/test_request_path_safety.py, and restating it
        # here would give one rule two owners.
        "UserProfile?group=x",
        "A" * (_MAX_UOM_TYPE_LENGTH + 1),
    ],
)
def test_user_child_path_refuses_a_type_outside_the_grammar(child_type):
    """A future non-literal caller cannot reach the HMC with an unvalidated type.

    The AST drift tests that would catch such a caller walk `core.py` only and
    cannot see this module, so the guarantee is this runtime refusal rather
    than a test-time observation (ADR 0147).
    """
    with pytest.raises(ValueError, match="child_type must be an HMC resource type name"):
        UsersMixin._child_path("console-1", child_type)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get_hmc_user", "modify_hmc_user", "delete_hmc_user"])
@pytest.mark.parametrize("length", [257, 20_000])
async def test_profile_identifier_refused_before_transport(method, length):
    client, requested = _recording_client()
    value = "B" * length
    args = ("<UserProfile/>",) if method == "modify_hmc_user" else ()
    try:
        with pytest.raises(ValueError) as exc:
            await getattr(client, method)("console", value, *args)
        message = str(exc.value)
        assert "user_profile_uuid" in message and str(length) in message
        assert "256" in message and value not in message
        assert requested == []
    finally:
        await client._http.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get_hmc_user", "modify_hmc_user", "delete_hmc_user"])
async def test_profile_identifiers_accept_unicode_boundary(method, mock_hmc):
    value = "\U0001f600" * 256
    args = ("<UserProfile/>",) if method == "modify_hmc_user" else ()
    encoded = quote(value, safe="")
    path = f"/rest/api/uom/ManagementConsole/{encoded}/UserProfile/{encoded}"
    verb = {"get_hmc_user": "GET", "modify_hmc_user": "POST", "delete_hmc_user": "DELETE"}
    route = mock_hmc.route(method=verb[method], url=f"https://hmc.test:443{path}").mock(
        return_value=httpx.Response(200)
    )
    async with HMCClient(make_config()) as client:
        await getattr(client, method)(value, value, *args)
    assert route.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get_remote_access", "configure_remote_access"])
@pytest.mark.parametrize("length", [257, 20_000])
async def test_remote_access_identifier_refused_before_transport(method, length):
    client, requested = _recording_client()
    value = "B" * length
    args = ({"LdapEnabled": False}, []) if method == "configure_remote_access" else ()
    try:
        with pytest.raises(ValueError) as exc:
            await getattr(client, method)(value, *args)
        message = str(exc.value)
        assert "console_uuid" in message and str(length) in message
        assert "256" in message and value not in message
        assert requested == []
    finally:
        await client._http.aclose()


@pytest.mark.asyncio
async def test_remote_access_unicode_boundary_keeps_query_and_update(mock_hmc):
    value = "\U0001f600" * 256
    path = f"/rest/api/uom/ManagementConsole/{quote(value, safe='')}?group=RemoteAccess"
    document = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><content>'
        '<ManagementConsole xmlns=""><LdapEnabled>true</LdapEnabled>'
        '</ManagementConsole></content></entry></feed>'
    )
    get_route = mock_hmc.get(path).mock(return_value=httpx.Response(200, text=document))
    post_route = mock_hmc.post(path).mock(return_value=httpx.Response(200))
    async with HMCClient(make_config()) as client:
        result = await client.get_remote_access(value)
        await client.configure_remote_access(value, {"LdapEnabled": False}, [])
    assert result["Resource"]["LdapEnabled"] == "true"
    assert get_route.call_count == 2
    assert post_route.call_count == 1
    assert b">false</LdapEnabled>" in post_route.calls[0].request.content
