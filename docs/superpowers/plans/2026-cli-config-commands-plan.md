# CLI Config Commands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `hmc-mcp config init/list/show` subcommands and wire the existing `--profile` global flag into them, exposing the platform-native TOML profile loader safely without revealing secrets.

**Architecture:** New `cli_config.py` domain module registers on a `config_app` Typer wired into the root CLI. The three commands delegate to the existing `config.py` API (`resolve_config_path`, `list_profiles`, `load_profile`). `config show` reads the raw TOML dict before calling `load_profile` so credential-presence booleans can be reported without resolving `password_env`.

**Tech Stack:** Python 3.12+, typer, pydantic-settings, tomllib (stdlib), typer.testing.CliRunner, pytest, `just verify`

## Global Constraints

- Python ≥ 3.12 (tomllib is stdlib)
- No new PyPI dependencies
- All tests use `tmp_path` / `monkeypatch` — no test touches the real user home
- Guardrail: `just verify` must pass after every commit
- Secrets baseline: add `# pragma: allowlist secret` to any string that triggers detect-secrets
- Branch: `feat/config-commands-125`, BASE_BRANCH: `main`
- Repo root: `/Users/drc/src/hmc-mcp`
- Spec: `docs/superpowers/specs/2026-cli-config-commands.md`
- ADR: `docs/adr/0007-cli-config-commands.md`

---

### Task 1: Extend `config.py` with `config_dir()` and `list_profiles_with_default()`

**Files:**
- Modify: `src/hmc_mcp/config.py`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Produces:
  - `config_dir() -> Path` — returns the platform-native `hmc-mcp/` parent directory WITHOUT checking existence (same platform logic as `resolve_config_path()` but no `.exists()` guard)
  - `list_profiles_with_default(config_path: Path | None = None) -> tuple[list[str], str | None]` — returns `(names, default_profile_or_none)` from one TOML read

**Why these are needed:**
- `config init` needs the path even when the file doesn't exist — `resolve_config_path()` returns `None` when absent, so we need the raw path separately.
- `config list` must read profile names AND `default_profile` in a single `tomllib.loads` call (spec §`config list` step 3 — two separate reads give inconsistent views if the file is deleted between them).

- [ ] **Step 1: Write failing tests for `config_dir()`**

In `tests/unit/test_config.py`, add at the bottom:

```python
# ---------------------------------------------------------------------------
# config_dir — unconditional platform path (no existence check)
# ---------------------------------------------------------------------------

def test_config_dir_linux_xdg(monkeypatch):
    """config_dir() returns XDG path without checking existence."""
    xdg = Path("/tmp/fake_xdg")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    with patch.object(sys, "platform", "linux"):
        result = config_dir()
    assert result == xdg / "hmc-mcp"


def test_config_dir_macos(monkeypatch):
    """config_dir() returns ~/Library/Application Support/hmc-mcp on macOS."""
    fake_home = Path("/tmp/fake_home")
    with patch.object(sys, "platform", "darwin"), \
         patch("pathlib.Path.home", return_value=fake_home):
        result = config_dir()
    assert result == fake_home / "Library" / "Application Support" / "hmc-mcp"


def test_config_dir_returns_path_even_when_absent(tmp_path, monkeypatch):
    """config_dir() does not require the directory to exist."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nonexistent"))
    with patch.object(sys, "platform", "linux"):
        result = config_dir()
    assert not result.exists()  # no side-effects
    assert result.name == "hmc-mcp"


# ---------------------------------------------------------------------------
# list_profiles_with_default
# ---------------------------------------------------------------------------

def test_list_profiles_with_default_normal(tmp_path, monkeypatch):
    """Returns (names, default) from TOML."""
    cfg = _write_toml(tmp_path / "config.toml", TWO_PROFILE_TOML)
    names, default = list_profiles_with_default(config_path=cfg)
    assert set(names) == {"prod", "dev"}
    assert default == "prod"


def test_list_profiles_with_default_no_default(tmp_path, monkeypatch):
    """Returns (names, None) when no default_profile key."""
    cfg = _write_toml(tmp_path / "config.toml", MINIMAL_TOML)
    names, default = list_profiles_with_default(config_path=cfg)
    assert "dev" in names
    assert default is None


def test_list_profiles_with_default_absent(tmp_path):
    """Returns ([], None) when file absent."""
    names, default = list_profiles_with_default(config_path=tmp_path / "nonexistent.toml")
    assert names == []
    assert default is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/drc/src/hmc-mcp
uv run pytest tests/unit/test_config.py -k "config_dir or list_profiles_with_default" -v
```

