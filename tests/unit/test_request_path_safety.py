"""The request path must address the resource the caller named.

Every REST path in this package is built by interpolating caller-supplied
identifiers into an f-string, and httpx resolves RFC 3986 dot-segments when
merging a path onto ``base_url``. That combination lets an undeclared
sub-resource argument steer a request off the resource its declared selector
names — which defeats ADR 0039's target constraints, and applies equally to the
CLI and ``api`` paths that no access policy bounds.

See docs/adr/0039-dispatch-time-target-scope.md, "Sub-resources reached through
a declared selector".
"""

from __future__ import annotations

import asyncio
import inspect

import httpx
import pytest

from hmc_mcp.client.core import HMCClient, _reject_dot_segments, _reject_non_job_path
from hmc_mcp.config import HMCConfig
from hmc_mcp.errors import HMCError

UUID_A = "12345678-1234-1234-1234-1234567890ab"
UUID_B = "ABCDEFAB-CDEF-CDEF-CDEF-ABCDEFABCDEF"


def _client() -> HMCClient:
    return HMCClient(
        HMCConfig(host="hmc.test", user="u", password="p")  # pragma: allowlist secret
    )


# ---------------------------------------------------------------------------
# The property that makes the guard necessary
# ---------------------------------------------------------------------------


def test_httpx_leaves_percent_encoded_dot_segments_untouched():
    """The other half of the empirical fact, and the half that is *not* a reason
    to allow them.

    httpx normalizes literal dot-segments and leaves encoded ones alone. An
    earlier version of the guard concluded from that they could pass, because
    they would "address nothing" — a claim about whether the HMC decodes before
    routing, which nothing here can establish. This pins the library behaviour
    only; the guard refuses both forms regardless.
    """
    client = httpx.AsyncClient(base_url="https://hmc.test:12443")
    encoded = client.build_request(
        "DELETE", "/rest/api/uom/VirtualIOServer/v1/VolumeGroup/%2e%2e/%2e%2e/x"
    )
    assert "%2e%2e" in str(encoded.url)


def test_httpx_resolves_dot_segments_against_the_base_url():
    """The empirical fact the guard exists for, pinned against the real library.

    If a future httpx stops normalizing, this test tells us the guard became
    belt-and-braces rather than load-bearing — and if it starts normalizing
    something new, the guard needs to grow. Either way the assumption is not
    left implicit.
    """
    client = httpx.AsyncClient(base_url="https://hmc.test:12443")
    built = client.build_request(
        "DELETE",
        "/rest/api/uom/VirtualIOServer/vios-1/VolumeGroup/../../../LogicalPartition/prod",
    )
    assert str(built.url) == "https://hmc.test:12443/rest/api/uom/LogicalPartition/prod"


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/rest/api/uom/VirtualIOServer/v1/VolumeGroup/../../../LogicalPartition/prod",
        "/rest/api/uom/VirtualIOServer/v1/VirtualSCSIMapping/../../web/HmcUser/root",
        "/rest/api/uom/LogicalPartition/./x",
        "..",
        "https://hmc.test:12443/rest/api/uom/jobs/../HmcUser/root",
        # Percent-encoded, and mixed. httpx resolves only the raw form, so an
        # earlier version of this guard let these through on the reasoning that
        # they "address nothing" — an assumption about the HMC's own decoding
        # that cannot be tested from here, and the wrong way round for a
        # fail-closed check.
        "/rest/api/uom/Job/%2e%2e/%2e%2e/web/HmcUser/root",
        "/rest/api/uom/Job/..%2f..%2fweb%2fHmcUser%2froot",
        "/rest/api/uom/VirtualIOServer/v1/VolumeGroup/%2E%2E/%2E%2E/LogicalPartition/x",
        # Mixed encoding: httpx leaves this alone, but a literal "../" survives
        # in it, so the *raw* arm should already catch it. The case a
        # hand-written guard usually misses, pinned so it cannot regress to
        # being caught only by the decoded arm.
        "/rest/api/uom/VirtualIOServer/v1/VolumeGroup/..%2f../LogicalPartition/x",
    ],
)
def test_a_dot_segment_is_refused(path):
    with pytest.raises(HMCError, match="refused"):
        _reject_dot_segments("DELETE", path)


@pytest.mark.parametrize(
    "path",
    [
        "/rest/api/uom/LogicalPartition/prod",
        "/rest/api/uom/VirtualIOServer/v1/VolumeGroup/vg-1",
        # Characters, not segments: a resource may legitimately be named this.
        "/rest/api/uom/VirtualIOServer/v1/VolumeGroup/a..b",
        "/rest/api/uom/VirtualIOServer/v1/VolumeGroup/..log",
        "/rest/api/uom/ManagedSystem/s1/LogicalPartition?group=None",
    ],
)
def test_an_ordinary_path_is_not_refused(path):
    assert _reject_dot_segments("GET", path) is None


