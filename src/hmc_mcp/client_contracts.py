"""Static contracts between domain mixins and the composed HMC client."""

from __future__ import annotations

from typing import Any, Protocol


class LparsClient(Protocol):
    """Host operations required by :class:`client_lpars.LparsMixin`."""

    async def _get(
        self,
        path: str,
        resource_type: str | None = None,
        include_schema_version: bool = True,
    ) -> str: ...

    async def _post(
        self,
        path: str,
        body: str | bytes,
        resource_type: str | None = None,
        include_schema_version: bool = True,
    ) -> str: ...

    async def _put(
        self,
        path: str,
        body: str | bytes,
        resource_type: str | None = None,
        include_schema_version: bool = True,
    ) -> str: ...

    async def _delete(self, path: str) -> None: ...

    async def list_uom(
        self, resource_type: str, group: str | None = None
    ) -> list[dict[str, Any]]: ...

    async def get_uom(
        self, resource_type: str, uuid: str, group: str | None = None
    ) -> dict[str, Any] | None: ...

    async def search_uom(
        self, resource_type: str, property_name: str, property_value: str
    ) -> list[dict[str, Any]]: ...
