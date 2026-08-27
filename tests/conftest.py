"""Shared pytest fixtures for the hmc-mcp suite."""

import io
import logging
import os
import select
import sys
from dataclasses import dataclass

import fastmcp  # noqa: F401 — imported for its import-time logging configuration
import httpx
import pytest
import respx

from hmc_mcp import audit
from hmc_mcp import client as hmc_client
from hmc_mcp.audit import AUDIT_LOGGER_NAME
from hmc_mcp.config import HMCConfig


#: The ``fastmcp`` logger and its handlers as importing ``fastmcp`` leaves them.
#: Captured at collection time, before any test can serve, because ADR 0051 made
#: ``server._serve_application`` *replace* those handlers — so unlike the audit
#: logger, whose pristine state is "empty", no later code can reconstruct this one.
#:
#: ``import fastmcp`` above is what makes the snapshot the right one rather than an
#: empty list: ``fastmcp/__init__.py`` attaches the handlers, and nothing else this
#: module imports pulls that package in. Snapshotting before it would silently
#: replace every test's ``fastmcp`` logger with an unconfigured one.
_FASTMCP_LOGGER = logging.getLogger("fastmcp")
_PRISTINE_FASTMCP = (
    list(_FASTMCP_LOGGER.handlers),
    _FASTMCP_LOGGER.level,
    _FASTMCP_LOGGER.propagate,
)

_THIRD_PARTY_LOGGERS = tuple(
    logging.getLogger(name) for name in ("uvicorn", "uvicorn.access", "mcp")
)
#: Pristine state of the loggers #330's install binds beyond ``fastmcp``: captured
#: at collection like ``_PRISTINE_FASTMCP``, though for these it is simply "empty,
#: NOTSET, propagating" — nothing configures them at import while ``dictConfig``
#: never runs. Snapshotted rather than assumed so a future import-time configurator
#: changes the fixture's behaviour instead of being silently overwritten by it.
_PRISTINE_THIRD_PARTY = tuple(
    (list(each.handlers), each.level, each.propagate) for each in _THIRD_PARTY_LOGGERS
)


@pytest.fixture(autouse=True)
def enable_tls_verification_for_tests(monkeypatch):
    """Keep mocked HMC connections secure unless a test opts out explicitly."""
    monkeypatch.setenv("HMC_VERIFY_SSL", "true")


@pytest.fixture(autouse=True)
def default_power_ownership_guard_off(monkeypatch):
    """Give every test the shipped default of the ADR 0092 §4 power guard.

    `HMCConfig` reads `HMC_AUTHORIZE_POWER_OPERATIONS` from the environment
    like every other field, so a developer who exports it turns the ownership
    guard on inside every test that builds its config from the environment —
    the tool and CLI layers. Those tests then send the guard's ownership name
    lookups at routes respx never registered, and fail for a reason that has
    nothing to do with what they assert.

    Cleared here rather than in each module: the default is what nearly every
    test means to exercise. A test that wants the guard on sets the variable
    in its own body, which runs after this fixture, or builds the config with
    `HMCConfig.from_mapping` and bypasses the environment entirely (ADR 0096).

    Every casing, not just the canonical one: `HMCConfig` leaves
    pydantic-settings' `case_sensitive` at its `False` default, so a developer or
    CI runner exporting `hmc_authorize_power_operations` sets the field exactly
    as the upper-case spelling does — and `server_permissions` reads the casing
    itself to decide the reported `source`. Clearing only the exact name leaves
    both observable in the tests that pin them.
    """
    spellings = [n for n in os.environ if n.upper() == "HMC_AUTHORIZE_POWER_OPERATIONS"]
    for name in spellings:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def isolate_tls_warning_dedup_state():
    """Keep the process-global TLS warning throttle from leaking across tests."""
    hmc_client._reported_tls_verification_disabled.clear()
    yield
    hmc_client._reported_tls_verification_disabled.clear()


