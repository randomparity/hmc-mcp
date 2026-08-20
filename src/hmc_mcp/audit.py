"""The authorization audit record: its vocabulary, its rendering, and its sink.

One record per authorization decision, emitted from ``dispatch_scope.authorize`` —
the only place in the package that reaches one. See
docs/adr/0040-authorization-audit-events.md.

This module imports nothing from ``hmc_mcp``. ``target_scope`` imports :data:`Reason`
and :data:`State` from here, so any import back would be a cycle, and every value
arrives as a primitive. The single exception is :data:`ATTRIBUTION_ENV`, read from
``os.environ`` here rather than passed in: no module on the decision path may name
that variable (which is what makes "attribution cannot change an outcome" an
invariant rather than a sample), and reading it through ``HMCConfig`` would apply
validators that reject exactly the malformed values worth recording.

The record's grammar is one physical line of ASCII JSON. ``ensure_ascii=True`` is the
control, not a default worth keeping: JSON encoding already escapes newlines and C0
control characters, and this is what additionally stops a bidirectional override or
U+2028 in an LPAR name passing through raw to a line-oriented reader.

Stability, so a later change knows what it may not do: a field may be added, never
renamed, removed, or retyped; a reason code may be added, never repurposed; a
consumer ignores what it does not know. There is no version field — a written rule
settles it, and a second mechanism would have to be kept in agreement with it.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final, Literal, get_args

#: The reserved logger. Only this module resolves it, which is what makes "the
#: message is the record" a property of the logger rather than only of the emitter.
#: The reservation is checked inside this package only — a dependency or the
#: operator's own code can still log here, so a consumer should skip a line that
#: does not parse rather than fail on it.
AUDIT_LOGGER_NAME: Final = "hmc_mcp.audit"

# Set where the logger is *defined*, not only inside `install_audit_sink` — which
# `_serve_application` alone calls, so an embedder composing an application with
# `create_mcp` and serving it over stdio would otherwise keep propagating. Under
# that transport a `StreamHandler(sys.stdout)` anywhere above this logger puts an
# audit record into the JSON-RPC stream on every denial, which is the #221 defect
# class in a module this change introduces (#272).
#
# This is narrower than the "install the sink at import" that ADR 0040 rejects:
# it attaches no handler, writes nothing, and touches one flag on one logger this
# package reserves for itself. And it costs an unconfigured caller nothing —
# `Logger.callHandlers` consults `logging.lastResort` whenever the walk finds
# *zero* handlers, which `propagate` does not affect, so a CLI user still sees an
# ownership override on stderr exactly as before.
logging.getLogger(AUDIT_LOGGER_NAME).propagate = False

#: Every caller-supplied value is truncated to this, with no marker. In band a
#: marker is forgeable; out of band it is a field on every record for something a
#: reader can already measure, since a truncated value is exactly this long.
MAX_VALUE_LENGTH: Final = 128

ATTRIBUTION_ENV: Final = "HMC_AGENT_ID"

#: What ``connection_scope.selected_connection``'s ``None`` renders as — the
#: environment/default connection, in the access policy's own vocabulary.
DEFAULT_RENDERING: Final = "<default>"

#: What its ``UNRESOLVED`` sentinel renders as. Both of these share a string space
#: with legal profile keys, so a profile literally so named is indistinguishable
#: from the sentinel in this field. Narrow enough to document rather than escape.
UNRESOLVED_RENDERING: Final = "<unresolved>"

Reason = Literal[
    "permitted",
    "configuration-unreadable",
    "connection-not-granted",
    "target-selector-unreadable",
    "target-unboundable",
    "target-selector-absent",
    "target-not-granted",
]

#: The closed vocabulary, derived rather than restated — as ``tool_registry`` derives
#: ``EFFECTS`` from ``Effect``.
REASONS: frozenset[str] = frozenset(get_args(Reason))

Event = Literal["authorization", "ownership-override", "records-dropped"]

#: Derived, as REASONS is, so the vocabulary is something a checker and a test can
#: consult rather than a claim in a docstring. Every builder binds its literal
#: through ``Event``, so a typo in any of them is a type error.
#:
#: ``records-dropped`` is the sink's own event, not a decision: see
#: :class:`_StderrSink` and docs/adr/0043-non-blocking-stderr-diagnostics.md.
EVENTS: frozenset[str] = frozenset(get_args(Event))

#: What a caller supplied for one selector: a value, nothing, or something the
#: boundary declines to read. ``reason`` names the *decision*; these name the *input*,
#: which is why there is no ``connection-selector-unreadable`` reason code.
State = Literal["present", "absent", "unreadable"]

_DENY_LEVEL: Final = logging.WARNING
_ALLOW_LEVEL: Final = logging.INFO


@dataclass(frozen=True)
class AuditTarget:
    """One declared target selector, as the record carries it.

    A transport for what ``target_scope.selected_targets`` extracted; the value is
    truncated here rather than by the caller, so bounding has one owner.
    """

    kind: str
    argument: str
    state: State
    value: str | None


def resolved_connection(value: str | None) -> str:
    """Render what ``selected_connection`` returned, in policy terms.

    ``None`` is the environment/default connection and ``""`` is its ``UNRESOLVED``
    sentinel; anything else is a profile key, returned unchanged.
    """
    if value is None:
        return DEFAULT_RENDERING
    if value == "":
        return UNRESOLVED_RENDERING
    return value


def _value(raw: Any) -> str | None:
    """One caller-supplied value, truncated, or ``None`` if there is none to render.

    A non-``str`` renders ``None`` rather than its ``repr()``, for the reason
    ``target_scope.target_denial`` already declines to render one: an arbitrary
    object's repr is not the caller's token in any useful sense and can carry
    anything.
    """
    if isinstance(raw, str):
        return raw[:MAX_VALUE_LENGTH]
    return None


def _connection(token: Any, resolved: str | None) -> dict[str, Any]:
    """The ``connection`` object, mirroring ``selected_connection``'s three arms."""
    if token is None or token == "":
        state: State = "absent"
    elif isinstance(token, str):
        state = "present"
    else:
        state = "unreadable"
    # Rendered only in the "present" state, so "the selector is the caller's own
    # string, or null otherwise" is true of the field rather than of two of its
    # three states: `_value("")` is `""`, not None, and an empty token is absent.
    return {
        "state": state,
        "selector": _value(token) if state == "present" else None,
        "resolved": resolved,
    }


