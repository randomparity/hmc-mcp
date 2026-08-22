"""The read-only effective-permission inspection tool.

Reports the live permissions of one composed MCP application: the tools its
registry holds, their effect classes, and what the selected access policy
declares. It reports a registry rather than recomputing a ceiling, and it
distinguishes the policy dimensions that constrain this registry from the ones
that merely appear in its grants; see
docs/adr/0037-composition-time-capability-ceiling.md,
docs/adr/0038-dispatch-time-connection-scope.md, and
docs/adr/0047-per-dimension-enforcement-labels.md.

This module must not import ``server``: ``server`` imports it, and the
authoritative tool index arrives as a parameter for that reason.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from fastmcp import FastMCP

from .access_policy import DEFAULT_CONNECTION_TOKEN, AccessPolicy, AllTargets, Grant
from .tool_registry import (
    Authorize,
    ToolSecurity,
    annotations_for,
    authorized,
    is_authorized_wrapper,
    validate_security,
)

TOOL_NAME = "hmc_effective_permissions"

# The classification of a name the authoritative index does not carry. Reported
# rather than raised: a tool that describes the surface must not be the first
# thing to break when the surface changes.
UNKNOWN = "unknown"

# Every dimension of an access policy, in report order. ADR 0037 enforces
# "tools" at registration; ADR 0038 "connections" and ADR 0039 "targets" at
# dispatch. Each one is reported in exactly one of the two dimension tuples
# whenever a policy is selected, so the report never drops a dimension it
# is still enumerating grants for (ADR 0047).
DIMENSIONS: tuple[str, ...] = ("tools", "connections", "targets")


@dataclass(frozen=True)
class ToolPermission:
    """One registered tool and its authoritative classification.

    ``exhaustive_targets`` is false when a policy ``targets`` table cannot bound
    the tool, so only ``all-targets`` grants it (ADR 0039). Reported because it
    is otherwise invisible: the ceiling is per-tool and cannot see targets, so a
    table-only policy still registers and advertises such a tool while denying
    every call to it. Without this field an operator meets that as an
    unexplained denial.
    """

    name: str
    effect: str
    operation: str
    target_kind: str
    exhaustive_targets: bool


@dataclass(frozen=True)
class DeclaredGrant:
    """One compiled grant, rendered whole.

    Grants are conjunctive alternatives (ADR 0036), so connections and targets
    are never merged across entries: a union would describe reach no single
    grant confers.

    ``effects`` is what the grant authored — the effect-class names in the
    document — alongside ``tools``, what it resolves to (its effect-class
    expansion unioned with its named tools). #251: without authorship, a grant
    written as `effects = ["read"]` and one written as an equivalent named-tool
    list are indistinguishable, hiding both how the file was written and the
    ADR 0036/0037 upgrade hazard that an effect-class grant silently gains any
    tool a later release adds to the class.
    """

    tools: tuple[str, ...]
    connections: tuple[str, ...]
    all_targets: bool
    targets: dict[str, tuple[str, ...]]
    effects: tuple[str, ...] = ()


@dataclass(frozen=True)
class EffectivePermissions:
    """What one composed application may currently do."""

    policy_name: str | None
    policy_source: str | None
    ceiling_enforced: bool
    effects: tuple[str, ...]
    tools: tuple[ToolPermission, ...]
    declared_grants: tuple[DeclaredGrant, ...]
    enforced_dimensions: tuple[str, ...]
    declared_only_dimensions: tuple[str, ...]


EFFECTIVE_PERMISSIONS_SECURITY = ToolSecurity(
    effect="read",
    operation="permissions.describe",
    target_kind="none",
    connection_argument=None,
)


def _permission(name: str, tool_security: Mapping[str, ToolSecurity]) -> ToolPermission:
    """Classify one registered name, tolerating a name outside the index."""
    security = tool_security.get(name)
    if security is None:
        # False is the honest value for a name the index does not carry: nothing
        # establishes that a table could bound it.
        return ToolPermission(name, UNKNOWN, UNKNOWN, UNKNOWN, False)
    return ToolPermission(
        name,
        security.effect,
        security.operation,
        security.target_kind,
        security.exhaustive_targets,
    )


def _declared_grant(grant: Grant) -> DeclaredGrant:
    """Render one compiled grant, restoring the default-connection token."""
    all_targets = isinstance(grant.targets, AllTargets)
    return DeclaredGrant(
        tools=tuple(sorted(grant.tools)),
        connections=tuple(
            sorted(
                DEFAULT_CONNECTION_TOKEN if name is None else name
                for name in grant.connections
            )
        ),
        all_targets=all_targets,
        targets=(
            {}
            if all_targets
            else {
                kind: tuple(sorted(values))
                for kind, values in sorted(grant.targets.items())
            }
        ),
        effects=grant.effects,
    )


def _connections_enforced(
    handlers: Mapping[str, object],
    tool_security: Mapping[str, ToolSecurity],
) -> bool:
    """True when every reported tool that routes a connection is guarded.

    A tool declaring no connection argument opens no HMC connection, so this
    dimension has nothing to say about it — ADR 0037 records both such tools as
    local-only by construction.
    """
    for name, handler in handlers.items():
        security = tool_security.get(name)
        if security is None:
            # A name the index does not carry could route a connection with no
            # guard, and nothing here can tell — the fail-closed default
            # `_permission` already applies to `exhaustive_targets`.
            return False
        if security.connection_argument is None:
            continue
        if not is_authorized_wrapper(handler):
            return False
    return True


def _targets_enforced(
    handlers: Mapping[str, object],
    tool_security: Mapping[str, ToolSecurity],
) -> bool:
    """True when every reported tool carries the dispatch wrapper.

    ADR 0047 had to ask a narrower question than the connection one: `authorized`
    keyed its wrapper on the connection argument, so a tool declaring none
    registered unwrapped and escaped the target check, and this label was
    withheld only when the skipped check would have decided something. #297
    closed that by wrapping every tool, so the question is now the same shape as
    `_connections_enforced`'s — with no exemption, because a tool that opens no
    connection still acts on resources a `targets` table either can or cannot
    bound.
    """
    for name, handler in handlers.items():
        if tool_security.get(name) is None:
            # A name the index does not carry could be registered without the
            # wrapper and nothing here can tell; the same fail-closed default
            # `_permission` applies to `exhaustive_targets`.
            return False
        if not is_authorized_wrapper(handler):
            return False
    return True


def describe(
    handlers: Mapping[str, object],
    policy: AccessPolicy | None,
    tool_security: Mapping[str, ToolSecurity],
) -> EffectivePermissions:
    """Build the report for a registry of *handlers* by name, under *policy*.

    The registered callables, not just their names: the connection and target
    dimensions are enforced by a wrapper around the callable (ADR 0038,
    ADR 0039), so the callable is the only evidence that they are enforced at
    all.

    ``ceiling_enforced`` is checked, not inferred: a policy must be selected and
    every reported name must satisfy it. A registry that has drifted past its
    ceiling therefore reports a policy name with no *tool*-enforcement claim,
    rather than a claim the registry contradicts.

    The two dimension tuples are decided per dimension rather than from
    ``ceiling_enforced`` alone (ADR 0047). Together they cover all three
    dimensions whenever a policy is selected, so a drifted registry says which
    dimensions still constrain it instead of falling silent about every one.
    With no policy selected both are empty: nothing is declared, so nothing is
    declared-only either.

    An empty *handlers* satisfies all three checks vacuously, and deliberately:
    a registry holding nothing cannot exceed any ceiling nor skip any wrapper,
    so a policy denying everything is enforced maximally rather than not at all.
    The state is unreachable through the tool — its own registration is what
    makes *handlers* non-empty — so this only binds a direct caller of
    :func:`describe`.
    """
    names = sorted(handlers)
    tools = tuple(_permission(name, tool_security) for name in names)
    ceiling = policy is not None and all(policy.permits_tool(name) for name in names)
    enforced_by_dimension = {
        "tools": ceiling,
        "connections": (
            policy is not None and _connections_enforced(handlers, tool_security)
        ),
        "targets": policy is not None and _targets_enforced(handlers, tool_security),
    }
    enforced = tuple(d for d in DIMENSIONS if enforced_by_dimension[d])
    return EffectivePermissions(
        policy_name=None if policy is None else policy.name,
        policy_source=None if policy is None else policy.source,
        ceiling_enforced=ceiling,
        effects=tuple(
            sorted({tool.effect for tool in tools if tool.effect != UNKNOWN})
        ),
        tools=tools,
        declared_grants=(
            () if policy is None else tuple(_declared_grant(g) for g in policy.grants)
        ),
        enforced_dimensions=enforced,
        declared_only_dimensions=(
            ()
            if policy is None
            else tuple(d for d in DIMENSIONS if d not in enforced)
        ),
    )


def register_permissions_tool(
    mcp: FastMCP,
    policy: AccessPolicy,
    tool_security: Mapping[str, ToolSecurity],
    *,
    permits: Callable[[str], bool],
    authorize: Authorize,
) -> None:
    """Register the inspection tool on *mcp*, unless the ceiling withholds it.

    The gate lives here rather than in the caller so that this site and the
    domain modules honour ``permits`` under one contract, and no caller can be
    handed a ceiling it forgets to apply.

    The handler closes over *mcp* so it reads the registry it is reporting on.
    It reads ``local_provider`` rather than ``mcp.list_tools()``: the provider is
    what ``configure_arbitrary_command_tool`` mutates, and the server-level call
    runs the ``tools/list`` middleware chain, which this is not.

    The provider is the wider set for a tool that is disabled or filtered by
    app visibility, and the *narrower* one for a tool reached through a mounted
    sub-server. So ``ceiling_enforced`` is accurate for an application nothing
    is mounted onto, which is every application this package composes; nothing
    in ``src/`` or ``scripts/`` calls ``mount()`` or ``as_proxy()``. A future
    mount would need the two accessors reconciled.
    """
    if not permits(TOOL_NAME):
        return

    async def hmc_effective_permissions() -> EffectivePermissions:
        """Report what this MCP server may currently do.

        Returns the tools this server exposes with their effect classes, the
        selected access policy's name and file, and, per grant, the effect
        classes the grant authored beside its resolved tools and its declared
        connection and target constraints. Each of the three dimensions —
        tools, connections, and targets — is reported in exactly one of
        `enforced_dimensions` and `declared_only_dimensions`, checked against
        this registry rather than assumed. A tool reporting
        `exhaustive_targets: false` can only be granted by a grant whose targets
        are the `all-targets` sentinel. Contains no credentials.
        """
        # `getattr`, because fastmcp declares `fn` on `FunctionTool` and not on
        # the `Tool` base the provider is typed to return. A registration that
        # carries no callable cannot witness the dispatch wrapper, and reports
        # the connection and target dimensions as unenforced rather than
        # assuming them.
        handlers = {
            tool.name: getattr(tool, "fn", None)
            for tool in await mcp.local_provider.list_tools()
        }
        return describe(handlers, policy, tool_security)

    validate_security(EFFECTIVE_PERMISSIONS_SECURITY, hmc_effective_permissions)
    # Wrapped like every other tool since #297, and it is this site that made the
    # async branch in `authorized` necessary: this is the package's only coroutine
    # handler, and it was previously left unwrapped only because it declares no
    # connection argument. The target dimension reaches it now — a `targets` table
    # denies it, since `target_kind="none"` leaves nothing for a table to bind.
    mcp.tool(
        authorized(
            TOOL_NAME,
            EFFECTIVE_PERMISSIONS_SECURITY,
            hmc_effective_permissions,
            authorize,
        ),
        annotations=annotations_for(EFFECTIVE_PERMISSIONS_SECURITY.effect),
    )
