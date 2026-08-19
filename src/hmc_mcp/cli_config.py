"""Configuration subgroup commands for hmc-mcp.

hmc-mcp config init                — create the platform-native config file
hmc-mcp config list                — list configured profile names
hmc-mcp config show                — show non-secret connection metadata for a profile
hmc-mcp config init-access-policy  — generate a legacy-equivalent server access policy

Two different files live under this group, and they are not two spellings of one
thing. ``config.toml`` holds **HMC connection profiles**: which consoles this
installation can reach, and how. ``access-policy.toml`` holds **server access
policies**: what an MCP server composed from this package may do with them. Separate
files, separate lifecycles; a policy grant's ``connections`` entries are profile
*keys* from the first file, never profile contents.
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
from pathlib import Path
from typing import Any

import typer
from rich.markup import escape

from . import cli_app
from .access_policy import AccessPolicyError
from .cli_app import _fail, _policy_file, config_app, console, err_console
from .config import (
    ConfigError,
    config_dir,
    list_nicknames,
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

# Optional friendly nicknames map a memorable name to an existing profile key.
# A nickname works anywhere a profile name does (--profile / HMC_PROFILE /
# default_profile). A profile key always wins on a name collision; resolution
# is one level deep (no chained nicknames); matching is case-sensitive.
# nicknames = { "big-iron" = "example" }
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

    if not names:
        console.print("No profiles defined in config file.")
        return

    for name in names:
        marker = "  (default)" if name == default else ""
        console.print(f"{name}{marker}")

    # Surface nicknames (secret-free): each maps to a profile key, flagged if
    # its target does not exist.
    try:
        nicknames = list_nicknames(config_path=config_path)
    except ConfigError as exc:
        _fail(exc)

    for nick, target in nicknames.items():
        status = "" if target in names else "  (no such profile)"
        console.print(f"{nick} -> {target}{status}")


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
    # Command --profile takes precedence over the invocation's root option.
    effective_profile = profile or cli_app._current_options().profile

    config_path = resolve_config_path()
    if config_path is None:
        _fail(ConfigError(f"No config file found at {config_dir() / 'config.toml'}"))

    # Read the raw TOML dict to determine credential presence WITHOUT
    # resolving password_env (load_profile() resolves it, which requires
    # the env var to be present — a production secret may not be set locally).
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        _fail(ConfigError(f"{config_path}: TOML parse error: {exc}"))

    # When effective_profile is None, load_profile() uses default_profile.
    # Resolve the same name here so the raw dict lookup and the display agree
    # with load_profile(), and surface a nickname resolution transparently.
    requested = effective_profile or raw.get("default_profile")
    profiles_raw = raw.get("profiles", {})

    try:
        nicknames = list_nicknames(config_path=config_path)
    except ConfigError as exc:
        _fail(exc)

    # One level deep, case-sensitive; a profile key always wins because this
    # branch only runs when the requested name is not already a profile key.
    resolved_name = requested
    resolved_from: str | None = None
    if requested is not None and requested not in profiles_raw and requested in nicknames:
        target = nicknames[requested]
        if target in profiles_raw:
            resolved_name = target
            resolved_from = requested

    profile_dict = profiles_raw.get(resolved_name or "", {})

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
        "profile": resolved_name or "(default)",
        "resolved_from": resolved_from,
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


def _write_exclusive(target: Path, text: str) -> None:
    """Create *target* with *text*, refusing to overwrite, leaving no partial file.

    ``O_EXCL`` gives "no partial file" only for a failure at ``open``. A write or close
    that fails afterwards — ENOSPC, EDQUOT, EIO — leaves a truncated file, and a
    truncated file here is worse than elsewhere: it exists, so this command's own
    no-overwrite rule refuses to regenerate over it, and it does not compile, so
    ``serve`` refuses too. So the descriptor's contents are the unit of work: if
    anything after the create fails, the destination is unlinked before the error is
    reported.

    A signal or an OOM kill between the create and the flush can still leave one; no
    handler runs there. The README's migration section carries that recovery — delete
    the file and re-run.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        # O_CREAT|O_EXCL|0o600: atomic exclusive create with restrictive permissions,
        # the same shape `config init` uses — no create-then-chmod window.
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
    else:
        # Windows: os.open mode bits are no-ops; 'x' gives the same O_EXCL semantics,
        # and the file inherits the user-account ACL from %APPDATA% (ADR 0007).
        try:
            with open(target, "x", encoding="utf-8") as handle:
                handle.write(text)
        except FileExistsError:
            raise
        except BaseException:
            target.unlink(missing_ok=True)
            raise


