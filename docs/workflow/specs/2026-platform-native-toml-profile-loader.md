# Spec: Platform-Native TOML Profile Loader

**Issue:** #124  
**ADR:** [ADR 0006](../../adr/0006-toml-profile-loader.md)  
**Status:** Design

---

## Outcome

Replace the single-connection, checkout-local `.env` configuration with a
platform-native TOML profile store that supports multiple named HMC connections
and correct override precedence.

---

## Completion Criteria

1. TOML path resolves per platform: Linux `$XDG_CONFIG_HOME/hmc-mcp/config.toml`
   (fallback `~/.config/hmc-mcp/config.toml`), macOS `~/Library/Application
   Support/hmc-mcp/config.toml`, Windows `%APPDATA%/hmc-mcp/config.toml`
   (fallback `~/.config/hmc-mcp/config.toml`).
2. Profile selection: explicit profile arg → `HMC_PROFILE` env var →
   `default_profile` in TOML → error.
3. Structural inventory (list profiles) never resolves secrets.
4. Selected connection construction resolves either `password` or
   `password_env` (lazy); rejects both together.
5. Invalid config fails with path, affected profile/field, and corrective
   action.
6. Checkout-local `.env` is ignored — `env_file` removed from `HMCConfig`.
7. Direct explicit `HMCConfig(...)` construction remains supported.
8. Tests in `tests/unit/test_config.py` cover all above without touching the
   real user home.
9. All guardrails pass: `just verify`.

---

## TOML Schema

```toml
default_profile = "prod"      # optional

[profiles.prod]
host = "hmc.example.com"
user = "admin"
password_env = "HMC_PROD_PASSWORD"  # pragma: allowlist secret

[profiles.dev]
host = "hmc-dev.example.com"
user = "admin"
password = "devpassword"  # pragma: allowlist secret
```

All fields from `HMCConfig` except `password` are valid in a profile entry.
Exactly one of `password` or `password_env` is required; both together is an
error. Neither is required when the profile is listed (inventory only).

---

## Public API (config.py additions)

```python
def resolve_config_path() -> Path | None:
    """Return the platform-native config.toml path, or None if absent."""

def load_profile(
    profile: str | None = None,
    config_path: Path | None = None,
) -> HMCConfig:
    """Load and return an HMCConfig for the selected profile.

    Selection order: explicit profile arg → HMC_PROFILE env var →
    default_profile in TOML → ConfigError.

    Precedence: explicit constructor args > HMC_* env vars > TOML values.
    .env is NOT loaded (env_file=None on HMCConfig).
    """

def list_profiles(config_path: Path | None = None) -> list[str]:
    """Return profile names from config.toml; empty list if absent."""
```

`ConfigError` (new) — `ValueError` subclass carrying `path`, `profile`, and
`field` context.

---

## Precedence Contract

Given a selected profile `P` with fields `host`, `user`, `password`/`password_env`:

1. If `HMC_HOST` (or `--host`) is set, it overrides the TOML `host`.
2. TOML fills fields not present in the environment.
3. `password_env = "VAR"` is resolved via `os.environ["VAR"]` at construction  # pragma: allowlist secret
   time, only for the selected profile.

`HMCConfig` remains a `pydantic_settings.BaseSettings` with `env_prefix="HMC_"`
and `env_file=None`. Direct `HMCConfig(host=..., user=..., password=...)` still
works unchanged.

---

## Error Messages

| Situation | Message shape |
|---|---|
| No profile selected and no default | `{path}: no default_profile set and no --profile / HMC_PROFILE supplied` |
| Named profile not in TOML | `{path}: profile {name!r} not found; available: {list}` |
| Both password and password_env set | `{path}: profile {name!r}: set password or password_env, not both` |
| password_env references missing env var | `{path}: profile {name!r}: password_env={var!r} is not set` |
| TOML parse error | `{path}: TOML parse error: {detail}` |
| Unknown field in profile | ignored (extra="ignore" on HMCConfig) |

---

## Threat Model

**This change is security-relevant** — it parses user-controlled files,
handles secrets, introduces a new env var, and widens the config surface.

### Boundary Inventory

| Boundary | What enters | From whom | Control |
|---|---|---|---|
| TOML file read | File contents from user's home dir | Local operator | `tomllib.loads()` — no code execution; read-only TOML parser |
| `password` field | Plaintext password in TOML | Local operator | Accepted as-is; no stripping needed (pydantic validates type) |
| `password_env` resolution | Env var name from TOML, value from environment | Local operator | `os.environ[name]` — KeyError surfaced as ConfigError with no secret in message |
| `HMC_PROFILE` env var | Profile name | Process environment | Looked up; must match a key in `[profiles]`; mismatch is ConfigError |
| Config path override | `config_path` argument | Caller | `Path` object; no shell expansion; `open()` surfaces `OSError` normally |

**No new boundaries are widened** relative to existing `HMC_*` env var reading.

### Actor Model

The only untrusted actor is a **local operator** (the process owner) who
controls both the TOML file and the process environment. There is no
network-origin input; no multi-tenant deployment; no remote actor can supply
a config path.

### Controls per Boundary

- **TOML parsing:** `tomllib` is the stdlib read-only parser — no custom
  deserializer, no object instantiation from file data, no shell expansion.
- **password_env:** resolved with `os.environ[name]`; on KeyError, a
  `ConfigError` message contains the **variable name** only, never its value.
- **Profile name input:** matched against the `[profiles]` table keys (dict
  lookup); no pattern matching, no shell execution.
- **File path:** resolved with `Path.expanduser()` — standard stdlib, no
  additional expansion. `open()` may raise `OSError`; callers receive the
  exception naturally.

### Out of Scope

- File-system permission checks on `config.toml` (accepted risk: same as
  `.env` today; operator is responsible for file mode).
- Secrets redaction in log output (no logging added by this change).
- Multi-user or container credential isolation (out of scope for this epic).

---

## Test Plan

All tests in `tests/unit/test_config.py`, using `tmp_path` and
`monkeypatch`. No test touches the real user home.

| Test | Coverage |
|---|---|
| `test_resolve_linux_xdg` | XDG_CONFIG_HOME set → uses it |
| `test_resolve_linux_fallback` | XDG unset → ~/.config |
| `test_resolve_macos` | sys.platform=darwin → Library/Application Support |
| `test_resolve_windows` | sys.platform=win32 → APPDATA |
| `test_load_profile_explicit` | explicit profile arg selects correct profile |
| `test_load_profile_env_var` | HMC_PROFILE selects profile |
| `test_load_profile_default` | default_profile in TOML |
| `test_load_profile_env_overrides_toml` | HMC_HOST env beats TOML host |
| `test_load_profile_password_env` | password_env resolved from env |
| `test_load_profile_both_passwords_error` | both password + password_env → ConfigError |
| `test_load_profile_missing_password_env` | missing env var → ConfigError |
| `test_load_profile_no_default_no_arg` | no selection path → ConfigError |
| `test_load_profile_unknown_profile` | named profile missing → ConfigError with list |
| `test_load_profile_toml_parse_error` | bad TOML → ConfigError |
| `test_list_profiles_normal` | returns profile names |
| `test_list_profiles_absent` | file absent → empty list |
| `test_hmcconfig_no_env_file` | HMCConfig() does NOT load .env |
| `test_direct_construction_still_works` | HMCConfig(host=...) still works |
