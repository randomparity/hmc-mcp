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

import ast
import asyncio
import inspect
from urllib.parse import quote, unquote

import httpx
import pytest

from hmc_mcp.client.client_contracts import (
    _MAX_UOM_TYPE_LENGTH,
    ADAPTER_TYPES,
    _reject_unknown_uom_type,
)
from hmc_mcp.client.core import (
    MEDIA_UOM,
    HMCClient,
    _reject_dot_segments,
    _reject_non_job_path,
)
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

    def _forbidden(*args, **kwargs):
        sent.append("request")
        raise AssertionError("a refused path reached the transport")

    client._http.build_request = _forbidden  # type: ignore[method-assign]

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

    def _forbidden(*args, **kwargs):
        sent.append("request")
        raise AssertionError("a refused UUID reached the transport")

    client._http.build_request = _forbidden  # type: ignore[method-assign]

    with pytest.raises(ValueError, match=r"^vg_uuid must be a UUID$") as error:
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
    "UpdatesMixin": {"submit_platform_update": ("system_uuid",)},
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
    from hmc_mcp.client.client_updates import UpdatesMixin

    owner_type = {
        "HMCClient": HMCClient,
        "StorageMixin": StorageMixin,
        "UpdatesMixin": UpdatesMixin,
    }[owner]
    source = inspect.getsource(getattr(owner_type, method))
    for argument in arguments:
        assert f'"{argument}": {argument}' in source


def test_platform_update_rejects_a_non_uuid_system_before_transport():
    client = _client()
    sent: list[str] = []

    def _forbidden(*args, **kwargs):
        sent.append("request")
        raise AssertionError("an invalid system UUID reached the transport")

    client._http.build_request = _forbidden  # type: ignore[method-assign]

    with pytest.raises(ValueError, match=r"^system_uuid must be a UUID$"):
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
    with pytest.raises((HMCError, ValueError), match=r"refused|must be a UUID"):
        asyncio.run(
            call(
                *[
                    a.replace("{X}", TRAVERSAL) if isinstance(a, str) else a
                    for a in args
                ]
            )
        )


def test_the_guard_is_reached_by_every_transport_helper():
    """`_request` is the only place any of them can send from.

    If a future helper bypasses `_request` — calling `self._http` directly — the
    guard above stops covering it. This asserts the property the placement
    depends on, rather than trusting that no such helper appears.
    """
    from hmc_mcp.client import core as client_module

    source = inspect.getsource(client_module)
    tree = ast.parse(source)
    direct: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        expression = ast.unparse(node.func.value)
        if expression != "self._http" or node.func.attr in {"aclose"}:
            continue
        owner = next(
            (
                parent.name
                for parent in ast.walk(tree)
                if isinstance(parent, ast.AsyncFunctionDef)
                and any(child is node for child in ast.walk(parent))
            ),
            None,
        )
        if owner != "_request":
            direct.append(ast.unparse(node.func))
    assert not direct, f"a transport call bypassing _request: {direct}"


# ---------------------------------------------------------------------------
# The type-segment grammar (ADR 0143)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "LogicalPartition",
        "ManagedSystem",
        "VirtualIOServer",
        "Cluster",
        "SharedStoragePool",
        "UserProfile",
        "TaskRole",
        "ResourceRole",
        "VirtualSwitch",
        "VirtualNetwork",
        "NetworkBridge",
        "VolumeGroup",
        "Job",
        # Lowercase and digit-bearing type names the HMC serves. The grammar
        # constrains the character set, not the casing convention.
        "jobs",
        "SRIOVAdapter",
        *sorted(ADAPTER_TYPES),
    ],
)
def test_a_well_formed_type_is_accepted(value):
    assert _reject_unknown_uom_type("resource_type", value) is None