@config_app.command("init-access-policy")
def config_init_access_policy(
    output: str | None = typer.Option(
        None,
        "--output",
        metavar="PATH",
        help="Write to PATH instead of the platform-native access-policy.toml. This "
        "is how you regenerate: the command never overwrites, so generate to a "
        "scratch path, diff it against the deployed policy, and merge by hand.",
    ),
) -> None:
    """Generate a legacy-equivalent server access policy for review.

    Writes the policy an existing deployment needs after an access policy became
    mandatory: every ordinary tool, every configured connection, no target
    restriction. It is a migration aid, not a recommended posture — a new deployment
    should start from the read-only example in the README.

    It activates nothing. Review the file, then pass its name to
    ``hmc-mcp serve --access-policy legacy-equivalent``.

    Never overwrites an existing file. Run it as the identity and with the environment
    ``serve`` runs under: both resolve the file through the same config directory, and
    the connection list is read from that identity's ``config.toml``.
    """
    from .legacy_policy import (
        LEGACY_POLICY_NAME,
        compile_rendered_policy,
        legacy_connections,
        render_legacy_policy,
    )
    from .server import TOOL_SECURITY

    if output is not None:
        target = Path(output)
    else:
        resolved = _policy_file()
        if resolved is None:
            _fail(
                RuntimeError(
                    "cannot resolve the access-policy path: no home directory. Set "
                    "HOME or XDG_CONFIG_HOME for the identity this runs as, or pass "
                    "--output PATH."
                )
            )
        target = Path(resolved[0])

    try:
        connections = legacy_connections()
    except ConfigError as exc:
        _fail(exc)

    text = render_legacy_policy(TOOL_SECURITY, connections)

    # Load what was rendered, before anything is written. Escaping makes the document
    # parse; ADR 0036 enforces rules on entry *content* that escaping cannot satisfy —
    # an empty or whitespace-padded profile key is legal TOML and an illegal connection
    # entry. Compiling through the real loader keeps those rules in one place instead
    # of copying them here, where they would drift.
    try:
        compile_rendered_policy(text, TOOL_SECURITY)
    except AccessPolicyError as exc:
        # Every noun in the loader's message belongs to a document that was never
        # written, while the operator's actual edit is a profile key — so the origin
        # and the remedy are named alongside it.
        _fail(
            AccessPolicyError(
                f"{exc}\n\nThat entry came from a profile key in config.toml. Remove "
                "the padding from the profile key, or generate elsewhere with "
                "--output PATH and edit the connections list by hand."
            )
        )

    try:
        _write_exclusive(target, text)
    except FileExistsError:
        _fail(
            FileExistsError(
                f"Access policy file already exists: {target}. This command never "
                "overwrites a reviewed policy. To regenerate, write to a scratch path "
                "with --output PATH, diff it against this file, and merge by hand."
            )
        )
    except OSError as exc:
        _fail(exc)

    # Escaped for the reason `_fail` escapes: these render through a markup-enabled
    # rich Console, and under --output the path is the operator's own. A bracketed
    # path would print with the bracketed segment silently deleted — so the operator
    # copies a path that does not exist — and a `[/x]`-shaped one would raise
    # MarkupError in place of the success line.
    console.print(escape(str(target)))
    err_console.print(
        "Review it, then start the server with: hmc-mcp serve --access-policy "
        f"{LEGACY_POLICY_NAME}"
    )
