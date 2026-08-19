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
plus every tool in `tools`. Intersection was the alternative, and it makes the
epic's own limited-policy criterion — all reads plus one named mutation — require
listing all 54 read tools by hand.

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
written quoted; that residual is recorded below rather than solved by reading
`config.toml`, which policy validation deliberately does not do.

Validation rules P1–P13 are enumerated in the specification. Two of them are less
obvious than the rest and are decisions rather than mechanics:

- **P8 rejects a tool named in `tools` that the same grant's `effects` already
  covers.** Under a pure allowlist the entry changes nothing, and an operator who
  writes it is describing a narrowing that did not happen.
- **P10 requires every *required* target selector of every granted tool to be covered**
  — by `"all-targets"` or by its kind appearing in the `targets` table. This follows
  from requirement 5's "fails closed if metadata cannot extract a required … target
  selector": an uncovered required selector can only be denied at call time, so the
  grant is dead, and a dead grant in a security artifact is a defect worth failing at
  load. Optional selectors are *not* required to be covered — how an absent optional
  selector is treated is #223's decision, per ADR 0035.

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
- `"<default>"` is reserved. An HMC connection profile keyed `"<default>"` in
  `config.toml` — which requires a quoted TOML key — cannot be granted, because the
  token compiles to `None` instead. No such profile exists, and none can be created by
  accident.
- Subsumption detection is partial. P12 rejects a grant subsumed by a sibling that
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

- **Do nothing; express the ceiling in `HMCConfig` or a new section of `config.toml`.**
  Issue #220 excludes authorization state from `HMCConfig` outright, and the file-mixing
  problems are in the Decision above: credentials in the review path, no safe generator,
  and opposite unknown-key strictness.
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
  bypass waiting for a renamed LPAR. The single `"all-targets"` literal covers the one
  case — legacy-equivalent exposure — that motivated wildcards.
- **Deny rules alongside allow rules.** Order-dependent and it makes "contradictory
  grants" a genuinely hard question. An allowlist with no denies has one reading.
- **Pydantic all the way to the runtime form, with no compile step.** Fewer types, but
  the tool index would have to travel through pydantic validation context, and frozen
  pydantic models still hold mutable lists. Parsing shape with pydantic and compiling to
  frozen dataclasses keeps the index-dependent rules in a plain, directly testable
  function.
- **A module-level `SELECTED_POLICY` set once at startup.** Simplest for #221 to read,
  and it is exactly the mutable-global-built-as-a-side-effect shape ADR 0035 removed from
  the registry. Explicit passing keeps `create_mcp()` repeatable per test.