@pytest.mark.parametrize(
    "value",
    [
        # The two characters issue #809 reproduced: each retargets the GET
        # inside /rest/api/uom/ without tripping the dot-segment guard.
        "LogicalPartition?group=None",
        "LogicalPartition#x",
        # The dot segments the waist guard already refuses, which now fail the
        # grammar first.
        "..",
        ".",
        "%2e%2e",
        "../web/Logon",
        # Separators and whitespace.
        "Logical Partition",
        "Logical/Partition",
        "Logical-Partition",
        "Logical_Partition",
        "Logical.Partition",
        # A leading digit: the HMC's type names are XML element names.
        "1LogicalPartition",
        "",
        # Header-shaped input. httpx carries this into the Accept value and only
        # h11 refuses it, at send time.
        "LogicalPartition\r\nEvil: 1",
        # The pair an `^...$` grammar accepts, because Python's `$` matches
        # before a trailing newline. This is why the predicate uses fullmatch.
        "LogicalPartition\n",
        "LogicalPartition\r",
        # Non-ASCII that str.isalnum() would call alphanumeric.
        "LogicalPartitioñ",
        "Ⅴ",
    ],
)
def test_a_malformed_type_is_refused(value):
    with pytest.raises(ValueError, match="must be an HMC resource type name") as error:
        _reject_unknown_uom_type("resource_type", value)

    message = str(error.value)
    assert message.startswith("resource_type must be")
    # The leak rule _reject_dot_segments already follows: the message names the
    # argument and one character, never the caller's whole string or the host.
    assert value not in message or len(value) <= 1
    assert "hmc.test" not in message
    # A CR or LF reaches the message only through !r, so a refusal cannot inject
    # a line break into a log.
    assert "\n" not in message and "\r" not in message


# Every client method that interpolates a caller-supplied type segment, named by
# symbol with the argument position under test. `RETARGET` is the reproduction
# issue #809 carries: `?` splits a query string and `#` truncates the path at a
# fragment, both retargeting inside /rest/api/uom/ without tripping the
# dot-segment guard.
RETARGET = "LogicalPartition?group=None"

_TYPE_SEGMENT_CALLS = (
    ("list_uom", (RETARGET,), {}),
    ("get_uom", (RETARGET, UUID_A), {}),
    ("get_quick_property", (RETARGET, UUID_A, "PartitionState"), {}),
    ("list_quick_properties", (RETARGET,), {}),
    (
        "list_quick_properties",
        ("LogicalPartition",),
        {"parent_type": RETARGET, "parent_uuid": UUID_A},
    ),
    ("search_uom", (RETARGET, "PartitionName", "prod"), {}),
    ("list_search_parameters", (RETARGET,), {}),
    ("list_operations", (RETARGET,), {}),
    (
        "list_operations",
        ("LogicalPartition",),
        {"parent_type": RETARGET, "parent_uuid": UUID_A},
    ),
    ("list_child", (RETARGET, UUID_A, "ClientNetworkAdapter"), {}),
    ("list_child", ("LogicalPartition", UUID_A, RETARGET), {}),
    ("create_child", (RETARGET, UUID_A, "ClientNetworkAdapter", "<x/>"), {}),
    ("create_child", ("LogicalPartition", UUID_A, RETARGET, "<x/>"), {}),
    ("delete_child", (RETARGET, UUID_A, "ClientNetworkAdapter", UUID_B), {}),
    ("delete_child", ("LogicalPartition", UUID_A, RETARGET, UUID_B), {}),
)


@pytest.mark.parametrize(
    "method, args, kwargs",
    _TYPE_SEGMENT_CALLS,
    ids=[
        f"{name}-{index}" for index, (name, _, _) in enumerate(_TYPE_SEGMENT_CALLS)
    ],
)
def test_no_unsafe_type_segment_reaches_transport(method, args, kwargs):
    """Refused at the boundary, before anything is built (ADR 0143).

    Asserted against the transport rather than against the exception alone: a
    refusal that still built a request would leave the retargeted path in the
    HMC's audit log even though the caller saw an error.
    """
    client = _client()
    sent: list[str] = []

    def _forbidden(*call_args, **call_kwargs):
        sent.append("request")
        raise AssertionError("a refused type segment reached the transport")

    client._http.build_request = _forbidden  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="must be an HMC resource type name"):
        asyncio.run(getattr(client, method)(*args, **kwargs))
    assert sent == []


