"""Async IBM HMC REST API client.

Owns the Logon/Logoff session lifecycle, the HTTP transport and the generic
uom helpers (list_uom / get_uom / search_uom / child resources / jobs / raw
escape hatch). Domain operations live in the per-domain mixin modules
(``client_users``, ``client_storage``, ``client_pcm``, ...) and are composed
into :class:`HMCClient` by inheritance.
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
import warnings
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from .client_parse import _find_text, _parse_feed
from .config import HMCConfig
from .errors import HMCError, HMCTransportError
from .jobs import TERMINAL_JOB_STATUSES
from .xmlutil import WEB_NS

from .client_adapters import AdaptersMixin
from .client_cluster import ClusterMixin
from .client_lpars import LparsMixin
from .client_lpm import LpmMixin
from .client_network import NetworkMixin
from .client_pcm import PcmMixin
from .client_storage import StorageMixin
from .client_systems import SystemsMixin
from .client_templates import TemplatesMixin
from .client_users import UsersMixin

if TYPE_CHECKING:
    import httpx


class _LazyHttpx:
    """Load HTTPX only when a client instance first needs the transport."""

    _module: ModuleType | None = None

    def __getattr__(self, name: str) -> Any:
        if self._module is None:
            self._module = import_module("httpx")
        return getattr(self._module, name)


if not TYPE_CHECKING:
    httpx = _LazyHttpx()

LOGON_REQUEST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<LogonRequest xmlns="{web_ns}" schemaVersion="V1_0">
  <Metadata>
    <Atom/>
  </Metadata>
  <UserID kb="CUR" kxe="false">{user}</UserID>
  <Password kb="CUR" kxe="false">{password}</Password>
</LogonRequest>
"""

# Media-type fragments used by the HMC API.
MEDIA_WEB = "application/vnd.ibm.powervm.web+xml"
MEDIA_UOM = "application/vnd.ibm.powervm.uom+xml"


