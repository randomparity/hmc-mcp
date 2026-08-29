# Implementation Plan: SSH Tool Profile Routing (#127)

**Branch:** feat/ssh-profile-routing-127  
**Base branch:** main  
**Spec:** `docs/superpowers/specs/2026-ssh-profile-routing-spec.md`  
**ADR:** `docs/adr/0009-ssh-tool-profile-routing.md`  
**Guardrails:** `just verify` (= `just static` + `just test` + `just smoke` + CLI group loads)

---

## Phase 1 — Tests first (TDD red → green)

### Task 1.1 — Write failing tests for profile routing in `ssh_with_client`

**File:** `tests/unit/test_ssh_profile_routing.py` (new)

**What to test:**
- `ssh_with_client` with `profile="dev"`: the `HMCConfig` passed to `run_hmc_command` has the `dev` profile's host/user.
- `ssh_with_client` REST resolver (`_resolve_system_name`) calls `client_from_env(profile)` with the supplied profile.
- `ssh_with_client` SSH fallback: when `httpx.HTTPError` is raised by the REST leg, the fallback SSH call uses the same `config`.
- `ssh_with_client` with `profile=None`: behavior unchanged from today (env-default HMC).
- Two calls with different profiles produce independent `HMCConfig` values (no shared state).

**Acceptance criteria:**
- All new tests fail with `TypeError` or `AssertionError` before implementation (red).
- They pass after implementation (green).

**Repo conventions:** Use `pytest`, `unittest.mock`, `respx` for REST mocking, `asyncssh` mock from `conftest.py`.

---

### Task 1.2 — Write failing tests for `_resolve_system_name`/`_resolve_lpar_name` with profile

**File:** `tests/unit/test_ssh_profile_routing.py` (continue)

**What to test:**
- `_resolve_system_name` calls `client_from_env(profile)` (not `client_from_env()`) when `profile` is set.
- `_resolve_lpar_name` same.
- Both still call `client_from_env(None)` when `profile=None` (backward compat).

---

### Task 1.3 — Write failing tests for `run_hmc_cli` config passthrough

**File:** `tests/unit/test_ssh_profile_routing.py` (continue)

**What to test:**
- `run_hmc_cli(cmd, config=some_config)` calls `run_hmc_command(some_config, cmd)`.
- `run_hmc_cli(cmd)` still calls `run_hmc_command(HMCConfig(), cmd)` (backward compat).

---

### Task 1.4 — Write failing tests for MCP tool `profile` parameter

**File:** `tests/unit/test_ssh_profile_routing.py` (continue)

**What to test:**
- `hmc_run_command(cmd, profile="dev")` routes SSH to the dev-profile host.
- One `server_tools/vios.py` tool (`hmc_restore_vios`) with `profile="dev"` uses dev-profile config.
- One `server_tools/cli.py` tool (`hmc_list_memory_pools`) with `profile="dev"` passes profile through `ssh_with_client`.
- All existing `test_ssh_quoting.py` tests still pass (backward-compat guard).

---

## Phase 2 — Implementation

### Task 2.1 — Extend `_resolve_system_name` and `_resolve_lpar_name` in `_app.py`

**File:** `src/hmc_mcp/_app.py`  
**Lines affected:** ~187-228

**Changes:**
1. `_resolve_system_name(config, system_name_or_uuid, profile=None)` — add `profile` param.
2. Replace `async with client_from_env() as hmc:` with `async with client_from_env(profile) as hmc:`.
3. Remove `# TODO(#127)` comment at both call sites.
4. `_resolve_lpar_name(config, lpar_name_or_uuid, system_name=None, profile=None)` — same pattern.

**No change to function return types or SSH fallback paths.**

---

### Task 2.2 — Extend `ssh_with_client` in `_app.py`

**File:** `src/hmc_mcp/_app.py`  
**Lines affected:** ~289-309

**Changes:**
1. Add `profile: str | None = None` to the function signature.
2. Replace `config = HMCConfig(_env_file=None)` with `config = client_from_env(profile).config`.
3. Thread `profile` to both `_resolve_system_name(config, system_name_or_uuid, profile)` and
   `_resolve_lpar_name(config, lpar_name_or_uuid, system_name, profile)`.

