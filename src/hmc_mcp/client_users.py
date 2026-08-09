"""HMCClient users mixin.

The full client is assembled in :mod:`hmc_mcp.client` by inheriting every
domain mixin; this module only defines methods for users.
"""

from __future__ import annotations




class UsersMixin:
    # ------------------------------------------------------------------ #
    # HMC user management (/rest/api/web/HmcUser)
    # ------------------------------------------------------------------ #
    async def list_hmc_users(self, user_type: str = "all") -> str:
        """GET /rest/api/web/HmcUser, optionally filtered by UserType.

        user_type is one of 'local', 'kerberos', or 'all' (default).
        Returns raw XML; the caller decides how to present it.
        """
        path = "/rest/api/web/HmcUser"
        if user_type != "all":
            path += f"?UserType={user_type}"
        return await self._web_get(path)

    async def get_hmc_user(self, name: str) -> str:
        """GET /rest/api/web/HmcUser/{name}."""
        return await self._web_get(f"/rest/api/web/HmcUser/{name}")

    async def create_hmc_user(self, user_xml: str) -> str:
        """POST an HmcUser document to /rest/api/web/HmcUser."""
        return await self._web_post("/rest/api/web/HmcUser", user_xml)

    async def modify_hmc_user(self, name: str, user_xml: str) -> str:
        """POST a partial HmcUser document to /rest/api/web/HmcUser/{name}."""
        return await self._web_post(f"/rest/api/web/HmcUser/{name}", user_xml)

    async def delete_hmc_user(self, name: str) -> None:
        """DELETE /rest/api/web/HmcUser/{name}."""
        await self._web_delete(f"/rest/api/web/HmcUser/{name}")

    # ------------------------------------------------------------------ #
    # HMC LDAP server configuration (/rest/api/web/HmcLdapServer)
    # ------------------------------------------------------------------ #
    async def list_ldap_config(self) -> str:
        """GET /rest/api/web/HmcLdapServer.

        Returns raw XML; the caller decides how to present it.
        """
        return await self._web_get("/rest/api/web/HmcLdapServer")

    async def configure_ldap(self, ldap_xml: str) -> str:
        """POST an HmcLdapServer document to /rest/api/web/HmcLdapServer."""
        return await self._web_post("/rest/api/web/HmcLdapServer", ldap_xml)

    async def remove_ldap_config(self, resource: str) -> str:
        """POST to /rest/api/web/HmcLdapServer?Remove={resource}.

        resource is one of: 'backup', 'ldap', 'binddn', 'bindpw',
        'searchfilter', 'hmcgroups', 'groupmemberattributes'.
        """
        return await self._web_post(
            f"/rest/api/web/HmcLdapServer?Remove={resource}", ""
        )

    # ------------------------------------------------------------------ #
    # HMC password policy management (/rest/api/web/HmcPasswordPolicy)
    # ------------------------------------------------------------------ #
    async def list_password_policies(self, policy_type: str = "policies") -> str:
        """GET /rest/api/web/HmcPasswordPolicy, optionally filtered by PolicyType.

        policy_type is one of 'policies' (default, returns policy list) or
        'status' (returns activation status).
        Returns raw XML; the caller decides how to present it.
        """
        path = "/rest/api/web/HmcPasswordPolicy"
        if policy_type != "policies":
            path += f"?PolicyType={policy_type}"
        return await self._web_get(path)

    async def create_password_policy(self, policy_xml: str) -> str:
        """POST an HmcPasswordPolicy document to /rest/api/web/HmcPasswordPolicy."""
        return await self._web_post("/rest/api/web/HmcPasswordPolicy", policy_xml)

    async def modify_password_policy(self, name: str, policy_xml: str) -> str:
        """POST a partial HmcPasswordPolicy document to /rest/api/web/HmcPasswordPolicy/{name}."""
        return await self._web_post(f"/rest/api/web/HmcPasswordPolicy/{name}", policy_xml)

    async def delete_password_policy(self, name: str) -> None:
        """DELETE /rest/api/web/HmcPasswordPolicy/{name}."""
        await self._web_delete(f"/rest/api/web/HmcPasswordPolicy/{name}")
