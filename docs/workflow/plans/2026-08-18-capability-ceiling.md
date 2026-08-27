# Implementation plan — composition-time capability ceiling and permission inspection

Goal: a fresh MCP application registers only the tools the startup-selected access policy
permits, and a read-only tool reports the live effective permissions of the application it
is registered on.

Architecture: `tool_registry.tool_module()` already collects `ToolDefinition` records per
domain module and registers them onto a `FastMCP` instance. This change threads an optional
`permits: Callable[[str], bool] | None` predicate into that registration so a definition the
ceiling rejects is never registered. `server.create_mcp(policy)` derives the predicate from
`AccessPolicy.permits_tool` and passes it to every domain module and to a new
`server_permissions.register_permissions_tool`, which registers the inspection tool through
the same gate. `server.main_stdio` / `main_http` compose a fresh application per call,
intersect the arbitrary-command toggle with the same predicate, and emit startup warnings.
`hmc-mcp serve --access-policy NAME` loads and compiles the policy.

Tech stack: Python 3.11+, `fastmcp-slim[server]==3.4.7`, `pydantic`, `typer`, `pytest`.
Package under `src/hmc_mcp/`, tests under `tests/`.

Source of truth: `docs/workflow/specs/2026-08-18-capability-ceiling-design.md` (requirements
R1–R22) and `docs/adr/0037-composition-time-capability-ceiling.md`.

## Global Constraints

- **Guardrail command:** `just verify`, run bare — no pipes, no redirects, no `|| true`.
  It expands to `static` (`lint typecheck secrets workflow-security env-vars nicknames`)
  then `test smoke build verify-artifacts` plus CLI group `--help` checks.
- **Focused test command:** `uv run --no-sync pytest -q <paths> --no-cov`.
- **Coverage:** `[tool.coverage.report]` is `fail_under = 90`, `precision = 2` and is frozen
  to exactly those two keys by `tests/test_ci_pipeline.py::test_coverage_gate_declares_one_exact_floor`.
  No `omit`, no `include`, no `exclude_*` may be added.
- **`# pragma: no cover` is forbidden** anywhere under `src/hmc_mcp/`.
  `tests/test_ci_pipeline.py::test_coverage_gate_denominator_is_not_shrunk_in_source`
  rejects it outright. Every new line must be genuinely covered.
- **Line length 100; functions ≤100 lines; cyclomatic complexity ≤8.**
- **No new runtime dependency.**
- **Import direction is one-way and enforced by tests:** `access_policy.py` may import only
  `config` and `tool_registry` from the package
  (`tests/unit/test_access_policy.py::test_module_imports_only_the_declared_first_party_modules`)
  and must not import `hmc_mcp.server` (`::test_module_does_not_import_server`). Therefore
  `tool_registry.py` must not import `access_policy.py`, and `server_tools/permissions.py` must
  not import `server.py`.
- **`api.__all__` is not changed.** `tests/unit/test_public_api.py::test_public_api_exports_the_adr_inventory`
  pins it literally, and `tests/unit/test_access_policy.py::test_api_surface_is_unchanged`
  asserts no `access_policy` name appears in it.
- **Commit convention:** Conventional Commits 1.0.0, imperative subject ≤72 chars, trailer
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- **Never write to stdout from the serve path.** stdio transport carries JSON-RPC on stdout;
  every warning goes to `sys.stderr`.
- Branch: `feat/capability-ceiling-221`. Base: `main`.

## File map

| File | Status | Responsible for |
|---|---|---|
| `src/hmc_mcp/tool_registry.py` | modified | the `permits` gate on `tool_module().register_tools` |
| `src/hmc_mcp/server_tools/permissions.py` | **new** | the inspection tool: result types, its `ToolSecurity` record, and its gated registration factory |
| `src/hmc_mcp/server.py` | modified | `create_mcp(policy)`, the `TOOL_SECURITY` entry, fresh-composing entry points, the startup-warning function |
| `src/hmc_mcp/server_tools/command.py` | modified | the arbitrary-command / ceiling intersection |
| `src/hmc_mcp/cli_commands/app.py` | modified | `serve --access-policy NAME` and its load-error path |
| `tests/app/test_capability_ceiling.py` | **new** | R1–R3, R5–R6, R9a, R10a, R11–R18, R19a |
| `tests/app/test_application_boundaries.py` | modified | the 128 → 129 tool-count contract |
| `tests/app/test_tool_security.py` | modified | rule G10's two-name allowance |
| `tests/app/test_profile_routing.py` | modified | `_NO_NETWORK_TOOLS` gains the new tool |
| `tests/app/test_serve.py` | modified | entry-point signature and composition assertions |
| `README.md` | modified | the flag, the four warnings, the tool row, the disclosure |

## Task 1 — Filter registration by a ceiling predicate

Implements R1, R2, R3, R4. Creates the gate every later task uses.

**Interfaces this task publishes.**

```python
# src/hmc_mcp/tool_registry.py — inside tool_module()
def register_tools(mcp: FastMCP, *, permits: Callable[[str], bool] | None = None) -> None
```

```python
# src/hmc_mcp/server.py
def create_mcp(policy: AccessPolicy | None = None) -> FastMCP
```

### Step 1.1 — Write the failing test

Create `tests/app/test_capability_ceiling.py`:

