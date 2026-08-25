"""Contract tests for the supported cross-process job-polling operations (ADR 0093).

The operations exist so a consumer can persist a job identifier in one process and
poll it from another. Every test here therefore passes plain strings — never a job
object, a live coroutine, or a client instance carried over from submission.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from hmc_mcp.api import JobOutcome, get_job, wait_for_job
from hmc_mcp.client import HMCClient
from hmc_mcp.errors import HMCError

from conftest import make_config

_JOB_ID = "job-uuid-999"
_GLOBAL_PATH = f"/rest/api/uom/jobs/{_JOB_ID}"
_SELF_HREF = f"/rest/api/uom/LogicalPartition/lpar-uuid/do/PowerOn/Job/{_JOB_ID}"
_SUBMIT_PATH = "/rest/api/uom/LogicalPartition/lpar-uuid/do/PowerOn"


def _job_entry(status: str, *, self_href: str | None = None) -> str:
    """One Atom job entry as the HMC returns it, optionally carrying its SELF link."""
    link = f'  <link rel="SELF" href="{self_href}"/>\n' if self_href else ""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<entry xmlns="http://www.w3.org/2005/Atom">\n'
        f"  <id>urn:uuid:{_JOB_ID}</id>\n"
        "  <title>Job:PowerOn</title>\n"
        f"{link}"
        '  <content type="application/vnd.ibm.powervm.uom+xml">\n'
        '    <Job xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/'
        'mc/2012_10/">\n'
        f"      <JobID>{_JOB_ID}</JobID>\n"
        f"      <Status>{status}</Status>\n"
        "    </Job>\n"
        "  </content>\n"
        "</entry>\n"
    )


_FAILED_ENTRY = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<entry xmlns="http://www.w3.org/2005/Atom">\n'
    f"  <id>urn:uuid:{_JOB_ID}</id>\n"
    '  <content type="application/vnd.ibm.powervm.uom+xml">\n'
    '    <Job xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/'
    'mc/2012_10/">\n'
    f"      <JobID>{_JOB_ID}</JobID>\n"
    "      <Status>FAILED</Status>\n"
    "      <Results>\n"
    "        <JobParameter>\n"
    "          <ParameterName>result</ParameterName>\n"
    "          <ParameterValue>boot device missing</ParameterValue>\n"
    "        </JobParameter>\n"
    "      </Results>\n"
    "    </Job>\n"
    "  </content>\n"
    "</entry>\n"
)


@pytest.mark.asyncio
async def test_wait_for_job_polls_an_identifier_persisted_across_a_process_restart(
    mock_hmc,
) -> None:
    """The critical acceptance path: only strings survive from submission to poll.

    The submitting client is closed before the polling client is constructed, and
    the only thing crossing between them is a JSON round trip — the stand-in for the
    database a restarted worker reads its handle back from.
    """
    mock_hmc.put(_SUBMIT_PATH).mock(
        return_value=httpx.Response(200, text=_job_entry("RUNNING", self_href=_SELF_HREF))
    )
    poll = mock_hmc.get(_SELF_HREF).mock(
        return_value=httpx.Response(200, text=_job_entry("COMPLETED_OK"))
    )

    async with HMCClient(make_config()) as submitting:
        submitted = await submitting.submit_job(_SUBMIT_PATH, "<JobRequest/>")
    stored = json.dumps({"job_id": submitted["UUID"], "job_href": submitted["link"]})

    handle = json.loads(stored)
    assert isinstance(handle["job_id"], str) and isinstance(handle["job_href"], str)

    async with HMCClient(make_config()) as polling:
        outcome = await wait_for_job(
            polling,
            handle["job_id"],
            job_href=handle["job_href"],
            timeout_seconds=30,
            poll_interval=1,
        )

    assert poll.called
    assert isinstance(outcome, JobOutcome)
    assert outcome.found is True
    assert outcome.status == "COMPLETED_OK"
    assert outcome.timed_out is False
    assert outcome.error is None
    assert outcome.job_id == _JOB_ID


@pytest.mark.asyncio
async def test_wait_for_job_returns_a_terminal_job_after_one_poll(mock_hmc) -> None:
    """An already-finished job returns its outcome instead of blocking."""
    route = mock_hmc.get(_GLOBAL_PATH).mock(
        return_value=httpx.Response(200, text=_job_entry("COMPLETED"))
    )

    async with HMCClient(make_config()) as hmc:
        outcome = await wait_for_job(
            hmc, _JOB_ID, timeout_seconds=300, poll_interval=60
        )

    assert route.call_count == 1
    assert outcome.status == "COMPLETED"
    assert outcome.timed_out is False
    assert outcome.found is True


@pytest.mark.asyncio
async def test_wait_for_job_reports_an_actionable_terminal_job(mock_hmc) -> None:
    """A failed job keeps its ADR 0081 classification: terminal, with an error."""
    mock_hmc.get(_GLOBAL_PATH).mock(
        return_value=httpx.Response(200, text=_FAILED_ENTRY)
    )

    async with HMCClient(make_config()) as hmc:
        outcome = await wait_for_job(hmc, _JOB_ID, timeout_seconds=0, poll_interval=1)

    assert outcome.found is True
    assert outcome.timed_out is False
    assert outcome.status == "FAILED"
    assert outcome.error == "boot device missing"


@pytest.mark.asyncio
async def test_get_job_reports_a_reaped_identifier_as_not_found(mock_hmc) -> None:
    """A 404 becomes a documented outcome, not an opaque transport error."""
    mock_hmc.get(_GLOBAL_PATH).mock(
        return_value=httpx.Response(404, text="Unknown job")
    )

    async with HMCClient(make_config()) as hmc:
        outcome = await get_job(hmc, _JOB_ID)

    assert outcome.found is False
    assert outcome.status is None
    assert outcome.job is None
    assert outcome.job_id == _JOB_ID


@pytest.mark.asyncio
async def test_get_job_reports_an_empty_job_response_as_not_found(mock_hmc) -> None:
    """An HMC that answers with no job entry is the same observation as a 404."""
    mock_hmc.get(_GLOBAL_PATH).mock(return_value=httpx.Response(204))

    async with HMCClient(make_config()) as hmc:
        outcome = await get_job(hmc, _JOB_ID)

    assert outcome.found is False


@pytest.mark.asyncio
async def test_get_job_distinguishes_a_running_job_from_a_gone_one(mock_hmc) -> None:
    """The distinction a restarted worker needs: still running versus gone."""
    running_route = mock_hmc.get(_GLOBAL_PATH).mock(
        return_value=httpx.Response(200, text=_job_entry("RUNNING"))
    )

    async with HMCClient(make_config()) as hmc:
        running = await get_job(hmc, _JOB_ID)

    assert running_route.called
    assert (running.found, running.status) == (True, "RUNNING")
    assert running.timed_out is True


@pytest.mark.asyncio
async def test_wait_for_job_stops_as_soon_as_the_job_disappears(mock_hmc) -> None:
    """Polling ends at the first missing read rather than running out the timeout."""
    route = mock_hmc.get(_GLOBAL_PATH).mock(
        side_effect=[
            httpx.Response(200, text=_job_entry("RUNNING")),
            httpx.Response(404, text="Unknown job"),
        ]
    )

    async with HMCClient(make_config()) as hmc:
        outcome = await wait_for_job(
            hmc, _JOB_ID, timeout_seconds=3600, poll_interval=1
        )

    assert route.call_count == 2
    assert outcome.found is False


@pytest.mark.asyncio
async def test_wait_for_job_reports_a_still_running_job_at_the_deadline(
    mock_hmc,
) -> None:
    """``timeout_seconds=0`` performs exactly one poll and reports the timeout."""
    route = mock_hmc.get(_GLOBAL_PATH).mock(
        return_value=httpx.Response(200, text=_job_entry("RUNNING"))
    )

    async with HMCClient(make_config()) as hmc:
        outcome = await wait_for_job(hmc, _JOB_ID, timeout_seconds=0, poll_interval=5)

    assert route.call_count == 1
    assert outcome.found is True
    assert outcome.timed_out is True
    assert outcome.status == "RUNNING"


@pytest.mark.asyncio
async def test_get_job_propagates_a_transport_failure_that_is_not_a_missing_job(
    mock_hmc,
) -> None:
    """Only 404 is translated; every other HMC failure still raises."""
    mock_hmc.get(_GLOBAL_PATH).mock(
        return_value=httpx.Response(500, text="Internal error")
    )

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError):
            await get_job(hmc, _JOB_ID)


@pytest.mark.asyncio
async def test_get_job_uses_the_persisted_self_link_when_supplied(mock_hmc) -> None:
    """A persisted SELF link addresses the job on firmware that cannot resolve the UUID."""
    href_route = mock_hmc.get(_SELF_HREF).mock(
        return_value=httpx.Response(200, text=_job_entry("COMPLETED_OK"))
    )
    global_route = mock_hmc.get(_GLOBAL_PATH).mock(
        return_value=httpx.Response(400, text="Unrecognized root REST type of Job")
    )

    async with HMCClient(make_config()) as hmc:
        outcome = await get_job(hmc, _JOB_ID, job_href=_SELF_HREF)

    assert href_route.called
    assert not global_route.called
    assert outcome.job_href == _SELF_HREF


@pytest.mark.asyncio
async def test_get_job_echoes_the_handle_needed_to_poll_again(mock_hmc) -> None:
    """The outcome carries both persistable strings, so the handle round-trips."""
    mock_hmc.get(_GLOBAL_PATH).mock(
        return_value=httpx.Response(200, text=_job_entry("RUNNING", self_href=_SELF_HREF))
    )

    async with HMCClient(make_config()) as hmc:
        outcome = await get_job(hmc, _JOB_ID)

    assert (outcome.job_id, outcome.job_href) == (_JOB_ID, _SELF_HREF)


@pytest.mark.asyncio
async def test_get_job_reports_a_blank_job_href_as_no_link(mock_hmc) -> None:
    """A blank link falls back to the global path and is not echoed as usable."""
    global_route = mock_hmc.get(_GLOBAL_PATH).mock(
        return_value=httpx.Response(200, text=_job_entry("RUNNING"))
    )

    async with HMCClient(make_config()) as hmc:
        outcome = await get_job(hmc, _JOB_ID, job_href="   ")

    assert global_route.called
    assert outcome.job_href is None


@pytest.mark.asyncio
async def test_get_job_refuses_a_job_href_that_addresses_another_resource(
    mock_hmc,
) -> None:
    """The client's job-path guard still bounds a persisted link (ADR 0039)."""
    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError, match="job_href refused"):
            await get_job(hmc, _JOB_ID, job_href="/rest/api/uom/HmcUser/root")


