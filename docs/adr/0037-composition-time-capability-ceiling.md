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
fields say what those dimensions currently mean: `enforced_dimensions` is `("tools",)`
and `declared_only_dimensions` is `("connections", "targets")`. #222 and #223 move a
string from the second tuple to the first. Prose in a description would have said the
same thing to a human and nothing to a client.

**The arbitrary-command flag and the ceiling compose conjunctively**, as ADR 0036
recorded. `configure_arbitrary_command_tool(enabled, mcp, permits=None)` registers
`hmc_run_command` only when `enabled` and the ceiling admits it, taking the same
`permits` callable as the domain registration. ADR 0036's rule that `arbitrary-command`
cannot be granted by effect class is untouched: it is enforced in the policy validator,
and this intersection reads only the compiled result.

## Consequences

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
  operator's home directory and so names the account. It carries no credential: the
  policy document's four grant keys are `effects`, `tools`, `connections`, and `targets`,
  and `access_policy.py` parses it with `extra="forbid"`, so there is no field a secret
  could be written into. The already-shipping `hmc_list_configured_hosts` returns each
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
- **Deny at dispatch instead of filtering at registration** — keep every tool registered
  and refuse a denied call. It gives the agent an actionable error and a place to hang
  #224's audit event, and it is one code path instead of one per registration site.
  Rejected because a registered tool is advertised: its name, schema, and description
  still reach the client, an agent still plans with it, and the ceiling becomes a runtime
  check that must be reached rather than a property of the composed application. #222
  adds a dispatch layer for the dimensions that can only be evaluated per call;
  the ceiling is not one of them.
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
