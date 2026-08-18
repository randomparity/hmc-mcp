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
| Direct collector contract tests using the removed forms | `tests/unit/test_tool_registry.py:17-69` |

Live census, measured from `mcp.list_tools()` on `main` at 20f3068: **128 tools** — 53
carrying `readOnlyHint=True`, 26 carrying `destructiveHint=True`, 49 carrying neither (48 of
those carry no annotation object at all; `hmc_mount_optical_media` carries
`readOnlyHint=False`). The two frozensets currently agree exactly with the annotations; the
gap is that the 49 uncategorised tools are unconstrained.

`api.py`, the supported reusable Python API of ADR 0029, exports neither frozenset.

FastMCP 3.4.7 does **not** reject a duplicate tool name: it replaces the earlier
registration and logs `WARNING Component already exists`. Duplicate-name detection therefore
has to be ours.

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
    handler: Callable[..., Any]
    security: ToolSecurity
```

Field meanings, normative:

- **`effect`** — the strongest effect any path through the tool may have. A tool with a
  `dry_run` argument declares the effect of its non-dry-run path; dry-run refinement is #223.
- **`operation`** — a `<domain>.<verb>` identity, unique across the composed registry,
  independent of the Python function name. Required by epic #218 requirement 1; no consumer
  in #220–#225 reads it, all of which key on the tool name.
- **`target_kind`** — the kind of the resource whose state the operation changes. For a
  creation tool this is the **container** that gains the resource, because no argument names
  a resource that does not exist yet: `hmc_create_lpar` declares `managed_system`.
  `"console"` means the console as a whole; `"none"` means no HMC resource is involved.
- **`targets`** — one entry per public handler argument that carries a resource identity.
  Entries whose `kind` equals `target_kind` are the operation's **subjects**; the rest are
  **scope** — arguments that narrow or disambiguate without being what is acted on.
- **`required`** — derived from the handler signature by `tool()`, never written by hand: a
  parameter with no default is required, one with a default is not. The normative rule #223
  inherits is that an absent optional selector is not matched against a target constraint and
  does not on its own deny; a required selector is always present and always matched.
- **`connection_argument`** — the public argument selecting the HMC connection profile, or
  `None` when the tool opens no HMC connection.

`required` is derived rather than declared because it is the field a hand-written
declaration would get wrong in the dangerous direction. `hmc_power_off_lpar`,
`hmc_power_off_vios`, `hmc_delete_vios`, and `hmc_restore_vios` all take
`system_name_or_uuid: str | None = None` purely to disambiguate duplicate partition names,
and `hmc_list_lpars(system_name_or_uuid=None)` means console-wide. Treating those as
mandatory targets would make #223 deny the documented normal call on four destructive tools.

### 3.2 Declaration and validation

`tool()` loses its bare form and its `annotations=` parameter. Its new signature is
keyword-only with three required fields, and `targets` is declared as `(kind, argument)`
pairs whose `required` flag `tool()` fills in from the signature:

```python
def tool(*, effect, operation, target_kind, targets=(), connection_argument="profile"):
```

All validation lives in one function, `validate_security(security, handler) -> None`.
`tool()` calls it at decoration time; `server_command.py` calls it at import on its own
constant, so the escape hatch's declaration is checked by the same rules as every other.

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
```

V8 forces the subject; V10 forces every *additional* identity-bearing argument, which is what
stops `hmc_migrate_lpar` from declaring its LPAR and silently dropping
`target_system_name_or_uuid`, and `hmc_attach_disk_to_lpar` from dropping the `vios_uuid` it
writes to. The mapping holds exactly the argument names that unambiguously identify one kind.
It deliberately excludes:

- sub-resource arguments — `vg_uuid`, `adapter_uuid`, `mapping_uuid`, `network_uuid`,
  `lu_udid`, `pool_name`, `disk_name`, `media_name`, `vnic_id`, `adapter_id`, `drc_index` —
  which are addressed through their owning VIOS, LPAR, or system per ADR 0035;
- `name`, which means a user on `hmc_create_user` and a new LPAR on `hmc_create_lpar`;
- `job_href`, a URL rather than an identity.

`REQUIRED_TARGET_ARGUMENTS` and the §3.5 selector table are the same table; G10 pins that.

Every message names the tool (`handler.__name__`) so an import failure identifies the
declaration to fix.

Registry-wide rules cannot be seen from one module:

