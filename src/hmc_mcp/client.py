"""Async IBM HMC REST API client.

Owns the Logon/Logoff session lifecycle, the HTTP transport and the generic
uom helpers (list_uom / get_uom / search_uom / child resources / jobs / raw
escape hatch). Domain operations live in the per-domain mixin modules
(``client_users``, ``client_storage``, ``client_pcm``, ...) and are composed
into :class:`HMCClient` by inheritance.
"""

from __future__ import annotations

import warnings
from typing import Any
import re
from urllib.parse import unquote, urlparse

from .client_contracts import httpx
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

# The two RFC 3986 dot-segments. Held as a frozenset and compared per path
# segment rather than with a substring test, so a resource legitimately named
# "..log" or "a..b" is not refused for containing the characters.
_DOT_SEGMENTS: frozenset[str] = frozenset({".", ".."})


def _reject_dot_segments(method: str, path: str) -> None:
    """Refuse a request path that could resolve away from the resource it names.

    Raised as ``HMCError`` because it is a request this client will not send,
    which is what every other pre-flight refusal here is. The message names the
    method and the offending segment only — never the full path, which on the
    CLI and API paths can carry an operator's own filesystem-derived values.
    """
    candidate = urlparse(path).path if "://" in path else path
    # Raw *and* percent-decoded. httpx resolves only the raw form, so an earlier
    # version of this guard checked only that and reasoned that `%2e%2e` "addresses
    # nothing". That was an assumption about how the HMC's own web stack decodes a
    # path — untestable from here, and the wrong way round for a fail-closed check.
    # A single decode is enough: `%252e` decodes to `%2e`, not to `.`, so nothing
    # this rejects can be reached by decoding again.
    for form in (candidate, unquote(candidate)):
        for segment in form.split("/"):
            if segment in _DOT_SEGMENTS:
                raise HMCError(
                    f"{method.upper()} refused: the request path contains a "
                    f"{segment!r} segment, which would resolve to a different "
                    "resource than the one addressed. Pass an identifier, not a path."
                )


# The two shapes an HMC job SELF link takes: the legacy uom resource type
# (`/rest/api/uom/Job/{uuid}`) and the per-operation collection the submission
# response points at (`/rest/api/uom/jobs/{id}`, issue #95). Anchored on the
# *last two* segments rather than tested for membership: membership let
# `/rest/api/web/HmcUser/jobs` through, because it contains the word.
_JOB_PATH = re.compile(r"^(?:/[^/]+)*/(?:Job|jobs)/[^/]+$")


def _reject_non_job_path(path: str) -> None:
    """Refuse a ``job_href`` that does not address a job.

    ``get_job`` fetches the caller's ``job_href`` directly, so the path — not the
    ``job_uuid`` argument — decides which resource is read. ``_web_get`` sends
    the same ``web+xml`` Accept header ``client_users.get_hmc_user`` uses, so
    without this an ``href`` of ``/rest/api/web/HmcUser/root`` returns the root
    account record through a tool classified ``read``/``job``.

    The check binds the *resource class*, not the identifier. Binding the last
    segment to ``job_uuid`` would be tighter, and was rejected: ``jobs.job_identifier``
    prefers the response's ``UUID``/``JobID`` over the link's last segment, so the
    two can legitimately differ — and issue #95 exists precisely because some
    firmware cannot resolve the UUID, which is the case this argument serves and
    the one that cannot be tested here. Binding the class is what can be verified
    from this checkout.

    The residual is that a caller may read a *different* job. That is the reach
    an access-policy grant for these tools already confers: job UUIDs are minted
    by the HMC at runtime and cannot be enumerated in a policy allowlist, so
    ADR 0039 marks both job tools ``exhaustive_targets=False`` and only
    ``targets = "all-targets"`` grants them — a grant that means "any job".
    After this check the tool can reach exactly what that grant says.
    """
    if not _JOB_PATH.match(unquote(path)):
        raise HMCError(
            "job_href refused: the link does not address a job resource. Pass "
            "the SELF link returned when the job was submitted."
        )


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
        """Send one REST request and normalize transport-layer failures.

        Refuses a path carrying an RFC 3986 dot-segment before anything leaves
        the process. Every REST path in this package is built by interpolating
        caller-supplied identifiers into an f-string — ``/VirtualIOServer/
        {vios_uuid}/VolumeGroup/{vg_uuid}`` and a dozen siblings — and httpx
        *resolves* dot-segments when merging a path onto ``base_url``. Verified
        against httpx 0.28.1: a ``vg_uuid`` of ``../../../LogicalPartition/X``
        sends ``DELETE /rest/api/uom/LogicalPartition/X``.

        That silently retargets the request at a resource the caller never
        named, which defeats every layer above it. The MCP access policy
        authorizes the *declared* selectors (ADR 0039), so a grant scoped to one
        VIOS would permit a call that deletes an arbitrary partition; the CLI and
        the ``api`` facade have no policy at all and are equally exposed. So the
        guard lives here, at the one waist all three paths cross, rather than at
        the thirteen interpolation sites or at the authorization boundary only
        two of them reach.

        Percent-encoded dot-segments are refused too. An earlier version of this
        docstring said they were deliberately allowed through because "httpx does
        not resolve them either, so they reach the HMC as literal path text and
        address nothing". Only the first half of that is verified: httpx 0.28.1
        leaves ``%2e%2e`` and ``..%2f`` untouched, confirmed here. The second half
        is a claim about whether the *HMC's* server decodes a path before routing
        it, which nothing in this repository can establish and which many HTTP
        servers do. So the check reads the raw and the once-decoded form, and the
        claim it makes is one this checkout can actually support: no dot-segment
        reaches the transport in any encoding. No legitimate identifier in this
        API contains a percent sign, so the refusal costs nothing.
        """
        _reject_dot_segments(method, path)
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
    # Brokered file upload/import transport primitives (verification only)
    # ------------------------------------------------------------------ #
    #
    # These methods exercise the complete brokered upload/import sequence at
    # the transport boundary. They are deliberately kept private (_ prefix) and
    # not exposed through the public API contract. Their purpose is to:
    # - Verify endpoint paths, media types, and request/response formats
    # - Record cleanup behavior and version-dependent failures
    # - Determine whether imported media exposes trustworthy checksums
    #
    # Public MCP tools, CLI commands, and api.py exports will be added in a
    # follow-up issue (#203) after the verification findings are recorded.
    # ------------------------------------------------------------------ #

    async def _broker_file_create(self, vios_uuid: str, vg_uuid: str, filename: str) -> str:
        """Create a brokered file handle for upload (verification primitive).

        Returns the brokered file URI from the Location header.
        This method exists to verify the broker creation endpoint behavior.
        """
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        # brokered file creation payload - the exact structure is version-dependent
        # this placeholder exercises the endpoint to record the actual contract
        create_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<BrokeredFile xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
  <Filename>{filename}</Filename>
