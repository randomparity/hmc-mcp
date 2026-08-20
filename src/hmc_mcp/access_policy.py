"""Server access policies: strict TOML loading, validation, and compilation.

A *server access policy* bounds what the MCP server may do. It is selected at
startup, is immutable for the process lifetime, and is never influenced by an MCP
tool argument. It is a different concept from an *HMC connection profile*, which
``config.py`` resolves from ``config.toml``; see
docs/adr/0036-server-access-policy-model.md.

This module loads, validates, and compiles a policy. It does not enforce one:
registration filtering is issue #221, connection scope #222, and target
constraints #223.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

from .config import config_dir
from .tool_registry import TARGET_KINDS, TargetKind, ToolSecurity

ACCESS_POLICY_FILENAME = "access-policy.toml"

# The policy token for the environment/default HMC connection. It compiles to
# ``None``, which is what ``common.build_config(profile=None)`` already means.
# Angle brackets are not valid in a TOML bare key, so a connection profile would
# have to be written quoted to collide with it.
DEFAULT_CONNECTION_TOKEN = "<default>"

# The one bounded widening form. It widens targets only — never tools, never
# connections — and has no partial spelling.
ALL_TARGETS_TOKEN = "all-targets"

# `arbitrary-command` is deliberately absent: epic #218 requirement 6 keeps it a
# distinct maximum-risk capability, so `hmc_run_command` must be named in `tools`.
GRANT_EFFECTS: frozenset[str] = frozenset({"read", "mutate", "destructive"})

_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True)


class AccessPolicyError(ValueError):
    """Raised when an access-policy document is invalid or cannot be selected."""


def _check_entries(values: tuple[str, ...], field: str) -> tuple[str, ...]:
    """Reject duplicates, and entries that are blank or padded with whitespace.

    Padding matters because selectors and connection names are compared exactly:
    a padded entry could never match, so the grant would be dead. Unlike the
    lints ADR 0036 rejected, this one reads only operator-authored text and so
    cannot fire on a tool-index change.
    """
    if len(values) != len(set(values)):
        raise ValueError(f"{field} contains a duplicate entry")
    for value in values:
        if not value or value != value.strip():
            raise ValueError(
                f"{field} entry {value!r} is empty or padded with whitespace"
            )
    return values


class _GrantModel(BaseModel):
    model_config = _MODEL_CONFIG

    effects: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    connections: tuple[str, ...]
    # Deliberately `Any` rather than `str | dict[...]`. Under a union, pydantic
    # short-circuits with its own two member errors before an after-validator
    # runs, so a bare TOML array would never reach the message that tells the
    # operator to write "all-targets". Discriminating here keeps one message.
    targets: Any

    @field_validator("effects")
    @classmethod
    def _validate_effects(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        _check_entries(values, "effects")
        for value in values:
            if value == "arbitrary-command":
                raise ValueError(
                    "'arbitrary-command' cannot be granted by effect class; name "
                    "'hmc_run_command' in tools instead"
                )
            if value not in GRANT_EFFECTS:
                raise ValueError(
                    f"unknown effect {value!r}; expected one of {sorted(GRANT_EFFECTS)}"
                )
        return values

    @field_validator("tools")
    @classmethod
    def _validate_tools(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _check_entries(values, "tools")

    @field_validator("connections")
    @classmethod
    def _validate_connections(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values:
            raise ValueError("connections must name at least one connection")
        return _check_entries(values, "connections")

    @field_validator("targets")
    @classmethod
    def _validate_targets(cls, value: Any) -> Any:
        if value == ALL_TARGETS_TOKEN:
            return value
        if not isinstance(value, dict):
            # A misspelled sentinel falls through to here, so one message covers
            # both "wrong string" and "wrong type".
            raise ValueError(
                "'targets' must be the string \"all-targets\" or a table of target "
                f"kind to selector strings; got {value!r}"
            )
        if not value:
            raise ValueError(
                'targets table must not be empty; write targets = "all-targets" for '
                "no target restriction"
            )
        for kind, selectors in value.items():
            if not isinstance(kind, str) or kind == "none" or kind not in TARGET_KINDS:
                raise ValueError(
                    f"unknown target kind {kind!r}; expected one of "
                    f"{sorted(TARGET_KINDS - {'none'})}"
                )
            if not isinstance(selectors, (list, tuple)):
                raise ValueError(
                    f"targets kind {kind!r} must be an array of selector strings"
                )
            if not selectors:
                raise ValueError(f"targets kind {kind!r} names no selector")
            if not all(isinstance(item, str) for item in selectors):
                raise ValueError(
                    f"targets kind {kind!r} must contain only selector strings"
                )
            _check_entries(tuple(selectors), f"targets.{kind}")
        return {kind: tuple(selectors) for kind, selectors in value.items()}

    @model_validator(mode="after")
    def _validate_names_a_tool(self) -> _GrantModel:
        """P6. A shape rule, so it binds every policy in the document."""
        if not self.effects and not self.tools:
            raise ValueError("names no tool; set 'effects', 'tools', or both")
        return self


class _PolicyModel(BaseModel):
    model_config = _MODEL_CONFIG

    grants: tuple[_GrantModel, ...]


class _DocumentModel(BaseModel):
    model_config = _MODEL_CONFIG

    policies: dict[str, _PolicyModel]

    @field_validator("policies")
    @classmethod
    def _validate_policies(
        cls, value: dict[str, _PolicyModel]
    ) -> dict[str, _PolicyModel]:
        if not value:
            raise ValueError("policies must define at least one policy")
        for name in value:
            if not name or name != name.strip():
                raise ValueError(
                    f"policy name {name!r} is empty or padded with whitespace"
                )
        return value


def _render_error(source: str, error: Mapping[str, Any]) -> str:
    """Render one pydantic error in config.py's fail-fast message convention.

    The policy name and grant index come from the error's ``loc`` tuple; whichever
    segments the ``loc`` does not carry are dropped, so a document-level error
    names only the source. The rule id is deliberately not recovered — mapping a
    ``loc`` back to a P number would be machinery bought for a testability nicety.
    """
    loc = tuple(error["loc"])
    parts = [source]
    consumed = 0
    if len(loc) >= 2 and loc[0] == "policies":
        parts.append(f"policy {loc[1]!r}")
        consumed = 2
        if len(loc) >= 4 and loc[2] == "grants" and isinstance(loc[3], int):
            parts.append(f"grant {loc[3]}")
            consumed = 4
    if error["type"] == "extra_forbidden":
        parts.append(f"unknown key {loc[-1]!r}")
    elif error["type"] == "missing":
        parts.append(f"missing required key {loc[-1]!r}")
    else:
        message = str(error["msg"]).removeprefix("Value error, ")
        # A pydantic type error ("Input should be a valid tuple") names no key, so
        # `connections = "lab"` and `effects = "read"` would render identically.
        # Recover the field from the loc. Messages from this module's own
        # validators already name their field, so they are left alone.
        if error["type"] != "value_error":
            key = next(
                (item for item in loc[consumed:] if isinstance(item, str)), None
            )
            if key is not None:
                message = f"{key!r}: {message}"
        parts.append(message)
    return ": ".join(parts)


def _parse_document(document: Mapping[str, Any], source: str) -> _DocumentModel:
    """Validate document shape, raising AccessPolicyError on any violation."""
    try:
        return _DocumentModel.model_validate(document)
    except ValidationError as error:
        detail = "\n".join(_render_error(source, item) for item in error.errors())
        raise AccessPolicyError(detail) from error


@dataclass(frozen=True)
class AllTargets:
    """The grant places no target constraint. The one bounded widening form."""

    def __repr__(self) -> str:
        return "ALL_TARGETS"


ALL_TARGETS = AllTargets()


@dataclass(frozen=True)
class Grant:
    """One compiled grant: tools, connections, and targets, evaluated together.

    A request is permitted only when a *single* grant covers its tool, its
    connection, and its targets simultaneously. Grants are disjoint alternatives;
    the three dimensions are never unioned independently across grants.

    ``connections`` holds ``None`` for the environment/default connection, which
    is what ``common.build_config(profile=None)`` means.
    """

    tools: frozenset[str]
    connections: frozenset[str | None]
    targets: AllTargets | Mapping[TargetKind, frozenset[str]]

    # Uniformly unhashable. Without this a frozen dataclass hashes its field
    # tuple, so a grant carrying ALL_TARGETS would hash while one carrying a
    # MappingProxyType raised TypeError — hashability would depend on the
    # operator's file. `dataclass` honours an explicit __hash__ in the class body
    # when the body defines no __eq__, so this assignment survives the decorator.
    __hash__ = None  # type: ignore[assignment]


@dataclass(frozen=True)
class AccessPolicy:
    """A named access policy, fixed from construction."""

    name: str
    source: str
    grants: tuple[Grant, ...]
    tools: frozenset[str]

    def permits_tool(self, tool: str) -> bool:
        """True when the capability ceiling admits *tool*.

        This is the ceiling question #221's registration filter asks. It is never
        sufficient authorization on its own: #222 and #223 must evaluate a whole
        grant from :meth:`grants_for`.
        """
        return tool in self.tools

    def grants_for(self, tool: str) -> tuple[Grant, ...]:
        """Every grant covering *tool*, in document order."""
        return tuple(grant for grant in self.grants if tool in grant.tools)


def _resolve_tools(
    model: _GrantModel, tool_security: Mapping[str, ToolSecurity]
) -> frozenset[str]:
    """The union of the grant's effect classes and its explicitly named tools."""
    effects = set(model.effects)
    resolved = {
        name for name, security in tool_security.items() if security.effect in effects
    }
    resolved.update(model.tools)
    return frozenset(resolved)


