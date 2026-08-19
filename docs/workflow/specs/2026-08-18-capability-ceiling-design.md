# Composition-time capability ceiling and effective-permission inspection

Issue: [#221](https://github.com/randomparity/hmc-mcp/issues/221) — part of epic #218.
Decision record: [ADR 0037](../../adr/0037-composition-time-capability-ceiling.md).
Builds on [ADR 0035](../../adr/0035-enforceable-tool-security-metadata.md) (tool security
metadata) and [ADR 0036](../../adr/0036-server-access-policy-model.md) (the policy model).

## Goal

A fresh MCP application registers only the tools the startup-selected access policy
permits; the arbitrary-command escape hatch requires both its CLI flag and a policy grant;
and a read-only tool reports the effective permissions of the application it is registered
on, distinguishing what is enforced from what is merely declared.

## Scope

In scope: registration-time filtering, the arbitrary-command intersection, startup policy
selection on `serve`, and the inspection tool.

Out of scope, with owners: connection-scope authorization at dispatch (#222); target
constraint matching at call time (#223); structured audit events (#224); fail-closed
startup when no policy is selected and the legacy-equivalent policy generator (#225).

## Requirements

Each requirement is numbered and testable. R-prefixed identifiers are cited by the plan.

**R1 — Registration is filtered.** `server.create_mcp(policy=None)` returns an
application whose registered tool names are exactly
`{name for name in server.TOOL_SECURITY if policy is None or policy.permits_tool(name)}`,
minus `hmc_run_command`, which is never registered by `create_mcp`.

**R2 — No policy means no ceiling.** `create_mcp()` with no argument registers every tool
`create_mcp` registered before this change, plus `hmc_effective_permissions`: 129 tools.

**R3 — Applications stay independent.** Two `create_mcp` calls with different policies
produce applications whose registries differ accordingly and share no state; a call with
no policy after a call with a restrictive one is unaffected by it.

**R4 — The ceiling parameter is a predicate, not a policy object.**
`tool_registry.tool_module()`'s `register_tools` signature is
`register_tools(mcp: FastMCP, *, permits: Callable[[str], bool] | None = None) -> None`.
`tool_registry.py` imports nothing from `access_policy.py`.

**R5 — Arbitrary command is a conjunction.**
`server_command.configure_arbitrary_command_tool(enabled, mcp, *, permits=None)`
registers `hmc_run_command` when `enabled is True` and (`permits is None` or
`permits("hmc_run_command")`), and removes it in every other combination. It stays
idempotent and symmetric in both directions, as it is today.

**R6 — `arbitrary-command` is still ungrantable by effect class.** A policy whose only
grant is `effects = ["read", "mutate", "destructive"]` does not permit `hmc_run_command`
even with `--enable-arbitrary-command`. This is enforced by `access_policy.py` and this
change must not route around it.

**R7 — Startup selection.** `hmc-mcp serve --access-policy NAME` loads NAME from the
platform-native `access-policy.toml` via
`load_access_policy(NAME, server.TOOL_SECURITY)` and serves an application composed with
it. Without the option, the server composes with no policy.

**R8 — An explicit selection that cannot be loaded is a startup failure.** Any
`AccessPolicyError` — absent file, TOML error, unknown policy name, invalid grant — exits
non-zero with the error's own message on stderr and starts no server.

**R9 — The served application is freshly composed.** `server.main_stdio` and
`server.main_http` compose the application they serve through `create_mcp(policy)`; they
do not serve or mutate the module-level `server.mcp`.

**R10 — The inspection tool exists and is classified.** `hmc_effective_permissions` is in
`server.TOOL_SECURITY` with `effect="read"`, `operation="permissions.describe"`,
`target_kind="none"`, `targets=()`, `connection_argument=None`. It takes no arguments and
its MCP annotations are `annotations_for("read")`.

**R11 — The inspection tool is subject to the ceiling.** A policy that does not permit
`hmc_effective_permissions` yields an application without it. A policy granting
`effects = ["read"]` yields an application with it.

**R12 — Inspection output matches the registry exactly.** For any application `app`
composed by `create_mcp(policy)`, and after any sequence of
`configure_arbitrary_command_tool` calls on it, the `name` values in the tool's `tools`
field equal the names in `app.list_tools()`, as sets and as a sorted sequence.

**R13 — Inspection reports live effect classes.** The `effects` field is the sorted set of
`TOOL_SECURITY[name].effect` over the reported tools — so a read-only policy reports
`("read",)`, and an application with the arbitrary-command tool registered reports
`arbitrary-command` among them.

**R14 — Inspection reports the policy source.** `policy_name` and `policy_source` are the
selected policy's `name` and `source`, or both `None` when no policy is selected.
`ceiling_enforced` is `True` exactly when a policy is selected.

**R15 — Inspection reports grants individually.** `declared_grants` has one entry per
grant in document order, each carrying that grant's sorted tool names, sorted connection
tokens with `None` rendered as `"<default>"`, an `all_targets` boolean, and a `targets`
mapping of target kind to sorted selector strings — empty when `all_targets` is true.
Connections and targets from different grants are never merged.

**R16 — Inspection states what is enforced.** `enforced_dimensions == ("tools",)` and
`declared_only_dimensions == ("connections", "targets")` in every output.

**R17 — Inspection carries no credential.** The output contains no value read from
`config.toml` and no environment variable value. The only path it contains is the policy
file's own path.

**R18 — Withholding the inspection tool is visible.** When `--access-policy` selects a
policy that does not permit `hmc_effective_permissions`, `serve` writes one warning line
to stderr naming the tool and the policy, then starts normally.

**R19 — Guardrails.** `just verify` passes bare, including the 90.00% coverage floor and
the no-`# pragma: no cover` rule in `tests/test_ci_pipeline.py`.

## Design

### Filtering during composition

`tool_module()`'s closure already holds a `list[ToolDefinition]`, each carrying `name`,
`handler`, and `security`. `register_tools` gains a keyword-only `permits` predicate and
skips a definition whose name the predicate rejects. `None` means no ceiling, which is
both the default and the only value any existing caller passes.

`server.create_mcp(policy=None)` derives `permits = None if policy is None else
policy.permits_tool` once, passes it to every domain module, and then registers the
inspection tool if the same predicate admits it. Nothing is ever registered and removed.

### The inspection tool

`src/hmc_mcp/server_permissions.py` owns the result types and a factory:

```python
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

EFFECTIVE_PERMISSIONS_SECURITY: ToolSecurity
def register_permissions_tool(mcp: FastMCP, policy: AccessPolicy | None) -> None: ...
```

`register_permissions_tool` builds an async, argument-free handler named
`hmc_effective_permissions` that closes over `mcp` and `policy`, and registers it with
`annotations_for("read")`. The handler reads the live registry through
`await mcp.list_tools()`, maps each name through `server.TOOL_SECURITY`, and renders the
policy's compiled grants.

`server_permissions.py` must not import `server.py` (`server.py` imports it). The tool
index reaches the handler as a second closure argument supplied by `create_mcp`, keeping
the import direction one-way.

The `declared_only_dimensions` tuple is a module constant, not a computed value: it
changes when #222 and #223 land, and a computation would have nothing to compute from.

### Startup selection

`serve` gains `--access-policy NAME`. It loads the policy before touching the server,
converts `AccessPolicyError` into the CLI's standard `_fail` path (stderr + exit 1),
warns when the policy withholds the inspection tool, and passes the compiled object to
`main_stdio` / `main_http` as `access_policy`. Both entry points compose
`create_mcp(access_policy)` and run that application, passing `permits` on to
`configure_arbitrary_command_tool`.

### Errors

| Condition | Behaviour |
|---|---|
| `--access-policy` names a policy the file lacks | `AccessPolicyError` from `compile_access_policy` → exit 1, message names the file and available policies |
| `access-policy.toml` absent | `AccessPolicyError` "cannot be read" → exit 1 |
| Policy invalid (any ADR 0036 rule) | `AccessPolicyError` with the module's rendered message → exit 1 |
| Policy permits no tool at all | Impossible: ADR 0036 rule P6 rejects a grant naming no tool, and a policy with zero grants fails the `grants` requirement |
| Policy withholds `hmc_effective_permissions` | Warning on stderr; server starts |
| No `--access-policy` | No ceiling; unchanged behaviour |

### Testing

Behaviour, not implementation. New module `tests/app/test_capability_ceiling.py` covers
R1–R3, R5–R6, R11–R17 by composing applications from policies built with
`compile_access_policy` over `server.TOOL_SECURITY` and inspecting the resulting
registries. R12 is asserted by calling the registered inspection handler through the
application and comparing against `app.list_tools()` — including after toggling the
arbitrary-command tool, which is the case a recomputed answer would get wrong.

Existing tests that change: `tests/app/test_application_boundaries.py` (128 → 129),
`tests/app/test_tool_security.py` (G10 becomes a two-name allowance),
`tests/app/test_profile_routing.py` (`_NO_NETWORK_TOOLS` gains the new tool),
`tests/app/test_serve.py` (serve call assertions gain `access_policy`, and the entry
points no longer use `server.mcp`).

## Threat model

**Boundary inventory.** Boundaries this design *adds*: (a) a new MCP tool
`hmc_effective_permissions`, callable by any client that reaches the server, returning a
description of the server's own authorization state; (b) a new CLI option
`--access-policy NAME`, supplied by the operator starting the process, whose value
selects a key inside an operator-owned file. Boundaries this design *narrows*: the MCP
tool surface itself, which the ceiling now bounds. No boundary is widened in the reach
sense — no tool becomes callable that was not callable before.

**Actor model.** The untrusted party is the MCP client and the agent driving it: it
supplies tool calls and arguments, and ADR 0036 already records that a client-facing
annotation is advisory because this party is the one that would have to honour it. The
operator who starts the process and writes `access-policy.toml` is trusted — the file
sits at the same trust level as `config.toml`, and anyone who can write it can widen the
ceiling. The HMC is a trusted downstream service. This design places no trust in the
client and does not change what the operator is trusted with.

**Control per boundary.**

- *MCP tool surface.* Control: the ceiling, applied at registration, derived from the
  policy's grants by `access_policy.py`. Fails closed only within a selected policy; with
  no policy selected there is no control, by the decision recorded in ADR 0037 and owned
  by #225. Leaks on failure: nothing — a withheld tool is absent, not refused.
- *`hmc_effective_permissions`.* No authorization beyond the ceiling that governs every
  tool: the tool is `read`, takes no arguments, performs no I/O, and reaches neither the
  HMC nor `config.toml`. Its disclosure is bounded by construction — it reads
  `TOOL_SECURITY` (compiled-in), the live registry, and the compiled `AccessPolicy`, and
  the policy document's grammar (`extra="forbid"`, four known keys) admits no field a
  credential could occupy. The policy file's absolute path is disclosed deliberately;
  ADR 0037 records why.
- *`--access-policy NAME`.* The value is operator-supplied and used only as a dictionary
  key inside the parsed document. It is never a path, never interpolated into a command,
  and `compile_access_policy` already `repr()`s it in the not-found message so a name
  carrying a control character cannot forge a log line. Validation of the file's contents
  is `access_policy.py`'s, already reviewed under ADR 0036.
- *Composed application.* Control: construction. `create_mcp` never registers a tool the
  predicate rejects, so there is no window in which the registry exceeds the ceiling.

**Explicitly out of scope.**

- A caller may still pass any `profile` value to any permitted tool; `connections` is
  declared, not enforced. Owner: #222. Inspection labels it.
- A caller may still name any target; `targets` is declared, not enforced. Owner: #223.
  Inspection labels it.
- No record is made of a call to a permitted tool, nor of a client probing for a withheld
  one. Owner: #224.
- A deployment that starts with no `--access-policy` has no ceiling. Owner: #225.
- The policy file's permissions are not checked. ADR 0036 decided this: checking one
  credential-adjacent file and not the other is theatre.
- Denial-of-service through repeated `hmc_effective_permissions` calls is not bounded.
  The handler does no I/O and allocates a result proportional to the registry, which is
  fixed at composition; it is cheaper than every other tool on the surface.

## Open questions

None. The one design-changing question — behaviour when no policy is selected — is
decided in ADR 0037 and owned onward by #225.