Expected: `ImportError` or `NameError` (functions not yet defined)

- [ ] **Step 3: Add `config_dir` and `list_profiles_with_default` to `config.py`**

In `src/hmc_mcp/config.py`, add after `resolve_config_path()` (after line 108):

```python
def config_dir() -> Path:
    """Return the platform-native hmc-mcp/ config directory (no existence check).

    Same platform resolution as resolve_config_path() but never checks whether
    the directory or file exists. Used by config init to compute the target path.
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        base = Path(appdata) if appdata else Path.home() / ".config"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME", "")
        base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "hmc-mcp"


def list_profiles_with_default(
    config_path: Path | None = None,
) -> tuple[list[str], str | None]:
    """Return (profile_names, default_profile_or_none) from one TOML read.

    Never resolves secrets — safe for diagnostics.
    Returns ([], None) when the file is absent or path is None.
    Raises ConfigError on TOML parse errors.
    """
    path = config_path if config_path is not None else resolve_config_path()
    if path is None or not path.exists():
        return [], None
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: TOML parse error: {exc}") from exc
    names = list(doc.get("profiles", {}).keys())
    default = doc.get("default_profile")
    return names, default
```

Also update the imports in `tests/unit/test_config.py` to include `config_dir` and `list_profiles_with_default`:

```python
from hmc_mcp.config import (
    ConfigError,
    HMCConfig,
    config_dir,
    list_profiles,
    list_profiles_with_default,
    load_profile,
    resolve_config_path,
)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/unit/test_config.py -k "config_dir or list_profiles_with_default" -v
```

Expected: all new tests pass

- [ ] **Step 5: Run full static + test suite**

```bash
just verify
```

Expected: all checks pass

- [ ] **Step 6: Commit**

```bash
git add src/hmc_mcp/config.py tests/unit/test_config.py
git commit -m "feat(config): add config_dir() and list_profiles_with_default() (#125)"
```

---

### Task 2: Register `config_app` in `cli_app.py` and `cli.py`

**Files:**
- Modify: `src/hmc_mcp/cli_app.py` — add `config_app` Typer and register it
- Modify: `src/hmc_mcp/cli.py` — add `from . import cli_config` import

**Interfaces:**
- Consumes: `typer.Typer` (already imported in cli_app.py)
- Produces: `config_app` Typer instance (used by `cli_config.py` Task 3)

This task only adds the wiring — no command bodies yet. The import in `cli.py` will fail until Task 3 creates `cli_config.py`, so Task 3 must follow immediately.

- [ ] **Step 1: Add `config_app` to `cli_app.py`**

In `src/hmc_mcp/cli_app.py`, after the `raw_app` and `memory_pools_app` lines and their `app.add_typer(...)` calls, add:

```python
config_app = typer.Typer(help="Profile configuration commands.", no_args_is_help=True)
```

And immediately after `app.add_typer(memory_pools_app, name="memory-pools")`:

```python
app.add_typer(config_app, name="config")
```

- [ ] **Step 2: Add `cli_config` import to `cli.py`**

In `src/hmc_mcp/cli.py`, after the last `from . import cli_vios` line, add:

```python
from . import cli_config  # noqa: F401  (side-effect: registers commands)
```

- [ ] **Step 3: Create an empty `cli_config.py` stub so the import doesn't fail**

Create `src/hmc_mcp/cli_config.py` with just:

```python
"""Configuration subgroup commands for hmc-mcp."""

from __future__ import annotations

from .cli_app import config_app  # noqa: F401  (import required; commands registered below)
```

- [ ] **Step 4: Verify the CLI loads cleanly**

```bash
uv run hmc-mcp --help
uv run hmc-mcp config --help
```

Expected: `config` appears in the command list; `config --help` shows an empty group.

- [ ] **Step 5: Run static suite**

```bash
just static
```

Expected: passes

- [ ] **Step 6: Commit**

```bash
git add src/hmc_mcp/cli_app.py src/hmc_mcp/cli.py src/hmc_mcp/cli_config.py
git commit -m "feat(cli): register config_app subgroup (#125)"
```

---

### Task 3: Implement `config init`, `config list`, `config show` with tests

**Files:**
- Create/Modify: `src/hmc_mcp/cli_config.py` (expand the stub from Task 2)
- Create: `tests/app/test_cli_config.py`

**Interfaces:**
- Consumes from Task 1: `config_dir()`, `list_profiles_with_default()`, `resolve_config_path()`, `load_profile()`, `ConfigError` from `hmc_mcp.config`
- Consumes from Task 2: `config_app` from `hmc_mcp.cli_app`
- Consumes: `GLOBALS` from `hmc_mcp.cli_app`, `_fail` from `hmc_mcp.cli_app`, `console` from `hmc_mcp.cli_app`, `err_console` from `hmc_mcp.cli_app`
- Consumes: `app` from `hmc_mcp.cli` (for CliRunner tests)

#### Step 1 — Write ALL failing tests first

- [ ] **Step 1: Create `tests/app/test_cli_config.py` with all tests (they will fail)**