def _compile_grant(
    model: _GrantModel,
    tool_security: Mapping[str, ToolSecurity],
    where: str,
) -> Grant:
    """Apply P7-P9 to one grant and compile it.

    P6 (a grant names at least one tool) is a shape rule and already ran in
    ``_GrantModel``, so it binds every policy rather than only the selected one.

    *where* is the pre-rendered ``<source>: policy '<name>': grant <i>`` prefix, so
    this function never re-derives message context.
    """
    for tool in model.tools:
        if tool not in tool_security:
            # The bare generator command would collide here: this policy already
            # exists on disk (it was just read and compiled), and the generator
            # never overwrites (ADR 0041, cli_config.py's `_write_exclusive`). Name
            # the scratch-path-and-merge flow that actually regenerates one.
            raise AccessPolicyError(
                f"{where}: unknown tool {tool!r}; if this policy predates a tool "
                "rename or removal, regenerate it to a scratch path with "
                "'hmc-mcp config init-access-policy --output PATH', diff that "
                "against this file, and merge the change by hand"
            )

    resolved = _resolve_tools(model, tool_security)

    if not isinstance(model.targets, str):
        declared = {
            selector.kind
            for name in resolved
            for selector in tool_security[name].targets
        }
        for kind in model.targets:
            if kind not in declared:
                raise AccessPolicyError(
                    f"{where}: no granted tool declares a target selector of kind "
                    f"{kind!r}, so the constraint could never match"
                )
        for tool in model.tools:
            security = tool_security[tool]
            if security.connection_argument is None:
                # Never wrapped by `tool_registry.authorized`, so no authorizer
                # ever runs on it and the target dimension structurally cannot
                # reach it. A grant naming it beside a table is bounded by the
                # ceiling alone — which is exactly how it behaved before ADR 0039
                # — so it is not dead and must not fail the load. Failing here
                # would refuse to start a server over a working grant, and would
                # do it for `hmc_effective_permissions` in particular.
                continue
            if not security.exhaustive_targets:
                raise AccessPolicyError(
                    f"{where}: tool {tool!r} has no target selector that a targets "
                    "table can bound, so this grant could never authorize it; "
                    f'grant it under targets = "{ALL_TARGETS_TOKEN}" instead'
                )
            # Every declared selector kind, not only the required ones. ADR 0039
            # denies an omitted optional selector at call time, so a grant leaving
            # one uncovered is dead in the same way a missing required kind is —
            # the authoring error ADR 0036 invented this rule to catch. This
            # supersedes ADR 0036 acceptance criterion A7.
            for selector in security.targets:
                if selector.kind not in model.targets:
                    raise AccessPolicyError(
                        f"{where}: tool {tool!r} requires a target constraint for "
                        f"kind {selector.kind!r}; add it to targets or use "
                        f'targets = "{ALL_TARGETS_TOKEN}"'
                    )

    connections: frozenset[str | None] = frozenset(
        None if name == DEFAULT_CONNECTION_TOKEN else name
        for name in model.connections
    )
    targets: AllTargets | Mapping[TargetKind, frozenset[str]] = (
        ALL_TARGETS
        if isinstance(model.targets, str)
        else MappingProxyType(
            {kind: frozenset(values) for kind, values in model.targets.items()}
        )
    )
    return Grant(tools=resolved, connections=connections, targets=targets)


