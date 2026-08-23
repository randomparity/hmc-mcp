"""Documented UOM user and remote-access client operations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal, get_args
from urllib.parse import quote

from .client_parse import _parse_feed
from .documents import merge_remote_access_document
from .errors import HMCError

REMOTE_ACCESS_MEDIA = "application/vnd.ibm.powervm.web+xml; type=ManagementConsole"

AuthenticationFilter = Literal["local", "ldap", "kerberos", "all"]
_AUTHENTICATION_TYPES = {"local": "Local", "ldap": "LDAP", "kerberos": "Kerberos"}
_VALID_AUTHENTICATION_FILTERS = frozenset(get_args(AuthenticationFilter))


class UsersMixin:
    """Operations below a documented UOM ``ManagementConsole`` resource."""

    _get: Callable[..., Awaitable[str]]
    _put: Callable[..., Awaitable[str]]
    _post: Callable[..., Awaitable[str]]
    _delete: Callable[..., Awaitable[None]]
    _request: Callable[..., Awaitable[Any]]

    @staticmethod
    def _entries(xml_text: str, path: str) -> list[dict[str, Any]]:
        return _parse_feed(xml_text, path) if (xml_text or "").strip() else []

    @classmethod
    def _first_entry(cls, xml_text: str, path: str) -> dict[str, Any] | None:
        entries = cls._entries(xml_text, path)
        return entries[0] if entries else None

    @staticmethod
    def _child_path(console_uuid: str, child_type: str) -> str:
        console_path_id = quote(console_uuid, safe="")
        return f"/rest/api/uom/ManagementConsole/{console_path_id}/{child_type}"

    async def list_hmc_users(
        self,
        console_uuid: str,
        authentication_type: AuthenticationFilter = "all",
    ) -> list[dict[str, Any]]:
        """List documented ``UserProfile`` children of a management console."""
        if authentication_type not in _VALID_AUTHENTICATION_FILTERS:
            raise ValueError(
                f"Invalid authentication_type {authentication_type!r}. Must be one of: "
                f"{', '.join(sorted(_VALID_AUTHENTICATION_FILTERS))}"
            )
        path = self._child_path(console_uuid, "UserProfile")
        entries = self._entries(await self._get(path, "UserProfile"), path)
        if authentication_type == "all":
            return entries
        expected = _AUTHENTICATION_TYPES[authentication_type]
        return [
            entry
            for entry in entries
            if (entry.get("Resource") or {}).get("AuthenticationType") == expected
        ]

    async def get_hmc_user(
        self, console_uuid: str, user_profile_uuid: str
    ) -> dict[str, Any] | None:
        profile_path_id = quote(user_profile_uuid, safe="")
        path = f"{self._child_path(console_uuid, 'UserProfile')}/{profile_path_id}"
        return self._first_entry(await self._get(path, "UserProfile"), path)

    async def create_hmc_user(
        self, console_uuid: str, user_xml: str
    ) -> dict[str, Any] | None:
        path = self._child_path(console_uuid, "UserProfile")
        return self._first_entry(await self._put(path, user_xml, "UserProfile"), path)

    async def modify_hmc_user(
        self, console_uuid: str, user_profile_uuid: str, user_xml: str
    ) -> dict[str, Any] | None:
        profile_path_id = quote(user_profile_uuid, safe="")
        path = f"{self._child_path(console_uuid, 'UserProfile')}/{profile_path_id}"
        return self._first_entry(await self._post(path, user_xml, "UserProfile"), path)

    async def delete_hmc_user(self, console_uuid: str, user_profile_uuid: str) -> None:
        profile_path_id = quote(user_profile_uuid, safe="")
        path = f"{self._child_path(console_uuid, 'UserProfile')}/{profile_path_id}"
        await self._delete(path)

    async def list_task_roles(self, console_uuid: str) -> list[dict[str, Any]]:
        path = self._child_path(console_uuid, "TaskRole")
        return self._entries(await self._get(path, "TaskRole"), path)

    async def list_resource_roles(self, console_uuid: str) -> list[dict[str, Any]]:
        path = self._child_path(console_uuid, "ResourceRole")
        return self._entries(await self._get(path, "ResourceRole"), path)

    async def get_remote_access(self, console_uuid: str) -> dict[str, Any] | None:
        console_path_id = quote(console_uuid, safe="")
        path = f"/rest/api/uom/ManagementConsole/{console_path_id}?group=RemoteAccess"
        xml = await self._get_remote_access_xml(path)
        return self._first_entry(xml, path)

    async def _get_remote_access_xml(self, path: str) -> str:
        response = await self._request(
            "GET", path, headers={"Accept": REMOTE_ACCESS_MEDIA}
        )
        if response.status_code == 204:
            return ""
        if response.status_code != 200:
            raise HMCError(f"GET {path} failed", response.status_code, response.text)
        return response.text

    async def configure_remote_access(
        self,
        console_uuid: str,
        values: dict[str, str | int | bool] | None,
        clear_fields: list[str] | None,
    ) -> dict[str, Any] | None:
        console_path_id = quote(console_uuid, safe="")
        path = f"/rest/api/uom/ManagementConsole/{console_path_id}?group=RemoteAccess"
        current_xml = await self._get_remote_access_xml(path)
        if not current_xml.strip():
            raise ValueError("RemoteAccess GET returned no ManagementConsole document")
        remote_access_xml = merge_remote_access_document(
            current_xml, values, clear_fields
        )
        xml = await self._post(path, remote_access_xml, "ManagementConsole")
        return self._first_entry(xml, path)
