"""Async IBM HMC REST API client.

Owns the Logon/Logoff session lifecycle, the HTTP transport and the generic
uom helpers (list_uom / get_uom / search_uom / child resources / jobs / raw
escape hatch). Domain operations live in the per-domain mixin modules
(``client_users``, ``client_storage``, ``client_pcm``, ...) and are composed
into :class:`HMCClient` by inheritance.
"""

from __future__ import annotations

import warnings
from collections.abc import AsyncIterator
from collections.abc import Mapping
from typing import Any, Literal, get_args
import re
from threading import Lock
from urllib.parse import quote, unquote, urlparse

from ..audit import records as audit
from .client_contracts import httpx
from .client_parse import _find_text, _parse_feed
from ..config import HMCConfig, env_var_value
from ..documents import (
    build_brokered_file_document,
    build_linked_optical_media_document,
    build_logon_request_document,
)
from ..errors import HMCError, HMCTransportError
from ..jobs import TERMINAL_JOB_STATUSES
from ..resource_identity import is_uuid

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

# Media-type fragments used by the HMC API.
MEDIA_WEB = "application/vnd.ibm.powervm.web+xml"
MEDIA_WEB_JSON = "application/vnd.ibm.powervm.web+json"
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
# an unrelated `/rest/api/web/Logon/jobs` path through, because it contains the word.
_JOB_PATH = re.compile(r"^(?:/[^/]+)*/(?:Job|jobs)/[^/]+$")


def _reject_non_job_path(path: str) -> None:
    """Refuse a ``job_href`` that does not address a job.

    ``get_job`` fetches the caller's ``job_href`` directly, so the path — not the
    ``job_id`` argument — decides which resource is read. Without this, an
    unrelated web-resource href could be fetched through a tool classified
    ``read``/``job``.

    The check binds the *resource class*, not the identifier. Binding the last
    segment to ``job_id`` would be tighter, and was rejected: ``jobs.job_identifier``
    prefers the response's ``UUID``/``JobID`` over the link's last segment, so the
    two can legitimately differ — and issue #95 exists precisely because some
    firmware cannot resolve the job identifier, which is the case this argument
    serves and the one that cannot be tested here. Binding the class is what can
    be verified from this checkout.

    The residual is that a caller may read a *different* job. That is the reach
    an access-policy grant for these tools already confers: job identifiers are
    minted by the HMC at runtime and cannot be enumerated in a policy allowlist, so
    ADR 0039 marks both job tools ``exhaustive_targets=False`` and only
    ``targets = "all-targets"`` grants them — a grant that means "any job".
    After this check the tool can reach exactly what that grant says.
    """
    if not _JOB_PATH.match(unquote(path)):
        raise HMCError(
            "job_href refused: the link does not address a job resource. Pass "
            "the SELF link returned when the job was submitted."
        )


def _env_flag(value: str) -> bool | None:
    """Parse a boolean environment value the way pydantic-settings would.

    Returns ``None`` for anything unparseable. Unreachable in practice — a
    value pydantic cannot parse fails ``HMCConfig`` construction long before
    this runs — but kept total so the caller can never raise out of an
    otherwise-successful client construction (#379).
    """
    lowered = value.strip().lower()
    if lowered in {"1", "t", "true", "y", "yes", "on"}:
        return True
    if lowered in {"0", "f", "false", "n", "no", "off"}:
        return False
    return None


VerifySSLSource = Literal[
    "explicit-argument",
    "environment:HMC_VERIFY_SSL",
    "field-default",
]


class TLSVerificationDisabledWarning(UserWarning):
    """Warning emitted when HMC TLS certificate verification is disabled."""


_reported_tls_warning_keys: set[tuple[str, VerifySSLSource]] = set()
_tls_warning_lock = Lock()

