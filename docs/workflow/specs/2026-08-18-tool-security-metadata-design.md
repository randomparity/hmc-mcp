# Enforceable tool security metadata and exhaustive classification

Issue: [#219](https://github.com/randomparity/hmc-mcp/issues/219) — part of epic
[#218](https://github.com/randomparity/hmc-mcp/issues/218).
Decision record: [ADR 0035](../../adr/0035-enforceable-tool-security-metadata.md).

## 1. Goal

Give every live MCP tool one authoritative, server-enforced security classification —
effect class, operation identity, target kind, and the public arguments carrying connection
and target selectors — declared on the registry entry that drives registration. Derive the
MCP `ToolAnnotations` from it, delete the parallel hint-only classification sets, and add a
guardrail that fails when a tool omits or contradicts the metadata.

Out of scope, owned elsewhere in epic #218: access-policy models and loading (#220),
capability-ceiling filtering and permission inspection (#221), connection-scope
authorization (#222), target-constraint enforcement and composite/dry-run semantics (#223),
audit events (#224), fail-closed startup and policy generation (#225).

## 2. Current state

| Fact | Location |
|---|---|
| `ToolDefinition` holds only `handler` and `annotations` | `src/hmc_mcp/tool_registry.py:13-16` |
| `tool()` accepts a bare `@tool` form and an optional `annotations=` | `src/hmc_mcp/tool_registry.py:23-32` |
| `_READ_ONLY` / `_DESTRUCTIVE` / `_STATE_CHANGING` annotation constants | `src/hmc_mcp/_app.py:131-133` |
| `READ_ONLY_TOOLS` (53 names) / `DESTRUCTIVE_TOOLS` (26 names) | `src/hmc_mcp/_app.py:135-222` |
| Re-exported from `server.py` | `src/hmc_mcp/server.py:43-44` |
| 19 domain modules call `tool_module()` | `src/hmc_mcp/server_*.py` |
| `hmc_run_command` registers outside the collector | `src/hmc_mcp/server_command.py:26-32` |
| Category test with a passing `else` branch | `tests/app/test_capabilities.py:144-160` |

Live census, measured from `mcp.list_tools()` on `main` at 20f3068: **128 tools** — 53
carrying `readOnlyHint=True`, 26 carrying `destructiveHint=True`, 49 carrying neither (48 of
those carry no annotation object at all; `hmc_mount_optical_media` carries
`readOnlyHint=False`). The two frozensets currently agree exactly with the annotations; the
gap is that the 49 uncategorised tools are unconstrained.

`api.py`, the supported reusable Python API of ADR 0029, exports neither frozenset.

## 3. Design

### 3.1 Types

Added to `src/hmc_mcp/tool_registry.py`:

```python
Effect = Literal["read", "mutate", "destructive", "arbitrary-command"]

TargetKind = Literal[
    "none", "console", "managed_system", "lpar", "vios", "cluster",
    "shared_storage_pool", "user", "password_policy", "job", "template",
    "metric_resource",
]

@dataclass(frozen=True)
class TargetSelector:
    kind: TargetKind
    argument: str

@dataclass(frozen=True)
class ToolSecurity:
    effect: Effect
    operation: str
    target_kind: TargetKind
    targets: tuple[TargetSelector, ...] = ()
    connection_argument: str | None = "profile"

@dataclass(frozen=True)
class ToolDefinition:
    handler: Callable[..., Any]
    security: ToolSecurity
```

Field meanings, normative:

- **`effect`** — the strongest effect any path through the tool may have. A tool with a
  `dry_run` argument declares the effect of its non-dry-run path; dry-run refinement is #223.
- **`operation`** — a `<domain>.<verb>` identity, unique across the composed registry,
  independent of the Python function name. Required by epic #218 requirement 1; no consumer
  in #220–#225 reads it, all of which key on the tool name.
- **`target_kind`** — the primary HMC resource kind the operation acts on. `"console"` means
  the console as a whole; `"none"` means no HMC resource is involved.
- **`targets`** — one entry per public handler argument that carries a target identity, in
  declaration order.
- **`connection_argument`** — the public argument selecting the HMC connection profile, or
  `None` when the tool opens no HMC connection.

### 3.2 Declaration and validation

`tool()` loses its bare form and its `annotations=` parameter. Its new signature is
keyword-only with three required fields:

```python
def tool(*, effect, operation, target_kind, targets=(), connection_argument="profile"):
```

Validation runs inside `tool()`, at import time, against the decorated handler:

| id | rule | failure |
|---|---|---|
| V1 | `effect`, `operation`, `target_kind` are required | `TypeError` from the call signature |
| V2 | `effect` is in the `Effect` vocabulary | `ValueError` naming the tool and the bad value |
| V3 | `target_kind` and every `TargetSelector.kind` are in the `TargetKind` vocabulary | `ValueError` |
| V4 | `operation` matches `^[a-z0-9_]+\.[a-z0-9_]+$` | `ValueError` |
| V5 | every `TargetSelector.argument` is a parameter of the handler | `ValueError` naming tool, argument, and the actual parameters |
| V6 | a non-`None` `connection_argument` is a parameter of the handler | `ValueError` |
| V7 | `target_kind == "none"` ⟹ `targets == ()` and `connection_argument is None` | `ValueError` |
| V8 | `target_kind not in ("none", "console")` ⟹ at least one `targets` entry with `kind == target_kind` | `ValueError` |
| V9 | no argument appears twice in `targets` | `ValueError` |
| V10 | every handler parameter named in `REQUIRED_TARGET_ARGUMENTS` appears in `targets` under its mapped kind | `ValueError` naming tool, argument, and kind |

`REQUIRED_TARGET_ARGUMENTS` is a fixed mapping in `tool_registry.py`:

```python
REQUIRED_TARGET_ARGUMENTS: Mapping[str, TargetKind] = {
    "lpar_name_or_uuid": "lpar",
    "lpar_uuid": "lpar",
    "system_name_or_uuid": "managed_system",
    "target_system_name_or_uuid": "managed_system",
    "vios_name_or_uuid": "vios",
    "cluster_uuid": "cluster",
}
```

V10 exists because V8 alone would let `hmc_migrate_lpar` declare only its LPAR and silently
drop `target_system_name_or_uuid`, leaving #223 with nothing to constrain the migration
destination by. The mapping is a closed explicit table rather than a `*_uuid` naming
convention: sub-resource arguments (`vg_uuid`, `adapter_uuid`, `mapping_uuid`, `lu_udid`,
`network_uuid`) are deliberately not targets, and a convention would need a waiver on each.
Adding a tool that introduces a new owner-resource argument means adding a table row —
a deliberate act, which is the point.

Every message names the tool (`handler.__name__`) so an import failure identifies the
declaration to fix.

Registry-wide rules cannot be seen from one module. `server.create_mcp()` — the composing
function, not the same-named empty-application factory in `_app.py` — builds the index while
composing:

| id | rule | failure |
|---|---|---|
| V11 | tool names are unique across all domain modules | `ValueError` naming the collision |
| V12 | `operation` identities are unique across all domain modules and the escape hatch | `ValueError` naming both tools |

`register_tools(mcp)` returns `Mapping[str, ToolSecurity]` for the module it registered;
`create_mcp()` merges those with the escape hatch's entry, raising on V11/V12, and stores the
merged mapping as the module-level `TOOL_SECURITY`. Composition is deterministic over the
fixed `TOOL_MODULES` tuple, so every application `create_mcp()` produces carries the same
classification and a module-level mapping is correct; #221 filters registration against a
policy, which does not change what a tool is classified as. Readers index it directly, so an
unknown tool name raises `KeyError` rather than returning a permissive default.

### 3.3 Derived annotations

`tool_registry.annotations_for(effect)` is the single derivation, a total function over the
four effect values:

| effect | `ToolAnnotations` |
|---|---|
| `read` | `readOnlyHint=True` |
| `mutate` | `readOnlyHint=False` |
| `destructive` | `readOnlyHint=False, destructiveHint=True` |
| `arbitrary-command` | `readOnlyHint=False, destructiveHint=True` |

`register_tools` calls `mcp.tool(definition.handler, annotations=annotations_for(...))`.
`_READ_ONLY`, `_DESTRUCTIVE`, `_STATE_CHANGING`, `READ_ONLY_TOOLS`, and `DESTRUCTIVE_TOOLS`
are deleted from `_app.py`, and the two frozensets are removed from the `server.py`
re-export block.

`mutate` intentionally leaves `destructiveHint` unset — see ADR 0035 consequences. The
annotation change set is 47 tools gaining `readOnlyHint=False` where they had no annotation,
26 destructive tools gaining an explicit `readOnlyHint=False`, `hmc_run_command` gaining
`destructiveHint=True`, and `hmc_read_lpar_boot_order` gaining `readOnlyHint=True`.
`hmc_mount_optical_media` already carries `readOnlyHint=False` and does not change.

### 3.4 The escape hatch

`server_command.py` defines `HMC_RUN_COMMAND_SECURITY = ToolSecurity(effect=
"arbitrary-command", operation="command.run", target_kind="console",
connection_argument="profile")` and registers with `annotations_for("arbitrary-command")`.
`configure_arbitrary_command_tool` is otherwise unchanged.

The escape hatch registers onto an application composition has already finished, so it cannot
be added to the index by composition. `create_mcp()` therefore seeds its entry into
`TOOL_SECURITY` **unconditionally**, independent of the operator toggle: the toggle governs
whether the tool is registered, not what it is classified as, and an entry that appeared only
when the toggle was on would make a #222 lookup fail open exactly when the highest-risk tool
is live.

### 3.5 Classification of the 128 live tools

Effect assignment preserves today's classification exactly, with one correction:

- the 53 tools carrying `readOnlyHint=True` become `read`;
- the 26 carrying `destructiveHint=True` become `destructive`;
- the 49 carrying neither become `mutate`, except `hmc_read_lpar_boot_order`, which becomes
  `read` (it performs one GET and returns boot-order state; see ADR 0035);
- `hmc_run_command` becomes `arbitrary-command`.

Resulting census: 54 `read`, 48 `mutate`, 26 `destructive`, and 1 `arbitrary-command`
registered only when the operator enables it.

Target declarations follow the existing argument conventions:

| pattern | declaration |
|---|---|
| `lpar_name_or_uuid` / `lpar_uuid` | `TargetSelector("lpar", …)` |
| `system_name_or_uuid`, `target_system_name_or_uuid` | `TargetSelector("managed_system", …)` |
| `vios_name_or_uuid` | `TargetSelector("vios", …)` |
| `cluster_uuid` | `TargetSelector("cluster", …)` |
| `ssp_uuid` | `TargetSelector("shared_storage_pool", …)` |
| `name` on user tools | `TargetSelector("user", …)` |
| `policy_name` | `TargetSelector("password_policy", …)` |
| `job_uuid` | `TargetSelector("job", …)` |
| `template_uuid`, `draft_template_uuid` | `TargetSelector("template", …)` |
| `console_uuid` | `TargetSelector("console", …)` |
| `resource_name_or_uuid` on metric tools | `TargetSelector("metric_resource", …)` |

Console-wide reads and mutations (`hmc_console_info`, `hmc_capacity_report`,
`hmc_fleet_health`, `hmc_list_systems`, `hmc_list_clusters`, `hmc_list_users`,
`hmc_list_password_policies`, `hmc_list_password_policy_status`, `hmc_get_ldap_config`,
`hmc_configure_ldap`, `hmc_remove_ldap_config`, `hmc_list_partition_templates`,
`hmc_list_recent_jobs`, `hmc_list_resources`, `hmc_list_shared_storage_pools`) declare
`target_kind="console"` with empty `targets`.

`hmc_list_configured_hosts` takes no arguments and reads the local TOML configuration file,
never an HMC. It declares `target_kind="none"`, `targets=()`, `connection_argument=None`. It
is the only tool with `connection_argument=None`.

Sub-resources are addressed through their owning resource: `hmc_create_virtual_disk(
vios_name_or_uuid, vg_uuid, disk_name)` declares one `vios` target, not three. A policy
constrains which VIOS may be written to, and `vg_uuid` / `disk_name` are not policy targets.

Operation identities use the domain prefixes `console`, `config`, `system`, `lpar`,
`lpar_profile`, `boot_order`, `vios`, `adapter`, `vnic`, `network`, `storage`, `media`,
`cluster`, `memory_pool`, `io_slot`, `sriov`, `metrics`, `pcm`, `user`, `password_policy`,
`ldap`, `job`, `template`, `update`, `capacity`, `health`, `placement`, `provision`, and
`command`.

## 4. Threat model

This change is security-relevant on intent: it defines the classification a later
authorization boundary reads, and it changes hints that MCP clients use for gating.

**Boundary inventory.** The change adds no runtime data path and no new entry point. It
widens one existing boundary and touches one client-facing surface:

1. *Tool declaration → registry* (in-process, developer-controlled). New input: the security
   keyword arguments, all Python literals in first-party source. Crossed at import.
2. *Registry → MCP client* (process boundary, client-controlled consumer). Existing. The
   derived `ToolAnnotations` travel here. No caller-supplied data crosses inward as a result
   of this change.

**Actor model.** The untrusted parties for this deployment are the MCP client and whatever
drives it — an LLM agent, possibly one exposed to prompt injection through HMC data it
reads. The trusted parties are the repository's own source and the operator running the
process. This change places its trust entirely in first-party source: the metadata is
compiled-in literals, and no request field, environment variable, or configuration file can
supply or alter it. That is deliberate and is the property #220–#225 depend on — epic #218
requirement 2 requires that ordinary MCP tool arguments never select or widen policy.

**Control per boundary.**

1. *Declaration → registry*: V1–V9 validate at import; V10–V11 validate at composition. A
   contradiction fails the process, not a request. There is no runtime path that constructs
   a `ToolSecurity` from untrusted input, and `ToolSecurity`/`ToolDefinition` are frozen
   dataclasses, so the index cannot be mutated after composition. The exhaustive contract
   test is the guardrail that a new tool cannot skip the boundary.
2. *Registry → client*: the derived annotation is a total function of `effect` with a fixed
   four-entry table, pinned by a contract test. The derivation is audited in §3.3 to move
   only in the restricting direction, except the `hmc_read_lpar_boot_order` correction. It
   leaks nothing: annotations carry no host, credential, or configuration value.

**Explicitly out of scope.**

- No registration filtering, call-time authorization, or audit event exists after this
  change. The metadata is inert. A caller who can reach a mutating tool today can still
  reach it; the blast radius of epic #218's problem statement is unchanged until #221.
  That is the accepted, sequenced risk of landing entry 1 of a seven-entry epic.
- Annotations remain hints. A client that ignores them is unaffected, before and after.
- HMC-side authorization, the LPAR ownership-token convention in `operations_lpar.py`, and
  the password-policy DTO in `documents.py` are unrelated pre-existing mechanisms and are
  not touched.
- Effect assignment is a human judgment recorded in source. The guardrail proves a
  declaration exists and is internally consistent; it cannot prove the declared effect
  matches what the handler does. The `hmc_delete_`/`hmc_remove_` naming invariant (§5, G7)
  is a partial mitigation for the most likely mistake, not a general proof.

**AI surface.** No LLM call, prompt, system message, retrieval path, classifier, or agent
loop is added or modified. The `_app.py` server instructions string is untouched. Tool
annotations are client-side metadata rather than model input under this server's control, and
their exact value is pinned by G4, so no eval plan applies.

## 5. Acceptance criteria

Each is a test in `tests/app/test_tool_security.py` unless stated otherwise.

| id | criterion |
|---|---|
| G1 | The set of live tool names from `mcp.list_tools()` is a subset of `set(TOOL_SECURITY)` with the arbitrary-command tool disabled, and equals it with the tool enabled. `TOOL_SECURITY` always contains `hmc_run_command`. |
| G2 | Every `ToolSecurity.operation` is unique across the composed registry, and `create_mcp()` raises `ValueError` when two modules declare the same identity. |
| G3 | Every declared `TargetSelector.argument` and every non-`None` `connection_argument` is a parameter of its handler, and appears in the tool's rendered MCP parameter schema. |
| G4 | For every live tool, `tool.annotations == annotations_for(TOOL_SECURITY[name].effect)`, and `annotations_for` covers exactly the four effect values. |
| G5 | `tool()` rejects each of V2–V10 with a `ValueError` naming the offending tool; the required-argument cases (V1) raise `TypeError`. |
| G6 | `target_kind` and `targets` are internally consistent for every live tool: V7, V8, and V10 hold across the whole registry. Specifically, `hmc_migrate_lpar` declares both its `lpar` and its destination `managed_system` target. |
| G7 | Every live tool whose name starts with `hmc_delete_` or `hmc_remove_` has `effect == "destructive"`. |
| G8 | The default application exposes no `arbitrary-command` tool; enabling the escape hatch exposes exactly `hmc_run_command`, classified `arbitrary-command`. |
| G9 | `READ_ONLY_TOOLS`, `DESTRUCTIVE_TOOLS`, `_READ_ONLY`, `_DESTRUCTIVE`, and `_STATE_CHANGING` are absent from `hmc_mcp._app` and `hmc_mcp.server`. |
| G10 | `hmc_list_configured_hosts` declares `target_kind="none"` and `connection_argument=None`; every other live tool declares `connection_argument="profile"`. |
| G11 | Every live tool's `effect` matches its pre-change classification, with the single documented exception of `hmc_read_lpar_boot_order` — pinned by asserting the derived read/mutate/destructive census is 54/48/26 with the escape hatch disabled. |
| G12 | `just verify` passes, including the `scripts/smoke_mcp.py` stdio smoke path. Not a pytest case. |
| G13 | No new runtime dependency is added to `pyproject.toml`. Not a pytest case. |

`tests/app/test_capabilities.py` loses `test_classification_sets_are_disjoint`,
`test_every_registered_tool_matches_its_category`, and the frozenset assertions inside
`test_attach_disk_is_state_changing_not_destructive`,
`test_fleet_health_is_read_only`, and
`test_decommission_lpar_is_public_destructive_and_schema_stable`; those tools' effects are
asserted against `TOOL_SECURITY` instead, and G1/G4 subsume the category test.

## 6. Files

| file | change |
|---|---|
| `src/hmc_mcp/tool_registry.py` | add `Effect`, `TargetKind`, `TargetSelector`, `ToolSecurity`, `annotations_for`; rewrite `ToolDefinition` and `tool_module()` |
| `src/hmc_mcp/_app.py` | delete the three annotation constants, both frozensets, and the stale comment block |
| `src/hmc_mcp/server.py` | drop the two re-exports; build and expose `TOOL_SECURITY` and `ARBITRARY_COMMAND_SECURITY` in `create_mcp()` |
| `src/hmc_mcp/server_command.py` | declare `HMC_RUN_COMMAND_SECURITY`; register with the derived annotation |
| 19 `src/hmc_mcp/server_*.py` domain modules | declare security metadata on all 128 collected tools; drop the `_READ_ONLY` / `_DESTRUCTIVE` / `_STATE_CHANGING` imports |
| `tests/app/test_tool_security.py` | new — G1–G10 |
| `tests/app/test_capabilities.py` | remove the superseded category tests; re-point three assertions at `TOOL_SECURITY` |
| `README.md` | update the `_app.py` structure line |
