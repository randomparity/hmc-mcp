# Implementation plan — load and validate immutable server access policies

Issue: [#220](https://github.com/randomparity/hmc-mcp/issues/220), part of epic
[#218](https://github.com/randomparity/hmc-mcp/issues/218).
Specification: [`2026-08-18-server-access-policy-design.md`](../specs/2026-08-18-server-access-policy-design.md).
Decision record: [ADR 0036](../../adr/0036-server-access-policy-model.md).

**Goal.** Add one module that reads a TOML *server access policy*, validates it strictly
against the authoritative tool-security index, and compiles it into a frozen object that
answers which tools the policy permits and which grants apply to a given tool.

**Architecture.** Two validation tiers in one module. A pydantic tier checks document
shape and binds every policy in the file; a hand-written tier checks the selected policy
against the injected tool index and compiles it. `load_access_policy` is a thin I/O
wrapper over the pure `compile_access_policy`. Nothing enforces the policy — that is
issues #221 through #223.

**Tech stack.** Python 3.11+, `pydantic==2.13.4` (already a core dependency), stdlib
`tomllib`. Tests are `pytest==9.1.1`. No new dependency is added.

## Global constraints

Every task's requirements implicitly include this section.

- **Branch.** `feat/access-policy-220`, off `main`. Never commit to `main`.
- **Guardrail.** `just verify`, run **bare** — no pipes, no `| tail`, no `|| true`. It
  expands to `static` (lint, typecheck, secrets, workflow-security, env-vars, nicknames),
  then `test`, `smoke`, `build`, `verify-artifacts`, plus CLI group help checks.
- **Focused test runs need `--no-cov`.** `pyproject.toml` sets `addopts = "--cov=hmc_mcp
  --cov-report=term-missing"` and `[tool.coverage.report] fail_under = 90`, so a focused
  `pytest tests/unit/test_access_policy.py` exits 1 on package coverage alone. Use
  `uv run --no-sync pytest tests/unit/test_access_policy.py -q --no-cov` while iterating
  and the bare `just verify` as the real gate.
- **Never write a string of the form `password_policy = "..."` or `password... = "..."`**
  in source, tests, or docs. The repo's `detect-secrets` keyword rule flags it and
  `just secrets` is part of `just verify`. `password_policy` as a *value* inside a list —
  `["password_policy"]` — is fine; as an assignment target it is not.
- **Non-interactive shells only.** `GIT_EDITOR=true`, `git --no-pager`, always pass
  `--title`/`--body` to `gh`.
- **Do not modify** `src/hmc_mcp/tool_registry.py`, `src/hmc_mcp/server.py`,
  `src/hmc_mcp/config.py`, `src/hmc_mcp/common.py`, `src/hmc_mcp/api.py`, or
  `pyproject.toml`. The change is two new files only.
- **Do not add** `hmc_mcp.authorization.access_policy` to `tests/test_optional_dependencies.py`'s
  core-only import contract. The module imports `tool_registry`, which imports `fastmcp`
  and `mcp.types` unconditionally, so it legitimately requires the `app` extra.
- **Never write `# pragma: no cover`.** `tests/test_ci_pipeline.py::test_coverage_gate_denominator_is_not_shrunk_in_source`
  walks every `src/hmc_mcp/**/*.py` and fails on any occurrence not listed in
  `REVIEWED_NO_COVER`, which is deliberately empty (accepted ADR 0034). Adding a line to
  that allowlist is the disarm route the ADR exists to close, and it would edit a third
  file outside this change's surface. Cover the statement with a test instead.
- **Vocabulary.** An *HMC connection profile* is the existing `config.toml` concept. A
  *server access policy* is the new one. Never call either the other.
- Commit after each task with a Conventional Commits subject of 72 characters or fewer.

### Files

| file | responsibility |
|---|---|
| `src/hmc_mcp/authorization/access_policy.py` | new — constants, `AccessPolicyError`, pydantic shape models, the compiled `AllTargets`/`Grant`/`AccessPolicy` types, `compile_access_policy`, `resolve_access_policy_path`, `load_access_policy` |
| `tests/unit/test_access_policy.py` | new — acceptance criteria A1–A16 |

### Interfaces the whole change publishes

```python
ACCESS_POLICY_FILENAME: str = "access-policy.toml"
DEFAULT_CONNECTION_TOKEN: str = "<default>"
ALL_TARGETS_TOKEN: str = "all-targets"
GRANT_EFFECTS: frozenset[str]                       # {"read", "mutate", "destructive"}

class AccessPolicyError(ValueError): ...

@dataclass(frozen=True)
class AllTargets: ...
ALL_TARGETS: AllTargets

@dataclass(frozen=True)
class Grant:
    tools: frozenset[str]
    connections: frozenset[str | None]
    targets: AllTargets | Mapping[TargetKind, frozenset[str]]

@dataclass(frozen=True)
class AccessPolicy:
    name: str
    source: str
    grants: tuple[Grant, ...]
    tools: frozenset[str]
    def permits_tool(self, tool: str) -> bool: ...
    def grants_for(self, tool: str) -> tuple[Grant, ...]: ...

def resolve_access_policy_path() -> Path: ...
def compile_access_policy(
    document: Mapping[str, Any],
    name: str,
    tool_security: Mapping[str, ToolSecurity],
    source: str,
) -> AccessPolicy: ...
def load_access_policy(
    name: str,
    tool_security: Mapping[str, ToolSecurity],
    *,
    path: Path | str | None = None,
) -> AccessPolicy: ...
```

Everything else in the module is private (leading underscore).

---

## Task 1 — The shape tier

Validates document shape (spec rules P1–P6) with pydantic and renders any
`ValidationError` into the repository's fail-fast message convention. Binds every policy
in the document, not just the selected one.

**Creates:** `src/hmc_mcp/authorization/access_policy.py`
**Creates:** `tests/unit/test_access_policy.py`
**Consumes from earlier tasks:** nothing — this is the first task.
**Later tasks rely on:** `AccessPolicyError`, `_parse_document(document, source) ->
_DocumentModel`, `_DocumentModel.policies: dict[str, _PolicyModel]`,
`_PolicyModel.grants: tuple[_GrantModel, ...]`, `_GrantModel.effects/tools/connections/
targets`, and the constants `ACCESS_POLICY_FILENAME`, `DEFAULT_CONNECTION_TOKEN`,
`ALL_TARGETS_TOKEN`, `GRANT_EFFECTS`.

### Step 1.1 — Write the failing test

Create `tests/unit/test_access_policy.py`:

```python
"""Unit tests for server access-policy loading, validation, and compilation."""

from __future__ import annotations

import pytest

from hmc_mcp.authorization.access_policy import AccessPolicyError, _parse_document


def _document(**grant: object) -> dict[str, object]:
    """A one-policy document whose single grant is `grant`."""
    return {"policies": {"lab": {"grants": [grant]}}}


VALID_GRANT: dict[str, object] = {
    "effects": ["read"],
    "connections": ["lab"],
    "targets": "all-targets",
}


def test_parse_document_accepts_a_minimal_policy() -> None:
    parsed = _parse_document(_document(**VALID_GRANT), "access-policy.toml")

    assert set(parsed.policies) == {"lab"}
    grant = parsed.policies["lab"].grants[0]
    assert grant.effects == ("read",)
    assert grant.tools == ()
    assert grant.connections == ("lab",)
    assert grant.targets == "all-targets"
```

### Step 1.2 — Run it and confirm it fails

```sh
uv run --no-sync pytest tests/unit/test_access_policy.py -q --no-cov
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'hmc_mcp.authorization.access_policy'`.

### Step 1.3 — Write the module's shape tier

Create `src/hmc_mcp/authorization/access_policy.py`:

```python
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

from collections.abc import Mapping
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

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
        from .tool_registry import TARGET_KINDS

        if isinstance(value, str):
            if value != ALL_TARGETS_TOKEN:
                raise ValueError(
                    "'targets' must be the string \"all-targets\" or a table of "
                    f"target kind to selector strings; got {value!r}"
                )
            return value
        if not isinstance(value, dict):
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
    def _validate_names_a_tool(self) -> "_GrantModel":
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
    if len(loc) >= 2 and loc[0] == "policies":
        parts.append(f"policy {loc[1]!r}")
        if len(loc) >= 4 and loc[2] == "grants" and isinstance(loc[3], int):
            parts.append(f"grant {loc[3]}")
    if error["type"] == "extra_forbidden":
        parts.append(f"unknown key {loc[-1]!r}")
    elif error["type"] == "missing":
        parts.append(f"missing required key {loc[-1]!r}")
    else:
        parts.append(str(error["msg"]).removeprefix("Value error, "))
    return ": ".join(parts)


def _parse_document(document: Mapping[str, Any], source: str) -> _DocumentModel:
    """Validate document shape, raising AccessPolicyError on any violation."""
    try:
        return _DocumentModel.model_validate(document)
    except ValidationError as error:
        detail = "\n".join(_render_error(source, item) for item in error.errors())
        raise AccessPolicyError(detail) from error
```

`TARGET_KINDS` is imported inside `_validate_targets` rather than at module scope
purely to keep the shape tier's import list minimal in this task; Task 2 moves it to a
module-scope import once the compile tier needs `ToolSecurity` anyway.

### Step 1.4 — Run the test and confirm it passes

```sh
uv run --no-sync pytest tests/unit/test_access_policy.py -q --no-cov
```

Expected: `1 passed`.

### Step 1.5 — Add the shape-rule rejection tests

Append to `tests/unit/test_access_policy.py`:

```python
@pytest.mark.parametrize(
    ("document", "expected"),
    [
        pytest.param(
            {"policies": {"lab": {"grants": [VALID_GRANT]}}, "version": 1},
            "unknown key 'version'",
            id="unknown-top-level-key",
        ),
        pytest.param(
            {"policies": {"lab": {"grants": [VALID_GRANT], "note": "x"}}},
            "unknown key 'note'",
            id="unknown-policy-key",
        ),
        pytest.param(
            _document(**VALID_GRANT, targt="all-targets"),
            "unknown key 'targt'",
            id="unknown-grant-key",
        ),
        pytest.param(
            {"policies": {"lab": {}}},
            "missing required key 'grants'",
            id="grants-absent",
        ),
        pytest.param(
            {"policies": {}},
            "policies must define at least one policy",
            id="empty-policies",
        ),
        pytest.param(
            {"policies": {" ": {"grants": []}}},
            "is empty or padded with whitespace",
            id="blank-policy-name",
        ),
        pytest.param(
            _document(effects=["read"], targets="all-targets"),
            "missing required key 'connections'",
            id="connections-missing",
        ),
        pytest.param(
            _document(effects=["read"], connections=[], targets="all-targets"),
            "connections must name at least one connection",
            id="connections-empty",
        ),
        pytest.param(
            _document(effects=["read"], connections=["lab "], targets="all-targets"),
            "padded with whitespace",
            id="connection-padded",
        ),
        pytest.param(
            _document(effects=["read"], connections=["lab", "lab"], targets="all-targets"),
            "connections contains a duplicate entry",
            id="connections-duplicate",
        ),
        pytest.param(
            _document(effects=["arbitrary-command"], connections=["lab"], targets="all-targets"),
            "name 'hmc_run_command' in tools instead",
            id="arbitrary-command-effect",
        ),
        pytest.param(
            _document(effects=["write"], connections=["lab"], targets="all-targets"),
            "unknown effect 'write'",
            id="unknown-effect",
        ),
        pytest.param(
            _document(effects=["read"], connections=["lab"], targets=["all-targets"]),
            "must be the string",
            id="targets-bare-list",
        ),
        pytest.param(
            _document(effects=["read"], connections=["lab"], targets="all_targets"),
            "got 'all_targets'",
            id="targets-misspelled-sentinel",
        ),
        pytest.param(
            _document(effects=["read"], connections=["lab"], targets={}),
            "targets table must not be empty",
            id="targets-empty-table",
        ),
        pytest.param(
            _document(effects=["read"], connections=["lab"], targets={"none": ["x"]}),
            "unknown target kind 'none'",
            id="targets-kind-none",
        ),
        pytest.param(
            _document(effects=["read"], connections=["lab"], targets={"lpar": []}),
            "names no selector",
            id="targets-kind-empty",
        ),
        pytest.param(
            _document(effects=["read"], connections=["lab"], targets={"lpar": [""]}),
            "is empty or padded with whitespace",
            id="selector-empty",
        ),
        pytest.param(
            _document(
                tools=["hmc_get_job", "hmc_get_job"],
                connections=["lab"],
                targets="all-targets",
            ),
            "tools contains a duplicate entry",
            id="tools-duplicate",
        ),
        pytest.param(
            _document(connections=["lab"], targets="all-targets"),
            "names no tool",
            id="grant-names-no-tool",
        ),
        pytest.param(
            _document(
                effects=["read"], connections=["lab"], targets={"lpar": ["a", "a"]}
            ),
            "targets.lpar contains a duplicate entry",
            id="selector-duplicate",
        ),
        pytest.param(
            _document(effects=["read"], connections=["lab"], targets={"lpar": "db-01"}),
            "must be an array of selector strings",
            id="selector-not-an-array",
        ),
        pytest.param(
            _document(effects=["read"], connections=["lab"], targets={"lpar": [1]}),
            "must contain only selector strings",
            id="selector-not-a-string",
        ),
    ],
)
def test_shape_tier_rejects(document: dict[str, object], expected: str) -> None:
    with pytest.raises(AccessPolicyError) as raised:
        _parse_document(document, "access-policy.toml")

    assert expected in str(raised.value)
    assert str(raised.value).startswith("access-policy.toml")


def test_shape_tier_binds_every_policy_not_just_one() -> None:
    document = {
        "policies": {
            "selected": {"grants": [VALID_GRANT]},
            "unselected": {"grants": [dict(VALID_GRANT, targt="x")]},
        }
    }

    with pytest.raises(AccessPolicyError) as raised:
        _parse_document(document, "access-policy.toml")

    assert "policy 'unselected'" in str(raised.value)
    assert "grant 0" in str(raised.value)
```

### Step 1.6 — Run, confirm green, commit

```sh
uv run --no-sync pytest tests/unit/test_access_policy.py -q --no-cov
just lint
just typecheck
just secrets
```

Expected: all tests pass; each `just` target prints its command and exits 0.

```sh
git add src/hmc_mcp/authorization/access_policy.py tests/unit/test_access_policy.py
GIT_EDITOR=true git commit -m "feat(access-policy): validate policy document shape"
```

**Acceptance criteria for Task 1.** `_parse_document` accepts a minimal valid document;
rejects each of the twenty-three shape violations above with an `AccessPolicyError` whose
message starts with the source and names the policy and grant index when the error has
them; and validates every policy in the document, not only one.

---

## Task 2 — The compiled form and the semantic tier

Adds the frozen `AllTargets`/`Grant`/`AccessPolicy` types and `compile_access_policy`,
which applies spec rules P7–P10 and P12 against the injected tool index.

**Modifies:** `src/hmc_mcp/authorization/access_policy.py`
**Modifies:** `tests/unit/test_access_policy.py`

**Consumes from Task 1:** `AccessPolicyError`, `_parse_document(document, source) ->
_DocumentModel`, `_DocumentModel.policies`, `_PolicyModel.grants`, `_GrantModel` fields,
`DEFAULT_CONNECTION_TOKEN`, `ALL_TARGETS_TOKEN`, `GRANT_EFFECTS`.

**Later tasks rely on:** `compile_access_policy(document, name, tool_security, source) ->
AccessPolicy`, `AccessPolicy`, `Grant`, `ALL_TARGETS`. Private helpers this task adds:
`_resolve_tools(model, tool_security) -> frozenset[str]` and
`_compile_grant(model, tool_security, where) -> Grant`.

**Where this fits.** This is the whole evaluator. #221 will call `permits_tool` to filter
registration; #222 and #223 will call `grants_for` and read a whole `Grant`. They must
never union the three dimensions independently across grants — a request is permitted
only when one single grant covers its tool, its connection, and its targets together.

### Step 2.1 — Write the failing test

Append to `tests/unit/test_access_policy.py`, and add
`from hmc_mcp.authorization.access_policy import ALL_TARGETS, compile_access_policy` plus
`from hmc_mcp.server import TOOL_SECURITY` to the imports at the top of the file:

```python
def _compile(document: dict[str, object], name: str = "lab"):
    return compile_access_policy(document, name, TOOL_SECURITY, "access-policy.toml")


def test_read_only_policy_ceiling_is_exactly_the_read_tools() -> None:
    policy = _compile(_document(**VALID_GRANT))

    expected = {name for name, sec in TOOL_SECURITY.items() if sec.effect == "read"}
    assert policy.tools == expected
    assert policy.name == "lab"
    for name, security in TOOL_SECURITY.items():
        assert policy.permits_tool(name) is (security.effect == "read")
```

### Step 2.2 — Run it and confirm it fails

```sh
uv run --no-sync pytest tests/unit/test_access_policy.py -q --no-cov -k ceiling
```

Expected: `ImportError: cannot import name 'ALL_TARGETS' from 'hmc_mcp.authorization.access_policy'`.

### Step 2.3 — Add the compiled types and the compile function

In `src/hmc_mcp/authorization/access_policy.py`, replace the import block with:

```python
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

from .tool_registry import TARGET_KINDS, TargetKind, ToolSecurity
```

and delete the function-local `from .tool_registry import TARGET_KINDS` inside
`_validate_targets`.

Append to the module:

```python
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
            raise AccessPolicyError(f"{where}: unknown tool {tool!r}")

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
            for selector in tool_security[tool].targets:
                if selector.required and selector.kind not in model.targets:
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
        available = ", ".join(sorted(parsed.policies)) or "(none)"
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
```

`frozenset().union(*())` returns an empty frozenset, so a policy with `grants = []`
needs no special case.

### Step 2.4 — Run and confirm the first test passes

```sh
uv run --no-sync pytest tests/unit/test_access_policy.py -q --no-cov -k ceiling
```

Expected: `1 passed`.

### Step 2.5 — Add the remaining compile-tier tests

Append to `tests/unit/test_access_policy.py`:

```python
def test_effect_class_plus_named_tool_unions_the_ceiling() -> None:
    document = {
        "policies": {
            "lab": {
                "grants": [
                    VALID_GRANT,
                    {
                        "tools": ["hmc_create_lpar"],
                        "connections": ["lab"],
                        "targets": {"managed_system": ["S1"]},
                    },
                ]
            }
        }
    }

    policy = _compile(document)

    reads = {name for name, sec in TOOL_SECURITY.items() if sec.effect == "read"}
    assert policy.tools == reads | {"hmc_create_lpar"}
    assert policy.permits_tool("hmc_delete_lpar") is False
    assert policy.grants_for("hmc_create_lpar") == (policy.grants[1],)


def test_arbitrary_command_needs_its_own_name() -> None:
    broad = _compile(
        _document(
            effects=["read", "mutate", "destructive"],
            connections=["lab"],
            targets="all-targets",
        )
    )
    assert broad.permits_tool("hmc_run_command") is False
    assert broad.tools == {
        name
        for name, sec in TOOL_SECURITY.items()
        if sec.effect != "arbitrary-command"
    }

    named = _compile(
        _document(
            tools=["hmc_run_command"], connections=["lab"], targets="all-targets"
        )
    )
    assert named.permits_tool("hmc_run_command") is True


def test_default_connection_token_compiles_to_none() -> None:
    policy = _compile(
        _document(
            effects=["read"], connections=["<default>", "lab"], targets="all-targets"
        )
    )

    assert policy.grants[0].connections == frozenset({None, "lab"})


def test_unknown_tool_is_rejected() -> None:
    with pytest.raises(AccessPolicyError, match="unknown tool 'hmc_create_lpars'"):
        _compile(
            _document(
                tools=["hmc_create_lpars"], connections=["lab"], targets="all-targets"
            )
        )


def test_targets_kind_no_granted_tool_declares_is_rejected() -> None:
    with pytest.raises(AccessPolicyError, match="could never match"):
        _compile(
            _document(
                tools=["hmc_list_systems"],
                connections=["lab"],
                targets={"managed_system": ["S1"]},
            )
        )


def test_inert_console_constraint_on_the_escape_hatch_is_rejected() -> None:
    with pytest.raises(AccessPolicyError, match="could never match"):
        _compile(
            _document(
                tools=["hmc_run_command"],
                connections=["lab"],
                targets={"console": ["c1"]},
            )
        )


def test_coverage_rule_binds_explicitly_named_tools_only() -> None:
    with pytest.raises(AccessPolicyError) as raised:
        _compile(
            _document(
                tools=["hmc_delete_lpar"],
                connections=["lab"],
                targets={"managed_system": ["S1"]},
            )
        )
    assert "hmc_delete_lpar" in str(raised.value)
    assert "'lpar'" in str(raised.value)

    # The same table under an effect class validates: an index change alone must
    # not make an unedited file stop loading.
    policy = _compile(
        _document(
            effects=["destructive"],
            connections=["lab"],
            targets={"managed_system": ["S1"]},
        )
    )
    assert policy.permits_tool("hmc_delete_lpar") is True


def test_optional_selectors_need_no_coverage() -> None:
    policy = _compile(
        _document(
            tools=["hmc_power_off_lpar"],
            connections=["lab"],
            targets={"lpar": ["db-01"]},
        )
    )

    assert policy.permits_tool("hmc_power_off_lpar") is True


def test_selector_less_tools_stay_in_a_table_scoped_effect_grant() -> None:
    policy = _compile(
        _document(
            effects=["destructive"], connections=["lab"], targets={"lpar": ["db-01"]}
        )
    )

    assert policy.permits_tool("hmc_remove_ldap_config") is True


def test_identical_grants_are_rejected() -> None:
    with pytest.raises(AccessPolicyError, match="grants 0 and 1 are identical"):
        _compile({"policies": {"lab": {"grants": [VALID_GRANT, dict(VALID_GRANT)]}}})


def test_grants_differing_only_in_text_are_rejected() -> None:
    document = {
        "policies": {
            "lab": {
                "grants": [
                    VALID_GRANT,
                    dict(VALID_GRANT, tools=["hmc_list_systems"]),
                ]
            }
        }
    }

    with pytest.raises(AccessPolicyError, match="identical after compilation"):
        _compile(document)


def test_empty_grants_list_permits_nothing() -> None:
    policy = _compile({"policies": {"lab": {"grants": []}}})

    assert policy.tools == frozenset()
    assert policy.grants == ()
    assert policy.permits_tool("hmc_list_systems") is False


def test_missing_policy_names_the_available_ones() -> None:
    document = {
        "policies": {
            "lab": {"grants": [VALID_GRANT]},
            "read-only": {"grants": [VALID_GRANT]},
        }
    }

    with pytest.raises(AccessPolicyError) as raised:
        compile_access_policy(document, "typo", TOOL_SECURITY, "access-policy.toml")

    assert "policy 'typo' not found" in str(raised.value)
    assert "lab, read-only" in str(raised.value)


def test_index_dependent_rules_bind_only_the_selected_policy() -> None:
    document = {
        "policies": {
            "lab": {"grants": [VALID_GRANT]},
            "other": {
                "grants": [
                    dict(VALID_GRANT, effects=[], tools=["hmc_no_such_tool"]),
                ]
            },
        }
    }

    policy = _compile(document)

    assert policy.name == "lab"


def test_grants_for_returns_whole_grants_not_merged_dimensions() -> None:
    document = {
        "policies": {
            "lab": {
                "grants": [
                    {
                        "effects": ["read"],
                        "connections": ["prod"],
                        "targets": "all-targets",
                    },
                    {
                        "tools": ["hmc_delete_lpar"],
                        "connections": ["lab"],
                        "targets": {
                            "managed_system": ["S1"],
                            "lpar": ["scratch-01"],
                        },
                    },
                ]
            }
        }
    }

    policy = _compile(document)
    found = policy.grants_for("hmc_delete_lpar")

    assert len(found) == 1
    assert found[0].connections == frozenset({"lab"})
    assert found[0].targets is not ALL_TARGETS
    assert all("prod" not in grant.connections for grant in found)


def test_compiled_policy_is_immutable() -> None:
    policy = _compile(
        _document(
            tools=["hmc_power_off_lpar"],
            connections=["lab"],
            targets={"lpar": ["db-01"]},
        )
    )
    grant = policy.grants[0]

    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.name = "other"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        grant.tools = frozenset()  # type: ignore[misc]
    with pytest.raises(TypeError):
        grant.targets["lpar"] = frozenset()  # type: ignore[index]
    with pytest.raises(TypeError):
        hash(grant)
    with pytest.raises(TypeError):
        hash(_compile(_document(**VALID_GRANT)).grants[0])

    assert isinstance(policy.tools, frozenset)
    assert isinstance(grant.connections, frozenset)
    assert repr(ALL_TARGETS) == "ALL_TARGETS"


def test_compile_does_not_retain_the_caller_dict() -> None:
    document = _document(**VALID_GRANT)
    policy = _compile(document)
    ceiling = policy.tools

    document["policies"] = {}  # type: ignore[index]

    assert policy.tools == ceiling


def test_module_exposes_no_mutator() -> None:
    import inspect

    from hmc_mcp import access_policy

    # Filter to functions this module *defines*. `vars()` also carries what it
    # imported — `dataclass`, `field_validator`, `config_dir` are all public
    # functions — so an unfiltered check can never pass.
    functions = {
        name
        for name, value in vars(access_policy).items()
        if not name.startswith("_")
        and inspect.isfunction(value)
        and value.__module__ == access_policy.__name__
    }
    assert functions == {
        "resolve_access_policy_path",
        "compile_access_policy",
        "load_access_policy",
    }

    methods = {
        name
        for name, value in vars(AccessPolicy).items()
        if not name.startswith("_") and inspect.isfunction(value)
    }
    assert methods == {"permits_tool", "grants_for"}
```

Add `import dataclasses` to the test module's imports.

**`test_module_exposes_no_mutator` belongs to Task 3, not this one** — it names
`load_access_policy` and `resolve_access_policy_path`, which Task 3 adds. Write it in
Task 3 step 3.5 rather than here. A `strict=True` xfail was considered and rejected: any
exception satisfies an xfail, so a test failing for an unintended reason — a missing
import, say — would still report `xfailed`, and the real defect would surface only at the
final `just verify`.

### Step 2.6 — Run, confirm green, commit

```sh
uv run --no-sync pytest tests/unit/test_access_policy.py -q --no-cov
just lint
just typecheck
just secrets
```

Expected: all tests pass.

```sh
git add src/hmc_mcp/authorization/access_policy.py tests/unit/test_access_policy.py
GIT_EDITOR=true git commit -m "feat(access-policy): compile a validated policy into a frozen evaluator"
```

**Acceptance criteria for Task 2.** A read-only policy's ceiling equals exactly the 54
`read` tools. A broad effect grant excludes `hmc_run_command` and yields 128 tools;
naming it in `tools` includes it. `"<default>"` compiles to `None`. Unknown tools,
tool-less grants, unmatched target kinds, uncovered required selectors on explicitly
named tools, and grants identical after compilation are each rejected. Optional
selectors and effect-class members are exempt from coverage. `grants_for` returns whole
grants. The compiled objects reject attribute assignment, item assignment, and hashing,
and do not retain the caller's dict.

---

## Task 3 — The loader

Adds `resolve_access_policy_path` and `load_access_policy`, converting file-level
failures (spec rule P11) into `AccessPolicyError`.

**Modifies:** `src/hmc_mcp/authorization/access_policy.py`
**Modifies:** `tests/unit/test_access_policy.py`

**Consumes from Task 2:** `compile_access_policy(document, name, tool_security, source)`,
`AccessPolicyError`, `ACCESS_POLICY_FILENAME`.

**Later tasks rely on:** nothing — this is the last task.

### Step 3.1 — Write the failing test

Append to `tests/unit/test_access_policy.py`:

```python
POLICY_FILE = """
[[policies.lab.grants]]
effects = ["read"]
connections = ["lab"]
targets = "all-targets"
"""


def test_load_round_trips_a_written_file(tmp_path) -> None:
    path = tmp_path / ACCESS_POLICY_FILENAME
    path.write_text(POLICY_FILE, encoding="utf-8")

    loaded = load_access_policy("lab", TOOL_SECURITY, path=path)
    compiled = compile_access_policy(
        tomllib.loads(POLICY_FILE), "lab", TOOL_SECURITY, str(path)
    )

    assert loaded == compiled
    assert loaded.source == str(path)
```

Add `import tomllib` and extend the `hmc_mcp.authorization.access_policy` import with
`ACCESS_POLICY_FILENAME`, `load_access_policy`, and `resolve_access_policy_path`.

### Step 3.2 — Run it and confirm it fails

```sh
uv run --no-sync pytest tests/unit/test_access_policy.py -q --no-cov -k round_trips
```

Expected: `ImportError: cannot import name 'load_access_policy'`.

### Step 3.3 — Add the loader

Add `import tomllib` and `from pathlib import Path` to the module's imports, and
`from .config import config_dir`. Append:

```python
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
    resolved = Path(path) if path is not None else resolve_access_policy_path()
    source = str(resolved)
    try:
        text = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise AccessPolicyError(f"{source}: is not valid UTF-8: {error}") from error
    except OSError as error:
        raise AccessPolicyError(f"{source}: cannot be read: {error}") from error
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise AccessPolicyError(f"{source}: TOML parse error: {error}") from error
    return compile_access_policy(document, name, tool_security, source)
```

### Step 3.4 — Run and confirm it passes

```sh
uv run --no-sync pytest tests/unit/test_access_policy.py -q --no-cov -k round_trips
```

Expected: `1 passed`.

### Step 3.5 — Add the remaining loader tests

Move `test_module_exposes_no_mutator` here from Task 2 step 2.5 — it needs
`load_access_policy` and `resolve_access_policy_path`, which only now exist. Confirm the
test module's `hmc_mcp.authorization.access_policy` import gains `AccessPolicy` — the test body names
it, and importing it in Task 2 would have failed `just lint` with F401 and blocked that
task's commit through the prek hook. Then append:

```python
def test_resolve_path_sits_beside_config_toml(monkeypatch, tmp_path) -> None:
    """Pin the relationship, not the implementation.

    Asserting ``resolve_access_policy_path() == config_dir() / FILENAME`` would
    restate the function body and pass on any platform. What §3.1 and §4 actually
    claim is that the policy file sits *beside* ``config.toml`` — and
    ``config_dir()`` duplicates ``resolve_config_path()``'s platform branching
    rather than sharing it, so the two can drift apart silently.
    """
    from hmc_mcp.config import config_dir, resolve_config_path

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.toml").write_text("", encoding="utf-8")

    config_path = resolve_config_path()
    assert config_path is not None
    assert resolve_access_policy_path().parent == config_path.parent
    assert resolve_access_policy_path().name == ACCESS_POLICY_FILENAME


def test_load_uses_the_resolved_path_when_none_is_given(monkeypatch, tmp_path) -> None:
    target = tmp_path / ACCESS_POLICY_FILENAME
    target.write_text(POLICY_FILE, encoding="utf-8")
    monkeypatch.setattr(
        "hmc_mcp.authorization.access_policy.resolve_access_policy_path", lambda: target
    )

    policy = load_access_policy("lab", TOOL_SECURITY)

    assert policy.source == str(target)


def test_absent_file_names_the_resolved_path(tmp_path) -> None:
    missing = tmp_path / ACCESS_POLICY_FILENAME

    with pytest.raises(AccessPolicyError) as raised:
        load_access_policy("lab", TOOL_SECURITY, path=missing)

    assert str(missing) in str(raised.value)
    assert "cannot be read" in str(raised.value)


def test_non_utf8_file_is_an_access_policy_error(tmp_path) -> None:
    path = tmp_path / ACCESS_POLICY_FILENAME
    path.write_bytes(b"\xff\xfe not utf-8")

    with pytest.raises(AccessPolicyError, match="not valid UTF-8"):
        load_access_policy("lab", TOOL_SECURITY, path=path)


def test_toml_syntax_error_is_an_access_policy_error(tmp_path) -> None:
    path = tmp_path / ACCESS_POLICY_FILENAME
    path.write_text("[policies.lab\n", encoding="utf-8")

    with pytest.raises(AccessPolicyError, match="TOML parse error"):
        load_access_policy("lab", TOOL_SECURITY, path=path)


def test_directory_in_place_of_the_file_is_an_access_policy_error(tmp_path) -> None:
    directory = tmp_path / ACCESS_POLICY_FILENAME
    directory.mkdir()

    with pytest.raises(AccessPolicyError, match="cannot be read"):
        load_access_policy("lab", TOOL_SECURITY, path=directory)


def test_module_does_not_import_server() -> None:
    script = (
        "import sys\n"
        "from hmc_mcp.authorization.access_policy import load_access_policy\n"
        "assert 'hmc_mcp.server' not in sys.modules, sorted(sys.modules)\n"
    )

    subprocess.run([sys.executable, "-c", script], check=True)


def test_module_imports_only_the_declared_first_party_modules() -> None:
    import ast
    from pathlib import Path as _Path

    import hmc_mcp.authorization.access_policy as module

    tree = ast.parse(_Path(module.__file__).read_text(encoding="utf-8"))
    first_party = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 1
    }
    third_party = {
        alias.name.partition(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.partition(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
    }

    assert first_party == {"config", "tool_registry"}
    assert third_party & {"fastmcp", "mcp", "rich", "typer"} == set()
    assert "pydantic" in third_party


def test_api_surface_is_unchanged() -> None:
    from hmc_mcp import api

    assert not any("access_policy" in name for name in api.__all__)
```

Add `import subprocess` and `import sys` to the test module's imports.

### Step 3.6 — Run the full guardrail and commit

```sh
uv run --no-sync pytest tests/unit/test_access_policy.py -q --no-cov
just verify
```

`just verify` must be run **bare**. Expected: every static target passes, the whole
pytest suite passes with package coverage at or above 90, `scripts/smoke_mcp.py`
completes its stdio handshake, the wheel and sdist build, artifact validation passes, and
the six CLI group help checks print `verify: all groups load OK`.

```sh
git add src/hmc_mcp/authorization/access_policy.py tests/unit/test_access_policy.py
GIT_EDITOR=true git commit -m "feat(access-policy): load a policy file with fail-fast errors"
```

**Acceptance criteria for Task 3.** `resolve_access_policy_path()` equals
`config_dir() / ACCESS_POLICY_FILENAME`; `load_access_policy` with no `path` reads it. A
written file round-trips to the same `AccessPolicy` as `compile_access_policy` over the
parsed document. An absent file, a directory, non-UTF-8 bytes, and a TOML syntax error
each raise `AccessPolicyError` naming the resolved path. The module imports no first-party
module but `config` and `tool_registry`, never loads `hmc_mcp.server`, and does not appear
in `api.__all__`. `just verify` is green.

## Rollback

Both files are new and nothing imports them. `git revert` of the three task commits, or
deleting the two files, restores the previous state completely. There is no migration, no
persisted state, and no change to any existing runtime path.

## Deliberately not built

Recorded here so a reviewer does not read the absence as an oversight; each is owned by a
later issue in epic #218 and argued in ADR 0036 or the specification's threat model.

- No enforcement anywhere: no registration filter, no dispatch check, no audit event.
- No matcher on `Grant` — exact selector matching, the `vios_uuid`/`vios_partition_id`
  namespace split, `metric_resource`'s dependence on `category`, composites, and `dry_run`
  are #223's.
- No cross-check of connection names against `config.toml`, no resolution of the
  `"<default>"` token, and no all-connections sentinel.
- No module-level "selected policy" global; #221 and #225 pass the object explicitly.
- No subsumption or redundancy lint, and no format `version` key.
- No CLI command, no startup wiring, no operator documentation — those are #225's.
