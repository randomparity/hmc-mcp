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
authorization artifact must not have. Two further names in the tree are unrelated and
are not touched: the `authorize_*` functions in `operations_lpar.py` are the
multi-agent LPAR ownership-token convention, and `PasswordPolicySettings` in
`documents.py` is an HMC password-policy DTO.

## Decision

A server access policy is a **list of grants**. A grant names tools (by effect class,
by explicit tool name, or both), the HMC connections those tools may use, and the
targets they may act on. The policy's capability ceiling is *derived* — the union of
the tools its grants resolve to — rather than declared a second time.

Policies live in their own file, `access-policy.toml`, in the same platform-native
directory as `config.toml`:

```toml
[policies.read-only]
grants = [
  { effects = ["read"], connections = ["<default>"], targets = "all-targets" },
]

[[policies.lab-provisioning.grants]]
effects = ["read"]
connections = ["lab"]
targets = "all-targets"

[[policies.lab-provisioning.grants]]
tools = ["hmc_create_lpar"]
connections = ["lab"]
targets = { managed_system = ["Server-9080-HEX-SN123456"] }
```

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
exactly one such tool, so requiring its name costs one string and makes requirement 6
— a destructive grant does not imply arbitrary-command access — structural rather than
a convention.

**`targets` is one key with two forms**, the literal string `"all-targets"` or a table
of target kind to exact selector strings. Two keys with a mutual-exclusion rule was the
alternative; one key makes "exactly one of" impossible to violate rather than merely
checked. The sentinel is bounded in the sense requirement 3 means: it is a fixed
literal that widens *targets only*, never tools and never connections, and it admits no
partial form, so there is no wildcard language to reason about.

**The environment/default connection is the reserved token `"<default>"`**, compiled to
`None` — the value `common.py:build_config(profile=None)` already means. The compiled
grant therefore speaks the runtime's vocabulary and #222 needs no translation table.
`<default>` is not a valid TOML bare key, so a colliding profile name would have to be
written quoted; policy validation deliberately does not read `config.toml`. What the
token *denotes* is late-bound, which is the residual recorded below.

Validation rules P1–P12 are enumerated in the specification. One is a decision rather
than mechanics:

**P9 requires every *required* target selector of every granted tool to be covered** —
by `"all-targets"` or by its kind appearing in the `targets` table. Requirement 5's
"fails closed if metadata cannot extract a required … target selector" is a *call-time*
rule; P9 is a load-time coverage rule this record invents from it, on the ground that an
uncovered required selector can only ever be denied, so the grant is dead and a dead
grant in a security artifact is a defect worth failing at load. Optional selectors are
*not* required to be covered — how an absent optional selector is treated is #223's
decision, per ADR 0035.

P9 fails the load where the connections rule below does not, and the line between them
is what the *validator* must read to decide, not whether the values involved are stable.
A tool's required selector kinds are compiled-in metadata that validation already holds;
whether a connection-profile name exists means reading `config.toml`. That a `job_uuid`
is minted by the HMC at runtime is a different axis and does not move P9 across the line
— P9 constrains kinds, never values. Later rules should follow that split.

Two grants can never contradict each other. The policy is a pure additive allowlist with
no deny form, so the union of any two grants is well defined, and #220's
"contradictory grants" criterion reduces to duplication (P10) and subsumption (P11), of
which P11 is deliberately partial.

**Immutability is a property of the object, not of a singleton.** `AccessPolicy` and
`Grant` are frozen dataclasses over `frozenset` and `MappingProxyType`; the module
exposes no mutator, no reload, and no module-level policy state. #221 and #225 will
pass the policy explicitly into composition. A process-global holder was rejected:
`create_mcp()` is called repeatedly — at import and again per test — and ADR 0035
already records what a registration-time global costs.

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
- `"all-targets"` is the mandatory form for any effect-class grant, not the
  legacy-migration edge case it looks like. Two required selector kinds carry values no
  static file can enumerate: `job`/`job_uuid` on `hmc_get_job` and `hmc_wait_for_job`, and
  `console`/`console_uuid` on `hmc_get_available_hmc_ptfs` and
  `hmc_update_console_software`. Since selectors are exact strings with no wildcard form,
  P9 cannot be satisfied for any grant containing `effects = ["read"]` — the read set
  includes both job tools — except by the sentinel. Target-scoped reads are still
  expressible, but only by naming tools rather than an effect class.
