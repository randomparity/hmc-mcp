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
   failure (consistent with other CLI commands using `_fail`).
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
| `config show` output | Caller-provided `--profile` name (CLI arg) | Passed to `load_profile()` — unknown profiles raise `ConfigError` before any output |
| File write (`init`) | Target path from `resolve_config_path()` | Path is always under platform config dir, never user-supplied; no path traversal possible |
| Secret values | Literal password from TOML, `password_env` value | `show` never resolves `password_env`; literal password suppressed and replaced with a boolean |

**Out of scope:** the underlying file permissions, OS keychain, and network
access are unchanged from the loader (ADR-0006).

---

## Design

### File layout

| File | Role |
|---|---|
| `src/hmc_mcp/cli_config.py` | New — the `config_app` Typer and three command bodies |
| `src/hmc_mcp/cli_app.py` | Add `config_app = typer.Typer(...)`, `app.add_typer(config_app, name="config")` |
| `src/hmc_mcp/cli.py` | Add `from . import cli_config` import (side-effect: registers commands) |
| `tests/app/test_cli_config.py` | New — all test cases for the config commands |

### `config init`

1. Calls `resolve_config_path()` — returns None when absent or the existing path.
2. Computes the target path: `<platform_dir>/hmc-mcp/config.toml`.
   - We need a `config_dir()` helper (or inline) that returns the parent dir
     without checking existence; this is a thin wrapper on the same logic in
     `resolve_config_path()` but without the existence check.
3. If the file already exists: print error, exit 1.
4. Create parent directories (`mkdir -p`).
5. Write starter TOML; on POSIX apply `chmod 0o600`.
6. Print the absolute path to stdout.

Starter TOML content:
```toml
# hmc-mcp configuration — see README for the full schema
# default_profile = "prod"

[profiles.example]
host = "hmc.example.com"
user = "admin"
password_env = "HMC_PASSWORD"  # or: password = "..."
# verify_ssl = false
```

### `config list`

1. Calls `resolve_config_path()` → None → print "No config file found at
   `<platform path>`." and exit 0.
2. Calls `list_profiles()` with the resolved path.
3. Gets the default from the TOML `default_profile` key.
4. Prints each name; appends `(default)` to the matching one.

### `config show [--profile NAME]`

1. Calls `load_profile(profile=...)` using `GLOBALS.profile` or the command's
   `--profile` arg.
2. Inspects the loaded `HMCConfig`:
   - Emits all non-secret fields.
   - `password_configured`: `True` if `cfg.password != ""`.
   - `ssh_key_configured`: `True` if `cfg.ssh_key_file is not None`.
   - Never emits `cfg.password` value.
3. On `ConfigError`: `_fail(exc)`.
4. With `--json`: emits JSON dict; without: aligned key-value table.

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
| 5 | `list` absent config file | exit 0, "No config file" in output |
| 6 | `show` password redaction | `password_configured: true`, no literal password |
| 7 | `show` password_env non-resolution | shows `password_configured: true` without resolving var |
| 8 | `show` `--json` flag | valid JSON, no `password` key |
| 9 | `show` unknown profile → error | exit 1, error message contains profile name |
| 10 | `show` absent config file → error | exit 1, helpful message |

---

## Excluded Work

- MCP `profile` parameter on tools (#126, #127)
- `hmc_list_configured_hosts` MCP tool (#128)
- Profile add/edit/delete CLI commands (epic non-goal)
- OS keychain integration (epic non-goal)
- Live connectivity/auth check in `config show` (epic non-goal)
