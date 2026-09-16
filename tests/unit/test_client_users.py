"""Direct contracts for documented user-client path and response handling."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hmc_mcp.client.client_contracts import _MAX_UOM_TYPE_LENGTH
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
