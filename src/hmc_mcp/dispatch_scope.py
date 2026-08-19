"""The dispatch-boundary authorization decision: one grant, every dimension.

ADR 0036 fixed that grants combine disjunctively and a grant is evaluated
conjunctively — a call is permitted only when a *single* grant covers its tool,
its connection, and its targets together. ADR 0038 wrote that loop inside the
connection dimension and left a comment on it, because with one condition a union
across grants and a conjunction inside one give the same answer. ADR 0039 added
the second condition, which is what finally makes them differ, so the loop stopped
being a comment and became this module.

That is the whole of it. ``connection_scope`` and ``target_scope`` each answer one
dimension for one grant and are given neither the policy nor a grant sequence, so
neither can combine one grant's connection with another grant's targets. This file
is the only place in the package that iterates ``AccessPolicy.grants_for``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from . import audit
from .access_policy import AccessPolicy
from .connection_scope import (
    ConnectionScopeError,
    connection_denial,
    connection_permitted,
    selected_connection,
)
from .target_scope import (
    audit_state,
    denial_reason,
    selected_targets,
    target_denial,
    targets_permitted,
)
from .tool_registry import Authorize, ToolSecurity


def dispatch_authorizer(policy: AccessPolicy) -> Authorize:
    """An authorizer denying any call no single grant of *policy* covers whole.

    The returned callable is what ``server._gates`` hands to every registration
    site. It closes over the frozen policy and holds no other state.
    """

    def authorize(
        name: str, security: ToolSecurity, arguments: Mapping[str, Any]
    ) -> None:
        argument = security.connection_argument
        if argument is None:
            # No connection is selected, so there is nothing for this boundary to
            # scope — and the target dimension cannot reach such a tool either,
            # since `authorized` declines to wrap it. Withholding it is the tool
            # dimension's job (ADR 0037). An authorizer must still be safe to
            # call on any tool. No record either: no decision was reached, and a
            # per-call record for a per-process decision would record nothing.
            return
        # Indexed, not `.get`: `authorized` applies the handler's defaults and
        # `validate_security` guarantees the parameter exists, so an absent key is
        # a malformed call, and treating it as an omitted argument would silently
        # make it the default connection. A KeyError here is a registration-path
        # defect rather than an authorization outcome, so it too goes unrecorded.
        token = arguments[argument]

        def record(
            decision: Literal["allow", "deny"],
            reason: audit.Reason,
            resolved: str | None,
            targets: tuple[audit.AuditTarget, ...] | None,
        ) -> None:
            """One emission point, closed over what every record shares.

            Typed to the closed vocabularies rather than to ``str``: this is the
            only place they are enforced, and widening here would erase the check
            at exactly the four call sites that can be checked exactly.

            Guarded here as well as inside ``audit._emit``, and not redundantly:
            ``resolved`` and ``targets`` are *built* by this module, above and
            outside that guard, so without this one ADR 0040's "building one and
            writing one are both total" would hold for the payload ``audit``
            assembles and not for the part assembled here. Two boundaries, two
            guards; ``audit``'s still protects its other caller.
            """
            try:
                _record(decision, reason, resolved, targets)
            except Exception:  # noqa: BLE001 - a diagnostic must not fail a call
                pass

        def _record(
            decision: Literal["allow", "deny"],
            reason: audit.Reason,
            resolved: str | None,
            targets: tuple[audit.AuditTarget, ...] | None,
        ) -> None:
            audit.record_authorization(
                policy=policy.name,
                tool=name,
                effect=security.effect,
                decision=decision,
                reason=reason,
                token=token,
                resolved=resolved,
                targets=targets,
            )

        # May raise ConnectionScopeError when the configuration cannot be read at
        # all — before any grant is examined, which is ADR 0038's behaviour
        # preserved by ordering rather than by a clause. The record is emitted and
        # the original error re-raised unchanged; `targets` and `resolved` are both
        # null because nothing was extracted and nothing was resolved.
        try:
            connection = selected_connection(token, tool=name)
        except ConnectionScopeError:
            record("deny", "configuration-unreadable", None, None)
            raise
        extracted = selected_targets(security, arguments)
        # Built inside the guard, not beside it: these are the half of the record
        # this module assembles, and ADR 0040's totality rule covers building as
        # well as writing. A failure here degrades the record to nulls rather than
        # dropping it, and cannot touch the decision below either way.
        try:
            resolved = audit.resolved_connection(connection)
            audited: tuple[audit.AuditTarget, ...] | None = tuple(
                audit.AuditTarget(
                    kind=kind,
                    argument=selector,
                    state=audit_state(value),
                    value=value if isinstance(value, str) else None,
                )
                for kind, selector, value in extracted
            )
        except Exception:  # noqa: BLE001 - a diagnostic must not fail a call
            resolved, audited = None, None

        connection_matched = False
        # One conjunction per grant, never a union across them. Both conditions
        # are evaluated against the *same* grant before it can permit, so a policy
        # whose first grant allows the connection and whose second allows the
        # targets denies. That is ADR 0036's combination rule, and it is the
        # fail-open this module exists to make structurally unavailable.
        for grant in policy.grants_for(name):
            if not connection_permitted(connection, grant.connections):
                continue
            connection_matched = True
            if targets_permitted(grant.targets, security, extracted):
                record("allow", "permitted", resolved, audited)
                return

        # Assigned inside the loop and read only after it: the message says which
        # dimension blocked, and cannot change which grant satisfied the call.
        # Epic #218 requirement 8 asks a denial to name the constraint that
        # blocked it, and this is the only place in the design where grants are
        # considered together — for the message, never for the decision.
        if connection_matched:
            record("deny", denial_reason(security, extracted), resolved, audited)
            raise target_denial(name, policy.name, security, extracted)
        record("deny", "connection-not-granted", resolved, audited)
        raise connection_denial(name, policy.name, argument, token, connection)

    return authorize
