"""The read-only effective-permission inspection tool.

Reports the live permissions of one composed MCP application: the tools its
registry holds, their effect classes, and what the selected access policy
declares. It reports a registry rather than recomputing a ceiling, and it
distinguishes the one dimension this server enforces from the two it only
records; see docs/adr/0037-composition-time-capability-ceiling.md.

This module must not import ``server``: ``server`` imports it, and the
authoritative tool index arrives as a parameter for that reason.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from fastmcp import FastMCP

from .access_policy import DEFAULT_CONNECTION_TOKEN, AccessPolicy, AllTargets, Grant
from .tool_registry import ToolSecurity, annotations_for, validate_security

TOOL_NAME = "hmc_effective_permissions"

# The classification of a name the authoritative index does not carry. Reported
# rather than raised: a tool that describes the surface must not be the first
# thing to break when the surface changes.
UNKNOWN = "unknown"

# The dimensions of an access policy this server evaluates today, and those it
# only records. #222 moves "connections" and #223 moves "targets" across.
ENFORCED_DIMENSIONS: tuple[str, ...] = ("tools",)
DECLARED_ONLY_DIMENSIONS: tuple[str, ...] = ("connections", "targets")


@dataclass(frozen=True)
class ToolPermission:
    """One registered tool and its authoritative classification."""

    name: str
    effect: str
    operation: str
    target_kind: str


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
        return ToolPermission(name, UNKNOWN, UNKNOWN, UNKNOWN)
    return ToolPermission(
        name, security.effect, security.operation, security.target_kind
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
    policy: AccessPolicy | None,
    tool_security: Mapping[str, ToolSecurity],
    *,
    permits: Callable[[str], bool] | None = None,
) -> None:
    """Register the inspection tool on *mcp*, unless the ceiling withholds it.

    The gate lives here rather than in the caller so that this site and the
    domain modules honour ``permits`` under one contract, and no caller can be
    handed a ceiling it forgets to apply.

    The handler closes over *mcp* so it reads the registry it is reporting on.
    It reads ``local_provider`` rather than ``mcp.list_tools()``: the provider is
    what ``configure_arbitrary_command_tool`` mutates, and the server-level call
    runs the ``tools/list`` middleware chain, which this is not.
    """
    if permits is not None and not permits(TOOL_NAME):
        return

    async def hmc_effective_permissions() -> EffectivePermissions:
        """Report what this MCP server may currently do.

        Returns the tools this server exposes with their effect classes, the
        selected access policy's name and file, and the connection and target
        constraints each of its grants declares. Only the tool dimension is
        enforced today: `enforced_dimensions` and `declared_only_dimensions` say
        which is which. Contains no credentials.
        """
        names = sorted(tool.name for tool in await mcp.local_provider.list_tools())
        return describe(names, policy, tool_security)

    validate_security(EFFECTIVE_PERMISSIONS_SECURITY, hmc_effective_permissions)
    mcp.tool(
        hmc_effective_permissions,
        annotations=annotations_for(EFFECTIVE_PERMISSIONS_SECURITY.effect),
    )
