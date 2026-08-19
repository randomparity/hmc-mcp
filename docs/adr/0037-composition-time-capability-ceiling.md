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
`permissions.describe`, `target_kind="none"`) in `server.TOOL_SECURITY`, so a policy may
name it, an `effects = ["read"]` grant reaches it, and a `tools`-only grant that omits it
withholds it. To keep that from being a silent debuggability trap, `serve` prints one
stderr line when the selected policy withholds it — the operator learns it at the moment
they chose the policy, and the warning lives in the CLI rather than in `create_mcp`,
which is a library function called at import and once per test.

**Inspection reports the application it is registered on, read live.** The tool is built
by a small factory closing over the `FastMCP` instance and the policy, and its body reads
that application's registry at call time. It does not recompute the permitted set from
`TOOL_SECURITY` and the policy. The two would agree at composition and diverge the moment
anything else changes the registry — `configure_arbitrary_command_tool` already does,
after `create_mcp` has returned — and "the registry" is what #221 is asked to report.
Reading it makes agreement structural rather than a duplicated derivation two tests must
keep in step.

**Inspection renders grants one at a time, and labels what is not enforced.** ADR 0036
fixed that a grant is evaluated conjunctively and grants combine disjunctively, so a
union of connections across grants would misstate the policy. Each grant is reported with
its own tools, connections (with `None` rendered back as `"<default>"`), and targets. Two
fields say what those dimensions currently mean: with a policy selected,
`enforced_dimensions` is `("tools",)` and `declared_only_dimensions` is
`("connections", "targets")`; with none selected both are empty, because a server with no
ceiling enforces no dimension and declares none. #222 and #223 move a string from the
second tuple to the first. The tuples are computed rather than constant precisely so the
permissive default cannot report an enforcement it is not performing — a client keying off
a constant `("tools",)` would draw the fail-open conclusion in the fail-open case. Prose
in a description would have said the same thing to a human and nothing to a client.

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
  every deployment until #225. `hmc_effective_permissions` reporting a null policy name
  and empty `enforced_dimensions` is the only in-band way to observe it. A second stderr
  warning at `serve` was considered and not added: it would fire on every existing
  deployment, for a state #225 converts into a startup failure outright.
- **A policy that grants `hmc_run_command` while `--enable-arbitrary-command` is unset
  produces the same unexplained absence** the inspection-tool warning exists to prevent,
  and gets no warning. The conjunction is deliberate and the flag is the outer gate, so
  the absence is correct; only the diagnosis is missing, and inspection reports it.
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
  surface — but it is a new one, and a policy that withholds the inspection tool removes
  it.
- **The registered tool count is 129, not 128**, in the unfiltered default composition.
  `hmc_effective_permissions` also joins the `read` effect class, so an existing
  `effects = ["read"]` grant silently gains it on upgrade — the index-drift consequence
  ADR 0036 already recorded, now with its first instance.
- **`main_stdio` / `main_http` no longer serve `server.mcp`.** They compose a fresh
  application per call. The module-level `mcp` remains the unfiltered composition that
  tests and `scripts/` import; it is no longer mutated by the serve path.
- **Inspection reports a registry, so it inherits whatever the registry does.** If a
  future change disables rather than removes a tool, inspection reports what the provider
  reports, without a second opinion. That is intended.
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
  the same seam #222 and #223 need anyway. Rejected because it is a check that must be
  *reached*: any provider, transport, or direct-invocation path that does not run the
  middleware exposes the full registry, so the ceiling fails open on a bug of omission,
  whereas a tool that was never registered cannot be reached by construction. The
  acknowledged cost of rejecting it: #222 and #223 add a dispatch layer regardless, so
  one policy ends up enforced by two mechanisms with different failure shapes, and only
  the dispatch one is visible to #224's audit. That is the price of making the highest-risk
  dimension structural.
- **Register everything, then remove the denied tools from the provider**, reusing
  `configure_arbitrary_command_tool`'s existing `remove_tool` shape. Rejected because
  composition would construct a registry that exceeds the ceiling and then repair it, so
  "the registry never exceeds the ceiling" becomes a post-condition someone must maintain
  rather than something construction cannot violate. Any early return between the two
  loops ships the unfiltered application.
- **Pass the permitted set as a `frozenset[str]` rather than a predicate.** Plain data,
  obviously pure, and `AccessPolicy.tools` is already exactly that set. Rejected because
  ADR 0036 named `permits_tool` the ceiling interface precisely so that callers ask the
  question rather than depend on its representation; threading the set would make
  `tool_registry` couple to the ceiling being a set of names.
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
- **Report the policy name only, not its path.** The cheapest form of "policy source",
  and it removes the one disclosure this change adds that names the operator's account.
  Rejected because a name does not answer the question an operator asks inspection: *which
  file was read* — `resolve_access_policy_path` is platform-dependent, and a deployment
  that believes it edited the policy in effect is the failure mode this field exists to
  catch. A policy that considers the path sensitive withholds the tool, which is the
  control this record already gives it.
- **Report a flat union of connections and targets across grants.** A shorter, flatter
  output. Rejected because ADR 0036 fixed grants as conjunctive alternatives, so the
  union of the connection dimension across grants describes reach no grant confers — the
  exact misreading that record wrote its combination rule to prevent.
- **Add `--access-policy-file PATH` beside `--access-policy NAME`.** Useful in a
  container with no home directory, and it would make CLI-level tests cheaper. Rejected
  as unrequested surface: `load_access_policy` already takes `path`, no criterion asks for
  it, and #225 revisits startup selection anyway.
- **Fail closed when no policy is selected**, which is the stronger default. Rejected
  because it is #225's deliverable including its generator, and shipping it here would
  break every current deployment on upgrade with no supported migration. Recorded in the
  Decision because the question is #221's to answer, not to leave unnamed.
