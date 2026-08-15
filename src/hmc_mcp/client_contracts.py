"""Static contracts between domain mixins and the composed HMC client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import httpx

from .config import HMCConfig


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

    async def list_logical_partitions(
        self, system_uuid: str | None = None
    ) -> list[dict[str, Any]]: ...

    async def list_uom(
        self, resource_type: str, group: str | None = None
    ) -> list[dict[str, Any]]: ...

    async def get_uom(
        self, resource_type: str, uuid: str, group: str | None = None
    ) -> dict[str, Any] | None: ...

    async def search_uom(
        self, resource_type: str, property_name: str, property_value: str
    ) -> list[dict[str, Any]]: ...

    async def list_managed_systems(self) -> list[dict[str, Any]]: ...

    async def get_managed_system(self, uuid: str) -> dict[str, Any] | None: ...


class PcmClient(Protocol):
    """Host state and operations required by :class:`client_pcm.PcmMixin`."""

    config: HMCConfig
    _http: httpx.AsyncClient

    async def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> httpx.Response: ...

    async def _get(
        self,
        path: str,
        resource_type: str | None = None,
        include_schema_version: bool = True,
    ) -> str: ...

    async def _post_pcm(self, path: str, body: str) -> str: ...

    async def _metrics_links(
        self,
        category: str,
        resource_uuid: str,
        kind: str,
        start_ts: str,
        end_ts: str | None,
        no_of_samples: int | None,
    ) -> list[dict[str, str]]: ...

    async def get_metrics_feed(self, path: str) -> list[dict[str, str]]: ...


class StorageClient(Protocol):
    """Host state and operations required by :class:`client_storage.StorageMixin`."""

    config: HMCConfig

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

    def get_lpar_link(self, lpar_uuid: str) -> str: ...

    async def _post_volume_group_op(
        self, vios_uuid: str, vg_uuid: str, xml: str
    ) -> dict[str, Any] | None: ...
