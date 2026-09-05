# Spec: Profile-Aware Configuration Commands for the CLI

**Issue:** #125  
**ADR:** [ADR 0007](../../adr/0007-cli-config-commands.md)  
**Status:** Design  
**Parent Epic:** #123  
**Depends on:** #124 (merged — `load_profile`, `list_profiles`, `resolve_config_path`)

---

## Outcome

Add a `config` subgroup to the `hmc-mcp` CLI exposing `init`, `list`, and `show`
commands that let operators safely bootstrap, inspect, and verify their profile
configuration without exposing secrets. The global `--profile` flag (already
present in `GlobalOpts`) selects the profile for commands that consume it.

---

## Completion Criteria

1. `hmc-mcp config init` creates parent directories and a starter TOML at the
   platform-native path, refuses to overwrite an existing file, applies
   restrictive permissions (mode 0o600 on POSIX), and prints the resulting
   absolute path to stdout. Non-destructive: an existing file yields a clear
   error, not corruption.
2. `hmc-mcp config list` prints profile names and marks the default (if any).
   When the file is absent, prints a helpful "no config file found" message
   (not an error exit).
3. `hmc-mcp config show [--profile NAME]` emits non-secret connection metadata:
   host, port, user, verify_ssl, timeout, audit_memento, schema_version, and
   booleans `password_configured` / `ssh_key_configured`. Never resolves
   `password_env`, never emits literal passwords. `--json` flag emits JSON.
4. All three commands exit 0 on success, exit 1 with a red `Error:` line on
   **stderr** on failure (consistent with other CLI commands using `_fail`).
5. `--profile` selects the profile for `show`; `list` is profile-independent.
6. Tests cover: `init` happy path, `init` existing-file refusal, `init`
   permissions (POSIX), `list` with profiles, `list` absent file, `show`
   password redaction, `show` `password_env` non-resolution, `show` JSON flag,
   `show` unknown profile error, `show` absent file error.
7. `just verify` passes.

---

## Security Considerations (Threat Model)

The change reads a config file the user owns. There are no new trust boundaries
beyond those already introduced by #124.

**Relevant boundaries:**

| Boundary | What enters | Control |
|---|---|---|
| `config show` output | Caller-provided `--profile` name (CLI arg) | Resolved to a raw TOML dict (unknown profile → empty dict → `ConfigError` from `load_profile()`) — all fields gathered before any output, so no partial output on error |
| File write (`init`) | Target path from `resolve_config_path()` | Path is always under platform config dir, never user-supplied; no path traversal possible |
| Secret values | Literal password from TOML, `password_env` value | `show` never resolves `password_env`; literal password suppressed and replaced with a boolean |

**Out of scope:** the underlying file permissions, OS keychain, and network
access are unchanged from the loader (ADR-0006).

---

## Design

### File layout

| File | Role |
|---|---|
| `src/hmc_mcp/cli_commands/config.py` | New — the `config_app` Typer and three command bodies |
| `src/hmc_mcp/cli_commands/app.py` | Add `config_app = typer.Typer(...)`, `app.add_typer(config_app, name="config")` |
| `src/hmc_mcp/cli.py` | Add `from . import cli_config` import (side-effect: registers commands) |
| `tests/app/test_cli_commands/config.py` | New — all test cases for the config commands |

### `config init`

1. Computes the target path using a `config_dir()` helper (or inline equivalent)
   that returns `<platform_dir>/hmc-mcp/config.toml` without checking for file
   existence — this is the same path logic as `resolve_config_path()` minus the
   existence check.
2. Calls `resolve_config_path()` to check whether the file already exists.
   - If it returns non-None (file exists): print error to stderr, exit 1. Do
     **not** call `os.path.exists()` separately — the `resolve_config_path()`
     result is the definitive existence check; a second stat re-opens the TOCTOU
     window that `O_EXCL` is meant to close.
3. Create parent directories (`mkdir -p`).
4. Write starter TOML using `open(path, 'x')` (`O_CREAT|O_EXCL`) — MUST use
   exclusive-create; without it a concurrent process could silently overwrite an
   existing config file, destroying credentials. On POSIX apply `chmod 0o600`.
5. Print the absolute path to stdout.

Starter TOML content:
```toml
# hmc-mcp configuration — see README for the full schema
# default_profile = "prod"

[profiles.example]
host = "hmc.example.com"
user = "admin"
password_env = "HMC_PASSWORD"  # preferred: secret stays out of the file  # pragma: allowlist secret
# password = "..."             # alternative: literal password (less secure)
# verify_ssl = false
```

