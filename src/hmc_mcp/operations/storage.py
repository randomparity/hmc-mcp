"""Presentation-neutral VIOS storage operations."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlparse

from hmc_mcp.client.core import HMCClient
from hmc_mcp.operations.ownership import resolve_and_authorize_lpar_mutation

from ..client.client_contracts import httpx
from ..config import ISO_URL_ALLOWLIST_HELP
from ..documents import StorageKind
from ..errors import HMCError
from ..jobs import (
    DEFAULT_JOB_POLL_INTERVAL,
    DEFAULT_JOB_TIMEOUT_SECONDS,
    DeviceType,
    LuType,
    validate_logical_unit_types,
    validate_wait_timing,
    wait_for_submitted_job,
)
from ..resource_identity import resolve_lpar_uuid, resolve_vios_uuid

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StorageMapResult:
    """Authorized LPAR identity and the resulting VIOS storage resource."""

    lpar_uuid: str
    resource: dict[str, Any] | None


async def list_clusters(hmc: HMCClient) -> list[dict[str, Any]]:
    """List clusters through the shared presentation-neutral operation seam."""
    return await hmc.list_clusters()


async def list_shared_storage_pools(hmc: HMCClient) -> list[dict[str, Any]]:
    """List shared storage pools through the shared operation seam."""
    return await hmc.list_shared_storage_pools()


async def get_shared_storage_pool(
    hmc: HMCClient, ssp_uuid: str
) -> dict[str, Any] | None:
    """Get one shared storage pool by UUID."""
    return await hmc.get_shared_storage_pool(ssp_uuid)


# HTTP download configuration
CONNECT_TIMEOUT = 30.0
READ_TIMEOUT = 300.0
MAX_DOWNLOAD_SIZE_BYTES = 100 * 1024 * 1024 * 1024  # 100 GiB
DEFAULT_CHUNK_SIZE = 8192
# The upload's chunk is a *wire* unit, not a read hint: httpcore issues one
# `network_stream.write` per chunk an async iterator yields
# (`httpcore/_async/http11.py:157-166`), where a `bytes` body produced one write
# for the whole payload. 8 KiB would make a 20 GiB ISO 2.6 million writes.
# 64 KiB is httpx's own streaming unit (`AsyncIteratorByteStream.CHUNK_SIZE`).
UPLOAD_CHUNK_SIZE = 64 * 1024


async def list_volume_groups(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    *,
    system_name_or_uuid: str | None = None,
) -> list[dict[str, Any]]:
    """List volume groups on a VIOS."""
    vios_uuid = await resolve_vios_uuid(
        hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    return await hmc.list_volume_groups(vios_uuid)


async def create_volume_group(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    name: str,
    physical_volumes: list[str],
    *,
    system_name_or_uuid: str | None = None,
) -> dict[str, Any] | None:
    """Create a volume group from the selected physical volumes."""
    return await hmc.create_volume_group(
        await resolve_vios_uuid(
            hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
        ),
        name,
        physical_volumes,
    )


async def create_virtual_disk(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    vg_uuid: str,
    name: str,
    capacity_mib: int,
    *,
    system_name_or_uuid: str | None = None,
) -> dict[str, Any] | None:
    """Create a virtual disk of ``capacity_mib`` in a volume group."""
    return await hmc.create_virtual_disk(
        await resolve_vios_uuid(
            hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
        ),
        vg_uuid,
        name,
        capacity_mib,
    )


async def delete_virtual_disk(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    vg_uuid: str,
    disk_name: str,
    *,
    system_name_or_uuid: str | None = None,
) -> dict[str, Any] | None:
    """Delete a Virtual Disk from a Volume Group.

    Validates that the disk is not mapped to any LPAR before deletion.

    Args:
        hmc: HMC client instance.
        system_name_or_uuid: Optional managed-system name or UUID used to scope
            a VIOS name.
        vios_name_or_uuid: VIOS partition name or UUID.
        vg_uuid: Volume Group UUID containing the disk.
        disk_name: Name of the Virtual Disk to delete.

    Returns:
        Deleted disk metadata when supplied by the HMC, or ``None`` when a
        successful deletion response contains no resource entry.

    Raises:
        HMCError: If the disk is mapped to an LPAR or deletion fails.
    """
    vios_uuid = await resolve_vios_uuid(
        hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    mappings = await hmc.list_storage_mappings(vios_uuid)
    disk_link = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}/VirtualDisk/{disk_name}"

    for mapping in mappings:
        backing_storage = mapping.get("Storage", {}).get("VirtualDisk", {})
        if isinstance(backing_storage, dict):
            storage_link = backing_storage.get("href", "")
            if disk_link in storage_link or storage_link.endswith(
                f"VirtualDisk/{disk_name}"
            ):
                lpar = mapping.get("AssociatedLogicalPartition", {})
                lpar_name = lpar.get("PartitionName", lpar.get("href", "unknown"))
                raise HMCError(
                    f"Cannot delete virtual disk {disk_name!r}: it is mapped to "
                    f"LPAR {lpar_name!r}. Use detach_storage_mapping first to "
                    "remove the mapping."
                )

    return await hmc.delete_virtual_disk(vios_uuid, vg_uuid, disk_name)


async def map_storage(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    lpar_name_or_uuid: str,
    *,
    system_name_or_uuid: str | None = None,
    kind: StorageKind,
    storage_name: str,
    target: str | None = None,
    ownership_override: bool = False,
) -> StorageMapResult:
    """Authorize an LPAR and map VIOS storage to it."""
    vios_uuid = await resolve_vios_uuid(
        hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    lpar_uuid = await resolve_and_authorize_lpar_mutation(
        hmc,
        system_name_or_uuid,
        lpar_name_or_uuid,
        ownership_override=ownership_override,
    )
    resource = await hmc.map_storage_to_lpar(
        vios_uuid, kind, storage_name, lpar_uuid, target
    )
    return StorageMapResult(lpar_uuid, resource)


async def create_media_repository(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    vg_uuid: str,
    size_mib: int,
    *,
    system_name_or_uuid: str | None = None,
) -> dict[str, Any] | None:
    """Create a media repository in a VIOS volume group."""
    return await hmc.create_media_repository(
        await resolve_vios_uuid(
            hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
        ),
        vg_uuid,
        size_mib,
    )


async def create_optical_media(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    vg_uuid: str,
    name: str,
    size_mib: int,
    *,
    system_name_or_uuid: str | None = None,
) -> dict[str, Any] | None:
    """Create blank optical media in a VIOS media repository."""
    return await hmc.create_optical_media(
        await resolve_vios_uuid(
            hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
        ),
        vg_uuid,
        name,
        size_mib,
    )


async def list_storage_mappings(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    lpar_name_or_uuid: str | None = None,
    *,
    system_name_or_uuid: str | None = None,
) -> list[dict[str, Any]]:
    """List VirtualSCSIMappings on a VIOS, optionally scoped to an LPAR.

    Returns mappings with backing storage details and client LPAR information.
    Use lpar to scope mappings to a single partition by name or UUID.
    """
    vios_uuid = await resolve_vios_uuid(
        hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    lpar_uuid = None
    if lpar_name_or_uuid:
        lpar_uuid = await resolve_lpar_uuid(
            hmc, lpar_name_or_uuid, system_name_or_uuid=system_name_or_uuid
        )
    return await hmc.list_storage_mappings(vios_uuid, lpar_uuid)


async def detach_storage_mapping(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    mapping_uuid: str,
    *,
    system_name_or_uuid: str | None = None,
    ownership_override: bool = False,
) -> None:
    """Authorize the mapped LPAR, then detach its VirtualSCSIMapping.

    ``mapping_uuid`` is the exact UUID returned by ``list_storage_mappings``.
    """
    vios_uuid = await resolve_vios_uuid(
        hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    mappings = await hmc.list_storage_mappings(vios_uuid)
    mapping = next(
        (item for item in mappings if item.get("UUID") == mapping_uuid), None
    )
    if mapping is None:
        raise ValueError(
            f"Storage mapping {mapping_uuid!r} was not found on VIOS {vios_name_or_uuid!r}"
        )
    href = (mapping.get("AssociatedLogicalPartition") or {}).get("href")
    path = urlparse(href).path if isinstance(href, str) else ""
    marker = "/rest/api/uom/LogicalPartition/"
    if not path.startswith(marker) or not path[len(marker) :]:
        raise ValueError(
            f"Storage mapping {mapping_uuid!r} does not identify its client LPAR"
        )
    lpar_uuid = path[len(marker) :]
    if "/" in lpar_uuid:
        raise ValueError(
            f"Storage mapping {mapping_uuid!r} has an invalid client LPAR link"
        )
    await resolve_and_authorize_lpar_mutation(
        hmc,
        system_name_or_uuid,
        lpar_uuid,
        ownership_override=ownership_override,
    )
    await hmc.delete_storage_mapping(vios_uuid, mapping_uuid)


async def delete_media_repository(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    vg_uuid: str,
    *,
    system_name_or_uuid: str | None = None,
) -> str:
    """Delete the Virtual Media Repository from a Volume Group.

    Refuses deletion if the repository contains any VirtualOpticalMedia
    entries (ISO images). Delete all media first.

    Raises:
        HMCError: If the repository is not empty.
    """
    vios_uuid = await resolve_vios_uuid(
        hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    media = await hmc.list_optical_media(vios_uuid, vg_uuid)
    if media:
        names = ", ".join(m.get("MediaName", "unknown") for m in media)
        raise HMCError(
            f"Cannot delete media repository: it contains {len(media)} "
            f"image(s): {names!r}. Delete all images first."
        )
    await hmc.delete_media_repository(vios_uuid, vg_uuid)
    return vios_uuid


async def delete_optical_media(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    vg_uuid: str,
    media_name: str,
    *,
    system_name_or_uuid: str | None = None,
) -> dict[str, Any] | None:
    """Delete a VirtualOpticalMedia (ISO image) from the media repository.

    Validates that no optical mapping references the media before deletion.
    Returns an error listing every blocking mapping when the image is in use.

    Raises:
        HMCError: If the media is referenced by any optical mapping.
    """
    vios_uuid = await resolve_vios_uuid(
        hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )

    # Exhaustively check optical mappings for references to this media
    optical_mappings = await hmc.list_optical_mappings(vios_uuid)
    blockers = []
    for mapping in optical_mappings:
        storage = mapping.get("Storage", {}).get("VirtualOpticalMedia", {})
        if isinstance(storage, dict):
            name = storage.get("MediaName", "")
            if name == media_name:
                lpar = mapping.get("AssociatedLogicalPartition", {})
                lpar_id = lpar.get("href", lpar.get("PartitionName", "unknown"))
                blockers.append(str(lpar_id))

    if blockers:
        raise HMCError(
            f"Cannot delete optical media {media_name!r}: it is mounted on "
            f"{len(blockers)} LPAR(s): {', '.join(blockers)!r}. "
            "Unmount the media first."
        )

    return await hmc.delete_optical_media(vios_uuid, vg_uuid, media_name)


async def get_media_repository(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    vg_uuid: str,
    *,
    system_name_or_uuid: str | None = None,
) -> dict[str, Any] | None:
    """Get the Virtual Media Repository (VMLibrary) from a Volume Group.

    Returns the repository with capacity (RepositorySize) and optionally
    embedded VirtualOpticalMedia entries if present.
    """
    return await hmc.get_media_repository(
        await resolve_vios_uuid(
            hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
        ),
        vg_uuid,
    )


ACCEPTED_ISO_SCHEMES = ("http", "https")


def _require_http_url(iso_source: str) -> str:
    """Return ``iso_source`` if it is an http(s) URL, else raise ``ValueError``.

    This is the first half of ``upload_iso``'s input validation — the host check
    in ``_require_allowlisted_iso_url`` is the other — and it runs before the
    operation touches the filesystem, the network, or the HMC. ADR 0049
    records why: anything that is not an http(s) URL used to be read as a path on
    the MCP server's own host, so a caller holding a grant for the tool could
    have any file the server process could read uploaded into a VIOS media
    repository (#261). Refusing by scheme, first, closes that by construction —
    there is no path for traversal or a symlink to escape through.

    The message is identical whether or not a file exists at the rejected value,
    and is derived only from the caller's own input, so a refusal discloses
    nothing about the server's filesystem.
    """
    if urlparse(iso_source).scheme not in ACCEPTED_ISO_SCHEMES:
        accepted = " or ".join(f"{scheme}://" for scheme in ACCEPTED_ISO_SCHEMES)
        raise ValueError(
            f"iso_source must be an {accepted} URL: got {iso_source!r}. "
            "The ISO is downloaded over HTTP(S); a path on the MCP server's "
            "filesystem is not an accepted source. Publish the ISO on a web "
            "server reachable from the MCP server and pass its URL."
        )
    return iso_source


DEFAULT_SCHEME_PORTS = {"http": 80, "https": 443}


def _require_allowlisted_iso_url(
    iso_url: str, allowlist: tuple[tuple[str, int | None], ...]
) -> str:
    """Return ``iso_url`` if its host is on the operator's allowlist, else raise.

    ``hmc_upload_iso`` fetches from the MCP server's network position, so a
    caller who chooses the URL chooses a destination inside the server's segment
    — instance-metadata endpoints, loopback services, hosts the caller cannot
    route to (#303). Nothing in the access policy bounds that: no ``TargetKind``
    names a network endpoint the server can reach (ADR 0039). Only the operator
    knows which ISO servers are legitimate, so only the operator can say.

    **An unset allowlist refuses every URL.** There is no safe default
    destination to fall back to, and a default that fetched anything would be
    the defect. The message names the setting because a fail-closed default that
    produces an opaque error is a support burden.

    The check runs before the fetch and resolves no name, so a refused host that
    exists and one that does not are refused identically — in message, because
    the text is derived only from the caller's input and the operator's
    allowlist, and in timing, because neither DNS nor a socket is touched.
    """
    if not allowlist:
        raise ValueError(
            f"iso_source is refused: got {iso_url!r}, and no ISO download "
            "allowlist is configured. hmc_upload_iso downloads from the MCP "
            "server's network position, so it refuses every URL until an "
            f"operator names the ISO servers it may reach. {ISO_URL_ALLOWLIST_HELP}"
        )
    parsed = urlparse(iso_url)
    try:
        port = parsed.port
    except ValueError:
        port = 0
    if port == 0:
        # An unusable port never reaches the allowlist comparison: falling back
        # to the scheme default here would let ``host:99999`` match an allowlist
        # entry pinned to ``host:443``.
        raise ValueError(
            f"iso_source is refused: got {iso_url!r}, which does not carry a "
            "usable TCP port. Pass an http(s) URL whose port is between 1 and "
            "65535, or omit the port to use the scheme's default."
        )
    host = parsed.hostname
    effective_port = (
        port if port is not None else DEFAULT_SCHEME_PORTS.get(parsed.scheme.lower())
    )
    for allowed_host, allowed_port in allowlist:
        if host == allowed_host and allowed_port in (None, effective_port):
            return iso_url
    permitted = ", ".join(
        host_ if port_ is None else f"{host_}:{port_}" for host_, port_ in allowlist
    )
    raise ValueError(
        f"iso_source is refused: got {iso_url!r}, whose host is not on the ISO "
        f"download allowlist. Permitted: {permitted}. Publish the ISO on one of "
        "those hosts, or have an operator add this one. " + ISO_URL_ALLOWLIST_HELP
    )


async def _download_iso_from_url(url: str) -> tuple[Path, str, int]:
    """Download an ISO file from an HTTP(S) URL with bounds and validation.

    The scheme and the host are the caller's responsibility: ``upload_iso``
    admits only what ``_require_http_url`` and ``_require_allowlisted_iso_url``
    accept, and it is this function's only caller.

    **Redirects are not followed.** Following them would send the fetch to a URL
    the allowlist never saw: a permitted host that answers ``302
    http://169.254.169.254/…`` would defeat a check applied to the URL the
    caller passed (#303). A redirect response is refused rather than followed,
    so the URL fetched is always the URL checked. ``raise_for_status`` does not
    cover this — 3xx is not an error status — so the refusal is explicit.

    Args:
        url: HTTP(S) URL to download the ISO from.

    Returns:
        Tuple of (temp_file_path, sha256_hexdigest, file_size_bytes).

    Raises:
        ValueError: If the response is a redirect, or the download exceeds
            ``MAX_DOWNLOAD_SIZE_BYTES``.
        httpx.TimeoutException: If connection or read timeout is exceeded.
        httpx.HTTPStatusError: If HTTP request fails (4xx/5xx).
    """
    timeout = httpx.Timeout(CONNECT_TIMEOUT, read=READ_TIMEOUT)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
    ) as client, client.stream("GET", url) as response:
        # Every 3xx, not just httpx's `is_redirect` (which additionally
        # requires a Location header): a 3xx without one is not a body to
        # import into a media repository either.
        if 300 <= response.status_code < 400:
            raise ValueError(
                f"iso_source is refused: {url!r} answered with HTTP "
                f"{response.status_code}, a redirect. The ISO must be served "
                "at the URL you pass, because a redirect would move the "
                "download to a host the operator's allowlist never checked. "
                "Pass the URL the ISO is actually served from."
            )
        response.raise_for_status()

        import tempfile

        fd, temp_path = tempfile.mkstemp(suffix=".iso", prefix="hmc_upload_")
        temp_file = Path(temp_path)

        sha256_hash = hashlib.sha256()
        downloaded_size = 0

        try:
            with os.fdopen(fd, "wb") as f:
                async for chunk in response.aiter_bytes(
                    chunk_size=DEFAULT_CHUNK_SIZE
                ):
                    downloaded_size += len(chunk)
                    if downloaded_size > MAX_DOWNLOAD_SIZE_BYTES:
                        raise ValueError(
                            f"Download size {downloaded_size} bytes exceeds "
                            f"maximum allowed size of {MAX_DOWNLOAD_SIZE_BYTES} bytes"
                        )

                    f.write(chunk)
                    sha256_hash.update(chunk)

            iso_sha256 = sha256_hash.hexdigest()
            return temp_file, iso_sha256, downloaded_size

        except Exception:
            # Clean up temp file on any error
            try:
                temp_file.unlink(missing_ok=True)
            except OSError:
                logger.exception(
                    "failed to remove incomplete ISO download %s", temp_file
                )
            raise


async def _aiter_file_chunks(
    handle: BinaryIO, chunk_size: int = UPLOAD_CHUNK_SIZE
) -> AsyncIterator[bytes]:
    """Yield *handle*'s remaining bytes in ``chunk_size`` pieces.

    The upload body httpx accepts from an ``AsyncClient`` is an async iterator —
    a file object or a sync generator raises ``RuntimeError`` at send time — so
    the file is read here rather than handed over. *handle* is left open and
    closed by the caller, which is what guarantees the descriptor is released on
    every outcome rather than at some later finalization of this generator.
    """
    while chunk := await asyncio.to_thread(handle.read, chunk_size):
        yield chunk


async def list_optical_media(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    vg_uuid: str,
    *,
    system_name_or_uuid: str | None = None,
) -> list[dict[str, Any]]:
    """List Virtual Optical Media in the Virtual Media Repository.

    Returns a list of optical media entries (ISO containers) with their
    MediaName, MediaSize, and MediaType. The repository must exist
    (VMLibrary on the specified Volume Group).
    """
    return await hmc.list_optical_media(
        await resolve_vios_uuid(
            hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
        ),
        vg_uuid,
    )


async def _upload_iso_via_broker(
    hmc: HMCClient,
    vios_uuid: str,
    vg_uuid: str,
    media_name: str,
    iso_path: Path,
    file_size: int,
) -> dict[str, Any] | None:
    """Run the HMC broker allocation, upload, import, and release transaction."""
    broker_uri: str | None = None
    try:
        broker_uri = await hmc._broker_file_create(vios_uuid, vg_uuid, media_name)
        with iso_path.open("rb") as handle:
            await hmc._broker_file_upload(
                broker_uri, _aiter_file_chunks(handle), file_size
            )
        await hmc._broker_iso_import(vios_uuid, vg_uuid, media_name, broker_uri)

        updated_media = await hmc.list_optical_media(vios_uuid, vg_uuid)
        return next(
            (media for media in updated_media if media.get("MediaName") == media_name),
            None,
        )
    finally:
        primary_error = sys.exception()
        if broker_uri:
            try:
                await hmc._broker_file_cleanup(broker_uri)
            except Exception:
                if primary_error is None:
                    raise
                logger.exception("broker cleanup failed for ISO upload %s", broker_uri)


async def upload_iso(
    hmc: HMCClient,
    system_name_or_uuid: str | None,
    vios_name_or_uuid: str,
    vg_uuid: str,
    media_name: str,
    iso_source: str,
) -> dict[str, Any]:
    """Upload an ISO to a VIOS media repository via the HMC file broker.

    ``iso_source`` must be an ``http`` or ``https`` URL whose host the operator
    has put on ``iso_url_allowlist`` (``HMC_ISO_URL_ALLOWLIST``), and both
    conditions are checked before any other work happens. **With no allowlist
    configured every URL is refused** — see ``_require_allowlisted_iso_url`` and
    ADR 0050. The media name is then validated against HMC's FileName.Pattern
    and refused on collision with existing media before any transfer begins;
    the ISO is downloaded with explicit timeout and size bounds and without
    following redirects, with SHA-256 and size computed from the download; and
    both the local temp file and the HMC broker resources are cleaned up on
    every outcome.

    Args:
        hmc: HMC client instance.
        system_name_or_uuid: Optional managed-system name or UUID used to scope
            a VIOS name.
        vios_name_or_uuid: VIOS name or UUID.
        vg_uuid: Volume Group UUID containing the media repository.
        media_name: Target name for the ISO in the repository.
        iso_source: HTTP(S) URL to download the ISO from.

    Returns:
        Dict with:
        - 'status': 'uploaded'
        - 'media_name': Name of the media in the repository.
        - 'media_size_bytes': Size of the uploaded ISO.
        - 'sha256': SHA-256 checksum of the uploaded ISO.
        - 'media': Full uploaded media entry dict, or None when the HMC's
          post-import inventory does not include it.

    Raises:
        HMCError: For HMC API errors during broker operations or import.
        ValueError: If ``iso_source`` is not an http(s) URL, if its host is not
                   on the operator's allowlist (including the unset allowlist,
                   which permits nothing), if the server answers with a
                   redirect, or if the download exceeds the size bound.
        FileExistsError: If media_name already exists in the repository.
    """
    iso_url = _require_allowlisted_iso_url(
        _require_http_url(iso_source), hmc.config.iso_url_allowlist_entries
    )
    vios_uuid = await resolve_vios_uuid(
        hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )

    _HMC_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.]{1,79}$")
    if not _HMC_FILENAME_RE.match(media_name):
        raise ValueError(
            f"media_name {media_name!r} is invalid. "
            "HMC only accepts filenames matching [A-Za-z0-9_.]{1,79} "
            "(no hyphens, spaces, or other special characters)."
        )

    # Check for name collision before downloading anything — the check needs
    # only vios_uuid and vg_uuid, so a taken name is refused without the
    # transfer (#325).
    existing_media = await hmc.list_optical_media(vios_uuid, vg_uuid)
    for media in existing_media:
        if media.get("MediaName") == media_name:
            raise FileExistsError(
                f"Media name '{media_name}' already exists in repository. "
                "Use a different name or delete the existing media first."
            )

    iso_path, iso_sha256, file_size = await _download_iso_from_url(iso_url)

    try:
        uploaded_media_entry = await _upload_iso_via_broker(
            hmc, vios_uuid, vg_uuid, media_name, iso_path, file_size
        )

        return {
            "status": "uploaded",
            "media_name": media_name,
            "media_size_bytes": file_size,
            "sha256": iso_sha256,
            "media": uploaded_media_entry,
        }
    finally:
        primary_error = sys.exception()

        if iso_path.exists():
            try:
                iso_path.unlink()
            except OSError as exc:
                if primary_error is None:
                    exc.add_note(
                        f"ISO upload completed, but temporary file cleanup failed: "
                        f"{iso_path}"
                    )
                    raise
                else:
                    logger.exception("temporary ISO cleanup failed for %s", iso_path)


async def create_logical_unit(
    hmc: HMCClient,
    cluster_uuid: str,
    lu_name: str,
    lu_size_gib: int,
    lu_type: LuType,
    device_type: DeviceType,
    *,
    cloned_from: str | None = None,
    wait: bool = False,
    timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS,
    poll_interval: int = DEFAULT_JOB_POLL_INTERVAL,
) -> dict[str, Any] | None:
    """Submit logical-unit creation and optionally wait for completion.

    Raises:
        ValueError: If the LU/device combination or polling controls are invalid.
    """
    validate_logical_unit_types(lu_type, device_type)
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    job = await hmc.create_logical_unit(
        cluster_uuid, lu_name, lu_size_gib, lu_type, device_type, cloned_from
    )
    return await wait_for_submitted_job(hmc, job, wait, timeout_seconds, poll_interval)


async def delete_logical_unit(
    hmc: HMCClient,
    cluster_uuid: str,
    lu_udid: str,
    *,
    wait: bool = False,
    timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS,
    poll_interval: int = DEFAULT_JOB_POLL_INTERVAL,
) -> dict[str, Any] | None:
    """Submit logical-unit deletion and optionally wait for completion.

    Raises:
        ValueError: If the polling controls are invalid.
    """
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    job = await hmc.delete_logical_unit(cluster_uuid, lu_udid)
    return await wait_for_submitted_job(hmc, job, wait, timeout_seconds, poll_interval)


def validate_logical_unit_create(
    lu_type: LuType,
    device_type: DeviceType,
    wait: bool,
    timeout_seconds: int,
    poll_interval: int,
) -> None:
    """Validate a create request before an adapter opens a connection."""
    validate_logical_unit_types(lu_type, device_type)
    validate_wait_timing(wait, timeout_seconds, poll_interval)


def validate_logical_unit_wait(
    wait: bool, timeout_seconds: int, poll_interval: int
) -> None:
    """Validate job polling controls before an adapter opens a connection."""
    validate_wait_timing(wait, timeout_seconds, poll_interval)


async def list_optical_mappings(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    lpar_name_or_uuid: str | None = None,
    *,
    system_name_or_uuid: str | None = None,
) -> list[dict[str, Any]]:
    """List VirtualSCSIMappings for optical media on a VIOS, optionally scoped to an LPAR.

    Returns only mappings that reference VirtualOpticalMedia backing, with media
    details and client LPAR information. Use lpar to scope mappings to a single partition
    by name or UUID.
    """
    vios_uuid = await resolve_vios_uuid(
        hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    lpar_uuid = None
    if lpar_name_or_uuid:
        lpar_uuid = await resolve_lpar_uuid(
            hmc, lpar_name_or_uuid, system_name_or_uuid=system_name_or_uuid
        )
    return await hmc.list_optical_mappings(vios_uuid, lpar_uuid)


async def mount_optical_media(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    lpar_name_or_uuid: str,
    *,
    system_name_or_uuid: str | None = None,
    media_name: str,
    target_device: str | None = None,
    ownership_override: bool = False,
) -> dict[str, Any] | None:
    """Create a VirtualSCSIMapping for optical media (mount ISO to LPAR).

    Creates a read-only optical mapping from a VirtualOpticalMedia (ISO container)
    to a client LPAR. The media_name must exist in the VIOS media repository.
    target_device optionally pins the vtscsi name. Returns the created mapping resource.
    """
    vios_uuid = await resolve_vios_uuid(
        hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    lpar_uuid = await resolve_and_authorize_lpar_mutation(
        hmc,
        system_name_or_uuid,
        lpar_name_or_uuid,
        ownership_override=ownership_override,
    )
    return await hmc.create_optical_mapping(
        vios_uuid, media_name, lpar_uuid, target_device
    )


async def unmount_optical_media(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    lpar_name_or_uuid: str,
    *,
    system_name_or_uuid: str | None = None,
    media_name: str,
    ownership_override: bool = False,
) -> None:
    """Remove the VirtualSCSIMapping for an optical device (unmount).

    Resolves the LPAR-scoped optical inventory entry whose ``MediaName`` equals
    ``media_name``, requires one exact mapping UUID, and removes it through the
    shared VirtualIOServer read-modify-write path. The backing
    VirtualOpticalMedia (ISO container) is preserved and can be remounted later.

    Removing the mapping is the whole unmount as this client implements it:
    mount_optical_media creates a VirtualSCSIMapping with the media referenced
    inside it, and no unload-without-detach path has been identified on the
    surveyed firmware, so detaching the mapping and unmounting the image are one
    operation here.  (#403 and ADR 0079 record only that a detailed
    VirtualSCSIMapping is not directly addressable; establishing the absence of
    an unload path would need its own live survey, on the ADR 0069 pattern.)

    Empty, missing, ambiguous, or malformed identities fail without a POST, as
    required by ADR 0079.

    The read-modify-write rewrites the whole VirtualIOServer document from a GET
    snapshot, so another writer's change in that window is lost; ADR 0079 puts
    the duty to serialize concurrent VIOS mapping changes on the caller.
    """
    if not media_name:
        raise HMCError("Optical media name must not be empty")

    vios_uuid = await resolve_vios_uuid(
        hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    lpar_uuid = await resolve_and_authorize_lpar_mutation(
        hmc,
        system_name_or_uuid,
        lpar_name_or_uuid,
        ownership_override=ownership_override,
    )
    matches: list[dict[str, Any]] = []
    for mapping in await hmc.list_optical_mappings(vios_uuid, lpar_uuid):
        if not isinstance(mapping, dict):
            continue
        storage = mapping.get("Storage")
        optical = (
            storage.get("VirtualOpticalMedia")
            if isinstance(storage, dict)
            else None
        )
        if isinstance(optical, dict) and optical.get("MediaName") == media_name:
            matches.append(mapping)

    if not matches:
        raise HMCError(
            f"Optical mapping for media {media_name!r} was not found on "
            f"LPAR {lpar_name_or_uuid!r}"
        )
    if len(matches) > 1:
        raise HMCError(
            f"Optical mapping for media {media_name!r} on LPAR "
            f"{lpar_name_or_uuid!r} is ambiguous"
        )

    mapping_uuid = matches[0].get("UUID")
    if not isinstance(mapping_uuid, str) or not mapping_uuid.strip():
        raise HMCError("VirtualSCSIMapping has an invalid UUID identity")
    await hmc.delete_storage_mapping(vios_uuid, mapping_uuid)