- A policy's meaning is coupled to the shipping tool index in both directions.
  `effects = ["read"]` silently gains any tool a later release adds to that class, with
  no edit to the file the operator reviewed. In the other direction, a tool that gains a
  required selector kind the grant's `targets` table does not cover makes an unchanged,
  previously-valid file fail P9 — under #225's fail-closed startup that is a server that
  will not start after an upgrade. An operator wanting a ceiling stable across upgrades
  must enumerate `tools`. The upgrade-time startup failure is #225's to handle.
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
- `"<default>"` is a late-bound alias, not a connection identity, and this is the
  sharpest residual in the record. `build_config(profile=None)` resolves through
  `HMC_PROFILE`, then `default_profile` in `config.toml`, then bare `HMC_*` variables —
  so the token denotes whatever HMC the process environment selects, including a *named*
  profile the same policy withholds elsewhere. A policy granting `["lab"]` on one grant
  and `["<default>"]` on another reaches `prod` whenever the deployment's
  `default_profile` is `prod`, and at #222's boundary that reach is obtained by *omitting*
  the `profile` argument rather than supplying it. #222 must choose: either `<default>`
  means "whatever the deployment selects", by design, or it must compare the *resolved*
  profile identity rather than the literal token. This record does not choose, because
  choosing means reading `config.toml` at authorization time. The lesser residual: a
  profile keyed `"<default>"` in `config.toml` — which requires a quoted TOML key —
  cannot be granted at all.
- Selector strings are form-ambiguous. `lpar_name_or_uuid`, `system_name_or_uuid`,
  `target_system_name_or_uuid`, `vios_name_or_uuid`, and `resource_name_or_uuid` each map
  to one `TargetKind`, and `common.py`'s resolvers accept a name or a UUID
  interchangeably. So `targets = { lpar = ["db-01"] }` binds a caller who sends the name
  and not one who sends that partition's UUID. The file is meant to carry the exact
  argument value the caller sends, so an operator must list every form they intend to
  allow until #223 canonicalizes — and canonicalizing later may be a breaking change to
  an operator-visible file. This affects more selectors than ADR 0035's
  `vios_uuid`/`vios_partition_id` note, which is one case of the same problem.
- Subsumption detection is partial. P11 rejects a grant subsumed by a sibling that
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
  that motivated wildcards — legacy-equivalent exposure, and the runtime-generated
  selector kinds noted in the consequences — with one form that has no partial reading.
- **Deny rules alongside allow rules.** Order-dependent and it makes "contradictory
  grants" a genuinely hard question. An allowlist with no denies has one reading.
- **Reject a tool named in `tools` that the grant's own `effects` already covers.** This
  was a rule in an earlier draft, on the ground that the entry describes a narrowing that
  did not happen. It does not: within one grant the connections and targets apply to both
  routes identically, so the entry is inert noise rather than a misleading narrowing —
  the misleading case is cross-grant, and P11 covers it. Rejecting inert noise is a lint,
  and it is a lint whose false positive is a server that will not start: reclassifying one
  tool into an effect class a grant already names would make an unchanged file fail to
  load under #225's fail-closed startup, over an entry that grants nothing.
- **Narrow P9 to statically enumerable selector kinds**, excluding `job` and `console`,
  so a target-scoped read grant is expressible without the sentinel. Rejected because
  an exempted kind then has no allowlist entry at call time, and whether that means allow
  or deny is #223's decision — exempting now would either open a silent hole or leave the
  grant just as dead. Forcing `"all-targets"` on effect-class read grants is the honest
  form until #223 rules.
- **Pydantic all the way to the runtime form, with no compile step.** Fewer types, but
  the tool index would have to travel through pydantic validation context, and frozen
  pydantic models still hold mutable lists. Parsing shape with pydantic and compiling to
  frozen dataclasses keeps the index-dependent rules in a plain, directly testable
  function.
- **A module-level `SELECTED_POLICY` set once at startup.** Simplest for #221 to read,
  and it is exactly the mutable-global-built-as-a-side-effect shape ADR 0035 removed from
  the registry. Explicit passing keeps `create_mcp()` repeatable per test.
