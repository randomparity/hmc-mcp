"""MCP tools for HMC user, password policy, and LDAP management."""

from __future__ import annotations

from typing import Any

from ._app import (
    _DESTRUCTIVE,
    _READ_ONLY,
    _run,
    mcp,
)

from .common import client_from_env
from .client_users import (
    LdapRemovalResource,
    PolicyType,
    UserType,
    validate_ldap_removal_resource,
)
from .documents import (
    TaskRole,
    build_hmc_user_document,
    build_ldap_config_document,
    build_password_policy_document,
)


@mcp.tool(annotations=_READ_ONLY)
def hmc_users(
    name: str | None = None,
    user_type: UserType = "all",
    profile: str | None = None,
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """List HMC user accounts or get one by username.

    When name is provided, returns a single resource dict for that user,
    or None if not found. user_type is ignored when name is supplied.

    When name is omitted, returns a list of all user accounts filtered by
    user_type: 'local' (local HMC accounts), 'kerberos'
    (Kerberos/LDAP-backed accounts), or 'all' (default).
    Returns one dict per user: {UUID, title, link, ResourceType, Resource}
    where Resource holds the flattened HmcUser fields.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            if name is not None:
                return await hmc.get_hmc_user(name)
            return await hmc.list_hmc_users(user_type)

    return _run(_go)


@mcp.tool
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


@mcp.tool
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
    disabled account; enable=False disables it. Use hmc_users(name=...) to
    confirm the current state before calling. Returns the updated user
    resource dict, or None when the HMC returns an empty successful response.
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


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_delete_user(name: str, profile: str | None = None) -> str:
    """Delete an HMC user account by username.

    This permanently removes the account — it is irreversible. Confirm
    the username with hmc_users(name=...) before calling. Returns a confirmation
    string (immediate delete — no job to poll).
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            await hmc.delete_hmc_user(name)
            return f"Deleted HMC user {name}"

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_password_policies(
    policy_type: PolicyType = "policies",
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """List HMC password policies.

    policy_type selects what to return: 'policies' (default) returns the list
    of defined password policies, 'status' returns activation status.
    Returns one dict per policy: {UUID, title, link, ResourceType, Resource}.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.list_password_policies(policy_type)

    return _run(_go)


@mcp.tool
def hmc_create_password_policy(
    policy_name: str,
    pwage: int = 0,
    min_length: int = 8,
    min_digits: int = 0,
    min_uppercase: int = 0,
    min_lowercase: int = 0,
    min_special: int = 0,
    hist_size: int = 0,
    warn_pwage: int = 0,
    min_pwage: int = 0,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Create a new HMC password policy.

    policy_name is the unique name for the policy.  pwage is the maximum
    password age in days (0 = never expires).  min_length is the minimum
    password length.  min_digits, min_uppercase, min_lowercase, and
    min_special set character-class minimums.  hist_size controls how many
    previous passwords cannot be reused.  warn_pwage is the number of days
    before expiry to warn the user.  min_pwage is the minimum days before a
    password may be changed.  Confirm the policy_name before calling. Returns
    the created policy resource dict, or None when the HMC returns an empty
    successful response.
    """
    xml = build_password_policy_document(
        policy_name=policy_name,
        pwage=pwage,
        min_length=min_length,
        min_digits=min_digits,
        min_uppercase=min_uppercase,
        min_lowercase=min_lowercase,
        min_special=min_special,
        hist_size=hist_size,
        warn_pwage=warn_pwage,
        min_pwage=min_pwage,
    )

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.create_password_policy(xml)

    return _run(_go)


@mcp.tool
def hmc_modify_password_policy(
    policy_name: str,
    pwage: int | None = None,
    min_length: int | None = None,
    min_digits: int | None = None,
    min_uppercase: int | None = None,
    min_lowercase: int | None = None,
    min_special: int | None = None,
    hist_size: int | None = None,
    warn_pwage: int | None = None,
    min_pwage: int | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Modify an existing HMC password policy.

    Only the fields you supply are changed.  Use hmc_list_password_policies
    to confirm the current state before calling.  To activate or deactivate a
    policy, use the HMC console — the REST API activates a policy by name via
    the PolicyType=status query path rather than a direct field change.
    Returns the updated policy resource dict, or None when the HMC returns an
    empty successful response.
    """
    xml = build_password_policy_document(
        pwage=pwage,
        min_length=min_length,
        min_digits=min_digits,
        min_uppercase=min_uppercase,
        min_lowercase=min_lowercase,
        min_special=min_special,
        hist_size=hist_size,
        warn_pwage=warn_pwage,
        min_pwage=min_pwage,
    )

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.modify_password_policy(policy_name, xml)

    return _run(_go)


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_delete_password_policy(policy_name: str, profile: str | None = None) -> str:
    """Delete an HMC password policy by name.

    This permanently removes the policy — it is irreversible.  Confirm
    the policy_name with hmc_list_password_policies before calling. Returns
    a confirmation string (immediate delete — no job to poll).
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            await hmc.delete_password_policy(policy_name)
            return f"Deleted HMC password policy {policy_name}"

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_ldap_config(profile: str | None = None) -> dict[str, Any] | None:
    """Get the current HMC LDAP server configuration.

    Returns a single resource dict describing the configured LDAP server URL,
    base DN, bind DN, search filter, and HMC group mappings, or None if no
    LDAP is configured.
    Equivalent to Ansible ``hmc_user`` state=ldap_facts.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.get_ldap_config()

    return _run(_go)


@mcp.tool
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

    base_dn: LDAP search base (e.g. 'dc=example,dc=com').
    bind_dn: DN of the account used to bind for searches.
    bind_pw: password for the bind account.
    search_filter: LDAP search filter (e.g. '(objectClass=person)').
    hmc_groups: comma-separated LDAP groups mapped to HMC access.
    group_member_attributes: LDAP attribute used for group membership.

    Equivalent to Ansible ``hmc_user`` action=configure_ldap.
    Returns the updated LDAP configuration resource dict, or None when the HMC
    returns an empty successful response.
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


@mcp.tool(annotations=_DESTRUCTIVE)
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
    """
    validate_ldap_removal_resource(resource)

    async def _go():
        async with client_from_env(profile) as hmc:
            await hmc.remove_ldap_config(resource)
            return f"Removed LDAP configuration component: {resource}"

    return _run(_go)
