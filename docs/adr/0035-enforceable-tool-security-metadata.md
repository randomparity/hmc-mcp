# 0035 — Enforceable tool security metadata on the registry entry

## Status

Accepted (2026-08-18)

## Context

The MCP server exposes 128 tools. Two structures describe their risk, and neither
governs the other:

- `src/hmc_mcp/tool_registry.py` collects a `ToolDefinition` holding a handler and an
  optional `ToolAnnotations`. That collection is what actually registers tools into a
  `FastMCP` application.
- `src/hmc_mcp/_app.py` holds `READ_ONLY_TOOLS` and `DESTRUCTIVE_TOOLS`, hand-maintained
  frozensets of tool-name strings that nothing in the registration path reads.

`tests/app/test_capabilities.py::test_every_registered_tool_matches_its_category` checks the
two against the live registry, but a tool named in neither set falls to an `else` branch
that accepts any non-read, non-destructive annotation — including no annotation at all. A
new tool that declares nothing therefore passes today. 49 of the 128 live tools are in that
position.

Epic #218 builds a server-side access boundary on top of this classification: a capability
ceiling by effect class and tool (#221), connection-scope authorization (#222), exact target
constraints (#223), and audit events naming the tool, effect class, and declared selectors
(#224). Each of those reads metadata that does not exist yet, and each fails open if the
metadata can be omitted. The classification has to become one source, attached to the entry
that drives registration, and it has to be impossible to omit.

Two facts constrain the shape. First, the effect vocabulary is fixed by epic #218
requirement 1: `read`, `mutate`, `destructive`, `arbitrary-command`. Second, `readOnlyHint`
and `destructiveHint` are already shipped to MCP clients, so whatever replaces the sets must
keep producing an annotation for every tool without loosening what a cautious client will
auto-approve.

## Decision

`ToolDefinition` carries a required `ToolSecurity` record, declared as keyword arguments on
the module-local `tool()` decorator. `ToolAnnotations` become a derived value; the
`annotations=` parameter and both classification frozensets are removed.

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
    argument: str          # public handler parameter carrying the target identity
    required: bool         # derived from the handler signature, never declared

@dataclass(frozen=True)
class ToolSecurity:
    effect: Effect
    operation: str                              # "<domain>.<verb>" identity
    target_kind: TargetKind                      # primary resource kind acted on
    targets: tuple[TargetSelector, ...] = ()
    connection_argument: str | None = "profile"  # public HMC-connection selector
```

`operation` is required by epic #218 requirement 1 and issue #219's "Expected"; no consumer
in #220–#225 reads it, all of which key on the tool name instead, so it is the one field here
whose consumer does not yet exist.

`target_kind` names the resource whose state the operation changes — for a creation tool,
the container that gains the resource, since no argument names a resource that does not exist
yet. `targets` names every public argument from which an identity is read. The entries whose
`kind` equals `target_kind` are the operation's **subjects**; the rest are **scope** — an
argument that narrows or disambiguates without being what is acted on, such as
`hmc_power_off_lpar`'s optional `system_name_or_uuid`, which exists only to disambiguate
duplicate partition names.

`required` is not declared. It is derived from the handler signature: a parameter with no
default is required, one with a default is not. That distinction is load-bearing and must not
be hand-written, because four destructive tools (`hmc_power_off_lpar`, `hmc_power_off_vios`,
`hmc_delete_vios`, `hmc_restore_vios`) take `system_name_or_uuid=None`, and `hmc_list_lpars`
takes `system_name_or_uuid=None` to mean console-wide. A declaration that could not say the
argument may be absent would make #223 deny the documented normal call. The normative rule
#223 inherits: an absent optional selector is not matched against a target constraint and
does not on its own deny; a required selector is always present and always matched.

Validation lives in one function, `validate_security(security, handler)`. `tool()` calls it
at decoration time, and the escape hatch calls it at import on its own constant, so no
declaration in the repo is unchecked. A contradiction is an `ImportError` rather than a test
failure in one arm of the suite:

1. `effect`, `operation`, and `target_kind` have no defaults; omitting one is a `TypeError`.
2. `operation` matches `^[a-z0-9_]+\.[a-z0-9_]+$`.
3. Every `TargetSelector.argument` and a non-`None` `connection_argument` names a parameter
   of the decorated handler, checked with `inspect.signature`.
4. `target_kind == "none"` requires empty `targets` and `connection_argument is None`.
5. Any other `target_kind` except `"console"` requires at least one `targets` entry whose
   `kind` equals `target_kind`.
6. `targets` names no argument twice.
7. Every handler parameter named in `REQUIRED_TARGET_ARGUMENTS`, a fixed argument-to-kind
   table, appears in `targets` under its mapped kind. Rule 5 forces only the subject, so
   without this `hmc_migrate_lpar` could declare its LPAR and silently drop
   `target_system_name_or_uuid` — the omission this record's own motivating example depends
   on catching. The table is closed and explicit rather than a `*_uuid` naming convention,
   because sub-resource arguments (`vg_uuid`, `adapter_uuid`, `mapping_uuid`, `network_uuid`,
   `lu_udid`) are deliberately not targets and a convention would need a waiver on each, and
   because `name` means a user on `hmc_create_user` and a new LPAR on `hmc_create_lpar`. It
   holds every argument name that unambiguously identifies a resource of one kind, so a new
   entry is a deliberate edit.

Registry-wide uniqueness cannot be seen from one module. `build_tool_security()` is a pure
function over the per-module mappings plus the escape hatch's entry; it raises on a duplicate
tool name or a duplicate operation identity and returns the `TOOL_SECURITY:
Mapping[str, ToolSecurity]` index. `server.py` calls it once at module scope.

It is built from the *collected declarations*, not from what registration produced. That
matters twice: #221 filters registration against a policy, and a classification index that
shrank with the policy would leave #222 unable to explain why a tool is absent; and
`create_mcp()` is called repeatedly — at import, and again per test — so an index accumulated
as a registration side effect would raise on its own second call. Registration and
classification are separate passes over the same declarations. An unknown tool name is a
lookup error for the index's readers, not a permissive default.

Annotations derive from `effect` alone, by a total function:

| effect | derived `ToolAnnotations` |
|---|---|
| `read` | `readOnlyHint=True` |
| `mutate` | `readOnlyHint=False` |
| `destructive` | `readOnlyHint=False, destructiveHint=True` |
| `arbitrary-command` | `readOnlyHint=False, destructiveHint=True` |

`hmc_run_command` is registered outside the collector by `configure_arbitrary_command_tool`.
It carries a module-level `ToolSecurity` constant, validated by `validate_security` at import
and registered through the same derivation, and its entry is in `TOOL_SECURITY`
unconditionally — the operator toggle governs whether the tool is *registered*, not what it
is classified as, and a lookup that failed while the toggle was off would fail open in #222
exactly when the highest-risk tool is live. The contract test enables the toggle before
asserting coverage.

## Consequences

- Every tool declaration grows four to five keyword arguments. That is 128 collector-declared
  tools across 19 domain modules, plus the escape hatch, and every future tool pays the same
  cost — which is the point: the collector has no bare `@tool` form left to forget the
  declaration in. The collector is not the only way in, though: `mcp.tool(...)` can be called
  directly, as `server_command.py` does, and `register_tools` can be pointed at any
  application outside `create_mcp()`. Those holes are closed by the exhaustive contract test,
  which enumerates the live application — with the arbitrary-command toggle on, so 129 tools
  — rather than the collector. FastMCP silently replaces a duplicate tool name with a log
  warning, which is why the duplicate-name check is in `build_tool_security` rather than left
  to registration.
- The wire annotation changes for 74 tools. 47 `mutate` tools go from no annotation to
  `readOnlyHint=False` and 26 `destructive` tools gain an explicit `readOnlyHint=False`; both
  are identical to a client applying MCP's documented defaults (`readOnlyHint` false,
  `destructiveHint` true). `hmc_run_command` gains `destructiveHint=True`, a tightening.
  `hmc_mount_optical_media` already carried `readOnlyHint=False` and does not change. One
  tool does become less restricted — `hmc_read_lpar_boot_order`, below — and that is a
  correction rather than a relaxation.
- `mutate` deliberately does **not** derive `destructiveHint=False`, even though the effect
  vocabulary distinguishes the two. MCP's `destructiveHint` defaults to true, so stating
  `False` would newly invite a cautious client to auto-approve 48 mutating tools. The
  `mutate`/`destructive` distinction is carried by `ToolSecurity` for server-side use in
  #221–#224, not pushed into a client-side hint that would loosen behavior.
- `READ_ONLY_TOOLS` and `DESTRUCTIVE_TOOLS` are deleted from `_app.py` and from the
  `server.py` re-exports. Neither is exported by `api.py`, so ADR 0029's supported reusable
  Python API contract is unaffected. In-repo readers — `tests/app/test_capabilities.py`,
  `tests/unit/test_tool_registry.py`, and the `README.md` structure line — move to
  `TOOL_SECURITY`. ADRs 0003, 0004, 0005, and 0010 mention the frozensets in their
  consequences; those are historical records of decisions this one does not supersede, and
  they are left untouched.
- `hmc_read_lpar_boot_order` is reclassified from untagged/state-changing to `read`. It
  issues one GET and returns boot-order state; leaving it `mutate` would be exactly the
  contradiction this record exists to make impossible. Its annotation changes from none to
  `readOnlyHint=True` — the one loosening change in the set.
- `target_kind` stays coarse. Volume groups, media, adapters, VNICs, and mappings are
  addressed through their owning VIOS or LPAR rather than earning their own kinds, because a
  policy constrains "may create a virtual disk on VIOS X", and a finer vocabulary would add
  declaration surface that nothing in #220–#225 evaluates.
- `metric_resource` is the one kind that is not a single HMC resource type: the metric tools'
  `resource_name_or_uuid` names a managed system or an LPAR depending on the `category`
  argument. #223 cannot bind an exact per-kind constraint to it without resolving `category`
  first; that is left to #223 with the ambiguity stated here rather than papered over by
  guessing a kind at declaration time.
- This record defines and validates the metadata. It does not read it at call time: nothing
  here filters registration, authorizes a call, or emits an audit event. Those are #221,
  #222, #223, and #224. The residual is that if the epic stalls after this entry, the repo
  has paid 128 declarations for a structure whose only live consumer is the annotation
  derivation from `effect`. `effect` alone still earns its keep — it is what the deleted
  frozensets encoded, now unforgettable; `operation` and `targets` are the at-risk part.

## Considered & rejected

- **Do nothing; keep the frozensets and tighten only the test.** Making the `else` branch
  fail closed would catch omission, but the sets would still be a second source that nothing
  in the registration path reads, and they carry no operation identity, target kind, or
  selector declarations — so #220 onward would still have no metadata to evaluate.
- **A central `TOOL_SECURITY` table keyed by tool name, in one module.** Readable in one
  place, and it is what the frozensets already are: a structure separated from the entry that
  registers the tool, kept in agreement only by a test. Reintroducing that separation is the
  defect being removed.
- **Infer the classification from the handler — name prefix, signature, or call graph.** Zero
  declaration cost and no possibility of omission, but a security classification produced by
  a heuristic is wrong silently and in the permissive direction. `hmc_backup_lpar_profiles`
  is destructive and `hmc_read_lpar_boot_order` is a read; neither is derivable from its name
  or its parameters.
- **Keep `annotations=` alongside the new metadata for a transition.** Two ways to say the
  same thing, with nothing forcing them to agree — the same defect one layer down.
- **A single `target_kind` plus a flat `target_selectors: tuple[str, ...]`.** Fewer types,
  but it cannot express a tool that names two kinds, and `hmc_migrate_lpar` names an LPAR and
  a destination system. #223 must constrain both independently.
- **Drop `target_kind`; keep `targets` alone.** Rule 5 makes the primary kind nearly
  derivable from the declared set, and #218 requirement 3 speaks only of "the target kinds
  declared by tool metadata". Kept anyway because requirement 1 names target kind as a field
  in its own right, and because it is what makes a console-scoped operation with no selectors
  declarable at all.
- **Declare `effect` now and defer `operation`, `target_kind`, and `targets` to #223, where
  a consumer first reads them.** This is the smallest change meeting the immediate need and
  fits the repo's no-speculative-features standard. Rejected because #218 requirement 1 and
  #219's acceptance criteria name all four fields as this entry's deliverable, and splitting
  them would make every later entry re-open all 128 declarations.
- **Enforce name and identity uniqueness only in the contract test.** `build_tool_security`
  builds the index either way, so the check is one dictionary lookup, and a collision the
  server refuses to start on beats one reported from a single test arm.
