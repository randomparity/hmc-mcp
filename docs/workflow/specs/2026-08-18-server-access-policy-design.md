# Load and validate immutable server access policies

Issue: [#220](https://github.com/randomparity/hmc-mcp/issues/220) — part of epic
[#218](https://github.com/randomparity/hmc-mcp/issues/218).
Decision record: [ADR 0036](../../adr/0036-server-access-policy-model.md).
Builds on: [ADR 0035](../../adr/0035-enforceable-tool-security-metadata.md) (#219).

## 1. Goal

Give the server a named *access policy* that an operator writes in TOML, that is
strictly validated against the authoritative `ToolSecurity` metadata, and that compiles
into an immutable in-process object answering two questions: which tools the policy
permits at all, and which grants apply to a given tool. The policy expresses a
capability ceiling by effect class and explicit tool, an allowed set of HMC connections
including the environment/default connection, exact target selectors per target kind,
and one bounded `all-targets` sentinel.

Out of scope, owned elsewhere in epic #218: registration-time capability-ceiling
filtering and permission inspection (#221), connection-scope authorization at dispatch
(#222), target-constraint matching and composite/dry-run semantics (#223), audit events
(#224), fail-closed startup selection, the legacy-equivalent generator, and operator
documentation (#225). Also out of scope by the issue's own terms: any authorization
state on `HMCConfig`, and any new runtime dependency.

## 2. Current state

`src/hmc_mcp/tool_registry.py` (ADR 0035, merged) exports `Effect`, `TargetKind`,
`TargetSelector`, and `ToolSecurity`. `src/hmc_mcp/server.py:249` builds
`TOOL_SECURITY: Mapping[str, ToolSecurity]` at module scope from the collected
declarations. It holds 129 entries — 54 `read`, 46 `mutate`, 28 `destructive`, and
`hmc_run_command` as the single `arbitrary-command` — and always contains
`hmc_run_command` regardless of the operator toggle, so a lookup cannot fail open.

Eighteen tools declare no target selectors: seventeen `console`-kind tools plus
`hmc_list_configured_hosts`, which is the one tool with `target_kind="none"` and
`connection_argument=None`. Every other tool declares `connection_argument="profile"`.

`src/hmc_mcp/config.py` resolves *HMC connection profiles* from a platform-native
`config.toml` via `config_dir()`, `resolve_config_path()`, and `load_profile()`, raising
`ConfigError`. `src/hmc_mcp/common.py:30` `build_config(profile=None, ...)` selects one;
`profile=None` means the environment/default connection. Nothing in the repository
carries a server access policy today.

## 3. Design

One new module, `src/hmc_mcp/access_policy.py`. It imports `tomllib`, `pydantic`, and
`hmc_mcp.tool_registry`, and `config_dir` from `hmc_mcp.config`. It does **not** import
`hmc_mcp.server`: #221 will make `server.py` policy-aware, and the dependency must run
one way only. The tool index is a parameter.

### 3.1 File format

```toml
# access-policy.toml, beside config.toml in the platform-native config directory

[policies.read-only]
grants = [
  { effects = ["read"], connections = ["<default>"], targets = "all-targets" },
]

[[policies.lab-provisioning.grants]]
effects = ["read"]
connections = ["lab"]
targets = "all-targets"

[[policies.lab-provisioning.grants]]
tools = ["hmc_create_lpar", "hmc_delete_lpar"]
connections = ["lab"]
targets = { managed_system = ["Server-9080-HEX-SN123456"] }
```

`policies` is the only top-level key. A policy is a table with one required key,
`grants`. A grant has four keys:

| key | required | form |
|---|---|---|
| `effects` | no (default `[]`) | array of `"read"`, `"mutate"`, `"destructive"` |
| `tools` | no (default `[]`) | array of live tool names |
| `connections` | **yes** | non-empty array of connection-profile names and/or `"<default>"` |
| `targets` | **yes** | either the string `"all-targets"` or a table of target kind to non-empty array of exact selector strings |

The tools a grant resolves to are the **union** of every tool whose `effect` is in
`effects` and every tool named in `tools`.

### 3.2 Validation rules

Shape rules are enforced by pydantic models carrying
`ConfigDict(extra="forbid", frozen=True)`; semantic rules run in `compile_access_policy`
against the injected tool index. Every failure raises `AccessPolicyError` naming the
source, the policy name, the grant index, and the offending value.

| id | rule |
|---|---|
| P1 | Unknown keys are rejected at every level: an unknown top-level key, an unknown key in a policy table, and an unknown key in a grant table. |
| P2 | `grants` is required on every policy (an explicit `grants = []` is a valid deny-everything policy). `connections` and `targets` are required on every grant; `connections` is non-empty. |
| P3 | `targets` is exactly one of the literal string `"all-targets"` or a **non-empty** table whose keys are `TargetKind` values other than `"none"` and whose values are non-empty arrays of strings. No other form parses. An empty table is rejected: an operator who means "no target restriction" writes `"all-targets"`, and the compiled empty mapping would be falsy beside a truthy `ALL_TARGETS`, which invites `if not grant.targets:` to read the narrowest possible table as the widest grant. |
| P4 | `effects` entries are `read`, `mutate`, or `destructive`. `"arbitrary-command"` is rejected with a message directing the operator to name `hmc_run_command` in `tools` (epic #218 requirement 6). |
| P5 | No array contains a duplicate entry — `effects`, `tools`, `connections`, and every target-kind selector array. No string entry in any of them is empty or whitespace-only. |
| P6 | A grant names at least one tool: `effects` and `tools` are not both empty. |
| P7 | Every name in `tools` is a key of the tool index. |
| P8 | Every key of a `targets` table is the `kind` of at least one `TargetSelector` declared by a tool the grant resolves to — **including tools reached through `effects`**, unlike P9. That makes P8 index-coupled, deliberately: narrowing it to explicitly named tools would let a pure effect-class grant carry an entirely inert `targets` table, which is the "looks like a control" failure the format exists to prevent. The cost is recorded in §4 alongside P9's and P10's. A tool's `target_kind` alone does not qualify it: the 18 selector-less tools, `hmc_run_command` among them, declare no selector, so a `targets` table naming `console` for a grant of only those tools is inert and is rejected. |
| P9 | For every tool a grant names **explicitly in `tools`**, every **required** `TargetSelector` kind is covered — either `targets` is `"all-targets"`, or the kind is a key of the `targets` table. Tools reached through `effects` are exempt. Optional selectors need no coverage; #223 owns their call-time treatment. |
| P10 | No two grants in a policy are identical after compilation. |
| P11 | `load_access_policy` raises `AccessPolicyError` when the file is absent, unreadable, or unparseable, and when the named policy is not in the file; the not-found message lists the available policy names. |

Within the semantic tier (P7–P10, hand-written) rules are checked in numeric order and
the **first** violation is raised, so an input tripping two of them reports the
lower-numbered one. The shape tier makes no such promise: pydantic aggregates every error
into one `ValidationError` ordered by model traversal, not by P number, and mapping error
locations back to P ids would be machinery bought for a testability nicety. Every input in
the acceptance suite therefore carries exactly **one** violation, and the message for an
input violating several shape rules is unspecified beyond naming the file.

P1–P6 are shape rules and bind **every** policy in the document; P7–P10 depend on the tool
index and bind only the **selected** policy. The split is deliberate in both directions. A
malformed policy anywhere is an authoring error the operator wants at the next load, and
the file is meant to be reviewable as a unit. But running the index-dependent rules over
unselected policies would multiply the upgrade-fragility recorded in §4 across policies a
deployment never uses — a staging policy could then stop production from starting.

Naming a tool the same grant's `effects` already covers is inert, not an error; ADR 0036
records why an earlier draft's rule against it was dropped. P9 is a decision rather than
mechanics, and ADR 0036's Decision section records both why it fails the load where the
unknown-connection case does not, and why it binds only explicitly named tools.

The exemption matters in both directions. It means an effect-class grant may carry a
partial `targets` table — `effects = ["destructive"], targets = { lpar = ["db-01"] }`
loads, and the destructive tools requiring a `managed_system`, `vios`, `cluster`, `user`,
or `password_policy` selector are simply denied at call time by #223. The grant reads
broader than it behaves, which is the fail-closed direction; #221's permission inspection
is where that gap becomes visible. And it means the only *complete* form for an effect
class is `targets = "all-targets"`, because `hmc_get_job` and `hmc_wait_for_job` (`read`)
and `hmc_update_console_software` (`mutate`) carry required selectors whose values the HMC
mints at runtime and no static file can enumerate.

Naming those tools explicitly is what P9 catches: `tools = ["hmc_delete_lpar"]` with
`targets = { managed_system = ["S1"] }` is rejected, because the operator wrote a
permission for a tool whose required `lpar` selector nothing covers, and it could never
fire. The error names the tool and the uncovered kind.

### 3.3 Compiled form

```python
DEFAULT_CONNECTION_TOKEN = "<default>"
ACCESS_POLICY_FILENAME = "access-policy.toml"

class AccessPolicyError(ValueError): ...

@dataclass(frozen=True)
class AllTargets: ...          # repr "ALL_TARGETS"
ALL_TARGETS = AllTargets()

@dataclass(frozen=True)
class Grant:
    tools: frozenset[str]
    connections: frozenset[str | None]                          # None == default connection
    targets: AllTargets | Mapping[TargetKind, frozenset[str]]   # MappingProxyType

@dataclass(frozen=True)
class AccessPolicy:
    name: str
    source: str                 # operator-facing origin, e.g. the file path
    grants: tuple[Grant, ...]
    tools: frozenset[str]       # derived capability ceiling
    def permits_tool(self, tool: str) -> bool: ...
    def grants_for(self, tool: str) -> tuple[Grant, ...]: ...

def resolve_access_policy_path() -> Path: ...
def compile_access_policy(document, name, tool_security, source) -> AccessPolicy: ...
def load_access_policy(name, tool_security, *, path=None) -> AccessPolicy: ...
```

`"<default>"` compiles to `None`, matching `build_config(profile=None)`. `AccessPolicy.tools`
is the union of every grant's `tools`. `Grant` sets `__hash__ = None`, so it is uniformly
unhashable and P10 compares grants by equality. Without that, hashability would depend on
the operator's file: a frozen dataclass hashes its field tuple, so a grant carrying
`ALL_TARGETS` (a no-field frozen dataclass) hashes while one carrying a `MappingProxyType`
raises `TypeError` — a downstream that keyed grants into a set would pass every
all-targets test and fail on the first target-scoped policy.

`grants_for(tool)` exists because grants combine **disjunctively while each grant is
evaluated conjunctively**: a request is permitted only when one single grant covers its
tool, its connection, and its targets together, and the dimensions are never unioned
independently across grants (ADR 0036). `permits_tool` answers the ceiling question for
#221's registration filter only and is never sufficient authorization on its own; #222
and #223 must evaluate a grant returned by `grants_for`, not a union of connection or
target sets.

`load_access_policy` defaults `path` to `resolve_access_policy_path()`. `tool_security`
is **required** and has no default: a convenience default would have to reach
`server.TOOL_SECURITY` through a deferred in-function import, making the dependency
two-way in fact while the design says it is one-way, and leaving an unstated ordering
constraint on `server.py`'s module body. #225's startup path passes the index explicitly.
`compile_access_policy` is pure and does the I/O-free work; it is what the tests drive.

### 3.4 Immutability

`AccessPolicy` and `Grant` are frozen dataclasses over `frozenset`, `tuple`, and
`MappingProxyType`. The module defines no mutator, no reload function, and no
module-level policy state; the underlying dict behind each `MappingProxyType` is built
locally and never retained. A policy is therefore fixed from construction, and there is
nothing for an MCP tool argument to reach — no policy value is read from a request, an
environment variable, or `HMCConfig`. #221 and #225 pass the object explicitly rather
than reading a global, which keeps `create_mcp()` repeatable per test (ADR 0035).

### 3.5 Error messages

`AccessPolicyError` messages follow `config.py`'s fail-fast convention: the source, then
the policy and grant, then what was wrong and what to do. For example —

```
access-policy.toml: policy 'lab': grant 1: unknown tool 'hmc_create_lpars'
access-policy.toml: policy 'lab': grant 0: 'arbitrary-command' cannot be granted by
  effect class; name 'hmc_run_command' in tools instead
access-policy.toml: policy 'lab': grant 2: tool 'hmc_delete_lpar' requires a target
  constraint for kind 'lpar'; add it to targets or use targets = "all-targets"
access-policy.toml: policy 'typo' not found; available policies: lab, read-only
access-policy.toml: policy 'lab': grant 0: 'targets' must be the string "all-targets" or
  a table of target kind to selector strings; got "all_targets"
```

Messages name tool names, target kinds, connection-profile names, and the file path.
None of those is a credential. Target *selector values* (system and LPAR names) are
operator-supplied and are echoed only in the P5 duplicate/empty messages, where the
offending value is what the operator needs to see; these errors surface at startup to
an operator, not to an MCP client.

## 4. Threat model

Security-relevant on intent: this defines the artifact a later authorization boundary
reads, and it parses a file the process did not produce.

**Boundary inventory.** One boundary is added; none is widened.

1. *Policy file → process* (new, filesystem, operator-controlled). Input: a TOML
   document naming policies, effect classes, tool names, connection-profile names, and
   target selector strings. Crossed once, at load, before serving.
2. *Tool index → policy compile* (in-process, first-party). `TOOL_SECURITY` is
   compiled-in literals validated at import by ADR 0035's V1–V11. Unchanged.

No MCP request, tool argument, or HMC response reaches either boundary. The policy is
never constructed from anything a client sends — epic #218 requirement 2.

**Actor model.** The untrusted party is the MCP client and whatever drives it, including
an LLM agent exposed to prompt injection through HMC data it reads. The trusted parties
are the repository's own source and the local operator. The policy file sits at the same
trust level as `config.toml`: an actor who can write it can already write the credential
file and point the server at an HMC of their choosing, so the file's integrity is a
filesystem-permissions property of the deployment, not something this module can
establish. That trust placement is stated because it is the assumption the whole epic
rests on.

**Control per boundary.**

1. *Policy file → process*: P1 rejects unknown keys, so a misspelled key never silently
   becomes a wider policy; P4 and P7 reject values outside the closed vocabularies; P2,
   P6, and P9 fail closed on omission rather than defaulting to permissive; P10 rejects a
   duplicated grant. Parsing is `tomllib` — stdlib, no code
   execution, no object construction from the document. A parse error, an unreadable
   file, and an absent file are all `AccessPolicyError`, never a silently empty policy.
   The result is frozen (§3.4).
2. *Tool index → compile*: the caller supplies the index; the module reads it and never
   writes it. `MappingProxyType` from `server.py` is already read-only.

**Explicitly out of scope.**

- Nothing enforces the policy after this change. A valid policy file does not reduce any
  caller's reach until #221. This is the accepted, sequenced risk of entry 2 of seven,
  and it is the one an operator is most likely to misread — the file looks like a
  control before it is one.
- File mode, ownership, and symlink checks on the policy file. Its trust level equals
  `config.toml`'s, which the repository does not check either; a check on one and not the
  other would suggest a distinction that does not exist.
- Resource bounds on the document (size, nesting depth). The file is operator-authored
  and read once at startup, before serving; a hostile file implies an actor who can
  already replace the credential file.
- Connection-profile names are not cross-checked against `config.toml` (ADR 0036
  consequences). A typo fails closed at call time under #222, not at load.
- `connections` allowlists the `profile` argument token, not an HMC endpoint.
  `build_config` skips profile resolution entirely when `HMC_HOST` is set and discards the
  `profile` argument, so in an env-var-only deployment every granted connection name
  resolves to the same HMC. `"<default>"` is a late-bound alias for whatever the
  deployment selects, which may be a named profile the same policy withholds elsewhere;
  and a policy naming only profiles does not cover a caller who omits `profile`. Whether
  #222 compares the literal token or a resolved identity is #222's decision. The token
  space is many-to-one by a second, independent route as well: `load_profile` resolves an
  ADR 0030 `nicknames` table one level deep and case-sensitively, so several distinct
  `profile` values may reach the same profile — and a nickname may point at a profile the
  policy withholds. Withholding an HMC means withholding every token that reaches it, and
  because validation never reads `config.toml`, nothing here can enumerate that set.
  Recorded in ADR 0036.
- Selector strings are form-ambiguous: `lpar_name_or_uuid` and its four siblings accept a
  name or a UUID interchangeably, so an allowlist binds only the form the caller sends
  until #223 canonicalizes. Recorded in ADR 0036.
- One allowlist per kind spans both of ADR 0035's roles: `managed_system` covers the
  system acted on and `hmc_migrate_lpar`'s migration *destination*, so a grant listing
  systems for one role authorizes the other. The `(kind, argument)` distinction is #223's.
- A `targets` table bounds only the identities ADR 0035's selectors name, per that
  record's own instruction to its downstream entries: `hmc_provision_lpar`'s nested VIOS
  and storage identities and the profile backup/restore `file_path` sit outside every
  grant.
- Legacy *connection* reach is not expressible. `connections` is an exact-string
  allowlist with no all-connections sentinel, while today any caller may pass any
  `profile` token: in an env-var-only deployment an arbitrary, never-configured token
  succeeds, and in a `config.toml` deployment reproducing current reach means enumerating
  every profile *and* every ADR 0030 nickname — an enumeration only #225's generator can
  perform, by reading the credential file ADR 0036 keeps out of the policy path, and one
  that stops covering any profile added later. #225 owns whatever approximation it emits.
  An all-connections sentinel is deliberately not added: the charter authorizes "an
  allowed set of HMC connection profiles".
- An unselected policy's index-dependent rules never run, so a policy can sit in a
  reviewed file looking valid for months and fail on first selection — including one that
  was valid when written and drifted with a later index. Nothing here offers an
  all-policies check; #225 owns whether startup selection gains one and how the failure is
  made diagnosable. The realistic cost is an operator switching to a fallback policy
  during an incident and meeting a P7–P10 violation at that moment.
- An index change alone can make an unedited policy file stop loading: P8 resolves tool
  sets including effect-class members (several kinds are backed by a single
  selector-declaring tool — `shared_storage_pool` by `hmc_get_shared_storage_pool` alone),
  P9 reads a `required` flag ADR 0035 derives from the handler signature, and P10 compares
  index-resolved tool sets. Under #225's fail-closed startup that is a failed start after
  an upgrade; making it diagnosable is #225's. Recorded in ADR 0036.
- The `serve --enable-arbitrary-command` flag remains an independent outer gate on
  `hmc_run_command`; per epic #218 requirement 6 the flag and a naming grant compose
  conjunctively, and #221 implements that intersection.
- `connections` is inert on `hmc_list_configured_hosts`, which carries no connection
  argument and returns every configured profile's name, host, user, and default flag. It
  is effect `read`, so it falls inside any effect-class read grant and a
  `connections = ["lab"]` grant still discloses the `prod` inventory. #222 must decide
  what a connection-less tool means.
- Target-selector *matching*, the `vios_uuid`/`vios_partition_id` namespace split,
  `metric_resource`'s dependence on `category`, composite tools, and `dry_run` are #223's.
  The compiled `Grant` exposes the allowlists and no matcher.
- Read grants carry `connections`, but #222 authorizes mutations at the dispatch
  boundary. Until that is settled on #222, a read grant's connection list is expressible
  and unenforced.

**AI surface.** No LLM call, prompt, system message, retrieval path, classifier, or agent
loop is added or modified. No tool is registered, and no tool description or annotation
changes. No eval plan applies.

## 5. Acceptance criteria

Each is a test in `tests/unit/test_access_policy.py` unless stated otherwise.

| id | criterion |
|---|---|
| A1 | A policy with one grant of `effects = ["read"]` compiles to a ceiling equal to exactly the set of tool names whose `effect` is `read` in the live index (54), and `permits_tool` is `False` for every `mutate`, `destructive`, and `arbitrary-command` tool. |
| A2 | A policy granting `effects = ["read"]` on one grant and `tools = ["hmc_create_lpar"]` on another compiles to a ceiling of the reads plus that one tool; `permits_tool("hmc_delete_lpar")` is `False`, and `grants_for("hmc_create_lpar")` returns exactly the second grant. |
| A3 | `effects = ["arbitrary-command"]` raises `AccessPolicyError` whose message names `hmc_run_command` (P4). `effects = ["read", "mutate", "destructive"]` produces a ceiling that excludes `hmc_run_command`, and `tools = ["hmc_run_command"]` includes it. |
| A4 | `"<default>"` in `connections` compiles to `None` in `Grant.connections`; a named profile compiles to its own string; both may appear in one grant. |
| A5 | The legacy-equivalent shape — one grant with `effects = ["read", "mutate", "destructive"]`, two connections, and `targets = "all-targets"` — validates and compiles to a ceiling of exactly the 128 collector-declared tools. The criterion pins the *tool ceiling* only — see §4 on why legacy connection reach is not expressible. This is the arbitrary-command flag **off**; a legacy-equivalent policy for a flag-on deployment additionally names `hmc_run_command` in `tools`, which is #225's generator to emit. |
| A6 | Each of P1–P10 raises `AccessPolicyError` (or, for pydantic shape rules, an `AccessPolicyError` wrapping the validation failure). Every message names the source; a message names the policy and the grant index whenever the error has them — document-level errors (an unknown top-level key, and P11's absent/unreadable/unparseable file) have neither and are exempt. Each input carries exactly one violation. One case per rule, including: an unknown top-level key, a `targets` key of `"none"` in an *unselected* policy (exercising the shape tier's whole-document reach), an unknown grant key, a missing `connections`, an empty `connections`, `targets` given as a bare list, an empty `targets` table, a duplicate tool name in one array, an empty selector string, a grant with neither `effects` nor `tools`, an unknown tool name, a `targets` kind no granted tool declares, an uncovered required selector kind, and two identical grants. |
| A7 | P9 does not fire for optional selectors: a grant of `tools = ["hmc_power_off_lpar"]` with `targets = { lpar = ["db-01"] }` validates even though that tool also declares an optional `managed_system` selector. |
| A8 | P9 does not fire for tools with no selectors: a grant of `tools = ["hmc_list_systems"]` (a `console` tool) with `targets = "all-targets"` validates. P8 rejects the same grant with `targets = { managed_system = ["S1"] }`, and rejects `tools = ["hmc_run_command"]` with `targets = { console = ["c1"] }` — `hmc_run_command`'s `target_kind` is `console` but it declares no selector, so the constraint would be inert. |
| A9 | `AccessPolicy` and `Grant` reject attribute assignment with `FrozenInstanceError`; `AccessPolicy.tools` and `Grant.connections` are `frozenset`; a `targets` table compiles to a non-empty `MappingProxyType` that rejects item assignment; `hash(grant)` raises `TypeError` for a grant carrying `ALL_TARGETS` as well as one carrying a table, so hashability does not vary with the policy file. Mutating the source `dict` a caller passed to `compile_access_policy` after it returns does not change the compiled policy, proving the backing mapping is not retained. The module's public callables are exactly `resolve_access_policy_path`, `compile_access_policy`, and `load_access_policy`, so there is no mutator or reload entry point. |
| A10 | `load_access_policy` on a missing file, on a file with a TOML syntax error, and on an absent policy name each raise `AccessPolicyError`; the absent-name message lists the available names. Round-trip: a written temp file loads to the same `AccessPolicy` as `compile_access_policy` over the parsed document. |
| A11 | `grants = []` compiles to a policy that permits no tool at all. |
| A12 | A subprocess that imports `hmc_mcp.access_policy` and then calls `load_access_policy` finds `hmc_mcp.server` absent from `sys.modules` throughout — the module never imports `server`, deferred or otherwise, and is importable without the `app` extra. `api.__all__` is unchanged. |
| A13 | P9 binds explicit tools only: `tools = ["hmc_delete_lpar"]` with `targets = { managed_system = ["S1"] }` is rejected with a message naming `hmc_delete_lpar` and the uncovered `lpar` kind — that tool declares required `managed_system` and `lpar` selectors, so P8 is satisfied and P9 alone fires — while `effects = ["destructive"]` with the same `targets` table validates. This pins that P9 binds explicitly named tools only, so adding a tool to a granted *effect class* cannot make an unedited file stop loading. It narrows the index fragility recorded in §4; it does not remove it — P8, P10, and a `required` flag flipping on an explicitly named tool all remain. |
| A14 | Validation scope: a two-policy document whose *unselected* policy carries an unknown grant key fails the load (P1 binds every policy), while a two-policy document whose unselected policy names an unknown tool loads successfully when the other policy is selected (P7 binds the selected policy only). |
| A15 | `grants_for` returns whole grants, not merged dimensions: for a policy whose first grant is `effects = ["read"]` on connection `prod` with `all-targets` and whose second is `tools = ["hmc_delete_lpar"]` on connection `lab` with `targets = { lpar = ["scratch-01"] }`, `grants_for("hmc_delete_lpar")` returns only the second grant, and no `Grant` in the result carries `prod` or `ALL_TARGETS`. |
| A16 | `just verify` passes, including `scripts/smoke_mcp.py`. Not a pytest case. |
| A17 | No new runtime dependency is added to `pyproject.toml`. Not a pytest case. |

## 6. Files

| file | change |
|---|---|
| `src/hmc_mcp/access_policy.py` | new — models, validation P1–P11, compiled `AccessPolicy`/`Grant`, `load_access_policy` |
| `tests/unit/test_access_policy.py` | new — A1–A15 |
| `docs/adr/0036-server-access-policy-model.md` | new |
| `docs/workflow/specs/2026-08-18-server-access-policy-design.md` | new — this file |
| `docs/workflow/plans/2026-08-18-server-access-policy.md` | new |

No existing source file changes. The dispatch's file-scope hint listed `config.py` and
`common.py`; neither needs an edit — `config_dir()` is imported as it stands, and
`common.py` is read only to establish that `profile=None` is the default connection.