def compile_access_policy(
    document: Mapping[str, Any],
    name: str,
    tool_security: Mapping[str, ToolSecurity],
    source: str,
) -> AccessPolicy:
    """Validate *document* and compile its *name* policy into a frozen object.

    *tool_security* is the authoritative classification index, normally
    ``server.TOOL_SECURITY``. It is a parameter rather than an import so the
    dependency runs one way: #221 makes ``server`` policy-aware.

    *source* names the origin for error messages — the resolved file path when
    the caller read one.
    """
    parsed = _parse_document(document, source)
    policy = parsed.policies.get(name)
    if policy is None:
        # repr each name, as every other operator-authored string in this module
        # is rendered, so a policy name carrying a control character cannot forge
        # a line in whatever startup log surfaces this message.
        available = ", ".join(repr(name) for name in sorted(parsed.policies)) or "(none)"
        raise AccessPolicyError(
            f"{source}: policy {name!r} not found; available policies: {available}"
        )

    grants = tuple(
        _compile_grant(
            model, tool_security, f"{source}: policy {name!r}: grant {index}"
        )
        for index, model in enumerate(policy.grants)
    )
    for later, grant in enumerate(grants):
        for earlier in range(later):
            if grants[earlier] == grant:
                raise AccessPolicyError(
                    f"{source}: policy {name!r}: grants {earlier} and {later} are "
                    "identical after compilation"
                )

    tools: frozenset[str] = frozenset().union(*(grant.tools for grant in grants))
    return AccessPolicy(name=name, source=source, grants=grants, tools=tools)