```python
"""Composition-time capability ceiling and effective-permission inspection.

Covers docs/workflow/specs/2026-08-18-capability-ceiling-design.md; the decision
record is docs/adr/0037-composition-time-capability-ceiling.md.
"""

from __future__ import annotations

import asyncio

import pytest

from hmc_mcp.access_policy import compile_access_policy
from hmc_mcp.server import TOOL_SECURITY, create_mcp

SOURCE = "test-access-policy.toml"


def _policy(grants: list[dict], name: str = "test"):
    """Compile one named policy from grant tables, against the live tool index."""
    return compile_access_policy(
        {"policies": {name: {"grants": grants}}}, name, TOOL_SECURITY, SOURCE
    )


def _names(application) -> set[str]:
    return {tool.name for tool in asyncio.run(application.list_tools())}


READ_ONLY_GRANT = [
    {"effects": ["read"], "connections": ["<default>"], "targets": "all-targets"}
]


def test_a_read_only_policy_registers_only_read_tools():
    """R1: the registry is exactly the ceiling, minus the escape hatch."""
    policy = _policy(READ_ONLY_GRANT)
    names = _names(create_mcp(policy))

    assert names == {
        name for name in TOOL_SECURITY if policy.permits_tool(name)
    } - {"hmc_run_command"}
    assert names
    assert all(TOOL_SECURITY[name].effect == "read" for name in names)
    assert "hmc_delete_lpar" not in names


def test_no_policy_applies_no_ceiling():
    """R2: the default composition is unfiltered."""
    names = _names(create_mcp())

    assert names == set(TOOL_SECURITY) - {"hmc_run_command"}


def test_applications_composed_with_different_policies_are_independent():
    """R3: a restrictive composition does not leak into a later one."""
    restricted = _names(create_mcp(_policy(READ_ONLY_GRANT)))
    unrestricted = _names(create_mcp())
    restricted_again = _names(create_mcp(_policy(READ_ONLY_GRANT)))

    assert restricted == restricted_again
    assert restricted < unrestricted
```

### Step 1.2 — Confirm it fails

```
uv run --no-sync pytest -q tests/app/test_capability_ceiling.py --no-cov
```

Expect the first and third tests to fail with
`TypeError: create_mcp() takes 0 positional arguments but 1 was given`.
`test_no_policy_applies_no_ceiling` passes at this point and is meant to: `TOOL_SECURITY`
holds **129** entries on this tree (128 collector-declared plus the hand-built
`hmc_run_command` record), so `set(TOOL_SECURITY) - {"hmc_run_command"}` is 128 names against
a 128-tool registry. Task 2 takes both sides up by one.

### Step 1.3 — Add the gate to `tool_registry.py`

In `src/hmc_mcp/tool_registry.py`, replace the `register_tools` closure inside
`tool_module()`:

```python
    def register_tools(
        mcp: FastMCP, *, permits: Callable[[str], bool] | None = None
    ) -> None:
        """Register this module's tools, skipping any the ceiling withholds.

        *permits* is the access policy's ceiling question. ``None`` means no
        ceiling, which is what a caller composing without a policy passes. The
        predicate is taken rather than the policy object because
        ``access_policy`` imports this module; see ADR 0037.
        """
        for definition in definitions:
            if permits is not None and not permits(definition.name):
                continue
            mcp.tool(
                definition.handler,
                annotations=annotations_for(definition.security.effect),
            )
```

`Callable` is already imported at the top of the file
(`from collections.abc import Callable, Iterable, Mapping`).

### Step 1.4 — Make `create_mcp` policy-aware

In `src/hmc_mcp/server.py`, add to the imports:

```python
from .access_policy import AccessPolicy
```

and replace `create_mcp`:

```python
def create_mcp(policy: AccessPolicy | None = None) -> FastMCP:
    """Compose a fresh MCP application bounded by *policy*'s capability ceiling.

    ``None`` applies no ceiling and registers every tool — the behaviour before
    ADR 0037, and what every deployment gets until #225 makes startup fail
    closed. The predicate is passed to each registration site rather than
    checked here, so no site can be given a ceiling it does not apply.
    """
    permits = None if policy is None else policy.permits_tool
    application = _create_base_mcp()
    for module in TOOL_MODULES:
        module.register_tools(application, permits=permits)
    return application
```

### Step 1.5 — Confirm the first and third tests pass

```
uv run --no-sync pytest -q tests/app/test_capability_ceiling.py --no-cov
```

Expect `test_a_read_only_policy_registers_only_read_tools` and
`test_applications_composed_with_different_policies_are_independent` to pass.
`test_no_policy_applies_no_ceiling` passes too at this point and is tightened by Task 2.

### Step 1.6 — Run the affected existing tests

```
uv run --no-sync pytest -q tests/app/test_application_boundaries.py tests/unit/test_tool_registry.py --no-cov
```

Expect all to pass — `permits` defaults to `None`, so every existing caller is unchanged.

### Step 1.7 — Commit

```
git add src/hmc_mcp/tool_registry.py src/hmc_mcp/server.py tests/app/test_capability_ceiling.py
git commit -m "feat(server): filter tool registration by the policy ceiling

Thread an optional permits predicate through each domain module's
register_tools so a tool the access policy withholds is never registered.
create_mcp derives it from AccessPolicy.permits_tool.

Refs #221

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

**Acceptance:** `create_mcp(policy)` returns an application whose tool names equal the
policy's ceiling minus `hmc_run_command`; `create_mcp()` is unchanged from before;
`tool_registry.py` imports nothing from `access_policy.py`.

## Task 2 — The effective-permission inspection tool

Implements R10, R11, R12, R13, R14, R15, R16, R17, R20, and completes R2 (129 tools).

**Interfaces this task consumes:** `create_mcp(policy)` and `register_tools(mcp, *, permits=...)`
from Task 1.

**Interfaces this task publishes:**

```python
# src/hmc_mcp/server_tools/permissions.py
TOOL_NAME: str                                  # "hmc_effective_permissions"
EFFECTIVE_PERMISSIONS_SECURITY: ToolSecurity
UNKNOWN: str                                    # "unknown"
ENFORCED_DIMENSIONS: tuple[str, ...]            # ("tools",)
DECLARED_ONLY_DIMENSIONS: tuple[str, ...]       # ("connections", "targets")

@dataclass(frozen=True)
class ToolPermission:
    name: str
    effect: str
    operation: str
    target_kind: str

@dataclass(frozen=True)
class DeclaredGrant:
    tools: tuple[str, ...]
    connections: tuple[str, ...]
    all_targets: bool
    targets: dict[str, tuple[str, ...]]

@dataclass(frozen=True)
class EffectivePermissions:
    policy_name: str | None
    policy_source: str | None
    ceiling_enforced: bool
    effects: tuple[str, ...]
    tools: tuple[ToolPermission, ...]
    declared_grants: tuple[DeclaredGrant, ...]
    enforced_dimensions: tuple[str, ...]
    declared_only_dimensions: tuple[str, ...]