def test_uom_headers_refuses_a_malformed_type():
    """The second destination: the same value reaches the Accept parameter.

    `_uom_headers` is where `get_uom_path` -- which builds no uom f-string of
    its own and has no caller inside this package -- puts a caller-supplied
    type. Without this, the Accept destination's only control is h11's
    send-time header validation: it does refuse this value, but it belongs to a
    transitive dependency this repository neither owns, pins for that property,
    nor tests, and it refuses at send time rather than at the boundary
    (ADR 0143).
    """
    client = _client()
    with pytest.raises(ValueError, match="must be an HMC resource type name"):
        client._uom_headers("LogicalPartition\n")


@pytest.mark.parametrize(
    "resource_type, expected",
    [
        ("LogicalPartition", f"{MEDIA_UOM}; type=LogicalPartition"),
        # Falsy values keep the generic Accept they always produced: no type=
        # parameter is emitted, so nothing reaches the destination to check.
        ("", MEDIA_UOM),
        (None, MEDIA_UOM),
    ],
)
def test_uom_headers_passes_a_valid_type_through(resource_type, expected):
    client = _client()
    assert client._uom_headers(resource_type)["Accept"] == expected


# ---------------------------------------------------------------------------
# The group query value (ADR 0145)
# ---------------------------------------------------------------------------


# The values `list_uom` and `get_uom` may put after `?group=`. The first three
# are every group name this repository passes, and `quote(g, safe="")` is the
# identity function on each — so for those rows the expected path below pins the
# wire form as unchanged. The rest are the characters ADR 0145 governs: `&` and
# `=` append a parameter this client did not name, `#` truncates the value, and
# CR/LF raise `httpx.InvalidURL`, which is neither a `TransportError` nor an
# `HTTPError` and so escapes `_request`'s handlers entirely.
_GROUP_VALUES = (
    "RemoteAccess",
    "ViosSCSIMapping",
    "ViosFCMapping",
    "None&foo=bar",
    "None=x",
    "None?x=1",
    "None#/rest/api/web/HmcUser/root",
    "a/b",
    "a b",
    "None\rX-Evil: 1",
    "None\nX-Evil: 1",
)

_GROUP_CALLS = (
    ("list_uom", ("LogicalPartition",), "/rest/api/uom/LogicalPartition"),
    ("get_uom", ("LogicalPartition", UUID_A), f"/rest/api/uom/LogicalPartition/{UUID_A}"),
)


@pytest.mark.parametrize(
    "method, args, prefix", _GROUP_CALLS, ids=[name for name, _, _ in _GROUP_CALLS]
)
@pytest.mark.parametrize("group", _GROUP_VALUES)
def test_a_group_value_reaches_the_query_string_percent_encoded(
    method, args, prefix, group
):
    """One `group` parameter, holding exactly what the caller passed (ADR 0145).

    Asserted twice over, because the two halves fail differently. The path
    equality pins the encoding itself — and for the three names this repository
    passes, `quote` is the identity function, so the same assertion pins that
    their wire form did not change. The parsed-query assertion is what the
    injection characters trip: raw, `None&foo=bar` reaches the HMC as two
    parameters and `None#...` reaches it truncated, and neither shows up as a
    difference in the *number* of characters this client sent.
    """
    client = _client()
    requested: list[str] = []

    async def _record(method_name, path, **kwargs):
        requested.append(path)
        return httpx.Response(204)

    client._request = _record  # type: ignore[method-assign]

    asyncio.run(getattr(client, method)(*args, group=group))

    assert requested == [f"{prefix}?group={quote(group, safe='')}"]
    # Raw, a CR or LF here raises httpx.InvalidURL rather than returning a URL.
    params = httpx.URL(f"https://hmc.test:12443{requested[0]}").params
    assert params.get_list("group") == [group]
    assert len(params) == 1


def test_a_falsy_group_appends_no_query_string():
    """The `if group:` guard is unchanged: nothing is encoded and nothing is sent."""
    client = _client()
    requested: list[str] = []

    async def _record(method_name, path, **kwargs):
        requested.append(path)
        return httpx.Response(204)

    client._request = _record  # type: ignore[method-assign]

    asyncio.run(client.list_uom("LogicalPartition", group=""))
    asyncio.run(client.list_uom("LogicalPartition", group=None))

    assert requested == ["/rest/api/uom/LogicalPartition"] * 2