#: The closed vocabulary, derived rather than restated — as ``audit.REASONS`` is from
#: ``audit.Reason``. ``audit`` imports nothing from ``hmc_mcp``, so its TLS record
#: builder still takes a plain ``str``; the narrowing lives here, at the only place
#: that produces a value. ``tests/test_authorization_audit_doc.py`` holds the two
#: documents that restate this set to it (#497), and holds
#: ``record_tls_verification_disabled``'s docstring and its test to naming this alias
#: instead of the values (#504). Its ledger records what is still out of reach; for this
#: vocabulary that is the literals in the documents' JSON sample records, unbackticked so
#: no extractor reads them, which #506 owns.
VERIFY_SSL_SOURCES: frozenset[str] = frozenset(get_args(VerifySSLSource))


def _verify_ssl_source(config: HMCConfig) -> VerifySSLSource:
    """Name where the effective ``verify_ssl`` value came from, for #379's audit record.

    The vocabulary is :data:`VerifySSLSource`, so a typo here is a type error.
    ``pydantic-settings`` folds environment values into the constructor kwargs, so
    ``model_fields_set`` alone cannot separate an explicit argument from an
    environment-sourced one once ``HMC_VERIFY_SSL`` is set; when both are present
    and disagree, the explicit argument won pydantic-settings' source priority, and
    when they agree they are indistinguishable and the environment is named —
    telling the operator which knob matches the effective value is what lets them
    change it. For a config the environment could not have reached, the two are
    distinguishable in principle but not from here, so that arm names the
    environment for an isolated config that supplied a matching ``verify_ssl``
    itself; it self-corrects the moment the two disagree.

    Because that folding is what puts an environment value into
    ``model_fields_set``, its *absence* is decisive the other way: nothing
    supplied the field, so the value is the field default and
    ``HMC_VERIFY_SSL`` is not the knob — even when it is set. That is the case
    ``HMCConfig.from_mapping`` produces (ADR 0096), where the environment cannot
    reach the config at all and naming it would send the operator to a variable
    that has no effect on this connection.

    The variable is read through :func:`config.env_var_value` because
    pydantic-settings folded it in case-insensitively; an exact-case read would
    report ``explicit-argument`` for a value nothing in the call supplied
    (#531). The vocabulary keeps the canonical spelling either way — it names
    the knob, not the operator's spelling of it.
    """
    if "verify_ssl" not in config.model_fields_set:
        return "field-default"
    raw = env_var_value("HMC_VERIFY_SSL")
    if raw is None or _env_flag(raw) != config.verify_ssl:
        return "explicit-argument"
    return "environment:HMC_VERIFY_SSL"


def _platform_response_error(field: str) -> HMCError:
    """Build a payload-safe malformed PlatformUpdate response error."""
    return HMCError(f"Malformed PlatformUpdate response: invalid {field}")