</BrokeredFile>
'''
        resp = await self._request(
            "POST",
            path,
            content=create_xml,
            headers={"Content-Type": MEDIA_UOM, "Accept": MEDIA_UOM},
        )
        if resp.status_code not in (200, 201):
            raise HMCError(
                f"Brokered file create failed for {filename}",
                resp.status_code,
                resp.text,
            )
        # Extract Location header containing the brokered file URI
        location = resp.headers.get("Location")
        if not location:
            raise HMCError(
                "Brokered file create missing Location header",
                resp.status_code,
                resp.text,
            )
        return location

    async def _broker_file_upload(self, broker_uri: str, content: bytes) -> str:
        """Upload content to a brokered file (verification primitive).

        Streams the content to the broker URI and returns the final media UUID.
        This method exists to verify the upload endpoint behavior and response format.
        """
        resp = await self._request(
            "PUT",
            broker_uri,
            content=content,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(content)),
                "Accept": MEDIA_UOM,
            },
        )
        if resp.status_code not in (200, 201, 202):
            raise HMCError(
                f"Brokered file upload failed to {broker_uri}",
                resp.status_code,
                resp.text,
            )
        # Parse response to extract the media UUID (structure version-dependent)
        # This placeholder exercises the endpoint to record the actual contract
        return resp.text if resp.text else ""

    async def _broker_iso_import(
        self,
        vios_uuid: str,
        vg_uuid: str,
        media_name: str,
        broker_uri: str,
    ) -> str:
        """Import an uploaded ISO into the Virtual Media Library (verification primitive).

        Creates VirtualOpticalMedia linked to the brokered file and returns the media UUID.
        This method exists to verify the import endpoint behavior and checksum handling.
        """
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        import_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<LinkedVirtualOpticalMedia xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
  <MediaName>{media_name}</MediaName>
  <LinkedFileURI>{broker_uri}</LinkedFileURI>
</LinkedVirtualOpticalMedia>
'''
        resp = await self._post(path, import_xml, resource_type="VolumeGroup")
        # Parse response to extract checksum information (if exposed)
        # This placeholder exercises the endpoint to record whether checksums are available
        return resp if resp else ""

    async def _broker_file_cleanup(self, broker_uri: str) -> None:
        """Clean up a brokered file (verification primitive).

        Deletes the brokered file to release resources after import or on failure.
        This method exists to verify cleanup behavior and error handling.
        """
        resp = await self._request(
            "DELETE",
            broker_uri,
            headers={"Accept": MEDIA_UOM},
        )
        # Some versions may return 404 for already-deleted brokered files
        if resp.status_code not in (200, 202, 204, 404):
            raise HMCError(
                f"Brokered file cleanup failed for {broker_uri}",
                resp.status_code,
                resp.text,
            )

    async def _verify_imported_checksum(
        self, vios_uuid: str, vg_uuid: str, media_name: str
    ) -> dict[str, str] | None:
        """Query imported media for checksum information (verification primitive).

        Returns a dict with checksum type and value if exposed by the HMC.
        Returns None if checksum information is not available.
        This method exists to verify whether repository inventory exposes trustworthy checksums.
        """
        path = (
            f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
            f"/VirtualMediaRepository/VMLibrary/VirtualOpticalMedia"
        )
        resp_text = await self._get(path)
        if not resp_text:
            return None
        # Parse the feed to find the named media and extract checksum fields
        # The actual checksum field names are version-dependent
        # This placeholder exercises the endpoint to record available checksum data
        return None  # To be replaced with actual checksum extraction after verification
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
            _reject_non_job_path(path)
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