def test_a_group_whose_decoded_form_holds_a_dot_segment_is_still_refused():
    """Encoding does not smuggle a dot segment past the waist.

    `_reject_dot_segments` checks `unquote(candidate)` as well as the raw form,
    so `quote("a/../../x", safe="")` — `a%2F..%2F..%2Fx` — is still read as
    carrying `..` segments and still refused before transport. This is a pin,
    not a red test: the raw path was refused too, and what ADR 0145 records is
    that the refusal does *not* move. A `group` the caller percent-encoded
    itself is the one value that stops tripping it, which that record accepts.
    """
    client = _client()
    sent: list[str] = []

    def _forbidden(*args, **kwargs):
        sent.append("request")
        raise AssertionError("a refused group value reached the transport")

    client._http.build_request = _forbidden  # type: ignore[method-assign]

    with pytest.raises(HMCError, match="refused"):
        asyncio.run(
            client.list_uom("LogicalPartition", group="a/../../web/HmcUser/root")
        )
    assert sent == []


def test_a_caller_percent_encoded_group_now_reaches_the_transport_as_data():
    """The one refusal this change does move, pinned rather than left to the record.

    A `group` the caller already percent-encoded is double-encoded here, so the
    waist's single decode resolves `x%252F..%252Fy` to `x%2F..%2Fy` rather than
    to a dot segment. At `4d823cbb` the raw value decoded straight to `x/../y`
    and was refused, so this is red against the unfixed code. Nothing is
    retargeted: the value sits after the `?`, where no path resolution applies
    (ADR 0145, "one narrow residual opens").
    """
    client = _client()
    requested: list[str] = []

    def _record(method, path, **kwargs):
        requested.append(path)
        return httpx.Request(method, f"https://hmc.test:12443{path}")

    client._http.build_request = _record  # type: ignore[method-assign]

    async def _send(request, **kwargs):
        return httpx.Response(204, request=request)

    client._http.send = _send  # type: ignore[method-assign]

    asyncio.run(client.list_uom("LogicalPartition", group="x%2F..%2Fy"))

    assert requested == ["/rest/api/uom/LogicalPartition?group=x%252F..%252Fy"]


def _is_quote_binding(node: ast.AST) -> str | None:
    """The name a literal `<target> = quote(<arg>, safe="")` statement binds, or `None`.

    The declaration form for a query value, as `_is_boundary_check` is the
    declaration form for a type segment: the assignment says at the call site
    which rule governs the name the f-string below it interpolates. Unlike
    `_is_boundary_check`, this returns the *target* name without requiring it
    to equal `<arg>` — a query value's encoded form is routinely bound to a
    new name (e.g. `encoded_value = quote(property_value, safe="")`), so the
    declaration form for a query value cannot require the same identifier on
    both sides the way the type-segment form does.
    """
    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
        return None
    target = node.targets[0]
    call = node.value
    if not isinstance(target, ast.Name) or not isinstance(call, ast.Call):
        return None
    if not isinstance(call.func, ast.Name) or call.func.id != "quote":
        return None
    if len(call.args) != 1 or not isinstance(call.args[0], ast.Name):
        return None
    safe = [k for k in call.keywords if k.arg == "safe"]
    if len(safe) != 1 or not isinstance(safe[0].value, ast.Constant):
        return None
    if safe[0].value.value != "":
        return None
    return target.id


