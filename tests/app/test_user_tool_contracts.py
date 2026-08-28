"""Public user-tool documentation contracts."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hmc_mcp.server_tools import users as server_users


def _client_context(client: MagicMock) -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


def test_nullable_user_mutations_document_empty_responses() -> None:
    nullable_mutations = (
        server_users.hmc_create_user,
        server_users.hmc_modify_user,
        server_users.hmc_configure_remote_access,
    )

    for handler in nullable_mutations:
        assert handler.__doc__ is not None
        normalized_doc = " ".join(handler.__doc__.split())
        assert "None" in normalized_doc or "partial" in normalized_doc


def test_create_user_tool_forwards_identifiers_and_optional_fields() -> None:
    client = MagicMock()
    context = _client_context(client)
    operation = AsyncMock(return_value={"Resource": {"UserID": "alice"}})
    resource_roles = ["/roles/operators", "/roles/storage"]

    with (
        patch.object(server_users, "client_from_env", return_value=context) as factory,
        patch.object(server_users, "create_user", operation),
    ):
        result = server_users.hmc_create_user(
            "console-1",
            "alice",
            "secret",
            "LDAP",
            description="database operator",
            associated_task_role="/roles/task-operator",
            associated_resource_roles=resource_roles,
            password_expiry=30,
            session_timeout=60,
            verify_session_timeout=False,
            idle_session_timeout=15,
            user_inactivity=90,
            minimum_password_age=2,
            allow_web_remote_access=True,
            allow_ssh_remote_access=False,
            remote_user_id="directory-alice",
            profile="lab",
        )

    factory.assert_called_once_with("lab")
    operation.assert_awaited_once_with(
        client,
        "console-1",
        "alice",
        "secret",
        "LDAP",
        description="database operator",
        associated_task_role="/roles/task-operator",
        associated_resource_roles=resource_roles,
        password_expiry=30,
        session_timeout=60,
        verify_session_timeout=False,
        idle_session_timeout=15,
        user_inactivity=90,
        minimum_password_age=2,
        allow_web_remote_access=True,
        allow_ssh_remote_access=False,
        remote_user_id="directory-alice",
    )
    context.__aexit__.assert_awaited_once()
    assert result == {"Resource": {"UserID": "alice"}}


def test_modify_user_tool_preserves_explicit_clear_values() -> None:
    client = MagicMock()
    context = _client_context(client)
    operation = AsyncMock(return_value=None)

    with (
        patch.object(server_users, "client_from_env", return_value=context),
        patch.object(server_users, "modify_user", operation),
    ):
        result = server_users.hmc_modify_user(
            "console-1",
            "profile-1",
            password="replacement",  # pragma: allowlist secret -- synthetic fixture
            description="",
            authentication_type="Kerberos",
            associated_task_role="",
            associated_resource_roles=[],
            password_expiry=0,
            session_timeout=0,
            verify_session_timeout=False,
            idle_session_timeout=0,
            user_inactivity=0,
            minimum_password_age=0,
            allow_web_remote_access=False,
            allow_ssh_remote_access=False,
            remote_user_id="",
        )

    operation.assert_awaited_once_with(
        client,
        "console-1",
        "profile-1",
        authentication_type="Kerberos",
        password="replacement",  # pragma: allowlist secret -- synthetic fixture
        description="",
        associated_task_role="",
        associated_resource_roles=[],
        password_expiry=0,
        session_timeout=0,
        verify_session_timeout=False,
        idle_session_timeout=0,
        user_inactivity=0,
        minimum_password_age=0,
        allow_web_remote_access=False,
        allow_ssh_remote_access=False,
        remote_user_id="",
    )
    context.__aexit__.assert_awaited_once()
    assert result is None


def test_delete_user_tool_returns_identified_confirmation() -> None:
    client = MagicMock()
    context = _client_context(client)
    operation = AsyncMock(return_value=None)

    with (
        patch.object(server_users, "client_from_env", return_value=context),
        patch.object(server_users, "delete_user", operation),
    ):
        result = server_users.hmc_delete_user(
            "console-1", "profile-1", profile="lab"
        )

    operation.assert_awaited_once_with(client, "console-1", "profile-1")
    context.__aexit__.assert_awaited_once()
    assert result == "Deleted HMC user profile profile-1"


@pytest.mark.parametrize(
    ("values", "clear_fields"),
    [
        ({"LdapEnabled": True, "LdapServer": "ldap.example.test"}, None),
        (None, ["LdapServer", "KerberosRealm"]),
        ({"LdapEnabled": False}, ["LdapServer"]),
    ],
)
def test_remote_access_tool_preserves_value_and_clear_semantics(
    values: dict[str, str | int | bool] | None,
    clear_fields: list[str] | None,
) -> None:
    client = MagicMock()
    context = _client_context(client)
    operation = AsyncMock(return_value={"Resource": {"LdapEnabled": False}})

    with (
        patch.object(server_users, "client_from_env", return_value=context) as factory,
        patch.object(server_users, "configure_remote_access", operation),
    ):
        result = server_users.hmc_configure_remote_access(
            "console-1", values, clear_fields, profile="security"
        )

    factory.assert_called_once_with("security")
    operation.assert_awaited_once_with(client, "console-1", values, clear_fields)
    context.__aexit__.assert_awaited_once()
    assert result == {"Resource": {"LdapEnabled": False}}
