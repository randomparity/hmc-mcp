"""HMCClient users mixin.

The full client is assembled in :mod:`hmc_mcp.client` by inheriting every
domain mixin; this module only defines methods for users.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal, get_args

from .client_parse import _parse_feed

UserType = Literal["local", "kerberos", "all"]
LdapRemovalResource = Literal[
    "backup",
    "ldap",
    "binddn",
    "bindpw",
    "searchfilter",
    "hmcgroups",
    "groupmemberattributes",
]

_VALID_USER_TYPES = frozenset(get_args(UserType))
LDAP_REMOVAL_RESOURCES = frozenset(get_args(LdapRemovalResource))


def validate_ldap_removal_resource(
    resource: LdapRemovalResource,
) -> LdapRemovalResource:
    """Validate the LDAP component used in the destructive removal URL."""
    if resource not in LDAP_REMOVAL_RESOURCES:
        raise ValueError(
            f"Invalid LDAP removal resource {resource!r}. "
            f"Must be one of: {', '.join(sorted(LDAP_REMOVAL_RESOURCES))}"
        )
    return resource


class UsersMixin:
    _web_get: Callable[..., Awaitable[str]]
    _web_post: Callable[..., Awaitable[str]]
    _web_delete: Callable[..., Awaitable[None]]

    # ------------------------------------------------------------------ #
    # web-endpoint response parsing
    # ------------------------------------------------------------------ #
    def _web_entries(self, xml_text: str, context: str) -> list[dict[str, Any]]:
        """Parse a web-endpoint response into entry dicts; empty body → []."""
        if not (xml_text or "").strip():
            return []
        return _parse_feed(xml_text, context)

    def _first_web_entry(self, xml_text: str, context: str) -> dict[str, Any] | None:
        """Parse a web-endpoint response into its first entry, or None."""
        entries = self._web_entries(xml_text, context)
        return entries[0] if entries else None

    # ------------------------------------------------------------------ #
    # HMC user management (/rest/api/web/HmcUser)
    # ------------------------------------------------------------------ #
    async def list_hmc_users(self, user_type: UserType = "all") -> list[dict[str, Any]]:
        """GET /rest/api/web/HmcUser, optionally filtered by UserType.

        user_type is one of 'local', 'kerberos', or 'all' (default).
        Returns one parsed entry dict per account.

        Raises:
            ValueError: If *user_type* is not one of the recognised values.
        """
        if user_type not in _VALID_USER_TYPES:
            raise ValueError(
                f"Invalid user_type {user_type!r}. "
                f"Must be one of: {', '.join(sorted(_VALID_USER_TYPES))}"
            )
        path = "/rest/api/web/HmcUser"
        if user_type != "all":
            path += f"?UserType={user_type}"
        return self._web_entries(await self._web_get(path), path)

    async def get_hmc_user(self, name: str) -> dict[str, Any] | None:
        """GET /rest/api/web/HmcUser/{name}.

        Returns the parsed resource dict, or None when the server returns no
        content.
        """
        path = f"/rest/api/web/HmcUser/{name}"
        return self._first_web_entry(await self._web_get(path), path)

    async def create_hmc_user(self, user_xml: str) -> dict[str, Any] | None:
        """POST an HmcUser document to /rest/api/web/HmcUser.

        Returns the created resource dict, or None on an empty response.
        """
        return self._first_web_entry(
            await self._web_post("/rest/api/web/HmcUser", user_xml),
            "/rest/api/web/HmcUser",
        )

    async def modify_hmc_user(self, name: str, user_xml: str) -> dict[str, Any] | None:
        """POST a partial HmcUser document to /rest/api/web/HmcUser/{name}.

        Returns the updated resource dict, or None on an empty response.
        """
        path = f"/rest/api/web/HmcUser/{name}"
        return self._first_web_entry(await self._web_post(path, user_xml), path)

    async def delete_hmc_user(self, name: str) -> None:
        """DELETE /rest/api/web/HmcUser/{name}."""
        await self._web_delete(f"/rest/api/web/HmcUser/{name}")

    # ------------------------------------------------------------------ #
    # HMC LDAP server configuration (/rest/api/web/HmcLdapServer)
    # ------------------------------------------------------------------ #
    async def get_ldap_config(self) -> dict[str, Any] | None:
        """GET /rest/api/web/HmcLdapServer.

        Returns the parsed LDAP server configuration dict, or None when no
        LDAP is configured.
        """
        path = "/rest/api/web/HmcLdapServer"
        return self._first_web_entry(await self._web_get(path), path)

    async def configure_ldap(self, ldap_xml: str) -> dict[str, Any] | None:
        """POST an HmcLdapServer document to /rest/api/web/HmcLdapServer.

        Returns the updated configuration dict, or None on an empty response.
        """
        return self._first_web_entry(
            await self._web_post("/rest/api/web/HmcLdapServer", ldap_xml),
            "/rest/api/web/HmcLdapServer",
        )

    async def remove_ldap_config(self, resource: LdapRemovalResource) -> None:
        """POST to /rest/api/web/HmcLdapServer?Remove={resource}.

        resource is one of: 'backup', 'ldap', 'binddn', 'bindpw',
        'searchfilter', 'hmcgroups', 'groupmemberattributes'.
        """
        validate_ldap_removal_resource(resource)
        await self._web_post(f"/rest/api/web/HmcLdapServer?Remove={resource}", "")

    # ------------------------------------------------------------------ #
    # HMC password policy management (/rest/api/web/HmcPasswordPolicy)
    # ------------------------------------------------------------------ #
    async def list_password_policies(self) -> list[dict[str, Any]]:
        """Return defined policies from GET /rest/api/web/HmcPasswordPolicy."""
        path = "/rest/api/web/HmcPasswordPolicy"
        return self._web_entries(await self._web_get(path), path)

    async def list_password_policy_status(self) -> list[dict[str, Any]]:
        """Return activation status entries for HMC password policies."""
        path = "/rest/api/web/HmcPasswordPolicy?PolicyType=status"
        return self._web_entries(await self._web_get(path), path)

    async def create_password_policy(self, policy_xml: str) -> dict[str, Any] | None:
        """POST an HmcPasswordPolicy document to /rest/api/web/HmcPasswordPolicy.

        Returns the created policy dict, or None on an empty response.
        """
        return self._first_web_entry(
            await self._web_post("/rest/api/web/HmcPasswordPolicy", policy_xml),
            "/rest/api/web/HmcPasswordPolicy",
        )

    async def modify_password_policy(
        self, name: str, policy_xml: str
    ) -> dict[str, Any] | None:
        """POST a partial HmcPasswordPolicy document to /rest/api/web/HmcPasswordPolicy/{name}.

        Returns the updated policy dict, or None on an empty response.
        """
        path = f"/rest/api/web/HmcPasswordPolicy/{name}"
        return self._first_web_entry(await self._web_post(path, policy_xml), path)

    async def delete_password_policy(self, name: str) -> None:
        """DELETE /rest/api/web/HmcPasswordPolicy/{name}."""
        await self._web_delete(f"/rest/api/web/HmcPasswordPolicy/{name}")