def test_the_guard_runs_before_anything_leaves_the_process():
    """Refusal is not merely a different error: no request is built at all."""
    client = _client()
    sent: list[str] = []

    async def _forbidden(*args, **kwargs):
        sent.append("request")
        raise AssertionError("a refused path reached the transport")

    client._http.request = _forbidden  # type: ignore[method-assign]

    with pytest.raises(HMCError, match="refused"):
        asyncio.run(
            client._request(
                "DELETE",
                "/rest/api/uom/VirtualIOServer/v1/VolumeGroup/../../LogicalPartition/x",
            )
        )
    assert sent == []


def test_a_uuid_only_path_argument_is_refused_before_transport():
    client = _client()
    sent: list[str] = []

    async def _forbidden(*args, **kwargs):
        sent.append("request")
        raise AssertionError("a refused UUID reached the transport")

    client._http.request = _forbidden  # type: ignore[method-assign]

    with pytest.raises(HMCError, match=r"^vg_uuid must be a UUID$") as error:
        asyncio.run(
            client._request_with_uuid_path_arguments(
                "GET",
                "/rest/api/uom/VirtualIOServer/vios/VolumeGroup/not-a-uuid",
                uuid_path_arguments={"vg_uuid": "not-a-uuid"},
            )
        )

    assert "hmc.test" not in str(error.value)
    assert "not-a-uuid" not in str(error.value)
    assert sent == []


def test_canonical_mixed_case_uuid_path_arguments_reach_the_request_boundary():
    client = _client()
    requested: list[tuple[str, str]] = []

    async def _record(method, path, **kwargs):
        requested.append((method, path))
        return httpx.Response(204)

    client._request = _record  # type: ignore[method-assign]
    path = f"/rest/api/uom/VirtualIOServer/{UUID_A}/VolumeGroup/{UUID_B}"

    asyncio.run(
        client._request_with_uuid_path_arguments(
            "GET",
            path,
            uuid_path_arguments={"vios_uuid": UUID_A, "vg_uuid": UUID_B},
        )
    )

    assert requested == [("GET", path)]


_UUID_PATH_BUILDERS = {
    "HMCClient": {
        "get_uom": ("uuid",),
        "get_quick_property": ("uuid",),
        "list_child": ("parent_uuid",),
        "create_child": ("parent_uuid",),
        "delete_child": ("parent_uuid", "child_uuid"),
        "_broker_file_create": ("vios_uuid", "vg_uuid"),
        "_broker_iso_import": ("vios_uuid", "vg_uuid"),
        "submit_platform_update": ("system_uuid",),
    },
    "StorageMixin": {
        "list_volume_groups": ("vios_uuid",),
        "get_volume_group": ("vios_uuid", "vg_uuid"),
        "create_volume_group": ("vios_uuid",),
        "create_virtual_disk": ("vios_uuid", "vg_uuid"),
        "delete_virtual_disk": ("vios_uuid", "vg_uuid"),
        "map_storage_to_lpar": ("vios_uuid",),
        "list_storage_mappings": ("vios_uuid",),
        "delete_storage_mapping": ("vios_uuid", "system_uuid"),
        "_get_vg_raw_xml": ("vios_uuid", "vg_uuid"),
        "_post_vg_xml": ("vios_uuid", "vg_uuid"),
        "get_media_repository": ("vios_uuid", "vg_uuid"),
        "list_optical_media": ("vios_uuid", "vg_uuid"),
        "list_optical_mappings": ("vios_uuid",),
        "create_optical_mapping": ("vios_uuid",),
    },
}


@pytest.mark.parametrize(
    "owner, method, arguments",
    [
        (owner, method, arguments)
        for owner, methods in _UUID_PATH_BUILDERS.items()
        for method, arguments in methods.items()
    ],
)
def test_uuid_only_path_builder_inventory_uses_explicit_metadata(
    owner, method, arguments
):
    from hmc_mcp.client.client_storage import StorageMixin

    owner_type = HMCClient if owner == "HMCClient" else StorageMixin
    source = inspect.getsource(getattr(owner_type, method))
    for argument in arguments:
        assert f'"{argument}": {argument}' in source


def test_platform_update_rejects_a_non_uuid_system_before_transport():
    client = _client()
    sent: list[str] = []

    async def _forbidden(*args, **kwargs):
        sent.append("request")
        raise AssertionError("an invalid system UUID reached the transport")

    client._http.request = _forbidden  # type: ignore[method-assign]

    with pytest.raises(HMCError, match=r"^system_uuid must be a UUID$"):
        asyncio.run(client.submit_platform_update("not-a-uuid", {}))

    assert sent == []