def register_permissions_tool(
    mcp: FastMCP,
    policy: AccessPolicy | None,
    tool_security: Mapping[str, ToolSecurity],
    *,
    permits: Callable[[str], bool] | None = None,
) -> None: ...
```

### Step 2.1 — Write the failing tests

Append to `tests/app/test_capability_ceiling.py`:

```python
def _inspect(application):
    """Call the registered inspection tool through the application.

    Reads ``structured_content`` (a plain dict), not ``data`` — on
    fastmcp 3.4.7 ``data`` is a pydantic model reconstructed from the output
    schema, which is neither subscriptable nor iterable.
    """
    from fastmcp import Client

    async def _go():
        async with Client(application) as client:
            result = await client.call_tool("hmc_effective_permissions", {})
            return result.structured_content

    return asyncio.run(_go())


def test_inspection_is_registered_and_classified():
    """R10: an ordinary read-effect record with no connection argument."""
    security = TOOL_SECURITY["hmc_effective_permissions"]

    assert security.effect == "read"
    assert security.operation == "permissions.describe"
    assert security.target_kind == "none"
    assert security.targets == ()
    assert security.connection_argument is None
    assert "hmc_effective_permissions" in _names(create_mcp())


def test_inspection_is_subject_to_the_ceiling():
    """R11: an effects grant reaches it; a tools-only grant can withhold it."""
    assert "hmc_effective_permissions" in _names(create_mcp(_policy(READ_ONLY_GRANT)))

    withheld = _policy(
        [{"tools": ["hmc_list_systems"], "connections": ["lab"], "targets": "all-targets"}]
    )
    assert "hmc_effective_permissions" not in _names(create_mcp(withheld))


def test_inspection_matches_the_registry_and_the_policy():
    """R12: equal to the live registry, and to the policy-derived expectation."""
    policy = _policy(READ_ONLY_GRANT)
    application = create_mcp(policy)

    reported = [tool["name"] for tool in _inspect(application)["tools"]]
    live = sorted(tool.name for tool in asyncio.run(application.list_tools()))
    expected = sorted(
        {name for name in TOOL_SECURITY if policy.permits_tool(name)} - {"hmc_run_command"}
    )

    assert reported == live == expected


def test_inspection_reports_effects_source_and_dimensions():
    """R13, R14, R15, R16: what the payload says about the policy."""
    policy = _policy(READ_ONLY_GRANT)
    result = _inspect(create_mcp(policy))

    assert result["effects"] == ["read"]
    assert result["policy_name"] == "test"
    assert result["policy_source"] == SOURCE
    assert result["ceiling_enforced"] is True
    assert result["enforced_dimensions"] == ["tools"]
    assert result["declared_only_dimensions"] == ["connections", "targets"]

    grant = result["declared_grants"][0]
    assert grant["connections"] == ["<default>"]
    assert grant["all_targets"] is True
    assert grant["targets"] == {}
    assert "hmc_list_systems" in grant["tools"]


def test_inspection_reports_no_policy_honestly():
    """R14, R16: nothing is enforced and nothing is declared."""
    result = _inspect(create_mcp())

    assert result["policy_name"] is None
    assert result["policy_source"] is None
    assert result["ceiling_enforced"] is False
    assert result["enforced_dimensions"] == []
    assert result["declared_only_dimensions"] == []
    assert result["declared_grants"] == []


def test_inspection_reports_grants_separately_without_merging():
    """R15: one entry per grant, in document order, never unioned."""
    policy = _policy([
        {"effects": ["read"], "connections": ["lab"], "targets": "all-targets"},
        {
            "tools": ["hmc_delete_lpar"],
            "connections": ["scratch"],
            "targets": {"lpar": ["db-01"], "managed_system": ["sys-a"]},
        },
    ])
    grants = _inspect(create_mcp(policy))["declared_grants"]

    assert [grant["connections"] for grant in grants] == [["lab"], ["scratch"]]
    assert grants[1]["all_targets"] is False
    assert grants[1]["targets"] == {"lpar": ["db-01"], "managed_system": ["sys-a"]}


def test_inspection_does_not_raise_on_a_tool_outside_the_index():
    """R13: an unindexed registered name is reported, not fatal."""
    application = create_mcp()

    def hmc_not_in_the_index() -> str:
        """A tool registered outside the authoritative index."""
        return "ok"

    application.tool(hmc_not_in_the_index)
    reported = {tool["name"]: tool for tool in _inspect(application)["tools"]}

    assert reported["hmc_not_in_the_index"]["effect"] == "unknown"
    assert reported["hmc_not_in_the_index"]["operation"] == "unknown"
    assert reported["hmc_not_in_the_index"]["target_kind"] == "unknown"
    assert "unknown" not in _inspect(application)["effects"]


def test_inspection_carries_only_allowlisted_value_sources(monkeypatch):
    """R17: no config.toml value and no HMC_* environment value reaches the payload.

    The sentinels must exist in the process for their absence to mean anything —
    an unset variable is absent from every payload, correct or leaking — and they
    are checked as *values*, since a leak of HMC_PASSWORD carries its value and
    not its name.
    """
    monkeypatch.setenv("HMC_HOST", "sentinel-host-do-not-leak")
    monkeypatch.setenv("HMC_USER", "sentinel-user-do-not-leak")
    monkeypatch.setenv("HMC_PASSWORD", "sentinel-password-do-not-leak")

    policy = _policy(READ_ONLY_GRANT)
    result = _inspect(create_mcp(policy))

    assert set(result) == {
        "policy_name",
        "policy_source",
        "ceiling_enforced",
        "effects",
        "tools",
        "declared_grants",
        "enforced_dimensions",
        "declared_only_dimensions",
    }
    rendered = repr(result)
    for sentinel in (
        "sentinel-host-do-not-leak",
        "sentinel-user-do-not-leak",
        "sentinel-password-do-not-leak",
    ):
        assert sentinel not in rendered
