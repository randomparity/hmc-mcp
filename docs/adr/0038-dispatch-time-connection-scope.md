# 0038 — Authorize connection scope at dispatch, on the resolved selector

## Status

Accepted (2026-08-19)

## Context

ADR 0035 gave every MCP tool a `ToolSecurity` record carrying, among other fields, the
public argument from which its HMC connection is chosen — `connection_argument`,
`"profile"` on all but the two local-only tools. ADR 0036 added `access_policy.py`, whose
compiled `Grant` names tools, connections, and targets and is evaluated conjunctively.
ADR 0037 enforced the first of those three dimensions by not registering a withheld tool,
and made `hmc_effective_permissions` label the other two as declared but not enforced.

So today a granted tool may be called with any `profile` value the caller likes.
`server_lpars.py` selects the connection inside the handler and begins work; `common.py`
`build_config` constructs configuration for whatever the caller named. Epic #218
requirement 4 and issue #222 close that gap: reauthorize the connection immediately
before handler execution, so a mutation fails closed *before* any REST or SSH operation.

One property of `build_config` decides most of this record. Its TOML branch is gated on
`if not explicit_host and not os.environ.get("HMC_HOST")`. When `HMC_HOST` is set — the
single-HMC shape the README documents under *Environment variables*, and the shape most
MCP deployments use — the whole profile-resolution block is skipped and the `profile`
argument is **silently discarded**. Verified directly in this checkout: with
`HMC_HOST=env-hmc.example`, `build_config(profile="never-configured-token")` returns the
environment host and raises nothing. ADR 0036 recorded the consequence and assigned it
here: "`connections` allowlists the `profile` argument token, not an HMC endpoint … #222
owns all of it."

The token space is many-to-one by a second route. ADR 0030's `nicknames` table maps a
friendly name to a profile key, resolved one level deep by `load_profile`, so several
distinct `profile` values reach one profile.

