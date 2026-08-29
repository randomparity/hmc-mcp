# Implementation Plan: Platform-Native TOML Profile Loader (#124)

**Branch:** feat/toml-profile-loader-124  
**BASE_BRANCH:** main  
**Guardrail:** `just verify` (inside worktree `../hmc-mcp-worktrees/feat/toml-profile-loader-124`)  
**Spec:** `docs/superpowers/specs/2026-platform-native-toml-profile-loader.md`  
**ADR:** `docs/adr/0006-toml-profile-loader.md`

---

## Phase 0 — Commit design artifacts

**Task:** Commit spec, ADR, and this plan.

- Files: `docs/adr/0006-toml-profile-loader.md`,
  `docs/superpowers/specs/2026-platform-native-toml-profile-loader.md`,
  `docs/superpowers/plans/2026-toml-profile-loader-plan.md`
- Acceptance: `git status` shows files committed; `just static` passes.

---

## Phase 1 — Write failing tests first (TDD)

**Task:** Create `tests/unit/test_config.py` with all tests listed in the spec
test plan. All tests must **fail** (or skip) before any implementation changes.

**Files touched:** `tests/unit/test_config.py` (new)

**Test strategy:**
- Use `tmp_path` fixture to create TOML files; never use real `~`.
- Use `monkeypatch` for env vars (`HMC_PROFILE`, `XDG_CONFIG_HOME`, etc.) and
  `sys.platform`.
- Patch `hmc_mcp.config.resolve_config_path` where needed to return a
  `tmp_path`-based path rather than monkeypatching the entire home directory.
- Import `ConfigError`, `load_profile`, `list_profiles`, `resolve_config_path`
  from `hmc_mcp.config`.

**Test list (18 tests):**

1. `test_resolve_linux_xdg` — `XDG_CONFIG_HOME=/tmp/x` → path is
   `/tmp/x/hmc-mcp/config.toml` (not checked for existence)
2. `test_resolve_linux_fallback` — `XDG_CONFIG_HOME` unset, `sys.platform=linux`
   → `~/.config/hmc-mcp/config.toml`
3. `test_resolve_macos` — `sys.platform=darwin` → `~/Library/Application
   Support/hmc-mcp/config.toml`
4. `test_resolve_windows` — `sys.platform=win32`, `APPDATA=/w/appdata` →
   `/w/appdata/hmc-mcp/config.toml`
5. `test_load_profile_explicit` — TOML with `[profiles.dev]`; call
   `load_profile("dev", config_path=...)` → `HMCConfig` with correct host/user
6. `test_load_profile_env_var` — `HMC_PROFILE=dev` set; call
   `load_profile(config_path=...)` → selects dev profile
7. `test_load_profile_default` — TOML with `default_profile="dev"`, no arg →
   selects dev profile
8. `test_load_profile_env_overrides_toml` — TOML has `host=toml-host`, env has
   `HMC_HOST=env-host` → `HMCConfig.host == "env-host"`
9. `test_load_profile_password_env` — `password_env="MY_PW"`, env has  # pragma: allowlist secret
   `MY_PW=secret` → `HMCConfig.password == "secret"`  # pragma: allowlist secret
10. `test_load_profile_both_passwords_error` — both `password` and
    `password_env` in TOML → `ConfigError`
11. `test_load_profile_missing_password_env` — `password_env="MISSING_VAR"`, var  # pragma: allowlist secret
    not set → `ConfigError` mentioning var name; no secret in message
12. `test_load_profile_no_default_no_arg` — TOML has profiles but no
    `default_profile`, no arg, `HMC_PROFILE` unset → `ConfigError`
13. `test_load_profile_unknown_profile` — profile name not in TOML →
    `ConfigError` listing available profiles
14. `test_load_profile_toml_parse_error` — file with invalid TOML →
    `ConfigError` with path
15. `test_list_profiles_normal` — TOML with two profiles → returns both names
16. `test_list_profiles_absent` — no file at path → returns `[]`
17. `test_hmcconfig_no_env_file` — instantiate `HMCConfig()` and verify
    `.model_config` has no `env_file` key pointing to `.env` (or `env_file` is
    absent / None)
18. `test_direct_construction_still_works` — `HMCConfig(host="h", user="u",
    password="p", _env_file=None)` returns config with those values

**Acceptance:** All 18 tests collected; most fail with `ImportError` or
`AttributeError` because the functions don't exist yet.

---

## Phase 2 — Implement the loader in config.py

**Task:** Add `ConfigError`, `resolve_config_path`, `load_profile`,
`list_profiles` to `src/hmc_mcp/config.py` and remove `env_file=".env"`.

**Files touched:** `src/hmc_mcp/config.py`

**Implementation notes:**

