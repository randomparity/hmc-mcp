"""Dispatch-time authorization of the targets an MCP call names.

The access policy loaded by ``access_policy.py`` names the targets each grant
allows, per target kind. This module answers, for one call and **one grant**,
which targets the call actually names and whether that grant covers them. It
never sees the policy and never iterates grants — ``dispatch_scope`` owns the
only loop, because ADR 0036's rule is that a single grant must cover the tool,
the connection, and the targets *together*, and a module that could union a
dimension across grants is a module that could break it.

See docs/adr/0039-dispatch-time-target-scope.md.

Unlike the connection dimension, extraction here reads nothing: no filesystem,
no environment, no HMC. A target selector is already the identity it names, so
there is no ``build_config`` to mirror and no two-read race to inherit.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from .access_policy import ALL_TARGETS_TOKEN, AllTargets
from .tool_registry import TargetKind, ToolSecurity


class TargetScopeError(Exception):
    """An MCP call named a target the access policy does not grant.

    Also raised when a declared selector is absent from the call, when its value
    is of a type the boundary declines to read, and when the tool's selectors
    cannot bound it at all. Every one of those is a denial for the same reason:
    the server cannot show that the call acts only on granted targets.
    """


class _Unresolved:
    """A selector that yielded no comparable value, as a named singleton."""

    __slots__ = ("_label",)

    def __init__(self, label: str) -> None:
        self._label = label

    def __repr__(self) -> str:
        return self._label


#: An optional selector the caller omitted. A well-formed call, so ``all-targets``
#: still covers it — #225's legacy-equivalent exposure includes every call that
#: omits an optional argument — but no ``targets`` table can bound a target the
#: call declined to name.
ABSENT: Final = _Unresolved("ABSENT")

#: A selector value the boundary declines to read, uninspected and uncoerced.
#: A malformed call rather than a narrow one, so it denies under ``all-targets``
#: too. Unreachable through MCP, where the generated schema types every selector
#: ``string`` or ``integer``; the G14 guardrail is what keeps that true.
UNREADABLE: Final = _Unresolved("UNREADABLE")

#: One extracted selector: its kind, the argument it came from, and either the
#: string to compare or one of the two sentinels above.
Selected = tuple[TargetKind, str, str | _Unresolved]

_UNBOUNDABLE = (
    "{tool} is not permitted by access policy {policy}: its declared target "
    "selectors do not name every resource it acts on, so a targets table cannot "
    'constrain it. Grant {tool} under targets = "{sentinel}" in a grant that '
    "names it."
)
_UNREADABLE_VALUE = (
    "{tool} is not permitted by access policy {policy}: the {argument} argument "
    "does not carry a readable {kind} target."
)
_MISSING = (
    "{tool} is not permitted by access policy {policy}: it declares a {kind} "
    "target through {argument}, which this call did not supply, and a "
    "target-constrained grant cannot bound an omitted target. Supply it and "
    'grant that {kind}, or grant {tool} under targets = "{sentinel}".'
)
_DENIED = (
    "{tool} is not permitted on {targets} by access policy {policy}. No grant "
    "naming {tool} allows that combination of targets. Grant them in a policy "
    "grant that already names {tool}, or call {tool} with targets the policy "
    "grants."
)


def _value(raw: Any) -> str | _Unresolved:
    """One bound argument, as the string to compare or a sentinel.

    The arms are ordered, and the order is the whole rule. ``bool`` is tested
    before ``int`` because it is an ``int`` subclass, and ``str(True)`` would put
    ``"True"`` into a comparison against resource names.

    The ``int`` arm exists for ``vios_partition_id``, the surface's only non-string
    selector. It is load-bearing rather than convenient: without it those calls
    would be UNREADABLE, which denies even under ``all-targets``, and #225's
    legacy-equivalent policy would stop covering three live tools. It does not
    make the *comparison* unambiguous — a ``vios`` allowlist holds partition IDs
    and VIOS names in one set — which is why ADR 0039 refuses ``vios_partition_id``
    as a bounding identity outright instead of trusting the rendering.
    """
    if isinstance(raw, str):
        # Including "": a well-formed string that no table can hold, because
        # `access_policy._check_entries` rejects an empty allowlist entry.
        return raw
    if raw is None:
        return ABSENT
    if isinstance(raw, bool):
        return UNREADABLE
    if isinstance(raw, int):
        return str(raw)
    return UNREADABLE


def selected_targets(
    security: ToolSecurity, arguments: Mapping[str, Any]
) -> tuple[Selected, ...]:
    """Every target *security* declares, read from the call's bound *arguments*.

    Indexed rather than ``.get``: ``tool_registry.authorized`` has already applied
    the handler's defaults and ``validate_security`` guarantees each selector is a
    parameter, so an absent key is a malformed call. Letting it read as an omitted
    argument would turn a broken call into a merely narrow one.

    Declaration order is preserved, which is the order the denial message reports.
    """
    return tuple(
        (target.kind, target.argument, _value(arguments[target.argument]))
        for target in security.targets
    )


def targets_permitted(
    grant_targets: AllTargets | Mapping[TargetKind, frozenset[str]],
    security: ToolSecurity,
    extracted: tuple[Selected, ...],
) -> bool:
    """True when *grant_targets* covers every target the call named.

    One grant's ``targets`` value, never a union across grants: the caller holds
    the loop and this function holds no state.
    """
    if any(value is UNREADABLE for _kind, _argument, value in extracted):
        # Denied under both forms. The sentinel says "any target of the kinds you
        # declare", not "any argument of any type", and a value the boundary
        # cannot read is a malformed call rather than part of any exposure.
        return False
    if isinstance(grant_targets, AllTargets):
        return True
    if not security.exhaustive_targets:
        # The tool's selectors cannot bound it, so no table can. Checked before
        # the loop below, which an empty `extracted` would otherwise satisfy
        # vacuously — the fail-open ADR 0039 names and refuses.
        return False
    for kind, _argument, value in extracted:
        if value is ABSENT:
            return False
        allowed = grant_targets.get(kind)
        if allowed is None or value not in allowed:
            return False
    return True


def target_denial(
    tool: str,
    policy_name: str,
    security: ToolSecurity,
    extracted: tuple[Selected, ...],
) -> TargetScopeError:
    """The error a target denial raises, chosen from what the call itself shows.

    It receives no grant and no policy object, because once the loop has fallen
    through there is no single grant to blame — selecting one would be the
    cross-grant read the whole design forbids. So the choice is a total function
    of *security* and *extracted*, in this order:

    1. an UNREADABLE selector, reported as malformed. No policy edit fixes it, so
       every other message would be misleading advice.
    2. a tool a table can never bound, reported without naming a selector.
    3. an ABSENT selector, reported with the argument to supply.
    4. otherwise the whole tuple of targets the caller named.

    Case 4 renders every selector rather than picking the dispositive one, which
    would require choosing a grant to be dispositive against. The honest claim is
    that no grant allowed this *combination*, and the combination is also what
    the operator must add.

    Every template is closed in ADR 0038's sense: only the tool name, the policy
    name, a compiled-in kind or argument name, and the caller's own values are
    substituted, all under ``repr()``. No allowlist, host, path, credential, or
    chained exception text can reach it, which makes "carries no secret" a
    property of the text rather than a claim about it.
    """
    for kind, argument, value in extracted:
        if value is UNREADABLE:
            # The value itself is deliberately not rendered: an arbitrary object's
            # repr is not the caller's own token in any useful sense and could
            # carry anything. The argument name is enough to act on.
            return TargetScopeError(
                _UNREADABLE_VALUE.format(
                    tool=tool, policy=repr(policy_name), argument=repr(argument),
                    kind=kind,
                )
            )
    if not security.exhaustive_targets:
        return TargetScopeError(
            _UNBOUNDABLE.format(
                tool=tool, policy=repr(policy_name), sentinel=ALL_TARGETS_TOKEN
            )
        )
    for kind, argument, value in extracted:
        if value is ABSENT:
            return TargetScopeError(
                _MISSING.format(
                    tool=tool, policy=repr(policy_name), kind=kind,
                    argument=repr(argument), sentinel=ALL_TARGETS_TOKEN,
                )
            )
    return TargetScopeError(
        _DENIED.format(
            tool=tool,
            policy=repr(policy_name),
            # The caller's own values, exactly as ADR 0038 renders the caller's
            # own connection token: the caller already holds them, so echoing
            # them discloses nothing it did not send. repr() also neutralizes any
            # control character a caller puts in one.
            targets=", ".join(
                f"{kind}={value!r}" for kind, _argument, value in extracted
            ),
        )
    )