| id | rule | failure |
|---|---|---|
| V11 | tool names are unique across all domain modules and the escape hatch | `ValueError` naming the collision |
| V12 | `operation` identities are unique across all domain modules and the escape hatch | `ValueError` naming both tools |

`tool_module()` returns a three-tuple `(tool, register_tools, tool_security)`, where
`tool_security()` yields that module's `Mapping[str, ToolSecurity]`.
`build_tool_security(module_mappings, extra)` is a pure function enforcing V11 and V12 and
returning the merged index; `server.py` calls it once at module scope:

```python
TOOL_SECURITY: Mapping[str, ToolSecurity] = build_tool_security(
    [module.tool_security() for module in TOOL_MODULES],
    {"hmc_run_command": HMC_RUN_COMMAND_SECURITY},
)
```

The index is built from the **collected declarations**, not from what registration produced.
That matters twice. #221 filters registration against a policy, and an index that shrank with
the policy would leave #222 unable to explain why a tool is absent. And `create_mcp()` is
called repeatedly — at import (`server.py:256`) and again per test
(`tests/app/test_application_boundaries.py`) — so an index accumulated as a registration side
effect would raise its own duplicate-name error on the second call. Registration and
classification are two separate passes over the same declarations, and `build_tool_security`
being a pure function is also what makes V11/V12 testable without injecting a fake module
into `TOOL_MODULES`.

Readers index `TOOL_SECURITY` directly, so an unknown tool name raises `KeyError` rather than
returning a permissive default.

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

`server_command.py` defines

```python
HMC_RUN_COMMAND_SECURITY = ToolSecurity(
    effect="arbitrary-command",
    operation="command.run",
    target_kind="console",
    connection_argument="profile",
)
validate_security(HMC_RUN_COMMAND_SECURITY, hmc_run_command)
```

at module scope, and `configure_arbitrary_command_tool` registers with
`annotations_for("arbitrary-command")`. It is otherwise unchanged.

Its entry is in `TOOL_SECURITY` **unconditionally**, independent of the operator toggle: the
toggle governs whether the tool is registered, not what it is classified as, and an entry
that appeared only when the toggle was on would make a #222 lookup fail open exactly when the
highest-risk tool is live.

### 3.5 Classification of the live tools

Effect assignment preserves today's classification exactly, with one correction:

- the 53 tools carrying `readOnlyHint=True` become `read`;
- the 26 carrying `destructiveHint=True` become `destructive`;
- the 49 carrying neither become `mutate`, except `hmc_read_lpar_boot_order`, which becomes
  `read` (it performs one GET and returns boot-order state; see ADR 0035);
- `hmc_run_command` becomes `arbitrary-command`.

Resulting census: 54 `read`, 48 `mutate`, 26 `destructive`, and 1 `arbitrary-command`
registered only when the operator enables it.

Target declarations use exactly the `REQUIRED_TARGET_ARGUMENTS` table of §3.2 — that mapping
is the whole selector convention, not a subset of it.

`target_kind` is chosen by one rule: **the kind of the resource whose state changes**, and
for a creation tool, the container that gains the resource. Worked cases, because these are
the ones with more than one defensible answer:

| tool | `target_kind` | why |
|---|---|---|
| `hmc_create_lpar`, `hmc_create_vios`, `hmc_provision_lpar` | `managed_system` | the system gains a partition; no argument names the new one |
| `hmc_migrate_lpar` | `lpar` | the LPAR moves; the destination system is scope |
| `hmc_deploy_partition_template` | `managed_system` | the system gains a partition; the template is read |
| `hmc_map_storage_to_lpar`, `hmc_mount_optical_media` | `vios` | the mapping is created on the VIOS; the LPAR is scope |
| `hmc_attach_disk_to_lpar` | `lpar` | the LPAR gains the disk; the VIOS is scope |
| `hmc_read_lpar_boot_order`, `hmc_set_lpar_boot_order` | `lpar` | the boot order belongs to the LPAR |
| `hmc_list_lpars`, `hmc_list_vios` | `managed_system` | scoped by an optional system; console-wide when omitted |
| `hmc_list_fc_ports`, `hmc_list_sea_adapters`, `hmc_list_storage_mappings`, `hmc_list_optical_mappings` | the required owner (`managed_system` or `vios`) | the optional `lpar_name_or_uuid` is a result filter |

