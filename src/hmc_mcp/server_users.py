"""MCP tools for HMC user, password policy, and LDAP management."""

from __future__ import annotations

from .tool_registry import tool_module

from typing import Any

from ._app import (
    _run,
)

from .common import client_from_env
from .client_users import (
    LdapRemovalResource,
    UserType,
    validate_ldap_removal_resource,
)
from .documents import (
    PASSWORD_POLICY_CREATION_DEFAULTS,
    PasswordPolicySettings,
    TaskRole,
    build_hmc_user_document,
    build_ldap_config_document,
    build_password_policy_document,
)


tool, register_tools, tool_security = tool_module()


@tool(effect="read", operation="user.list", target_kind="console")
def hmc_list_users(
    user_type: UserType = "all",
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """List HMC user accounts filtered by type.

    ``local`` selects local HMC accounts, ``kerberos`` selects
    (Kerberos/LDAP-backed accounts), or 'all' (default).
    Returns one dict per user: {UUID, title, link, ResourceType, Resource}
    where Resource holds the flattened HmcUser fields.

    Args:
        user_type: ``local``, ``kerberos``, or ``all`` accounts.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.list_hmc_users(user_type)

    return _run(_go)


@tool(
    effect="read",
    operation="user.get",
    target_kind="user",
    extra_targets=(("user", "name"),),
)
def hmc_get_user(
    name: str,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Get one HMC user account by username.

    Returns None only when the HMC sends an empty successful response. A
    missing account reported as HTTP 404 raises HMCError like other REST
    lookup failures.

    Args:
        name: Exact HMC login username.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.get_hmc_user(name)

    return _run(_go)


@tool(
    effect="mutate",
    operation="user.create",
    target_kind="user",
    extra_targets=(("user", "name"),),
)
def hmc_create_user(
    name: str,
    taskrole: TaskRole,
    password: str,
    description: str = "",
    pwage: int = 0,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Create a new HMC local user account.

    name is the login username. taskrole controls what the user can do
    (e.g. 'hmcoperator', 'hmcviewer', 'hmcsuperadmin'). password is the
    initial password. description is optional. pwage is the password
    expiration in days (0 = never expires). This creates a real account —
    confirm the taskrole before calling. Returns the created user resource
    dict, or None when the HMC returns an empty successful response.

    Args:
        name: Login username for the new local account.
        taskrole: HMC authorization role assigned to the account.
        password: Initial account password.
        description: Optional human-readable account description.
        pwage: Password lifetime in days; ``0`` means it never expires.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    xml = build_hmc_user_document(
        username=name,
        taskrole=taskrole,
        password=password,
        description=description or None,
        pwage=pwage,
    )

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.create_hmc_user(xml)

    return _run(_go)


@tool(
    effect="mutate",
    operation="user.modify",
    target_kind="user",
    extra_targets=(("user", "name"),),
)
def hmc_modify_user(
    name: str,
    taskrole: TaskRole | None = None,
    password: str | None = None,
    description: str | None = None,
    enable: bool | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Modify an existing HMC user account.

    Only the fields you supply are changed. enable=True re-enables a
    disabled account; enable=False disables it. Use hmc_get_user(name) to
    confirm the current state before calling. Returns the updated user
    resource dict, or None when the HMC returns an empty successful response.

    Args:
        name: Exact username of the account to modify.
        taskrole: Replacement HMC role, or ``None`` to leave it unchanged.
        password: Replacement password, or ``None`` to leave it unchanged.
        description: Replacement description, or ``None`` to leave it unchanged.
        enable: ``True`` to enable, ``False`` to disable, or ``None`` unchanged.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    xml = build_hmc_user_document(
        taskrole=taskrole,
        password=password,
        description=description,
        enable=enable,
    )

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.modify_hmc_user(name, xml)

    return _run(_go)


@tool(
    effect="destructive",
    operation="user.delete",
    target_kind="user",
    extra_targets=(("user", "name"),),
)
def hmc_delete_user(name: str, profile: str | None = None) -> str:
    """Delete an HMC user account by username.

    This permanently removes the account — it is irreversible. Confirm
    the username with hmc_get_user(name) before calling. Returns a confirmation
    string (immediate delete — no job to poll).

    Args:
        name: Exact username of the account to permanently remove.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            await hmc.delete_hmc_user(name)
            return f"Deleted HMC user {name}"

    return _run(_go)


@tool(effect="read", operation="policy.list", target_kind="console")
def hmc_list_password_policies(
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """List defined HMC password-policy resources.

    Args:
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.list_password_policies()

    return _run(_go)


@tool(effect="read", operation="policy.status", target_kind="console")
def hmc_list_password_policy_status(
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """Get activation-status resources for HMC password policies.

    Args:
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.list_password_policy_status()

    return _run(_go)


@tool(effect="mutate", operation="policy.create", target_kind="password_policy")
def hmc_create_password_policy(
    policy_name: str,
    settings: PasswordPolicySettings = PASSWORD_POLICY_CREATION_DEFAULTS,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Create a new HMC password policy.

    policy_name is the unique name for the policy. settings contains password
    age, length, character-class, history, and warning requirements. Omitted
    settings use the HMC-compatible creation defaults. Confirm policy_name
    before calling. Returns the created policy resource dict, or None when the
    HMC returns an empty successful response.

    Args:
        policy_name: Unique name for the new password policy.
        settings: Password requirements; omitted fields use creation defaults.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    xml = build_password_policy_document(
        policy_name=policy_name,
        settings=settings,
    )

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.create_password_policy(xml)

    return _run(_go)


@tool(effect="mutate", operation="policy.modify", target_kind="password_policy")
def hmc_modify_password_policy(
    policy_name: str,
    settings: PasswordPolicySettings = PasswordPolicySettings(),
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Modify an existing HMC password policy.

    Only non-None fields in settings are changed. Use hmc_list_password_policies
    to confirm the current state before calling. To activate or deactivate a
    policy, use the HMC console — the REST API activates a policy by name via
    the PolicyType=status query path rather than a direct field change.
    Returns the updated policy resource dict, or None when the HMC returns an
    empty successful response.

    Args:
        policy_name: Exact password-policy name to modify.
        settings: Partial requirements; ``None`` fields remain unchanged.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    xml = build_password_policy_document(
        settings=settings,
    )

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.modify_password_policy(policy_name, xml)

    return _run(_go)


@tool(effect="destructive", operation="policy.delete", target_kind="password_policy")
def hmc_delete_password_policy(policy_name: str, profile: str | None = None) -> str:
    """Delete an HMC password policy by name.

    This permanently removes the policy — it is irreversible.  Confirm
    the policy_name with hmc_list_password_policies before calling. Returns
    a confirmation string (immediate delete — no job to poll).

    Args:
        policy_name: Exact password-policy name to permanently remove.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            await hmc.delete_password_policy(policy_name)
            return f"Deleted HMC password policy {policy_name}"

    return _run(_go)


@tool(effect="read", operation="ldap.get", target_kind="console")
def hmc_get_ldap_config(profile: str | None = None) -> dict[str, Any] | None:
    """Get the current HMC LDAP server configuration.

    Returns a single resource dict describing the configured LDAP server URL,
    base DN, bind DN, search filter, and HMC group mappings, or None if no
    LDAP is configured.
    Equivalent to Ansible ``hmc_user`` state=ldap_facts.

    Args:
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.get_ldap_config()

    return _run(_go)


@tool(effect="mutate", operation="ldap.configure", target_kind="console")
def hmc_configure_ldap(
    server_url: str,
    base_dn: str | None = None,
    bind_dn: str | None = None,
    bind_pw: str | None = None,
    search_filter: str | None = None,
    hmc_groups: str | None = None,
    group_member_attributes: str | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Configure the HMC LDAP server integration.

    server_url is the LDAP or LDAPS URL (e.g. 'ldap://ldap.example.com' or
    'ldaps://ldap.example.com:636'). Only the fields you supply are changed.

    Equivalent to Ansible ``hmc_user`` action=configure_ldap.
    Returns the updated LDAP configuration resource dict, or None when the HMC
    returns an empty successful response.

    Args:
        server_url: LDAP or LDAPS server URL, including an optional port.
        base_dn: LDAP search base, such as ``dc=example,dc=com``.
        bind_dn: Distinguished name used to bind for directory searches.
        bind_pw: Password for the bind account.
        search_filter: LDAP search filter, such as ``(objectClass=person)``.
        hmc_groups: Comma-separated LDAP groups mapped to HMC access.
        group_member_attributes: LDAP attribute used for group membership.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    xml = build_ldap_config_document(
        server_url=server_url,
        base_dn=base_dn,
        bind_dn=bind_dn,
        bind_pw=bind_pw,
        search_filter=search_filter,
        hmc_groups=hmc_groups,
        group_member_attributes=group_member_attributes,
    )

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.configure_ldap(xml)

    return _run(_go)


@tool(effect="destructive", operation="ldap.remove", target_kind="console")
def hmc_remove_ldap_config(
    resource: LdapRemovalResource, profile: str | None = None
) -> str:
    """Remove a component of the HMC LDAP server configuration.

    resource selects what to remove.  Valid values:
      'backup'                  — remove the backup LDAP server
      'ldap'                    — remove the entire LDAP configuration
      'binddn'                  — remove the bind DN
      'bindpw'                  — remove the bind password
      'searchfilter'            — remove the custom search filter
      'hmcgroups'               — remove HMC group mappings
      'groupmemberattributes'   — remove group-member attribute settings

    Equivalent to Ansible ``hmc_user`` action=remove_ldap_config.
    Use hmc_get_ldap_config to inspect the current state before calling.
    Returns a confirmation string (immediate delete — no job to poll).

    Args:
        resource: LDAP component to remove: ``backup``, ``ldap``, ``binddn``,
            ``bindpw``, ``searchfilter``, ``hmcgroups``, or
            ``groupmemberattributes``.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    validate_ldap_removal_resource(resource)

    async def _go():
        async with client_from_env(profile) as hmc:
            await hmc.remove_ldap_config(resource)
            return f"Removed LDAP configuration component: {resource}"

    return _run(_go)