```

`test_no_policy_applies_no_ceiling` needs no edit: it already reads
`set(TOOL_SECURITY) - {"hmc_run_command"}`, which becomes **130** index entries minus one =
129 names, against the 128 domain tools plus the inspection tool.

### Step 2.2 — Confirm they fail

```
uv run --no-sync pytest -q tests/app/test_capability_ceiling.py --no-cov
```

Expect `KeyError: 'hmc_effective_permissions'` from the `TOOL_SECURITY[...]` lookup in
`test_inspection_is_registered_and_classified`, and a tool-not-found `ToolError` from
`call_tool("hmc_effective_permissions", {})` in every test that calls `_inspect`.

### Step 2.3 — Write `src/hmc_mcp/server_tools/permissions.py`

```python
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
```

### Step 2.4 — Wire it into `server.py`

Add the import beside the other `server_*` imports:

```python
from .server_permissions import (
    EFFECTIVE_PERMISSIONS_SECURITY,
    register_permissions_tool,
)
```

Extend the index:

```python
TOOL_SECURITY: Mapping[str, ToolSecurity] = build_tool_security(
    [module.tool_security() for module in TOOL_MODULES],
    {
        "hmc_run_command": HMC_RUN_COMMAND_SECURITY,
        "hmc_effective_permissions": EFFECTIVE_PERMISSIONS_SECURITY,
    },
)
```

And register it at the end of `create_mcp`, after the domain loop:

```python
    register_permissions_tool(application, policy, TOOL_SECURITY, permits=permits)
    return application
```

### Step 2.5 — Confirm the tests pass

```
uv run --no-sync pytest -q tests/app/test_capability_ceiling.py --no-cov
```

Expect every test in the file to pass.

### Step 2.6 — Update the contracts a new tool breaks

`tests/app/test_application_boundaries.py`, lines 61–62:

```python
    assert len(asyncio.run(first.list_tools())) == 129
    assert len(asyncio.run(second.list_tools())) == 129
```

`tests/app/test_tool_security.py`, `test_only_the_local_config_tool_opens_no_hmc_connection` —
rename and widen to two names:

```python
def test_only_local_tools_open_no_hmc_connection():
    """G10: every tool that reaches an HMC declares how its connection is chosen."""
    local_only = {"hmc_list_configured_hosts", "hmc_effective_permissions"}
    for name, security in TOOL_SECURITY.items():
        if name in local_only:
            assert security.target_kind == "none", name
            assert security.connection_argument is None, name
        else:
            assert security.connection_argument == "profile", name
```

`tests/app/test_profile_routing.py`, line 25:

```python
_NO_NETWORK_TOOLS = frozenset({"hmc_list_configured_hosts", "hmc_effective_permissions"})
```

### Step 2.7 — Run the affected suites

```
uv run --no-sync pytest -q tests/app/test_capability_ceiling.py tests/app/test_application_boundaries.py tests/app/test_tool_security.py tests/app/test_profile_routing.py tests/app/test_capabilities.py --no-cov
```

Expect all to pass.

### Step 2.8 — Commit

```
git add src/hmc_mcp/server_tools/permissions.py src/hmc_mcp/server.py tests/
git commit -m "feat(server): add the effective-permission inspection tool

hmc_effective_permissions reports the live registry of the application it
is registered on, its effect classes, and what the selected policy
declares, separating the one enforced dimension from the two recorded
ones. It is subject to the ceiling like every other tool.

Refs #221

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

**Acceptance:** the tool is in `TOOL_SECURITY`, registered by default, withheld by a
`tools`-only policy that omits it, and its reported names equal both the live registry and
the policy-derived expectation.

## Task 3 — Intersect the escape hatch, and compose the served application freshly

Implements R5, R6, R9, R9a.

**Interfaces this task consumes:** `create_mcp(policy)` from Task 1.

**Interfaces this task publishes:**

```python
# src/hmc_mcp/server_tools/command.py
async def configure_arbitrary_command_tool(
    enabled: bool, mcp: FastMCP, *, permits: Callable[[str], bool] | None = None
) -> None

# src/hmc_mcp/server.py
def main_stdio(
    enable_arbitrary_command: bool = False, access_policy: AccessPolicy | None = None
) -> None
def main_http(
    host: str = "127.0.0.1",
    port: int = 8000,
    enable_arbitrary_command: bool = False,
    allow_remote: bool = False,
    access_policy: AccessPolicy | None = None,
) -> None
```

### Step 3.1 — Write the failing tests

Append to `tests/app/test_capability_ceiling.py`:

