# Dispatch-time HMC connection-scope authorization

Issue: [#222](https://github.com/randomparity/hmc-mcp/issues/222) — part of epic #218.
Decision record: [ADR 0038](../../adr/0038-dispatch-time-connection-scope.md).
Builds on [ADR 0035](../../adr/0035-enforceable-tool-security-metadata.md) (tool security
metadata), [ADR 0036](../../adr/0036-server-access-policy-model.md) (the policy model),
and [ADR 0037](../../adr/0037-composition-time-capability-ceiling.md) (the capability
ceiling). The boundary statement cites
[ADR 0029](../../adr/0029-supported-reusable-python-api-contract.md).

## Goal

An MCP call that selects an HMC connection through a tool argument is reauthorized
against the startup-selected access policy immediately before the handler runs, and fails
closed — with a stable, actionable, non-secret error and without constructing an HMC
client — when the connection it actually selects is not granted.

## Scope

In scope: the dispatch-boundary wrapper and its three registration sites; normalization of
the caller-supplied connection token to the connection `common.build_config` will select;
whole-grant evaluation of that connection; the denial error; the effective-permissions
label; and the documented statement that the CLI and the supported Python API sit outside
this boundary.

Out of scope, with owners: exact target-constraint matching at call time (#223);
structured redacted audit events (#224); fail-closed startup when no policy is selected,
and the legacy-equivalent policy generator (#225); the `declared_only_dimensions`
encoding under registry drift (#254).

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

**R3 — Registration is signature-transparent.** For every tool in
`server.TOOL_SECURITY`, the registered tool's `name`, `description`, and `parameters`
schema are equal whether or not an authorizer was supplied.

**R4 — The selector comes from metadata only.** The wrapper reads the argument named by
`ToolSecurity.connection_argument` and nothing else. The two tools whose
`connection_argument` is `None` — `hmc_list_configured_hosts` and
`hmc_effective_permissions` — register unwrapped.

**R5 — Arguments are bound, not read from `kwargs`.** The wrapper binds the call against
`inspect.signature(handler)` and applies defaults, so a selector passed positionally, or
omitted and defaulted, is read correctly. The signature is resolved once at registration.

**R6 — Normalization: `HMC_HOST` collapses the token space.** When `HMC_HOST` is set and
non-empty in the process environment, `connection_scope.selected_connection(token)`
returns `None` for every `token`, because `build_config` discards the argument in that
shape.

**R7 — Normalization: an absent token is the default connection.**
`selected_connection(None)` returns `None`. `HMC_PROFILE` and `default_profile` are not
consulted: ADR 0036 fixed `<default>` as the denotation of the omitted argument.

**R8 — Normalization: a nickname resolves one level to its profile key.** Otherwise
`selected_connection(token)` reads the `nicknames` table from the platform-native
`config.toml` via `config.list_nicknames()` and returns `nicknames[token]` when `token`
is not itself a profile key and is a nickname; otherwise `token` unchanged. A profile key
wins over a same-named nickname, mirroring `config.load_profile`.

**R9 — Normalization fails closed.** A token that is not `str | None` denies without
being inspected. A `ConfigError` raised while reading the nicknames table denies with a
fixed message that does not embed the `ConfigError` text or the config path.

**R10 — Evaluation is whole-grant.** A call on tool `T` with normalized connection `C` is
permitted iff `any(C in grant.connections for grant in policy.grants_for(T))`. No
grant-crossing union of dimensions. A registered tool no grant covers is denied.

**R11 — A denied call performs no outbound operation.** `ConnectionScopeError` is raised
before the handler body runs, so no `HMCClient` is constructed, no HTTP transport is
opened, and no SSH command is issued. Tests assert on the client and transport
constructors, not only on the exception.

**R12 — Denials are stable, actionable, and disclose nothing new.** The message names the
tool, the policy name, and the connection the request was evaluated as (`None` rendered
`<default>`); adds one explanatory clause when `HMC_HOST` forced the normalization; and
ends with a remedy. It contains no host, port, user, password, resolved endpoint,
filesystem path, or enumeration of the connections the policy grants.

**R13 — No policy means no authorization.** `server.create_mcp()` with no policy supplies
no authorizer, registers 129 tools, and every handler is registered unwrapped. Behaviour
is byte-for-byte what ADR 0037 shipped.

**R14 — Applications stay independent.** Two `create_mcp` calls with different policies
authorize independently; a call with no policy after a restrictive one is unaffected.

**R15 — The module-level handler is unwrapped.** `server_lpars.hmc_create_lpar` and its
siblings are the same function objects as before this change, so the CLI and the ADR 0029
Python API reach an unauthorized path by construction.

**R16 — Inspection reports connections as enforced.**
`server_permissions.ENFORCED_DIMENSIONS == ("tools", "connections")` and
`DECLARED_ONLY_DIMENSIONS == ("targets",)`. The `ceiling_enforced` gate on both tuples is
unchanged; #254 owns it.

**R17 — The boundary is documented.** `README.md` states that the access policy bounds
the MCP server only, that `hmc-mcp` CLI commands and the supported reusable Python API
(ADR 0029) run outside it, and that HMC-side user roles are the control that binds them.

## Design

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

`selected_connection` is the whole of ADR 0038's normalization, R6–R9, in that order:
`HMC_HOST` first, then an absent token, then nickname resolution. It reads `os.environ`
and `config.list_nicknames()`; it reads no secret, builds no client, and contacts no HMC.

`connection_authorizer` closes over the frozen policy and returns a function of
`(name, security, arguments)`. It returns immediately when
`security.connection_argument is None` — a redundant guard, since `authorized` already
declines to wrap such a tool, kept because an authorizer must be safe to call on any
tool.

`DEFAULT_CONNECTION_TOKEN` from `access_policy` is reused for rendering `None`, so the
denial and `hmc_effective_permissions` speak one vocabulary.

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
schema and description through the wrapper. R3 pins that as a test rather than a claim.

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
passes.

### Errors

One message skeleton, assembled in `connection_scope.py`:

```
<tool> is not permitted on connection '<connection>' by access policy '<policy>'.
[<clause>]Grant that connection in a policy grant that already names <tool>, or call it
with a connection the policy grants.
```

`<clause>` is present only under R6:

```
HMC_HOST is set, so the 'profile' argument is ignored and every call resolves to the
environment connection.
```

The connection rendered is the **normalized** one, not the caller's token. That is the
value the decision was made on, so it is the only rendering that makes the error
actionable; under R8 it names a profile key, which is an operator-authored identifier of
the same class as the policy's own connection tokens — ADR 0037 already argued that class
is not a secret, and no host, user, or credential is derived from it.

A malformed `nicknames` table denies with:

```
<tool> cannot be authorized: the HMC connection configuration could not be read.
```

The originating `ConfigError` is chained as `__cause__` for the server-side traceback and
never interpolated, so the config path does not reach the client.

### Testing

- `tests/unit/test_connection_scope.py` — normalization (R6–R9) against a real
  `config.toml` written under a monkeypatched `HOME`, whole-grant evaluation (R10), and
  the denial message's content and prohibitions (R12).
- `tests/app/test_connection_authorization.py` — the three registration sites (R1),
  schema transparency (R3), argument binding including positional and defaulted selectors
  (R5), no-policy behaviour (R13), independence (R14), unwrapped module-level handlers
  (R15), and the fail-closed proof (R11): a denied call with `HMCClient.__init__`,
  `httpx.AsyncClient`, and the SSH entry point patched to raise, asserting the
  `ConnectionScopeError` surfaces and none of them was called.
- `tests/app/test_capability_ceiling.py` gains the R16 assertions.
- A live stdio server exercise: compose a policy granting one connection, run the
  packaged server over stdio, call a granted tool with a withheld connection, and confirm
  the denial reaches the client as a tool error.

## Threat model

The untrusted party is the MCP client and the agent driving it. It controls every tool
argument, including `profile`. It does not control the process environment, `config.toml`,
or `access-policy.toml`, all of which are the operator's and sit at one trust level
(ADR 0036).

- **Selecting a withheld HMC through a tool argument** — the threat this entry closes.
  Denied before any outbound operation (R10, R11).
- **Laundering reach through a nickname** — a granted alias that targets a withheld
  profile. Closed by R8; the alias resolves before comparison.
- **Relying on a discarded token** — passing a granted profile name in an `HMC_HOST`
  deployment. Closed by R6: the token cannot be evidence, so the call is evaluated as
  `<default>`.
- **Learning the policy from denials** — closed by R12: no enumeration of granted
  connections, so a denial is not a read of the dimension `hmc_effective_permissions` can
  withhold.
- **Learning the deployment's secrets from denials** — closed by R12's prohibitions and by
  R9's fixed text for a config read failure.
- **Bypassing the check by dispatch path** — the authorization is inside the registered
  callable, so there is no dispatch that reaches the handler without it.
- **Not addressed here:** which resources a permitted connection may act on (#223); any
  record that a denial happened (#224); a deployment that selects no policy at all (#225).

## Open questions

None. The two questions ADR 0036 delegated to this entry — literal-token versus
resolved-selector comparison, and whether the connection dimension binds reads — are
decided in ADR 0038 with their residuals recorded.
