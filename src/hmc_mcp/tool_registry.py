"""Local MCP tool collection for explicit application composition.

Every collected tool carries a :class:`ToolSecurity` record — effect class,
operation identity, target kind, and the public arguments from which connection
and target selectors are read. It is the single authoritative classification:
the MCP ``ToolAnnotations`` shipped to clients are derived from ``effect``, and
``server.TOOL_SECURITY`` indexes the records for the access-policy layers built
on top of them. See docs/adr/0035-enforceable-tool-security-metadata.md.
"""

from __future__ import annotations

import functools
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
REQUIRED_TARGET_ARGUMENTS: Mapping[str, TargetKind] = MappingProxyType({
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
})

# Public argument names that carry the identity of an HMC-side resource no
# allowlist can pin down. A tool accepting one cannot declare
# `exhaustive_targets=True`, so a policy `targets` table never grants it and only
# `all-targets` does.
#
# - `file_path` names a file on the HMC's own filesystem, which no TargetKind
#   expresses — ADR 0036 placed it outside every grant.
# - `cmd` is free-form console command text.
# - `vios_partition_id` is a *slot number within one managed system*, reused
#   across every system in a fleet, so an allowlist entry of "2" names a
#   different VIOS on each of them; unlike a partition name it has no UUID form
#   to fall back on, so there is no way to write it precisely.
# - `job_href` is a caller-supplied URI whose *path* replaces the `job_uuid`
#   selector entirely (`client.get_job`), so the value authorized and the value
#   fetched are different values.
#
# Membership is decided by whether a `targets` table can bound the identity, and
# a name fails that by either of two routes. It cannot be written down at all
# (`cmd` is free-form text; a `vios_partition_id` of "2" names a different VIOS on
# every system), or it can be written and still designates something the declared
# selectors do not contain (`file_path`, `job_href`). Which filesystem the value
# refers to decides nothing: `file_path` is a member because an absolute console
# path is contained by no selector — for `rstprofdata -f`, which only reads it,
# exactly as for `bkprofdata -f` — and not because the file sits on the HMC.
#
# `backup_name` on `hmc_restore_vios` is the HMC-side name that made that
# explicit and is deliberately *not* a member: `chviosbackup -id` selects the
# catalog `-file` resolves in, so a bare name is reached through containment from
# a declared selector. That holds only while the value cannot leave the catalog,
# which `server_vios._validate_backup_name` is what enforces. ADR 0044 records the
# decision and the two questions it leaves open (#282, #283).
#
# This is not the complement of REQUIRED_TARGET_ARGUMENTS: `vios_partition_id`
# is in both, deliberately. It is a declared selector — so it is extracted and
# compared under `all-targets`, which is what keeps three live tools working —
# *and* an identity no table can bound. The two tables answer different
# questions about the same name and must not be merged.
#
# They are kept adjacent for that reason: together they are one piece of
# knowledge — which public argument names carry which identity — and a name that
# moved between them while they lived in different files would drift silently.
# No runtime path reads this one; the boolean it justifies is what the authorizer
# reads. The ADR 0039 guardrail in tests/app/test_tool_security.py enforces it.
UNBOUNDED_ARGUMENTS: frozenset[str] = frozenset({
    "cmd",
    "file_path",
    "job_href",
    "vios_partition_id",
})

# (readOnlyHint, destructiveHint) per effect class, held as immutable values
# rather than shared ToolAnnotations instances: the model is mutable, so a shared
# instance would let one in-place edit re-flag every tool of that class. `mutate`
# leaves destructiveHint unset — MCP defaults it to true, and asserting false
# would newly invite a client to auto-approve every mutating tool.
_ANNOTATIONS: Mapping[str, tuple[bool, bool | None]] = MappingProxyType({
    "read": (True, None),
    "mutate": (False, None),
    "destructive": (False, True),
    "arbitrary-command": (False, True),
})


@dataclass(frozen=True)
class TargetSelector:
    """A public handler argument carrying the identity of one target resource."""

    kind: TargetKind
    argument: str
    required: bool