```python
"""Tests for hmc-mcp config init/list/show commands."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from hmc_mcp import cli

RUNNER = CliRunner()

TWO_PROFILE_TOML = """\
default_profile = "prod"

[profiles.prod]
host = "hmc.example.com"
user = "admin"
password = "prodpass"  # pragma: allowlist secret

[profiles.dev]
host = "hmc-dev.example.com"
user = "devadmin"
password_env = "HMC_DEV_PW"  # pragma: allowlist secret
"""

NO_DEFAULT_TOML = """\
[profiles.alpha]
host = "hmc-alpha.example.com"
user = "admin"
password = "alphapw"  # pragma: allowlist secret

[profiles.beta]
host = "hmc-beta.example.com"
user = "admin"
password = "betapw"  # pragma: allowlist secret
"""


def _write_toml(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# config init
# ---------------------------------------------------------------------------

def test_init_creates_file(tmp_path, monkeypatch):
    """init creates the config file and prints the path when it does not exist."""
    target = tmp_path / "hmc-mcp" / "config.toml"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["config", "init"])
    assert result.exit_code == 0, result.output
    assert target.exists()
    assert str(target) in result.output


def test_init_refuses_existing_file(tmp_path, monkeypatch):
    """init exits 1 with an error message when the file already exists."""
    target = tmp_path / "hmc-mcp" / "config.toml"
    _write_toml(target, "[profiles.x]\nhost='h'\nuser='u'\npassword='p'  # pragma: allowlist secret\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["config", "init"])
    assert result.exit_code == 1
    assert "already exists" in result.output
    # File must be unchanged
    assert target.read_text() != ""


@pytest.mark.skipif(sys.platform == "win32", reason="chmod not meaningful on Windows")
def test_init_permissions(tmp_path, monkeypatch):
    """init creates the file with mode 0o600 on POSIX."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["config", "init"])
    assert result.exit_code == 0, result.output
    target = tmp_path / "hmc-mcp" / "config.toml"
    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"


# ---------------------------------------------------------------------------
# config list
# ---------------------------------------------------------------------------

def test_list_shows_profiles_and_default(tmp_path, monkeypatch):
    """list shows both profile names; the default is marked."""
    cfg = _write_toml(tmp_path / "hmc-mcp" / "config.toml", TWO_PROFILE_TOML)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["config", "list"])
    assert result.exit_code == 0, result.output
    assert "prod" in result.output
    assert "dev" in result.output
    assert "(default)" in result.output
    # Only prod should be marked default
    lines = result.output.splitlines()
    default_lines = [l for l in lines if "(default)" in l]
    assert len(default_lines) == 1
    assert "prod" in default_lines[0]


def test_list_no_default_key(tmp_path, monkeypatch):
    """list shows names without any (default) marker when default_profile absent."""
    cfg = _write_toml(tmp_path / "hmc-mcp" / "config.toml", NO_DEFAULT_TOML)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["config", "list"])
    assert result.exit_code == 0, result.output
    assert "alpha" in result.output
    assert "beta" in result.output
    assert "(default)" not in result.output


def test_list_absent_file(tmp_path, monkeypatch):
    """list exits 0 with a helpful message when no config file exists."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["config", "list"])
    assert result.exit_code == 0
    assert "No config file" in result.output or "no config" in result.output.lower()


# ---------------------------------------------------------------------------
# config show
# ---------------------------------------------------------------------------

def test_show_password_redacted(tmp_path, monkeypatch):
    """show emits password_configured:True but never the literal password."""
    cfg = _write_toml(tmp_path / "hmc-mcp" / "config.toml", TWO_PROFILE_TOML)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["--profile", "prod", "config", "show"])
    assert result.exit_code == 0, result.output
    assert "prodpass" not in result.output
    assert "password_configured" in result.output
    assert "true" in result.output.lower() or "True" in result.output


def test_show_password_env_not_resolved(tmp_path, monkeypatch):
    """show reports password_configured:True for password_env without resolving the var."""
    cfg = _write_toml(tmp_path / "hmc-mcp" / "config.toml", TWO_PROFILE_TOML)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    # Deliberately do NOT set HMC_DEV_PW — show must not resolve it
    monkeypatch.delenv("HMC_DEV_PW", raising=False)
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["--profile", "dev", "config", "show"])
    assert result.exit_code == 0, result.output
    assert "password_configured" in result.output
    assert "true" in result.output.lower() or "True" in result.output


def test_show_json_flag(tmp_path, monkeypatch):
    """show --json emits valid JSON with no password key."""
    cfg = _write_toml(tmp_path / "hmc-mcp" / "config.toml", TWO_PROFILE_TOML)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["--profile", "prod", "config", "show", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "password" not in data
    assert "password_configured" in data
    assert data["password_configured"] is True
    assert data["host"] == "hmc.example.com"


def test_show_unknown_profile_error(tmp_path, monkeypatch):
    """show exits 1 with a message containing the unknown profile name."""
    cfg = _write_toml(tmp_path / "hmc-mcp" / "config.toml", TWO_PROFILE_TOML)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["--profile", "nonexistent", "config", "show"])
    assert result.exit_code == 1
    assert "nonexistent" in result.output


def test_show_absent_config_file_error(tmp_path, monkeypatch):
    """show exits 1 with a helpful message when no config file exists."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["--profile", "prod", "config", "show"])
    assert result.exit_code == 1


def test_show_no_profile_no_default_error(tmp_path, monkeypatch):
    """show exits 1 when no --profile, no HMC_PROFILE, no default_profile in TOML."""
    cfg = _write_toml(tmp_path / "hmc-mcp" / "config.toml", NO_DEFAULT_TOML)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    monkeypatch.delenv("HMC_HOST", raising=False)
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["config", "show"])
    assert result.exit_code == 1
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/app/test_cli_config.py -v 2>&1 | head -40
```

Expected: most tests fail with errors because `cli_config.py` is a stub with no commands.

#### Step 3 — Implement `cli_config.py`

- [ ] **Step 3: Write the full `cli_config.py` implementation**

Replace the stub `src/hmc_mcp/cli_config.py` with:

