# 0039 — Authorize target scope at dispatch, on the declared selectors only

## Status

Accepted (2026-08-19)

## Context

ADR 0035 gave every MCP tool a `ToolSecurity` record carrying `TargetSelector`s — the public
handler arguments from which a target identity is read. ADR 0036 compiled `Grant.targets` into
either the `ALL_TARGETS` sentinel or a `Mapping[TargetKind, frozenset[str]]`, and fixed that a
grant is evaluated conjunctively: a call is permitted only when a *single* grant covers its tool,
its connection, and its targets together. ADR 0037 enforced the tool dimension by not registering
a withheld tool. ADR 0038 enforced the connection dimension inside `tool_registry.authorized`,
wrote the per-grant loop, and left a comment on it saying #223 extends the condition *inside* the
loop rather than beside it.

So today `Grant.targets` is compiled, validated, reported by `hmc_effective_permissions` as
declared-but-not-enforced, and read by nothing. A grant reading
`targets = { lpar = ["scratch-01"] }` places no constraint whatsoever: every one of the 130
registered tools may be called on any resource the granted connection reaches. Epic #218
requirement 5 and issue #223 close that gap.

The surface this record must decide over, measured in this checkout:

- 130 tools; 55 `read`, 46 `mutate`, 28 `destructive`, 1 `arbitrary-command`.
- 19 tools declare **no** selector at all: 17 with `target_kind = "console"` (including
  `hmc_remove_ldap_config`, `destructive`, and `hmc_run_command`), plus the two
  `connection_argument = None` tools the wrapper never wraps.
- 43 tools declare an `lpar` selector. 23 also declare a `managed_system` selector; **20 do not**.
- 10 tools declare at least one `required=False` selector; two of them (`hmc_list_lpars`,
  `hmc_list_vios`) have no other.
- Selector arguments are typed `str`, `str | None`, or `int` — the last only for
  `vios_partition_id`, on three tools.
- Three tools accept `dry_run`: `hmc_provision_lpar`, `hmc_decommission_lpar`,
  `hmc_attach_disk_to_lpar`.

ADR 0036 deferred five things here by name: exact matching, the `vios_uuid`/`vios_partition_id`
split, `metric_resource`'s dependence on a non-selector `category` argument, composites, and
`dry_run`. It also recorded that "optional selectors are never required to be covered — how an
absent optional selector is treated is #223's decision", and that `hmc_provision_lpar`'s nested
identities and the profile backup/restore `file_path` "sit outside every grant".

