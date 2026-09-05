"""Direct contracts for documented user-client path and response handling."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hmc_mcp.client.client_users import UsersMixin


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