@pytest.mark.asyncio
async def test_cancelling_the_waiter_leaves_the_session_usable(mock_hmc) -> None:
    """Cancellation unwinds nothing: no session mutation, and the client still works."""
    poll = mock_hmc.get(_GLOBAL_PATH).mock(
        return_value=httpx.Response(200, text=_job_entry("RUNNING"))
    )
    logoff = mock_hmc.delete("/rest/api/web/Logon")

    async with HMCClient(make_config()) as hmc:
        waiter = asyncio.create_task(
            wait_for_job(hmc, _JOB_ID, timeout_seconds=3600, poll_interval=600)
        )
        while not poll.called:
            await asyncio.sleep(0)
        for _ in range(10):
            await asyncio.sleep(0)

        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert hmc.is_logged_on
        assert not logoff.called
        assert (await get_job(hmc, _JOB_ID)).status == "RUNNING"


@pytest.mark.asyncio
@pytest.mark.parametrize("job_id", ["", "   "])
async def test_job_operations_reject_an_empty_identifier(mock_hmc, job_id) -> None:
    async with HMCClient(make_config()) as hmc:
        with pytest.raises(ValueError, match="job_id"):
            await get_job(hmc, job_id)
        with pytest.raises(ValueError, match="job_id"):
            await wait_for_job(hmc, job_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("timeout_seconds", "poll_interval", "message"),
    [(-1, 5, "timeout_seconds"), (10, 0, "poll_interval"), (10, -1, "poll_interval")],
)
async def test_wait_for_job_rejects_invalid_polling_settings(
    mock_hmc, timeout_seconds, poll_interval, message
) -> None:
    async with HMCClient(make_config()) as hmc:
        with pytest.raises(ValueError, match=message):
            await wait_for_job(
                hmc,
                _JOB_ID,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )
