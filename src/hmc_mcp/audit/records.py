"""The authorization audit record: its vocabulary and rendering.

One record per authorization decision, emitted from ``dispatch_scope.authorize`` —
the only place in the package that reaches one. See
docs/adr/0040-authorization-audit-events.md.

This module imports only the sink's :func:`emit` function from ``hmc_mcp``.
``target_scope`` imports :data:`Reason` and :data:`State` from here, so every
other value arrives as a primitive. The single exception is
:data:`ATTRIBUTION_ENV`, read from
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

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final, Literal, get_args

from .sink import emit

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

Decision = Literal["allow", "deny"]

#: Derived, as ``REASONS`` and ``EVENTS`` are. Lifted out of
#: :func:`record_authorization`'s own signature so this vocabulary is something a
#: checker and a test can consult, rather than one that has to be read back off the
#: signature to be compared with anything (#518).
DECISIONS: frozenset[str] = frozenset(get_args(Decision))

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

Event = Literal[
    "authorization",
    "install-attempted",
    "install-submitted",
    "ownership-denied",
    "ownership-override",
    "power-ownership-guard",
    "records-dropped",
    "tls-verification-disabled",
]

#: Derived, as REASONS is, so the vocabulary is something a checker and a test can
#: consult rather than a claim in a docstring. Every builder binds its literal
#: through ``Event``, so a typo in any of them is a type error.
#:
#: ``records-dropped`` is the sink's own event, not a decision: see
#: :class:`audit_sink._StderrSink` and
#: docs/adr/0043-non-blocking-stderr-diagnostics.md.
EVENTS: frozenset[str] = frozenset(get_args(Event))

#: What a caller supplied for one selector: a value, nothing, or something the
#: boundary declines to read. ``reason`` names the *decision*; these name the *input*,
#: which is why there is no ``connection-selector-unreadable`` reason code.
State = Literal["present", "absent", "unreadable"]

#: Which ADR 0011 guard entry point refused, on the ``ownership-denied`` record. Two
#: members rather than the MCP tool or API function name: threading the caller's name
#: through would move the frozen public signature digest across two exports and fourteen
#: call sites, and per-tool granularity is a later issue's (ADR 0100).
OwnershipOperation = Literal["lpar-mutation", "lpar-decommission-snapshot"]

#: Derived, as :data:`REASONS` and :data:`EVENTS` are, so
#: ``tests/test_authorization_audit_doc.py`` holds the document's field row to it in both
#: directions rather than to a set spelled a second time.
OWNERSHIP_OPERATIONS: frozenset[str] = frozenset(get_args(OwnershipOperation))

#: Which of the guard's two rules refused. Named ``denial`` on the record and not
#: ``reason``, which already names ADR 0040's access-policy vocabulary: one key cannot
#: name two boundaries' vocabularies and still tell a reader which it is reading.
OwnershipDenial = Literal["malformed-token", "foreign-owner"]

#: Derived, as :data:`OWNERSHIP_OPERATIONS` is, and held to the document the same way.
OWNERSHIP_DENIALS: frozenset[str] = frozenset(get_args(OwnershipDenial))

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


def _env_var_value(name: str) -> str | None:
    """*name*'s value from the environment, matched the way ``HMCConfig`` matches it.

    :data:`ATTRIBUTION_ENV` is also an ``HMCConfig`` field, and ``HMCConfig`` leaves
    pydantic-settings' ``case_sensitive`` at its ``False`` default, so a
    ``hmc_agent_id=…`` export reaches ``config.agent_id``: it stamps every LPAR the
    process creates with the ADR 0011 ownership token and rides out in the
    ``X-Audit-Memento`` header. Reading it exact-case here left the authorization
    record unattributed for that same process, so the audit stream said nobody
    acted while the partitions said somebody did (#543).

    This duplicates ``config.env_var_value`` rather than calling it: no module on
    the decision path may name :data:`ATTRIBUTION_ENV`, which is what the module
    docstring's no-import rule protects. A second copy of one rule only stays
    honest if something compares the two, so
    ``test_the_audit_env_fold_agrees_with_the_configs`` pins them against each
    other — a change to either that the other does not follow reddens there.

    Both halves of the rule matter. The fold is ``str.lower()``, matching
    pydantic-settings' own, which folds down; folding up would match names the
    loader never reads and miss names it does. And when several casings are set
    the **last** one in ``os.environ`` order wins, because that is the one its
    case-blind fold leaves on the field — preferring the canonical spelling would
    put an empty ``HMC_AGENT_ID`` in the record while ``hmc_agent_id`` stamped
    the partitions, the same divergence in the other direction.

    Keys are snapshotted and each read with a default rather than iterated as
    items: ``os.environ.items()`` re-indexes every key after ``__iter__`` has
    snapshotted them, so a key an embedding host deletes from another thread in
    between raises ``KeyError``. This runs inside the record builder that
    ``dispatch_scope.authorize`` calls ahead of a denial, where the
    ``os.environ.get`` it replaced could not raise, and neither may it.
    """
    wanted = name.lower()
    found: str | None = None
    for key in list(os.environ):
        if key.lower() == wanted:
            found = os.environ.get(key, found)
    return found


def _attribution(claim: Any, source: str) -> dict[str, Any]:
    """Unverified attribution, with its source named.

    One builder, two sources: the authorization record reads the environment while
    the ownership override reads the effective ``agent_id`` the ADR 0011 check
    compared. Naming them separately is the honest form; ``verified`` is constant
    ``False`` because neither is authenticated, and it is what lets an operator
    filter without knowing this ADR exists.
    """
    return {"claim": _value(claim), "source": source, "verified": False}


def record_authorization(
    *,
    policy: str,
    tool: str,
    effect: str,
    decision: Decision,
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
                _env_var_value(ATTRIBUTION_ENV), f"environment:{ATTRIBUTION_ENV}"
            ),
        }

    emit(_ALLOW_LEVEL if decision == "allow" else _DENY_LEVEL, build)


def record_ownership_override(
    *, system: str, lpar: str, host: str, agent_id: str
) -> None:
    """Emit one record for an approved ADR 0011 LPAR ownership override.

    Carries no ``policy``, ``decision``, ``reason`` or ``targets``, and not as nulls:
    none of them exists for this event, and rendering them empty would suggest an
    access-policy decision was taken when this is an ownership check on a token
    parsed from an LPAR description (#268). It carries no ``connection`` either: a
    hostname is not an access-policy connection selector, and this path also runs
    from the CLI and the Python API where no policy connection exists (ADR 0040).
    The HMC identity travels as its own ``host`` field instead (#271). Like every
    caller-supplied field it passes through ``_value``, so an unset
    ``HMCConfig.host`` — whose default is ``""`` — renders as an empty string.

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
            "host": _value(host),
            "attribution": _attribution(agent_id, "config:agent_id"),
        }

    emit(_DENY_LEVEL, build)

def record_ownership_denied(
    *,
    operation: OwnershipOperation,
    denial: OwnershipDenial,
    system: str,
    lpar: str,
    owner: str | None,
    host: str,
    agent_id: str,
) -> None:
    """Emit one record for a refused ADR 0011 LPAR ownership check.

    #467, ADR 0100. A separate event rather than a ``decision`` arm on
    :func:`record_ownership_override`: the two answer different questions and carry
    different facts, and folding them together would silently change what an
    existing ``event == "ownership-override"`` filter counts — a query for approved
    bypasses would start matching refusals.

    *operation* names which guard entry point refused and *denial* which of its two
    rules did. *owner* is the owner token parsed out of the LPAR description: the
    claimed owner on ``foreign-owner``, and ``None`` on ``malformed-token``, where
    nothing parsed and the record carries the actor alone. It is HMC-supplied text,
    so like every caller-supplied field it passes through :func:`_value`.

    Carries no ``policy``, ``decision``, ``reason``, ``targets`` or ``connection``,
    and not as nulls, for the reason :func:`record_ownership_override` gives: an
    ownership check on a token parsed from an LPAR description is not an
    access-policy decision, and this path also runs from the CLI and the Python API
    where no policy connection exists.

    Always ``WARNING``, matching the override, so a CLI or API process that never
    installed the sink still sees it through ``logging.lastResort`` — which drops
    anything below that level. It also means ``--audit-level WARNING``, the setting
    that drops permits, keeps denials.
    """

    def build() -> dict[str, Any]:
        event: Event = "ownership-denied"
        return {
            "time": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "operation": operation,
            "denial": denial,
            "system": _value(system),
            "lpar": _value(lpar),
            "owner": _value(owner),
            "host": _value(host),
            "attribution": _attribution(agent_id, "config:agent_id"),
        }

    emit(_DENY_LEVEL, build)


def record_install_attempted(
    *, system: str, partition: str, log_path: str, host: str, agent_id: str
) -> None:
    """Emit one record for an ``installios`` submission about to be made.

    #469, ADR 0102. Named for the attempt and emitted *before* the submission,
    because a submission that raises is the ambiguous case: the caller cannot tell
    a resolution failure from a failed submit, so a record written afterwards
    would be missing exactly when it is most needed. A record here therefore says
    an irreversible detached install was attempted against these disks, not that
    one started.

    *log_path* is the HMC-side path the install writes to. It is keyed on the
    partition name alone and truncated by the next submission, so it is not unique
    per managed system — which is why the record carries *system* and *host*
    beside it, and why an operator correlating installs needs all three.

    This path has no HMC job to poll and no ADR 0011 ownership guard, so no
    :func:`record_ownership_denied`. A served deployment does write a
    :func:`record_authorization` permit for the tool call, but that one names the
    tool and none of the three values above; a ``hmc_mcp.api`` consumer gets no
    record at all without this one.

    Always ``WARNING``, matching :func:`record_ownership_denied`, and for the same
    reason: a process that never installed the sink has no handler here and no
    propagation, so ``logging.lastResort`` — which drops anything below that level —
    is what puts the line on stderr.
    """

    def build() -> dict[str, Any]:
        event: Event = "install-attempted"
        return {
            "time": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "system": _value(system),
            "partition": _value(partition),
            "log_path": _value(log_path),
            "host": _value(host),
            "attribution": _attribution(agent_id, "config:agent_id"),
        }

    emit(_DENY_LEVEL, build)


def record_install_submitted(
    *,
    system: str,
    partition: str,
    pid: int,
    log_path: str,
    host: str,
    agent_id: str,
) -> None:
    """Emit the remote PID after ``installios`` submission succeeds.

    The target and attribution fields match :func:`record_install_attempted`,
    allowing an operator to correlate the outcome without access to the MCP
    result. A failed or ambiguous submit emits no outcome record.
    """

    def build() -> dict[str, Any]:
        event: Event = "install-submitted"
        return {
            "time": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "system": _value(system),
            "partition": _value(partition),
            "pid": pid,
            "log_path": _value(log_path),
            "host": _value(host),
            "attribution": _attribution(agent_id, "config:agent_id"),
        }

    emit(_DENY_LEVEL, build)


def record_tls_verification_disabled(*, host: str, source: str) -> None:
    """Emit one record for a client constructed with TLS verification off.

    #379. The ``warnings.warn`` in ``HMCClient.logon`` stays: it is the right
    channel for a CLI user, but under the default warning filter it renders once
    per process per location and never reaches the structured stream an operator
    monitors. This is the durable counterpart — one record per client
    construction (not per request, which would flood the sink; not per process,
    which would miss a later client built with different settings), so the audit
    stream can answer "were credentials ever sent over an unverified channel,
    and to which HMC".

    *source* names where the effective setting came from, because an operator
    needs to know which knob to turn to fix it. Its closed vocabulary is
    ``hmc_mcp.client.core.VerifySSLSource``, at the only place that produces a value;
    this parameter stays a plain ``str`` because this module imports nothing from
    the package (#504 — spelling the values here left them to drift silently).
    It carries no credential, no session token and no request body; like every
    caller-supplied field, *host* and *source* pass through ``_value``, so they
    are bounded and an unset ``HMCConfig.host`` renders as an empty string.

    Always ``WARNING``, matching :func:`record_ownership_override`, so a CLI user
    whose process never installed the sink still sees it through
    ``logging.lastResort``.
    """

    def build() -> dict[str, Any]:
        event: Event = "tls-verification-disabled"
        return {
            "time": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "host": _value(host),
            "source": _value(source),
        }

    emit(_DENY_LEVEL, build)


def record_power_ownership_guard(
    *,
    connection: str,
    authorize_power_operations: bool | None,
    source: str,
    detail: str | None,
) -> None:
    """Emit the effective ownership guard for one startup connection."""

    def build() -> dict[str, Any]:
        event: Event = "power-ownership-guard"
        return {
            "time": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "connection": _value(connection),
            "authorize_power_operations": authorize_power_operations,
            "source": _value(source),
            "detail": _value(detail),
        }

    emit(_DENY_LEVEL, build)