class HMCClient(
    UsersMixin,
    SystemsMixin,
    LparsMixin,
    AdaptersMixin,
    StorageMixin,
    ClusterMixin,
    LpmMixin,
    PcmMixin,
    NetworkMixin,
    TemplatesMixin,
):
    """Async context-manager client for one HMC session.

    Usage:
        async with HMCClient(config) as hmc:
            systems = await hmc.list_managed_systems()
    """

    def __init__(self, config: HMCConfig):
        config.validate_credentials()
        self.config = config
        self._session_token: str | None = None
        # X-Audit-Memento is evaluated once at construction time — this is safe
        # because each tool invocation creates a new HMCClient (via asyncio.run(_go)).
        # If the transport ever moves to a persistent shared client, this header would
        # stale when HMC_AGENT_ID changes; re-evaluate effective_audit_memento per-request
        # in that case.
        self._http = httpx.AsyncClient(
            base_url=config.base_url,
            verify=config.verify_ssl,
            timeout=config.timeout,
            headers={
                "X-Audit-Memento": config.effective_audit_memento,
                # Most HMC builds ignore charset but honour JSON when asked;
                # we stick to the canonical XML representation everywhere.
            },
        )

    # ------------------------------------------------------------------ #
    # Session lifecycle
    # ------------------------------------------------------------------ #

    async def __aenter__(self) -> "HMCClient":
        try:
            await self.logon()
        except BaseException:
            await self._http.aclose()
            raise
        return self

    async def __aexit__(self, _exc_type, exc, _traceback) -> None:
        cleanup_error: BaseException | None = None
        try:
            await self.logoff()
        except BaseException as logoff_error:
            cleanup_error = logoff_error

        try:
            await self._http.aclose()
        except BaseException as close_error:
            if cleanup_error is None:
                cleanup_error = close_error
            else:
                cleanup_error.add_note(
                    f"HTTP client close also failed: {close_error!r}"
                )

        if cleanup_error is None:
            return
        if exc is not None:
            exc.add_note(f"HMC session cleanup also failed: {cleanup_error!r}")
            return
        raise cleanup_error

    @property
    def is_logged_on(self) -> bool:
        return self._session_token is not None

    async def logon(self) -> str:
        """Authenticate and store the X-API-Session token.

        Emits a one-time warning when TLS certificate verification is disabled
        so the MITM exposure of the credentials in flight is never silent.
        """
        if not self.config.verify_ssl:
            warnings.warn(
                "TLS certificate verification is disabled (verify_ssl=False). "
                "HMC credentials travel over an unverified TLS connection and "
                "can be intercepted by a man-in-the-middle. Install the HMC's "
                "CA locally and set HMC_VERIFY_SSL=true (or --verify-ssl) to "
                "enable verification.",
                stacklevel=2,
            )
        body = LOGON_REQUEST_TEMPLATE.format(
            web_ns=WEB_NS, user=self.config.user, password=self.config.password
        )
        resp = await self._request(
            "PUT",
            "/rest/api/web/Logon",
            content=body,
            headers=self._web_headers(
                {
                    "Content-Type": f"{MEDIA_WEB}; type=LogonRequest",
                    "Accept": f"{MEDIA_WEB}; type=LogonResponse",
                }
            ),
        )
        if resp.status_code != 200:
            raise HMCError("HMC logon failed", resp.status_code, resp.text)
        token = _find_text(resp.text, "/rest/api/web/Logon", "X-API-Session")
        if not token:
            raise HMCError("HMC logon response did not contain an X-API-Session token")
        self._session_token = token
        self._http.headers["X-API-Session"] = token
        return token

    async def logoff(self) -> None:
        """Invalidate the session token (DELETE the Logon resource)."""
        if not self._session_token:
            return
        try:
            await self._request(
                "DELETE",
                "/rest/api/web/Logon",
                headers=self._web_headers({"Accept": MEDIA_WEB}),
            )
        finally:
            self._session_token = None
            self._http.headers.pop("X-API-Session", None)

    # ------------------------------------------------------------------ #
    # Generic request helpers
    # ------------------------------------------------------------------ #

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send one REST request and normalize transport-layer failures."""
        try:
            return await self._http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            timeout = f"{self.config.timeout:g}"
            raise HMCTransportError(
                f"{method.upper()} {path} timed out after the configured {timeout}s. "
                f"Increase HMC_TIMEOUT above {timeout} for a slower HMC or network."
            ) from exc
        except httpx.TransportError as exc:
            raise HMCTransportError(
                f"{method.upper()} {path} failed before the HMC returned a response: {exc}"
            ) from exc

    def _uom_headers(
        self,
        resource_type: str | None,
        include_schema_version: bool = True,
    ) -> dict[str, str]:
        accept = MEDIA_UOM
        if resource_type:
            accept = f"{MEDIA_UOM}; type={resource_type}"
        headers: dict[str, str] = {"Accept": accept}
        if include_schema_version and self.config.schema_version:
            headers["X-HMC-Schema-Version"] = self.config.schema_version
        return headers

    async def _get(
        self,
        path: str,
        resource_type: str | None = None,
        include_schema_version: bool = True,
    ) -> str:
        resp = await self._request(
            "GET",
            path,
            headers=self._uom_headers(resource_type, include_schema_version),
        )
        if resp.status_code == 204:
            return ""
        if resp.status_code != 200:
            raise HMCError(f"GET {path} failed", resp.status_code, resp.text)
        return resp.text

    async def _post(
        self,
        path: str,
        body: str | bytes,
        resource_type: str | None = None,
        include_schema_version: bool = True,
    ) -> str:
        headers = self._uom_headers(resource_type, include_schema_version)
        headers["Content-Type"] = headers["Accept"]
        resp = await self._request("POST", path, content=body, headers=headers)
        if resp.status_code not in (200, 201, 202):
            raise HMCError(f"POST {path} failed", resp.status_code, resp.text)
        return resp.text

    async def _put(
        self,
        path: str,
        body: str | bytes,
        resource_type: str | None = None,
        include_schema_version: bool = True,
    ) -> str:
        headers = self._uom_headers(resource_type, include_schema_version)
        headers["Content-Type"] = headers["Accept"]
        resp = await self._request("PUT", path, content=body, headers=headers)
        if resp.status_code not in (200, 201, 202, 204):
            raise HMCError(f"PUT {path} failed", resp.status_code, resp.text)
        return resp.text

    async def _delete(self, path: str) -> None:
        resp = await self._request("DELETE", path, headers=self._uom_headers(None))
        if resp.status_code not in (200, 202, 204):
            raise HMCError(f"DELETE {path} failed", resp.status_code, resp.text)

    # ------------------------------------------------------------------ #
    # Web endpoint helpers (/rest/api/web/)
    #
    # The HMC exposes user management and other non-UOM resources under
    # /rest/api/web/ with the MEDIA_WEB content type.  These helpers mirror
    # _get/_post/_delete but use MEDIA_WEB for Content-Type and Accept.
    #
    # Auth assumption: /rest/api/web/HmcUser (and sibling web endpoints)
    # accept the same X-API-Session token that _get/_post/_delete use.
    # This is consistent with the HMC REST API design — the token is set
    # on the shared httpx client during logon and applies to every request,
    # including the /rest/api/web/Logon and /rest/api/web/Logoff calls that
    # already use MEDIA_WEB in this file.  The ansible-power-hmc reference
    # implementation uses the same session token for HmcUser operations.
    # ------------------------------------------------------------------ #

    def _web_headers(self, extra: dict[str, str]) -> dict[str, str]:
        """Build headers for a web-endpoint request.

        Merges *extra* with a conditional X-HMC-Schema-Version header so that
        HMC versions requiring the header on /rest/api/web/ endpoints receive
        it whenever the caller has configured schema_version (issue #99).
        """
        headers = dict(extra)
        if self.config.schema_version:
            headers["X-HMC-Schema-Version"] = self.config.schema_version
        return headers

    @staticmethod
    def _check_web_rest000e(path: str, status_code: int, body: str) -> None:
        """Raise an actionable HMCError when an HTTP 400 body contains REST000E.

        REST000E ('Unrecognized root REST type') means the /rest/api/web/ endpoint
        is not present on this HMC.  The cause is unknown from the client side: it
        may require a specific configuration, license, or PTF level.  Convert the
        raw error into a message that names the endpoint, the error code, and the
        remediation hint (issue #113).
        """
        if status_code == 400 and "REST000E" in body:
            raise HMCError(
                f"{path} returned HTTP 400 (REST000E: Unrecognized root REST type). "
                "This endpoint is not available on this HMC. "
                "The HMC may require a specific configuration, license, or PTF level. "
                "Check your HMC documentation.",
                status_code,
            )

    async def _web_get(self, path: str) -> str:
        resp = await self._request(
            "GET", path, headers=self._web_headers({"Accept": MEDIA_WEB})
        )
        if resp.status_code == 204:
            return ""
        if resp.status_code != 200:
            self._check_web_rest000e(path, resp.status_code, resp.text)
            raise HMCError(f"GET {path} failed", resp.status_code, resp.text)
        return resp.text

    async def _web_post(self, path: str, body: str) -> str:
        resp = await self._request(
            "POST",
            path,
            content=body,
            headers=self._web_headers({"Content-Type": MEDIA_WEB, "Accept": MEDIA_WEB}),
        )
        if resp.status_code not in (200, 201, 202):
            self._check_web_rest000e(path, resp.status_code, resp.text)
            raise HMCError(f"POST {path} failed", resp.status_code, resp.text)
        return resp.text

    async def _web_delete(self, path: str) -> None:
        resp = await self._request(
            "DELETE", path, headers=self._web_headers({"Accept": MEDIA_WEB})
        )
        if resp.status_code not in (200, 202, 204):
            self._check_web_rest000e(path, resp.status_code, resp.text)
            raise HMCError(f"DELETE {path} failed", resp.status_code, resp.text)

    # ------------------------------------------------------------------ #
    # uom resources
    # ------------------------------------------------------------------ #

    async def list_uom(
        self, resource_type: str, group: str | None = None
    ) -> list[dict[str, Any]]:
        """GET /rest/api/uom/{ResourceType} and parse the Atom feed."""
        path = f"/rest/api/uom/{resource_type}"
        if group:
            path += f"?group={group}"
        xml = await self._get(path, resource_type)
        if not xml:
            return []
        return _parse_feed(xml, path)

    async def get_uom(
        self, resource_type: str, uuid: str, group: str | None = None
    ) -> dict[str, Any] | None:
        """GET /rest/api/uom/{ResourceType}/{uuid} and parse the entry."""
        path = f"/rest/api/uom/{resource_type}/{uuid}"
        if group:
            path += f"?group={group}"
        xml = await self._get(path, resource_type)
        if not xml:
            return None
        entries = _parse_feed(xml, path)
        return entries[0] if entries else None

    async def get_quick_property(
        self, resource_type: str, uuid: str, property_name: str
    ) -> str | None:
        """GET a quick property, e.g. LogicalPartition/{uuid}/quick/PartitionState.

        quick/ endpoints return a plain-text value and require Accept: */* —
        a typed uom+xml Accept header causes HTTP 406.
        """
        path = f"/rest/api/uom/{resource_type}/{uuid}/quick/{property_name}"
        resp = await self._request("GET", path, headers={"Accept": "*/*"})
        if resp.status_code == 204:
            return None
        if resp.status_code != 200:
            raise HMCError(f"GET {path} failed", resp.status_code, resp.text)
        # The HMC sometimes wraps the value in double-quotes; strip them.
        value = resp.text.strip()
        if value.startswith('"') and value.endswith('"') and len(value) > 1:
            value = value[1:-1]
        return value or None

    async def search_uom(
        self, resource_type: str, property_name: str, property_value: str
    ) -> list[dict[str, Any]]:
        """GET /rest/api/uom/{ResourceType}/search/({Property}=={Value})."""
        path = (
            f"/rest/api/uom/{resource_type}/search/({property_name}=={property_value})"
        )
        xml = await self._get(path, resource_type)
        if not xml:
            return []
        return _parse_feed(xml, path)

    # ------------------------------------------------------------------ #
    # Virtual adapters (children of LogicalPartition)
    # ------------------------------------------------------------------ #

    async def list_child(
        self, parent_type: str, parent_uuid: str, child_type: str
    ) -> list[dict[str, Any]]:
        """GET /rest/api/uom/{parent}/{uuid}/{child} and parse the feed."""
        path = f"/rest/api/uom/{parent_type}/{parent_uuid}/{child_type}"
        xml = await self._get(path, child_type)
        return _parse_feed(xml, path) if xml else []

    async def create_child(
        self, parent_type: str, parent_uuid: str, child_type: str, child_xml: str
    ) -> dict[str, Any] | None:
        """PUT a child resource (e.g. a virtual adapter) under a parent.

        Omits X-HMC-Schema-Version header — the HMC returns HTTP 406 on adapter
        PUT endpoints when this header is present (same as VolumeGroup and LPAR).
        """
        path = f"/rest/api/uom/{parent_type}/{parent_uuid}/{child_type}"
        xml = await self._put(
            path, child_xml, resource_type=child_type, include_schema_version=False
        )
        entries = _parse_feed(xml, path) if xml else []
        return entries[0] if entries else None

    async def delete_child(
        self, parent_type: str, parent_uuid: str, child_type: str, child_uuid: str
    ) -> None:
        """DELETE a child resource instance."""
        await self._delete(
            f"/rest/api/uom/{parent_type}/{parent_uuid}/{child_type}/{child_uuid}"
        )

    async def get_uom_path(
        self, path: str, resource_type: str
    ) -> dict[str, Any] | None:
        xml = await self._get(path, resource_type)
        if not xml:
            return None
        entries = _parse_feed(xml, path)
        return entries[0] if entries else None

    # ------------------------------------------------------------------ #
    # Jobs (long-running operations)
    # ------------------------------------------------------------------ #

    async def submit_job(
        self, job_path: str, job_request_xml: str
    ) -> dict[str, Any] | None:
        """PUT a JobRequest to /rest/api/uom/.../do/{Operation} and return the job.

        `job_path` is the full do-path, e.g.
        /rest/api/uom/LogicalPartition/{uuid}/do/PowerOn

        The HMC requires PUT (not POST) for do/ job operations, web+xml media
        types, and atom+xml Accept — as confirmed by the ansible-power-hmc
        reference implementation.
        """
        resp = await self._request(
            "PUT",
            job_path,
            content=job_request_xml,
            headers={
                "Content-Type": f"{MEDIA_WEB}; type=JobRequest",
                "Accept": "application/atom+xml",
            },
        )
        if resp.status_code not in (200, 201, 202):
            raise HMCError(f"PUT {job_path} failed", resp.status_code, resp.text)
        entries = _parse_feed(resp.text, job_path) if resp.text else []
        return entries[0] if entries else None

    async def get_job(
        self,
        job_uuid: str,
        *,
        job_href: str | None = None,
    ) -> dict[str, Any] | None:
        """Fetch an HMC job by UUID.

        When *job_href* is provided (the SELF link returned by ``submit_job``),
        it is used directly so the request hits the per-operation path.

        HMC versions that do not expose ``Job`` as a root UOM resource type
        return HTTP 400 on ``GET /rest/api/uom/Job/{uuid}``.  Those versions
        use the ``web+xml`` content type for job responses (the SELF link in
        the submission response points to ``/rest/api/uom/jobs/{id}`` and
        requires ``Accept: application/vnd.ibm.powervm.web+xml``).  When a
        ``job_href`` is supplied the request is sent with the ``web+xml``
        Accept header so it works on both endpoint shapes (see issue #95).
        Without ``job_href`` the legacy uom path is used for backward compat.
        """
        if job_href:
            path = urlparse(job_href).path
            xml = await self._web_get(path)
            if not xml:
                return None
            entries = _parse_feed(xml, path)
            return entries[0] if entries else None
        return await self.get_uom("Job", job_uuid)

    async def wait_for_job(
        self,
        job_uuid: str,
        timeout_seconds: int = 300,
        poll_interval: int = 5,
        *,
        job_href: str | None = None,
    ) -> dict[str, Any] | None:
        """Poll an HMC job until it reaches a terminal state or timeout.

        Terminal states cover the UOM Job and documented web+xml JobResponse
        completion, failure, warning, and cancellation values.
        Returns the last-seen job entry (terminal or not, after timeout).

        When *job_href* is provided it is forwarded to ``get_job`` so polling
        uses the per-operation SELF link instead of the global UOM path.
        """
        import asyncio

        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be greater than or equal to 0")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be greater than 0")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        entry = await self.get_job(job_uuid, job_href=job_href)
        while True:
            resource = (entry or {}).get("Resource")
            status = resource.get("Status", "") if isinstance(resource, dict) else ""
            if status in TERMINAL_JOB_STATUSES:
                return entry
            remaining = deadline - loop.time()
            if remaining <= 0:
                return entry
            await asyncio.sleep(min(poll_interval, remaining))
            if loop.time() >= deadline:
                return entry
            entry = await self.get_job(job_uuid, job_href=job_href)

    async def delete_job(self, job_uuid: str) -> None:
        await self._delete(f"/rest/api/uom/Job/{job_uuid}")

    # ------------------------------------------------------------------ #
    # Raw escape hatch
    # ------------------------------------------------------------------ #

    async def raw_get(
        self, path: str, accept: str = "*/*"
    ) -> tuple[str, dict[str, str]]:
        """GET a raw path and return (body, response_headers).

        Returns a 2-tuple so callers can inspect response headers such as
        ``X-HMC-Schema-Version`` to discover the schema version in effect.
        """
        resp = await self._request("GET", path, headers={"Accept": accept})
        if resp.status_code == 204:
            return "", dict(resp.headers)
        if resp.status_code != 200:
            raise HMCError(f"GET {path} failed", resp.status_code, resp.text)
        return resp.text, dict(resp.headers)

    async def raw_post(
        self, path: str, body: str, content_type: str = "application/xml"
    ) -> str:
        resp = await self._request(
            "POST", path, content=body, headers={"Content-Type": content_type}
        )
        if resp.status_code not in (200, 201, 202):
            raise HMCError(f"POST {path} failed", resp.status_code, resp.text)
        return resp.text