def _restore_fastmcp_logger() -> None:
    handlers, level, propagate = _PRISTINE_FASTMCP
    _FASTMCP_LOGGER.handlers[:] = handlers
    _FASTMCP_LOGGER.setLevel(level)
    _FASTMCP_LOGGER.propagate = propagate


def _restore_third_party_loggers() -> None:
    for logger, (handlers, level, propagate) in zip(
        _THIRD_PARTY_LOGGERS, _PRISTINE_THIRD_PARTY
    ):
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate


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

    Since ADR 0051 it does the same for the ``fastmcp`` logger, whose handlers
    ``_serve_application`` also replaces; since #330's amendment it does it for the
    three further loggers that install binds — ``uvicorn``, ``uvicorn.access`` and
    ``mcp`` — which add level and propagation to what must be restored. Those
    loggers need their pristine state captured at import rather than reset to empty,
    so the snapshots live at module level and this fixture only applies them — at
    setup as well as teardown, for the reason above.
    """
    _restore_fastmcp_logger()
    _restore_third_party_loggers()
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
        _restore_fastmcp_logger()
        _restore_third_party_loggers()
        # ADR 0043 made delivery asynchronous, so a record emitted here can still
        # be in flight after the test returns. Settle the sink and clear anything
        # it is owed, or one test's records land in another test's captured output
        # — or, when the writer is only scheduled after the test's `sys.stderr`
        # redirection is undone, on the console. Settling it against a throwaway
        # stream is what keeps that late write somewhere harmless; a test that
        # cares about the content has already waited for it with an explicit
        # `flush`/`drain`.
        stderr, sys.stderr = sys.stderr, io.StringIO()
        try:
            audit._SINK.drain(audit._DRAIN_TIMEOUT)
        finally:
            sys.stderr = stderr
        with audit._SINK._state:
            audit._SINK._dropped = 0


@dataclass
class FullPipe:
    """A pipe whose buffer is full and whose write end blocks. See ADR 0043.

    `stream` is what a test puts on `sys.stderr`; `read_fd` is the end nothing is
    reading, which is what makes the next write block.
    """

    stream: object
    read_fd: int
    capacity: int

    def read_available(self) -> bytes:
        """Drain whatever is buffered, freeing the writer. Never blocks.

        A bare ``os.read`` would block once the buffer is empty, which is exactly
        the state this leaves behind — so it would hang the *test* on the second
        call instead of the server on the first.
        """
        chunks: list[bytes] = []
        while select.select([self.read_fd], [], [], 0.1)[0]:
            chunk = os.read(self.read_fd, 1 << 16)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)


@pytest.fixture
def full_stderr_pipe():
    """A genuinely full, genuinely blocking pipe — not a mock of one.

    `O_NONBLOCK` is a property of the open file description rather than of the
    descriptor, so setting it, writing until `BlockingIOError`, and clearing it
    again leaves the buffer full *and* the descriptor blocking. That is the exact
    condition issue #269 describes: `write()` neither returns nor raises.

    Teardown closes the read end first, which wakes a blocked writer with `EPIPE`
    — an `OSError` the sink already treats as a drop — so the daemon thread is
    never left parked on a descriptor the next test cannot free.
    """
    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)
    capacity = 0
    try:
        while True:
            capacity += os.write(write_fd, b"x" * 4096)
    except BlockingIOError:
        pass
    os.set_blocking(write_fd, True)
    stream = os.fdopen(write_fd, "w")
    try:
        yield FullPipe(stream=stream, read_fd=read_fd, capacity=capacity)
    finally:
        os.close(read_fd)
        audit._SINK.drain(audit._DRAIN_TIMEOUT)
        try:
            stream.close()
        except OSError:
            pass


BASE = "https://hmc.test:443"

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
        "verify_ssl": True,
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
        if call.request.method != "GET" and call.request.url.path not in _SESSION_PATHS
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
