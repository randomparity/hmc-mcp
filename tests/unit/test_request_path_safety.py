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

import httpx
import pytest

from hmc_mcp.client import HMCClient, _reject_dot_segments, _reject_non_job_path
from hmc_mcp.config import HMCConfig
from hmc_mcp.errors import HMCError


def _client() -> HMCClient:
    return HMCClient(
        HMCConfig(host="hmc.test", user="u", password="p", _env_file=None)  # pragma: allowlist secret
    )


# ---------------------------------------------------------------------------
# The property that makes the guard necessary
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# job_href addresses a job, or nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/rest/api/uom/jobs/j-1", "/rest/api/uom/Job/abcd-1234", "/jobs/j-1"]
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
    ],
)
def test_a_non_job_link_is_refused(path):
    """`_web_get` sends the same Accept header `get_hmc_user` uses, so without
    this an href of `/rest/api/web/HmcUser/root` returns the root account record
    through a tool classified `read` on target kind `job`."""
    with pytest.raises(HMCError, match="does not address a job"):
        _reject_non_job_path(path)