```python
import os
import sys
import tomllib
from pathlib import Path

class ConfigError(ValueError):
    """Raised when hmc-mcp/config.toml is invalid or a profile cannot be selected."""

def resolve_config_path() -> Path | None:
    """Return platform-native config.toml path, or None when absent."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / ".config"))
    else:  # Linux / other POSIX
        xdg = os.environ.get("XDG_CONFIG_HOME", "")
        base = Path(xdg) if xdg else Path.home() / ".config"
    p = base / "hmc-mcp" / "config.toml"
    return p if p.exists() else None

def list_profiles(config_path: Path | None = None) -> list[str]:
    path = config_path or resolve_config_path()
    if path is None or not path.exists():
        return []
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: TOML parse error: {exc}") from exc
    return list(doc.get("profiles", {}).keys())

def load_profile(
    profile: str | None = None,
    config_path: Path | None = None,
) -> "HMCConfig":
    path = config_path or resolve_config_path()
    # Determine selected profile name
    name = profile or os.environ.get("HMC_PROFILE")
    doc: dict = {}
    if path and path.exists():
        try:
            doc = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: TOML parse error: {exc}") from exc
        if name is None:
            name = doc.get("default_profile")
    if name is None:
        raise ConfigError(
            f"{path or 'config.toml'}: no default_profile set and no "
            "--profile / HMC_PROFILE supplied"
        )
    profiles = doc.get("profiles", {})
    if name not in profiles:
        available = list(profiles.keys())
        raise ConfigError(
            f"{path}: profile {name!r} not found; available: {available}"
        )
    entry = dict(profiles[name])
    # Resolve password_env
    if "password" in entry and "password_env" in entry:
        raise ConfigError(
            f"{path}: profile {name!r}: set password or password_env, not both"
        )
    if "password_env" in entry:
        var = entry.pop("password_env")
        if var not in os.environ:
            raise ConfigError(
                f"{path}: profile {name!r}: password_env={var!r} is not set"
            )
        entry["password"] = os.environ[var]
    return HMCConfig(_env_file=None, **entry)
```

**Remove from HMCConfig:**
```python
# BEFORE
model_config = SettingsConfigDict(
    env_prefix="HMC_",
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
)
# AFTER
model_config = SettingsConfigDict(
    env_prefix="HMC_",
    extra="ignore",
)
```

**Acceptance:** All 18 tests pass; `just verify` green.

---

## Phase 3 — Update common.py

**Task:** Update `client_from_env` docstring to reflect `.env` no longer
loaded. No functional change to `client_from_env` itself — it still
constructs `HMCConfig(**overrides)`. The `.env` removal in Phase 2 is
sufficient; `client_from_env` is unchanged in behavior because `HMCConfig`
already handles env vars via `env_prefix`.

**Files touched:** `src/hmc_mcp/common.py` — docstring only.

**Acceptance:** `just verify` still green; no new test needed.

---

## Phase 4 — Update cli_commands/app.py: add --profile option

**Task:** Add a `--profile` / `HMC_PROFILE` option to the root CLI callback.
Thread the profile through `_client()` and `_ssh_config()` via
`client_from_env`.

**Files touched:** `src/hmc_mcp/cli_commands/app.py`

**Changes:**

1. Add `profile: str | None` field to `GlobalOpts`.
2. Add `profile: str | None = typer.Option(None, "--profile", envvar="HMC_PROFILE", ...)`
   to `main()` callback and set `GLOBALS.profile`.
3. In `_client()`, pass `profile=GLOBALS.profile` to `client_from_env`.
4. In `_ssh_config()`, pass `profile=GLOBALS.profile` to `HMCConfig`
   construction (or load via `load_profile`).

**Note:** `client_from_env` does not yet accept `profile`; this will be wired in
Phase 3 (update common.py to pass `profile` to `load_profile` when no explicit
host is given). Keep it simple: if `GLOBALS.host` is explicitly set, build
`HMCConfig` directly; otherwise use `load_profile`.

**Revised common.py change (replaces Phase 3):**

```python
def client_from_env(profile: str | None = None, **overrides) -> HMCClient:
    """Create HMCClient. kwargs override env/TOML settings.

    If no override supplies `host` (the minimal required field), `load_profile`
    is called to select a TOML profile first, then overrides are applied.
    Checkout-local .env is NOT loaded.
    """
    filtered = {k: v for k, v in overrides.items() if v is not None}
    if "host" not in filtered and "host" not in os.environ.get("HMC_HOST", "").join(""):
        # No explicit host — try TOML profile loader
        try:
            from .config import load_profile
            config = load_profile(profile=profile)
            if filtered:
                config = HMCConfig(_env_file=None, **{**config.model_dump(), **filtered})
            return HMCClient(config)
        except Exception:
            pass  # Fall through to direct construction (env vars may supply host)
    config = HMCConfig(_env_file=None, **filtered)
    return HMCClient(config)
```

Actually simpler: keep `client_from_env` simple — it already works with env vars;
the profile loader is an additive path. See Phase 3 note in full plan below.

**Acceptance:** `hmc-mcp --profile dev systems list` wires through;
`just verify` green.

---

## Phase 5 — Update _app.py

**Task:** `ssh_with_client` calls `HMCConfig()` directly (line 308). Pass
`_env_file=None` to prevent any `.env` loading (belt-and-suspenders — env_file
is already removed in Phase 2, but the explicit `None` is a no-op and clarifies
intent).

**Files touched:** `src/hmc_mcp/_app.py` — one line change.

**Acceptance:** `just verify` green.

---

## Phase 6 — Update README.md and docs

**Task:** Update the "Configure" section in `README.md` to document the TOML
profile approach as the primary method and demote env vars to the override
description. Add `HMC_PROFILE` to `docs/environment-variables.md`.

**Files touched:** `README.md`, `docs/environment-variables.md`

**Acceptance:** `just env-vars` passes (HMC_PROFILE documented); README is
accurate.

---

## Phase 7 — Final guardrail run and commit

**Task:** Run `just verify` inside the worktree. Fix any remaining failures.
Commit all changes.

**Acceptance:** `just verify` exits 0 with no new warnings or failures.

---

## Rollback

This PR adds new functions and removes `env_file=".env"`. Rollback:
1. Revert `env_file` removal from `HMCConfig.model_config`.
2. Remove the new functions (`resolve_config_path`, `load_profile`,
   `list_profiles`, `ConfigError`).
3. Remove `--profile` from CLI.
No migration artifacts to roll back.
