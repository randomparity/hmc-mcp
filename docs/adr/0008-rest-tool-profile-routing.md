# ADR 0008: Per-Call Profile Routing for REST-Backed MCP Tools

## Status

Accepted; module ownership superseded by ADR 0013

## Context

Issue #126 (part of epic #123) extends the multi-profile TOML loader from
ADR 0006 to the MCP server layer. Before this change every REST-backed MCP
tool calls `client_from_env()` without a profile argument, so all concurrent
or consecutive calls share the same connection configuration derived from the
environment. Agents cannot independently route two calls to different HMC
profiles without an out-of-band state mutation — which the epic explicitly
forbids ("no global selection is mutated").

`client_from_env(profile=...)` already accepts a profile name since ADR 0006.
The missing piece is:

1. Surfacing `profile` as an optional MCP tool parameter on every REST tool.
2. Threading the caller-supplied value (or `None`) to every `client_from_env()`
   call inside that tool's body.
3. Fixing the three direct `HMCConfig()` constructions used on REST paths as
   CLI-fallback branches (`server_tools/power.py:156`, `server_tools/provision.py:227`)
   so the selected profile's credentials are used there too.

## Decision

### Public API change

Add `profile: str | None = None` as the *last* keyword parameter on every
`@mcp.tool` function whose body opens a REST connection. Callers that omit it
get the existing resolution order (`HMC_PROFILE` → `default_profile`) unchanged.

### Propagation rule

Every `client_from_env()` call inside a tool body is changed to
`client_from_env(profile)`. No global state is written; each call resolves
its own credentials.

### `_app.py` helpers

`with_client`, `_resolve_system_uuid`, `_resolve_lpar_uuid`, `_resolve_vios_uuid`
are sync helpers used by exactly one level above (tool bodies). They are replaced
by direct inline patterns in the tool body, or the helpers are extended to accept
`profile`. Specifically:

- `with_client` only covered the single-client common case; it is removed and
  callers are inlined (the callers were already simple one-liners).
- `_run` and the UUID resolvers (`_resolve_system_uuid`, `_resolve_lpar_uuid`,
  `_resolve_vios_uuid`) are internal to `_app.py` and used by multiple tools;
  they already accept `hmc` as a client parameter, so no signature change is
  needed there — callers simply pass the profile-aware client.

### `_ssh_with_client` exclusion

`_ssh_with_client` in `_app.py` is excluded from this change; SSH routing
is scoped to #127.

### Direct `HMCConfig()` fallback paths

The CLI-fallback branches in `server_tools/power.py` (create-LPAR-via-CLI) and
`server_tools/provision.py` (provision-LPAR fallback) construct `HMCConfig()` directly.
These are changed to `client_from_env(profile).config` — i.e., they derive
the config from the same profile the tool selected, so the fallback path uses the
same credentials as the REST path it fell back from.

**Simpler alternative:** pass the already-open `HMCConfig` from the caller scope.
In both cases the `config` object is already available from the outer
`async with client_from_env(profile) as hmc:` context — use `hmc.config` instead
of constructing a new one.

### `ssh.run_hmc_command` exclusion

`ssh.run_hmc_command` (the SSH-only path in `server_tools/cli.py`) is excluded; it
is owned by #127.

## Consequences

- `profile=None` defaults preserve current behavior for all callers; no
  breaking change to the tool API.
- Adding the parameter is backward-compatible with MCP clients that do not
  supply it.
- No shared mutable state: every call resolves its own `HMCConfig` from its
  own `profile` argument; the test for concurrent independence confirms this.
- `with_client` helper is removed: the single-line pattern it replaced is now
  spelled `async with client_from_env(profile) as hmc:` at the call site.
  This makes profile propagation explicit and removes an invisible seam.

### Trust model

The `profile` parameter is accepted verbatim from any MCP caller and resolved
against the operator's local TOML config file.  This is intentional: the MCP
server is designed for **single-user local deployment** over stdio transport,
where the caller identity is the same as the config-file owner.  All configured
profiles are equally accessible to any caller because there is no multi-tenant
trust boundary between the server process and its MCP clients.

Operators who expose the MCP server to untrusted callers (e.g., as a shared
network gateway) must ensure that every profile in their TOML file grants only
the minimum required privilege, or restrict the exposed parameter via a
proxy/gateway layer, since the server itself performs no per-caller profile
authorization check.