```python
"""Configuration subgroup commands for hmc-mcp.

hmc-mcp config init   — create the platform-native config file
hmc-mcp config list   — list configured profile names
hmc-mcp config show   — show non-secret connection metadata for a profile
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
from pathlib import Path
from typing import Any

import typer

from .cli_app import GLOBALS, _fail, config_app, console, err_console
from .config import (
    ConfigError,
    config_dir,
    list_profiles_with_default,
    load_profile,
    resolve_config_path,
)

_STARTER_TOML = """\
# hmc-mcp configuration — see README for the full schema
# default_profile = "prod"

[profiles.example]
host = "hmc.example.com"
user = "admin"
password_env = "HMC_PASSWORD"  # preferred: secret stays out of the file  # pragma: allowlist secret
# password = "..."             # alternative: literal password (less secure)
# verify_ssl = false
"""


@config_app.command("init")
def config_init() -> None:
    """Create the platform-native config file with a starter profile.

    Creates parent directories as needed. Refuses to overwrite an existing
    file. On POSIX systems, the new file is created with mode 0o600.
    """
    target = config_dir() / "config.toml"

    # Use resolve_config_path() as the authoritative existence check —
    # do not call os.path.exists() separately (would re-open TOCTOU window).
    if resolve_config_path() is not None:
        _fail(FileExistsError(f"Config file already exists: {target}"))

    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        # O_CREAT|O_EXCL: atomic exclusive create — fails if another process
        # raced us to the file between the existence check above and now.
        with open(target, "x", encoding="utf-8") as fh:
            fh.write(_STARTER_TOML)
    except FileExistsError:
        _fail(FileExistsError(f"Config file already exists: {target}"))

    # Apply restrictive permissions on POSIX; chmod is a no-op on Windows.
    if sys.platform != "win32":
        os.chmod(target, 0o600)

    console.print(str(target))


@config_app.command("list")
def config_list() -> None:
    """List configured profile names and indicate the default profile."""
    config_path = resolve_config_path()

    if config_path is None:
        # Compute what the path *would* be for the helpful message.
        would_be = config_dir() / "config.toml"
        console.print(f"No config file found at {would_be}")
        return

    try:
        names, default = list_profiles_with_default(config_path=config_path)
    except ConfigError as exc:
        _fail(exc)

    if not names:
        console.print("No profiles defined in config file.")
        return

    for name in names:
        marker = "  (default)" if name == default else ""
        console.print(f"{name}{marker}")


@config_app.command("show")
def config_show(
    profile: str | None = typer.Option(
        None,
        "--profile",
        help="Profile name to show (overrides global --profile)",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Show non-secret connection metadata for a profile.

    Reports host, port, user, and connection settings. Never emits literal
    passwords or resolves password_env. Reports only whether a password or
    SSH key credential is configured.
    """
    # Command --profile takes precedence over global --profile.
    effective_profile = profile or GLOBALS.profile

    config_path = resolve_config_path()
    if config_path is None:
        would_be = config_dir() / "config.toml"
        _fail(ConfigError(f"No config file found at {would_be}"))

    # Read the raw TOML dict to determine credential presence WITHOUT
    # resolving password_env (load_profile() resolves it, which requires
    # the env var to be present — a production secret may not be set locally).
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
    except tomllib.TOMLDecodeError as exc:
        _fail(ConfigError(f"{config_path}: TOML parse error: {exc}"))

    profile_dict: dict[str, Any] = raw.get("profiles", {}).get(effective_profile or "", {})  # type: ignore[assignment]

    # Gather credential presence booleans from raw dict — safe because we
    # never look at the password value, just whether the key is present.
    password_configured = bool(
        profile_dict.get("password") or profile_dict.get("password_env")
    )
    ssh_key_configured = bool(profile_dict.get("ssh_key_file"))

    # Load the full HMCConfig for non-secret fields. This call may raise
    # ConfigError (unknown profile, no default, etc.) — that is the intended
    # error path for those conditions.
    try:
        cfg = load_profile(profile=effective_profile, config_path=config_path)
    except ConfigError as exc:
        _fail(exc)

    # Gather all output fields before emitting anything (no partial output).
    data: dict[str, Any] = {
        "profile": effective_profile or "(default)",
        "host": cfg.host,  # type: ignore[union-attr]
        "port": cfg.port,  # type: ignore[union-attr]
        "user": cfg.user,  # type: ignore[union-attr]
        "verify_ssl": cfg.verify_ssl,  # type: ignore[union-attr]
        "timeout": cfg.timeout,  # type: ignore[union-attr]
        "audit_memento": cfg.audit_memento,  # type: ignore[union-attr]
        "schema_version": cfg.schema_version or "(not set)",  # type: ignore[union-attr]
        "password_configured": password_configured,
        "ssh_key_configured": ssh_key_configured,
    }

    if as_json:
        console.print_json(json.dumps(data))
    else:
        width = max(len(k) for k in data)
        for key, value in data.items():
            console.print(f"{key:<{width}}  {value}")
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/app/test_cli_config.py -v
```