Every tool taking no argument from `REQUIRED_TARGET_ARGUMENTS` and no other resource
identity declares `target_kind="console"` with empty `targets`. That rule governs; the
sixteen tools it covers are `hmc_console_info`, `hmc_capacity_report`, `hmc_fleet_health`,
`hmc_find_placement`, `hmc_list_systems`, `hmc_list_clusters`, `hmc_list_users`,
`hmc_list_password_policies`, `hmc_list_password_policy_status`, `hmc_get_ldap_config`,
`hmc_configure_ldap`, `hmc_remove_ldap_config`, `hmc_list_partition_templates`,
`hmc_list_recent_jobs`, `hmc_list_resources`, and `hmc_list_shared_storage_pools`. Tools
taking `console_uuid` (`hmc_get_available_hmc_ptfs`, `hmc_update_console_software`) are also
`target_kind="console"` but carry a `console` selector, which V8 permits and V10 requires.

`hmc_create_user`, `hmc_modify_user`, `hmc_get_user`, and `hmc_delete_user` declare
`target_kind="user"` with an explicit `TargetSelector("user", "name", required=True)`; V8
forces the subject even though `name` is outside `REQUIRED_TARGET_ARGUMENTS`.

`hmc_list_configured_hosts` takes no arguments and reads the local TOML configuration file,
never an HMC. It declares `target_kind="none"`, `targets=()`, `connection_argument=None`. It
is the only tool with `connection_argument=None`.

Operation identities use the domain prefixes `console`, `config`, `system`, `lpar`,
`lpar_profile`, `boot_order`, `vios`, `adapter`, `vnic`, `network`, `storage`, `media`,
`cluster`, `memory_pool`, `io_slot`, `sriov`, `metrics`, `pcm`, `user`, `password_policy`,
`ldap`, `job`, `template`, `update`, `capacity`, `health`, `placement`, `provision`, and
`command`. The per-tool assignment is enumerated in the implementation plan.

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

1. *Declaration → registry*: V1–V10 run in `validate_security` at import, for the collector
   and the escape hatch alike; V11–V12 run in `build_tool_security` at module scope. A
   contradiction fails the process, not a request. There is no runtime path that constructs a
   `ToolSecurity` from untrusted input, and `ToolSecurity`, `TargetSelector`, and
   `ToolDefinition` are frozen dataclasses, so the index cannot be mutated after
   construction. The exhaustive contract test is the guardrail that a tool registered outside
   the collector cannot skip the boundary.
2. *Registry → client*: the derived annotation is a total function of `effect` with a fixed
   four-entry table, pinned by G4. Every change it makes is audited in ADR 0035's
   consequences and moves in the restricting direction, except the `hmc_read_lpar_boot_order`
   correction.  Annotations carry no host, credential, or configuration value.

**Explicitly out of scope.**

- No registration filtering, call-time authorization, or audit event exists after this
  change. The metadata is inert. A caller who can reach a mutating tool today can still
  reach it; the blast radius of epic #218's problem statement is unchanged until #221.
  That is the accepted, sequenced risk of landing entry 1 of a seven-entry epic.
- Annotations remain hints. A client that ignores them is unaffected, before and after.
- `metric_resource` cannot be bound to an exact per-kind constraint without resolving the
  `category` argument. Stated here and left to #223 rather than guessed at declaration time.
- HMC-side authorization, the LPAR ownership-token convention in `operations_lpar.py`, and
  the password-policy DTO in `documents.py` are unrelated pre-existing mechanisms and are
  not touched.
- Effect assignment is a human judgment recorded in source. The guardrail proves a
  declaration exists and is internally consistent; it cannot prove the declared effect
  matches what the handler does. G7's naming invariant and G11's no-regression snapshot are
  partial mitigations for the two likeliest mistakes, not a general proof.

**AI surface.** No LLM call, prompt, system message, retrieval path, classifier, or agent
loop is added or modified; the `_app.py` server instructions string is untouched. No eval
plan applies because the change produces no model output to judge. It is *not* true that
annotations are inert for agents — clients render them into the tool list and gate
auto-approval on `readOnlyHint`, which is precisely why §3.3's change set is audited for
direction and pinned by G4 and G11.

## 5. Acceptance criteria

Each is a test in `tests/app/test_tool_security.py` unless stated otherwise.

