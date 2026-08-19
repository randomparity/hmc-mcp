"""The read-only effective-permission inspection tool.

Reports the live permissions of one composed MCP application: the tools its
registry holds, their effect classes, and what the selected access policy
declares. It reports a registry rather than recomputing a ceiling, and it
distinguishes the dimensions this server enforces from the one it only
records; see docs/adr/0037-composition-time-capability-ceiling.md and
docs/adr/0038-dispatch-time-connection-scope.md.

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
    validate_security,
)

TOOL_NAME = "hmc_effective_permissions"

# The classification of a name the authoritative index does not carry. Reported
# rather than raised: a tool that describes the surface must not be the first
# thing to break when the surface changes.
UNKNOWN = "unknown"

# The dimensions of an access policy this server evaluates. ADR 0037 enforced
# "tools", ADR 0038 "connections", and ADR 0039 "targets" — so nothing is
# declared-only any more. The empty tuple stays rather than being deleted: it is
# the field a client reads to learn what a policy records but does not apply, and
# removing it would make "no such field" and "nothing to report" indistinguishable.
ENFORCED_DIMENSIONS: tuple[str, ...] = ("tools", "connections", "targets")
DECLARED_ONLY_DIMENSIONS: tuple[str, ...] = ()


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
    """

    tools: tuple[str, ...]
    connections: tuple[str, ...]
    all_targets: bool
    targets: dict[str, tuple[str, ...]]


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
    )


def describe(
    names: list[str],
    policy: AccessPolicy | None,
    tool_security: Mapping[str, ToolSecurity],
) -> EffectivePermissions:
    """Build the report for a registry holding *names* under *policy*.

    ``ceiling_enforced`` is checked, not inferred: a policy must be selected and
    every reported name must satisfy it. A registry that has drifted past its
    ceiling therefore reports a policy name with no enforcement claim, rather
    than a claim the registry contradicts.

    An empty *names* satisfies that check vacuously, and deliberately: a registry
    holding nothing cannot exceed any ceiling, so a policy denying everything is
    enforced maximally rather than not at all. The state is unreachable through
    the tool — its own registration is what makes *names* non-empty — so this
    only binds a direct caller of :func:`describe`.
    """
    tools = tuple(_permission(name, tool_security) for name in names)
    enforced = policy is not None and all(policy.permits_tool(name) for name in names)
    return EffectivePermissions(
        policy_name=None if policy is None else policy.name,
        policy_source=None if policy is None else policy.source,
        ceiling_enforced=enforced,
        effects=tuple(
            sorted({tool.effect for tool in tools if tool.effect != UNKNOWN})
        ),
        tools=tools,
        declared_grants=(
            () if policy is None else tuple(_declared_grant(g) for g in policy.grants)
        ),
        enforced_dimensions=ENFORCED_DIMENSIONS if enforced else (),
        declared_only_dimensions=DECLARED_ONLY_DIMENSIONS if enforced else (),
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
        selected access policy's name and file, and the connection and target
        constraints each of its grants declares. All three dimensions — tools,
        connections, and targets — are enforced; `enforced_dimensions` and
        `declared_only_dimensions` say which is which. A tool reporting
        `exhaustive_targets: false` can only be granted by a grant whose targets
        are the `all-targets` sentinel. Contains no credentials.
        """
        names = sorted(tool.name for tool in await mcp.local_provider.list_tools())
        return describe(names, policy, tool_security)

    validate_security(EFFECTIVE_PERMISSIONS_SECURITY, hmc_effective_permissions)
    # Inert by construction, and deliberately still routed through the shared
    # helper so this site cannot decide for itself: `validate_security` forbids a
    # connection argument on `target_kind="none"`, so `authorized` always returns
    # the handler here. It would have to, in fact — this is the package's only
    # coroutine handler and the wrapper is synchronous, so a tool that was both
    # async and connection-bearing would need an async branch in `authorized`.
    mcp.tool(
        authorized(
            TOOL_NAME,
            EFFECTIVE_PERMISSIONS_SECURITY,
            hmc_effective_permissions,
            authorize,
        ),
        annotations=annotations_for(EFFECTIVE_PERMISSIONS_SECURITY.effect),
    )
