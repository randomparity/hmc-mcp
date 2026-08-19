# 0036 — Grant-based server access policies, loaded apart from connection config

## Status

Accepted (2026-08-18)

## Context

ADR 0035 gave every one of the 129 live MCP tools a `ToolSecurity` record — effect
class, operation identity, target kind, and the public arguments carrying connection
and target selectors. Nothing reads it yet. Epic #218 requirement 2 needs a *named
server access policy*, selected at startup, immutable for the process lifetime, that
five later entries evaluate: the capability ceiling at registration (#221), connection
scope at dispatch (#222), exact target constraints (#223), audit reason codes (#224),
and fail-closed startup with a legacy-equivalent generator (#225).

This record covers loading, validating, and compiling that policy. It does not
authorize anything.

The repository already has a named-selection mechanism that must not be confused with
this one. `config.py` resolves *HMC connection profiles* from a platform-native
`config.toml`, and `common.py:build_config` lets an MCP caller pick one through the
public `profile` tool argument. That file holds credentials, is read with
`extra="ignore"`, and is selectable from a tool argument — three properties an
authorization artifact must not have.

## Decision

A server access policy is a **list of grants**. A grant names tools (by effect class,
by explicit tool name, or both), the HMC connections those tools may use, and the
targets they may act on. The policy's capability ceiling is *derived* — the union of
the tools its grants resolve to — rather than declared a second time.

Policies live in their own file, `access-policy.toml`, in the same platform-native
directory as `config.toml`:

```toml
[[policies.lab-provisioning.grants]]
tools = ["hmc_create_lpar"]
connections = ["lab"]
targets = { managed_system = ["Server-9080-HEX-SN123456"] }
```

The specification carries the full format and the numbered validation rules; this
record decides their shape, not their spelling.

`load_access_policy(name, path=..., tool_security=...)` reads the file, validates it,
and compiles the named policy into a frozen `AccessPolicy`. The tool index is a
**parameter**, not an import: `server.py` will import the policy module in #221, so
the dependency must not run the other way.

**Grants combine disjunctively; a grant is evaluated conjunctively.** A request is
permitted only when some *single* grant covers its tool, its connection, and its
targets together. The dimensions are never unioned independently across grants: a
policy whose first grant permits all reads on `prod` and whose second permits
`hmc_delete_lpar` on `lab`/`scratch-01` must not be read as permitting
`hmc_delete_lpar` on `prod`. `AccessPolicy.tools` is the derived ceiling for #221's
registration filter and is never sufficient authorization on its own — #222 and #223
evaluate a grant, not a dimension. This rule is stated here because #221, #222, and
#223 each read exactly one dimension and would otherwise each infer the combination
separately.

Five choices carry the design.

**A separate file, not a section of `config.toml`.** `config.toml` holds passwords, so
reviewing a policy would mean handling credentials. #225 must generate a
legacy-equivalent policy without overwriting anything, and the repository has no TOML
*writer* — only `tomllib` — so appending to an existing credential file is not
something that can be done safely. A separate file is written whole or not at all.
The two files also want opposite strictness: `HMCConfig` ignores unknown keys, and a
policy must reject them.

**Grants only; the ceiling is derived.** Requirement 3 asks for "a capability ceiling
by effect class and explicit tool" and, separately, for an `all-targets` sentinel
"permitted only inside grants that already name allowed tools and connection
profiles". A grant already names tools and connections, so a second top-level ceiling
would restate what the grants say and could disagree with them. The ceiling is
`set().union(*(grant.tools for grant in policy.grants))`.

Tool selection inside a grant is a **union**: every tool whose effect is in `effects`,
plus every tool in `tools`. Intersection was the alternative — `effects` plus `tools`
would then mean "the named tools that also carry this effect", a narrowing no
requirement asks for and one an operator would read as additive. Union keeps a grant a
single additive statement. Naming a tool the same grant's `effects` already covers is
therefore a no-op, and it is *not* rejected; see the rejected alternatives.

**`arbitrary-command` cannot be granted by effect class.** `effects` accepts `read`,
`mutate`, and `destructive` only; `hmc_run_command` must be named in `tools`. There is
exactly one such tool, so its name costs one string and makes requirement 6 — a
destructive grant does not imply arbitrary-command access — structural.

**`targets` is one key with two forms**, the literal string `"all-targets"` or a table
of target kind to exact selector strings; one key makes "exactly one of" structural
rather than checked. The sentinel is bounded in the sense requirement 3 means: a fixed
literal that widens *targets only*, never tools and never connections, admitting no
partial form, so there is no wildcard language to reason about.

**The environment/default connection is the reserved token `"<default>"`**, compiled to
`None` — the value `common.py:build_config(profile=None)` already means. The compiled
grant therefore speaks the runtime's vocabulary and #222 needs no translation table.
`<default>` is not a valid TOML bare key, so a colliding profile name would have to be
written quoted; policy validation deliberately does not read `config.toml`. What the
token *denotes* is late-bound, which is the residual recorded below.

The validation rules are enumerated and numbered in the specification. One is a
decision rather than mechanics:

**A grant must cover the required target selectors of every tool it names *explicitly*.**
For each tool in a grant's `tools`, every required `TargetSelector` kind must be
satisfied — by `"all-targets"` or by that kind appearing in the `targets` table — or the
load fails. Requirement 5's "fails closed if metadata cannot extract a required … target
selector" is a *call-time* rule; this is a load-time coverage rule the record invents
from it, because an uncovered required selector can only ever be denied, so the grant is
dead, and a dead grant in a security artifact is an authoring error worth failing on.

The rule binds **only** explicitly named tools. Tools reached through `effects` are
exempt, for two reasons. Their set is the shipping index's, not the operator's: a release
that adds a tool to a granted effect class would otherwise make an unedited, previously
valid file fail to load, and under #225's fail-closed startup that is a server that does
not start after an upgrade. And the exempt case is one the rule could not usefully catch
anyway — `hmc_get_job` and `hmc_wait_for_job` (`read`) and `hmc_update_console_software`
(`mutate`) carry required selectors whose values the HMC mints at runtime, so the only
form that satisfies coverage for an effect-class grant is `"all-targets"`. A rule
justified as fail-closed whose sole satisfiable remedy is the *widest* form in the format
is working against itself. Under the exemption, an effect-class grant may carry a partial
`targets` table and the tools it does not cover are denied at call time by #223.

Optional selectors are never required to be covered — how an absent optional selector is
treated is #223's decision, per ADR 0035.

This rule fails the load where the unknown-connection case below does not, and the line
between them is what the *validator* must read to decide. A tool's required selector
kinds are compiled-in metadata that validation already holds; whether a connection-profile
name exists means reading `config.toml`. Later rules should follow that split.

Two grants can never contradict each other. The policy is a pure additive allowlist with
no deny form, so the union of any two grants is well defined, and #220's
"contradictory grants" criterion reduces to the duplicate-grant and subsumption rules, of
which subsumption is deliberately partial.

**Immutability is a property of the object, not of a singleton.** The compiled policy is
frozen and the module holds no policy state; #221 and #225 pass the object explicitly into
composition. A process-global holder was rejected: `create_mcp()` is called repeatedly —
at import and again per test — and ADR 0035 already records what a registration-time
global costs.

## Consequences

- Nothing enforces this policy. After this change the repository can load and reject
  policy files and answer "does this policy permit this tool", and no caller's reach
  changes. That is the sequenced risk of entry 2 of seven, and it is larger here than
  in ADR 0035: a policy file that exists and validates looks like a control until
  #221 lands.
- Reads are connection-scoped in the model, but #222 as written authorizes *mutations*
  at the dispatch boundary. A policy that lists `connections = ["lab"]` on a read grant
  therefore expresses a constraint nothing currently enforces. Recorded on #222 rather
  than resolved here; the alternative — omitting `connections` from read grants — would
  bake #222's scope into the file format and make it unfixable later.
- An effect-class grant's `targets` table is only ever partial, and nothing at load says
  which tools it leaves uncovered. `effects = ["destructive"]` reaches required selectors
  of six kinds (`cluster`, `lpar`, `managed_system`, `password_policy`, `user`, `vios`);
  a table naming only `lpar` grants the rest nothing, and #223 denies them at call time.
  The grant reads broader than it behaves. That is fail-closed and therefore the safe
  direction, but it is a real usability cliff, and the place to surface it is #221's
  effective-permission inspection, which can show the tools a policy actually reaches.
  `"all-targets"` remains the only form that covers an effect class completely, because
  `hmc_get_job`, `hmc_wait_for_job`, and `hmc_update_console_software` carry required
  selectors the HMC mints at runtime.
- A policy's meaning is coupled to the shipping tool index. `effects = ["read"]` silently
  gains any tool a later release adds to that class, with no edit to the file the operator
  reviewed, and reclassifying a tool moves it between grants the same way. An operator
  wanting a ceiling stable across upgrades must enumerate `tools`. Load-time validation is
  deliberately arranged so that no index change alone can make an unedited file stop
  loading: every rule that can fail on the index binds only what the operator wrote by
  name.
- The compiled `Grant` exposes `tools`, `connections`, and `targets` and stops there. It
  carries no `matches()` method, because exact-selector matching, the
  `vios_uuid`/`vios_partition_id` namespace split, `metric_resource`'s dependence on
  `category`, composite tools, and `dry_run` are all #223's, and a matcher written now
  would decide them silently.
- Policy validation does not read `config.toml`. A grant may name a connection profile
  that does not exist, and load succeeds. Cross-checking would make policy loading fail
  when a profile is renamed and would couple an authorization artifact to a credential
  file; #221's permission inspection is the better place to surface the mismatch.
  The consequence is that a typo in a connection name fails closed at call time rather
  than at load.
- `"<default>"` denotes whatever HMC the deployment selects, by design — it is an alias,
  not a connection identity. `build_config(profile=None)` resolves through `HMC_PROFILE`,
  then `default_profile` in `config.toml`, then bare `HMC_*` variables, so a policy
  granting `["lab"]` on one grant and `["<default>"]` on another also reaches `prod`
  whenever the deployment's `default_profile` is `prod` — and at #222's boundary that
  reach is obtained by *omitting* the `profile` argument rather than supplying it. This
  record takes the by-design reading rather than resolving the token, and the operator
  consequence is the rule to write down: **a policy that withholds a connection must not
  grant `<default>` unless the deployment's default is one it means to allow.** An
  operator who needs a fixed identity names the profile. The lesser residual: a profile
  keyed `"<default>"` in `config.toml` — which requires a quoted TOML key — cannot be
  granted at all.
- Selector strings are form-ambiguous. `lpar_name_or_uuid`, `system_name_or_uuid`,
  `target_system_name_or_uuid`, `vios_name_or_uuid`, and `resource_name_or_uuid` each map
  to one `TargetKind`, and `common.py`'s resolvers accept a name or a UUID
  interchangeably. So `targets = { lpar = ["db-01"] }` binds a caller who sends the name
  and not one who sends that partition's UUID. The file is meant to carry the exact
  argument value the caller sends, so an operator must list every form they intend to
  allow until #223 canonicalizes — and canonicalizing later may be a breaking change to
  an operator-visible file. This affects more selectors than ADR 0035's
  `vios_uuid`/`vios_partition_id` note, which is one case of the same problem.
- One allowlist per kind spans both roles ADR 0035 distinguishes. `managed_system` is the
  kind of `system_name_or_uuid`, the system acted on, *and* of
  `target_system_name_or_uuid`, the migration destination on `hmc_migrate_lpar` and three
  siblings; `template` and `lpar` each carry two arguments the same way. So listing the
  systems an operator may create partitions on also authorizes those systems as migration
  destinations for any migrate tool in the same grant, and "may migrate out of prod-A only
  into lab-B" is not expressible. Keying `targets` by argument instead of kind would fix
  it and would tie an operator-visible file to Python parameter names; requirement 3 asks
  for kinds. The `(kind, argument)` role distinction is handed to #223.
- `connections` binds only tools that carry a connection argument.
  `hmc_list_configured_hosts` declares `connection_argument=None`, is effect `read`, and
  returns every configured profile's name, host, user, and default flag — so it lands
  inside any effect-class read grant and a `connections = ["lab"]` grant still discloses
  the `prod` inventory. #222 must decide what a connection-less tool means; until it does,
  an operator who cares should not grant that tool by effect class.
- Subsumption detection is partial. The subsumption rule rejects a grant subsumed by a sibling that
  carries `"all-targets"`, which is the case that needs no matching semantics. General
  subsumption between two target *tables* would require deciding what a narrower kind
  set means, which is #223's; a check written now would be an unverified claim about
  semantics that do not exist yet.
- The module adds no runtime dependency: `tomllib` is stdlib and `pydantic` is already a
  core dependency. It is deliberately absent from `api.__all__` — ADR 0029 places the
  server policy boundary outside the supported reusable Python API, and the CLI and that
  API keep their existing HMC authorization boundary.
- The policy file is operator-controlled and sits at the same trust level as
  `config.toml`. Anyone who can write it can widen the ceiling. The module does not
  check file modes; that is filesystem policy, and adding a check here and not on the
  credential file would be theatre.

## Considered & rejected

- **Do nothing: rely on ADR 0035's client-facing annotations plus the existing
  arbitrary-command toggle.** Every tool already ships `readOnlyHint`/`destructiveHint`,
  and the one maximum-risk tool is already off by default. Rejected because an annotation
  is advisory and the party that would honour it — the MCP client and the agent driving
  it — is the untrusted party; nothing server-side constrains reach today.
- **Express the ceiling in `HMCConfig` or a new section of `config.toml`.** Issue #220
  excludes authorization state from `HMCConfig` outright, and the file-mixing problems are
  in the Decision above: credentials in the review path, no safe generator, and opposite
  unknown-key strictness.
- **A top-level capability ceiling plus separate grants.** Closer to requirement 3's
  literal wording, and it lets an operator see the ceiling without reading every grant.
  Rejected because the ceiling then has to agree with the grants and nothing forces it
  to; a policy whose ceiling permits a tool no grant covers is the fail-open shape this
  format exists to prevent. #221's permission inspection can present the derived ceiling.
- **A `default_policy` key in the file, mirroring `default_profile`.** Convenient, but
  requirement 9 wants startup to refuse when no policy is *selected*, and a file that can
  select for the operator is one step from a deployment where nobody chose.
- **Wildcards or globs in target selectors (`Server-*`).** A general expression engine is
  an explicit non-goal of #218, and a glob over resource names is a selector-confusion
  bypass waiting for a renamed LPAR. The single `"all-targets"` literal covers the cases
  that motivated wildcards — legacy-equivalent exposure, and the runtime-minted selector
  values noted in the consequences — with one form that has no partial reading.
- **Deny rules alongside allow rules.** Order-dependent and it makes "contradictory
  grants" a genuinely hard question. An allowlist with no denies has one reading.
- **Reject a tool named in `tools` that the grant's own `effects` already covers.** This
  was a rule in an earlier draft, on the ground that the entry describes a narrowing that
  did not happen. It does not: within one grant the connections and targets apply to both
  routes identically, so the entry is inert noise rather than a misleading narrowing —
  the misleading case is cross-grant, and the subsumption rule covers it. Rejecting inert
  noise is a lint whose false positive is a server that will not start: reclassifying one
  tool into an effect class a grant already names would make an unchanged file fail to
  load under #225's fail-closed startup, over an entry that grants nothing.
- **No load-time selector-coverage rule at all; let #223 deny an uncovered selector at
  call time, where requirement 5 put it.** This is the null option for the one rule the
  record invents, and the same lint test that dropped the entry above has to be answered:
  what makes coverage worth a load failure when redundancy was not? Two things. A
  redundant tool name grants and denies nothing, so rejecting it is a guess at intent,
  whereas an uncovered required selector makes a grant *dead* — the operator wrote a
  permission that can never fire, and would meet it as an unexplained denial rather than
  an error naming the tool and the kind. And the lint's false positive was an index change
  the operator did not make; scoping coverage to explicitly named tools removes exactly
  that case, so the rule now fires only on text the operator wrote.
- **Narrow the coverage rule to statically enumerable selector kinds**, excluding `job`
  and `console`, rather than to explicitly named tools. Rejected because an exempted kind
  then has no allowlist entry at call time and whether that means allow or deny is #223's
  — exempting a kind would either open a silent hole or leave the grant just as dead,
  while exempting effect-class membership leaves every kind constrainable and moves
  nothing into #223 that was not already there.
- **Resolve `"<default>"` to a concrete profile at load, or forbid the token entirely.**
  Resolving would make the policy name a fixed HMC and would read `config.toml` and the
  environment inside policy loading — the credential-file coupling rejected above, bought
  for a token whose whole purpose is to name the connection an operator did *not*
  configure by name. Forbidding it would make an env-var-only deployment — the shape the
  README documents first — unable to express any connection at all.
- **Pydantic all the way to the runtime form, with no compile step.** Fewer types, but
  the tool index would have to travel through pydantic validation context, and frozen
  pydantic models still hold mutable lists. Parsing shape with pydantic and compiling to
  frozen dataclasses keeps the index-dependent rules in a plain, directly testable
  function.
- **A module-level `SELECTED_POLICY` set once at startup.** Simplest for #221 to read,
  and it is exactly the mutable-global-built-as-a-side-effect shape ADR 0035 removed from
  the registry. Explicit passing keeps `create_mcp()` repeatable per test.