**Note:** `client_from_env(profile)` is a synchronous call that opens no connections; it is safe to call here before `asyncio.run`.

---

### Task 2.3 — Extend `run_hmc_cli` in `ssh.py`

**File:** `src/hmc_mcp/ssh.py`  
**Lines affected:** ~119-126

**Changes:**
1. Add `config: HMCConfig | None = None` to the `run_hmc_cli` signature.
2. When `config` is provided, pass it to `run_hmc_command`; otherwise `HMCConfig()`.
3. Update the docstring to mention the optional `config` parameter.

---

### Task 2.4 — Add `profile` to `hmc_run_command` in `server_tools/system.py`

**File:** `src/hmc_mcp/server_tools/systems.py`
**Lines affected:** ~22-36

**Changes:**
1. Add `profile: str | None = None` to `hmc_run_command(cmd, profile=None)`.
2. Build config before `_run`: `config = client_from_env(profile).config`
3. Replace `_run(lambda: run_hmc_cli(cmd))` with `_run(lambda: run_hmc_cli(cmd, config))`.

---

### Task 2.5 — Add `profile` to VIOS tools in `server_tools/vios.py`

**File:** `src/hmc_mcp/server_tools/vios.py`
**Lines affected:** ~221-272

Three tools call `run_hmc_cli` directly: `hmc_list_vios_backups`, `hmc_backup_vios`, `hmc_restore_vios`.

For each:
1. Add `profile: str | None = None` as last parameter.
2. Build config before lambda: `config = client_from_env(profile).config`
3. Replace `run_hmc_cli(cmd)` with `run_hmc_cli(cmd, config)` (or the f-string lambda form).

---

### Task 2.6 — Add `profile` to all `ssh_with_client` callers

**Files:** `server_tools/cli.py` (10 tools), `server_tools/network.py` (6 tools), `server_tools/profiles.py` (4 tools)

**Pattern (identical for every tool):**
1. Add `profile: str | None = None` as last parameter.
2. Pass `profile=profile` to `ssh_with_client(...)`.

No other logic changes in these files.

---

### Task 2.7 — Update `common.py` import if needed

`_app.py` already imports `client_from_env` from `.common`. Verify the import is
present and no new import is needed.

---

## Phase 3 — Verification

### Task 3.1 — Run guardrails

```sh
just verify
```

All checks must pass with no new warnings. Fix any failures before proceeding.

---

### Task 3.2 — Confirm backward-compat: existing tests unchanged

```sh
just test
```

`test_ssh_quoting.py` and `test_ssh.py` must continue to pass without modification.

---

## Commit strategy

- Commit after Task 1.4 (all tests written, all red): `test: add failing tests for SSH profile routing (#127)`
- Commit after Task 2.7 (all implementation, tests green): `feat(mcp): route SSH tools by per-call profile (#127)`
- Run `just verify` before each commit.

---

## Rollback

The `profile` parameter is additive and optional (`= None`). Reverting this PR
restores previous behavior completely; no migration or config-file change is needed.

---

## Files touched

| File | Change type |
|---|---|
| `src/hmc_mcp/_app.py` | Extend `ssh_with_client`, `_resolve_system_name`, `_resolve_lpar_name` |
| `src/hmc_mcp/ssh.py` | Extend `run_hmc_cli` |
| `src/hmc_mcp/server_tools/system.py` | Add `profile` to `hmc_run_command` |
| `src/hmc_mcp/server_tools/vios.py` | Add `profile` to 3 tools |
| `src/hmc_mcp/server_tools/cli.py` | Add `profile` to 10 tools |
| `src/hmc_mcp/server_tools/network.py` | Add `profile` to 6 tools |
| `src/hmc_mcp/server_tools/profiles.py` | Add `profile` to 4 tools |
| `tests/unit/test_ssh_profile_routing.py` | New test file |
| `docs/adr/0009-ssh-tool-profile-routing.md` | New ADR |
| `docs/superpowers/specs/2026-ssh-profile-routing-spec.md` | New spec |
