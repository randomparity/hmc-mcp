"""Transport contract tests bound to the F1 capability reference.

Reference rows exercised:
  rest:logon-and-logoff  — PUT/DELETE /rest/api/web/Logon,
                           X-API-Session token propagation,
                           LogonRequest/LogonResponse media types.
  rest:job-status        — job-state vocabulary including EXCEPTION and
                           COMPLETED_WITH_ERROR.

Each test names the row it binds, keeping the claim local to one assertion
rather than spreading it across larger integration suites.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from conftest import LOGON_RESPONSE, make_config

from hmc_mcp.client.core import MEDIA_UOM, MEDIA_WEB, HMCClient

_LOGON_PATH = "/rest/api/web/Logon"
_LP_PATH = "/rest/api/uom/LogicalPartition"

# ── Minimal Atom feed used for GET probes ────────────────────────────────────
_EMPTY_FEED = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><feed xmlns="http://www.w3.org/2005/Atom"/>'


# ── rest:logon-and-logoff — endpoint and method ──────────────────────────────


@pytest.mark.asyncio
async def test_logon_uses_put_on_web_logon_path(mock_hmc):
    """rest:logon-and-logoff: logon uses PUT /rest/api/web/Logon (row L00026)."""
    # Re-mock the pre-registered route to capture the call after construction.
    route = mock_hmc.put(_LOGON_PATH).mock(
        return_value=httpx.Response(200, text=LOGON_RESPONSE)
    )
    async with HMCClient(make_config()):
        pass
    assert route.called


@pytest.mark.asyncio
async def test_logoff_uses_delete_on_web_logon_path(mock_hmc):
    """rest:logon-and-logoff: logoff uses DELETE /rest/api/web/Logon (row L00071)."""
    delete_route = mock_hmc.delete(_LOGON_PATH).mock(
        return_value=httpx.Response(204)
    )
    async with HMCClient(make_config()):
        pass
    assert delete_route.called


# ── rest:logon-and-logoff — media types ─────────────────────────────────────


@pytest.mark.asyncio
async def test_logon_request_carries_web_xml_content_type(mock_hmc):
    """rest:logon-and-logoff: logon PUT body uses web+xml; type=LogonRequest (row L00031)."""
    route = mock_hmc.put(_LOGON_PATH).mock(
        return_value=httpx.Response(200, text=LOGON_RESPONSE)
    )
    async with HMCClient(make_config()):
        pass
    sent = route.calls.last.request.headers.get("content-type", "")
    assert MEDIA_WEB in sent, f"Expected {MEDIA_WEB!r} in Content-Type, got: {sent!r}"
    assert "LogonRequest" in sent, f"Expected 'LogonRequest' in Content-Type, got: {sent!r}"


@pytest.mark.asyncio
async def test_logon_request_accepts_web_xml_logon_response(mock_hmc):
    """rest:logon-and-logoff: logon PUT Accept requests LogonResponse (row L00048)."""
    route = mock_hmc.put(_LOGON_PATH).mock(
        return_value=httpx.Response(200, text=LOGON_RESPONSE)
    )
    async with HMCClient(make_config()):
        pass
    sent = route.calls.last.request.headers.get("accept", "")
    assert MEDIA_WEB in sent, f"Expected {MEDIA_WEB!r} in Accept, got: {sent!r}"
    assert "LogonResponse" in sent, f"Expected 'LogonResponse' in Accept, got: {sent!r}"


# ── rest:logon-and-logoff — session token propagation ───────────────────────


@pytest.mark.asyncio
async def test_session_token_propagates_to_subsequent_requests(mock_hmc):
    """rest:logon-and-logoff: X-API-Session token from logon appears on resource GETs (row L00024)."""
    route = mock_hmc.get(_LP_PATH).mock(
        return_value=httpx.Response(200, text=_EMPTY_FEED)
    )
    async with HMCClient(make_config()) as hmc:
        await hmc.list_logical_partitions()
    sent = route.calls.last.request.headers.get("x-api-session", "")
    assert sent == "test-session-token-123", (
        f"Expected session token on resource request, got: {sent!r}"
    )


@pytest.mark.asyncio
async def test_session_token_absent_before_logon():
    """rest:logon-and-logoff: X-API-Session header is absent before logon (row L00069)."""
    with respx.mock(assert_all_called=False):
        hmc = HMCClient(make_config())
        assert "x-api-session" not in hmc._http.headers


@pytest.mark.asyncio
async def test_session_token_cleared_after_logoff(mock_hmc):
    """rest:logon-and-logoff: X-API-Session header is removed after logoff."""
    async with HMCClient(make_config()) as hmc:
        assert hmc._session_token == "test-session-token-123"
    assert hmc._session_token is None
    assert "x-api-session" not in hmc._http.headers


# ── UOM GET — media type ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_uom_get_sends_uom_accept_header(mock_hmc):
    """UOM GETs carry Accept: application/vnd.ibm.powervm.uom+xml (no type qualifier)."""
    route = mock_hmc.get(_LP_PATH).mock(
        return_value=httpx.Response(200, text=_EMPTY_FEED)
    )
    async with HMCClient(make_config()) as hmc:
        await hmc._get(_LP_PATH)
    sent = route.calls.last.request.headers.get("accept", "")
    assert sent.startswith(MEDIA_UOM), f"Expected {MEDIA_UOM!r} Accept, got: {sent!r}"


@pytest.mark.asyncio
async def test_uom_get_with_resource_type_qualifies_accept(mock_hmc):
    """UOM GETs with a resource type carry Accept: uom+xml; type=<type>."""
    route = mock_hmc.get(_LP_PATH).mock(
        return_value=httpx.Response(200, text=_EMPTY_FEED)
    )
    async with HMCClient(make_config()) as hmc:
        await hmc._get(_LP_PATH, resource_type="LogicalPartition")
    sent = route.calls.last.request.headers.get("accept", "")
    assert f"{MEDIA_UOM}; type=LogicalPartition" == sent, (
        f"Expected qualified Accept, got: {sent!r}"
    )


@pytest.mark.asyncio
async def test_uom_post_mirrors_accept_as_content_type(mock_hmc):
    """UOM POST Content-Type equals its Accept header."""
    route = mock_hmc.post(_LP_PATH).mock(
        return_value=httpx.Response(201, text=_EMPTY_FEED)
    )
    async with HMCClient(make_config()) as hmc:
        await hmc._post(_LP_PATH, b"<xml/>", resource_type="LogicalPartition")
    sent_accept = route.calls.last.request.headers.get("accept", "")
    sent_ct = route.calls.last.request.headers.get("content-type", "")
    assert sent_accept == sent_ct, (
        f"POST Content-Type ({sent_ct!r}) must equal Accept ({sent_accept!r})"
    )


@pytest.mark.asyncio
async def test_uom_put_mirrors_accept_as_content_type(mock_hmc):
    """UOM PUT Content-Type equals its Accept header."""
    route = mock_hmc.put(f"{_LP_PATH}/uuid1").mock(
        return_value=httpx.Response(200, text=_EMPTY_FEED)
    )
    async with HMCClient(make_config()) as hmc:
        await hmc._put(f"{_LP_PATH}/uuid1", b"<xml/>", resource_type="LogicalPartition")
    sent_accept = route.calls.last.request.headers.get("accept", "")
    sent_ct = route.calls.last.request.headers.get("content-type", "")
    assert sent_accept == sent_ct, (
        f"PUT Content-Type ({sent_ct!r}) must equal Accept ({sent_accept!r})"
    )


# ── rest:job-status — complete terminal-status vocabulary ────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        # rest:job-status row — actionable terminal statuses
        "EXCEPTION",
        "COMPLETED_WITH_ERROR",
    ],
)
async def test_wait_for_job_treats_remaining_terminal_statuses_as_terminal(
    status, mock_hmc
):
    """rest:job-status: EXCEPTION and COMPLETED_WITH_ERROR are terminal (row fields L00022–L00025).

    Both statuses are in TERMINAL_JOB_STATUSES but had no dedicated wait test.
    A wait that did not recognise them would loop until the deadline; this test
    confirms they stop the poll immediately.
    """
    from hmc_mcp.jobs import TERMINAL_JOB_STATUSES

    assert status in TERMINAL_JOB_STATUSES, (
        f"{status!r} is not in TERMINAL_JOB_STATUSES — reference row contract broken"
    )

    job_entry = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<entry xmlns="http://www.w3.org/2005/Atom">'
        '  <id>urn:uuid:job-terminal</id>'
        "  <content>"
        f'    <Job xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">'
        f"      <JobID>job-terminal</JobID>"
        f"      <Status>{status}</Status>"
        "    </Job>"
        "  </content>"
        "</entry>"
    )
    mock_hmc.get("/rest/api/uom/jobs/job-terminal").mock(
        return_value=httpx.Response(200, text=job_entry)
    )
    async with HMCClient(make_config()) as hmc:
        result = await hmc.wait_for_job_entry(
            "job-terminal", timeout_seconds=5, poll_interval=1
        )
    assert result is not None
    assert result["Resource"]["Status"] == status


@pytest.mark.asyncio
async def test_terminal_status_set_matches_reference_row(mock_hmc):
    """rest:job-status: TERMINAL_JOB_STATUSES contains exactly the reference-documented values.

    The reference (rows.json row 'rest:job-status') names eleven terminal states.
    This test pins the set so that a future edit is visible here.
    """
    from hmc_mcp.jobs import TERMINAL_JOB_STATUSES

    expected = {
        "CANCELED_BEFORE_START",
        "CANCELED_WHILE_RUNNING",
        "COMPLETED",
        "COMPLETED_OK",
        "COMPLETED_WITH_ERROR",
        "COMPLETED_WITH_WARNINGS",
        "EXCEPTION",
        "FAILED",
        "FAILED_BEFORE_COMPLETION",
        "FAILED_BEFORE_COMPLETION_RETRY",
        "FAILED_TO_START",
    }
    assert TERMINAL_JOB_STATUSES == expected, (
        f"TERMINAL_JOB_STATUSES differs from the rest:job-status reference row.\n"
        f"  Missing: {expected - TERMINAL_JOB_STATUSES}\n"
        f"  Extra:   {TERMINAL_JOB_STATUSES - expected}"
    )
