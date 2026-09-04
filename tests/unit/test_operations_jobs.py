"""Contract tests for the supported cross-process job-polling operations (ADR 0093).

The operations exist so a consumer can persist a job identifier in one process and
poll it from another. Every test here therefore passes plain strings — never a job
object, a live coroutine, or a client instance carried over from submission.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from conftest import make_config

from hmc_mcp.api import JobOutcome, get_job, wait_for_job
from hmc_mcp.client.core import HMCClient
from hmc_mcp.errors import HMCError

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
async def test_get_job_reports_an_empty_job_response_as_not_found(
    mock_hmc, caplog
) -> None:
    """An HMC that answers with no job entry is the same observation as a 404.

    It is also the same destructive signal, so it is as loud: an HMC answering
    the jobs path with no content reports every job gone, forever.
    """
    mock_hmc.get(_GLOBAL_PATH).mock(return_value=httpx.Response(204))

    with caplog.at_level(logging.WARNING, logger="hmc_mcp.operations.jobs"):
        async with HMCClient(make_config()) as hmc:
            outcome = await get_job(hmc, _JOB_ID)

    assert outcome.found is False
    assert any(
        record.levelno == logging.WARNING and _JOB_ID in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_get_job_does_not_hand_back_a_link_on_a_missing_job(mock_hmc) -> None:
    """A found=False outcome carries no handle: nothing resolved to persist."""
    mock_hmc.get(_SELF_HREF).mock(return_value=httpx.Response(404, text="Unknown job"))
    mock_hmc.get(_GLOBAL_PATH).mock(return_value=httpx.Response(404, text="Unknown job"))

    async with HMCClient(make_config()) as hmc:
        outcome = await get_job(hmc, _JOB_ID, job_href=_SELF_HREF)

    assert (outcome.found, outcome.job_href) == (False, None)


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
async def test_wait_for_job_confirms_a_disappearance_then_stops(mock_hmc) -> None:
    """A vanished job ends the wait after one confirming read, not at the deadline."""
    route = mock_hmc.get(_GLOBAL_PATH).mock(
        side_effect=[
            httpx.Response(200, text=_job_entry("RUNNING")),
            httpx.Response(404, text="Unknown job"),
            httpx.Response(404, text="Unknown job"),
        ]
    )

    async with HMCClient(make_config()) as hmc:
        outcome = await wait_for_job(
            hmc, _JOB_ID, timeout_seconds=3600, poll_interval=1
        )

    assert route.call_count == 3
    assert outcome.found is False


@pytest.mark.asyncio
async def test_wait_for_job_does_not_report_a_momentary_404_as_a_vanished_job(
    mock_hmc,
) -> None:
    """One 404 is the only failure here that returns instead of raising.

    A proxy reload or a failover must not be handed to a consumer as "your
    90-minute install is gone", because the documented re-call recovery cannot
    undo an answer the caller has already acted on.
    """
    route = mock_hmc.get(_GLOBAL_PATH).mock(
        side_effect=[
            httpx.Response(200, text=_job_entry("RUNNING")),
            httpx.Response(404, text="Unknown job"),
            httpx.Response(200, text=_job_entry("COMPLETED_OK")),
        ]
    )

    async with HMCClient(make_config()) as hmc:
        outcome = await wait_for_job(
            hmc, _JOB_ID, timeout_seconds=3600, poll_interval=1
        )

    assert route.call_count == 3
    assert (outcome.found, outcome.status) == (True, "COMPLETED_OK")


@pytest.mark.asyncio
async def test_wait_for_job_reports_a_first_read_miss_immediately(mock_hmc) -> None:
    """With no earlier observation to contradict, one missing read is the answer."""
    route = mock_hmc.get(_GLOBAL_PATH).mock(
        return_value=httpx.Response(404, text="Unknown job")
    )

    async with HMCClient(make_config()) as hmc:
        outcome = await wait_for_job(
            hmc, _JOB_ID, timeout_seconds=3600, poll_interval=1
        )

    assert route.call_count == 1
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
async def test_get_job_keeps_the_link_the_caller_polled_with(mock_hmc) -> None:
    """A stored handle does not rotate to an untried link the response advertises."""
    other_link = f"/rest/api/uom/LogicalPartition/other/do/PowerOn/Job/{_JOB_ID}"
    mock_hmc.get(_SELF_HREF).mock(
        return_value=httpx.Response(
            200, text=_job_entry("RUNNING", self_href=other_link)
        )
    )

    async with HMCClient(make_config()) as hmc:
        outcome = await get_job(hmc, _JOB_ID, job_href=_SELF_HREF)

    assert outcome.job_href == _SELF_HREF


@pytest.mark.asyncio
async def test_get_job_warns_with_the_discarded_detail_when_a_job_is_missing(
    mock_hmc, caplog
) -> None:
    """The one place an error becomes an ordinary value leaves a loud record.

    A deployment whose job path 404s answers ``found=False`` for every job, and a
    consumer acts on that signal, so it is not an INFO-level event.
    """
    mock_hmc.get(_GLOBAL_PATH).mock(
        return_value=httpx.Response(404, text="<Message>Unknown job</Message>")
    )

    with caplog.at_level(logging.WARNING, logger="hmc_mcp.operations.jobs"):
        async with HMCClient(make_config()) as hmc:
            assert (await get_job(hmc, _JOB_ID)).found is False

    assert any(
        record.levelno == logging.WARNING
        and _JOB_ID in record.getMessage()
        and "Unknown job" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_get_job_confirms_a_stale_link_against_the_global_path(
    mock_hmc, caplog
) -> None:
    """A SELF link can stop resolving while the job is fine; do not call that gone."""
    stale = mock_hmc.get(_SELF_HREF).mock(
        return_value=httpx.Response(404, text="Unknown job")
    )
    fallback = mock_hmc.get(_GLOBAL_PATH).mock(
        return_value=httpx.Response(
            200, text=_job_entry("RUNNING", self_href=_SELF_HREF)
        )
    )

    with caplog.at_level(logging.WARNING, logger="hmc_mcp.operations.jobs"):
        async with HMCClient(make_config()) as hmc:
            outcome = await get_job(hmc, _JOB_ID, job_href=_SELF_HREF)

    assert stale.called and fallback.called
    assert (outcome.found, outcome.status) == (True, "RUNNING")
    assert outcome.job_href is None, "a link proved stale is not handed back"
    assert any("no longer resolves" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_get_job_drops_a_stale_link_with_an_equivalent_absolute_spelling(
    mock_hmc,
) -> None:
    """A retired resource stays retired when the response makes its link absolute."""
    mock_hmc.get(_SELF_HREF).mock(
        return_value=httpx.Response(404, text="Unknown job")
    )
    absolute_self_href = f"https://hmc.test:443{_SELF_HREF}"
    mock_hmc.get(_GLOBAL_PATH).mock(
        return_value=httpx.Response(
            200, text=_job_entry("RUNNING", self_href=absolute_self_href)
        )
    )

    async with HMCClient(make_config()) as hmc:
        outcome = await get_job(hmc, _JOB_ID, job_href=_SELF_HREF)

    assert (outcome.found, outcome.job_href) == (True, None)


@pytest.mark.asyncio
async def test_wait_for_job_drops_a_stale_link_after_confirming_it_once(
    mock_hmc, caplog
) -> None:
    """The confirming read and its warning happen once, not on every poll."""
    stale = mock_hmc.get(_SELF_HREF).mock(
        return_value=httpx.Response(404, text="Unknown job")
    )
    fallback = mock_hmc.get(_GLOBAL_PATH).mock(
        side_effect=[
            httpx.Response(200, text=_job_entry("RUNNING", self_href=_SELF_HREF)),
            httpx.Response(200, text=_job_entry("COMPLETED_OK", self_href=_SELF_HREF)),
        ]
    )

    with caplog.at_level(logging.WARNING, logger="hmc_mcp.operations.jobs"):
        async with HMCClient(make_config()) as hmc:
            outcome = await wait_for_job(
                hmc,
                _JOB_ID,
                job_href=_SELF_HREF,
                timeout_seconds=3600,
                poll_interval=1,
            )

    assert stale.call_count == 1, "the stale link must not be retried every poll"
    assert fallback.call_count == 2
    assert outcome.status == "COMPLETED_OK"
    assert outcome.job_href is None, (
        "a link retired earlier in the wait must not come back on a later poll"
    )
    assert len([r for r in caplog.records if "no longer resolves" in r.getMessage()]) == 1