| id | criterion |
|---|---|
| G1 | The set of live tool names from `mcp.list_tools()` is a subset of `set(TOOL_SECURITY)` with the arbitrary-command tool disabled, and equals it with the tool enabled. `TOOL_SECURITY` contains `hmc_run_command` in both states. |
| G2 | `build_tool_security` raises `ValueError` on a duplicate operation identity and on a duplicate tool name, including a collision between a domain module and the escape-hatch entry. Called directly with synthetic mappings; no injection into `TOOL_MODULES`. |
| G3 | For every live tool, every declared `TargetSelector.argument` and every non-`None` `connection_argument` appears in the tool's rendered MCP parameter schema, and every `required` flag matches whether that handler parameter has a default. |
| G4 | For every live tool, `tool.annotations == annotations_for(TOOL_SECURITY[name].effect)`, and `annotations_for` covers exactly the four effect values. |
| G5 | `validate_security` rejects each of V2–V10 with a `ValueError` naming the offending tool; the required-argument cases (V1) raise `TypeError`. One case per rule. |
| G6 | V7, V8, and V10 hold across the whole live registry. `hmc_migrate_lpar` declares both its `lpar` subject and its destination `managed_system` scope; `hmc_attach_disk_to_lpar` declares both its `lpar` subject and its `vios` scope. |
| G7 | Every live tool whose name starts with `hmc_delete_` or `hmc_remove_` has `effect == "destructive"`. |
| G8 | The default application exposes no `arbitrary-command` tool; enabling the escape hatch exposes exactly `hmc_run_command`, classified `arbitrary-command`. `HMC_RUN_COMMAND_SECURITY` passes `validate_security`. |
| G9 | `READ_ONLY_TOOLS`, `DESTRUCTIVE_TOOLS`, `_READ_ONLY`, `_DESTRUCTIVE`, and `_STATE_CHANGING` are absent from `hmc_mcp._app` and `hmc_mcp.server`. |
| G10 | `hmc_list_configured_hosts` declares `target_kind="none"` and `connection_argument=None`; every other live tool declares `connection_argument="profile"`. |
| G11 | No classification regresses. The 53 pre-change `READ_ONLY_TOOLS` names and the 26 `DESTRUCTIVE_TOOLS` names are snapshotted literally in the test file as `LEGACY_READ_ONLY` / `LEGACY_DESTRUCTIVE`; for each name, the derived effect is `read` / `destructive` respectively. `hmc_read_lpar_boot_order` is asserted `read` as the one named upgrade. A census count is deliberately *not* used — it is invariant under permutation. |
| G12 | `just verify` passes, including the `scripts/smoke_mcp.py` stdio smoke path. Not a pytest case. |
| G13 | No new runtime dependency is added to `pyproject.toml`. Not a pytest case. |

`tests/app/test_capabilities.py` loses `test_classification_sets_are_disjoint` and
`test_every_registered_tool_matches_its_category` (G1, G4, and G11 subsume them), and the
frozenset assertions inside `test_attach_disk_is_state_changing_not_destructive`,
`test_fleet_health_is_read_only`, and
`test_decommission_lpar_is_public_destructive_and_schema_stable` are re-pointed at
`TOOL_SECURITY`.

`tests/unit/test_tool_registry.py` keeps all three of its tests — collector isolation,
handler and annotation pass-through, and registration onto independent applications — but
re-expressed against the required keywords, since both the bare `@tool` form and
`annotations=` are gone. Its annotation assertion becomes an `annotations_for` comparison.

## 6. Files

| file | change |
|---|---|
| `src/hmc_mcp/tool_registry.py` | add `Effect`, `TargetKind`, `TargetSelector`, `ToolSecurity`, `REQUIRED_TARGET_ARGUMENTS`, `annotations_for`, `validate_security`, `build_tool_security`; rewrite `ToolDefinition` and `tool_module()` |
| `src/hmc_mcp/_app.py` | delete the three annotation constants, both frozensets, and the stale comment block |
| `src/hmc_mcp/server.py` | drop the two re-exports; unpack the three-tuple from each domain module; build `TOOL_SECURITY` at module scope |
| `src/hmc_mcp/server_command.py` | declare and validate `HMC_RUN_COMMAND_SECURITY`; register with the derived annotation |
| 19 `src/hmc_mcp/server_*.py` domain modules | declare security metadata on all 128 collected tools; unpack the three-tuple; drop the `_READ_ONLY` / `_DESTRUCTIVE` / `_STATE_CHANGING` imports |
| `tests/app/test_tool_security.py` | new — G1–G11 |
| `tests/app/test_capabilities.py` | remove the superseded category tests; re-point three assertions at `TOOL_SECURITY` |
| `tests/unit/test_tool_registry.py` | re-express all three tests against the new collector signature |
| `README.md` | update the `_app.py` structure line |