def resolve_access_policy_path() -> Path:
    """The platform-native access-policy path, beside ``config.toml``.

    No existence check — the caller reports an absent file with its own message.
    """
    return config_dir() / ACCESS_POLICY_FILENAME


def load_access_policy(
    name: str,
    tool_security: Mapping[str, ToolSecurity],
    *,
    path: Path | str | None = None,
) -> AccessPolicy:
    """Read, validate, and compile the *name* policy from an access-policy file.

    *path* defaults to :func:`resolve_access_policy_path`. Every failure is an
    :class:`AccessPolicyError` naming the resolved path, so a fail-closed startup
    can report which file it read rather than surfacing a decoding traceback.
    """
    try:
        resolved = Path(path) if path is not None else resolve_access_policy_path()
    except (RuntimeError, ValueError) as error:
        # config_dir() calls Path.home(), which raises RuntimeError when the home
        # directory cannot be determined — a container or systemd unit running
        # under a uid with no passwd entry and no HOME. Left unguarded that
        # escapes as a traceback from a security control's startup path.
        raise AccessPolicyError(
            f"cannot resolve the access-policy path: {error}"
        ) from error
    source = str(resolved)
    try:
        text = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise AccessPolicyError(f"{source}: is not valid UTF-8: {error}") from error
    except (OSError, ValueError) as error:
        # ValueError covers an unusable path string, such as an embedded null
        # byte, which read_text raises before it reaches the filesystem.
        raise AccessPolicyError(f"{source}: cannot be read: {error}") from error
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise AccessPolicyError(f"{source}: TOML parse error: {error}") from error
    except RecursionError as error:
        # tomllib recurses on nested arrays and inline tables, so a deeply nested
        # document exhausts the stack before it can report a syntax error. A
        # RecursionError carries no message, hence the fixed clause.
        raise AccessPolicyError(
            f"{source}: TOML parse error: document nesting is too deep"
        ) from error
    return compile_access_policy(document, name, tool_security, source)