### `config list`

1. Calls `resolve_config_path()` → None → print "No config file found at
   `<platform path>`." and exit 0.
2. Calls `list_profiles()` with the resolved path.
3. Gets the `default_profile` key from the TOML document in the same pass as
   `list_profiles()` — either extend `list_profiles()` to return both names and
   default, or use a single-read helper in `config.py`. A second `tomllib.loads`
   call is not acceptable: the file could be deleted between reads, and two reads
   give two inconsistent views of the same file.
4. Prints each name; appends `(default)` to the name matching `default_profile`.
   When `default_profile` is absent from the TOML, no name is marked — the
   `HMC_PROFILE` env var is not considered here (it is a runtime selector, not
   a saved default).

### `config show [--profile NAME]`

The command's own `--profile` arg takes precedence over the global `--profile`
(`GLOBALS.profile`). If the command's `--profile` is `None`, fall through to
`GLOBALS.profile`. This mirrors the resolution order used by `_client()`.

1. Read the config file path via `resolve_config_path()`. If None, `_fail` (exit 1).
2. Parse the raw TOML dict directly in the command body:
   ```python
   raw = tomllib.loads(config_path.read_text())
   # effective_profile is resolved before this point via the local/global chain
   profile_dict = raw.get("profiles", {}).get(effective_profile, {})
   ```
   This is the **only** read of the file for credential-presence detection; it
   must happen before `load_profile()` so that `password_env` absence from the
   environment does not prevent the booleans from being computed. An unknown
   profile name yields an empty `profile_dict`; the `ConfigError` fires later
   when `load_profile()` is called in step 3.
3. **Gather all output fields before emitting any output:**
   - `password_configured`: `True` if `profile_dict` contains a non-empty
     `"password"` key **or** a `"password_env"` key. Never derived from
     `cfg.password` — `load_profile()` resolves `password_env` at construction
     (ADR-0006), so `cfg.password` may be empty or may raise `ConfigError` when
     the env var is absent.
   - `ssh_key_configured`: `True` if `profile_dict` contains a non-empty
     `"ssh_key_file"` key.
   - Remaining non-secret fields (host, port, user, verify_ssl, timeout,
     audit_memento, schema_version): call `load_profile(profile=effective_profile)`
     and read from the returned `HMCConfig`.
   - Never emit `cfg.password`.
4. Only after all fields are gathered: emit output (key-value table or `--json`).
   A `ConfigError` at any point in the gather phase calls `_fail(exc)` — no
   partial output is ever emitted.

The `show` command deliberately does **not** try to resolve `password_env`:
the TOML file may store a variable name pointing to a secret that is not
in the current shell's environment (e.g., a production secret not present
locally). The command reports only that a credential _is configured_.

---

## Test Plan

All tests use `typer.testing.CliRunner` and `tmp_path`/`monkeypatch` to isolate
the real filesystem. No test touches the real user home.

| # | Test | Assert |
|---|---|---|
| 1 | `init` happy path — file absent | file exists, 0 exit, path printed to stdout |
| 2 | `init` existing file refusal | exit 1, "already exists" in stderr, file unchanged |
| 3 | `init` permissions (POSIX only) | mode 0o600, parent dirs created |
| 4 | `list` with two profiles + default | both names present, default marked |
| 4b | `list` with profiles, no `default_profile` key | names printed, none marked `(default)` |
| 5 | `list` absent config file | exit 0, "No config file" in output |
| 6 | `show` password redaction | `password_configured: true`, no literal password |
| 7 | `show` password_env non-resolution | shows `password_configured: true` without resolving var |
| 8 | `show` `--json` flag | valid JSON, no `password` key |
| 9 | `show` unknown profile → error | exit 1, error message contains profile name |
| 10 | `show` absent config file → error | exit 1, helpful message |
| 11 | `show` — no `--profile`, no `HMC_PROFILE` env var, no `default_profile` in TOML | exit 1, error references no profile selected |

---

## Excluded Work

- MCP `profile` parameter on tools (#126, #127)
- `hmc_list_configured_hosts` MCP tool (#128)
- Profile add/edit/delete CLI commands (epic non-goal)
- OS keychain integration (epic non-goal)
- Live connectivity/auth check in `config show` (epic non-goal)