Expected: all 11 tests pass.

- [ ] **Step 5: Run the full guardrail suite**

```bash
just verify
```

Expected: all checks pass. If detect-secrets fires on the starter TOML constant in `cli_config.py`, add `# pragma: allowlist secret` to the `password_env` line (already present in the constant string above).

- [ ] **Step 6: Commit**

```bash
git add src/hmc_mcp/cli_config.py tests/app/test_cli_config.py
git commit -m "feat(cli): implement config init/list/show commands (#125)"
```

---

### Task 4: Update secrets baseline if needed and final verification

**Files:**
- May modify: `.secrets.baseline` (if detect-secrets flags the starter TOML constant)

- [ ] **Step 1: Run `just verify` and check for secrets failures**

```bash
just verify
```

If detect-secrets fails on `cli_config.py`, run:

```bash
git ls-files -z | xargs -0 uv run detect-secrets-hook --baseline .secrets.baseline --no-verify --
```

If it still fails, the `# pragma: allowlist secret` comment on the `password_env` line in `_STARTER_TOML` in `cli_config.py` should silence it. Verify the line reads exactly:

```python
password_env = "HMC_PASSWORD"  # preferred: secret stays out of the file  # pragma: allowlist secret
```

If the baseline itself needs updating (new hash for an existing entry), run:

```bash
uv run detect-secrets scan --baseline .secrets.baseline
```

Then stage and commit:

```bash
git add .secrets.baseline
git commit -m "chore: update secrets baseline for cli_config starter TOML (#125)"
```

- [ ] **Step 2: Confirm `hmc-mcp config --help` renders correctly**

```bash
uv run hmc-mcp config --help
uv run hmc-mcp config init --help
uv run hmc-mcp config list --help
uv run hmc-mcp config show --help
```

Expected: all three subcommands visible, each with a one-line description.

- [ ] **Step 3: Run the full guardrail suite one final time**

```bash
just verify
```

Expected: green across all checks.

---

## Self-Review

**Spec coverage check:**

| Criterion | Covered by task |
|---|---|
| `config init` creates dirs, writes TOML, restrictive perms, refuses overwrite, prints path | Task 3 |
| `config list` prints profiles + marks default, exit 0 when absent | Task 3 |
| `config show` non-secret metadata, redacts password, no password_env resolution, --json | Task 3 |
| Exit codes: 0 success, 1 failure | Task 3 |
| `--profile` selects profile for show | Task 3 |
| All 11 test cases from spec test plan | Task 3 |
| `just verify` passes | Tasks 1, 2, 3, 4 |
| `config_dir()` needed by init | Task 1 |
| `list_profiles_with_default()` needed by list (single TOML read) | Task 1 |
| `config_app` registered in CLI | Task 2 |

**Placeholder scan:** None. All steps have exact code and expected output.

**Type consistency:**
- `config_dir()` → `Path` — used as such in Task 3
- `list_profiles_with_default()` → `tuple[list[str], str | None]` — destructured as `names, default` in Task 3
- `config_app` — imported from `cli_app` in Task 3's `cli_config.py`
- `GLOBALS`, `_fail`, `console`, `err_console` — all from `cli_app`, already present there