# ---------------------------------------------------------------------------
# job_href addresses a job, or nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/rest/api/uom/jobs/j-1",
        "/rest/api/uom/Job/abcd-1234",
        "/jobs/j-1",
        # The shape the suite's own fixture uses, so the anchored pattern cannot
        # tighten past what `submit_job` actually returns.
        "/rest/api/uom/LogicalPartition/lpar-uuid/do/PowerOn/Job/job-uuid-999",
    ],
)
def test_a_job_link_is_accepted(path):
    assert _reject_non_job_path(path) is None


@pytest.mark.parametrize(
    "path",
    [
        "/rest/api/web/HmcUser/root",
        "/rest/api/uom/LogicalPartition/prod",
        "/rest/api/uom/ManagedSystem/s1",
        "",
        # Contains the word but does not address a job — the case that made
        # segment membership too weak a test to rely on.
        "/rest/api/web/HmcUser/jobs",
        "/rest/api/web/HmcUser/root/Job",
        # Trailing content after the identifier.
        "/rest/api/uom/Job/j-1/../../web/HmcUser/root",
        "/rest/api/uom/Job/%2e%2e/web/HmcUser/root",
    ],
)
def test_a_non_job_link_is_refused(path):
    """`_web_get` sends the same Accept header `get_hmc_user` uses, so without
    this an href of `/rest/api/web/HmcUser/root` returns the root account record
    through a tool classified `read` on target kind `job`."""
    with pytest.raises(HMCError, match="does not address a job"):
        _reject_non_job_path(path)


# ---------------------------------------------------------------------------
# The guard is site-independent, and that is the point of where it lives
# ---------------------------------------------------------------------------


# Every client method that interpolates a caller-supplied sub-resource identifier
# into a path, named by symbol rather than by line: `client.py` gains and loses
# lines, and this list must outlive that. Four independent sweeps enumerated
# these; two of them produced lists that were each missing sites the other had,
# which is the argument for guarding the waist rather than the call sites.
_SUB_RESOURCE_CALLS = (
    ("delete_child", ("LogicalPartition", "AUTH", "ClientNetworkAdapter", "{X}")),
    ("create_virtual_disk", ("AUTH", "{X}", "disk", 1)),
    ("delete_virtual_disk", ("AUTH", "{X}", "disk")),
    ("_get_vg_raw_xml", ("AUTH", "{X}")),
    ("get_volume_group", ("AUTH", "{X}")),
    ("list_optical_media", ("AUTH", "{X}")),
    ("delete_optical_media", ("AUTH", "{X}", "media")),
    ("_broker_file_create", ("AUTH", "{X}", "iso")),
    ("_broker_iso_import", ("AUTH", "{X}", "media", "/broker/uri")),
    ("delete_virtual_network", ("AUTH", "{X}")),
)

TRAVERSAL = "../../../LogicalPartition/VICTIM"


@pytest.mark.parametrize(
    "method, args", _SUB_RESOURCE_CALLS, ids=[name for name, _ in _SUB_RESOURCE_CALLS]
)
def test_no_unsafe_sub_resource_identifier_reaches_transport(method, args):
    """One guard at the waist, not ten checks at the call sites.

    Each of these builds `/{Parent}/{authorized}/{Child}/{caller-supplied}` by
    f-string. A dot-segment in the trailing identifier resolves the authorized
    parent away — so the access policy authorizes one resource and the request
    addresses another, with nothing denied and every "a denied call makes no
    outbound attempt" test still green.

    Parametrized by symbol rather than by file:line deliberately. The
    enumerations of this pattern that circulated during review each missed sites
    the others caught, which is precisely why the check belongs somewhere no
    enumeration has to be complete.
    """
    client = _client()
    call = getattr(client, method)
    with pytest.raises(HMCError, match=r"refused|must be a UUID"):
        asyncio.run(call(*[a.replace("{X}", TRAVERSAL) if isinstance(a, str) else a
                           for a in args]))


def test_the_guard_is_reached_by_every_transport_helper():
    """`_request` is the only place any of them can send from.

    If a future helper bypasses `_request` — calling `self._http` directly — the
    guard above stops covering it. This asserts the property the placement
    depends on, rather than trusting that no such helper appears.
    """
    import inspect

    from hmc_mcp.client import core as client_module

    source = inspect.getsource(client_module)
    direct = [
        line.strip()
        for line in source.splitlines()
        if "self._http." in line
        and not any(k in line for k in ("aclose", "headers", "_http.request", "="))
    ]
    assert not direct, f"a transport call bypassing _request: {direct}"