def test_every_group_query_interpolation_is_encoded():
    """A `?group=` f-string in `core.py` may only interpolate an encoded name.

    The companion the existing uom inventory cannot be: `_uom_path_sites` matches
    an f-string whose *first* part begins `/rest/api/uom/`, and these sites build
    `f"?group={...}"` onto a path already assembled — so `group` is invisible to
    both checks above, which is how it stayed unexamined while the type segment
    beside it was decided twice (ADR 0145).

    **What this does not cover, stated rather than implied.** It matches the
    `path += f"?group={name}"` idiom and the literal `quote(name, safe="")`
    assignment, and only where the f-string interpolates a bare name.
    Concatenation, `.format`, an interpolated attribute or subscript
    (`f"?group={self.g}"`), a differently-spelled encoder, and a name rebound
    between the assignment and the f-string are all invisible here, exactly as
    concatenation and `.format` are to the type-segment walk. This raises the
    cost of adding an unencoded site in the idiom the module uses; it is not a
    proof that none can exist.
    """
    from hmc_mcp.client import core as client_module

    tree = ast.parse(inspect.getsource(client_module))
    unencoded: list[tuple[str, str]] = []
    for owner in ast.walk(tree):
        if not isinstance(owner, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        encoded = {
            name
            for node in ast.walk(owner)
            if (name := _is_quote_binding(node)) is not None
        }
        for node in ast.walk(owner):
            if not isinstance(node, ast.JoinedStr) or not node.values:
                continue
            head = node.values[0]
            if not isinstance(head, ast.Constant) or not str(head.value).startswith(
                "?group="
            ):
                continue
            unencoded.extend(
                (owner.name, part.value.id)
                for part in node.values
                if isinstance(part, ast.FormattedValue)
                and isinstance(part.value, ast.Name)
                and part.value.id not in encoded
            )
    assert not unencoded, f"group query values interpolated unencoded: {unencoded}"


# ---------------------------------------------------------------------------
# The guard is site-independent, and no site may join without it
# ---------------------------------------------------------------------------


# The arguments a `/rest/api/uom/` f-string in `core.py` may interpolate, and
# which rule governs each. A name outside this table is a segment nobody has
# decided a rule for, which is what the second assertion below refuses.
_TYPE_SEGMENT_ARGUMENTS = frozenset({"resource_type", "parent_type", "child_type"})

# Segments carrying *data* rather than a schema identifier, governed by
# percent-encoding at the site that builds them: `search_uom`'s two search values
# and `get_quick_property`'s name (ADR 0146). Membership is not self-certifying —
# `test_every_encoded_uom_segment_is_quote_bound` holds each of these to a literal
# `quote(..., safe="")` binding in its own function, so a name is in this class
# because of what its site does and not because of what it is called. That is the
# defect issue #818 found: `property_name` sat in the inventory below with no rule
# behind it, which made the unclassified-segment assertion pass for it.
_ENCODED_SEGMENT_ARGUMENTS = frozenset({"encoded_property", "encoded_value"})

_KNOWN_UOM_SEGMENT_ARGUMENTS = (
    _TYPE_SEGMENT_ARGUMENTS
    | _ENCODED_SEGMENT_ARGUMENTS
    | {
        "uuid",
        "parent_uuid",
        "child_uuid",
        "job_id",
    }
)


def _is_boundary_check(node: ast.AST) -> bool:
    """A literal `_reject_unknown_uom_type("x", x)` call — the declaration form."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_reject_unknown_uom_type"
        and len(node.args) == 2
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[1], ast.Name)
        and node.args[0].value == node.args[1].id
    )


def _uom_path_sites() -> tuple[
    list[tuple[str, str]], dict[str, set[str]], dict[str, set[str]]
]:
    """Every `/rest/api/uom/` f-string interpolation in `core.py`, paired with
    the type arguments each enclosing function hands the boundary predicate and
    the names each binds through a literal `quote(..., safe="")`.

    One parse and one walk for all three, because the guard tests need them
    paired: deriving the others per interpolation reparsed the whole module once
    per site.
    """
    from hmc_mcp.client import core as client_module

    tree = ast.parse(inspect.getsource(client_module))
    interpolations: list[tuple[str, str]] = []
    guarded: dict[str, set[str]] = {}
    quote_bound: dict[str, set[str]] = {}
    for owner in ast.walk(tree):
        if not isinstance(owner, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        for node in ast.walk(owner):
            if _is_boundary_check(node):
                guarded.setdefault(owner.name, set()).add(node.args[1].id)
                continue
            if (bound := _is_quote_binding(node)) is not None:
                quote_bound.setdefault(owner.name, set()).add(bound)
                continue
            if not isinstance(node, ast.JoinedStr) or not node.values:
                continue
            head = node.values[0]
            if not isinstance(head, ast.Constant) or not str(head.value).startswith(
                "/rest/api/uom/"
            ):
                continue
            interpolations.extend(
                (owner.name, part.value.id)
                for part in node.values
                if isinstance(part, ast.FormattedValue)
                and isinstance(part.value, ast.Name)
            )
    return interpolations, guarded, quote_bound


def test_every_uom_type_interpolation_is_guarded():
    """An f-string literally prefixed `/rest/api/uom/` cannot interpolate a type
    without its own boundary check.

    Site-directed for a reason: an inventory that only checks *which names* are
    interpolated says nothing about whether the site validates them, so a new
    method building `f"/rest/api/uom/{resource_type}/count"` with no predicate
    call would pass such a check. Here it fails until it carries the call
    (ADR 0143).

    **What this does not cover, stated rather than implied.** The walk matches
    an `ast.JoinedStr` whose first part is a constant beginning
    `/rest/api/uom/`. Concatenation, `%`, `.format`, a prefix held in a
    variable, and a path assembled in two steps all build the same request and
    are invisible here. Closing that would mean an AST guard against every way
    of building a string -- more machinery than the risk removes, on a module
    where every existing site is an f-string. This test raises the cost of
    adding an unguarded site in the idiom the module actually uses; it is not a
    proof that none can exist.
    """
    interpolations, guarded, _ = _uom_path_sites()
    unguarded = sorted(
        {
            (function, name)
            for function, name in interpolations
            if name in _TYPE_SEGMENT_ARGUMENTS
            and name not in guarded.get(function, set())
        }
    )
    assert not unguarded, f"uom type segments interpolated without a check: {unguarded}"


def test_every_uom_path_interpolation_is_a_known_argument():
    """A new *kind* of segment fails until someone decides which rule governs it.

    The companion to the check above, and not a substitute for it: this one
    catches a segment nobody has classified, that one catches a classified
    segment nobody guarded.
    """
    interpolations, _, _ = _uom_path_sites()
    unknown = sorted({name for _, name in interpolations} - _KNOWN_UOM_SEGMENT_ARGUMENTS)
    assert not unknown, f"unclassified uom path segment arguments: {unknown}"


def test_every_encoded_uom_segment_is_quote_bound():
    """A segment classed as encoded must be encoded at its own site.

    The third companion, and the one that stops the classification above from
    certifying itself. `_KNOWN_UOM_SEGMENT_ARGUMENTS` says a name has a rule;
    this says the rule is present in the function that interpolates it, so
    `_ENCODED_SEGMENT_ARGUMENTS` cannot be satisfied by naming a local
    `encoded_anything`. Issue #818 is what that costs: `property_name` sat in
    the inventory with no rule behind it, and the unclassified-segment assertion
    passed for exactly the case its docstring describes catching (ADR 0146).

    **What this does not cover, stated rather than implied.** It matches a
    literal `<local> = quote(<some name>, safe="")` assignment in the same
    function — the argument is not tied to the enclosing function's parameter, so
    `encoded = quote(unrelated, safe="")` would satisfy it — and only where the
    `/rest/api/uom/` f-string interpolates a bare name.
    Concatenation, `.format`, an interpolated attribute or subscript, a
    differently-spelled encoder, a qualified `module.quote(...)` call, and a name
    rebound between the assignment and the f-string are all invisible here — the
    same stated limits the type-segment and `?group=` walks carry. This raises
    the cost of adding an unencoded site in the idiom the module uses; it is not
    a proof that none can exist.
    """
    interpolations, _, quote_bound = _uom_path_sites()
    unbound = sorted(
        {
            (function, name)
            for function, name in interpolations
            if name in _ENCODED_SEGMENT_ARGUMENTS
            and name not in quote_bound.get(function, set())
        }
    )
    assert not unbound, f"encoded uom segments interpolated unbound: {unbound}"


# ---------------------------------------------------------------------------
# The type-segment length bound (ADR 0147)
# ---------------------------------------------------------------------------


def test_a_type_at_the_length_bound_is_accepted():
    """The bound is inclusive: exactly `_MAX_UOM_TYPE_LENGTH` is still a type."""
    value = "A" + "b" * (_MAX_UOM_TYPE_LENGTH - 1)
    assert len(value) == _MAX_UOM_TYPE_LENGTH
    assert _reject_unknown_uom_type("resource_type", value) is None


@pytest.mark.parametrize(
    "length",
    [
        _MAX_UOM_TYPE_LENGTH + 1,
        # The reproduction issue #820 carries: a megabyte of grammar-valid
        # characters built a megabyte-long URL and Accept header and sent them.
        1024 * 1024,
    ],
)
def test_a_grammar_valid_type_over_the_bound_is_refused(length):
    value = "A" * length
    with pytest.raises(ValueError, match="must be an HMC resource type name") as error:
        _reject_unknown_uom_type("resource_type", value)

    message = str(error.value)
    assert message.startswith("resource_type must be")
    # The length branch, not the character branch: an all-alphanumeric value has
    # no offending character, so naming one would describe a rule it did not
    # break.
    assert f"a value of {length} characters" in message
    assert f"{_MAX_UOM_TYPE_LENGTH}-character maximum" in message
    # The same leak rule the character branch follows: the length, never the
    # value, which on the CLI and API paths carries an operator's own strings.
    assert value not in message


def test_the_length_branch_precedes_the_character_branch():
    """An over-long value that also breaks the character class reports length.

    Checking length first is what keeps a megabyte from being scanned character
    by character before it is refused, and it is the ordering the message's
    detail depends on.
    """
    value = "A" * (_MAX_UOM_TYPE_LENGTH + 1) + "?group=None"
    with pytest.raises(ValueError) as error:
        _reject_unknown_uom_type("resource_type", value)

    assert f"a value of {len(value)} characters" in str(error.value)


# ---------------------------------------------------------------------------
# The quick-property name segment (ADR 0146)
# ---------------------------------------------------------------------------

# The six quick-property names this repository passes. `rg -n -o
# "quick/[A-Za-z0-9_.%-]*" src/ tests/ docs/` returns exactly these, beside a
# bare `quick/` and two prose artifacts (`quick/All.`, `quick/PartitionState.`).
_QUICK_PROPERTY_NAMES = (
    "PartitionState",
    "PartitionID",
    "SystemType",
    "NoSuchProperty",
    "all",
    "All",
)

_QUICK_PREFIX = f"/rest/api/uom/LogicalPartition/{UUID_A}/quick/"


def _quick_property_path(client: HMCClient, property_name: str) -> str:
    """The path `get_quick_property` hands the transport for *property_name*.

    Records at `build_request` rather than after `send`, because a value that
    re-points the request does so while the URL is being built.
    """
    requested: list[str] = []

    def _record(method, path, **kwargs):
        requested.append(path)
        return httpx.Request(method, f"https://hmc.test:12443{path}")

    client._http.build_request = _record  # type: ignore[method-assign]

    async def _send(request, **kwargs):
        return httpx.Response(204, request=request)

    client._http.send = _send  # type: ignore[method-assign]

    assert (
        asyncio.run(client.get_quick_property("LogicalPartition", UUID_A, property_name))
        is None
    )
    assert len(requested) == 1
    return requested[0]


@pytest.mark.parametrize(
    "property_name",
    [
        # The `?` and `#` reproduction issue #818 carries, on the same line
        # ADR 0143 hardened for the type segment beside it.
        "PartitionState?group=None",
        "PartitionState#/rest/api/web/HmcUser/root",
        # A second path segment: not a retarget out of the resource, but not the
        # property the caller named either.
        "PartitionState/extra",
        "Partition State",
        "Partitioñ",
        # Raw, this raises httpx.InvalidURL, which is neither httpx.TransportError
        # nor httpx.HTTPError and so escapes `_request`'s two handlers entirely.
        "PartitionState\r\nX-Evil: 1",
    ],
)
def test_a_quick_property_name_cannot_re_point_the_request(property_name):
    """The whole name stays inside the last path segment (ADR 0146).

    Not asserted by comparing against `quote(...)` alone, which would only say
    the client called the function this test expects. The two assertions below
    are the property itself: nothing structural survives into the segment, and
    the segment still decodes to exactly what the caller passed — so neither a
    truncation nor an addition can pass.
    """
    path = _quick_property_path(_client(), property_name)

    assert path.startswith(_QUICK_PREFIX)
    segment = path[len(_QUICK_PREFIX) :]
    assert not set(segment) & set("?#/\r\n")
    assert unquote(segment) == property_name
    # Every case here holds a character outside RFC 3986's unreserved set, so
    # each must actually be rewritten. Without this the space and non-ASCII
    # cases assert nothing that can fail: httpx encodes both while building the
    # URL, so their two assertions above hold whether or not the client encoded
    # anything, and dropping the binding would leave them green.
    assert segment != property_name


@pytest.mark.parametrize("property_name", _QUICK_PROPERTY_NAMES)
def test_encoding_is_a_no_op_on_the_quick_property_names_this_client_passes(
    property_name,
):
    """No wire-format change for any name this repository passes (ADR 0146).

    Asserted against the built path rather than `quote(n, safe="") == n`, which
    imports no client code at all. What the built-path form adds is coverage of
    the path *template* — a segment reordered or a literal changed reddens this.
    It is deliberately blind to the binding's removal, because `quote` is the
    identity on all six names; `test_a_quick_property_name_cannot_re_point_the_request`
    is what fails when the binding goes.
    """
    assert _quick_property_path(_client(), property_name) == _QUICK_PREFIX + property_name


@pytest.mark.parametrize("property_name", ["..", ".", "../../x"])
def test_a_dot_segment_quick_property_name_is_still_refused(property_name):
    """The refusal identity that does *not* move (ADR 0146).

    `quote` leaves `.` alone and turns `../../x` into `..%2F..%2Fx`, which the
    waist guard's percent-decoding arm still reads as dot segments. Asserted
    against the transport as well as the exception: a refusal that still built a
    request would leave the path in the HMC's audit log.
    """
    client = _client()

    def _forbidden(*args, **kwargs):
        raise AssertionError("a refused quick-property name reached the transport")

    client._http.build_request = _forbidden  # type: ignore[method-assign]

    with pytest.raises(HMCError, match="refused"):
        asyncio.run(client.get_quick_property("LogicalPartition", UUID_A, property_name))


@pytest.mark.parametrize(
    "property_name",
    [
        "..%2f..%2fweb%2fHmcUser%2froot",
        "%2e%2e",
        "%2E%2E",
        "..%2F..%2Fx",
        # Shaped like a URL. `_reject_dot_segments` takes a *path*, and its first
        # statement is `urlparse(path).path if "://" in path else path` — handed a
        # bare segment carrying `://`, that branch parses `x` as a scheme and
        # `%2e%2e` as a netloc, leaving an empty path for both arms to scan. The
        # segment is therefore handed to the predicate as the path it forms.
        "x://%2e%2e",
        "x://%2E%2E",
        "x://%2e%2e/y",
        # And the other half of the same hazard: a name starting with `/` makes
        # the prefixed value start `//`, which `urlparse` reads as a netloc
        # rather than a path. Both halves are why the value is handed over as
        # the path it actually occupies, with a non-empty first segment.
        "/..%2f://",
        "/..%2fx://",
        "/..%2F://",
    ],
)
def test_a_caller_percent_encoded_dot_segment_name_is_refused_too(property_name):
    """Encoding must not buy a dot segment passage past the waist (ADR 0146).

    This is the case the site guard exists for. Percent-encoding a name the
    caller had already encoded double-encodes it, so `..%2f..` reaches the wire
    as `..%252f..` and neither of the waist guard's two arms reads it as a dot
    segment — it would be sent. Calling `_reject_dot_segments` on the *argument*,
    before encoding, is what keeps it refused.

    Reasoning that the double-encoded form "addresses nothing" is exactly the
    argument `_reject_dot_segments`' own body records having removed: how many
    times the HMC's web stack decodes a path is untestable from here, and
    guessing low is the wrong direction for a fail-closed check.
    """
    client = _client()

    def _forbidden(*args, **kwargs):
        raise AssertionError("a pre-encoded dot segment reached the transport")

    client._http.build_request = _forbidden  # type: ignore[method-assign]

    with pytest.raises(HMCError, match="refused"):
        asyncio.run(client.get_quick_property("LogicalPartition", UUID_A, property_name))
