# Dispatch-time HMC connection-scope authorization

Issue: [#222](https://github.com/randomparity/hmc-mcp/issues/222) — part of epic #218.
Decision record: [ADR 0038](../../adr/0038-dispatch-time-connection-scope.md).
Builds on [ADR 0035](../../adr/0035-enforceable-tool-security-metadata.md) (tool security
metadata), [ADR 0036](../../adr/0036-server-access-policy-model.md) (the policy model),
and [ADR 0037](../../adr/0037-composition-time-capability-ceiling.md) (the capability
ceiling). The boundary statement cites
[ADR 0029](../../adr/0029-supported-reusable-python-api-contract.md); the profile-routing
correction restores [ADR 0008](../../adr/0008-rest-tool-profile-routing.md).

## Goal

An MCP call that selects an HMC connection through a tool argument is reauthorized
against the startup-selected access policy immediately before the handler runs, and fails
closed — with a stable, actionable, non-secret error and without constructing an HMC
client — when the connection it actually selects is not granted.

## Scope

In scope: the dispatch-boundary wrapper and its three registration sites; normalization of
the caller-supplied connection token to the connection `common.build_config` will select;
whole-grant evaluation of that connection; the denial error; the two handlers that declare
a connection argument and ignore it, plus the guardrail that keeps a third from being
written; the effective-permissions label; and the documented statement that the CLI and
the supported Python API sit outside this boundary.

Out of scope, with owners: exact target-constraint matching at call time (#223);
structured redacted audit events (#224), including any record that a denial happened and
any visibility into token probing; fail-closed startup when no policy is selected, and the
legacy-equivalent policy generator (#225); the `declared_only_dimensions` encoding under
registry drift (#254).

## Requirements

Each requirement is numbered and testable. R-prefixed identifiers are cited by the plan.

**R1 — One helper, three sites, no site decides.** `tool_registry.authorized(name,
security, handler, authorize)` returns `handler` unchanged when `authorize is None` or
`security.connection_argument is None`, and otherwise a wrapper. Every callable passed to
`mcp.tool(...)` by `tool_module()`'s `register_tools`, by
`server_permissions.register_permissions_tool`, and by
`server_command.configure_arbitrary_command_tool` is the return value of that helper.

**R2 — The authorizer is a callable, not a policy object.** The parameter type is
`Authorize = Callable[[str, ToolSecurity, Mapping[str, Any]], None]`: tool name,
authoritative classification, bound arguments; returns `None` to permit and raises to
deny. `tool_registry.py` imports nothing from `access_policy.py` or
`connection_scope.py`.

**R3 — Registration is signature-transparent.** For every tool registered by
`server.create_mcp(policy)`, and for `hmc_run_command` after
`configure_arbitrary_command_tool(True, app, ...)`, the registered tool's `name`,
`description`, and `parameters` schema are equal to the same tool's registered with no
authorizer.

**R4 — The selector comes from metadata only.** The wrapper reads the argument named by
`ToolSecurity.connection_argument` and nothing else. The two tools whose
`connection_argument` is `None` — `hmc_list_configured_hosts` and
`hmc_effective_permissions` — register unwrapped.

**R5 — Arguments are bound, not read from `kwargs`.** The wrapper binds the call against
`inspect.signature(handler)` and applies defaults, so a selector passed positionally, or
omitted and defaulted, is read correctly. The signature is resolved once at registration.
A call `bind` rejects raises `TypeError` before the authorizer and therefore before the
handler.

**R6 — Normalization rule 0: a non-string token denies.** A `connection_argument` value
that is not `str | None` normalizes to `connection_scope.UNRESOLVED` without being
inspected or coerced.

**R7 — Normalization rule 1: `HMC_HOST` collapses the token space.** When `HMC_HOST` is
set and non-empty in the process environment, `connection_scope.selected_connection(token)`
returns `None` for every `token`, because `build_config` gates its TOML branch on that
value being truthy and discards the argument.

**R8 — Normalization rule 2: a falsy token is the default connection.**
`selected_connection(None)` and `selected_connection("")` both return `None`, mirroring
`load_profile`'s `name = profile or os.environ.get("HMC_PROFILE")`. `HMC_PROFILE` and
`default_profile` are not consulted: ADR 0036 fixed `<default>` as the denotation of the
omitted argument.

**R9 — Normalization rule 3: profiles first, then nicknames, one level.** For any other
token, `selected_connection` reads the `profiles` and `nicknames` tables from the
platform-native `config.toml` in a single read and returns: `token` when `token` names a
profile; `nicknames[token]` when it does not but names a nickname whose target names a
profile. A profile key therefore wins over a same-named nickname, mirroring
`config.load_profile`'s `if name not in profiles:` gate.

**R10 — Normalization fails closed on anything else.** A token that names neither a
profile nor a nickname, and a nickname dangling on a missing profile, normalize to
`connection_scope.UNRESOLVED` — a value `access_policy` forbids in a compiled grant, so
it always denies, through the *same* message as a resolvable-but-withheld token. A
configuration that cannot be read at all raises `ConnectionScopeError` with its own fixed
sentence. `config.list_profiles_and_nicknames` raises `ConfigError` for every failure it
can meet — an unreadable file, a non-UTF-8 one, an unparseable one, and a malformed
`profiles` or `nicknames` table — so no `OSError` or `AttributeError` carrying the config
path escapes to the caller.

**R11 — Evaluation is one predicate per grant.** A call on tool `T` with normalized
connection `C` is permitted iff some single grant in `policy.grants_for(T)` holds `C` in
its `connections`. It is written as an early-return loop over grants rather than a
dimension-wise expression, so #223 adds its target condition *inside* the loop body and a
grant-crossing union — the misreading ADR 0036's combination rule exists to prevent —
cannot be written by accident. A registered tool no grant covers is denied.

**R12 — A denied call performs no outbound operation.** `ConnectionScopeError` is raised
before the handler body runs, so no `HMCClient` is constructed, no HTTP transport is
opened, and no SSH command is issued. Tests assert on the client and transport
constructors, not only on the exception.

**R13 — There is one denial message, and it is a closed template.** It is the fixed
skeleton below with exactly three substituted values — the tool name, `policy.name`, and
the connection **as the caller named it**, `repr`-rendered (`None` and `""` rendered
`<default>`) — plus the one `HMC_HOST` clause, which interpolates the declared selector's
name rather than hardcoding `profile`. Nothing else is interpolated; in particular no
`ConfigError` text, filesystem path, host, port, user, credential, resolved endpoint,
normalized profile key, or enumeration of the connections the policy grants.

A token that names no configured connection is denied by **this same message**: a second,
distinguishable message would let a caller enumerate `config.toml`'s profile and nickname
keys one probe at a time, through a channel no policy can withhold. The test asserts
string equality against the rendered template, and asserts that a known nickname and an
unknown name produce denials that differ only in the echoed token.

**R14 — No policy means no authorization.** `server.create_mcp()` with no policy supplies
no authorizer, registers 129 tools, and every handler is registered unwrapped. Behaviour
is what ADR 0037 shipped.

**R15 — Applications stay independent.** Two `create_mcp` calls with different policies
authorize independently; a call with no policy after a restrictive one is unaffected.

**R16 — The module-level handler is unwrapped.** `server_lpars.hmc_create_lpar` and its
siblings are the same function objects as before this change, so the CLI and the ADR 0029
Python API reach an unauthorized path by construction.

**R17 — Every connection-bearing registered tool is actually wrapped.** With a policy
selected, for every tool in the composed application — read from `local_provider` *after*
`configure_arbitrary_command_tool` has run — whose `ToolSecurity.connection_argument` is
non-`None`, the registered callable carries a `__wrapped__` attribute. This is what makes
"the check cannot be skipped" a checked property rather than a discipline over three
keyword arguments.

**R18 — A declared connection argument routes the connection.** `hmc_set_lpar_boot_order`
and `hmc_clear_lpar_boot_order` pass their `profile` argument to `client_from_env`, as
every other connection-bearing handler already does. A static guardrail over the parsed
source of the `server_*` modules asserts, for every handler whose `ToolSecurity` declares
a connection argument, that every `build_config` / `client_from_env` / `_ssh_with_client`
call in its body receives that argument, and that none of them receives a `host` keyword —
which would make `build_config` skip profile resolution exactly as `HMC_HOST` does.

**R19 — Inspection reports connections as enforced.**
`server_permissions.ENFORCED_DIMENSIONS == ("tools", "connections")` and
`DECLARED_ONLY_DIMENSIONS == ("targets",)`. The `ceiling_enforced` gate on both tuples is
unchanged; #254 owns it.

**R20 — The boundary is documented.** `README.md` states that the access policy bounds
the MCP server only, that `hmc-mcp` CLI commands and the supported reusable Python API
(ADR 0029) run outside it, and that HMC-side user roles are the control that binds them.

## Design

### `config.list_profiles_and_nicknames`

R9 needs both tables from one read. `config.py` already has the precedent —
`list_profiles_with_default` exists so that a caller needing two facts does not parse the
file twice — so this is a sibling of it:

```python
def list_profiles_and_nicknames(
    config_path: Path | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Return (profile_names, nicknames) from one TOML read. Never resolves secrets."""
```

Two separate reads would let the halves of one decision disagree, which is the failure
mode R9 exists to prevent.

### `connection_scope.py`

A new module, imported by `server.py` and by nothing in `tool_registry.py`.

```python
class ConnectionScopeError(Exception):
    """An MCP call selected an HMC connection the access policy does not grant."""

def selected_connection(token: str | None) -> str | None:
    """The connection ``build_config`` will select, in the policy's vocabulary."""

def connection_authorizer(policy: AccessPolicy) -> Authorize:
    """An authorizer that denies any call whose selected connection *policy* withholds."""
```

`selected_connection` is R6–R10 in order: non-string, `HMC_HOST`, falsy token, then the
profiles/nicknames read. It reads `os.environ` and `config.list_profiles_and_nicknames()`;
it reads no secret, builds no client, and contacts no HMC. It signals a fail-closed
normalization by raising `ConnectionScopeError` itself, so the authorizer has one exit
shape.

`connection_authorizer` closes over the frozen policy and returns a function of
`(name, security, arguments)`. It returns immediately when
`security.connection_argument is None` — a redundant guard, since `authorized` already
declines to wrap such a tool, kept because an authorizer must be safe to call on any tool.

`DEFAULT_CONNECTION_TOKEN` from `access_policy` renders `None`, so the denial and
`hmc_effective_permissions` speak one vocabulary.

### `tool_registry.authorized`

```python
def authorized(name, security, handler, authorize):
    if authorize is None or security.connection_argument is None:
        return handler
    signature = inspect.signature(handler)

    @functools.wraps(handler)
    def guarded(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        authorize(name, security, bound.arguments)
        return handler(*args, **kwargs)

    return guarded
```

`functools.wraps` sets `__wrapped__`, which `inspect.signature` follows, and FastMCP's
schema generation was verified in this checkout to produce an identical `parameters`
schema and description through the wrapper. R3 pins that as a test rather than a claim,
and R17 uses the same `__wrapped__` attribute as its evidence.

Every handler in the package is synchronous (`hmc_effective_permissions` is the only
coroutine, and it declares no connection argument, so `authorized` returns it unwrapped).
The wrapper is therefore synchronous, with no async branch to test or maintain. Adding a
coroutine handler with a connection argument would need this revisited; R4's assertion on
the `connection_argument is None` set is what makes that visible.

### Composition

`server.create_mcp(policy=None)` derives both gates from the one policy:

```python
permits = None if policy is None else policy.permits_tool
authorize = None if policy is None else connection_authorizer(policy)
```

and passes both to every registration site. `_serve_application` passes the same
`authorize` to `configure_arbitrary_command_tool`, alongside the `permits` it already
passes. R17 is asserted against the result of that second call, not the first.

### Errors

One skeleton, assembled in `connection_scope.py` from three substituted values and one
clause:

```
<tool> is not permitted on connection '<token>' by access policy '<policy>'.
<clause>Grant that connection in a policy grant that already names <tool>, or call
<tool> with a connection the policy grants.
```

`<clause>` is `""`, or one of exactly two fixed strings:

- rule 1 — `"HMC_HOST is set, so the 'profile' argument is ignored and the call was
  evaluated as the '<default>' connection. "`
- rule 3, when the token resolved through a nickname — `"The token resolves through the
  configured nickname table to a connection this policy does not grant. "`

A normalization failure under R10 uses a second closed template with one substitution:

```
<tool> cannot be authorized: the configured HMC connections could not be read, or the
requested connection does not name one.
```

The originating `ConfigError`, when there is one, is chained as `__cause__` for the
server-side traceback and never interpolated, so the config path does not reach the
client. Verified in this checkout under `fastmcp-slim` 3.4.7: the raised exception's
`str()` reaches an in-process client and `__cause__` does not.

The token is rendered as the caller supplied it, never as the normalized value; ADR 0038
records why, and R13 makes the closed template the mechanism rather than a prohibition
list.

## Testing

- `tests/unit/test_connection_scope.py` — normalization R6–R10 against a real
  `config.toml` written under a monkeypatched `HOME`, including the two cases a
  nicknames-only implementation gets wrong: a nickname that shadows a profile key, and a
  nickname dangling on a missing profile. Five corrupt-configuration shapes and an
  unreadable file, each asserting the fixed sentence and the absence of the path.
  Whole-grant evaluation (R11), the denial template asserted by string equality, and the
  known-nickname/unknown-name indistinguishability (R13).
- `tests/app/test_connection_authorization.py` — the three registration sites (R1),
  schema transparency (R3), argument binding including positional and defaulted selectors
  and a `bind` failure (R5), no-policy behaviour (R14), independence (R15), unwrapped
  module-level handlers (R16), and the fail-closed proof (R12): a denied REST call and a
  denied SSH-passthrough call with `HMCClient.__init__`, `httpx.AsyncClient.__init__`, and
  every module-level rebinding of `build_config` / `client_from_env` / `run_hmc_cli` /
  `run_hmc_command` patched to raise — patching only the defining module proves nothing,
  since `server_vios`, `server_command`, and `_app` each hold their own reference. A
  companion test shows a permitted call trips those same wires.
- R17 is asserted on the application `server._serve_application` returns, not one the test
  composes — that is the only path a deployment takes, and it is what pins the
  arbitrary-command registration site. It covers both directions: with a policy every
  connection-bearing tool carries `__wrapped__` and the two local-only tools do not;
  without one, nothing does. A served denial of `hmc_run_command` is asserted alongside.
- `tests/app/test_tool_security.py` — R18's static guardrail, beside the existing G-rules.
- `tests/app/test_profile_routing.py` — the two corrected boot-order handlers reach the
  profile they are given, matching the existing per-tool routing tests.
- `tests/app/test_capability_ceiling.py` — R19.
- A live stdio exercise, run once against the packaged server rather than kept in the
  suite (`just smoke` already covers the stdio handshake): a real `hmc-mcp serve
  --access-policy` subprocess under a throwaway `HOME`, denying a withheld connection,
  admitting a granted one, evaluating an omitted argument as `<default>`, and reporting
  `enforced_dimensions == ["tools", "connections"]`. Its result is recorded on the PR.

## Threat model

The untrusted party is the MCP client and the agent driving it. It controls every tool
argument, including `profile`. It does not control the process environment, `config.toml`,
or `access-policy.toml`, all of which are the operator's and sit at one trust level
(ADR 0036).

- **Selecting a withheld HMC through a tool argument** — the threat this entry closes.
  Denied before any outbound operation (R11, R12).
- **Laundering reach through a nickname** — a granted alias that targets a withheld
  profile. Closed by R9; the alias resolves before comparison.
- **Shadowing a profile with a nickname** — a nickname keyed the same as a profile the
  policy grants. Closed by R9's ordering, which mirrors `load_profile`.
- **Relying on a discarded token** — passing a granted profile name in an `HMC_HOST`
  deployment. Closed by R7: the token cannot be evidence, so the call is evaluated as
  `<default>`.
- **Reaching an unauthorized connection through a handler that ignores its selector** —
  closed for the two handlers that do it by R18, and kept closed by R18's guardrail.
- **Learning `config.toml`'s nickname targets from denials** — closed by R13: the message
  renders the caller's own token, so a denial emits no value the caller did not supply.
- **Learning the deployment's secrets or paths from denials** — closed by R13's closed
  template and R10's fixed text for a normalization failure.
- **Bypassing the check by dispatch path** — the authorization is inside the registered
  callable, so there is no dispatch that reaches the handler without it; R17 checks that
  every connection-bearing tool got one.
- **Not closed: the permit/deny bit is an oracle.** An agent holding one granted tool can
  probe candidate tokens and recover that tool's connection dimension one bit per call,
  and one probe reveals whether `<default>` is granted; a probe that succeeds also
  executes. This is inherent to having an enforcement point, and no message wording
  changes it. Making probing *visible* is #224's.
- **Not addressed here:** which resources a permitted connection may act on (#223); any
  record that a denial happened (#224); a deployment that selects no policy at all (#225).

## Open questions

None. The two questions ADR 0036 delegated to this entry — literal-token versus
resolved-selector comparison, and whether the connection dimension binds reads — are
decided in ADR 0038 with their residuals recorded.
