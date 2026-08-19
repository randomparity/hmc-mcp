"""Shared pytest fixtures for the hmc-mcp suite."""

import logging

import httpx
import pytest
import respx

from hmc_mcp.audit import AUDIT_LOGGER_NAME
from hmc_mcp.config import HMCConfig


@pytest.fixture(autouse=True)
def isolate_audit_logging():
    """Give every test a pristine ``hmc_mcp.audit`` logger, and restore it after.

    ``audit.install_audit_sink`` mutates process-global state: it sets
    ``propagate = False`` unconditionally, attaches a handler, and sets a level.
    Several tests call it directly, and ``server._serve_application`` calls it too
    — which ``tests/app/test_capability_ceiling.py`` and
    ``tests/app/test_connection_authorization.py`` drive in-process. Whichever runs
    first would otherwise leave the sink installed for the rest of the session, and
    a later ``caplog`` assertion would capture nothing and pass vacuously.

    So it resets at setup as well as restoring at teardown: restoring alone would
    faithfully restore an earlier test's contamination, which is the failure this
    exists to prevent. Autouse and in ``conftest.py`` rather than opted into
    per-file, because the files that merely *emit* a record — every test driving an
    ADR 0011 ownership override — are as able to leak state as the ones that read
    records, and any enumeration of them goes stale.

    It also snapshots and restores ``logging.root.handlers``, which it never
    modifies itself. That is a deliberate belt-and-braces sweep, not dead code:
    several audit tests clear or add root handlers to observe
    ``logging.lastResort``, which ``callHandlers`` consults only after an ancestor
    walk finds none. Each of those cleans up after itself; this is the backstop for
    one that forgets, since a stray root handler silently changes what every later
    test captures.
    """
    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    saved_propagate = logger.propagate
    saved_root = list(logging.root.handlers)
    logger.handlers.clear()
    logger.setLevel(logging.NOTSET)
    logger.propagate = True
    try:
        yield
    finally:
        logger.handlers[:] = saved_handlers
        logger.setLevel(saved_level)
        logger.propagate = saved_propagate
        logging.root.handlers[:] = saved_root


BASE = "https://hmc.test:12443"

LOGON_RESPONSE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<LogonResponse xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/">
  <X-API-Session>test-session-token-123</X-API-Session>
</LogonResponse>
"""

JOB_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:job-uuid-999</id>
  <title>Job</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <Job xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <JobID>job-uuid-999</JobID>
      <Status>RUNNING</Status>
    </Job>
  </content>
</entry>
"""

# Single-resource uom entries used by SSH tools to resolve a system / LPAR
# UUID to its CLI name via REST. Both accept {uuid} and {name} placeholders.
SYSTEM_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:{uuid}</id>
  <title>ManagedSystem:{name}</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <SystemName>{name}</SystemName>
    </ManagedSystem>
  </content>
</entry>
"""

LPAR_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:{uuid}</id>
  <title>LogicalPartition:{name}</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <PartitionName>{name}</PartitionName>
    </LogicalPartition>
  </content>
</entry>
"""


def mock_uuid_resolution(
    router,
    system_uuid: str,
    system_name: str,
    lpar_uuid: str | None = None,
    lpar_name: str | None = None,
):
    """Register respx routes resolving system/lpar UUIDs to their CLI names.

    SSH-passthrough tools look up a UUID via REST before running the HMC
    command, so tests must mock the ``get_managed_system`` /
    ``get_logical_partition`` GETs in addition to ``asyncssh.connect``.
    """
    router.get(f"/rest/api/uom/ManagedSystem/{system_uuid}").mock(
        return_value=httpx.Response(
            200, text=SYSTEM_ENTRY.format(uuid=system_uuid, name=system_name)
        )
    )
    if lpar_uuid is not None:
        router.get(f"/rest/api/uom/LogicalPartition/{lpar_uuid}").mock(
            return_value=httpx.Response(
                200, text=LPAR_ENTRY.format(uuid=lpar_uuid, name=lpar_name)
            )
        )


def make_config(**kw) -> HMCConfig:
    """Build a test HMCConfig; any field may be overridden via **kw."""
    defaults = {
        "host": "hmc.test",
        "user": "hscroot",
        "password": "abc123",
        "verify_ssl": False,
        "_env_file": None,  # suppress .env loading so live creds/schema don't bleed in
    }
    defaults.update(kw)
    return HMCConfig(**defaults)


@pytest.fixture
def mock_hmc():
    """respx router with logon/logoff pre-mocked; add per-test routes."""
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        router.put("/rest/api/web/Logon").mock(
            return_value=httpx.Response(200, text=LOGON_RESPONSE)
        )
        router.delete("/rest/api/web/Logon").mock(return_value=httpx.Response(204))
        yield router


# The session lifecycle is the only non-GET traffic a read-only call may produce.
_SESSION_PATHS = frozenset({"/rest/api/web/Logon"})


def assert_no_mutating_requests(router) -> int:
    """Assert *router* recorded no HMC mutation, and return the request count.

    Epic #218 requirement 5 asks that a ``dry_run=True`` path "provably performs
    no mutation" be *classified and tested explicitly rather than inferred from
    client claims". A per-route ``assert not route.called`` cannot do that: it
    proves the one route a test happened to register was not taken, and stays
    green when the handler mutates through a route nobody registered.

    This reads every request the call actually made, so a new write on the
    dry-run path fails whether or not the test anticipated it. ADR 0039 records
    what remains on those paths and is deliberately not a mutation: reads, and —
    on ``hmc_decommission_lpar`` — an SSH ``lssyscfg`` and a local audit line.
    """
    offending = [
        f"{call.request.method} {call.request.url.path}"
        for call in router.calls
        if call.request.method != "GET"
        and call.request.url.path not in _SESSION_PATHS
    ]
    assert not offending, f"a dry-run path mutated the HMC: {offending}"
    return len(router.calls)


def assert_only_these_client_methods_used(client, allowed: frozenset[str]) -> set[str]:
    """Assert *client* saw only *allowed* methods, and return the ones it saw.

    The mock-client counterpart of :func:`assert_no_mutating_requests`, for the
    handlers whose tests drive an ``AsyncMock`` rather than a transport. It pins
    the whole call set rather than asserting a handful of ``assert_not_awaited``
    negatives, so a *new* call on a dry-run path fails the test whether or not it
    mutates — which is the point: epic #218 requirement 5 asks the path to be
    classified, and a call nobody classified is exactly what must not slip in.
    """
    used = {name.split(".")[0] for name, _args, _kwargs in client.mock_calls if name}
    unexpected = used - allowed
    assert not unexpected, (
        f"unclassified calls on a dry-run path: {sorted(unexpected)}. Each is "
        "either a mutation (a defect) or a read that must be added to the pinned "
        "set with its classification."
    )
    return used
