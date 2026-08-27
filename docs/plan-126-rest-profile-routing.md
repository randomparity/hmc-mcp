# Implementation Plan: Issue #126 — REST Tool Profile Routing

**Branch:** feat/rest-profile-routing-126  
**Base:** main  
**Guardrails:** `just verify`  
**Resume facts:** branch=feat/rest-profile-routing-126, base=main, step=5 Build with TDD

## Overview

Thread a `profile: str | None = None` parameter through every REST-backed MCP
tool and update all `client_from_env()` call sites to pass it. Fix two direct
`HMCConfig()` constructions to use `hmc.config` from the already-open client.
Remove the `with_client` helper that cannot propagate profile. Add exhaustive
registry, sequential, and concurrent routing tests.

## Files changed

### Core helpers (`_app.py`)

- Remove `with_client` (and its import in server files that use it)
- No changes to `_run`, `_resolve_*_uuid`, or `_ssh_with_client`

### Server modules (add `profile` param + thread to `client_from_env`)

Each module: add `profile: str | None = None` to every `@mcp.tool` function,
change every `client_from_env()` → `client_from_env(profile)`.

1. `server_tools/power.py` — also fix `HMCConfig()` → `hmc.config` in CLI fallback
2. `server_tools/vios.py`
3. `server_tools/metrics.py`
4. `server_tools/system.py`
5. `server_tools/composite.py`
6. `server_tools/lpm.py`
7. `server_tools/network.py`
8. `server_tools/storage.py`
9. `server_tools/templates.py`
10. `server_tools/updates.py`
11. `server_tools/provision.py` — also fix `HMCConfig()` → `hmc.config` in CLI fallback
12. `server_tools/profiles.py` (read: check if REST)
13. `server_tools/users.py` (read: check if REST)
14. `server_tools/cli.py` — check: SSH-only, no change

### `_app.py` — profile-aware UUID resolution helpers

The UUID resolvers (`_resolve_system_uuid`, `_resolve_lpar_uuid`,
`_resolve_vios_uuid`) take an `hmc` client; the caller must pass the
profile-aware client. The helpers themselves don't need changes since
they receive an already-open client.

The SSH name-resolution helpers (`_resolve_system_name`, `_resolve_lpar_name`)
call `client_from_env()` internally for REST lookups. These are on the SSH
path and scoped to #127.

### Tests

New file: `tests/app/test_profile_routing.py`

- `test_every_rest_tool_has_profile_param` — registry-driven
- `test_sequential_profile_routing` — two calls, two profiles
- `test_concurrent_profile_routing` — asyncio concurrent tasks

## Step-by-step

### T-1: Failing registry test

Write `test_every_rest_tool_has_profile_param` first. It will fail until all
tools are updated.

### T-2: Update `_app.py`

Remove `with_client`. Update any internal call sites.

### T-3: Update `server_tools/power.py`

Add `profile` to all tools; fix `HMCConfig()` fallback; update `client_from_env` calls.

### T-4: Update remaining server modules

`server_tools/vios.py`, `server_tools/metrics.py`, `server_tools/system.py`, `server_tools/composite.py`,
`server_tools/lpm.py`, `server_tools/network.py`, `server_tools/storage.py`, `server_tools/templates.py`,
`server_tools/updates.py`, `server_tools/provision.py`, `server_tools/profiles.py`, `server_tools/users.py`.

### T-5: Add routing isolation tests

`test_sequential_profile_routing` and `test_concurrent_profile_routing`.

### T-6: Run `just verify`

All green. Commit.

## Risk notes

- `profile=None` is the default everywhere — no behavior change for existing callers.
- The change to `HMCConfig()` → `hmc.config` in CLI fallback paths preserves
  credentials from the selected profile in a path that was previously always
  using env vars only.
- Removing `with_client` removes an invisible seam and makes profile propagation
  explicit. Any remaining `with_client` usage will be a compile-time
  `AttributeError` caught by `just smoke`.