This record covers the target dimension only. Structured audit events (#224) and fail-closed
startup with the legacy-policy generator (#225) are not decided here, and neither is
`create_mcp`'s no-policy default.

## Decision

### The conjunction gets its own module, so the loop shape is not a comment

ADR 0038 put the grant loop inside `connection_scope.connection_authorizer` and marked it with a
comment: one predicate per grant, never a union across them. With one dimension that rule was
unobservable — a union across grants and a conjunction inside one give the same answer when there
is only one condition. Adding the target dimension is what finally makes the two differ, and
getting it wrong is a fail-open in which one grant's connection combines with another grant's
targets.

So the conjunction stops being a comment and becomes a module:

- `connection_scope.py` answers **one dimension for one grant**. It keeps `selected_connection`,
  `ConnectionScopeError`, and its denial template, and it no longer iterates grants.
- `target_scope.py` answers **the other dimension for one grant**: selector extraction,
  `TargetScopeError`, and its denial template.
- `dispatch_scope.py` holds the **only** loop over `policy.grants_for(name)` and is the callable
  `server._gates` hands to every registration site. It is short enough to read whole, and neither
  dimension module can iterate grants because neither is given the policy.

`connection_scope.connection_authorizer` is **removed**, not kept beside its replacement. There is
one authorizer, `dispatch_scope.dispatch_authorizer`, and no compatibility re-export: two
mechanisms for one job is the defect surface this package has been closing for four entries.

The `Authorize` signature — `(name, security, bound_arguments)` — is unchanged. ADR 0038 chose it
over passing only the connection token precisely because "#223 reads target selectors from the same
mapping", and it does.

### The target dimension binds every wrapped tool

Issue #223's outcome names mutations. This record enforces the dimension on every registered tool
whose `ToolSecurity` declares a connection argument — the same population ADR 0038 chose, for the
same three reasons. `Grant.targets` is a property of the grant, not of an effect class, so scoping
it to mutations would make one policy key mean two different things depending on which tool it
reached. A read against a withheld target is a disclosure: `hmc_get_lpar` on a partition the policy
excluded returns its configuration. And an effect filter is a condition that would have to be
written, tested, and kept in step with the effect classes, where enforcing everything is no
condition at all.

The consequence is sharp and is stated as one: `hmc_list_lpars(system_name_or_uuid=None)` — "list
every partition on every system" — is denied by a grant carrying `managed_system = ["S1"]`, because
that grant did not say the caller may enumerate every system. That is the rule working, not a
usability defect, and `targets = "all-targets"` remains available for a grant that means it.

### Extraction is total, and reads only the declared selectors

For each `TargetSelector` in `security.targets`, `dispatch_scope` reads
`arguments[selector.argument]` — indexed rather than `.get`, because `authorized` has already
applied the handler's defaults and `validate_security` guarantees the parameter exists, so an
absent key is a malformed call and treating it as an omitted argument would silently soften it.
Each value normalizes to exactly one of three outcomes:

1. **A string.** `str` is itself, including `""` — an empty selector is a well-formed string that
   denies under any table by construction, because `access_policy._check_entries` already rejects an
   empty allowlist entry, so no table can contain it. It is deliberately not `ABSENT`; note the
   asymmetry with `selected_connection`, where `""` *is* the default connection because
   `load_profile` treats it as an omitted argument.

   `int` that is not `bool` renders through `str()`. That arm exists for `vios_partition_id`, the
   surface's only non-`str` selector, and it is load-bearing rather than convenient: without it
   those three tools' calls would be `UNREADABLE`, which denies even under `all-targets`, and
   #225's legacy-equivalent policy would stop covering them. It is emphatically *not* justified by
   `str` being injective over `int` — an earlier draft said that, and it is the wrong domain. The
   function whose injectivity would matter is the one from *all* selectors of a kind into one
   `frozenset[str]`, and that one is not injective: `"2"` in a `vios` array is a partition ID and a
   VIOS name at the same time. That collision is why `vios_partition_id` is refused as a bounding
   identity outright, below.

   `bool` is excluded explicitly because it is an `int` subclass and `str(True)` would render
   `"True"` into a comparison against resource names.
2. **`ABSENT`**, for `None` — an optional selector the caller omitted.
3. **`UNREADABLE`**, for every other type, uninspected and uncoerced. ADR 0038's rule 0 for the
   connection token, applied to targets: a boundary with no coercion rule is one fewer thing to get
   wrong.

Nothing else is read. Not a nested field of a structured argument, not an environment variable, not
`config.toml`, not the HMC. Extraction performs no I/O at all, so unlike the connection dimension
it inherits no two-read race.

### Matching is exact, and every declared selector must match

A grant whose `targets` is a table covers a call only when **all** of these hold:

- the tool's selectors can bound it at all (`exhaustive_targets`, below);
- every extracted selector is a string — an `ABSENT` or `UNREADABLE` selector matches nothing;
- for every extracted selector, its `kind` is a key of the table **and** its value is a member of
  that key's frozen set.

Comparison is exact string equality. No case folding, no whitespace tolerance (`access_policy`
already rejects a padded entry at load, so a padded allowlist entry cannot silently go dead), no
globs — ADR 0036 rejected a wildcard language and epic #218 makes a general expression engine a
non-goal — and no name↔UUID resolution. The epic left canonicalization open with a condition
attached: it "must not create an authorization-time network call or a selector-confusion bypass".
Resolving a name to a UUID requires asking the HMC, which is an outbound call inside the decision
that is supposed to precede all outbound calls. Exact matching is retained, and the operator-facing
consequence is that a policy naming a partition by name does not cover a call naming it by UUID,
and the reverse.

**Every** declared selector must match, not only the `required=True` ones. An optional selector left
at its default is `ABSENT` and therefore denies under a table. This is the decision ADR 0036
explicitly deferred here, and it is the fail-closed one: an omitted `system_name_or_uuid` on
`hmc_power_off_lpar` means "find a partition with this name on whichever system has one", and an
omitted `system_name_or_uuid` on `hmc_list_lpars` means "every system". Neither is a target a
`managed_system` allowlist bounded. There is no third option available — ADR 0036 rejected a
per-kind widening form (`targets = { job = "all" }`), so the format offers nothing between "this
exact set" and "all targets" — and the remedy is legible: supply the argument, or grant
`all-targets`.

Because an uncovered optional selector now makes a grant dead rather than merely narrow, ADR 0036's
load-time coverage rule extends from *required* selector kinds to **every** declared selector kind.
It stays bound to tools a grant names **explicitly**, exactly as ADR 0036 bound it, so an index
change still cannot make an unedited file stop loading.

### `all-targets` is the only widening form, and it widens everything but unreadability

A grant whose `targets` is the `ALL_TARGETS` sentinel places no target constraint, so it covers a
call whatever the selectors say — with one exception. An `UNREADABLE` selector denies under
`all-targets` too. The sentinel says "any target of the kinds this tool declares"; it does not say
"any argument of any type", and a value the boundary cannot read is a malformed call, which ADR 0038
already established denies by ordering. This costs nothing: the guardrail below proves `UNREADABLE`
is unreachable through MCP, where the generated schema types every selector `string` or `integer`,
so the arm binds only a direct caller of the wrapped object. `ABSENT` is *not* treated this way —
an omitted optional argument is a well-formed call, and refusing it under `all-targets` would make
the sentinel unable to express the legacy exposure #225 must generate.

### Carry-forward: an `lpar` name is unique within a system, not across the fleet

`lpar_name_or_uuid` names a partition inside a managed system, and partition names collide across
systems. So a grant reading `targets = { lpar = ["db-01"] }` on `hmc_power_off_lpar` describes an
intent — "may power off db-01" — that the `lpar` allowlist alone cannot deliver. Both readings were
live.

**Reading A — kind-local matching.** Each selector is matched against its own kind's allowlist,
independently of the others. `lpar = ["db-01"]` means "a partition named db-01", wherever it lives.
This is what ADR 0036's kind-keyed table literally says, and it is one table lookup per selector.

**Reading B — mandatory disambiguation.** Refuse to authorize an `lpar` selector unless the call
also pins a `managed_system` selector that the grant allows. It answers the collision directly.

**Reading A is adopted, and the "every declared selector must match" rule above delivers Reading B's
effect wherever Reading B is implementable.** For the 23 lpar tools that declare a `managed_system`
selector, a table-constrained call must supply *and* match both — including `hmc_power_off_lpar`,
`destructive`, the one tool whose `managed_system` selector is optional and therefore the one case
where the collision was reachable with a fully covered grant.

Reading B is rejected as a standalone rule because it is unimplementable on the other 20 lpar tools:
`hmc_delete_adapter`, `hmc_dlpar_mem`, `hmc_modify_lpar` and 17 siblings accept no
`system_name_or_uuid` at all, so there is no argument to require. A rule that fires on 23 of 43
tools and silently does not fire on the remaining 20 is worse than a rule with a stated residual,
because the operator cannot tell which they wrote. Adding `system_name_or_uuid` to 20 handler
signatures would make Reading B total, and is rejected as a public-contract change ADR 0035 already
decided against for those tools, well outside this issue.

**Residual, owned and stated:** on the 20 lpar tools with no `managed_system` selector, a grant
naming `lpar = ["db-01"]` authorizes db-01 on every system the granted connection reaches. The
operator-side remedy needs no code and is exact: `lpar_name_or_uuid` accepts a **UUID**, which is
unique across the fleet, so a policy that must disambiguate on those tools lists UUIDs rather than
names — at the cost of a policy file that has to be rewritten when a partition is recreated. This is
filed as a follow-up issue rather than absorbed.

`metric_resource` has the same shape for a different reason: `resource_name_or_uuid` is
disambiguated by a `category` argument that is not a selector and is not in
`REQUIRED_TARGET_ARGUMENTS`. Recorded here, closed by the same follow-up, not silently inherited.

The `managed_system` **role** collision ADR 0036 flagged does *not* resolve safely, and an earlier
draft of this record claimed it did. The claim was that one allowlist spanning both roles makes
migration require both endpoints, which would be stricter than role-keyed matching. It is wrong,
and checking it is what found the residual: **no migration tool declares `system_name_or_uuid` at
all.** `hmc_migrate_lpar`, `hmc_migrate_validate_lpar`, and `hmc_remote_restart_lpar` declare
`(lpar, lpar_name_or_uuid)` and `(managed_system, target_system_name_or_uuid)` — the *destination*
only. So a grant naming `{ lpar = ["db-01"], managed_system = ["S2"] }` authorizes evacuating a
partition called `db-01` off **any** source system the connection reaches, into S2; and because
partition names collide across systems, the source is unconstrained twice over.
`hmc_migrate_abort_lpar` and `hmc_migrate_recover_lpar` declare only `lpar_name_or_uuid` and sit in
the 20-tool residual above. The remedy is the same UUID one, and this joins the same follow-up
issue. Recorded as a residual rather than closed here, because closing it means adding a source
`system_name_or_uuid` to three LPM signatures — the public-contract change rejected two paragraphs
above.

### Carry-forward: what a target-constrained grant means for a selector-less tool

`hmc_remove_ldap_config` is `@tool(effect="destructive", operation="ldap.remove",
target_kind="console")` over `(resource, profile)`. Neither argument is in
`REQUIRED_TARGET_ARGUMENTS`, so `build_targets` yields `()`, and `validate_security` deliberately
exempts `target_kind == "console"` from needing a matching selector. Once that tool is inside a
grant's ceiling, a `targets` table has nothing to bind on. Three readings:

**(i) Inert.** The table binds nothing for such a tool; only `connections` bounds it. This is the
fail-open reading and it is what happens if nobody decides. A grant written as `effects =
["destructive"], connections = ["lab"], targets = { lpar = ["scratch-01"] }` would read, to any
operator, as "may destroy scratch-01 on lab" — and would in fact permit deleting the LDAP
configuration of the entire lab console. **Rejected explicitly.**

**(ii) Widening must be written down.** A grant carrying a `targets` **table** never covers a tool
whose declared selectors cannot bound it. Granting such a tool requires `targets = "all-targets"`,
in its own grant. **Adopted.**

**(iii) Give the tools selectors.** Rejected as unimplementable: the console's identity *is* the
connection, so a `console` selector would restate `connections`, and `hmc_run_command`'s target is
free-form command text.

Reading (ii) has a load-time half, and it is the same argument ADR 0036 used to invent its coverage
rule. A grant that **explicitly names** such a tool in `tools` *and* carries a `targets` table can
never authorize it, so the grant is dead, and a dead entry in a security artifact is an authoring
error worth failing the load over. It is rejected at load with a message naming the tool and the
remedy. Tools reached through `effects` stay exempt at load — ADR 0036's reason holds unchanged: a
release adding a tool to a granted effect class must not make an unedited file stop loading, and
under #225's fail-closed startup that is a server that does not start after an upgrade — and are
denied at call time by (ii) instead.

**Both load-time rules exempt a tool declaring no connection argument, and the exemption is not
cosmetic.** `hmc_effective_permissions` and `hmc_list_configured_hosts` have
`connection_argument = None`, so `tool_registry.authorized` returns their handlers unwrapped and no
authorizer ever runs on them. A grant naming either beside a `targets` table is therefore *not*
dead — it works exactly as it did before this entry, bounded by the ceiling alone, which is the only
dimension that can reach a tool the wrapper never wraps. Failing the load over it would refuse to
start a server over a grant that functions, and it would do so for the introspection tool an
operator is most likely to name explicitly — the very tool `server._startup_warnings` already nags
about when a policy withholds it. ADR 0038 recorded the same structural fact for the connection
dimension; this is its target-dimension twin.

The cost is real and is the point: `effects = ["read"], targets = { managed_system = ["S1"] }` no
longer reaches `hmc_list_systems`, `hmc_console_info`, or the other 12 console reads. Those are
console-wide operations and that grant said nothing about a console. The operator writes a second
grant. ADR 0036 predicted this shape when it noted that `all-targets` "is also boilerplate on any
grant of the … selector-less tools, so its presence is not by itself an audit signal".

### Composites: `exhaustive_targets`, declared and guarded

Requirement 5 asks that "composite tools are authorized for every mutation they may perform". Some
cannot be. `hmc_provision_lpar` declares exactly one selector, `managed_system`, and then performs
up to six mutations, one of which creates a `VirtualSCSIMapping` on a VIOS whose UUID arrives as
`ProvisionStorage.vios_uuid` — one level below the signature, therefore invisible to
`build_targets`, and POSTed to the global `/rest/api/uom/VirtualIOServer/{uuid}` URI, so nothing
constrains that VIOS to the declared managed system. A grant reading `managed_system = ["S1"]`
would authorize a mapping onto a VIOS on S2.

`ToolSecurity` gains one field, `exhaustive_targets: bool`, meaning: *the declared selectors name
every resource this tool acts on that the caller chose, so a `targets` table can bound the call*.
When it is false, a table never covers the tool and only `all-targets` does — the same rule as
reading (ii) above, which makes the selector-less case a degenerate instance rather than a separate
mechanism. The decorator takes `exhaustive_targets: bool = True` and `tool()` computes the stored
value as `declared and bool(targets)`, so all 19 selector-less tools get `False` with no per-tool
churn and only the genuine composites carry an explicit declaration.

Eight tools declare `exhaustive_targets=False`:

- **`hmc_provision_lpar`** — the nested `vios_uuid` above, plus `ProvisionNetwork.vios_partition_id`.
- **`hmc_backup_lpar_profiles`** and **`hmc_restore_lpar_profiles`** — both act on an arbitrary
  HMC-side `file_path` (the backup with `force=True` overwriting whatever is there), a console
  filesystem object no `TargetKind` names. ADR 0036 already placed `file_path` outside every grant;
  this is that placement made enforceable rather than documented.
- **`hmc_add_vfc_adapter`**, **`hmc_add_vscsi_adapter`**, and **`hmc_attach_disk_to_lpar`** — each
  declares a `vios`-kind selector whose argument is `vios_partition_id`, a slot number *within one
  managed system* that is reused on every system in a fleet. A `vios = ["2"]` entry therefore names
  a different VIOS on each system the connection reaches, and unlike a partition name there is no
  UUID form to write instead: two of the three accept no VIOS UUID at all. `hmc_attach_disk_to_lpar`
  accepts both `vios_uuid` and `vios_partition_id` as `vios` selectors and checks nowhere that they
  agree, so the partition ID is a second, unverified identity beside the one that *can* be bounded.
  These are the tools an earlier draft of this record silently authorized under a table; the
  guardrail below is what found them.
- **`hmc_get_job`** and **`hmc_wait_for_job`** — both declare a required `job` selector on
  `job_uuid` and then accept `job_href`, whose *path* replaces it outright: `client.get_job`
  fetches `urlparse(job_href).path` and never reads `job_uuid`. A table would authorize one
  job identity while the server read another. These two were found by the threat scan rather
  than by the guardrail, for the reason the next paragraph gives, and they are the concrete
  instance of its stated limit.

Two categories are deliberately **not** made non-exhaustive, and both are decisions rather than
omissions:

- **Sub-resources reached through a declared selector.** `hmc_attach_disk_to_lpar`'s `vg_uuid`,
  `hmc_map_storage_to_lpar`'s `storage_name` and `target_device`, and their siblings name objects
  the HMC addresses *inside* a declared VIOS — `POST /VirtualIOServer/{vios_uuid}/VolumeGroup/{vg}`
  — so they cannot name a resource the declared selector does not contain.

  **That containment was not true when this record was first written, and saying so is the
  point of this paragraph.** Every such path is built by interpolating the argument into an
  f-string, and `httpx` resolves RFC 3986 dot-segments when merging a path onto `base_url`.
  Verified against httpx 0.28.1 in this checkout: a `vg_uuid` of `../../../LogicalPartition/X`
  turns `DELETE /VirtualIOServer/{vios}/VolumeGroup/{vg}` into
  `DELETE /rest/api/uom/LogicalPartition/X`. The authorizer sees an authorized
  `vios_name_or_uuid`, has no constraint for the undeclared `vg_uuid`, and permits — so a grant
  reading `targets = { lpar = ["dev-lpar"] }` authorized a `destructive` call against an
  arbitrary partition. Every "a denied call makes no outbound attempt" test stayed green,
  because the call was never denied.

  The containment claim is what justifies classifying these ~13 tools `exhaustive_targets=True`,
  so it is load-bearing rather than decorative, and this record cannot assert enforcement while
  it is false. `client._request` — the single waist every REST call crosses, from MCP, the CLI,
  and the `api` facade alike — now refuses any path carrying a `.` or `..` segment, before the
  request is built. That is one guard rather than thirteen call-site checks, it covers the two
  entry points no access policy bounds, and it also closes the `job_href` variant below.
  Rejecting rather than stripping is deliberate: a sanitized path would leave the caller acting
  on a resource it did not name, which is the same defect with a tidier URL.

  What that guard does **not** do is require these arguments to be UUID-shaped, which
  `common.is_uuid` already does for the `*_name_or_uuid` resolvers and issue #262 proposes here.
  Measured rather than assumed: with dot-segments refused **in both the raw and the
  percent-decoded form**, the remaining shapes an attacker controls — an absolute path, a
  protocol-relative `//host`, a deeper path segment, a query or matrix parameter, a backslash
  — were each built against httpx 0.28.1 and **none changes the host or escapes the declared
  VIOS prefix**; they address something at or below the resource the selector names, which is
  what containment claims.

  The decoded arm is there because the first version of this guard did not have it. It
  checked only literal dot-segments, on the reasoning that httpx leaves `%2e%2e` untouched
  "so it addresses nothing" — half verified, half a claim about whether the HMC's own server
  decodes before routing, which nothing here can establish and many HTTP servers do. That is
  the same substitution this record has now made three times: an assumption about a system
  we cannot observe, written in the grammar of a fact about code we can. It is worth more as
  a recorded pattern than as three separate corrections.
  So UUID-shape validation is strictly stronger than this record needs and is left to #262,
  where it can be weighed against a compatibility risk this checkout cannot settle: whether
  every HMC build's volume-group, mapping, and adapter identifiers are canonical 8-4-4-4-12
  UUIDs. Refusing a legitimate non-canonical identifier would be a regression traded for
  reach that dot-segment refusal has already removed.
- **Remote endpoints a call reads from.** `hmc_update_firmware`, `hmc_update_console_software`, and
  `hmc_vios_update` take a `repository`; `hmc_install_lpar_os` and `hmc_install_vios` take NIM
  server addresses. These are not HMC resources, so no `TargetKind` names one and no allowlist can
  hold one. Each still mutates exactly the resource its selectors declare — a system, a console, a
  VIOS, a partition — while loading the payload from an address the caller chose. Constraining
  *where a granted target is loaded from* is egress control, a different control this policy does
  not offer, and pretending the target dimension covers it would be worse than saying it does not.
  The five tools are enumerated in a test so a sixth cannot join them silently.

Five composites were examined and are **exhaustive**, which is why the flag is a declaration and not
a category: `hmc_decommission_lpar` deletes adapters whose UUIDs it derives from the declared
partition's own inventory; `hmc_deploy_partition_template` stamps a partition it creates on the
declared system; `hmc_system_summary` and `hmc_lpar_summary` read children of their declared target.
The line those share, and the six above do not, is that every resource acted on is either the value
of a declared selector or derived by the server through the HMC's own containment from one — so it
cannot name a resource the declared selector does not contain. A newly minted identity is on the
permitted side of that line by necessity: no allowlist can name a partition that did not exist when
the policy was written, and `managed_system = ["S1"]` conferring the ability to create partitions on
S1 is what the operator asked for.

**A declaration nobody checks is a comment**, so the flag is paired with a static guardrail over the
parsed `server_*` sources, in ADR 0038's tradition. For every tool declaring
`exhaustive_targets=True`, no handler parameter — and no field of a dataclass or pydantic-model
parameter, one level down — may carry a name in `REQUIRED_TARGET_ARGUMENTS` that is not already a
declared selector, and none may name an identity the format cannot bound (`file_path`, `cmd`). The
check runs at suite time, costs nothing per call, and fails the author rather than an operator. It
is what catches the next `hmc_provision_lpar`.

**What that check does and does not prove.** This is the section a reader should trust
least, and it has already been wrong twice. An earlier draft of this record ran it over all 130
tools, observed that it flagged exactly the tools already declared, and reported "zero false
positives" as evidence the declarations were mechanical rather than judgements. That was circular:
`UNBOUNDED_ARGUMENTS` had been written *from* the known cases, so it could not have flagged anything
else, and the direction that matters for a fail-open is false *negatives* — which the observation
says nothing about.

Looking for those instead is what produced the `vios_partition_id` entry above and the
payload-source decision beside it — and a subsequent threat scan found a third the name
list still did not hold, `job_href`, live in shipping code under any `effects = ["read"]`
grant. The guardrail was green throughout, exactly as this paragraph predicts a
name-matching check would be. The honest statement is therefore the weaker one: the check
turns a per-tool judgement into a per-*argument-name* judgement, which is a much smaller thing to
get wrong and a much easier thing to review, and it makes any future tool accepting one of those
names fail the suite rather than ship. It does not prove the name list is complete. Extending that
list is how a newly recognised unbounded identity is closed, and the list is deliberately kept in
`tool_registry.py` beside `REQUIRED_TARGET_ARGUMENTS`, since the two are one piece of knowledge —
which public argument names carry which identity — and would drift if they lived apart.

A second guardrail closes the other half, the direct analogue of ADR 0038's "a declared connection
argument must actually route the connection": every declared selector argument must be *referenced*
in its handler's body. A handler that accepts `lpar_name_or_uuid` and never reads it would be
authorized against a target it does not act on — the target-dimension shape of
`hmc_set_lpar_boot_order` ignoring `profile`. Verified in this checkout: all 130 handlers reference
every selector they declare, so the guardrail lands green and exists to keep it that way. Its limit
is stated rather than implied: it proves the value is read, not that it reaches the right sink.

### `dry_run` is not an authorization input

The authorization is byte-identical whether `dry_run` is `True` or `False`. `dispatch_scope` never
reads the flag, no effect class is downgraded by it, and no grant widens because of it.

The alternative — treat `dry_run=True` as effect `read` so a read grant covers it — is forbidden by
the epic outright: goal 7 is that "ordinary MCP tool arguments must never select or widen the
server's access policy". It also rests the boundary on a claim the server could verify only by
executing the handler it is deciding whether to run.

Requirement 5's other half, that dry-run paths "provably perform no mutation are classified and
tested explicitly rather than inferred from client claims", is therefore delivered as **tests**, not
as a policy relaxation. Each of the three `dry_run` tools gets a test proving its `dry_run=True`
path issues zero mutating HMC requests. Two facts found while classifying them are recorded here so
#224 does not rediscover them: `hmc_decommission_lpar`'s dry-run path still issues an SSH
`lssyscfg` **read** for its ownership snapshot, and with `ownership_override=True` it writes a local
warning-level audit line. Neither is an HMC mutation; both are observable behaviour on a path a
caller may reasonably believe is inert.

### The `build_config` bypass has a target-dimension analogue, in a different place

ADR 0038's whole shape was decided by one property of `common.build_config`: when `HMC_HOST` is set
or an explicit `host` override is passed, the profile-resolution block is skipped and the `profile`
argument is silently discarded. The target dimension was checked for the same thing rather than
assumed clear, and the answer is in two parts.

**`build_config` itself has no target-dimension bypass.** No environment variable supplies a default
target. Verified by scanning every `os.environ` and `getenv` read under `src/hmc_mcp/` in this checkout
on branch `feat/target-scope-dispatch-223`: each one names
`HMC_HOST`, `HMC_PROFILE`, `HMC_PASSWORD`, `APPDATA`, or `XDG_CONFIG_HOME`. Nothing names a
partition, a system, or a VIOS. There is no `HMC_SYSTEM` to discard a `system_name_or_uuid` in
favour of.

**The analogue exists one layer out, and is what `exhaustive_targets` closes.** The bypass is not
"the declared selector is discarded" but "the tool acts on an identity the declared selector never
saw" — `hmc_provision_lpar`'s nested `vios_uuid`, the profile pair's `file_path`, and the adapter
trio's `vios_partition_id`. Structurally
it is the same failure as `HMC_HOST`: the control compares the thing the caller named while the
runtime acts on something else. Both are closed above, and the guardrail is what keeps the next one
from being written.

### Denials name the dimension that blocked, and nothing else

There are two denial templates, `ConnectionScopeError`'s (ADR 0038's, unchanged) and
`TargetScopeError`'s. Which one fires is chosen by a pass over the grants that runs **after** the
decision and cannot change it: if some grant matched the connection but not the targets, the target
template; otherwise the connection template. The decision itself remains one conjunction per grant
with no cross-grant combination — the message selection is a separate, explicitly-marked read of
the same data, and it is the only place in this record where grants are considered together.

That concedes exactly one bit — *which dimension blocked* — and it is a bit epic requirement 8 asks
for: an authorization failure must name "the non-secret constraint that blocked it". It is strictly
less than ADR 0038 already rejected enumerating; the caller learns that the policy has a target
constraint on this tool, not what it contains.

`TargetScopeError` substitutes a closed set: the tool name, the policy name, the failing selector's
**kind**, and — for a selector the caller supplied — the caller's own value under `repr()`. Rendering
the caller's value passes ADR 0038's own test for the connection token: the caller already holds it,
so echoing it discloses nothing, and `repr()` neutralizes any control character in it. For an
`ABSENT` selector the message instead names the **argument** to supply, which is compiled-in
`ToolSecurity` metadata rather than anything read at runtime. It never renders the allowlist, a
host, a port, a user, a credential, a resolved endpoint, a filesystem path, or a chained exception's
text.

### `hmc_effective_permissions` reports targets as enforced

`ENFORCED_DIMENSIONS` becomes `("tools", "connections", "targets")` and
`DECLARED_ONLY_DIMENSIONS` becomes `()`, completing the sequence ADR 0037 started. `ToolPermission`
gains `exhaustive_targets`, so an operator can see which of the tools their policy exposes are the
ones a `targets` table can never narrow — the fact reading (ii) makes load-bearing, and the one an
operator would otherwise discover as an unexplained denial.

## Consequences

- **A `targets` table is now a much narrower statement than it reads.** Before this record it
  constrained nothing; after it, it constrains every declared selector of every granted tool, and
  denies any tool it structurally cannot bound. An operator upgrading with a policy authored
  against #221's or #222's semantics can start seeing denials on calls that worked yesterday. That
  is the point of the entry, and the denial names the tool, the policy, and the selector kind, so
  the diagnosis is one message long — but it is a behaviour change and not only a new failure mode.
- **Some policies that loaded yesterday now fail to compile, which is a different and
  louder failure than a denial.** The bullet above describes call-time denials; R12 and R13
  also refuse a *load*. A grant naming `hmc_power_off_lpar` beside
  `targets = { lpar = ["db-01"] }` was valid under ADR 0036's criterion A7 and is now
  rejected, as is any table grant explicitly naming a tool no table can bound. Under
  `--access-policy` that is a non-zero exit with no server started, and it is the failure an
  operator meets first — before any denial, at the moment they upgrade. The message names
  the tool, the kind, and the remedy, and both rules stay bound to explicitly named tools so
  an index change alone still cannot cause it.
- **The 17 selector-less console tools, and eight more, require their own grant.** Any policy
  narrowing targets must now be written as at least two grants: one with a table for the tools it
  can bound, one with `all-targets` for the ones it cannot. #225's legacy-equivalent generator
  emits `all-targets` throughout and is unaffected.
- **A table-only policy advertises tools it will always deny.** ADR 0037's ceiling is per-tool and
  structurally cannot see targets: `permits_tool` tests membership of the union of the grants'
  tool sets, and a tool may sit in a table grant and an `all-targets` grant at once, so there is no
  well-defined "this tool is unreachable" question for the registration filter to ask. A policy
  whose only grant carries a table therefore *registers* `hmc_remove_ldap_config` and the other 24
  non-exhaustive tools, advertises them in `tools/list`, and denies every call to them. The agent
  discovers that by calling one. `exhaustive_targets` in the effective-permissions report is the
  only signal an operator gets, which is why R19 adds it. Moving the ceiling to drop non-exhaustive
  tools from table-only grants is a #221 question and is explicitly not settled here — it would
  make registration depend on the target dimension, which is a coupling ADR 0037 does not have.
- **This entry supersedes ADR 0036's acceptance criterion A7**, "optional selectors need no
  coverage", which was written as a placeholder for the decision ADR 0036 deferred to this record.
  Now that an uncovered optional selector makes a grant *dead* rather than merely narrow, the same
  argument ADR 0036 used to invent the coverage rule applies to it, and the test asserting A7 is
  inverted rather than deleted.
- **An omitted optional selector denies, which retires two tools from table-constrained grants.**
  `hmc_list_lpars` and `hmc_list_vios` have no other selector, so under a table they are callable
  only when the caller pins the system. That is correct — the unpinned call enumerates every system
  — and it is a usability edge an operator will meet early.
- **Exact matching means a policy is written in one addressing form and must be called in it.** A
  grant naming `lpar = ["db-01"]` does not cover a call passing that partition's UUID. Retained
  deliberately: the epic's condition on canonicalization is that it add no authorization-time
  network call, and name→UUID resolution on this HMC API is a network call.
- **The residual on 20 lpar tools is a real fleet-wide grant.** Stated in full above, with the
  UUID remedy, and filed as a follow-up rather than closed here.
- **`exhaustive_targets` is a human judgement backed by a mechanical check, not a derived fact.**
  The guardrail catches a nested or unbounded *identity argument*; it cannot catch a handler that
  reaches a new resource kind through a helper it imports. The population that could get this wrong
  is the author of the next composite, and the check fires on the shape all three known cases have.
- **Extraction performs no I/O**, so the target dimension adds no per-call file read and inherits
  none of ADR 0038's two-read race. The per-call cost is one dict lookup and one set membership
  test per declared selector, over a maximum of three selectors on any live tool.
- **`hmc_run_command` is grantable only under `all-targets`.** It is selector-less, so
  `exhaustive_targets` is false. Combined with requirement 6 — it must be named in `tools`, never
  reached by effect class — the arbitrary-command escape hatch now requires an operator to write
  its name *and* the widest target form, in one grant, beside the CLI switch. Three explicit acts.
- **`TargetScopeError` is a new public exception on the MCP error path**, surfaced to the client as
  a tool error, and absent from `api.__all__` for the reason ADR 0029 and ADR 0038 place the whole
  server-policy boundary outside the supported reusable Python API.
- **`connection_scope.connection_authorizer` is gone**, one day after it shipped. Any caller outside
  this package that imported it breaks; ADR 0029 places this module outside the supported API, and
  #222 shipped yesterday, so the population is this repository.
- **The dry-run classification is a snapshot.** The three tests pin today's behaviour; nothing
  prevents a fourth `dry_run` tool from being added without one. The `dry_run` argument name is not
  metadata and this record deliberately does not make it any — doing so would be the first step
  toward reading it in the authorizer, which the epic forbids.
- **Enforcement holds for the request *path*; the request *body* is a second boundary this
  record does not close.** `documents.py` performs no XML escaping — there is no `escape` or
  `quoteattr` anywhere in `src/hmc_mcp/` — and every builder is an f-string. Two consequences,
  both verified in this checkout rather than reasoned about. `jobs.py` belongs beside
  `documents.py` here: it builds its XML from module-level `.format()` templates rather than
  f-strings, so a scan for f-string interpolation misses it entirely, and
  `build_job_request` raises the same parse error on a `RepositorySource` value containing
  `&` — reached from `hmc_update_console_software`, `hmc_vios_update`, and
  `hmc_update_firmware`.

  The first consequence is a plain functional defect needing no attacker:
  `build_ldap_config_document` raises a parse error on
  `search_filter="(&(objectClass=person)(uid=*))"`, which is ordinary LDAP conjunction
  syntax, and `build_hmc_user_document` does the same for a password or a description
  containing `&` or `<`.

  The second crosses *this* dimension, and the mapping builders are its sharpest case
  because they take `lpar_link` and `vios_lpar_link` as free strings — so the body carries
  the whole target identity rather than merely duplicating it.
  `build_vscsi_mapping_document` emits `target_device`
  immediately before the `AssociatedLogicalPartition` href built from the authorized `lpar`
  selector, so a `target_device` value can close its element and emit a second
  `AssociatedLogicalPartition` naming a different partition — the resulting document is
  well-formed and carries both hrefs, the injected one first. `build_hmc_user_document` has
  the same shape via `description`, yielding two `<UserID>` elements. **Explicitly unverified:**
  which element a real HMC's unmarshaller honours. The injection primitive is confirmed in
  code; the outcome on an HMC is not, and no live HMC was available.

  So the honest statement of this record's guarantee is: a call reaches only the resources its
  declared selectors name **in the request URI**. An unescaped body field can still name
  another. Closing that is output encoding across every builder plus a metacharacter
  round-trip test — a different control from target scope, in modules this record does not
  touch, and no `ToolSecurity` change fixes it (marking `hmc_create_user` non-exhaustive would
  force it under `all-targets` and leave both the parse errors and the primitive intact). It is
  owned separately. One datum worth keeping: of the string-taking builders, exactly one is
  safe, and it is safe because it validates against a `Literal` — the #143 remedy. That
  approach demonstrably works and demonstrably does not generalize, since `search_filter`,
  `description`, `password`, `media_name`, `storage_name`, and `target_device` have no
  vocabulary to constrain them to. Note also that issue #143 closed this class as a single
  instance — it
  constrained `LparResources.sharing_mode` to a `Literal` and called it "the one free-string
  parameter that reaches generated XML unvalidated" — and the evidence above falsifies that
  closing claim.

- **Two refusals now live in `client.py`, outside this record's dispatch boundary, and that
  placement is deliberate.** The dot-segment guard and the `job_href` class check sit in the
  client because the CLI and the `api` facade reach the same paths and ADR 0029 places both
  outside every access policy — a guard at the authorization boundary would protect one entry
  point of three. It is the argument ADR 0038 used to *reject* authorizing inside
  `build_config`, applied to the opposite conclusion, and the difference is what each check
  is: an access decision needs the tool and its grant and belongs at dispatch; a malformed
  request needs neither and belongs where the request is built. Consequence: the CLI and `api`
  now refuse a dot-segment path they previously sent.
- No new runtime dependency. `inspect`, `ast`, `dataclasses`, and `urllib.parse` are stdlib and
  already imported across the package and its suite.

## Considered & rejected

- **Extend `build_targets` to descend into structured parameters**, so `hmc_provision_lpar`'s
  `ProvisionStorage.vios_uuid` and `ProvisionNetwork.vios_partition_id` become real selectors and
  the tool becomes narrowable. This fixes the cause where `exhaustive_targets` refuses the case, and
  it is the better end state. Rejected here as more machinery than this entry needs and more than
  the issue asks for: #223's own words are that "unsupported composite behaviour … fail[s] closed",
  which authorizes refusing the case rather than modelling it. It would also mean the authorization
  boundary reaching into caller-supplied objects to read attributes — a second extraction rule with
  its own failure modes, added in the same entry that first makes extraction load-bearing. Residual:
  `hmc_provision_lpar`, a `mutate` tool an operator would plausibly want scoped to one system,
  cannot be scoped at all and must be granted under `all-targets`. Filed as a follow-up issue.
- **Treat `dry_run=True` as effect `read`.** The most requested-looking reading of requirement 5.
  Rejected in the Decision: epic goal 7 forbids a tool argument selecting the policy the call is
  matched against, and the flag is a client claim the server can only verify by running the handler.
- **Let a `targets` table be inert for a tool it cannot bound** (reading (i)). The silent default,
  named and rejected in the Decision rather than allowed to happen. Its residual, had it been taken,
  is a grant that reads as narrow and destroys a console.
- **Require a `managed_system` selector alongside every `lpar` selector** (reading B). Rejected in
  the Decision: unimplementable on 20 of 43 lpar tools, and making it implementable means changing
  20 public signatures.
- **Key the `targets` table by argument name rather than by target kind**, which would separate
  `system_name_or_uuid` from `target_system_name_or_uuid` and give `metric_resource` its `category`.
  It fixes the role collision exactly. Rejected because requirement 3 asks for kinds, because it
  ties an operator-visible policy file to Python parameter names, and because ADR 0036 already
  compiled and shipped the kind-keyed form — changing it now re-opens a file format operators have
  begun writing. Residual: the role collision stands, in its fail-closed direction.
- **Resolve names to UUIDs before comparing**, so a policy and a call may use different addressing
  forms. Rejected on the epic's own condition: resolution is an outbound HMC call, placed inside the
  decision whose entire purpose is to precede outbound calls, and a resolution that failed would
  have to either deny (a network fault becomes an authorization outage) or permit (fail-open).
- **Fold the target dimension into `connection_scope.py` and keep the module name.** Fewer files,
  and the grant loop stays where ADR 0038 left it. Rejected because a module named for one dimension
  deciding two is the misleading-name defect, and because the orchestrating loop is exactly the
  thing this entry must make hard to get wrong. Splitting it out costs one import and makes the
  combination rule a fifteen-line file.
- **Rename `connection_scope.py` to `dispatch_scope.py` and put everything in it.** The other
  single-module shape. Rejected because it churns #222's file and its 533-line test module one day
  after they landed, to produce a 350-line module in which the loop is again one paragraph among
  many.
- **Deny an `ABSENT` selector under `all-targets` as well as under a table.** Uniform, and it would
  make "every declared selector must be supplied" a property of the boundary rather than of the
  table. Rejected because `all-targets` must be able to express the legacy exposure #225 generates,
  and every call omitting an optional selector is part of that exposure. `UNREADABLE` is denied
  under both, because a malformed call is not part of any exposure.
- **Report which allowlist entries exist in the denial**, or how many. Strictly more actionable.
  Rejected for ADR 0038's reason, unchanged: it discloses the policy through a channel no policy can
  withhold, one entry after ADR 0037 made that disclosure withholdable on purpose.
- **Use one merged denial message for both dimensions**, conceding not even the blocked dimension.
  Marginally tighter, and it would remove the only cross-grant read in the design. Rejected because
  requirement 8 asks the error to name the constraint that blocked, and an operator who cannot tell
  a connection denial from a target denial must bisect their policy file to find out — a cost paid
  on every misconfiguration to withhold one bit an attacker learns from a second probe anyway.
- **Make `exhaustive_targets` default to `False` and require every tool to opt in.** The fail-closed
  default. Rejected because it means 127 opt-ins, and a 127-line diff of security assertions written
  in one sitting is a worse guarantee than three declarations plus a static check — the opt-ins
  themselves become the fail-open. The check is what makes `True` safe as a default.
- **Derive `exhaustive_targets` in `tool()` instead of declaring it**, by running the guardrail's
  own nested-field inspection at registration. Since that inspection reproduces the declared set
  exactly, this is the closest call in the record: it would make a new composite un-narrowable
  automatically, with no author action and no test to notice. Rejected on two grounds. The
  inspection is a *heuristic* — a name-table match one level deep — and making a heuristic
  authoritative at runtime means every tool's policy semantics depend on it; as a suite check its
  false negative is a missing guardrail, as a runtime derivation its false negative is a live
  fail-open with nothing left to catch it. And it would put `typing.get_type_hints` on the import
  path of `server.py` for 130 handlers, where an unresolvable annotation stops the server rather
  than the suite. Residual: the declaration can drift from the derivation, which is precisely what
  the guardrail asserts on every run.
- **Enforce targets on mutating effects only, as issue #223's title reads.** Rejected in the
  Decision, on ADR 0038's three reasons, and recorded as a deliberate widening of the issue's stated
  outcome rather than absorbed silently.