def _normalize_platform_update_response(payload: Any) -> dict[str, Any]:
    """Normalize IBM's JSON PlatformUpdate job into the shared job shape."""
    if not isinstance(payload, dict):
        raise _platform_response_error("root")
    job_id = payload.get("id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise _platform_response_error("id")
    content = payload.get("content")
    if not isinstance(content, dict):
        raise _platform_response_error("content")
    response = content.get("JobResponse")
    if not isinstance(response, dict):
        raise _platform_response_error("JobResponse")
    status = response.get("Status")
    if not isinstance(status, str) or not status.strip():
        raise _platform_response_error("Status")
    self_link = payload.get("selfLink")
    if self_link is not None and (
        not isinstance(self_link, str) or not self_link.strip()
    ):
        raise _platform_response_error("selfLink")

    resource = dict(response)
    if "Result" in resource:
        results = resource.pop("Result")
        if not isinstance(results, list):
            raise _platform_response_error("Result")
        normalized_results: list[dict[str, str]] = []
        for entry in results:
            if not isinstance(entry, dict):
                raise _platform_response_error("Result entry")
            name = entry.get("ParameterName")
            value = entry.get("ParameterValue")
            if not isinstance(name, str) or not name.strip():
                raise _platform_response_error("Result ParameterName")
            if not isinstance(value, str):
                raise _platform_response_error("Result ParameterValue")
            normalized_results.append({"ParameterName": name, "ParameterValue": value})
        resource["Results"] = {"JobParameter": normalized_results}

    normalized: dict[str, Any] = {"UUID": job_id.strip(), "Resource": resource}
    if isinstance(self_link, str):
        normalized["link"] = self_link.strip()
    return normalized


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

    def __init__(self, config: HMCConfig) -> None:
        config.validate_credentials()
        self.config = config
        self._session_token: str | None = None
        self._legacy_port_fallback = (
            config.port == 443 and "port" not in config.model_fields_set
        )
        self._verify_ssl_source = _verify_ssl_source(config)
        if not self.config.verify_ssl:
            # #379. Once per construction — not per request, which would flood
            # the sink, and not per process, which would miss a later client
            # built with different settings. The logon-time warnings.warn stays:
            # it is the CLI user's channel; this is the durable record's.
            audit.record_tls_verification_disabled(
                host=self.config.host,
                source=self._verify_ssl_source,
            )
        # X-Audit-Memento is evaluated once at construction time — this is safe
        # because each tool invocation creates a new HMCClient (via asyncio.run(_go)).
        # If the transport ever moves to a persistent shared client, this header would
        # stale when HMC_AGENT_ID changes; re-evaluate effective_audit_memento per-request
        # in that case.
        self._http = self._new_http_client(config.port)
        self._rest_base_url = str(self._http.base_url).rstrip("/")

    def _new_http_client(self, port: int) -> httpx.AsyncClient:
        base_url = httpx.URL(self.config.base_url).copy_with(port=port)
        return httpx.AsyncClient(
            base_url=base_url,
            verify=self.config.verify_ssl,
            timeout=self.config.timeout,
            headers={
                "X-Audit-Memento": self.config.effective_audit_memento,
                # Most HMC builds ignore charset but honour JSON when asked;
                # we stick to the canonical XML representation everywhere.
            },
        )

    # Session lifecycle

    async def __aenter__(self) -> "HMCClient":
        try:
            await self.logon()
        except BaseException:
            await self._http.aclose()
            raise
        return self

    async def __aexit__(self, _exc_type, exc, _traceback) -> None:
        """Log off and close the transport without masking the body's error.

        Cleanup runs even when the ``async with`` body raised. When it did,
        the body's exception is primary and wins: cleanup failures are
        attached to it via ``exc.add_note`` and never replace it — replacing
        the in-flight error would hide the failure that actually matters
        behind an incidental one. A failing logoff (an HMC rejection as
        :class:`HMCError`, or a transport failure as
        :class:`HMCTransportError`) is therefore recorded as a note on the
        body's exception rather than raised. Only when the body exited
        cleanly does a cleanup error propagate.
        """
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

        Emits one warning per ``(host, verify_ssl source)`` per process when TLS
        certificate verification is disabled, so the MITM exposure of the
        credentials in flight is never silent.
        """
        if not self.config.verify_ssl:
            warning_key = (self.config.host, self._verify_ssl_source)
            with _tls_warning_lock:
                if warning_key not in _reported_tls_warning_keys:
                    warnings.warn(
                        "TLS certificate verification is disabled (verify_ssl=False). "
                        "HMC credentials travel over an unverified TLS connection and "
                        "can be intercepted by a man-in-the-middle. Install the HMC's "
                        "CA locally and set HMC_VERIFY_SSL=true (or --verify-ssl) to "
                        "enable verification.",
                        TLSVerificationDisabledWarning,
                        stacklevel=2,
                    )
                    _reported_tls_warning_keys.add(warning_key)
        body = build_logon_request_document(
            user=self.config.user, password=self.config.password
        )
        try:
            token = await self._logon_once(body)
        except HMCTransportError:
            if not self._legacy_port_fallback or self._session_token is not None:
                raise
        else:
            self._legacy_port_fallback = False
            return token
        self._legacy_port_fallback = False
        await self._http.aclose()
        self._http = self._new_http_client(12443)
        self._rest_base_url = str(self._http.base_url).rstrip("/")
        return await self._logon_once(body)

    async def _logon_once(self, body: str) -> str:
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
        """Invalidate the session token (DELETE the Logon resource).

        The DELETE is expected to answer 200, 202, or 204; any other status
        raises :class:`HMCError`, so a rejected logoff is never mistaken for
        a closed session (ADR 0028). A transport-level failure surfaces as
        :class:`HMCTransportError` from ``_request`` — distinct from an HMC
        rejection, because they mean different things to a caller.

        Local state clears either way: the token and the ``X-API-Session``
        header are dropped even when the request fails, so a client that
        believes it is logged off never re-sends the dead token.
        """
        if not self._session_token:
            return
        try:
            resp = await self._request(
                "DELETE",
                "/rest/api/web/Logon",
                headers=self._web_headers({"Accept": MEDIA_WEB}),
            )
            if resp.status_code not in (200, 202, 204):
                raise HMCError("HMC logoff failed", resp.status_code, resp.text)
        finally:
            self._session_token = None
            self._http.headers.pop("X-API-Session", None)

    # Generic request helpers

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

    async def _request_with_uuid_path_arguments(
        self,
        method: str,
        path: str,
        *,
        uuid_path_arguments: Mapping[str, str],
        **kwargs: Any,
    ) -> httpx.Response:
        """Validate UUID-only path arguments before entering the transport."""
        for argument, value in uuid_path_arguments.items():
            if not is_uuid(value):
                raise HMCError(f"{argument} must be a UUID")
        return await self._request(method, path, **kwargs)

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
        *,
        uuid_path_arguments: Mapping[str, str] | None = None,
    ) -> str:
        resp = await self._request_with_uuid_path_arguments(
            "GET",
            path,
            uuid_path_arguments=uuid_path_arguments or {},
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
        *,
        uuid_path_arguments: Mapping[str, str] | None = None,
    ) -> str:
        headers = self._uom_headers(resource_type, include_schema_version)
        headers["Content-Type"] = headers["Accept"]
        resp = await self._request_with_uuid_path_arguments(
            "POST",
            path,
            uuid_path_arguments=uuid_path_arguments or {},
            content=body,
            headers=headers,
        )
        if resp.status_code not in (200, 201, 202):
            raise HMCError(f"POST {path} failed", resp.status_code, resp.text)
        return resp.text

    async def _put(
        self,
        path: str,
        body: str | bytes,
        resource_type: str | None = None,
        include_schema_version: bool = True,
        *,
        uuid_path_arguments: Mapping[str, str] | None = None,
    ) -> str:
        headers = self._uom_headers(resource_type, include_schema_version)
        headers["Content-Type"] = headers["Accept"]
        resp = await self._request_with_uuid_path_arguments(
            "PUT",
            path,
            uuid_path_arguments=uuid_path_arguments or {},
            content=body,
            headers=headers,
        )
        if resp.status_code not in (200, 201, 202, 204):
            raise HMCError(f"PUT {path} failed", resp.status_code, resp.text)
        return resp.text

    async def _delete(
        self,
        path: str,
        *,
        uuid_path_arguments: Mapping[str, str] | None = None,
    ) -> None:
        resp = await self._request_with_uuid_path_arguments(
            "DELETE",
            path,
            uuid_path_arguments=uuid_path_arguments or {},
            headers=self._uom_headers(None),
        )
        if resp.status_code not in (200, 202, 204):
            raise HMCError(f"DELETE {path} failed", resp.status_code, resp.text)

    # Brokered file upload helpers (/rest/api/web/File/)
    #
    # HMC uses a two-step brokered file protocol to import ISOs:
    #   1. PUT /rest/api/web/File/ — register the file entry; returns FileUUID
    #   2. PUT /rest/api/web/File/contents/{file_uuid} — stream the raw bytes
    #   The HMC then automatically imports the ISO into the VMLibrary.
    #   3. DELETE /rest/api/web/File/{file_uuid} — release the broker slot
    #
    # Reference: project-pim/cli/utils/iso_util.py (create_iso_path pattern)

    async def _broker_file_create(
        self, vios_uuid: str, vg_uuid: str, filename: str
    ) -> str:
        """Create a brokered file handle and return its URI."""
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        create_xml = build_brokered_file_document(filename=filename)
        resp = await self._request_with_uuid_path_arguments(
            "POST",
            path,
            uuid_path_arguments={"vios_uuid": vios_uuid, "vg_uuid": vg_uuid},
            content=create_xml,
            headers={"Content-Type": MEDIA_UOM, "Accept": MEDIA_UOM},
        )
        if resp.status_code not in (200, 201):
            raise HMCError(
                f"Brokered file create failed for {filename}",
                resp.status_code,
                resp.text,
            )
        location = resp.headers.get("Location")
        if not location:
            raise HMCError(
                "Brokered file create missing Location header",
                resp.status_code,
                resp.text,
            )
        return location

    async def _broker_file_upload(
        self,
        broker_uri: str,
        content: AsyncIterator[bytes],
        content_length: int,
    ) -> str:
        """Stream content to a brokered file and return its media UUID.

        The method never buffers the body: an ISO that passes the caller's size
        bound may be tens of gigabytes, and this process is shared by every caller
        of every tool (ADR 0052, #308).

        ``content`` must be an **async** iterator, and ``content_length`` the
        exact total it will yield. Both are constraints of the transport, not
        style, verified against httpx 0.28.1:

        - A file object or a sync generator becomes an ``IteratorByteStream``,
          which is a ``SyncByteStream``; ``AsyncClient._send_single_request``
          raises ``RuntimeError`` on one. Only an async iterator reaches the wire.
        - ``encode_content`` would set ``Transfer-Encoding: chunked`` for an
          iterator body, but ``Request._prepare`` skips that when an explicit
          ``Content-Length`` is already present — which is why the HMC, whose
          brokered upload requires ``Content-Length`` (ADR 0031), still gets one.

        The stream is consumed exactly once and cannot be replayed. Nothing in
        this path retries: ``_request`` sends once and only translates transport
        errors, ``AsyncClient`` is constructed without ``follow_redirects`` (so a
        3xx is returned, not re-sent), and the default transport does not retry a
        sent request. Re-sending the same ``Request`` raises ``StreamConsumed``;
        a *new* request around the exhausted iterator would send an empty body,
        which h11 then refuses against the unchanged ``Content-Length``. So a
        replay fails loudly rather than uploading a truncated ISO under a
        SHA-256 describing the whole file — but it still fails. If a retry,
        redirect-following, or a shared client is ever added above this method,
        the body must become re-creatable (a factory per attempt) in the same
        change.
        """
        resp = await self._request(
            "PUT",
            broker_uri,
            content=content,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(content_length),
                "Accept": MEDIA_UOM,
            },
        )
        if resp.status_code not in (200, 201, 202):
            raise HMCError(
                f"Brokered file upload failed to {broker_uri}",
                resp.status_code,
                resp.text,
            )
        return resp.text if resp.text else ""

    async def _broker_iso_import(
        self,
        vios_uuid: str,
        vg_uuid: str,
        media_name: str,
        broker_uri: str,
    ) -> str:
        """Import a brokered ISO and return its VirtualOpticalMedia UUID."""
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        import_xml = build_linked_optical_media_document(
            media_name=media_name, broker_uri=broker_uri
        )
        resp = await self._post(
            path,
            import_xml,
            resource_type="VolumeGroup",
            uuid_path_arguments={"vios_uuid": vios_uuid, "vg_uuid": vg_uuid},
        )
        return resp if resp else ""

    async def _broker_file_cleanup(self, broker_uri: str) -> None:
        """Delete a brokered file to release its resources."""
        resp = await self._request(
            "DELETE",
            broker_uri,
            headers={"Accept": MEDIA_UOM},
        )
        if resp.status_code not in (200, 202, 204, 404):
            raise HMCError(
                f"Brokered file cleanup failed for {broker_uri}",
                resp.status_code,
                resp.text,
            )

    # Web endpoint helpers (/rest/api/web/)
    #
    # The HMC exposes non-UOM resources under /rest/api/web/ with the MEDIA_WEB
    # content type. These helpers mirror
    # _get/_post/_delete but use MEDIA_WEB for Content-Type and Accept.
    #
    # The session token is set on the shared httpx client during logon and
    # therefore applies to documented web resources that use these helpers.

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

    # uom resources

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
        xml = await self._get(
            path, resource_type, uuid_path_arguments={"uuid": uuid}
        )
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
        resp = await self._request_with_uuid_path_arguments(
            "GET",
            path,
            uuid_path_arguments={"uuid": uuid},
            headers={"Accept": "*/*"},
        )
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
        encoded_property = quote(property_name, safe="")
        encoded_value = quote(property_value, safe="")
        path = (
            f"/rest/api/uom/{resource_type}/search/"
            f"({encoded_property}=={encoded_value})"
        )
        xml = await self._get(path, resource_type)
        if not xml:
            return []
        return _parse_feed(xml, path)

    # Virtual adapters (children of LogicalPartition)

    async def list_child(
        self, parent_type: str, parent_uuid: str, child_type: str
    ) -> list[dict[str, Any]]:
        """GET /rest/api/uom/{parent}/{uuid}/{child} and parse the feed."""
        path = f"/rest/api/uom/{parent_type}/{parent_uuid}/{child_type}"
        xml = await self._get(
            path,
            child_type,
            uuid_path_arguments={"parent_uuid": parent_uuid},
        )
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
            path,
            child_xml,
            resource_type=child_type,
            include_schema_version=False,
            uuid_path_arguments={"parent_uuid": parent_uuid},
        )
        entries = _parse_feed(xml, path) if xml else []
        return entries[0] if entries else None

    async def delete_child(
        self, parent_type: str, parent_uuid: str, child_type: str, child_uuid: str
    ) -> None:
        """DELETE a child resource instance."""
        await self._delete(
            f"/rest/api/uom/{parent_type}/{parent_uuid}/{child_type}/{child_uuid}",
            uuid_path_arguments={
                "parent_uuid": parent_uuid,
                "child_uuid": child_uuid,
            },
        )

    async def get_uom_path(
        self, path: str, resource_type: str
    ) -> dict[str, Any] | None:
        xml = await self._get(path, resource_type)
        if not xml:
            return None
        entries = _parse_feed(xml, path)
        return entries[0] if entries else None

    # Jobs (long-running operations)

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

    async def submit_platform_update(
        self, system_uuid: str, job_request: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """PUT one native JSON PlatformUpdate request and normalize its job."""
        system_path_id = quote(system_uuid, safe="")
        path = f"/rest/api/uom/ManagedSystem/{system_path_id}/do/PlatformUpdate"
        resp = await self._request(
            "PUT",
            path,
            json=job_request,
            headers={
                "Content-Type": f"{MEDIA_WEB_JSON}; type=JobRequest",
                "Accept": "application/json",
            },
        )
        if resp.status_code not in (200, 201, 202, 204):
            raise HMCError(f"PUT {path} failed", resp.status_code)
        if not resp.content or not resp.text.strip():
            return None
        try:
            payload = resp.json()
        except ValueError as exc:
            raise HMCError(
                "Malformed PlatformUpdate response: body is not valid JSON"
            ) from exc
        return _normalize_platform_update_response(payload)

    async def get_job(
        self,
        job_id: str,
        *,
        job_href: str | None = None,
    ) -> dict[str, Any] | None:
        """Fetch an HMC job by UUID or JobID.

        When *job_href* is provided (the SELF link returned by ``submit_job``),
        it is used directly so the request hits the per-operation path.

        The documented global endpoint is ``/rest/api/uom/jobs/{id}`` and uses
        the ``web+xml`` content type. When ``job_href`` is supplied, its job
        path remains preferred so per-operation SELF links work as returned by
        the HMC (see issue #95).
        """
        if job_href:
            path = urlparse(job_href).path
            _reject_non_job_path(path)
        else:
            path = f"/rest/api/uom/jobs/{job_id}"
        xml = await self._web_get(path)
        if not xml:
            return None
        entries = _parse_feed(xml, path)
        return entries[0] if entries else None

    async def wait_for_job(
        self,
        job_id: str,
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
        entry = await self.get_job(job_id, job_href=job_href)
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
            entry = await self.get_job(job_id, job_href=job_href)

    async def delete_job(
        self,
        job_id: str,
        *,
        job_href: str | None = None,
    ) -> None:
        """Delete a job, preferring its SELF link when available."""
        path = urlparse(job_href).path if job_href else f"/rest/api/uom/jobs/{job_id}"
        _reject_non_job_path(path)
        await self._delete(path)

    # Raw escape hatch

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