This record covers connection scope only. Exact target constraints (#223), structured
audit events (#224), and fail-closed startup with the legacy-policy generator (#225) are
not decided here.

## Decision

### The check lives inside the callable the registry holds

Each registration site wraps its handler before handing it to `mcp.tool(...)`. The
wrapper binds the call's arguments against the handler's own signature, reads the
connection selector `ToolSecurity.connection_argument` names, authorizes it, and only
then calls the handler. Nothing between the MCP dispatch and the first line of the
handler body can be skipped, because the authorization *is* the callable the registry
holds — there is no separate check to reach.

A `fastmcp` middleware on `on_call_tool` was the alternative and is rejected for the
reason ADR 0037 rejected it for the ceiling: a middleware is a check that must be
reached. It is also the seam #224 will want for audit events, and this record does not
foreclose it — a middleware observing calls is compatible with a wrapper deciding them.

`tool_registry.py` owns the wrapper and it cannot import `access_policy.py` — ADR 0036
fixed that direction — so the policy travels as a callable, exactly as ADR 0037's
`permits` predicate does:

```python
Authorize = Callable[[str, ToolSecurity, Mapping[str, Any]], None]
```

It is given the tool name, its authoritative classification, and the bound arguments; it
raises to deny and returns `None` to permit. Raising rather than returning a bool keeps
the denial message where the policy vocabulary lives, and makes "permitted" the value a
buggy authorizer cannot produce by falling off the end.

**Three registration sites, one contract, and none of them decides.**
`register_tools` (the domain collector), `register_permissions_tool`, and
`configure_arbitrary_command_tool` all take `authorize` and all pass it to the same
`tool_registry.authorized(...)` helper. The helper — not the site — returns the handler
unwrapped when there is no policy or when the tool declares no connection argument. This
is ADR 0037's "two sites, one contract" extended to the third, which matters most:
`configure_arbitrary_command_tool` registers `hmc_run_command`, the one
`arbitrary-command` tool.

The wrapper is applied to the callable that is *registered*. The module-level names —
`server_lpars.hmc_create_lpar` and its 128 siblings — are untouched, so a direct Python
caller and the CLI reach the unwrapped function. That is not an oversight; see *The
boundary this policy bounds* below.

### The comparison is on the resolved selector, not the literal token

This is the load-bearing choice, and both readings were live.

**Literal-token comparison** would test the caller's `profile` string against the grant's
`connections` set, with `None` matching the compiled `<default>`. It reads nothing,
costs nothing, and never touches `config.toml`. It is rejected because the token is not
the connection. Two failures follow from that, both fail-open:

- Under `HMC_HOST`, a grant naming `connections = ["lab"]` permits a call passing
  `profile="lab"`, which resolves to the environment HMC. The operator wrote a scope that
  has no referent; the control reports success while authorizing a connection it never
  checked.
- A grant naming a *nickname* permits every call passing that nickname, which resolves to
  whatever profile the nickname targets — possibly one the policy deliberately withholds.

**Resolved-selector comparison** is adopted. The caller's token is normalized to the
connection `build_config` will actually select, expressed in the policy's own vocabulary
— a profile key, or `None` for the environment/default connection — and *that* is
compared exactly against `Grant.connections`. Normalization is three rules, in order:

1. **`HMC_HOST` is set in the process environment → `None`.** `build_config` will
   discard the token, so no token can be evidence of reaching a named profile. The
   deployment has exactly one connection and it is the environment one, which is what
   `<default>` denotes.
2. **The token is absent (`None`) → `None`.** ADR 0036 fixed that omitting the argument
   is denoted by `<default>`, whose late binding through `HMC_PROFILE` and
   `default_profile` is an accepted, recorded property of that token. This record does
   not re-resolve it; see the rejected alternatives.
3. **Otherwise, resolve one level through the `nicknames` table** read from the
   platform-native `config.toml`, yielding the profile key. A token that is already a
   profile key, or that matches no nickname, passes through unchanged — `load_profile`
   resolves a nickname only when the name is not itself a profile key, and this mirrors
   it.

A `ConfigError` from reading that table denies. A token that is not `str | None` denies
without inspection.

The line between rules 2 and 3 is deliberate and is the one a reviewer should press on.
Rule 3 canonicalizes a selector the **caller supplied**; that is disambiguation, and
refusing it would let an alias launder reach past the policy. Rule 2 would have to
*substitute* a selector the caller did **not** supply, and the value it would substitute
is precisely what ADR 0036 defined `<default>` to denote. Canonicalize what was said;
never invent what was not.

Normalization reads only non-secret structure: whether `HMC_HOST` is set, and the
`nicknames` table via `config.list_nicknames`, which the module documents as never
resolving secrets. It reads no password, builds no client, and contacts no HMC.

### Under `HMC_HOST`, a profile-named policy denies everything

Rule 1's direct consequence, and the fail-closed answer to the discarded token. In an
`HMC_HOST` deployment a policy granting `connections = ["lab"]` denies every
connection-bearing tool, and a policy granting `["<default>"]` permits them all. Both are
correct: the server can reach exactly one HMC, so the only truthful statement the policy
can make about it is `<default>`, and a grant naming `lab` cannot be honoured because
nothing establishes that the environment HMC *is* `lab`.

The denial says so. It is the one place the deployment shape must reach the operator,
and the alternative — silently treating `lab` as satisfied — is the fail-open this record
exists to close.

### The connection dimension binds every tool that declares a connection argument

Issue #222's outcome names mutations. This record enforces the dimension on every
registered tool whose `ToolSecurity` declares a `connection_argument`, which is a strict
superset, for three reasons. `Grant.connections` is a property of the grant, not of an
effect class, so scoping it to mutations would make one policy key mean two different
things depending on which tool it reached — the declared-but-inert trap ADR 0036 and
ADR 0037 each wrote a residual about. A read against a withheld HMC is a disclosure, and
bounding reach is what the epic is for. And enforcing everything is *less* code than
enforcing some: an effect filter is a condition that would have to be written, tested,
and kept in step with the effect classes.

The two tools with `connection_argument = None` — `hmc_list_configured_hosts` and
`hmc_effective_permissions`, the local-only pair rule G10 pins — are unwrapped. Neither
opens an HMC connection, so there is no connection to scope; ADR 0036's residual that a
`connections = ["lab"]` grant still lets `hmc_list_configured_hosts` disclose the `prod`
inventory is answered here as *the connection dimension structurally cannot bound it*.
Withholding that disclosure is the tool dimension's job, and ADR 0037 made it
withholdable.

### Denials are stable, actionable, and disclose nothing new

`ConnectionScopeError` carries a message naming the tool, the policy, the connection the
request was evaluated as, and the remedy. It never names a host, a port, a user, a
credential, a resolved endpoint, or the contents of `config.toml`.

It also never enumerates the connections the policy *does* grant. ADR 0037 made
`hmc_effective_permissions` withholdable precisely so an operator could decline to
disclose the policy; a denial that listed the allowed connections would be that
disclosure through a channel no policy can withhold.

Naming the normalized connection is deliberate even under rule 1, where it reveals that
this deployment resolves its HMC from the environment. That is deployment shape, not a
secret — it names no host and confers no reach — and without it the denial is
unactionable in exactly the deployment where the denial is most surprising.

### Without a policy, nothing is authorized

`create_mcp(policy=None)` passes no authorizer and every handler registers unwrapped —
ADR 0037's default, unchanged. A policy's absence is not a denial until #225 makes
startup fail closed.

When a policy *is* selected, a registered tool that no grant covers is denied. That state
is reachable only through registry drift (`configure_arbitrary_command_tool(True, app)`
with its default `permits=None`), and denying is the fail-closed reading.

### The boundary this policy bounds

The server access policy bounds **the MCP server**, and nothing else. It does not bound
`hmc-mcp lpars delete ...` on the command line, and it does not bound a Python program
importing the `hmc_mcp.api` facade. ADR 0029 already places "MCP tools, CLI commands,
server and CLI composition modules" outside the supported reusable Python API contract;
this record adds the converse — the CLI and that facade reach `build_config` directly,
under the operator's own credentials, and no access policy applies to them.

This is structural rather than documented: the wrapper is applied to the registered
callable, so the exported function object is the unwrapped one. An operator who needs the
policy to bind a human at a shell is asking for something a server-side control cannot
give; the answer is HMC-side user roles, which is where it has always been.

### `hmc_effective_permissions` reports connections as enforced

`ENFORCED_DIMENSIONS` becomes `("tools", "connections")` and `DECLARED_ONLY_DIMENSIONS`
becomes `("targets",)`, which is what ADR 0037 said this entry would do.

## Consequences

- **Authorization and resolution are two reads of a mutable file.** The authorizer reads
  the `nicknames` table, and `build_config` re-reads `config.toml` moments later inside
  the handler; an edit between them authorizes a resolution that no longer happens. Not
  closed, and deliberately: the fix is to resolve once and hand the handler its
  `HMCConfig`, which changes 129 handler signatures for a race whose only exploiter is
  someone who can already rewrite the credential file — and who could therefore point a
  granted profile at an HMC of their choosing regardless. ADR 0036 already placed the two
  files at one trust level.
- **The `nicknames` table is read on every authorized call, with no cache.** Matching
  `build_config`, which re-reads `config.toml` per call. A cache would be a third
  reading of the same file that could disagree with the other two.
- **An `HMC_HOST` deployment can express exactly one connection.** Rule 1 collapses the
  token space, so `connections = ["<default>"]` is the only grant that permits anything
  and per-connection scoping is unavailable in that shape. That is the true state of such
  a deployment rather than a limitation this record introduces — it reaches one HMC — but
  it does mean an operator must either unset `HMC_HOST` and use profiles, or accept that
  the connection dimension grants all-or-nothing. The denial message says which.
- **Upgrading is a behaviour change for anyone already passing `--access-policy`.** A
  policy that loaded and constrained only registration now denies calls whose connection
  it does not name. That is the point, and #221 shipped one day ago, but a deployment
  authored against the declared-only semantics can start failing calls on upgrade. The
  denial names the connection and the policy, so the diagnosis is one error message long.
- **The wrapper binds arguments on every authorized call.** `inspect.signature` is
  resolved once at registration and the per-call cost is one `BoundArguments`. It is not
  free, and it sits on the hot path of every tool call. The alternative — reading the
  connection argument out of `kwargs` without binding — is wrong for any call that passes
  the selector positionally and for any handler whose selector has a default the caller
  omitted, which is all 127 of them.
- **`hmc_effective_permissions` understates connection enforcement under registry
  drift.** Both dimension tuples still derive from `ceiling_enforced`, so a drifted
  registry reports `enforced_dimensions = ()` while the wrapper is in fact denying
  connections. Issue #254 owns that encoding and proposes decoupling the two tuples; this
  record does not change it, because doing so would put the code in violation of #221's
  frozen spec requirement R16 and would settle #254's question inside an unrelated entry.
  The direction of the error is safe — the report claims *less* enforcement than exists —
  and #223 will meet the same encoding.
- **A tool reached with a non-string connection argument is denied rather than coerced.**
  Unreachable through MCP, where the schema types the parameter `str | None`; reachable
  by a direct caller of the wrapped object. Denying keeps the boundary from having a
  coercion rule at all.
- **`ConnectionScopeError` is a new public exception on the MCP error path**, surfaced to
  the client as a tool error. It is absent from `api.__all__`, per ADR 0029's placement of
  the server policy boundary outside the supported reusable Python API — the same
  placement `access_policy.py` and `server_permissions.py` already have.
- No new runtime dependency. `inspect`, `functools`, and `os` are stdlib and already
  imported across the package.

## Considered & rejected

- **Literal-token comparison.** The cheapest reading, and the one ADR 0036 predicted
  ("compiling the token to `None` makes literal comparison the path of least resistance
  at #222"). Rejected in the Decision: under `HMC_HOST` it authorizes a token the runtime
  discards, and it lets a granted nickname launder reach to a withheld profile. Its
  residual, had it been taken, is a control that reports success on a comparison it never
  performed — the exact fail-open shape #222 exists to close. What rejecting it costs is
  stated as a consequence: the authorizer now reads `config.toml`'s non-secret structure
  per call, and inherits a two-read race with `build_config`.
- **Resolve to the HMC endpoint — host and port — and compare identities.** The most
  honest possible reading of "the same connection", and the only one that survives two
  profiles pointing at one HMC. Rejected because comparing endpoints means resolving the
  *policy's* tokens to endpoints too, which means reading credentials inside the
  authorization path — the `config.toml` coupling ADR 0036 rejected when it declined to
  resolve `<default>` at load — and because a denial would then hold a resolved host it
  must be careful never to print. It also degenerates to nothing under `HMC_HOST`, where
  there is one endpoint and no profile identity, so it does not answer the case that
  motivated it. Residual of rejecting it: two tokens naming one HMC remain two
  connections to the policy, so withholding an HMC still means withholding every token
  that reaches it, and nothing checks that the operator did.
- **Resolve an omitted token through `HMC_PROFILE` and `default_profile` to a profile
  key** — normalization rule 2's alternative. It is arguably more honest: it names the
  connection actually used, and it would make a grant of `["prod"]` cover a caller who
  omits `profile` in a deployment whose default is `prod`, which is currently denied.
  Rejected because it re-decides an accepted record: ADR 0036 fixed `<default>` as the
  denotation of the omitted argument and recorded its late binding as a deliberate,
  operator-facing property with a stated rule ("do not grant `<default>` unless the
  deployment's default is a connection the policy means to allow"). Under the
  substitution, `<default>` would match nothing in any deployment that *has* a default
  profile — the token would be dead exactly where it is most used. Residual of rejecting
  it: a policy naming `["prod"]` denies a caller who omits the argument even when the
  deployment default is `prod`; the remedy is to grant `["prod", "<default>"]`, and the
  denial names `<default>` as the evaluated connection, so the remedy is legible from the
  error.
- **Enforce on mutating effects only, as issue #222's outcome literally reads.** Rejected
  in the Decision: it needs a filter this record would otherwise not write, and it leaves
  `Grant.connections` meaning one thing for `hmc_delete_lpar` and another for
  `hmc_list_lpars`. Recorded as a deliberate widening of the issue's stated outcome
  rather than absorbed silently.
- **A `fastmcp` middleware on `on_call_tool`.** One seam for all three registration
  sites, a natural home for #224's audit events, and it needs no signature preservation.
  Rejected because it is a check that must be reached, where a wrapper is the thing being
  called — ADR 0037's argument, applied to the dimension where fail-open is next-least
  acceptable after the ceiling. No bypass exists in this codebase today; the claim is
  that absence-by-construction should not depend on that staying true. The cost is
  acknowledged: #224 gets an observation seam that is not the decision seam.
- **Authorize inside `common.build_config`.** The narrowest possible waist — every path
  to an HMC goes through it, so nothing could be missed. Rejected because `build_config`
  is on the CLI and library paths too, so it would extend the server policy over the
  boundary this record and ADR 0029 draw, and because it has no access to the tool name
  or its `ToolSecurity`, so the grant it must evaluate conjunctively is not in scope
  there.
- **Pass only the connection token to the authorizer**, rather than the tool name, its
  `ToolSecurity`, and the bound arguments. Narrower, and it would hide the argument
  mapping from a callback that today reads one key of it. Rejected because the wrapper
  must bind the arguments regardless to read that key correctly, so the mapping already
  exists; because a grant is evaluated conjunctively and the authorizer therefore needs
  the tool name to select grants at all; and because #223 reads target selectors from the
  same mapping, so the narrower signature would be widened one entry later.
- **Cache the resolved `nicknames` table for the process lifetime**, since the policy is
  immutable for it. Rejected because the policy's immutability is a property of the
  policy, not of `config.toml`, which `build_config` re-reads on every call. A cached
  authorization decision that disagrees with the resolution it authorized is worse than
  a file read.
- **Enumerate the granted connections in the denial message.** Strictly more actionable
  for an operator debugging a policy. Rejected because it turns every denial into a
  disclosure of the policy's connection dimension, through a path no policy can withhold,
  one entry after ADR 0037 made that disclosure withholdable on purpose.
- **Deny by returning a structured result rather than raising.** It would let #224 record
  a denial without exception handling. Rejected as #224's decision to make: an exception
  is what every other failure on this path already is, and a tool that returns "denied" as
  a success payload is a shape an agent will read as data.