```python
def _configure(application, enabled, permits=None):
    from hmc_mcp.server_tools.command import configure_arbitrary_command_tool

    asyncio.run(configure_arbitrary_command_tool(enabled, application, permits=permits))


# Reaches the escape hatch by name (ADR 0036 forbids granting it by effect
# class) and the rest of the read class by effect — including
# hmc_effective_permissions, without which _inspect has no tool to call.
GRANT_RUN_COMMAND = [
    {
        "effects": ["read"],
        "tools": ["hmc_run_command"],
        "connections": ["<default>"],
        "targets": "all-targets",
    }
]


def test_escape_hatch_needs_both_the_flag_and_the_grant():
    """R5: the flag and the ceiling compose conjunctively."""
    granted = _policy(GRANT_RUN_COMMAND)
    application = create_mcp(granted)

    _configure(application, True, granted.permits_tool)
    assert "hmc_run_command" in _names(application)

    _configure(application, False, granted.permits_tool)
    assert "hmc_run_command" not in _names(application)


def test_escape_hatch_is_withheld_when_the_policy_omits_it():
    """R5, R6: an effect-class policy cannot reach it, flag or no flag."""
    every_effect = _policy([
        {
            "effects": ["read", "mutate", "destructive"],
            "connections": ["<default>"],
            "targets": "all-targets",
        }
    ])
    assert every_effect.permits_tool("hmc_run_command") is False

    application = create_mcp(every_effect)
    _configure(application, True, every_effect.permits_tool)

    assert "hmc_run_command" not in _names(application)


def test_inspection_tracks_the_arbitrary_command_toggle():
    """R12, R13: the report follows the registry across a post-composition change."""
    granted = _policy(GRANT_RUN_COMMAND)
    application = create_mcp(granted)
    _configure(application, True, granted.permits_tool)

    result = _inspect(application)
    reported = [tool["name"] for tool in result["tools"]]

    assert reported == sorted(tool.name for tool in asyncio.run(application.list_tools()))
    assert "hmc_run_command" in reported
    assert "arbitrary-command" in result["effects"]
    assert result["ceiling_enforced"] is True


def test_a_registry_that_drifted_past_its_ceiling_claims_no_enforcement():
    """R14, R16: a policy name with no enforcement claim is the honest reading."""
    policy = _policy(READ_ONLY_GRANT)
    application = create_mcp(policy)
    _configure(application, True)  # no predicate: the fail-open call shape

    result = _inspect(application)

    assert "hmc_run_command" in [tool["name"] for tool in result["tools"]]
    assert result["policy_name"] == "test"
    assert result["ceiling_enforced"] is False
    assert result["enforced_dimensions"] == []
    assert result["declared_only_dimensions"] == []


@pytest.mark.parametrize("entry_point", ["main_stdio", "main_http"])
def test_entry_points_serve_a_freshly_composed_filtered_application(entry_point):
    """R9, R9a: main_stdio serves its own application, wired to the ceiling."""
    from unittest.mock import patch

    import hmc_mcp.server as server_module

    every_effect = _policy([
        {
            "effects": ["read", "mutate", "destructive"],
            "connections": ["<default>"],
            "targets": "all-targets",
        }
    ])
    served = {}

    def _capture(self, **_kwargs):
        served["app"] = self
        served["names"] = {tool.name for tool in asyncio.run(self.list_tools())}

    with patch.object(type(server_module.mcp), "run", _capture):
        getattr(server_module, entry_point)(
            enable_arbitrary_command=True, access_policy=every_effect
        )

    # R9 is object identity, not set difference: an all-three-effects grant
    # resolves to every tool but hmc_run_command, which create_mcp never
    # registers anyway, so the two name sets are deliberately equal here.
    assert served["app"] is not server_module.mcp
    assert "hmc_run_command" not in served["names"]
    assert "hmc_delete_lpar" in served["names"]
```

### Step 3.2 — Confirm they fail

```
uv run --no-sync pytest -q tests/app/test_capability_ceiling.py -k "escape_hatch or entry_points" --no-cov
```

Expect `TypeError: configure_arbitrary_command_tool() got an unexpected keyword argument 'permits'`
and `TypeError: main_stdio() got an unexpected keyword argument 'access_policy'`.

### Step 3.3 — Intersect in `server_tools/command.py`

Replace `configure_arbitrary_command_tool`:

```python
async def configure_arbitrary_command_tool(
    enabled: bool,
    mcp: FastMCP,
    *,
    permits: Callable[[str], bool] | None = None,
) -> None:
    """Make escape-hatch registration match the requested capability state.

    The ``--enable-arbitrary-command`` flag is the outer gate and *permits* is
    the access policy's ceiling; per ADR 0036 they compose conjunctively, so the
    tool is registered only when both admit it. ``None`` means no ceiling.
    """
    permitted = enabled and (permits is None or permits("hmc_run_command"))
    registered = await mcp.local_provider.get_tool("hmc_run_command") is not None
    if permitted and not registered:
        mcp.tool(
            hmc_run_command,
            annotations=annotations_for(HMC_RUN_COMMAND_SECURITY.effect),
        )
    elif not permitted and registered:
        mcp.local_provider.remove_tool("hmc_run_command")
```

Add `from collections.abc import Callable` to that module's imports.

### Step 3.4 — Compose freshly in the entry points

In `src/hmc_mcp/server.py`, replace `main_stdio` and `main_http`:

```python
def _serve_application(
    enable_arbitrary_command: bool, access_policy: AccessPolicy | None
) -> FastMCP:
    """Compose, gate, and diagnose the application about to be served."""
    application = create_mcp(access_policy)
    permits = None if access_policy is None else access_policy.permits_tool

    async def _prepare() -> int:
        await configure_arbitrary_command_tool(
            enable_arbitrary_command, application, permits=permits
        )
        return len(await application.local_provider.list_tools())

    asyncio.run(_prepare())
    return application


def main_stdio(
    enable_arbitrary_command: bool = False,
    access_policy: AccessPolicy | None = None,
) -> None:
    """Start an MCP server over stdio, bounded by *access_policy*."""
    _serve_application(enable_arbitrary_command, access_policy).run()


def main_http(
    host: str = "127.0.0.1",
    port: int = 8000,
    enable_arbitrary_command: bool = False,
    allow_remote: bool = False,
    access_policy: AccessPolicy | None = None,
) -> None:
    """Start an MCP server over streamable HTTP, bounded by *access_policy*."""
    if not allow_remote and not _is_loopback(host):
        raise ValueError(
            f"listen host {host!r} binds beyond loopback, but the streamable HTTP "
            "server has no authentication and exposes every enabled tool "
            "(including user administration). Refusing to start. Explicitly "
            "authorize remote binding and put an authenticated reverse proxy in front."
        )
    _serve_application(enable_arbitrary_command, access_policy).run(
        transport="streamable-http", host=host, port=port
    )
```

`_startup_warnings` does not exist yet — it arrives whole in Task 4, with its tests and its
call site, so no commit on this branch carries a serve path that silently discards its
diagnostics. **Do not commit a stub for it**, and do not add `import sys` here: nothing in
Task 3 writes to stderr, and ruff's default `F401` would reject the unused import at the
pre-commit hook. Write `_serve_application` in exactly this form for now:

```python
def _serve_application(
    enable_arbitrary_command: bool, access_policy: AccessPolicy | None
) -> FastMCP:
    """Compose and gate the application about to be served."""
    application = create_mcp(access_policy)
    permits = None if access_policy is None else access_policy.permits_tool

    async def _prepare() -> None:
        await configure_arbitrary_command_tool(
            enable_arbitrary_command, application, permits=permits
        )

    asyncio.run(_prepare())
    return application
```

