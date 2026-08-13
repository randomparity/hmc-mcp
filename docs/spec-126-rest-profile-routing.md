# Spec: Route REST-Backed MCP Tools by Per-Call Profile (Issue #126)

## Problem

Every REST-backed MCP tool calls `client_from_env()` without a profile
argument, binding all calls to the same environment-derived HMC connection.
Agents cannot independently direct concurrent or sequential calls to
different named HMC profiles without mutating global state.

## Solution

### 1. Add `profile: str | None = None` to every REST MCP tool

Every `@mcp.tool` function that opens a REST connection receives one new
trailing keyword parameter:

```python
def hmc_some_tool(..., profile: str | None = None) -> ...:
```

When `None`, the existing resolution order applies:
`HMC_PROFILE` env var → `default_profile` in TOML → env-vars-only fallback.

### 2. Thread `profile` to every `client_from_env()` call

Every `client_from_env()` call inside a tool body becomes
`client_from_env(profile)`. This is the only change to ~50 existing call
sites; no other behavior changes.

### 3. Replace direct `HMCConfig()` fallback constructions

Two tools bypass `client_from_env` in their CLI-fallback branches:

- `server_power.py` `hmc_create_lpar`: CLI fallback after HTTP 406
- `server_provision.py` `hmc_provision_lpar`: CLI fallback after HTTP 406

Both construct `HMCConfig()` directly. They are changed to derive the config
from the already-open client: `hmc.config`. Since the outer async-with block
already holds `client_from_env(profile) as hmc`, the fallback path reuses
the same `HMCConfig` object — no new `load_profile` call needed.

### 4. Remove `with_client` helper from `_app.py`

`with_client` wraps `run_with_client(client_from_env, fn)` but cannot
thread `profile`. It is removed and the six call sites in the codebase are
inlined as `run_with_client(lambda: client_from_env(profile), fn)` — or more
idiomatically as a direct `async with client_from_env(profile) as hmc:` inside
`_run(async_fn)`. The existing `run_with_client` in `common.py` and `_run`
helper in `_app.py` are retained; only `with_client` is removed.

### 5. Exhaustive registry test

A new test asserts that every tool registered on the live `mcp` instance
that is not SSH-only accepts a `profile` key in its JSON schema properties.
The SSH-only exclusion list is maintained explicitly in the test module.

### 6. Sequential and concurrent isolation tests

Two focused tests:

- **Sequential:** call a REST tool twice with different `profile` values; each
  call reaches the correct TOML-sourced host.
- **Concurrent:** run two `asyncio.Task`s calling a REST tool with different
  profiles simultaneously; assert both reach the correct host with no
  cross-call leakage.

## Scope boundary

- SSH tools (`_ssh_with_client` path, `run_hmc_cli`) → #127
- `hmc_list_configured_hosts` → #128
- CLI `--profile` → done in #125
- TOML loader → done in #124

## Acceptance criteria (from epic #123)

1. Every REST-connecting MCP tool's schema includes `profile` (no exceptions
   in the REST set).
2. Consecutive calls with different profiles each reach the selected profile.
3. Concurrent calls with different profiles do not share state.
4. Omitting `profile` follows `HMC_PROFILE` → `default_profile`.
5. `just verify` passes.
