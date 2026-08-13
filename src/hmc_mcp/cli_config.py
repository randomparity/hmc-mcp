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
from typing import Any

import typer

from . import cli_app
from .cli_app import _fail, config_app, console
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
    # resolve_config_path() returns non-None only when the file exists.
    if resolve_config_path() is not None:
        _fail(FileExistsError(f"Config file already exists: {target}"))

    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        if sys.platform != "win32":
            # O_CREAT|O_EXCL|mode=0o600: atomic exclusive create with restrictive
            # permissions — no create-then-chmod window.  The mode is set before
            # any other process can open the file descriptor.
            fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(_STARTER_TOML)
        else:
            # Windows: os.open mode bits are no-ops; use plain open() with O_EXCL
            # semantics via 'x' mode.  The file inherits the user-account ACL from
            # %APPDATA%, which is the accepted Windows security posture (ADR-0007).
            with open(target, "x", encoding="utf-8") as fh:
                fh.write(_STARTER_TOML)
    except FileExistsError:
        _fail(FileExistsError(f"Config file already exists: {target}"))

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
        return  # unreachable but satisfies type checker

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
    # Reference cli_app.GLOBALS dynamically — importing GLOBALS directly would
    # capture the initial empty GlobalOpts instance; the root callback replaces
    # cli_app.GLOBALS on each invocation.
    effective_profile = profile or cli_app.GLOBALS.profile

    config_path = resolve_config_path()
    if config_path is None:
        _fail(ConfigError(f"No config file found at {config_dir() / 'config.toml'}"))

    assert config_path is not None  # _fail raises; narrowing for type checker
    # Read the raw TOML dict to determine credential presence WITHOUT
    # resolving password_env (load_profile() resolves it, which requires
    # the env var to be present — a production secret may not be set locally).
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        _fail(ConfigError(f"{config_path}: TOML parse error: {exc}"))
        return  # unreachable but satisfies type checker

    # When effective_profile is None, load_profile() will use default_profile
    # from the TOML. We need to resolve that same name for the raw dict lookup.
    _resolved_profile = effective_profile or raw.get("default_profile")
    profile_dict: dict[str, Any] = raw.get("profiles", {}).get(_resolved_profile or "", {})

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
        return  # unreachable but satisfies type checker

    # Gather all output fields before emitting anything (no partial output).
    data: dict[str, Any] = {
        "profile": _resolved_profile or "(default)",
        "host": cfg.host,
        "port": cfg.port,
        "user": cfg.user,
        "verify_ssl": cfg.verify_ssl,
        "timeout": cfg.timeout,
        "audit_memento": cfg.audit_memento,
        "schema_version": cfg.schema_version or "(not set)",
        "password_configured": password_configured,
        "ssh_key_configured": ssh_key_configured,
    }

    if as_json:
        console.print_json(json.dumps(data))
    else:
        width = max(len(k) for k in data)
        for key, value in data.items():
            console.print(f"{key:<{width}}  {value}")