Task 4 turns `_prepare` into the counting form and adds the warning loop.

### Step 3.5 — Confirm the tests pass, and repair `test_serve.py`

```
uv run --no-sync pytest -q tests/app/test_capability_ceiling.py --no-cov
```

Then `tests/app/test_serve.py`. Two kinds of change belong to **this** task — the
CLI-level `assert_called_once_with` edits belong to Task 4, because `cli_app.serve` does not
pass `access_policy` until Step 4.4.

First, `patch.object(server_app.mcp, "run")` at lines 54, 79, 189, and 197 becomes
`patch.object(type(server_app.mcp), "run")`, because the served application is no longer the
module global. The `run.assert_called_once_with(...)` assertions beside them are unchanged:
`patch.object` installs a `MagicMock`, which is not a descriptor, so a class-level patch
passes no `self` and the call still records exactly the transport/host/port keywords.
(Step 3.1 patches with a plain *function*, which **is** a descriptor and does bind, which is
why its `_capture` takes `self`.)

Second, the parametrized pair at lines ~157–185 asserts
`configure.assert_called_once_with(enabled, server_app.mcp)`, which no longer holds: the
entry point gates a freshly composed application, not the global. Replace both, keeping the
parametrization and the `run` assertions:

```python
@pytest.mark.parametrize("enabled", [False, True])
def test_stdio_entry_point_gates_the_escape_hatch(enabled, monkeypatch):
    import hmc_mcp.server as server_app

    calls = []

    async def _record(flag, application, *, permits=None):
        calls.append((flag, permits))

    monkeypatch.setattr(server_app, "configure_arbitrary_command_tool", _record)
    with patch.object(type(server_app.mcp), "run") as run:
        server_app.main_stdio(enable_arbitrary_command=enabled)

    assert calls == [(enabled, None)]
    run.assert_called_once_with()


@pytest.mark.parametrize("enabled", [False, True])
def test_http_entry_point_gates_the_escape_hatch(enabled, monkeypatch):
    import hmc_mcp.server as server_app

    calls = []

    async def _record(flag, application, *, permits=None):
        calls.append((flag, permits))

    monkeypatch.setattr(server_app, "configure_arbitrary_command_tool", _record)
    with patch.object(type(server_app.mcp), "run") as run:
        server_app.main_http(host="127.0.0.1", port=9000, enable_arbitrary_command=enabled)

    assert calls == [(enabled, None)]
    run.assert_called_once_with(
        transport="streamable-http", host="127.0.0.1", port=9000
    )
```

Run:

```
uv run --no-sync pytest -q tests/app/test_serve.py --no-cov
```

Expect all to pass.

### Step 3.6 — Commit

```
git add src/hmc_mcp/server_tools/command.py src/hmc_mcp/server.py tests/
git commit -m "feat(server): intersect the escape hatch with the policy ceiling

configure_arbitrary_command_tool takes the same permits predicate as the
domain registration, so hmc_run_command needs both its flag and a grant
naming it. main_stdio and main_http compose a fresh filtered application
instead of serving and mutating the module-level app.

Refs #221

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

**Acceptance:** an every-effect policy plus `--enable-arbitrary-command` serves an
application without `hmc_run_command`; the served application is not `server.mcp`.

## Task 4 — Startup selection and the four warnings

Implements R7, R8, R10a, R18, R19, R19a.

**Interfaces this task consumes:** `main_stdio` / `main_http` from Task 3.

**Interfaces this task publishes:**

```python
# src/hmc_mcp/server.py
def _startup_warnings(
    tool_count: int,
    access_policy: AccessPolicy | None,
    enable_arbitrary_command: bool,
) -> tuple[str, ...]
```

### Step 4.1 — Write the failing tests

Append to `tests/app/test_capability_ceiling.py`:

```python
def _warnings(tool_count, policy, enable_arbitrary_command=False):
    from hmc_mcp.server import _startup_warnings

    return _startup_warnings(tool_count, policy, enable_arbitrary_command)


DENY_EVERYTHING: list[dict] = []


def test_an_empty_served_surface_is_warned_and_suppresses_the_other_line():
    """R10a: keyed on the served registry, and it replaces R18's line."""
    policy = _policy(DENY_EVERYTHING, name="locked")
    assert len(policy.tools) == 0
    assert _names(create_mcp(policy)) == set()

    lines = _warnings(0, policy)

    assert len(lines) == 1
    assert "no tools" in lines[0]
    # Cause-neutral: R10a covers an empty surface however it arose, and the
    # withheld-inspection line is suppressed rather than printed beside it.
    assert "hmc_effective_permissions" not in lines[0]


def test_a_withheld_inspection_tool_is_warned():
    """R18: named, with the policy that withheld it."""
    policy = _policy(
        [{"tools": ["hmc_list_systems"], "connections": ["lab"], "targets": "all-targets"}]
    )

    lines = _warnings(1, policy)

    assert any("hmc_effective_permissions" in line and "test" in line for line in lines)


def test_an_authored_but_unselected_policy_file_is_warned(tmp_path, monkeypatch):
    """R19: fires only when the file exists and no policy was selected."""
    import hmc_mcp.server as server_app

    present = tmp_path / "access-policy.toml"
    present.write_text("", encoding="utf-8")
    monkeypatch.setattr(server_app, "resolve_access_policy_path", lambda: present)
    assert any("access-policy.toml" in line for line in _warnings(129, None))

    monkeypatch.setattr(
        server_app, "resolve_access_policy_path", lambda: tmp_path / "absent.toml"
    )
    assert _warnings(129, None) == ()


def test_an_unresolvable_policy_path_never_fails_the_start(monkeypatch):
    """R19: Path.home() with no home directory must not propagate."""
    import hmc_mcp.server as server_app

    def _boom():
        raise RuntimeError("Could not determine home directory")

    monkeypatch.setattr(server_app, "resolve_access_policy_path", _boom)

    assert _warnings(129, None) == ()


