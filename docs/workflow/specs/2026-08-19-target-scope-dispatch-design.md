# Dispatch-time exact target-constraint authorization

Issue: [#223](https://github.com/randomparity/hmc-mcp/issues/223) — part of epic #218.
Decision record: [ADR 0039](../../adr/0039-dispatch-time-target-scope.md).
Builds on [ADR 0035](../../adr/0035-enforceable-tool-security-metadata.md) (selector
metadata), [ADR 0036](../../adr/0036-server-access-policy-model.md) (the grant model and
its combination rule), [ADR 0037](../../adr/0037-composition-time-capability-ceiling.md)
(the capability ceiling), and [ADR 0038](../../adr/0038-dispatch-time-connection-scope.md)
(the dispatch seam this entry extends). The boundary statement cites
[ADR 0029](../../adr/0029-supported-reusable-python-api-contract.md).

## Goal

An MCP call is reauthorized against the startup-selected access policy immediately before
its handler runs, and executes only when a *single* grant covers its tool, its connection,
**and** every target its declared selectors name. A target the policy does not name, a
declared selector the call omits, a selector value the boundary cannot read, and a tool
whose selectors cannot bound it all fail closed — with a stable, actionable, non-secret
error, and without constructing an HMC client or opening an SSH connection.

## Scope

In scope: extraction of declared target selectors from the bound arguments; exact matching
against `Grant.targets` and the `ALL_TARGETS` sentinel; the `exhaustive_targets`
declaration and the two static guardrails that back it; the load-time extension of
ADR 0036's coverage rule; the target denial error; the split of the dispatch decision into
`connection_scope` / `target_scope` / `dispatch_scope`; the effective-permissions labels;
and explicit tests classifying the three `dry_run` paths.

Out of scope, with owners: structured redacted audit events and reason codes (#224);
fail-closed startup when no policy is selected, `create_mcp`'s no-policy default, and the
legacy-equivalent policy generator (#225); the `declared_only_dimensions` encoding under
registry drift (#254); descending into structured parameters so `hmc_provision_lpar`
becomes narrowable (follow-up filed by this issue); cross-system disambiguation for the 20
`lpar` tools that declare no `managed_system` selector, and `metric_resource`'s `category`
(same follow-up); name↔UUID canonicalization (epic open question, retained as exact
matching).

## Requirements

Each requirement is numbered and testable. R-prefixed identifiers are cited by the plan.

**R1 — One loop, in one module, over one grant at a time.** `dispatch_scope.py` holds the
only iteration over `AccessPolicy.grants_for(name)` in the package. For each grant it
evaluates the connection condition **and** the target condition together and returns on the
first grant satisfying both. Neither `connection_scope` nor `target_scope` receives the
policy or a grant sequence, so neither can union a dimension across grants. A policy whose
grant 0 permits the connection and whose grant 1 permits the targets denies.

**R2 — `connection_authorizer` is replaced, not kept.** `connection_scope.py` exports no
authorizer and no grant loop after this change. `server._gates` calls
`dispatch_scope.dispatch_authorizer(policy)`. No re-export, alias, or shim exists.

**R3 — The dimension binds every wrapped tool.** Every tool `tool_registry.authorized`
wraps — that is, every tool whose `ToolSecurity.connection_argument` is not `None`, under a
selected policy — is subject to the target condition, regardless of effect class. The two
`connection_argument = None` tools remain unwrapped.

**R4 — Extraction is total and reads only declared selectors.** For each `TargetSelector`
in `security.targets`, `target_scope` reads `arguments[selector.argument]` (indexed, not
`.get`) and yields exactly one of:

| bound value | result |
|---|---|
| `str`, including `""` | that string |
| `int`, and not `bool` | `str(value)` |
| `None` | `ABSENT` |
| anything else, including `bool` | `UNREADABLE` |

`""` is a string, not `ABSENT`. It denies under any table by construction, since
`access_policy._check_entries` rejects an empty allowlist entry so no table can contain
it; under `all-targets` it is permitted. Note the asymmetry with `selected_connection`,
where `""` *is* the default connection.

Extraction performs no filesystem, network, or environment read. A `KeyError` from the
indexed read is a malformed call and propagates before the handler runs.

**R5 — A `targets` table covers a call only when everything matches.** For a grant whose
`targets` is a `Mapping`, the target condition holds iff all of:

1. `security.exhaustive_targets` is `True`;
2. every extracted selector is a string (no `ABSENT`, no `UNREADABLE`);
3. for every extracted selector `(kind, value)`: `kind in grant.targets` **and**
   `value in grant.targets[kind]`.

Comparison is exact `str` equality — no case folding, no strip, no glob, no name↔UUID
resolution.

**R6 — `all-targets` widens everything except unreadability.** For a grant whose `targets`
is `ALL_TARGETS`, the target condition holds iff no extracted selector is `UNREADABLE`.
`ABSENT` is permitted, and `exhaustive_targets` is not consulted.

**R7 — `exhaustive_targets` is declared, stored, and validated.**
`ToolSecurity.exhaustive_targets: bool` is a new field. `tool_module().tool` takes
`exhaustive_targets: bool = True` and stores `exhaustive_targets and bool(targets)`, so a
selector-less tool is always `False`. `validate_security` rejects
`exhaustive_targets=True` with an empty `targets` tuple. `HMC_RUN_COMMAND_SECURITY` and
`EFFECTIVE_PERMISSIONS_SECURITY` are constructed directly and both carry `False`.

**R8 — Exactly eight tools declare `exhaustive_targets=False` explicitly**:
`hmc_provision_lpar`, `hmc_backup_lpar_profiles`, `hmc_restore_lpar_profiles`,
`hmc_add_vfc_adapter`, `hmc_add_vscsi_adapter`, `hmc_attach_disk_to_lpar`, `hmc_get_job`,
`hmc_wait_for_job`. A test pins the full set of `False` tools (those eight plus the 19
selector-less ones) so a change is deliberate, and a second test pins the six tools whose
payload-source arguments are outside the target dimension **by decision** rather than by
omission.

**R9 — Guardrail: no unbounded identity argument on an exhaustive tool.** A static check
over the parsed `src/hmc_mcp/server_*.py` sources fails when a tool declaring
`exhaustive_targets=True` has a handler parameter, or a field of a dataclass/pydantic-model
parameter one level down, whose name is in `REQUIRED_TARGET_ARGUMENTS` but is not a declared
selector, or whose name is in
`UNBOUNDED_ARGUMENTS = {"cmd", "file_path", "job_href", "vios_partition_id"}`. The check is proven to
bite on three fixture sources: a nested identity, an undeclared top-level identity, and an
argument no `TargetKind` can express. A further test asserts the check's output equals the
declared `exhaustive_targets=False` set exactly, in both directions.

**R10 — Guardrail: every declared selector is referenced by its handler.** A static check
over the same sources fails when a handler's body never loads a name matching one of its
declared selector arguments. Proven to bite by a fixture handler that accepts
`lpar_name_or_uuid` and ignores it. All 130 live handlers pass.

**R11 — Guardrail: every declared selector argument is annotated `str`, `str | None`, or
`int`.** This is what makes `UNREADABLE` unreachable through MCP, where the generated
schema types the parameter accordingly. A fourth annotation type fails the suite rather
than silently denying at runtime.

**R12 — Load-time coverage extends to every declared selector kind.** In
`access_policy._compile_grant`, for each tool named **explicitly** in a grant's `tools`
under a `targets` table, every declared selector's kind — `required` or not — must appear in
the table, or the load fails naming the tool and the kind. Tools reached through `effects`
stay exempt.

**R13 — Load-time rejection of a dead selector-less grant.** In the same place, a grant
under a `targets` table that explicitly names a tool with `exhaustive_targets=False` fails
the load, naming the tool and directing the operator to `targets = "all-targets"`. Tools
reached through `effects` stay exempt and are denied at call time by R5.1.

**R12a — Both load-time rules exempt a tool declaring no connection argument.**
`hmc_effective_permissions` and `hmc_list_configured_hosts` are never wrapped by
`tool_registry.authorized`, so no authorizer runs on them and a grant naming either beside
a `targets` table is not dead — it is bounded by the ceiling alone, exactly as before. A
test proves a policy naming `hmc_effective_permissions` under a table still loads and the
tool still answers.

**R14 — A target denial makes no outbound attempt.** A denied call constructs no
`HMCClient`, calls no `common.build_config` / `client_from_env`, and opens no SSH
connection. Asserted on client construction, not only on the raised error.

**R15 — Two denial templates, selected after the decision.** `TargetScopeError` is raised
when at least one grant matched the connection but no grant matched the targets;
`ConnectionScopeError` otherwise. The selection reads the per-grant connection results the
decision already computed and cannot change the decision. `ConnectionScopeError`'s template
is byte-identical to ADR 0038's.

**R16 — `TargetScopeError` leaks nothing.** Its templates substitute only: the tool name,
the policy name, the failing selector's `kind`, and either the caller's own value under
`repr()` or — when the selector is `ABSENT` — the selector's argument name. No host, port,
user, credential, resolved endpoint, filesystem path, allowlist content, grant count, or
chained exception text. Proven by a sentinel-secret test on the real user path.

**R17 — `dry_run` is never read by the authorizer.** The decision is identical for
`dry_run=True` and `dry_run=False` on all three tools that accept it, under both a matching
and a non-matching grant. The three modules that make the decision —
`src/hmc_mcp/dispatch_scope.py`, `src/hmc_mcp/target_scope.py`, and
`src/hmc_mcp/connection_scope.py` — contain no reference to `dry_run`.

**R18 — The three dry-run paths are classified by test.** For `hmc_provision_lpar`,
`hmc_decommission_lpar`, and `hmc_attach_disk_to_lpar`, a test proves the `dry_run=True`
path issues zero mutating HMC requests (no `POST`, `PUT`, or `DELETE`).

**R19 — Effective permissions report targets as enforced.**
`ENFORCED_DIMENSIONS == ("tools", "connections", "targets")` and
`DECLARED_ONLY_DIMENSIONS == ()`. `ToolPermission` gains `exhaustive_targets: bool`.

**R20 — No behaviour change without a policy.** `create_mcp(None)` registers every handler
unwrapped, exactly as before. Nothing in this change touches `cli_app.py` or `create_mcp`'s
default.

## Design

### `tool_registry.py`

`ToolSecurity` gains `exhaustive_targets: bool = False`. The default is the fail-closed
value so a directly constructed record — `HMC_RUN_COMMAND_SECURITY`,
`EFFECTIVE_PERMISSIONS_SECURITY`, and every record a test builds by hand — is safe without
naming the field. The `tool()` decorator overrides it with `exhaustive_targets and
bool(targets)` after `build_targets` has run, so the decorated surface gets `True` wherever
it is earned.

`validate_security` gains one rule, beside the existing `target_kind` rules:

```
if security.exhaustive_targets and not security.targets:
    raise ValueError(f"{name}: exhaustive_targets requires at least one target selector")
```

`REQUIRED_TARGET_ARGUMENTS` is unchanged. A new module-level
`UNBOUNDED_ARGUMENTS: frozenset[str] = frozenset({"cmd", "file_path", "job_href",
"vios_partition_id"})` names the argument
names that carry an identity no `TargetKind` can bound; it is data for R9's guardrail and is
read by nothing at runtime.

### `target_scope.py` (new)

```
ABSENT: Final = object()      # an optional selector the caller omitted
UNREADABLE: Final = object()  # a value the boundary declines to read

class TargetScopeError(Exception): ...

def selected_targets(security, arguments) -> tuple[tuple[TargetKind, str | object], ...]
def targets_permitted(grant_targets, security, extracted) -> bool
def target_denial(name, policy_name, security, extracted) -> TargetScopeError
```

`selected_targets` implements R4's table. `targets_permitted` implements R5 and R6 against
one grant's `targets` value.

`target_denial` receives no grant and no policy object, because after the loop there is no
single grant to blame — so its selection must be a total function of `security` and
`extracted` alone:

1. any `UNREADABLE` → `_UNREADABLE_VALUE`, naming that selector's kind and argument but
   **not** its value. A malformed call is reported as malformed first: no policy edit fixes
   it, so the other three messages would all be misleading advice.
2. else `not security.exhaustive_targets` → `_UNBOUNDABLE`, naming no selector.
3. else any `ABSENT` → `_MISSING`, naming the first one's kind and argument.
4. else → `_DENIED`, rendering **every** extracted selector as `kind=repr(value)`.

Step 4 renders the whole tuple rather than picking one selector deliberately. Naming the
dispositive selector would mean choosing a grant to be dispositive *against*, which is the
cross-grant read R1 forbids; and the honest statement is that no grant allowed this
*combination*, which is also the tuple the operator must add. The four templates:

```
_DENIED = (
    "{tool} is not permitted on {targets} by access policy {policy}. No grant naming "
    "{tool} allows that combination of targets. Grant them in a policy grant that "
    "already names {tool}, or call {tool} with targets the policy grants."
)
_MISSING = (
    "{tool} is not permitted by access policy {policy}: it declares a {kind} target "
    "through {argument}, which this call did not supply, and a target-constrained "
    "grant cannot bound an omitted target. Supply it and grant that {kind}, or grant "
    "{tool} under targets = \"all-targets\"."
)
_UNBOUNDABLE = (
    "{tool} is not permitted by access policy {policy}: no targets table can "
    "bound every resource it acts on. Grant {tool} under targets = \"all-targets\" "
    "in a grant that names it."
)
_UNREADABLE_VALUE = (
    "{tool} is not permitted by access policy {policy}: the {argument} argument does "
    "not carry a readable {kind} target."
)
```

Closed templates in ADR 0038's sense: only `tool`, `policy` (repr), `kind`, `argument`, and
the caller's own `value` (repr) are substituted, so "carries no secret" is a property of the
text rather than a claim about it.

### `connection_scope.py`

`selected_connection`, `ConnectionScopeError`, `UNRESOLVED`, `_UNREADABLE`, `_DENIED`, and
`_clause` are unchanged. `connection_authorizer` is **deleted** and replaced by two
exports the loop calls per grant:

```
def connection_permitted(connection, grant_connections) -> bool
def connection_denial(tool, policy_name, argument, token, connection) -> ConnectionScopeError
```

`connection_denial` takes the declared selector's *name* rather than the whole
`ToolSecurity`, since the clause is the only thing that needs it. It carries the
`HMC_HOST` clause logic verbatim, including reading
`os.environ` only after the decision and gating it on the decision's own result.

### `dispatch_scope.py` (new)

The whole conjunction, small enough to read at once:

```
def dispatch_authorizer(policy: AccessPolicy) -> Authorize:
    def authorize(name, security, arguments) -> None:
        argument = security.connection_argument
        if argument is None:
            return
        token = arguments[argument]
        connection = selected_connection(token, tool=name)
        extracted = selected_targets(security, arguments)
        connection_matched = False
        # One conjunction per grant, never a union across them (ADR 0036).
        for grant in policy.grants_for(name):
            if not connection_permitted(connection, grant.connections):
                continue
            connection_matched = True
            if targets_permitted(grant.targets, security, extracted):
                return
        if connection_matched:
            raise target_denial(name, policy.name, security, extracted)
        raise connection_denial(name, policy.name, argument, token, connection)
    return authorize
```

`connection_matched` is set inside the loop and read only after it, so the message selection
cannot influence which grant satisfies the call. `selected_connection` may raise
`ConnectionScopeError` for an unreadable configuration, before any grant is examined — the
ADR 0038 behaviour, preserved by ordering.

### `access_policy.py`

`_compile_grant`'s existing block over `model.tools` under a non-string `targets` gains two
rules and loses the `selector.required` filter:

```
for tool in model.tools:
    security = tool_security[tool]
    if not security.exhaustive_targets:
        raise AccessPolicyError(f"{where}: tool {tool!r} ... use targets = \"all-targets\"")
    for selector in security.targets:
        if selector.kind not in model.targets:
            raise AccessPolicyError(f"{where}: tool {tool!r} requires a target constraint ...")
```

The existing rule rejecting a `targets` kind no granted tool declares is unchanged.

### `server_permissions.py`

`ENFORCED_DIMENSIONS = ("tools", "connections", "targets")`,
`DECLARED_ONLY_DIMENSIONS: tuple[str, ...] = ()`. `ToolPermission` gains
`exhaustive_targets: bool`, filled from the index; the `UNKNOWN` fallback carries `False`.
The handler docstring stops saying targets are not yet enforced.

### `server.py`

`_gates` imports `dispatch_authorizer` from `dispatch_scope` instead of
`connection_authorizer` from `connection_scope`. No other change.

### `README.md`

Lines 263-267 currently say the tool and connection dimensions are enforced and that
`targets` "constrain nothing at call time"; all three are enforced after this change. The
`targets = "all-targets"      # or a table, e.g. { lpar = ["db-01"] }` example at line 319
becomes actively misleading — that substitution loads (effect-reached tools are exempt from
R12/R13) and then denies every console read, `hmc_list_systems`, and every unpinned
`hmc_list_lpars`. It is replaced with the two-grant shape, plus the operator rules a table
now implies: every declared selector must be supplied and matched, comparison is exact so a
name does not cover its UUID, and a table never covers a tool it cannot bound.

### `server_provision.py`, `server_profiles.py`

One decorator keyword each: `exhaustive_targets=False`, with a comment naming the identity
that escapes (`ProvisionStorage.vios_uuid` / the HMC-side `file_path`).

## Testing

New module `tests/unit/test_target_scope.py`: extraction over R4's table including `bool`,
`float`, and a list; exact matching including a near-miss (`"db-01 "`, `"DB-01"`, a UUID for
a name); `all-targets` with `ABSENT` and with `UNREADABLE`; `exhaustive_targets=False` under
a table and under the sentinel.

New module `tests/app/test_target_authorization.py`, mirroring
`tests/app/test_connection_authorization.py`: the cross-grant fail-open (R1) as the first
test; a permitted call reaching its handler; a denied target, a denied omitted optional
selector, and a denied selector-less tool, each asserting no `HMCClient` construction (R14);
message selection between the two error types (R15); the sentinel-secret leak test on the
real user path (R16); `dry_run` invariance (R17).

R9, R10, and R11 go in `tests/app/test_tool_security.py`, beside the G12 connection
guardrail they mirror, and reuse its `_module_functions` walker and `_REFUSED` fixture-source
convention. A second module would duplicate that AST infrastructure. Each check carries a
fixture source proving it bites.

Extended: `tests/unit/test_access_policy.py` (R12, R13, R12a); `tests/unit/test_tool_registry.py`
(R7); `tests/app/test_tool_security.py` (R8, R9, R10, R11, and the existing registry
assertions); `tests/app/test_capability_ceiling.py` (R19, and its
`declared_only_dimensions` assertion).

**R12 supersedes ADR 0036 acceptance criterion A7** ("optional selectors need no
coverage", `docs/workflow/specs/2026-08-18-server-access-policy-design.md:413`). Three
currently-green tests construct a grant naming `hmc_power_off_lpar` with a `targets` table
covering only `lpar`, which R12 now rejects at load:
`tests/unit/test_access_policy.py::test_optional_selectors_need_no_coverage` asserts A7
directly and is **inverted**, keeping the same fixture and asserting the new failure;
`test_compiled_policy_is_immutable` and `test_compile_does_not_retain_the_caller_containers`
use the shape incidentally and are re-pointed at a fully covered grant.

Existing dry-run tests in `tests/lpar/test_provision_tool.py`,
`tests/lpar/test_decommission_tool.py`, and `tests/lpar/test_attach_disk.py` are
extended to assert zero mutating requests rather than only a `dry_run=True` result (R18).

`tests/app/test_connection_authorization.py` is updated for R2: it imports
`dispatch_authorizer`, and its assertions on `ConnectionScopeError` are unchanged, which is
the regression proof that ADR 0038's behaviour survives.

## Threat model

The untrusted party is the MCP client and the agent driving it. It chooses every tool
argument, including every target selector, and may call any registered tool any number of
times.

- **Selector confusion.** Exact string matching with no normalization means the boundary
  compares what the caller sent, and each selector is compared against **its own kind's**
  allowlist — a value permitted for `managed_system` is not thereby permitted for `lpar`.
  `bool` is excluded from the `int` arm so `True` cannot render as a resource name. The
  `int` → `str` rendering is *not* offered as a safety argument: ADR 0039 withdraws that
  framing, because the map that would have to be injective is the one from all selectors
  of a kind into one `frozenset[str]`, and it is not — which is exactly why
  `vios_partition_id` is refused as a bounding identity rather than trusted.
- **Omission as widening.** An optional selector left out is the cheapest widening attack —
  it turns "this partition" into "whichever partition has this name" or "every system". R5.2
  denies it under a table.
- **Cross-grant combination.** The fail-open this entry is most exposed to, and R1 is its
  test.
- **Reach through a nested identity.** `hmc_provision_lpar`'s `ProvisionStorage.vios_uuid`
  names a VIOS by global UUID; R5.1 plus R8 deny the tool under any table, and R9 stops the
  next one being written.
- **Probing.** A caller holding one granted tool can recover that tool's target dimension
  one value at a time, and now also learns which dimension blocked. Inherent to having an
  enforcement point; making probing visible is #224's.
- **Not addressed here:** an attacker who can write `access-policy.toml` or `config.toml`
  (ADR 0036 places both at one trust level); the HTTP transport's absent authentication; and
  the CLI and `hmc_mcp.api` paths, which ADR 0029 and ADR 0038 place outside this boundary.

## Open questions

None blocking. Two residuals are filed as a follow-up issue rather than decided here: the
20 `lpar` tools with no `managed_system` selector (and `metric_resource`'s `category`), and
making `hmc_provision_lpar` narrowable by descending into structured parameters.
