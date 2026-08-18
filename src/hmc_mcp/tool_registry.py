"""Local MCP tool collection for explicit application composition.

Every collected tool carries a :class:`ToolSecurity` record — effect class,
operation identity, target kind, and the public arguments from which connection
and target selectors are read. It is the single authoritative classification:
the MCP ``ToolAnnotations`` shipped to clients are derived from ``effect``, and
``server.TOOL_SECURITY`` indexes the records for the access-policy layers built
on top of them. See docs/adr/0035-enforceable-tool-security-metadata.md.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Literal, get_args

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

Effect = Literal["read", "mutate", "destructive", "arbitrary-command"]

TargetKind = Literal[
    "none",
    "console",
    "managed_system",
    "lpar",
    "vios",
    "cluster",
    "shared_storage_pool",
    "user",
    "password_policy",
    "job",
    "template",
    "metric_resource",
]

EFFECTS: frozenset[str] = frozenset(get_args(Effect))
TARGET_KINDS: frozenset[str] = frozenset(get_args(TargetKind))

_OPERATION = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")

# Public argument names that unambiguously identify one resource kind. tool()
# intersects this with each handler signature to build its target selectors, so
# a tool cannot omit a target it accepts an identity for. Deliberately excludes
# `name` (a user on hmc_create_user, a new partition on hmc_create_lpar) and
# sub-resource arguments, which are addressed through their owning resource.
REQUIRED_TARGET_ARGUMENTS: Mapping[str, TargetKind] = {
    "lpar_name_or_uuid": "lpar",
    "lpar_uuid": "lpar",
    "system_name_or_uuid": "managed_system",
    "target_system_name_or_uuid": "managed_system",
    "vios_name_or_uuid": "vios",
    "vios_uuid": "vios",
    "vios_partition_id": "vios",
    "cluster_uuid": "cluster",
    "ssp_uuid": "shared_storage_pool",
    "console_uuid": "console",
    "job_uuid": "job",
    "template_uuid": "template",
    "draft_template_uuid": "template",
    "policy_name": "password_policy",
    "resource_name_or_uuid": "metric_resource",
}

_ANNOTATIONS: Mapping[str, ToolAnnotations] = {
    "read": ToolAnnotations(readOnlyHint=True),
    "mutate": ToolAnnotations(readOnlyHint=False),
    "destructive": ToolAnnotations(readOnlyHint=False, destructiveHint=True),
    "arbitrary-command": ToolAnnotations(readOnlyHint=False, destructiveHint=True),
}


@dataclass(frozen=True)
class TargetSelector:
    """A public handler argument carrying the identity of one target resource."""

    kind: TargetKind
    argument: str
    required: bool


@dataclass(frozen=True)
class ToolSecurity:
    """The authoritative security classification of one MCP tool."""

    effect: Effect
    operation: str
    target_kind: TargetKind
    targets: tuple[TargetSelector, ...] = ()
    connection_argument: str | None = "profile"


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    handler: Callable[..., Any]
    security: ToolSecurity


def annotations_for(effect: Effect) -> ToolAnnotations:
    """Return the MCP annotations for an effect class.

    A fresh copy each call: ``ToolAnnotations`` is a mutable pydantic model, and
    handing out the shared instance would let one in-place edit re-flag every
    tool of that class.
    """
    return _ANNOTATIONS[effect].model_copy()


def build_targets(
    handler: Callable[..., Any],
    extra_targets: Iterable[tuple[TargetKind, str]],
) -> tuple[TargetSelector, ...]:
    """Build target selectors from the argument table plus explicit extras."""
    parameters = inspect.signature(handler).parameters
    selectors = [
        TargetSelector(kind, name, parameter.default is inspect.Parameter.empty)
        for name, parameter in parameters.items()
        if (kind := REQUIRED_TARGET_ARGUMENTS.get(name)) is not None
    ]
    for kind, argument in extra_targets:
        parameter = parameters.get(argument)
        required = parameter is not None and parameter.default is inspect.Parameter.empty
        selectors.append(TargetSelector(kind, argument, required))
    return tuple(selectors)


def _validate_arguments(
    security: ToolSecurity,
    parameters: Mapping[str, inspect.Parameter],
    name: str,
) -> None:
    """Reject a selector or connection argument the handler does not accept."""
    for target in security.targets:
        if target.argument not in parameters:
            raise ValueError(
                f"{name}: target argument {target.argument!r} is not a parameter; "
                f"handler takes {sorted(parameters)}"
            )
    if (
        security.connection_argument is not None
        and security.connection_argument not in parameters
    ):
        raise ValueError(
            f"{name}: connection argument {security.connection_argument!r} is not a "
            f"parameter; handler takes {sorted(parameters)}"
        )
    arguments = [target.argument for target in security.targets]
    if len(arguments) != len(set(arguments)):
        raise ValueError(f"{name}: duplicate target argument in {sorted(arguments)}")


def validate_security(security: ToolSecurity, handler: Callable[..., Any]) -> None:
    """Reject a declaration that is malformed or contradicts its handler."""
    name = getattr(handler, "__name__", "<handler>")
    if security.effect not in EFFECTS:
        raise ValueError(f"{name}: unknown effect {security.effect!r}")
    if not _OPERATION.match(security.operation):
        raise ValueError(
            f"{name}: operation {security.operation!r} must be '<domain>.<verb>'"
        )
    kinds = {security.target_kind, *(target.kind for target in security.targets)}
    if unknown := sorted(kinds - TARGET_KINDS):
        raise ValueError(f"{name}: unknown target_kind {unknown}")

    _validate_arguments(security, inspect.signature(handler).parameters, name)

    if security.target_kind == "none":
        if security.targets or security.connection_argument is not None:
            raise ValueError(
                f"{name}: target_kind 'none' allows no targets and no connection argument"
            )
        return
    if security.target_kind != "console" and not any(
        target.kind == security.target_kind for target in security.targets
    ):
        raise ValueError(
            f"{name}: target_kind {security.target_kind!r} has no matching target "
            "selector; pass extra_targets when the argument table cannot name it"
        )


def build_tool_security(
    module_mappings: Iterable[Mapping[str, ToolSecurity]],
    extra: Mapping[str, ToolSecurity],
) -> Mapping[str, ToolSecurity]:
    """Merge per-module classifications, rejecting name and identity collisions."""
    index: dict[str, ToolSecurity] = {}
    operations: dict[str, str] = {}
    for mapping in [*module_mappings, extra]:
        for name, security in mapping.items():
            if name in index:
                raise ValueError(f"duplicate tool name {name!r}")
            if (owner := operations.get(security.operation)) is not None:
                raise ValueError(
                    f"duplicate operation {security.operation!r} on {owner!r} and {name!r}"
                )
            index[name] = security
            operations[security.operation] = name
    return MappingProxyType(index)


def tool_module():
    """Return a module-local decorator, registration function, and classifications."""
    definitions: list[ToolDefinition] = []

    def tool(
        *,
        effect: Effect,
        operation: str,
        target_kind: TargetKind,
        extra_targets: Iterable[tuple[TargetKind, str]] = (),
        connection_argument: str | None = "profile",
    ):
        def collect(fn: Callable[..., Any]):
            name = getattr(fn, "__name__", "<handler>")
            security = ToolSecurity(
                effect=effect,
                operation=operation,
                target_kind=target_kind,
                connection_argument=connection_argument,
            )
            security = replace(security, targets=build_targets(fn, extra_targets))
            validate_security(security, fn)
            definitions.append(ToolDefinition(name, fn, security))
            return fn

        return collect

    def register_tools(mcp: FastMCP) -> None:
        for definition in definitions:
            mcp.tool(
                definition.handler,
                annotations=annotations_for(definition.security.effect),
            )

    def tool_security() -> Mapping[str, ToolSecurity]:
        return MappingProxyType(
            {definition.name: definition.security for definition in definitions}
        )

    return tool, register_tools, tool_security