def test_a_withheld_escape_hatch_is_warned_only_when_requested():
    """R19a: the explicit request answered with silence gets a line."""
    policy = _policy(READ_ONLY_GRANT)

    requested = _warnings(90, policy, enable_arbitrary_command=True)
    assert any("hmc_run_command" in line for line in requested)

    assert not any(
        "hmc_run_command" in line for line in _warnings(90, policy)
    )


POLICY_FILE = """
[[policies.lab.grants]]
effects = ["read"]
connections = ["<default>"]
targets = "all-targets"
"""


@pytest.mark.parametrize(
    ("argv", "target"),
    [
        (["serve"], "main_stdio"),
        (["serve", "--http"], "main_http"),
    ],
)
def test_serve_forwards_the_compiled_policy_to_the_entry_point(
    argv, target, tmp_path, monkeypatch
):
    """R7: the selected policy reaches the server, not just the loader."""
    from unittest.mock import patch

    from typer.testing import CliRunner

    import hmc_mcp.access_policy as access_policy_module
    from hmc_mcp.access_policy import AccessPolicy
    from hmc_mcp.cli import app

    path = tmp_path / "access-policy.toml"
    path.write_text(POLICY_FILE, encoding="utf-8")
    monkeypatch.setattr(
        access_policy_module, "resolve_access_policy_path", lambda: path
    )

    with patch(f"hmc_mcp.server.{target}") as entry_point:
        result = CliRunner().invoke(app, [*argv, "--access-policy", "lab"])

    assert result.exit_code == 0, result.output
    forwarded = entry_point.call_args.kwargs["access_policy"]
    assert isinstance(forwarded, AccessPolicy)
    assert forwarded.name == "lab"
    assert forwarded.permits_tool("hmc_list_systems")
    assert not forwarded.permits_tool("hmc_delete_lpar")


ESCAPE_HATCH_ONLY = [
    {
        "tools": ["hmc_run_command"],
        "connections": ["<default>"],
        "targets": "all-targets",
    }
]


def test_the_serve_path_counts_after_the_toggle_and_writes_only_to_stderr(capsys):
    """R10a: ordering, suppression, and the stdout constraint, in one test.

    A ceiling of only ``hmc_run_command`` composes zero tools, so counting
    before the toggle would emit a false empty-surface line. Started with the
    flag, the served surface is exactly the escape hatch.
    """
    import hmc_mcp.server as server_app

    policy = _policy(ESCAPE_HATCH_ONLY, name="hatch")
    application = server_app._serve_application(True, policy)

    assert _names(application) == {"hmc_run_command"}

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no tools" not in captured.err
    assert "hmc_effective_permissions" in captured.err


def test_the_serve_path_warns_once_on_a_genuinely_empty_surface(capsys):
    """R10a: the empty-surface line suppresses the withheld-inspection line."""
    import hmc_mcp.server as server_app

    policy = _policy(ESCAPE_HATCH_ONLY, name="hatch")
    application = server_app._serve_application(False, policy)

    assert _names(application) == set()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no tools" in captured.err
    assert "hmc_effective_permissions" not in captured.err


def test_serve_reports_an_unloadable_policy_and_starts_nothing(tmp_path, monkeypatch):
    """R7, R8: an explicit selection that cannot be loaded exits non-zero."""
    from typer.testing import CliRunner

    import hmc_mcp.access_policy as access_policy_module
    from hmc_mcp.cli import app

    monkeypatch.setattr(
        access_policy_module,
        "resolve_access_policy_path",
        lambda: tmp_path / "absent.toml",
    )
    result = CliRunner().invoke(app, ["serve", "--access-policy", "missing"])

    # `_fail` renders through a rich Console, which hard-folds a non-tty line at
    # 80 columns with no spaces to break on, so a tmp_path substring can fold
    # mid-word between runs. Assert on wrap-proof text instead.
    assert result.exit_code == 1
    assert "cannot be read" in result.output
```

### Step 4.2 — Confirm they fail

```
uv run --no-sync pytest -q tests/app/test_capability_ceiling.py --no-cov
```

Run the whole file — Tasks 1–3's tests are green by now, so it costs nothing, and a `-k`
filter would silently skip `test_serve_forwards_the_compiled_policy_to_the_entry_point` and
`test_the_serve_path_counts_after_the_toggle_and_writes_only_to_stderr`, two of the most
consequential tests in this task. Expect exactly these failures:

- the six `_warnings(...)` tests and both `_serve_application` tests:
  `ImportError: cannot import name '_startup_warnings' from 'hmc_mcp.server'`;
- `test_serve_forwards_the_compiled_policy_to_the_entry_point` (both parametrizations) and
  `test_serve_reports_an_unloadable_policy_and_starts_nothing`: exit code 2, because
  `--access-policy` is not an option yet.

### Step 4.3 — Write `_startup_warnings`

In `src/hmc_mcp/server.py`, add the function — Task 3 deliberately left no stub. Add
`import sys` to the module imports (Step 4.3a is what uses it), and add
`from .access_policy import AccessPolicy, resolve_access_policy_path` to the imports and
`from .server_permissions import TOOL_NAME as PERMISSIONS_TOOL_NAME`.

```python
def _unselected_policy_file() -> str | None:
    """The platform-native policy file's path when one exists, else ``None``.

    Reads nothing and never raises: ``resolve_access_policy_path`` reaches
    ``Path.home()``, which raises under a uid with no passwd entry and no HOME,
    and a diagnostic that can abort a start nobody asked to constrain is worse
    than no diagnostic.
    """
    try:
        path = resolve_access_policy_path()
        return str(path) if path.exists() else None
    except (RuntimeError, OSError, ValueError):
        return None


