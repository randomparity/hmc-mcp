# 0037 — Enforce the capability ceiling at composition, and inspect it as a tool

## Status

Accepted (2026-08-18)

## Context

ADR 0035 gave every MCP tool a `ToolSecurity` record. ADR 0036 added `access_policy.py`,
which loads a named policy and compiles it to a frozen `AccessPolicy` whose derived
ceiling answers `permits_tool(name) -> bool`. ADR 0036's top residual is that nothing
reads it: "a policy file that exists and validates *looks* like a control." Epic #218
requirement 1 and issue #221 close that gap for the first of the three enforcement
dimensions.

Today `server.create_mcp()` registers every domain unconditionally, and
`server_command.configure_arbitrary_command_tool` is the only registration a caller can
influence. So the server has no capability ceiling, and an operator has no way to ask
what the running server can actually do.

This record covers registration-time filtering and effective-permission inspection.
Connection scope (#222), target constraints (#223), audit events (#224), and fail-closed
startup with the legacy-policy generator (#225) are not decided here.

## Decision

**The ceiling is enforced by not registering the tool.** `create_mcp(policy=None)`
threads the policy's ceiling question into each domain's `register_tools`, which skips a
definition the ceiling does not admit. A withheld tool never appears in the application's
registry, so it is absent from `tools/list` and unreachable by name.

The ceiling travels as `permits: Callable[[str], bool] | None`, defaulting to `None` for
"no ceiling". `tool_registry.py` cannot import `access_policy.py` — `access_policy`
imports `tool_registry`, and ADR 0036 fixed that direction deliberately — so the
parameter is the question, not the policy object. `server.create_mcp` passes
`policy.permits_tool`; every other caller passes nothing.

**`serve --access-policy NAME` selects the policy at startup.** The CLI loads and
compiles it through `load_access_policy(name, server.TOOL_SECURITY)` from the
platform-native `access-policy.toml`, and hands the compiled object to `main_stdio` /
`main_http`, which compose a fresh filtered application rather than serving the
module-level `server.mcp`. An `AccessPolicyError` from an explicit `--access-policy` is a
startup failure with the module's own message.

**Without `--access-policy`, no ceiling is applied and every tool registers — today's
behaviour, unchanged.** This is the question #221 must answer without building #225.
The alternative available to this entry is fail-closed-by-default, which is #225's
deliverable verbatim (requirement 9, "startup refuses when no policy is selected") and
which cannot land here: #225 also owns the legacy-equivalent policy generator, and a
fail-closed default without a generator means every existing deployment stops starting
on upgrade with no supported way to restore it. So the default stays permissive and
#225 replaces it. Until then a policy is opt-in, and its absence is not a denial.

**The inspection tool is subject to the ceiling like every other tool.** A tool the
policy cannot withhold is a hole in the ceiling, and this one discloses the policy.
`hmc_effective_permissions` therefore carries an ordinary `ToolSecurity` record (`read`,
`permissions.describe`, `target_kind="none"`) in `server.TOOL_SECURITY`. ADR 0036's format
is a pure additive allowlist with no deny form, so only a `tools`-only grant that omits it
withholds it; an `effects = ["read"]` grant reaches it and *cannot* exclude it without
abandoning effect-class grants entirely. To keep that from being a silent debuggability
trap, the serve path prints one stderr line when the selected policy withholds it. That
warning, and the three others this record adds, live in `main_stdio` / `main_http` rather
than in `create_mcp`: composition is a library function called at import and once per test,
while the entry points run only when a server actually starts and are the single point
where the served registry, the policy, and the arbitrary-command flag all exist at once.

> **Amended by #253** (2026-08-21). **"Only a `tools`-only grant that omits it withholds
> it" states a false necessary condition; the implementation was never wrong.** A grant
> reaches the union of its effect classes and its named tools (`_resolve_tools` in
> `access_policy.py`), so an effects-only policy granting `mutate` and `destructive` but
> neither `read` nor the tool name compiles cleanly and withholds
> `hmc_effective_permissions` — reproduced against `feat/capability-ceiling-221`,
> withheld-inspection warning and all. The corrected rule: any policy that neither grants
> the `read` effect class nor names the tool in a grant's `tools` withholds it; a policy
> granting `read` reaches it and cannot exclude it.

**Both registration sites take the same `permits` gate.** The inspection tool needs the
application object, which does not exist at import, so it cannot be one of the collector's
decorator-captured definitions and is registered by a factory outside the `TOOL_MODULES`
loop. That second site would otherwise be a hand-applied check — the post-condition this
record rejects `remove_tool` for. So `register_permissions_tool` takes `permits` and
applies it itself, exactly as `tool_module()`'s `register_tools` does: two sites, one
contract, and no caller that decides for either of them.

**Inspection reports the application it is registered on, read live.** The factory closes
over the `FastMCP` instance and the policy, and the handler reads that application's local
provider at call time rather than recomputing the permitted set from `TOOL_SECURITY` and
the policy. The provider, not `FastMCP.list_tools()`: the provider is what
`configure_arbitrary_command_tool` mutates, and the server-level call runs the `tools/list`
middleware chain, so reading it from inside a `tools/call` would emit a phantom `tools/list`
into whatever #224 hangs there and make the answer session-dependent. The two would agree at composition and diverge the moment anything else
changes the registry — `configure_arbitrary_command_tool` already does, after `create_mcp`
has returned — and "the registry" is what #221 is asked to report.

**Inspection renders grants one at a time, and labels what is not enforced.** ADR 0036
fixed that a grant is evaluated conjunctively and grants combine disjunctively, so a
union of connections across grants would misstate the policy. Each grant is reported with
its own tools, connections (with `None` rendered back as `"<default>"`), and targets. Two
fields say what those dimensions currently mean: with a policy selected,
`enforced_dimensions` is `("tools",)` and `declared_only_dimensions` is
`("connections", "targets")`; otherwise both are empty. They derive from
`ceiling_enforced`, which is *checked* rather than inferred — a policy must be selected and
every name in the read registry must satisfy `permits_tool` — so no output can pair a claim
of tool enforcement with a registry that has drifted past its ceiling, and the permissive
default cannot claim an enforcement it is not performing. #222 and #223 move a string from
the second tuple to the first.

> **Amended by [ADR 0047](0047-per-dimension-enforcement-labels.md)** (2026-08-19).
> **The derivation described in the paragraph above was wrong, and this record was wrong to
> fix it.** `ceiling_enforced` re-checks the *tool* dimension only, so deriving all three
> labels from it makes one fact about tools decide three answers. The failure was
> reproduced, not inferred: a registry drifted past its ceiling reported
> `enforced_dimensions == []` **and** `declared_only_dimensions == []` — every dimension
> dropped from a report still enumerating the connections and targets its grants name —
> while a live call on an ungranted connection was denied by the very policy the report
> named. The invariant this paragraph claims is real and is kept; what is corrected is that
> it is an invariant about `tools` and was applied to all three. Each dimension is now
> labelled from evidence about that dimension, and the two tuples partition all three
> whenever a policy is selected. Nothing else in this record changes and it is not
> superseded: the ceiling decision, the `permits` contract at both registration sites, the
> live-registry reading, the warning placement, and the disclosure analysis all still
> govern.

**The arbitrary-command flag and the ceiling compose conjunctively**, as ADR 0036
recorded. `configure_arbitrary_command_tool(enabled, mcp, permits=None)` registers
`hmc_run_command` only when `enabled` and the ceiling admits it, taking the same
`permits` callable as the domain registration. ADR 0036's rule that `arbitrary-command`
cannot be granted by effect class is untouched: it is enforced in the policy validator,
and this intersection reads only the compiled result.

## Consequences

- **A server started without `--access-policy` applies no ceiling and says nothing about
  it.** The realistic path after this entry ships is that an operator authors
  `access-policy.toml`, restarts, and gets zero enforcement because the selection flag was
  omitted: the file loads nowhere, validates nothing, and constrains nothing. ADR 0036's
  top residual — a policy file that validates *looks* like a control — now has a second
  silent step between authoring and enforcement, and that is the default configuration of
  every deployment until #225. Two things narrow it. `serve` prints one stderr line when
  `resolve_access_policy_path()` names a file that exists and `--access-policy` was not
  passed — the authored-but-unselected state exactly, and a state no deployment predating
  this entry can be in, since none has the file. That check must never fail the start:
  `resolve_access_policy_path()` reaches `Path.home()`, which raises `RuntimeError` under a
  uid with no passwd entry and no `HOME` — the container case `access_policy.py:418-425`
  already guards — so `serve` catches `RuntimeError`/`OSError` around it and simply skips
  the warning. A diagnostic that can abort a start nobody asked to constrain is worse than
  no diagnostic. And `hmc_effective_permissions` reports a
  null policy name with empty `enforced_dimensions`. An *unconditional* warning was
  rejected: it would fire on every existing deployment for a state #225 converts into a
  startup failure outright.
- **The arbitrary-command conjunction is warned in one direction only.** Passing
  `--enable-arbitrary-command` to a policy whose ceiling withholds `hmc_run_command` is an
  explicit request answered with silence, so `serve` prints one stderr line for it — the
  same reasoning that buys the inspection-tool warning. The mirror case, a policy granting
  the tool while the flag is unset, gets none: the flag is the outer gate and its absence
  is the operator's own most recent decision, not a surprise. Inspection reports both.
- **The ceiling is the only dimension enforced.** A policy naming
  `connections = ["lab"]` still lets a granted tool be called with `profile="prod"`, and
  a `targets` table constrains nothing at call time. That is why inspection labels both
  as declared. Between this entry and #222/#223 the server is *more* likely to be
  believed than before, which is the sequencing risk of enforcing one dimension first.
- **A withheld tool is invisible, not denied.** An agent gets no error explaining why a
  tool it expected is missing, and there is no server-side record of the attempt (#224
  owns audit events). Invisibility is the stronger property — the tool cannot be reached
  by name at all — and it is what "expose only tools permitted by the policy" asks for.
- **`hmc_effective_permissions` discloses the policy's contents to the MCP client**,
  including the absolute path of the policy file, which on most platforms sits under the
  operator's home directory and so names the account. It carries no credential, argued
  over values rather than keys: every value it echoes is an operator-authored identifier —
  the policy name, tool names drawn from the compiled-in index, connection tokens, and
  target selector strings — and no value is read from `config.toml`, from the environment,
  or from the HMC. `access_policy.py` parses the document with `extra="forbid"` over four
  grant keys, so a would-be secret has no field to travel in; an operator who writes one
  into a policy name or a selector string publishes it, which is the same exposure as
  writing it into an LPAR name. The already-shipping `hmc_list_configured_hosts` returns each
  profile's host and user to the same client, so this is not the widest disclosure on the
  surface — but that comparison assumes an unfiltered surface, and this change is what
  makes `hmc_list_configured_hosts` withholdable. Under a `tools`-only policy that withholds
  it, inspection becomes the widest configuration disclosure on the surface; under an
  `effects = ["read"]` policy it cannot be withheld at all. Only a `tools`-only policy can
  withhold it.

  > **Amended by #253** (2026-08-21). **"Only a `tools`-only policy can withhold it" is
  > false for the same reason; see the amendment on the Decision above.** Any policy that
  > neither grants the `read` effect class nor names the tool in a grant's `tools`
  > withholds it.

  > **Amended by #470** (2026-08-26). **"No value is read from `config.toml`, from the
  > environment, or from the HMC" no longer holds.** `power_ownership_guards` reads the
  > effective `authorize_power_operations` (ADR 0092 §4) per granted connection through
  > `common.build_config`, so each entry carries one boolean from the resolved config and
  > one provenance label decided by probing the environment for
  > `HMC_AUTHORIZE_POWER_OPERATIONS`. Three further facts about the served process become
  > readable with it: whether that variable is exported and whether its spelling is exact
  > or a case variant, from `source`; whether each granted connection resolves in
  > `config.toml`, from `source: unresolved`; and whether `HMC_HOST` is set, inferable
  > because the guard rows then collapse to the default connection while `declared_grants`
  > in the same response still names the profiles. That guard fails **open**, so its
  > effective value has to be readable from the process that would act on it; nothing else
  > answers for an env-var-only deployment. The bound the sentence was defending is now
  > carried by the code instead: no `config.toml` *string* is echoed. Connection names come
  > from the policy this tool already discloses, an entry that cannot resolve reports a
  > closed classification rather than the `ConfigError` message — which would name the
  > file's whole `profiles` and `nicknames` inventory, the disclosure ADR 0038 refuses —
  > and the reason goes to the server's log instead. Nothing from the HMC is read.
- **The registered tool count is 129, not 128**, in the unfiltered default composition, and
  `hmc_effective_permissions` joins the `read` effect class — the first instance of the
  index drift ADR 0036 recorded, so an existing `effects = ["read"]` grant gains it on
  upgrade.
- **`main_stdio` / `main_http` no longer serve `server.mcp`.** They compose a fresh
  application per call. The module-level `mcp` remains the unfiltered composition that
  tests and `scripts/` import; it is no longer mutated by the serve path.
- **Inspection reports a registry, so it inherits whatever the registry does.** If a
  future change disables rather than removes a tool, inspection reports what the provider
  reports, without a second opinion. `enforced_dimensions` does carry a second opinion: it
  re-checks every reported name against the policy, so a registration site added after
  `create_mcp` returns that skips `permits` degrades the label to "not enforced" rather than
  leaving it falsely true. `configure_arbitrary_command_tool` is the one that exists, and it
  takes `permits`.

  > **Amended by [ADR 0047](0047-per-dimension-enforcement-labels.md)** (2026-08-19). The
  > second opinion degrades the `tools` label, not the whole tuple — it re-checks names
  > against the ceiling, which says nothing about connection or target scope. Those two are
  > now re-checked against their own evidence, the dispatch wrapper on each registered
  > callable, so a site that skips `permits` and a site that skips `authorize` degrade
  > different labels instead of the same one.
- **`hmc_effective_permissions` is the second tool with no connection argument**, after
  `hmc_list_configured_hosts`. Rule G10 in `tests/app/test_tool_security.py` — "only the
  local config tool opens no HMC connection" — becomes an allowance of two, and both
  entries are local-only by construction.
- No new dependency. The module is absent from `api.__all__`, per ADR 0029's placement of
  the server policy boundary outside the supported reusable Python API.

## Considered & rejected

- **Do nothing: land inspection only, and leave filtering to #222's dispatch layer.** The
  null option. Rejected because #221's stated outcome is that fresh applications expose
  only permitted tools, and an inspection tool reporting an unfiltered registry against a
  loaded policy would report the gap accurately and change nothing about it.
- **Enforce the ceiling in a `fastmcp` middleware** holding the policy — the strongest
  form of "deny at dispatch". The pinned `fastmcp-slim[server]==3.4.7` exposes both
  `on_list_tools` and `on_call_tool`, so one middleware could hide a withheld tool from
  `tools/list` *and* refuse a call by name: one code path instead of a `permits`
  parameter, an actionable error for the agent, a place to hang #224's audit event, and
  the same seam #222 and #223 need anyway. Rejected because a middleware is a check that
  must be *reached* and an unregistered tool cannot be reached at all: the ceiling is the
  dimension where fail-open is least acceptable, and it is the one dimension that needs no
  per-call information to decide. No bypass path is claimed in this codebase today — it
  composes one `FastMCP` application with no mount or proxy — the argument is that
  absence-by-construction does not depend on that staying true. The acknowledged cost:
  #222 and #223 add a dispatch layer regardless, so one policy ends up enforced by two
  mechanisms with different failure shapes, and only the dispatch one is visible to #224's
  audit.
- **Register everything, then remove the denied tools from the provider**, reusing
  `configure_arbitrary_command_tool`'s existing `remove_tool` shape. Rejected because
  composition would construct a registry that exceeds the ceiling and then repair it, so
  "the registry never exceeds the ceiling" becomes a post-condition someone must maintain
  rather than something construction cannot violate. Any early return between the two
  loops ships the unfiltered application.
- **Pass the permitted set as a `frozenset[str]` rather than a predicate.** Rejected on the
  merits, not on authority: one callable serves the domain loop, the inspection factory,
  and `configure_arbitrary_command_tool` alike, and `tool_registry` never couples to the
  ceiling being a set of names.
- **Give the inspection tool an exemption from the ceiling**, so an operator can always
  ask what the server can do. Rejected because it is a tool no policy can withhold, in a
  file whose whole purpose is withholding tools, and its output is a description of the
  policy — the one exemption worth attacking. The stderr warning at `serve` gives the
  operator the same information at the moment the choice is made.
- **Recompute the permitted set inside the inspection tool from `TOOL_SECURITY` and the
  policy** rather than reading the live registry. Simpler to test in isolation and it
  needs no closure over the application. Rejected because it is a second derivation of
  the same fact: it would already disagree with the registry whenever
  `configure_arbitrary_command_tool` has run, and any future registration path would have
  to remember to update it.
- **Report the policy name only, not its path.** Rejected because a name does not answer
  the question inspection is asked — *which file was read* — and `resolve_access_policy_path`
  is platform-dependent, so a deployment editing a policy that is not the one in effect is
  the failure this field exists to catch.
- **Report a flat union of connections and targets across grants.** A shorter, flatter
  output. Rejected because ADR 0036 fixed grants as conjunctive alternatives, so the
  union of the connection dimension across grants describes reach no grant confers — the
  exact misreading that record wrote its combination rule to prevent.
- **Add `--access-policy-file PATH` beside `--access-policy NAME`.** Rejected as
  unrequested surface; `load_access_policy` already takes `path`, and #225 revisits startup
  selection anyway.
- **Fail closed when no policy is selected.** The stronger default, and #225's deliverable
  together with the generator that would make it survivable; see the Decision.
- **Make `hmc_effective_permissions` ungrantable by effect class**, mirroring ADR 0036's
  rule that `arbitrary-command` must be named in `tools`. It would make disclosure opt-in
  per policy and erase the index-drift consequence above, at the same one-string cost
  ADR 0036 priced. Rejected on two grounds. It amends an accepted record's resolution rule
  to add a second exception, where ADR 0036's first exists to satisfy an explicit epic
  requirement and no requirement asks for this one. And it inverts the trap rather than
  removing it: an operator writing `effects = ["read"]` would get a server whose
  permissions they cannot inspect, silently, unless they knew to name a tool that did not
  exist before the upgrade — strictly worse than a read-only self-description they can
  withhold deliberately.
- **Render the policy path home-relative (`~/Library/...`) instead of absolute**, answering
  *which file was read* without naming the account. Rejected because
  `AccessPolicyError` already reports the absolute path at startup, and two renderings of
  one path means an operator comparing a warning against inspection output has to
  translate between them.
