"""MCP tools for documented HMC user, role, and remote-access resources."""

from __future__ import annotations

from typing import Any

from .._app import run_sync
from ..client.client_users import AuthenticationFilter
from ..client.client_factory import client_from_env
from ..documents import AuthenticationType
from ..operations.users import (
    _configure_remote_access,
    _create_user,
    _delete_user,
    _modify_user,
)
from ..tool_registry import tool_module

tool, register_tools, tool_security = tool_module()


@tool(effect="read", operation="user.list", target_kind="console")
def hmc_list_users(
    console_uuid: str,
    authentication_type: AuthenticationFilter = "all",
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """List UserProfile children of a management console.

    Obtain ``console_uuid`` from ``hmc_console_info``. The optional filter is
    ``local``, ``ldap``, ``kerberos``, or ``all``.

    Args:
        console_uuid: ManagementConsole UUID from ``hmc_console_info``.
        authentication_type: Authentication source filter.
        profile: TOML profile name, or the environment default when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.list_hmc_users(console_uuid, authentication_type)

    return run_sync(_go)


@tool(
    effect="read",
    operation="user.get",
    target_kind="user",
    extra_targets=(("user", "user_profile_uuid"),),
)
def hmc_get_user(
    console_uuid: str,
    user_profile_uuid: str,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Get a UserProfile by its management-console and profile UUIDs.

    Args:
        console_uuid: ManagementConsole UUID from ``hmc_console_info``.
        user_profile_uuid: UserProfile UUID returned by ``hmc_list_users``.
        profile: TOML profile name, or the environment default when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.get_hmc_user(console_uuid, user_profile_uuid)

    return run_sync(_go)


@tool(
    effect="mutate",
    operation="user.create",
    target_kind="user",
    extra_targets=(("user", "user_id"),),
)
def hmc_create_user(
    console_uuid: str,
    user_id: str,
    password: str,
    authentication_type: AuthenticationType = "Local",
    *,
    description: str | None = None,
    associated_task_role: str | None = None,
    associated_resource_roles: list[str] | None = None,
    password_expiry: int | None = None,
    session_timeout: int | None = None,
    verify_session_timeout: bool | None = None,
    idle_session_timeout: int | None = None,
    user_inactivity: int | None = None,
    minimum_password_age: int | None = None,
    allow_web_remote_access: bool | None = None,
    allow_ssh_remote_access: bool | None = None,
    remote_user_id: str | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Create a documented UOM UserProfile below a management console.

    Role values are UOM role-resource hrefs returned by the role-list tools.
    Returns None when the HMC returns an empty successful response.

    Args:
        console_uuid: ManagementConsole UUID from ``hmc_console_info``.
        user_id: Login identifier for the new profile.
        password: Initial profile password.
        authentication_type: Local, LDAP, or Kerberos authentication.
        description: Optional human-readable profile description.
        associated_task_role: TaskRole href returned by the role-list tool.
        associated_resource_roles: ResourceRole hrefs assigned to the profile.
        password_expiry: Password-expiry interval accepted by the HMC.
        session_timeout: Session timeout value accepted by the HMC.
        verify_session_timeout: Whether the HMC verifies the session timeout.
        idle_session_timeout: Idle-session timeout accepted by the HMC.
        user_inactivity: User-inactivity interval accepted by the HMC.
        minimum_password_age: Minimum password age accepted by the HMC.
        allow_web_remote_access: Whether web remote access is allowed.
        allow_ssh_remote_access: Whether SSH remote access is allowed.
        remote_user_id: Directory-side user identifier.
        profile: TOML profile name, or the environment default when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await _create_user(
                hmc,
                console_uuid,
                user_id,
                password,
                authentication_type,
                description=description,
                associated_task_role=associated_task_role,
                associated_resource_roles=associated_resource_roles,
                password_expiry=password_expiry,
                session_timeout=session_timeout,
                verify_session_timeout=verify_session_timeout,
                idle_session_timeout=idle_session_timeout,
                user_inactivity=user_inactivity,
                minimum_password_age=minimum_password_age,
                allow_web_remote_access=allow_web_remote_access,
                allow_ssh_remote_access=allow_ssh_remote_access,
                remote_user_id=remote_user_id,
            )

    return run_sync(_go)


@tool(
    effect="mutate",
    operation="user.modify",
    target_kind="user",
    extra_targets=(("user", "user_profile_uuid"),),
)
def hmc_modify_user(
    console_uuid: str,
    user_profile_uuid: str,
    *,
    password: str | None = None,
    description: str | None = None,
    authentication_type: AuthenticationType | None = None,
    associated_task_role: str | None = None,
    associated_resource_roles: list[str] | None = None,
    password_expiry: int | None = None,
    session_timeout: int | None = None,
    verify_session_timeout: bool | None = None,
    idle_session_timeout: int | None = None,
    user_inactivity: int | None = None,
    minimum_password_age: int | None = None,
    allow_web_remote_access: bool | None = None,
    allow_ssh_remote_access: bool | None = None,
    remote_user_id: str | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Modify supplied fields of a UOM UserProfile identified by UUID.

    Returns None when the HMC returns an empty successful response.

    Args:
        console_uuid: ManagementConsole UUID from ``hmc_console_info``.
        user_profile_uuid: UserProfile UUID returned by ``hmc_list_users``.
        password: Replacement password, or None to leave unchanged.
        description: Replacement description, or None to leave unchanged.
        authentication_type: Replacement authentication type.
        associated_task_role: Replacement TaskRole href; empty clears the role.
        associated_resource_roles: Replacement ResourceRole hrefs; an empty list
            removes all resource roles.
        password_expiry: Replacement password-expiry interval.
        session_timeout: Replacement session timeout.
        verify_session_timeout: Replacement timeout-verification setting.
        idle_session_timeout: Replacement idle-session timeout.
        user_inactivity: Replacement user-inactivity interval.
        minimum_password_age: Replacement minimum password age.
        allow_web_remote_access: Replacement web-access setting.
        allow_ssh_remote_access: Replacement SSH-access setting.
        remote_user_id: Replacement directory-side identifier.
        profile: TOML profile name, or the environment default when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await _modify_user(
                hmc,
                console_uuid,
                user_profile_uuid,
                authentication_type=authentication_type,
                password=password,
                description=description,
                associated_task_role=associated_task_role,
                associated_resource_roles=associated_resource_roles,
                password_expiry=password_expiry,
                session_timeout=session_timeout,
                verify_session_timeout=verify_session_timeout,
                idle_session_timeout=idle_session_timeout,
                user_inactivity=user_inactivity,
                minimum_password_age=minimum_password_age,
                allow_web_remote_access=allow_web_remote_access,
                allow_ssh_remote_access=allow_ssh_remote_access,
                remote_user_id=remote_user_id,
            )

    return run_sync(_go)


@tool(
    effect="destructive",
    operation="user.delete",
    target_kind="user",
    extra_targets=(("user", "user_profile_uuid"),),
)
def hmc_delete_user(
    console_uuid: str, user_profile_uuid: str, profile: str | None = None
) -> str:
    """Permanently delete a UOM UserProfile identified by UUID.

    Args:
        console_uuid: ManagementConsole UUID from ``hmc_console_info``.
        user_profile_uuid: UserProfile UUID returned by ``hmc_list_users``.
        profile: TOML profile name, or the environment default when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            await _delete_user(hmc, console_uuid, user_profile_uuid)
            return f"Deleted HMC user profile {user_profile_uuid}"

    return run_sync(_go)


@tool(effect="read", operation="task_role.list", target_kind="console")
def hmc_list_task_roles(
    console_uuid: str, profile: str | None = None
) -> list[dict[str, Any]]:
    """List TaskRole children of a management console.

    Args:
        console_uuid: ManagementConsole UUID from ``hmc_console_info``.
        profile: TOML profile name, or the environment default when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.list_task_roles(console_uuid)

    return run_sync(_go)


@tool(effect="read", operation="resource_role.list", target_kind="console")
def hmc_list_resource_roles(
    console_uuid: str, profile: str | None = None
) -> list[dict[str, Any]]:
    """List ResourceRole children of a management console.

    Args:
        console_uuid: ManagementConsole UUID from ``hmc_console_info``.
        profile: TOML profile name, or the environment default when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.list_resource_roles(console_uuid)

    return run_sync(_go)


@tool(effect="read", operation="remote_access.get", target_kind="console")
def hmc_get_remote_access(
    console_uuid: str, profile: str | None = None
) -> dict[str, Any] | None:
    """Read the ManagementConsole RemoteAccess property group.

    Args:
        console_uuid: ManagementConsole UUID from ``hmc_console_info``.
        profile: TOML profile name, or the environment default when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.get_remote_access(console_uuid)

    return run_sync(_go)


@tool(effect="mutate", operation="remote_access.configure", target_kind="console")
def hmc_configure_remote_access(
    console_uuid: str,
    values: dict[str, str | int | bool] | None = None,
    clear_fields: list[str] | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Set or explicitly clear documented LDAP/Kerberos RemoteAccess fields.

    ``values`` maps documented property names to values. ``clear_fields``
    emits empty elements and cannot overlap with ``values``.
    Returns None when the HMC returns an empty successful response.

    Args:
        console_uuid: ManagementConsole UUID from ``hmc_console_info``.
        values: Documented RemoteAccess property names and replacement values.
        clear_fields: Documented properties to clear with empty XML elements.
        profile: TOML profile name, or the environment default when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await _configure_remote_access(
                hmc, console_uuid, values, clear_fields
            )

    return run_sync(_go)