def _startup_warnings(
    tool_count: int,
    access_policy: AccessPolicy | None,
    enable_arbitrary_command: bool,
) -> tuple[str, ...]:
    """The stderr lines describing what this server will and will not expose.

    Every input exists only here — the served registry, the policy, and the
    escape-hatch flag — which is why the four warnings share one function. An
    empty surface already implies the inspection tool is absent, so it replaces
    that line rather than printing beside it.
    """
    lines: list[str] = []
    if tool_count == 0:
        lines.append(
            "warning: this server exposes no tools. Nothing it is asked to do will "
            "succeed."
        )
    elif access_policy is not None and not access_policy.permits_tool(
        PERMISSIONS_TOOL_NAME
    ):
        lines.append(
            f"warning: access policy {access_policy.name!r} withholds "
            f"{PERMISSIONS_TOOL_NAME}, so this server cannot report its own "
            "effective permissions to a client."
        )
    if access_policy is None and (path := _unselected_policy_file()) is not None:
        lines.append(
            f"warning: {path} exists but no access policy was selected, so no "
            "capability ceiling is applied. Pass --access-policy NAME to enforce one."
        )
    if (
        enable_arbitrary_command
        and access_policy is not None
        and not access_policy.permits_tool("hmc_run_command")
    ):
        lines.append(
            "warning: --enable-arbitrary-command was requested, but access policy "
            f"{access_policy.name!r} does not grant hmc_run_command, so it is not "
            "exposed. Name it in a grant's tools to allow it."
        )
    return tuple(lines)
```

### Step 4.3a — Restore the tool count and emit the warnings

In `_serve_application`, replace Task 3's `_prepare` with the counting form and add the
warning loop, so the function, its call site, and `import sys` land in one commit:

```python
    async def _prepare() -> int:
        await configure_arbitrary_command_tool(
            enable_arbitrary_command, application, permits=permits
        )
        return len(await application.local_provider.list_tools())

    tool_count = asyncio.run(_prepare())
    for line in _startup_warnings(tool_count, access_policy, enable_arbitrary_command):
        print(line, file=sys.stderr)
    return application
```

### Step 4.4 — Add the CLI option

In `src/hmc_mcp/cli_commands/app.py`, add to `serve`'s parameters, after `enable_arbitrary_command`:

```python
    access_policy: str | None = typer.Option(
        None,
        "--access-policy",
        metavar="NAME",
        help="Enforce the named access policy from access-policy.toml. Without it, "
        "no capability ceiling is applied and every tool is exposed.",
    ),
```

Inside the body, after the connection-options guard and before the transport branch:

```python
    from .access_policy import AccessPolicyError, load_access_policy

    policy = None
    if access_policy is not None:
        try:
            policy = load_access_policy(access_policy, server.TOOL_SECURITY)
        except AccessPolicyError as exc:
            _fail(exc)
```

and pass `access_policy=policy` to both `server.main_http(...)` and
`server.main_stdio(...)`.

Extend the `serve` docstring with one paragraph:

```
    Pass ``--access-policy NAME`` to enforce a capability ceiling from
    ``access-policy.toml``: the server then registers only the tools that policy
    permits. Without it no ceiling is applied. Call ``hmc_effective_permissions``
    to see what a running server actually exposes.
```

### Step 4.4a — Update the CLI-level entry-point assertions

`cli_app.serve` now passes `access_policy`, so the four CLI-level mocks in
`tests/app/test_serve.py` must expect it. `patch(...)` installs an unspecced `MagicMock`, so
no default is filled in and the keyword must be written out:

- lines 70–75 and 98–103: `main_http.assert_called_once_with(...)` gains `access_policy=None`;
- line 131 and `test_serve_passes_arbitrary_command_opt_in` (lines 145–153):
  `main_stdio.assert_called_once_with(...)` gains `access_policy=None`.

### Step 4.5 — Confirm the tests pass

```
uv run --no-sync pytest -q tests/app/test_capability_ceiling.py tests/app/test_serve.py --no-cov
```

Expect all to pass.

### Step 4.6 — Commit

```
git add src/hmc_mcp/server.py src/hmc_mcp/cli_commands/app.py tests/app/test_capability_ceiling.py tests/app/test_serve.py
git commit -m "feat(cli): select an access policy at serve time

serve --access-policy NAME loads and compiles the named policy and serves
an application bounded by it; a policy that cannot be loaded exits
non-zero. Four stderr warnings cover an empty surface, a withheld
inspection tool, an authored-but-unselected policy file, and an escape
hatch the policy withholds.

Refs #221

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

**Acceptance:** `hmc-mcp serve --access-policy missing` exits 1 naming the file; each of the
four warning conditions produces exactly its own line and no other.

## Task 5 — Documentation

Implements R22.

### Step 5.1 — Update `README.md`

1. In the `serve` section (around lines 248–261) add `--access-policy NAME` to the
   documented options, with one sentence: it enforces a capability ceiling from
   `access-policy.toml`, and without it no ceiling is applied.
2. Add a short subsection listing the four startup warnings and what each means.
3. Add a row to the read-only tool table (around lines 273–295):

   | `hmc_effective_permissions` | Report the tools this server exposes, their effect classes, and the selected access policy's declared connections and targets |

4. Immediately after that table, add the disclosure note: the tool returns the policy name,
   its absolute path, every connection token, and every target selector to any MCP client
   that can call it; only a `tools`-only policy that omits it can withhold it.

### Step 5.2 — Verify the documented commands work

```
uv run --no-sync hmc-mcp serve --help
```

Expect `--access-policy` in the output with the help text from Task 4.

### Step 5.3 — Run the full guardrail suite, bare

```
just verify
```

Expect exit 0. If coverage falls below 90.00%, add tests for the uncovered lines — do not
add `# pragma: no cover` and do not touch `[tool.coverage.report]`.

### Step 5.4 — Commit

```
git add README.md
git commit -m "docs: document the access-policy ceiling and inspection tool

Refs #221

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

**Acceptance:** `just verify` exits 0; `README.md` documents the flag, the four warnings,
the tool, and its disclosure.

## Requirement coverage

| Requirement | Task |
|---|---|
| R1, R2, R3, R4 | 1 |
| R10, R11, R12, R13, R14, R15, R16, R17, R20 | 2 |
| R5, R6, R9, R9a | 3 |
| R7, R8, R10a, R18, R19, R19a | 4 |
| R22 | 5 |
| R21 (`just verify` bare) | 5, step 5.3 |

Deferred and tracked: a grant's authored `effects` list is not recoverable from the
inspection output — issue #251.