def _attribution(claim: Any, source: str) -> dict[str, Any]:
    """Unverified attribution, with its source named.

    One builder, two sources: the authorization record reads the environment while
    the ownership override reads the effective ``agent_id`` the ADR 0011 check
    compared. Naming them separately is the honest form; ``verified`` is constant
    ``False`` because neither is authenticated, and it is what lets an operator
    filter without knowing this ADR exists.
    """
    return {"claim": _value(claim), "source": source, "verified": False}


def _emit(level: int, build: Callable[[], dict[str, Any]]) -> None:
    """Render and log one record, or drop it. Never raises.

    The builder is taken rather than the built payload so the guard covers both
    halves of ADR 0040's rule: a failure to *build* a record drops it exactly as a
    failure to write one does. The blanket catch is deliberate and is the whole
    point — this runs inside ``dispatch_scope.authorize``, ahead of the denial and
    ahead of the handler, so anything escaping here would fail an authorized call
    and replace ADR 0038's and ADR 0039's client-facing errors with something else.
    A diagnostic must not abort a call, for the same reason #221 established that
    one must not abort a start.
    """
    try:
        message = json.dumps(build(), ensure_ascii=True)
        logging.getLogger(AUDIT_LOGGER_NAME).log(level, message)
    except Exception:  # noqa: BLE001 - see above; totality is the contract
        pass


def record_authorization(
    *,
    policy: str,
    tool: str,
    effect: str,
    decision: Literal["allow", "deny"],
    reason: Reason,
    token: Any,
    resolved: str | None,
    targets: tuple[AuditTarget, ...] | None,
) -> None:
    """Emit one record for one dispatch-boundary decision.

    *token* is the caller's raw connection argument and *resolved* is
    :func:`resolved_connection`'s rendering of what it resolved to — or ``None``
    when nothing was resolved at all, which is the ``configuration-unreadable`` case
    and the only one where *targets* is also ``None``. ``None`` there rather than
    ``"<unresolved>"``: a token that could not be checked has not been found absent.
    """

    def build() -> dict[str, Any]:
        event: Event = "authorization"
        return {
            "time": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "policy": policy,
            "tool": tool,
            "effect": effect,
            "decision": decision,
            "reason": reason,
            "connection": _connection(token, resolved),
            "targets": None
            if targets is None
            else [
                {
                    "kind": target.kind,
                    "argument": target.argument,
                    "state": target.state,
                    "value": _value(target.value),
                }
                for target in targets
            ],
            "attribution": _attribution(
                os.environ.get(ATTRIBUTION_ENV), f"environment:{ATTRIBUTION_ENV}"
            ),
        }

    _emit(_ALLOW_LEVEL if decision == "allow" else _DENY_LEVEL, build)


