# Implementation plan — enforceable tool security metadata

**Goal.** Every live MCP tool carries one authoritative, server-enforced security
classification on the registry entry that drives its registration; MCP annotations derive
from it; the parallel `READ_ONLY_TOOLS` / `DESTRUCTIVE_TOOLS` frozensets are deleted; and a
contract test fails when a tool omits or contradicts the metadata.

**Architecture.** `src/hmc_mcp/tool_registry.py` gains a frozen `ToolSecurity` record and a
`tool()` decorator that requires `effect`, `operation`, and `target_kind`, builds the target
selectors from a fixed argument-name table intersected with the handler signature, and
validates the result at import. Each domain module's `tool_module()` now returns a
three-tuple whose third element exposes that module's `{tool_name: ToolSecurity}` mapping;
`server.py` merges them with a pure `build_tool_security()` into a module-level
`TOOL_SECURITY`. `ToolAnnotations` are produced by `annotations_for(effect)` at registration
instead of being passed in.

**Tech stack.** Python 3.11+, `fastmcp`, `mcp.types.ToolAnnotations`, `pytest`, `uv`, `just`.
No new dependency.

Design: [spec](../specs/2026-08-18-tool-security-metadata-design.md),
[ADR 0035](../../adr/0035-enforceable-tool-security-metadata.md). Issue
[#219](https://github.com/randomparity/hmc-mcp/issues/219), epic
[#218](https://github.com/randomparity/hmc-mcp/issues/218).

## Global constraints

Binding on every task below.

- Branch `feat/tool-security-metadata-219`, base `main`. Never commit to `main`.
- Guardrail: `just verify` — run **bare**, no pipes, no `|| true`. It expands to `static`
  (lint, typecheck, secrets, workflow-security, env-vars, nicknames), `test`, `smoke`,
  `build`, `verify-artifacts`, plus CLI group help checks. Focused runs during a task use
  `uv run pytest -q --no-cov <path>` — `pyproject.toml` sets `--cov` with `fail_under = 90`,
  so a focused run without `--no-cov` exits 1 on coverage and hides the real result.
  `just verify` gates the branch before push.
- Non-interactive shells only: `GIT_EDITOR=true`, `git --no-pager`, `gh` always with
  `--title`/`--body`.
- Conventional commits, imperative mood, ≤72-char subject, one logical change per commit.
- ≤100 lines per function, cyclomatic complexity ≤8, 100-char lines. Zero tool warnings.
- A test that needs a credential-free `HMCConfig` passes `_env_file=None`; `monkeypatch.delenv`
  cannot suppress a local `.env`.
- No new runtime dependency in `pyproject.toml`.
- The effect vocabulary is exactly `read`, `mutate`, `destructive`, `arbitrary-command`
  (epic #218 requirement 1). Do not add a fifth value.
- The `TargetKind` vocabulary is exactly `none`, `console`, `managed_system`, `lpar`, `vios`,
  `cluster`, `shared_storage_pool`, `user`, `password_policy`, `job`, `template`,
  `metric_resource`.
- `REQUIRED_TARGET_ARGUMENTS` is exactly the fourteen rows given in Task 1 step 3. Do not add
  `name` (it means a user on `hmc_create_user` and a new partition on `hmc_create_lpar`), and
  do not add sub-resource arguments (`vg_uuid`, `adapter_uuid`, `mapping_uuid`,
  `network_uuid`, `lu_udid`, `job_href`).
- `required` is computed as `param.default is inspect.Parameter.empty` — *has no default*,
  not *defaults to None*.
- The live registry has **128** collector-registered tools across 19 domain modules, plus
  `hmc_run_command`, which registers only when the operator enables it. Post-change census
  with the escape hatch disabled: 54 `read`, 46 `mutate`, 28 `destructive` (the two install
  tools were reclassified from `mutate` during branch review; see ADR 0035).
- **Tasks 1–3 and Task 5 land as one commit.** Replacing the collector signature, declaring
  every tool, and deleting the frozensets is one atomic change: any split leaves a commit
  where `import hmc_mcp.server` fails or `tests/app/test_capabilities.py` errors at
  collection, which is hostile to `git bisect`. Work the tasks in order and keep the TDD
  rhythm inside them; defer `git commit` until Task 5 is done and the suite is green.
  Tasks 4 and 6 commit separately.
- The six prek hooks all declare `pass_filenames: false`, so `prek run --files <paths>` runs
  them repo-wide regardless. Use `prek run` and read the whole result.

---

## Task 1 — the registry contract

**Creates/modifies:** `src/hmc_mcp/tool_registry.py`.
**Tests:** `tests/unit/test_tool_registry.py` (rewritten).

**Interfaces this task publishes** (every later task depends on these exact signatures):

```python
Effect = Literal["read", "mutate", "destructive", "arbitrary-command"]
TargetKind = Literal[
    "none", "console", "managed_system", "lpar", "vios", "cluster",
    "shared_storage_pool", "user", "password_policy", "job", "template",
    "metric_resource",
]
EFFECTS: frozenset[str]
TARGET_KINDS: frozenset[str]
REQUIRED_TARGET_ARGUMENTS: Mapping[str, TargetKind]

@dataclass(frozen=True)
class TargetSelector:
    kind: TargetKind
    argument: str
    required: bool

@dataclass(frozen=True)
class ToolSecurity:
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

def annotations_for(effect: Effect) -> ToolAnnotations: ...
def build_targets(
    handler: Callable[..., Any],
    extra_targets: Iterable[tuple[TargetKind, str]],
) -> tuple[TargetSelector, ...]: ...
def validate_security(security: ToolSecurity, handler: Callable[..., Any]) -> None: ...
def tool_module() -> tuple[Callable, Callable[[FastMCP], None], Callable[[], Mapping[str, ToolSecurity]]]: ...
```

`tool()`'s signature, keyword-only:

```python
tool(*, effect, operation, target_kind, extra_targets=(), connection_argument="profile")
```

`extra_targets` is a tuple of `(kind, argument)` 2-tuples, typed
`Iterable[tuple[TargetKind, str]]`. The narrow element type is required, not cosmetic: with
`tuple[str, str]` the repo's `ty` check rejects the `TargetSelector(kind, ...)` construction
inside `build_targets`.

### Steps

1. **Write the failing tests.** Replace `tests/unit/test_tool_registry.py` wholesale:

```python
"""Direct contract tests for module-local MCP tool collection."""

from __future__ import annotations

import asyncio
import inspect

import pytest

from hmc_mcp._app import create_mcp
from hmc_mcp.tool_registry import (
    ToolSecurity,
    annotations_for,
    build_tool_security,
    tool_module,
    validate_security,
)


def _tool_names(application) -> set[str]:
    return {tool.name for tool in asyncio.run(application.list_tools())}


def test_tool_modules_collect_definitions_in_isolation():
    first_tool, first_register, _ = tool_module()
    second_tool, second_register, _ = tool_module()

    @first_tool(effect="read", operation="first.read", target_kind="console",
                connection_argument=None)
    def first_handler() -> str:
        return "first"

    @second_tool(effect="read", operation="second.read", target_kind="console",
                 connection_argument=None)
    def second_handler() -> str:
        return "second"

    first_application = create_mcp()
    second_application = create_mcp()
    first_register(first_application)
    second_register(second_application)

    assert _tool_names(first_application) == {"first_handler"}
    assert _tool_names(second_application) == {"second_handler"}


def test_tool_module_derives_annotations_and_preserves_handler():
    tool, register_tools, security = tool_module()

    @tool(effect="read", operation="status.read", target_kind="console",
          connection_argument=None)
    def status() -> str:
        return "ok"

    application = create_mcp()
    register_tools(application)
    registered = asyncio.run(application.list_tools())

    assert status() == "ok"
    assert len(registered) == 1
    assert registered[0].name == "status"
    assert registered[0].annotations == annotations_for("read")
    assert security()["status"].operation == "status.read"


def test_same_definitions_register_on_independent_applications():
    tool, register_tools, _ = tool_module()

    @tool(effect="read", operation="ping.read", target_kind="console",
          connection_argument=None)
    def ping() -> str:
        return "pong"

    first = create_mcp()
    second = create_mcp()
    register_tools(first)
    register_tools(second)

    assert _tool_names(first) == {"ping"}
    assert _tool_names(second) == {"ping"}


def test_targets_are_built_from_the_argument_table():
    tool, _register, security = tool_module()

    @tool(effect="mutate", operation="lpar.migrate", target_kind="lpar")
    def migrate(
        lpar_name_or_uuid: str,
        target_system_name_or_uuid: str,
        system_name_or_uuid: str | None = None,
        profile: str | None = None,
    ) -> str:
        return "ok"

    targets = security()["migrate"].targets
    assert [(t.kind, t.argument, t.required) for t in targets] == [
        ("lpar", "lpar_name_or_uuid", True),
        ("managed_system", "target_system_name_or_uuid", True),
        ("managed_system", "system_name_or_uuid", False),
    ]


def test_extra_targets_supply_a_kind_the_table_cannot_name():
    tool, _register, security = tool_module()

    @tool(effect="destructive", operation="user.delete", target_kind="user",
          extra_targets=(("user", "name"),))
    def remove_user(name: str, profile: str | None = None) -> str:
        return "ok"

    targets = security()["remove_user"].targets
    assert [(t.kind, t.argument, t.required) for t in targets] == [("user", "name", True)]


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"effect": "nonsense", "operation": "a.b", "target_kind": "console"}, "effect"),
        ({"effect": "read", "operation": "a.b", "target_kind": "nowhere"}, "target_kind"),
        ({"effect": "read", "operation": "nodot", "target_kind": "console"}, "operation"),
        (
            {"effect": "read", "operation": "a.b", "target_kind": "console",
             "extra_targets": (("lpar", "absent_argument"),)},
            "absent_argument",
        ),
        (
            {"effect": "read", "operation": "a.b", "target_kind": "console",
             "connection_argument": "absent_profile"},
            "absent_profile",
        ),
        ({"effect": "read", "operation": "a.b", "target_kind": "none"}, "none"),
        ({"effect": "read", "operation": "a.b", "target_kind": "user"}, "user"),
        (
            {"effect": "read", "operation": "a.b", "target_kind": "console",
             "extra_targets": (("lpar", "lpar_name_or_uuid"),)},
            "lpar_name_or_uuid",
        ),
    ],
    ids=["v2-effect", "v3-kind", "v4-operation", "v5-extra-arg", "v6-connection",
         "v7-none", "v8-no-subject", "v9-duplicate"],
)
def test_tool_rejects_contradictory_declarations(kwargs, message):
    tool, _register, _security = tool_module()

    with pytest.raises(ValueError, match=message):

        @tool(**kwargs)
        def sample(lpar_name_or_uuid: str, profile: str | None = None) -> str:
            return "ok"


def test_tool_requires_the_three_mandatory_fields():
    tool, _register, _security = tool_module()

    with pytest.raises(TypeError):

        @tool(effect="read")
        def sample(profile: str | None = None) -> str:
            return "ok"


def test_annotations_cover_exactly_the_effect_vocabulary():
    assert annotations_for("read").readOnlyHint is True
    assert annotations_for("mutate").readOnlyHint is False
    assert annotations_for("mutate").destructiveHint is None
    assert annotations_for("destructive").destructiveHint is True
    assert annotations_for("destructive").readOnlyHint is False
    assert annotations_for("arbitrary-command").destructiveHint is True
    with pytest.raises(KeyError):
        annotations_for("invented")


def test_required_uses_absence_of_a_default_not_a_none_default():
    tool, _register, security = tool_module()

    @tool(effect="read", operation="pinned.read", target_kind="managed_system")
    def pinned(system_name_or_uuid: str = "Server-1", profile: str | None = None) -> str:
        return "ok"

    selector = security()["pinned"].targets[0]
    assert selector.required is False
    assert inspect.signature(pinned).parameters["system_name_or_uuid"].default == "Server-1"


def test_build_tool_security_rejects_duplicate_names_and_operations():
    console = ToolSecurity(effect="read", operation="a.read", target_kind="console")
    other = ToolSecurity(effect="read", operation="b.read", target_kind="console")

    with pytest.raises(ValueError, match="duplicate tool name"):
        build_tool_security([{"one": console}, {"one": other}], {})

    with pytest.raises(ValueError, match="duplicate operation"):
        build_tool_security([{"one": console}, {"two": console}], {})

    with pytest.raises(ValueError, match="duplicate tool name"):
        build_tool_security([{"one": console}], {"one": other})


def test_build_tool_security_merges_modules_and_extras():
    first = ToolSecurity(effect="read", operation="a.read", target_kind="console")
    second = ToolSecurity(effect="mutate", operation="b.write", target_kind="console")

    index = build_tool_security([{"one": first}], {"two": second})

    assert index == {"one": first, "two": second}


def test_validate_security_accepts_a_console_declaration_with_no_targets():
    def handler(profile: str | None = None) -> str:
        return "ok"

    validate_security(
        ToolSecurity(effect="read", operation="console.info", target_kind="console"),
        handler,
    )
```

2. **Run it and confirm it fails.**
   `uv run pytest -q --no-cov tests/unit/test_tool_registry.py`
   Expect a collection error: `ImportError: cannot import name 'ToolSecurity' from
   'hmc_mcp.tool_registry'`.

3. **Write `src/hmc_mcp/tool_registry.py`** in full:

```python
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
    """Return the MCP annotations for an effect class."""
    return _ANNOTATIONS[effect]


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
    return index


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
            security = ToolSecurity(
                effect=effect,
                operation=operation,
                target_kind=target_kind,
                connection_argument=connection_argument,
            )
            security = replace(security, targets=build_targets(fn, extra_targets))
            validate_security(security, fn)
            name = getattr(fn, "__name__", "<handler>")
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
        return {
            definition.name: definition.security for definition in definitions
        }

    return tool, register_tools, tool_security
```

4. **Run the tests and confirm they pass.**
   `uv run pytest -q --no-cov tests/unit/test_tool_registry.py`
   Expect all cases green. Every other suite is still red at this point — the domain
   modules have not been updated — which Task 3 fixes.

5. **Do not commit yet.** The repo does not import until Task 3 lands; see the Global
   Constraints note on commit sequencing.

**Acceptance criteria.** `tests/unit/test_tool_registry.py` passes. `tool()` raises on each of
V2–V9 with a message naming the tool, and `TypeError` when a mandatory field is missing.
`build_targets` returns table matches in signature order followed by extras.
`annotations_for` covers exactly the four effect values and raises `KeyError` otherwise.

---

## Task 2 — the escape hatch and the composed index

**Modifies:** `src/hmc_mcp/server_command.py`, `src/hmc_mcp/server.py`.

**Interfaces consumed:** `ToolSecurity`, `annotations_for`, `validate_security`,
`build_tool_security` from Task 1.
**Interfaces published:** `server.TOOL_SECURITY: Mapping[str, ToolSecurity]` and
`server_command.HMC_RUN_COMMAND_SECURITY: ToolSecurity`.

### Steps

1. **Rewrite the head of `src/hmc_mcp/server_command.py`.** Replace the `_STATE_CHANGING`
   import and the `mcp.tool(...)` call:

```python
"""Opt-in MCP escape hatch for arbitrary HMC CLI commands."""

from __future__ import annotations

from fastmcp import FastMCP

from ._app import _run
from .common import build_config
from .ssh import run_hmc_cli
from .tool_registry import ToolSecurity, annotations_for, validate_security


def hmc_run_command(cmd: str, profile: str | None = None) -> str:
    """Execute an arbitrary HMC CLI command over SSH.

    This operator escape hatch can change HMC state. Prefer a dedicated tool
    when one exists. SSH authentication comes from the selected HMC profile.

    Args:
        cmd: Complete HMC CLI command to execute without shell mediation.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    config = build_config(profile=profile)
    return _run(lambda: run_hmc_cli(cmd, config))


HMC_RUN_COMMAND_SECURITY = ToolSecurity(
    effect="arbitrary-command",
    operation="command.run",
    target_kind="console",
)
validate_security(HMC_RUN_COMMAND_SECURITY, hmc_run_command)


async def configure_arbitrary_command_tool(enabled: bool, mcp: FastMCP) -> None:
    """Make escape-hatch registration match the requested capability state."""
    registered = await mcp.local_provider.get_tool("hmc_run_command") is not None
    if enabled and not registered:
        mcp.tool(
            hmc_run_command,
            annotations=annotations_for(HMC_RUN_COMMAND_SECURITY.effect),
        )
    elif not enabled and registered:
        mcp.local_provider.remove_tool("hmc_run_command")
```

2. **Wire the index in `src/hmc_mcp/server.py`.** Immediately after the `TOOL_MODULES` tuple
   and before `create_mcp()`, add:

```python
TOOL_SECURITY: Mapping[str, ToolSecurity] = build_tool_security(
    [module.tool_security() for module in TOOL_MODULES],
    {"hmc_run_command": HMC_RUN_COMMAND_SECURITY},
)
```

   Add the imports `from collections.abc import Mapping`, `from .tool_registry import
   ToolSecurity, build_tool_security`, and `from .server_command import
   HMC_RUN_COMMAND_SECURITY` alongside the existing `configure_arbitrary_command_tool`
   import. Delete the `DESTRUCTIVE_TOOLS as DESTRUCTIVE_TOOLS` and `READ_ONLY_TOOLS as
   READ_ONLY_TOOLS` lines from the `_app` re-export block. Leave `create_mcp()` registering
   only — it must not build or mutate the index.

3. **Verify the module imports.** This still fails until Task 3 lands, because the 19 domain
   modules still unpack a two-tuple: `uv run python -c "import hmc_mcp.server"` raises
   `ValueError: too many values to unpack (expected 2)` at the first
   `tool, register_tools = tool_module()`, before any decorator is evaluated. That exact
   error is the signal to proceed; anything else is a real defect. Do not commit here —
   see the Global Constraints note on commit sequencing.

**Acceptance criteria.** `HMC_RUN_COMMAND_SECURITY` is validated at import.
`configure_arbitrary_command_tool` derives its annotation. `server.py` builds `TOOL_SECURITY`
at module scope from collected declarations, including the escape hatch unconditionally, and
no longer re-exports the frozensets.

---

## Task 3 — declare every tool and delete the legacy sets

**Modifies:** the 19 `src/hmc_mcp/server_*.py` domain modules, `src/hmc_mcp/_app.py`,
`README.md`.

**Interfaces consumed:** `tool_module()`'s three-tuple from Task 1.

### Steps

1. **In each domain module**, change the unpack from
   `tool, register_tools = tool_module()` to
   `tool, register_tools, tool_security = tool_module()`, and delete the now-unused
   `_READ_ONLY` / `_DESTRUCTIVE` / `_STATE_CHANGING` names from its `._app` import.

2. **Rewrite every `@tool` decorator** using the tables in step 5. Each becomes:

```python
@tool(effect="<effect>", operation="<operation>", target_kind="<target_kind>")
```

   with `extra_targets=(("user", "name"),)` added on the four user tools, and
   `connection_argument=None` added on `hmc_list_configured_hosts`. Nothing else changes —
   no handler body, signature, or docstring is touched.

3. **Delete from `src/hmc_mcp/_app.py`**: the `from mcp.types import ToolAnnotations` import,
   the comment block at lines 124–130, `_READ_ONLY`, `_DESTRUCTIVE`, `_STATE_CHANGING`,
   `READ_ONLY_TOOLS`, and `DESTRUCTIVE_TOOLS`. Nothing else in that file changes.

4. **Update `README.md:612`** from
   `_app.py        # shared FastMCP instance, READ_ONLY/DESTRUCTIVE_TOOLS sets, entry points`
   to
   `_app.py        # shared FastMCP instance, sync-run and SSH helpers, entry points`.

5. **The per-tool assignment.** `target_kind` is the kind of the resource whose state
   changes — for a creation tool, the container that gains it. Target selectors are built,
   never written.

   `server_adapters.py` — all `target_kind="lpar"`:
   `hmc_list_adapters` read `adapter.list`; `hmc_add_network_adapter` mutate
   `adapter.add_network`; `hmc_add_vscsi_adapter` mutate `adapter.add_vscsi`;
   `hmc_add_vfc_adapter` mutate `adapter.add_vfc`; `hmc_delete_adapter` destructive
   `adapter.delete`.

   `server_capacity.py` — all `read`, `target_kind="console"`:
   `hmc_capacity_report` `capacity.report`; `hmc_find_placement` `placement.find`.

   `server_composite.py` — both `read`: `hmc_lpar_summary` `lpar.summary` `lpar`;
   `hmc_system_summary` `system.summary` `managed_system`.

   `server_health.py` — `hmc_fleet_health` read `health.fleet` `console`.

   `server_jobs.py` — all `read`: `hmc_get_job` `job.get` `job`; `hmc_list_recent_jobs`
   `job.list` `console`; `hmc_wait_for_job` `job.wait` `job`.

   `server_lpar_config.py` — all `target_kind="lpar"`:
   `hmc_get_lpar_description` read `lpar.get_description`; `hmc_set_lpar_description` mutate
   `lpar.set_description`; `hmc_get_lpar_msp` read `lpar.get_msp`; `hmc_set_lpar_msp` mutate
   `lpar.set_msp`; `hmc_get_lpar_proc_compat` read `lpar.get_proc_compat`;
   `hmc_set_lpar_proc_compat` mutate `lpar.set_proc_compat`.

   `server_lpars.py`: `hmc_create_lpar` mutate `lpar.create` `managed_system`;
   `hmc_modify_lpar` mutate `lpar.modify` `lpar`; `hmc_rename_lpar` mutate `lpar.rename`
   `lpar`; `hmc_dlpar_proc` mutate `lpar.dlpar_proc` `lpar`; `hmc_dlpar_mem` mutate
   `lpar.dlpar_mem` `lpar`; `hmc_delete_lpar` destructive `lpar.delete` `lpar`;
   `hmc_decommission_lpar` destructive `lpar.decommission` `lpar`; `hmc_power_on_lpar` mutate
   `lpar.power_on` `lpar`; `hmc_power_off_lpar` destructive `lpar.power_off` `lpar`;
   `hmc_read_lpar_boot_order` **read** `boot_order.read` `lpar`; `hmc_set_lpar_boot_order`
   mutate `boot_order.set` `lpar`; `hmc_clear_lpar_boot_order` mutate `boot_order.clear`
   `lpar`.

   `server_lpm.py` — all `target_kind="lpar"`: `hmc_migrate_lpar` mutate `lpar.migrate`;
   `hmc_migrate_validate_lpar` mutate `lpar.migrate_validate`; `hmc_migrate_abort_lpar`
   destructive `lpar.migrate_abort`; `hmc_migrate_recover_lpar` mutate
   `lpar.migrate_recover`; `hmc_remote_restart_lpar` destructive `lpar.remote_restart`.

   `server_metrics.py` — all `target_kind="metric_resource"`: `hmc_get_pcm_preferences` read
   `pcm.get_preferences`; `hmc_set_pcm_preferences` mutate `pcm.set_preferences`;
   `hmc_processed_metrics` read `metrics.processed`; `hmc_processed_metric_links` read
   `metrics.processed_links`; `hmc_aggregated_metrics` read `metrics.aggregated`;
   `hmc_aggregated_metric_links` read `metrics.aggregated_links`.

   `server_network.py`: `hmc_list_virtual_switches` read `network.list_switches`
   `managed_system`; `hmc_list_virtual_networks` read `network.list_networks`
   `managed_system`; `hmc_create_virtual_network` mutate `network.create_network`
   `managed_system`; `hmc_delete_virtual_network` destructive `network.delete_network`
   `managed_system`; `hmc_list_network_bridges` read `network.list_bridges`
   `managed_system`; `hmc_list_fc_ports` read `network.list_fc_ports` `managed_system`;
   `hmc_list_sea_adapters` read `network.list_sea` `managed_system`;
   `hmc_set_sriov_adapter_mode` mutate `sriov.set_mode` `managed_system`; `hmc_list_vnics`
   read `vnic.list` `lpar`; `hmc_add_vnic` mutate `vnic.add` `lpar`; `hmc_remove_vnic`
   destructive `vnic.remove` `lpar`.

   `server_profiles.py`: `hmc_backup_lpar_profiles` destructive `lpar_profile.backup`
   `managed_system`; `hmc_restore_lpar_profiles` destructive `lpar_profile.restore`
   `managed_system`; `hmc_sync_lpar_profile` destructive `lpar_profile.sync` `lpar`;
   `hmc_assign_profile_io_slot` mutate `lpar_profile.assign_io_slot` `lpar`.

   `server_provision.py` — `hmc_provision_lpar` mutate `provision.lpar` `managed_system`.

   `server_storage.py`: `hmc_list_volume_groups` read `storage.list_volume_groups` `vios`;
   `hmc_create_volume_group` mutate `storage.create_volume_group` `vios`;
   `hmc_attach_disk_to_lpar` mutate `storage.attach_disk` `lpar`; `hmc_create_virtual_disk`
   mutate `storage.create_disk` `vios`; `hmc_delete_virtual_disk` destructive
   `storage.delete_disk` `vios`; `hmc_map_storage_to_lpar` mutate `storage.map` `vios`;
   `hmc_create_media_repository` mutate `media.create_repository` `vios`;
   `hmc_create_optical_media` mutate `media.create` `vios`; `hmc_delete_media_repository`
   destructive `media.delete_repository` `vios`; `hmc_delete_optical_media` destructive
   `media.delete` `vios`; `hmc_get_media_repository` read `media.get_repository` `vios`;
   `hmc_list_optical_media` read `media.list` `vios`; `hmc_list_storage_mappings` read
   `storage.list_mappings` `vios`; `hmc_detach_storage_mapping` destructive
   `storage.detach_mapping` `vios`; `hmc_list_clusters` read `cluster.list` `console`;
   `hmc_list_shared_storage_pools` read `cluster.list_pools` `console`;
   `hmc_get_shared_storage_pool` read `cluster.get_pool` `shared_storage_pool`;
   `hmc_create_logical_unit` mutate `cluster.create_logical_unit` `cluster`;
   `hmc_delete_logical_unit` destructive `cluster.delete_logical_unit` `cluster`;
   `hmc_upload_iso` mutate `media.upload_iso` `vios`; `hmc_list_optical_mappings` read
   `media.list_mappings` `vios`; `hmc_mount_optical_media` mutate `media.mount` `vios`;
   `hmc_unmount_optical_media` destructive `media.unmount` `vios`;
   `hmc_detach_optical_mapping` destructive `media.detach_mapping` `vios`.

   `server_system_resources.py` — all `target_kind="managed_system"`:
   `hmc_get_proc_compat_modes` read `system.get_proc_compat_modes`; `hmc_list_io_slots` read
   `io_slot.list`; `hmc_list_memory_pools` read `memory_pool.list`; `hmc_remove_memory_pool`
   destructive `memory_pool.remove`.

   `server_systems.py`: `hmc_console_info` read `console.info` `console`;
   `hmc_list_configured_hosts` read `config.list_hosts` **`none`** with
   `connection_argument=None`; `hmc_list_systems` read `system.list` `console`;
   `hmc_list_lpars` read `lpar.list` `managed_system`; `hmc_get_lpar` read `lpar.get` `lpar`;
   `hmc_get_lpar_state` read `lpar.get_state` `lpar`; `hmc_list_vios` read `vios.list`
   `managed_system`; `hmc_get_vios` read `vios.get` `vios`; `hmc_list_resources` read
   `console.list_resources` `console`; `hmc_get_system` read `system.get` `managed_system`;
   `hmc_modify_system` mutate `system.modify` `managed_system`; `hmc_power_on_system` mutate
   `system.power_on` `managed_system`; `hmc_power_off_system` destructive
   `system.power_off` `managed_system`.

   `server_templates.py`: `hmc_list_partition_templates` read `template.list` `console`;
   `hmc_get_partition_template` read `template.get` `template`;
   `hmc_deploy_partition_template` mutate `template.deploy` `managed_system`.

   `server_updates.py`: `hmc_update_console_software` mutate `update.console` `console`;
   `hmc_get_available_hmc_ptfs` read `update.list_ptfs` `console`; `hmc_vios_update` mutate
   `update.vios` `vios`; `hmc_update_firmware` mutate `update.firmware` `managed_system`.

   `server_users.py`: `hmc_list_users` read `user.list` `console`; `hmc_get_user` read
   `user.get` `user` **+ `extra_targets=(("user", "name"),)`**; `hmc_create_user` mutate
   `user.create` `user` **+ extra_targets**; `hmc_modify_user` mutate `user.modify` `user`
   **+ extra_targets**; `hmc_delete_user` destructive `user.delete` `user` **+
   extra_targets**; `hmc_list_password_policies` read `policy.list` `console`;
   `hmc_list_password_policy_status` read `policy.status` `console`;
   `hmc_create_password_policy` mutate `policy.create` `password_policy`;
   `hmc_modify_password_policy` mutate `policy.modify` `password_policy`;
   `hmc_delete_password_policy` destructive `policy.delete` `password_policy`;
   `hmc_get_ldap_config` read `ldap.get` `console`; `hmc_configure_ldap` mutate
   `ldap.configure` `console`; `hmc_remove_ldap_config` destructive `ldap.remove` `console`.

   `server_vios.py`: `hmc_create_vios` mutate `vios.create` `managed_system`;
   `hmc_delete_vios` destructive `vios.delete` `vios`; `hmc_install_vios` mutate
   `vios.install` `vios`; `hmc_install_lpar_os` mutate `lpar.install_os` `lpar`;
   `hmc_list_vios_backups` read `vios.list_backups` `vios`; `hmc_backup_vios` mutate
   `vios.backup` `vios`; `hmc_restore_vios` destructive `vios.restore` `vios`;
   `hmc_power_on_vios` mutate `vios.power_on` `vios`; `hmc_power_off_vios` destructive
   `vios.power_off` `vios`.

6. **Fold in Task 5 now** — `tests/app/test_capabilities.py` imports the frozensets this
   task deletes, so it must be fixed before the suite can run. Do Task 5's five steps, then
   return here.

7. **Verify the import and the census.**
   `uv run python scripts/smoke_mcp.py` — expect it to complete without error.
   Then:
   `uv run python -c "import collections, asyncio; from hmc_mcp.server import mcp,
   TOOL_SECURITY; names={t.name for t in asyncio.run(mcp.list_tools())};
   print(len(names), collections.Counter(TOOL_SECURITY[n].effect for n in names))"`
   Expect `128 Counter({'read': 54, 'mutate': 48, 'destructive': 26})`.

8. **Run the whole suite.** `uv run pytest -q` — expect green, including the coverage gate.

9. **Lint.** `prek run`. Expect all six hooks `Passed`.

10. **Commit Tasks 1, 2, 3, and 5 together.**
    `git commit -m "feat(tools): enforce security metadata on every MCP tool"`

**Acceptance criteria.** `hmc_mcp.server` imports cleanly. The smoke path runs. The census is
54/48/26 over 128 tools. `_app.py` no longer defines the annotation constants or the
frozensets, and `grep -rn 'READ_ONLY_TOOLS\|DESTRUCTIVE_TOOLS\|_STATE_CHANGING' src/`
returns nothing.

---

## Task 4 — the exhaustive registry contract test

**Creates:** `tests/app/test_tool_security.py`.

**Interfaces consumed:** `server.TOOL_SECURITY`, `server.mcp`,
`server_command.HMC_RUN_COMMAND_SECURITY`, `tool_registry.annotations_for`,
`tool_registry.REQUIRED_TARGET_ARGUMENTS`, `tool_registry.validate_security`.

This is the guardrail issue #219 exists to add. It covers G1, G3, G4, G6, G7, G8, G10, G11.

### Steps

1. **Write the test module.** `LEGACY_READ_ONLY` and `LEGACY_DESTRUCTIVE` are the 53 and 26
   names from `_app.py` as they stood on `main` at 20f3068, transcribed literally; they are a
   frozen regression snapshot, not a list to maintain. A new tool is not added to them — it
   is covered by G1, G4, and G7.

```python
"""Exhaustive contract tests for the live tool security classification.

Every registered MCP tool must carry one authoritative ToolSecurity record, and
the MCP annotations shipped to clients must be derived from it. These tests fail
when a tool omits the metadata, contradicts its handler, or silently changes
classification. See docs/adr/0035-enforceable-tool-security-metadata.md.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from hmc_mcp import server_command
from hmc_mcp.server import TOOL_SECURITY, mcp
from hmc_mcp.tool_registry import (
    EFFECTS,
    REQUIRED_TARGET_ARGUMENTS,
    annotations_for,
    validate_security,
)

# The classification as it stood before ADR 0035, transcribed from _app.py at
# 20f3068. A frozen regression snapshot: no tool is ever added here.
LEGACY_READ_ONLY = frozenset({
    "hmc_console_info", "hmc_list_systems", "hmc_system_summary", "hmc_list_lpars",
    "hmc_get_lpar", "hmc_get_lpar_state", "hmc_lpar_summary", "hmc_list_vios",
    "hmc_get_vios", "hmc_list_resources", "hmc_get_job", "hmc_list_recent_jobs",
    "hmc_fleet_health", "hmc_capacity_report", "hmc_find_placement", "hmc_get_system",
    "hmc_wait_for_job", "hmc_list_adapters", "hmc_list_configured_hosts",
    "hmc_list_volume_groups", "hmc_list_virtual_switches", "hmc_list_virtual_networks",
    "hmc_list_network_bridges", "hmc_list_fc_ports", "hmc_list_sea_adapters",
    "hmc_list_partition_templates", "hmc_get_partition_template", "hmc_list_clusters",
    "hmc_list_shared_storage_pools", "hmc_get_shared_storage_pool",
    "hmc_get_pcm_preferences", "hmc_processed_metrics", "hmc_processed_metric_links",
    "hmc_aggregated_metrics", "hmc_aggregated_metric_links", "hmc_list_users",
    "hmc_get_user", "hmc_list_password_policies", "hmc_list_password_policy_status",
    "hmc_get_ldap_config", "hmc_get_available_hmc_ptfs", "hmc_list_vios_backups",
    "hmc_get_lpar_description", "hmc_get_lpar_msp", "hmc_get_proc_compat_modes",
    "hmc_get_lpar_proc_compat", "hmc_list_io_slots", "hmc_list_memory_pools",
    "hmc_list_vnics", "hmc_get_media_repository", "hmc_list_optical_media",
    "hmc_list_optical_mappings", "hmc_list_storage_mappings",
})

LEGACY_DESTRUCTIVE = frozenset({
    "hmc_power_off_lpar", "hmc_delete_lpar", "hmc_decommission_lpar", "hmc_delete_vios",
    "hmc_delete_adapter", "hmc_delete_virtual_network", "hmc_delete_media_repository",
    "hmc_delete_optical_media", "hmc_delete_virtual_disk", "hmc_delete_logical_unit",
    "hmc_delete_user", "hmc_delete_password_policy", "hmc_remove_ldap_config",
    "hmc_remove_memory_pool", "hmc_remove_vnic", "hmc_power_off_system",
    "hmc_power_off_vios", "hmc_migrate_abort_lpar", "hmc_remote_restart_lpar",
    "hmc_restore_vios", "hmc_restore_lpar_profiles", "hmc_backup_lpar_profiles",
    "hmc_sync_lpar_profile", "hmc_unmount_optical_media", "hmc_detach_optical_mapping",
    "hmc_detach_storage_mapping",
})


def _tools_by_name(enable_arbitrary_command: bool = False):
    asyncio.run(
        server_command.configure_arbitrary_command_tool(enable_arbitrary_command, mcp)
    )
    try:
        return {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    finally:
        if enable_arbitrary_command:
            asyncio.run(server_command.configure_arbitrary_command_tool(False, mcp))


def test_every_live_tool_has_security_metadata():
    """G1: no live tool escapes the classification, toggle on or off."""
    assert set(_tools_by_name()) <= set(TOOL_SECURITY)
    assert set(_tools_by_name(True)) == set(TOOL_SECURITY)
    assert "hmc_run_command" in TOOL_SECURITY


def test_annotations_are_derived_from_the_effect_class():
    """G4: the shipped hint is a function of the declared effect, nothing else."""
    for name, tool in _tools_by_name(True).items():
        assert tool.annotations == annotations_for(TOOL_SECURITY[name].effect), name


def test_declared_effects_use_the_closed_vocabulary():
    for name, security in TOOL_SECURITY.items():
        assert security.effect in EFFECTS, name


def test_selectors_and_connection_arguments_are_public_parameters():
    """G3: every declared selector is really an argument a caller supplies."""
    for name, tool in _tools_by_name(True).items():
        security = TOOL_SECURITY[name]
        properties = set(tool.parameters.get("properties", {}))
        for target in security.targets:
            assert target.argument in properties, (name, target.argument)
        if security.connection_argument is not None:
            assert security.connection_argument in properties, name


@pytest.mark.parametrize(
    "tool_name, argument, expected_required",
    [
        ("hmc_power_off_lpar", "system_name_or_uuid", False),
        ("hmc_power_off_vios", "system_name_or_uuid", False),
        ("hmc_delete_vios", "system_name_or_uuid", False),
        ("hmc_restore_vios", "system_name_or_uuid", False),
        ("hmc_list_lpars", "system_name_or_uuid", False),
        ("hmc_delete_lpar", "lpar_name_or_uuid", True),
        ("hmc_migrate_lpar", "target_system_name_or_uuid", True),
    ],
)
def test_optional_selectors_are_marked_optional(tool_name, argument, expected_required):
    """G3 anchors: #223 must not deny a call that legitimately omits a selector."""
    selector = next(
        target
        for target in TOOL_SECURITY[tool_name].targets
        if target.argument == argument
    )
    assert selector.required is expected_required


def test_multi_kind_tools_declare_every_target():
    """G6: the omission the argument table exists to make impossible."""
    migrate = {(t.kind, t.argument) for t in TOOL_SECURITY["hmc_migrate_lpar"].targets}
    assert ("lpar", "lpar_name_or_uuid") in migrate
    assert ("managed_system", "target_system_name_or_uuid") in migrate

    attach = {(t.kind, t.argument) for t in TOOL_SECURITY["hmc_attach_disk_to_lpar"].targets}
    assert ("lpar", "lpar_name_or_uuid") in attach
    assert ("vios", "vios_uuid") in attach


def test_target_declarations_are_internally_consistent():
    """G6: V7 and V8 hold across the whole live registry."""
    for name, tool in _tools_by_name(True).items():
        security = TOOL_SECURITY[name]
        if security.target_kind == "none":
            assert security.targets == (), name
            assert security.connection_argument is None, name
            continue
        if security.target_kind != "console":
            assert any(t.kind == security.target_kind for t in security.targets), name
        arguments = [t.argument for t in security.targets]
        assert len(arguments) == len(set(arguments)), name


def test_every_table_argument_becomes_a_target():
    """G6: a handler taking an identity argument cannot silently drop it."""
    for name, tool in _tools_by_name(True).items():
        declared = {t.argument for t in TOOL_SECURITY[name].targets}
        expected = set(tool.parameters.get("properties", {})) & set(
            REQUIRED_TARGET_ARGUMENTS
        )
        assert expected <= declared, (name, sorted(expected - declared))


def test_operation_identities_are_unique():
    """G2: two tools may not claim one operation."""
    operations = [security.operation for security in TOOL_SECURITY.values()]
    assert len(operations) == len(set(operations))


def test_delete_and_remove_tools_are_destructive():
    """G7: the likeliest misclassification, caught by name."""
    for name, security in TOOL_SECURITY.items():
        if name.startswith(("hmc_delete_", "hmc_remove_")):
            assert security.effect == "destructive", name


def test_arbitrary_command_is_absent_by_default_and_maximally_classified():
    """G8: the escape hatch is off by default and is the only arbitrary command."""
    default = _tools_by_name()
    assert "hmc_run_command" not in default
    assert not [n for n in default if TOOL_SECURITY[n].effect == "arbitrary-command"]

    enabled = _tools_by_name(True)
    assert [n for n in enabled if TOOL_SECURITY[n].effect == "arbitrary-command"] == [
        "hmc_run_command"
    ]
    validate_security(
        server_command.HMC_RUN_COMMAND_SECURITY, server_command.hmc_run_command
    )


def test_only_the_local_config_tool_opens_no_hmc_connection():
    """G10: every tool that reaches an HMC declares how its connection is chosen."""
    for name, security in TOOL_SECURITY.items():
        if name == "hmc_list_configured_hosts":
            assert security.target_kind == "none", name
            assert security.connection_argument is None, name
        else:
            assert security.connection_argument == "profile", name


def test_no_classification_regresses_against_the_pre_adr_sets():
    """G11: a permutation-proof pin on the classification this change inherited."""
    for name in LEGACY_READ_ONLY:
        assert TOOL_SECURITY[name].effect == "read", name
    for name in LEGACY_DESTRUCTIVE:
        assert TOOL_SECURITY[name].effect == "destructive", name
    assert TOOL_SECURITY["hmc_read_lpar_boot_order"].effect == "read"


def test_legacy_classification_sets_are_gone():
    """G9: replace, don't deprecate."""
    from hmc_mcp import _app, server

    for module in (_app, server):
        for removed in (
            "READ_ONLY_TOOLS",
            "DESTRUCTIVE_TOOLS",
            "_READ_ONLY",
            "_DESTRUCTIVE",
            "_STATE_CHANGING",
        ):
            assert not hasattr(module, removed), f"{module.__name__}.{removed}"
```

2. **Run it and confirm it passes.** `uv run pytest -q --no-cov tests/app/test_tool_security.py`
   Expect every case green. If `test_no_classification_regresses_against_the_pre_adr_sets`
   fails, a tool's effect in Task 3 disagrees with what it shipped — fix the declaration, not
   the snapshot.

3. **Prove the guardrail bites.** Temporarily change one tool's `effect` in
   `server_lpars.py` from `destructive` to `mutate`, run the module, and confirm both
   `test_no_classification_regresses_against_the_pre_adr_sets` and
   `test_delete_and_remove_tools_are_destructive` go red. Revert with
   `git checkout -- src/hmc_mcp/server_lpars.py` and confirm green again.

4. **Commit.** `git commit -m "test: add exhaustive tool security registry contract"`

**Acceptance criteria.** All cases pass. The deliberate-break check in step 3 reddens at least
two tests and green returns after revert.

---

## Task 5 — retire the superseded capability tests

**Modifies:** `tests/app/test_capabilities.py`.

### Steps

1. **Delete** `test_classification_sets_are_disjoint` and
   `test_every_registered_tool_matches_its_category` — Task 4's G1, G4, and G11 subsume both.

2. **Change the import** from

```python
from hmc_mcp.server import (
    DESTRUCTIVE_TOOLS,
    READ_ONLY_TOOLS,
    hmc_decommission_lpar,
    hmc_delete_lpar,
    hmc_delete_vios,
    mcp,
)
```

   to

```python
from hmc_mcp.server import (
    TOOL_SECURITY,
    hmc_decommission_lpar,
    hmc_delete_lpar,
    hmc_delete_vios,
    mcp,
)
```

3. **Re-point the three frozenset assertions.** In
   `test_attach_disk_is_state_changing_not_destructive`, replace the two `not in` assertions
   with `assert TOOL_SECURITY["hmc_attach_disk_to_lpar"].effect == "mutate"`. In
   `test_fleet_health_is_read_only`, replace the membership assertion with
   `assert TOOL_SECURITY["hmc_fleet_health"].effect == "read"`. In
   `test_decommission_lpar_is_public_destructive_and_schema_stable`, replace
   `assert "hmc_decommission_lpar" in DESTRUCTIVE_TOOLS` with
   `assert TOOL_SECURITY["hmc_decommission_lpar"].effect == "destructive"`.

4. **Update the module docstring** — its first paragraph describes the classification as
   living in `READ_ONLY_TOOLS` / `DESTRUCTIVE_TOOLS`. Replace that sentence with: "The
   classification lives on each tool's `ToolSecurity` record; `tests/app/test_tool_security.py`
   holds the exhaustive registry contract, and these tests cover annotation-adjacent schema
   stability and the destructive-tool precondition guards."

5. **Run the file.** `uv run pytest -q --no-cov tests/app/test_capabilities.py`
   Expect all remaining cases green.

6. **Return to Task 3 step 7.** This task shares Task 3's commit.

**Acceptance criteria.** `tests/app/test_capabilities.py` imports no frozenset, and every
retained test passes.

---

## Task 6 — full guardrail run

### Steps

1. **Run the whole suite bare.** `just verify`
   Expect a zero exit code with `static`, `test`, `smoke`, `build`, and `verify-artifacts`
   all green, plus the CLI group help checks.

2. **Fix anything red before proceeding.** A failure that predates this branch is fixed here,
   not documented and shipped over (`AGENTS.md`, "Pre-existing test failures"). The likeliest
   residue is a test elsewhere importing a deleted name:
   `rg -n 'READ_ONLY_TOOLS|DESTRUCTIVE_TOOLS|_STATE_CHANGING|_READ_ONLY\b|_DESTRUCTIVE\b'
   src tests scripts` must return nothing outside `docs/`.

3. **Confirm no dependency drift.** `git --no-pager diff main -- pyproject.toml uv.lock`
   Expect empty output.

4. **Commit any fixes** with their own message; do not fold them into an earlier commit.

**Acceptance criteria.** `just verify` exits zero. No source or test file references a
removed name. `pyproject.toml` and `uv.lock` are unchanged from `main`.

---

## Self-review against the spec

| spec criterion | task |
|---|---|
| G1 exhaustive coverage, toggle on and off | 4 (`test_every_live_tool_has_security_metadata`) |
| G2 unique operation identities; `build_tool_security` raises | 1 (`test_build_tool_security_rejects_duplicate_names_and_operations`), 4 (`test_operation_identities_are_unique`) |
| G3 selectors are public parameters; `required` anchors | 4 (`test_selectors_and_connection_arguments_are_public_parameters`, `test_optional_selectors_are_marked_optional`) |
| G4 annotations derived from effect | 4 (`test_annotations_are_derived_from_the_effect_class`) |
| G5 V2–V9 rejected with a naming message; V1 raises `TypeError` | 1 (`test_tool_rejects_contradictory_declarations`, `test_tool_requires_the_three_mandatory_fields`) |
| G6 V7/V8 hold registry-wide; built targets are complete | 4 (`test_target_declarations_are_internally_consistent`, `test_every_table_argument_becomes_a_target`, `test_multi_kind_tools_declare_every_target`) |
| G7 delete/remove tools are destructive | 4 (`test_delete_and_remove_tools_are_destructive`) |
| G8 escape hatch off by default, sole arbitrary-command, validated | 4 (`test_arbitrary_command_is_absent_by_default_and_maximally_classified`) |
| G9 legacy names absent | 3 (deletion), 4 (`test_legacy_classification_sets_are_gone`) |
| G10 only the local-config tool has no connection argument | 4 (`test_only_the_local_config_tool_opens_no_hmc_connection`) |
| G11 no classification regresses | 4 (`test_no_classification_regresses_against_the_pre_adr_sets`) |
| G12 `just verify` green including the smoke path | 6 |
| G13 no new runtime dependency | 6 (step 3) |
