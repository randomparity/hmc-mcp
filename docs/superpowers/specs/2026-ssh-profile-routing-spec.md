# SSH Tool Profile Routing — Design Spec

**Issue:** #127  
**ADR:** [ADR 0009 — Per-Call Profile Routing for SSH-Backed MCP Tools](../../adr/0009-ssh-tool-profile-routing.md)  
**Branch:** feat/ssh-profile-routing-127  
**Status:** Accepted  

---

## Problem

SSH-backed MCP tools ignore the caller-supplied `profile` parameter that REST-backed
tools have accepted since #126 (ADR 0008). This means a caller cannot route SSH
operations to a different HMC using a profile — they all go to the env-default HMC.

Three independent manifestations:

1. `_ssh_with_client()` in `_app.py` constructs `HMCConfig(_env_file=None)` directly,
   ignoring any `profile` argument.
2. `run_hmc_cli()` in `ssh.py` constructs `HMCConfig()` unconditionally.
3. `_resolve_system_name()` and `_resolve_lpar_name()` call `client_from_env()` with no
   profile; `TODO(#127)` comments at both sites mark this gap.

---

## Design

### Public interface

Every SSH-backing `@mcp.tool` function gains `profile: str | None = None` as its
last keyword parameter. Callers that omit it observe identical behavior to today.

**Affected tool files:**
- `server_tools/cli.py` — 10 tools via `_ssh_with_client`
- `server_tools/network.py` — 6 tools via `_ssh_with_client`
- `server_tools/profiles.py` — 4 tools via `_ssh_with_client`
- `server_tools/vios.py` — 3 tools calling `run_hmc_cli` directly
- `server_tools/system.py` — 1 tool (`hmc_run_command`) calling `run_hmc_cli` directly

### `_ssh_with_client(fn, *, system_name_or_uuid, lpar_name_or_uuid, profile)`

- Add `profile: str | None = None` keyword-only parameter.
- Replace `config = HMCConfig(_env_file=None)` with `config = client_from_env(profile).config`.
- Thread `profile` to `_resolve_system_name(config, system_name_or_uuid, profile)` and
  `_resolve_lpar_name(config, lpar_name_or_uuid, system_name, profile)`.

### `_resolve_system_name(config, system_name_or_uuid, profile=None)`

- Add `profile: str | None = None` parameter.
- Replace `client_from_env()` with `client_from_env(profile)` in the REST leg.
- Remove `TODO(#127)` comment.

### `_resolve_lpar_name(config, lpar_name_or_uuid, system_name=None, profile=None)`

- Same pattern as `_resolve_system_name`.

### `run_hmc_cli(cmd, config=None)`

- Add `config: HMCConfig | None = None` optional parameter.
- When `config` is provided, pass it to `run_hmc_command`; otherwise use `HMCConfig()`.
- The three direct call sites pass `HMCConfig` built via `client_from_env(profile).config`.

### Tool call-site pattern for `run_hmc_cli`

`run_hmc_cli` is called from `_run(lambda: run_hmc_cli(cmd))`. The lambda is entered
after `asyncio.run` is already in progress, so building the config must happen
synchronously before the event loop:

```python
config = client_from_env(profile).config
return _run(lambda: run_hmc_cli(cmd, config))
```

This is the same pattern used in `_ssh_with_client` and avoids any issue with calling
synchronous I/O inside the event loop.

---

## Success criteria

1. Every SSH-backed tool accepts `profile: str | None = None`.
2. A call with `profile="dev"` uses the `[profiles.dev]` credentials for SSH and REST.
3. A call with `profile=None` behaves identically to today (env-default resolution).
4. Two concurrent calls with different profiles use independent credentials.
5. The `TODO(#127)` comments in `_app.py` are removed.

---

## Test coverage

| Scenario | Test |
|---|---|
| Direct SSH uses supplied profile | `_ssh_with_client` passes profile-sourced config to SSH |
| REST resolution uses supplied profile | `client_from_env(profile)` called by `_resolve_system_name` |
| SSH fallback uses supplied profile | Transport failure falls back via SSH with same config |
| `run_hmc_cli` uses supplied config | Config passed through to `run_hmc_command` |
| `profile=None` is backward-compatible | Existing tests pass unchanged |
| Different profiles → independent configs | Two calls resolve different HMC hosts |

---

## Out of scope

- The `hmc_run_command` escape-hatch tool today uses `run_hmc_cli` with no profile; adding
  `profile` to it is in scope (it is an SSH tool), but changing its documented arbitrary-command
  warning or authentication documentation is not.
- No new CLI commands or config-file changes.
- No concurrent-execution infrastructure (asyncio task groups, etc.).
