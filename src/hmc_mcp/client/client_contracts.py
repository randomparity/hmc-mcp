"""Static contracts between domain mixins and the composed HMC client."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING, Any, Protocol

from ..config import HMCConfig

if TYPE_CHECKING:
    import httpx
else:

    class _LazyHttpx:
        """Load HTTPX when runtime annotation or transport access needs it."""

        _module: ModuleType | None = None

        def __getattr__(self, name: str) -> Any:
            if self._module is None:
                self._module = import_module("httpx")
            return getattr(self._module, name)

    httpx = _LazyHttpx()


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
    _rest_base_url: str

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
        *,
        system_uuid: str | None = None,
    ) -> list[dict[str, str]]: ...

    async def get_metrics_feed(self, path: str) -> list[dict[str, str]]: ...


class StorageClient(Protocol):
    """Host state and operations required by :class:`client_storage.StorageMixin`."""

    config: HMCConfig
    _rest_base_url: str

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any: ...

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

    def get_lpar_link(self, lpar_uuid: str) -> str: ...

    async def _get_vg_raw_xml(
        self, vios_uuid: str, vg_uuid: str
    ) -> tuple[str, ET.Element]: ...

    async def _post_vg_xml(
        self, vios_uuid: str, vg_uuid: str, vg_elem: ET.Element
    ) -> dict[str, Any] | None: ...

    def _build_mr_element(self, size_mib: int) -> ET.Element: ...

    def _insert_mr_at_correct_position(
        self, vg_elem: ET.Element, mr_elem: ET.Element
    ) -> None: ...

    def _find_vmlib(self, vg_elem: ET.Element) -> ET.Element | None: ...


class AdaptersClient(Protocol):
    """Host operations required by :class:`client_adapters.AdaptersMixin`."""

    async def list_child(
        self, parent_type: str, parent_uuid: str, child_type: str
    ) -> list[dict[str, Any]]: ...

    async def create_child(
        self, parent_type: str, parent_uuid: str, child_type: str, child_xml: str
    ) -> dict[str, Any] | None: ...

    async def delete_child(
        self, parent_type: str, parent_uuid: str, child_type: str, child_uuid: str
    ) -> None: ...


class JobClient(Protocol):
    """Host operation shared by mixins that submit HMC jobs."""

    async def submit_job(
        self, job_path: str, job_request_xml: str
    ) -> dict[str, Any] | None: ...


class ClusterClient(JobClient, Protocol):
    """Host operations required by :class:`client_cluster.ClusterMixin`."""

    async def list_uom(
        self, resource_type: str, group: str | None = None
    ) -> list[dict[str, Any]]: ...

    async def get_uom(
        self, resource_type: str, uuid: str, group: str | None = None
    ) -> dict[str, Any] | None: ...


class LpmClient(JobClient, Protocol):
    """Host operations required by :class:`client_lpm.LpmMixin`."""

    async def _lpar_job(
        self, lpar_uuid: str, operation: str, job_xml: str
    ) -> dict[str, Any] | None: ...


class NetworkClient(Protocol):
    """Host state and operations required by :class:`client_network.NetworkMixin`."""

    _rest_base_url: str

    async def _get(
        self,
        path: str,
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


class SystemsClient(JobClient, Protocol):
    """Host operations required by :class:`client_systems.SystemsMixin`."""

    async def list_uom(
        self, resource_type: str, group: str | None = None
    ) -> list[dict[str, Any]]: ...

    async def get_uom(
        self, resource_type: str, uuid: str, group: str | None = None
    ) -> dict[str, Any] | None: ...

    async def search_uom(
        self, resource_type: str, property_name: str, property_value: str
    ) -> list[dict[str, Any]]: ...

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

    async def list_managed_systems(self) -> list[dict[str, Any]]: ...

    async def get_managed_system(self, uuid: str) -> dict[str, Any] | None: ...

    async def list_vios(
        self, system_uuid: str | None = None
    ) -> list[dict[str, Any]]: ...


class TemplatesClient(JobClient, Protocol):
    """Host state and operations required by :class:`client_templates.TemplatesMixin`."""

    TEMPLATES_MEDIA: str
    _session_token: str | None

    async def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> httpx.Response: ...

    async def _templates_get(self, path: str) -> str: ...


class UsersClient(Protocol):
    """Host operations required by :class:`client_users.UsersMixin`."""

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

    async def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> httpx.Response: ...

    def _child_path(self, console_uuid: str, child_type: str) -> str: ...

    def _entries(self, xml_text: str, path: str) -> list[dict[str, Any]]: ...

    def _first_entry(self, xml_text: str, path: str) -> dict[str, Any] | None: ...

    async def _get_remote_access_xml(self, path: str) -> str: ...
