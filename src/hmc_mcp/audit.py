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

import json
import logging
import os
import sys
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

Event = Literal["authorization", "ownership-override"]

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
        return {
            "time": datetime.now(timezone.utc).isoformat(),
            "event": "authorization",
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
        return {
            "time": datetime.now(timezone.utc).isoformat(),
            "event": "ownership-override",
            "system": _value(system),
            "lpar": _value(lpar),
            "attribution": _attribution(agent_id, "config:agent_id"),
        }

    _emit(_DENY_LEVEL, build)


class _AuditHandler(logging.Handler):
    """Write one record per line to stderr, or drop it.

    ``sys.stderr`` is resolved at emit time rather than bound at install time, an
    absent stream returns early, and ``OSError`` and ``ValueError`` are caught: the
    same three guards ``server._warn`` applies, for the same three reasons — CPython
    sets ``sys.stderr`` to ``None`` when fd 2 is closed at interpreter start, a
    broken stream raises ``OSError``, and a closed one raises ``ValueError``.

    The message is written with its newline in **one** call and no ``Formatter`` is
    installed. A custom handler inherits no ``StreamHandler.terminator``, so the
    newline is explicit; and ``logging``'s lock serialises audit records against each
    other but against nothing else on this stream — FastMCP's traceback panel and
    ``server._warn`` both write to it — so a second call could let another writer
    land between a record and its terminator.
    """

    def emit(self, record: logging.LogRecord) -> None:
        stream = sys.stderr
        if stream is None:
            return
        try:
            stream.write(record.getMessage() + "\n")
            stream.flush()
        except (OSError, ValueError):
            pass
        except Exception:  # noqa: BLE001 - the stdlib handler contract
            # Every stdlib handler wraps its body this way, and that is what makes
            # a logging call safe to place anywhere in a program. This module's own
            # records cannot reach here — `_emit` already catches around both the
            # build and the log call — but the operator documentation names
            # `hmc_mcp.audit` as an attachment point, so a foreign writer's odd
            # record must not raise back into whatever called it.
            self.handleError(record)


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