@dataclass(frozen=True)
class ToolSecurity:
    """The authoritative security classification of one MCP tool.

    ``exhaustive_targets`` answers one question for the access policy: do the
    declared selectors name every resource this tool acts on, so a ``targets``
    table can bound the call? When false, only the ``all-targets`` sentinel can
    grant the tool — a table has either nothing to bind on (no selectors) or
    something it cannot see (a composite reaching an identity nested below the
    signature). See docs/adr/0039-dispatch-time-target-scope.md.

    Its default is the fail-closed value, so a record built by hand — as
    ``server_command`` and ``server_permissions`` build theirs — is safe without
    naming the field. Only :func:`tool_module`'s decorator, which has inspected a
    signature and found selectors, raises it.
    """

    effect: Effect
    operation: str
    target_kind: TargetKind
    targets: tuple[TargetSelector, ...] = ()
    connection_argument: str | None = "profile"
    exhaustive_targets: bool = False


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    handler: Callable[..., Any]
    security: ToolSecurity


# The access policy's dispatch-time question, taken as a callable for the reason
# ADR 0037 takes the ceiling as one: `access_policy` imports this module, so the
# dependency must not run back the other way. It is given the tool name, its
# authoritative classification, and the call's bound arguments; it returns None
# to permit and raises to deny. See ADR 0038.
Authorize = Callable[[str, ToolSecurity, Mapping[str, Any]], None]


def authorized(
    name: str,
    security: ToolSecurity,
    handler: Callable[..., Any],
    authorize: Authorize,
) -> Callable[..., Any]:
    """Return *handler*, or a wrapper that authorizes the call before running it.

    The wrapper — not the registration site — decides: a tool declaring no
    connection argument registers unwrapped, so no site can be handed an
    authorizer it forgets to apply. *authorize* is required since ADR 0041;
    the arm that returned a bare handler because no policy was selected
    described a composition that no longer exists.

    Arguments are bound against the handler's own signature rather than read out
    of ``kwargs``, so a selector passed positionally or left to its default is
    read correctly. ``functools.wraps`` sets ``__wrapped__``, which both
    ``inspect.signature`` and FastMCP's schema generation follow, so the
    registered tool's name, description, and parameter schema are unchanged.
    """
    if security.connection_argument is None:
        return handler
    signature = inspect.signature(handler)

    @functools.wraps(handler)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        authorize(name, security, bound.arguments)
        return handler(*args, **kwargs)

    return guarded


def annotations_for(effect: Effect) -> ToolAnnotations:
    """Return the MCP annotations for an effect class, as a fresh object."""
    read_only, destructive = _ANNOTATIONS[effect]
    return ToolAnnotations(readOnlyHint=read_only, destructiveHint=destructive)


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
    if security.exhaustive_targets and not security.targets:
        raise ValueError(
            f"{name}: exhaustive_targets requires at least one target selector; a "
            "policy targets table would have nothing to bind on"
        )

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
        exhaustive_targets: bool = True,
    ):
        def collect(fn: Callable[..., Any]):
            name = getattr(fn, "__name__", "<handler>")
            security = ToolSecurity(
                effect=effect,
                operation=operation,
                target_kind=target_kind,
                connection_argument=connection_argument,
            )
            try:
                targets = build_targets(fn, extra_targets)
            except Exception as error:  # noqa: BLE001 - re-raised with the tool named
                raise ValueError(
                    f"{name}: cannot inspect signature: {error!r}"
                ) from error
            # A tool that declares no selector can never be exhaustive, whatever
            # it claims: the conjunction is what makes the selector-less case a
            # degenerate instance of the composite rule rather than a second one.
            security = replace(
                security,
                targets=targets,
                exhaustive_targets=exhaustive_targets and bool(targets),
            )
            validate_security(security, fn)
            definitions.append(ToolDefinition(name, fn, security))
            return fn

        return collect

    def register_tools(
        mcp: FastMCP,
        *,
        permits: Callable[[str], bool],
        authorize: Authorize,
    ) -> None:
        """Register this module's tools, skipping any the ceiling withholds.

        *permits* is the access policy's ceiling question and *authorize* its
        dispatch-time question. Both are required since ADR 0041: this is the
        bulk registration site, and while they defaulted to ``None`` a caller
        that omitted them registered a module's entire tool set with no ceiling
        and no authorizer, silently and without error. Both are taken as
        callables rather than the policy object because ``access_policy``
        imports this module; see ADR 0037 and ADR 0038.
        """
        for definition in definitions:
            if not permits(definition.name):
                continue
            mcp.tool(
                authorized(
                    definition.name,
                    definition.security,
                    definition.handler,
                    authorize,
                ),
                annotations=annotations_for(definition.security.effect),
            )

    def tool_security() -> Mapping[str, ToolSecurity]:
        return MappingProxyType(
            {definition.name: definition.security for definition in definitions}
        )

    return tool, register_tools, tool_security
