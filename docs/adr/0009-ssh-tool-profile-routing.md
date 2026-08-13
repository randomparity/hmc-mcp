# ADR 0009: Per-Call Profile Routing for SSH-Backed MCP Tools

## Status

Accepted

## Context

Issue #127 (part of epic #123) extends per-call profile routing from
REST-backed tools (ADR 0008) to SSH-backed tools. Before this change the
SSH layer always derives credentials from the environment defaults:

- `_ssh_with_client` in `_app.py` constructs `HMCConfig(_env_file=None)` with
  no profile argument.
- `run_hmc_cli` in `ssh.py` constructs `HMCConfig()` unconditionally.
- The two REST-first/SSH-fallback resolvers (`_resolve_system_name`,
  `_resolve_lpar_name`) call `client_from_env()` with no profile, so UUID
  resolution for SSH tools also uses the wrong client when a caller-supplied
  profile differs from the env default.

ADR 0008 explicitly excluded `_ssh_with_client` and `run_hmc_cli`, deferring
them to this issue.

## Decision

### Public API change

Add `profile: str | None = None` as the *last* keyword parameter on every
`@mcp.tool` function whose body calls `_ssh_with_client` or `run_hmc_cli`
directly. The three tool bodies in `server_vios.py` and `server_system.py`
that call `run_hmc_cli` directly are updated to pass the profile through.

### `_ssh_with_client` signature

Add `profile: str | None = None` as a keyword-only parameter. Propagate it to:

1. The `HMCConfig` construction — replaced by `client_from_env(profile).config`
   so the same profile resolution path used by REST tools applies.
2. Both `_resolve_system_name` and `_resolve_lpar_name` calls inside `_go`.

### `_resolve_system_name` / `_resolve_lpar_name`

Add `profile: str | None = None` parameter. Replace the bare `client_from_env()`
call with `client_from_env(profile)` so the REST leg uses the correct profile.
The SSH fallback already receives the caller-supplied `config`; no change there.

### `run_hmc_cli` signature

Add `config: HMCConfig | None = None` as an optional parameter. When supplied,
pass it to `run_hmc_command`; when `None`, construct `HMCConfig()` from the
environment as before. Tool callers that want profile routing construct the
config via `client_from_env(profile).config` before the `asyncio.run` boundary
and pass it in.

**Alternative considered:** add `profile: str | None = None` directly to
`run_hmc_cli`. Rejected: `run_hmc_cli` is a thin async helper called inside an
`asyncio.run` loop that was already entered by the tool. `load_profile` is a
synchronous I/O call; invoking it inside the already-running event loop would
require a thread-pool escape. Accepting a pre-built config avoids that complexity
and keeps the call-site pattern symmetric with `_ssh_with_client`.

### No cross-call state

`profile` is threaded as a call argument. No module-level variable is written.
Each tool invocation resolves its own `HMCConfig`.

### Trust model (unchanged from ADR 0008)

The `profile` parameter is accepted verbatim from any MCP caller and resolved
against the operator's local TOML config file. The MCP server is a
single-user local deployment over stdio transport; the caller identity is the
same as the config-file owner. No per-caller authorization check is added.

## Consequences

- `profile=None` preserves current env-default behavior for all existing callers.
- UUID resolution for SSH tools now uses the same credentials as the SSH command.
- The `TODO(#127)` comments in `_app.py` at the two resolver call sites are
  removed.
- `run_hmc_cli` grows an optional `config` parameter; callers that omit it
  continue to work unchanged.
- `_ssh_with_client` grows a `profile` keyword parameter; existing call sites
  that do not pass it continue to work unchanged.

## Considered & rejected

**Thread `profile` through `run_hmc_cli` instead of `config`.**
`run_hmc_cli` is called from an already-running event loop, so a synchronous
`load_profile` call inside it would block the event loop or require a
thread-pool escape. Accepting a pre-built `HMCConfig` is simpler and consistent
with how other helpers in the codebase accept config objects.

**Add a module-level `_current_profile` context var.**
Rejected because it creates hidden shared state. Two concurrent tool calls
would race on the context variable if they used the same thread, and the
invariant "no global selection is mutated" (epic #123) would be violated.