def record_ownership_override(*, system: str, lpar: str, agent_id: str) -> None:
    """Emit one record for an approved ADR 0011 LPAR ownership override.

    Carries no ``policy``, ``decision``, ``reason`` or ``targets``, and not as nulls:
    none of them exists for this event, and rendering them empty would suggest an
    access-policy decision was taken when this is an ownership check on a token
    parsed from an LPAR description. It carries no ``connection`` either, though the
    HMC identity does exist at the emission point — see ADR 0040 and #271.

    Always ``WARNING``, which is what it was before convergence, so a CLI user whose
    process never installed the sink still sees it through ``logging.lastResort``.
    """

    def build() -> dict[str, Any]:
        event: Event = "ownership-override"
        return {
            "time": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "system": _value(system),
            "lpar": _value(lpar),
            "attribution": _attribution(agent_id, "config:agent_id"),
        }

    _emit(_DENY_LEVEL, build)


#: How many lines the sink will hold for a destination that is not reading. About
#: 0.5 MiB at the measured record sizes, and roughly six times the 64 KiB pipe
#: buffer it stands in for. Not configurable; #270 owns that gap.
_QUEUE_CAPACITY: Final = 1024

#: The whole of what shutdown waits on a destination nobody is draining. A
#: destination that *is* being read finishes in microseconds.
_DRAIN_TIMEOUT: Final = 2.0

_SENTINEL: Final = object()


class _StderrSink:
    """Every stderr write this package makes, off the thread that asked for it.

    A synchronous ``write()`` to a pipe nobody is draining blocks. It raises
    nothing, so no guard fires, and under ADR 0040 that write sits inside
    ``dispatch_scope.authorize`` — ahead of the denial and ahead of the handler — so
    the server stops answering. One bounded queue and one daemon writer move the
    only blocking call in the chain onto a thread whose progress nothing waits on.

    A full queue **drops** the line and counts it, which is the trade
    ADR 0043 takes deliberately: a droppable audit trail that keeps serving, over a
    complete one that stops. Nothing is lost silently — the count is written into
    the stream as a ``records-dropped`` record ahead of the next line that lands.

    ``sys.stderr`` is resolved per write rather than bound at construction, an
    absent stream is skipped, and ``OSError`` and ``ValueError`` are caught: the
    same three guards ADR 0040 gave the handler, for the same three reasons —
    CPython sets ``sys.stderr`` to ``None`` when fd 2 is closed at interpreter
    start, a broken stream raises ``OSError``, and a closed one raises
    ``ValueError``. What changes is that each of them now counts a drop.

    Each line is written with its newline in **one** call. ``logging``'s lock
    serialises audit records against each other but against nothing else on this
    stream — FastMCP's traceback panel writes to it too — so a second call could let
    another writer land between a record and its terminator.
    """

    def __init__(self, capacity: int, drain_timeout: float) -> None:
        # One slot past the capacity, reserved for the stop sentinel and refused to
        # `submit`. Without it, stopping a full queue would have to evict a queued
        # line to make room — losing, at shutdown, records a destination that *is*
        # being read would otherwise have received.
        self._capacity = capacity
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=capacity + 1)
        self._drain_timeout = drain_timeout
        self._state = threading.Condition()
        self._pending = 0
        self._dropped = 0
        self._closed = False
        self._writer: threading.Thread | None = None

    def submit(self, line: str) -> None:
        """Queue one already-terminated line. Never blocks, never raises."""
        with self._state:
            if self._closed:
                self._dropped += 1
                return
            if self._queue.qsize() >= self._capacity:
                self._dropped += 1
                return
            self._start_writer()
            self._queue.put_nowait(line)
            self._pending += 1

    def drain(self, timeout: float) -> bool:
        """Wait until every submitted line has been written or dropped.

        Bounded by *timeout* and by nothing else, which is what makes it safe to
        call from ``logging.shutdown``'s ``flush()`` on a destination that has
        stopped reading. Returns whether the queue emptied.
        """
        with self._state:
            if self._closed:
                return self._pending == 0
            return self._state.wait_for(lambda: self._pending == 0, timeout)

    def close(self) -> None:
        """Stop the writer, giving queued lines ``drain_timeout`` to land.

        The sentinel goes *behind* whatever is already queued and into a slot
        reserved for it, so nothing is evicted to make room and a destination being
        read drains completely. One that is not cannot be drained by any mechanism,
        so the join times out, the daemon thread is abandoned, and the process exits
        rather than hanging. Idempotent.

        Anything submitted after this is counted and can never be reported: the
        writer is gone, so no later write can carry the marker.

        Calling it again re-joins a writer the first call had to abandon, rather
        than returning while that thread is still parked on the descriptor — an
        abandoned writer wakes up eventually and writes wherever ``sys.stderr``
        points by then.
        """
        with self._state:
            writer, already_closing = self._writer, self._closed
            self._closed = True
            if writer is None:
                return
            if not already_closing:
                self._queue.put_nowait(_SENTINEL)
        writer.join(timeout=self._drain_timeout)
        with self._state:
            if not writer.is_alive():
                self._writer = None

    def _start_writer(self) -> None:
        """Begin draining. Caller holds ``_state``; importing must start nothing."""
        if self._writer is None:
            self._writer = threading.Thread(
                target=self._run, name="hmc-mcp-audit-stderr", daemon=True
            )
            self._writer.start()

    def _run(self) -> None:
        """Drain the queue onto stderr until the sentinel arrives."""
        while True:
            item = self._queue.get()
            self._report_drops()
            if item is _SENTINEL:
                return
            landed = self._write(item)
            with self._state:
                if not landed:
                    self._dropped += 1
                self._pending -= 1
                self._state.notify_all()

    def _report_drops(self) -> None:
        """Write the drop marker, if anything is owed, ahead of the next line."""
        with self._state:
            owed, self._dropped = self._dropped, 0
        if not owed:
            return
        if not self._write(_drop_marker(owed)):
            with self._state:
                self._dropped += owed

    def _write(self, line: str) -> bool:
        """Put one line on stderr. Returns whether it landed."""
        stream = sys.stderr
        if stream is None:
            return False
        try:
            stream.write(line)
            stream.flush()
        except (OSError, ValueError):
            return False
        except Exception:  # noqa: BLE001 - a dead writer stops the trail for good
            # Anything else the destination raises is still a drop rather than the
            # end of the audit trail: this thread is the only one draining, so
            # letting it die would silently strand every later record.
            return False
        return True


def _drop_marker(count: int) -> str:
    """The record that says lines are missing above this point in the stream."""
    event: Event = "records-dropped"
    return (
        json.dumps(
            {
                "time": datetime.now(timezone.utc).isoformat(),
                "event": event,
                "count": count,
            },
            ensure_ascii=True,
        )
        + "\n"
    )


_SINK: Final = _StderrSink(_QUEUE_CAPACITY, _DRAIN_TIMEOUT)
atexit.register(_SINK.close)


def write_diagnostic(line: str) -> None:
    """Queue one non-record diagnostic line for stderr. Never blocks, never raises.

    ``server._warn``'s entry point. Its lines are prose rather than records, and
    they share this sink rather than a second one because a second one would be a
    second mechanism with its own failure mode on the same descriptor — the
    condition #269 names first is precisely a start that hangs on such a write.
    """
    _SINK.submit(line + "\n")


class _AuditHandler(logging.Handler):
    """Hand one record per line to the sink, which writes it or counts it lost.

    No ``Formatter`` is installed and the newline is explicit: a custom handler
    inherits no ``StreamHandler.terminator``, so the message plus its terminator is
    what reaches the stream, in one write, as ADR 0040 requires.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _SINK.submit(record.getMessage() + "\n")
        except Exception:  # noqa: BLE001 - the stdlib handler contract
            # Every stdlib handler wraps its body this way, and that is what makes
            # a logging call safe to place anywhere in a program. This module's own
            # records cannot reach here — `_emit` already catches around both the
            # build and the log call — but the operator documentation names
            # `hmc_mcp.audit` as an attachment point, so a foreign writer's odd
            # record must not raise back into whatever called it.
            self.handleError(record)

    def flush(self) -> None:
        """Wait, boundedly, for what has been submitted to reach the stream.

        ``logging.shutdown`` calls this on every handler at interpreter exit, and
        the bound is what keeps that from becoming the hang this sink removes.
        """
        _SINK.drain(_DRAIN_TIMEOUT)


def install_audit_sink() -> None:
    """Attach the stderr sink the serve paths use. Idempotent.

    Called from ``server._serve_application``, so both transports get it and neither
    ``create_mcp`` nor any library or CLI caller mutates global logging state by
    composing an application.

    One rule for the last two clauses — what the operator configured wins, what they
    left unconfigured gets a default. ``propagate`` is the exception and is set
    unconditionally: propagation to an unknown ancestor handler is the stdio hazard
    itself, since one ``StreamHandler(sys.stdout)`` on the root logger would put a
    record into the JSON-RPC stream on every authorized call. That closes the
    in-process route only; a launcher merging fd 2 into fd 1 is outside any choice
    available here, and a handler the operator attaches *here* is theirs to keep off
    stdout — this defers to it without inspecting it.
    """
    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    logger.propagate = False
    if not logger.handlers:
        logger.addHandler(_AuditHandler())
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)
