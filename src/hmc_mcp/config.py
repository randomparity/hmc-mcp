"""Configuration for hmc-mcp.

Settings are resolved in priority order:
  1. CLI options / explicit constructor args
  2. Environment variables (HMC_*)
  3. TOML profile (~/.config/hmc-mcp/config.toml or platform equivalent)

Checkout-local .env files are NOT loaded.

Use load_profile() to load a named profile from the platform-native config file.
Use HMCConfig(...) directly for explicit construction (tests, programmatic use).
"""

from __future__ import annotations

import logging
import os
import sys
import tomllib
import warnings
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_logger = logging.getLogger(__name__)


def validate_agent_id(agent_id: str) -> None:
    """Validate an agent identifier used in audit and ownership tokens."""
    if not agent_id:
        raise ValueError("agent_id must not be empty")
    if agent_id == "hmc-mcp":
        raise ValueError("agent_id 'hmc-mcp' is reserved; choose a distinct identifier")
    if len(agent_id) > 64:
        raise ValueError(f"agent_id is {len(agent_id)} characters; maximum is 64")
    if not agent_id.isascii() or any(
        ord(character) < 0x20 or ord(character) == 0x7F for character in agent_id
    ):
        raise ValueError(
            "agent_id contains non-ASCII or non-printable characters; "
            "only printable ASCII is accepted"
        )
    forbidden = {
        ",": "commas corrupt the HMC CLI -i parser",
        "=": "equals signs corrupt the HMC CLI -i parser",
        "[": "brackets break the ownership token format",
        "]": "brackets break the ownership token format",
        "/": "the HMC REST API rejects '/' in X-Audit-Memento",
        ":": "colons make audit and ownership token formats ambiguous",
        " ": "spaces corrupt the ownership token in the CLI -i parser",
    }
    for character, reason in forbidden.items():
        if character in agent_id:
            raise ValueError(f"agent_id contains {character!r}; {reason}")


class HMCConfig(BaseSettings):
    """Connection settings for an IBM HMC."""

    model_config = SettingsConfigDict(
        env_prefix="HMC_",
        extra="ignore",
    )

    host: str = Field(default="", description="HMC hostname or IP address")
    port: int = Field(default=12443, description="HMC REST API port")
    user: str = Field(default="", description="HMC user name")
    password: str = Field(default="", description="HMC password")
    ssh_key_file: str | None = Field(default=None, description="Path to SSH private key file (HMC_SSH_KEY_FILE)")
    verify_ssl: bool = Field(default=False, description="Verify the HMC TLS certificate")
    timeout: float = Field(default=60.0, description="HTTP timeout in seconds")
    ssh_timeout: float = Field(
        default=300.0,
        description="SSH command timeout in seconds (HMC CLI ops are slower "
        "than REST calls, e.g. bkprofdata/rstprofdata; 60s is too tight)",
    )
    audit_memento: str = Field(
        default="hmc-mcp",
        description="Value sent in the X-Audit-Memento header (shows up in HMC audit logs)",
    )
    schema_version: str = Field(
        default="",
        description=(
            "Schema version sent as X-HMC-Schema-Version request header "
            "(e.g. 'V1_0'). Empty string disables the header (default). "
            "HMC V8/V9 targets do not need this; uom documents already declare "
            "schemaVersion=V1_0. Set it only to pin negotiation explicitly."
        ),
    )
    agent_id: str | None = Field(
        default=None,
        description=(
            "Per-agent identifier folded into the X-Audit-Memento header as "
            "hmc-mcp:<agent_id>. Used for multi-agent LPAR ownership attribution. "
            "Must be 1–64 printable ASCII characters with no commas, = signs, or "
            "square brackets. (HMC_AGENT_ID)"
        ),
    )

    @field_validator("agent_id")
    @classmethod
    def _validate_agent_id_field(cls, v: str | None) -> str | None:
        if v is not None and v != "":
            validate_agent_id(v)
        return v

    @model_validator(mode="after")
    def _warn_audit_memento_override(self) -> "HMCConfig":
        """Warn when HMC_AGENT_ID is set and HMC_AUDIT_MEMENTO has been customised.

        When both are set, effective_audit_memento returns ``hmc-mcp:<agent_id>``
        and ignores the custom audit_memento.  Emitting a warning at construction
        time prevents silent surprises in HMC audit logs.
        """
        if self.agent_id and self.audit_memento != "hmc-mcp":
            msg = (
                f"HMC_AGENT_ID is set ({self.agent_id!r}); the custom "
                f"HMC_AUDIT_MEMENTO value ({self.audit_memento!r}) will be "
                "ignored — X-Audit-Memento is always sent as "
                f"hmc-mcp:{self.agent_id}"
            )
            warnings.warn(msg, UserWarning, stacklevel=2)
            _logger.warning(msg)
        return self

    @property
    def effective_audit_memento(self) -> str:
        """Audit memento value sent in the X-Audit-Memento header.

        Returns ``hmc-mcp:<agent_id>`` when ``agent_id`` is set and non-empty;
        otherwise returns ``audit_memento`` (default ``"hmc-mcp"``).

        Note: when ``agent_id`` is set, ``audit_memento`` is ignored — the prefix
        is always ``hmc-mcp``.  An operator who has customised ``HMC_AUDIT_MEMENTO``
        and then sets ``HMC_AGENT_ID`` will see the audit prefix revert to
        ``hmc-mcp``.
        """
        if self.agent_id:
            return f"hmc-mcp:{self.agent_id}"
        return self.audit_memento

    @property
    def base_url(self) -> str:
        host = self.host.removeprefix("https://").removeprefix("http://").rstrip("/")
        return f"https://{host}:{self.port}"

    def validate_credentials(self, require_password: bool = True) -> None:
        """Raise ValueError naming any missing connection settings.

        ``require_password`` is False for key-based SSH auth, where a private
        key replaces the password; the REST path always requires it.
        """
        missing = []
        if not self.host:
            missing.append("host (HMC_HOST / --host)")
        if not self.user:
            missing.append("user (HMC_USER / --user)")
        if require_password and not self.password:
            missing.append("password (HMC_PASSWORD / --password)")
        if missing:
            raise ValueError(
                "Missing HMC configuration: " + ", ".join(missing)
            )


class ConfigError(ValueError):
    """Raised when hmc-mcp/config.toml is invalid or a profile cannot be selected."""


def resolve_config_path() -> Path | None:
    """Return the platform-native config.toml path, or None when absent.

    Platform resolution:
    - Linux/other POSIX: $XDG_CONFIG_HOME/hmc-mcp/config.toml
      (fallback: ~/.config/hmc-mcp/config.toml)
    - macOS:  ~/Library/Application Support/hmc-mcp/config.toml
    - Windows: %APPDATA%/hmc-mcp/config.toml
      (fallback: ~/.config/hmc-mcp/config.toml)
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        base = Path(appdata) if appdata else Path.home() / ".config"
    else:
        # Linux / other POSIX: honour XDG_CONFIG_HOME
        xdg = os.environ.get("XDG_CONFIG_HOME", "")
        base = Path(xdg) if xdg else Path.home() / ".config"

    p = base / "hmc-mcp" / "config.toml"
    return p if p.exists() else None


def config_dir() -> Path:
    """Return the platform-native hmc-mcp/ config directory (no existence check).

    Same platform resolution as resolve_config_path() but never checks whether
    the directory or file exists. Used by ``config init`` to compute the target
    path.
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


def _selected_config_path(config_path: Path | None) -> Path | None:
    """Return *config_path*, or the platform-native path when it is None.

    Raises ConfigError when the platform-native path cannot be resolved:
    :func:`resolve_config_path` reaches ``Path.home()``, which raises
    RuntimeError under a uid with no passwd entry and no HOME — a container or a
    systemd unit. ``access_policy.load_access_policy`` guards the same case.
    """
    if config_path is not None:
        return config_path
    try:
        return resolve_config_path()
    except (RuntimeError, ValueError) as exc:
        raise ConfigError(f"cannot resolve the config path: {exc}") from exc


def _read_config_document(path: Path) -> dict[str, Any]:
    """Read and parse *path*, converting every failure into a ConfigError.

    Returns ``{}`` when the file is absent: an absent config file is an empty
    configuration everywhere it is read. There is deliberately no ``exists()``
    pre-check — that is a TOCTOU, and the absent case is the FileNotFoundError
    arm below.

    Every other failure is a ConfigError naming *path*, so the callers that
    document ConfigError as their failure type tell the truth and a
    ``try/except ConfigError`` around one of them actually catches. This is the
    single read-and-parse for config.toml; see
    ``access_policy.load_access_policy`` for the same conversion over the
    access-policy file.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path}: is not valid UTF-8: {exc}") from exc
    except (OSError, ValueError) as exc:
        # A directory at the path or an unreadable mode lands in OSError;
        # ValueError covers an unusable path string, such as an embedded null
        # byte, which read_text raises before it reaches the filesystem.
        raise ConfigError(f"{path}: cannot be read: {exc}") from exc
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: TOML parse error: {exc}") from exc
    except RecursionError as exc:
        # tomllib recurses on nested arrays and inline tables, so a deeply nested
        # document exhausts the stack before it can report a syntax error. A
        # RecursionError carries no message, hence the fixed clause.
        raise ConfigError(
            f"{path}: TOML parse error: document nesting is too deep"
        ) from exc


def _coerce_profiles(raw: Any, path: str | Path | None) -> dict[str, Any]:
    """Validate and return the ``profiles`` table as ``dict[str, Any]``.

    ``raw`` is the parsed value of the top-level ``profiles`` key (or None when
    absent). Mirrors :func:`_coerce_nicknames` for the other half of profile
    selection: without it ``doc.get("profiles", {}).keys()`` raises an
    AttributeError from the middle of a dict access when the key is not a table,
    and ``name not in profiles`` quietly degrades into a substring test.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(
            f"{path}: 'profiles' must be a table of profile name to settings, "
            f"got {type(raw).__name__}"
        )
    # TOML keys are always strings; str() states that for the type checker, which
    # sees only the untyped mapping the isinstance check above narrowed to.
    return {str(name): entry for name, entry in raw.items()}


def list_profiles_with_default(
    config_path: Path | None = None,
) -> tuple[list[str], str | None]:
    """Return (profile_names, default_profile_or_none) from one TOML read.

    Never resolves secrets — safe for diagnostics.
    Returns ([], None) when the file is absent or path is None.
    Raises ConfigError on every read, decode, parse, or structure failure.
    """
    path = _selected_config_path(config_path)
    if path is None:
        return [], None
    doc = _read_config_document(path)
    return list(_coerce_profiles(doc.get("profiles"), path)), doc.get(
        "default_profile"
    )


def list_profiles(config_path: Path | None = None) -> list[str]:
    """Return profile names from the config file; empty list when absent.

    Never resolves secrets — safe for tab-completion and diagnostics.
    Raises ConfigError on every read, decode, parse, or structure failure.
    """
    return list_profiles_with_default(config_path=config_path)[0]


def _coerce_nicknames(raw: Any, path: str | Path | None) -> dict[str, str]:
    """Validate and return the ``nicknames`` table as ``dict[str, str]``.

    ``raw`` is the parsed value of the top-level ``nicknames`` key (or None
    when absent). Raises ``ConfigError`` naming ``path`` when the table is not
    a mapping or when any target value is not a string, so a malformed table
    fails loudly instead of silently shadowing profile selection.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(
            f"{path}: 'nicknames' must be a table mapping a friendly name to a "
            f"profile key, got {type(raw).__name__}"
        )
    for key, target in raw.items():
        if not isinstance(target, str):
            raise ConfigError(
                f"{path}: nickname {key!r} must map to a profile-key string, "
                f"got {type(target).__name__}"
            )
    return dict(raw)


def list_nicknames(config_path: Path | None = None) -> dict[str, str]:
    """Return the nicknames table as ``dict[str, str]``; empty when absent.

    Never resolves secrets - safe for diagnostics and display.
    Returns ``{}`` when the file is absent or path is None.
    Raises ConfigError on every read, decode, parse, or structure failure.
    """
    path = _selected_config_path(config_path)
    if path is None:
        return {}
    return _coerce_nicknames(_read_config_document(path).get("nicknames"), path)


def list_profiles_and_nicknames(
    config_path: Path | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Return (profile_names, nicknames) from one TOML read.

    Never resolves secrets — safe for diagnostics and for authorization decisions
    that must mirror :func:`load_profile`'s selection order. One read rather than
    ``list_profiles()`` plus ``list_nicknames()`` so that the two halves of a
    single decision cannot be taken from two different versions of the file.

    Returns ``([], {})`` when the file is absent or *config_path* is None.
    Every other failure is a ConfigError: this feeds ADR 0038's authorization
    decision, whose denial message must interpolate no path and no raw exception
    text, so an unreadable file, a non-UTF-8 one, and a malformed table must all
    arrive as one recognizable type rather than as an OSError or an
    AttributeError from the middle of a dict access.
    """
    path = _selected_config_path(config_path)
    if path is None:
        return [], {}
    doc = _read_config_document(path)
    return list(_coerce_profiles(doc.get("profiles"), path)), _coerce_nicknames(
        doc.get("nicknames"), path
    )


def _load_profile_from_document(
    doc: dict[str, Any],
    path: Path | None,
    profile: str | None = None,
) -> HMCConfig:
    """Build an HMCConfig for *profile* from an already-parsed *doc*.

    Shared by :func:`load_profile`, which reads and parses *path* itself, and
    by a caller that already holds the parsed document for this invocation —
    such as ``config_show``, which needs the same document for credential
    presence and nickname resolution and must not parse ``config.toml`` a
    second time to also select a profile (issue #295). *path* is used only for
    error messages; it is not re-read here.
    """
    name = profile or os.environ.get("HMC_PROFILE")
    if name is None:
        name = doc.get("default_profile")

    if name is None:
        raise ConfigError(
            f"{path or 'config.toml'}: no default_profile set and no "
            "--profile / HMC_PROFILE supplied"
        )

    profiles = _coerce_profiles(doc.get("profiles"), path)

    # Validate the nicknames table structure whenever the key is
    # present, so a malformed table is a ConfigError regardless of
    # which profile is selected (ADR 0030). No existing config
    # carries a nicknames key, so this cannot break current users.
    nicknames = _coerce_nicknames(doc.get("nicknames"), path)

    # Nickname resolution: one level deep, case-sensitive. A profile
    # key always wins over a same-named nickname because the branch
    # below only runs when the name is not already a profile key.
    if name not in profiles:
        if name in nicknames:
            target = nicknames[name]
            if target in profiles:
                name = target
            else:
                profile_names = ", ".join(sorted(profiles)) or "(none)"
                nickname_names = ", ".join(sorted(nicknames)) or "(none)"
                raise ConfigError(
                    f"nickname {name!r} targets missing profile {target!r} "
                    f"in {path}; available profiles: {profile_names}; "
                    f"available nicknames: {nickname_names}"
                  )
        else:
            profile_names = ", ".join(sorted(profiles)) or "(none)"
            nickname_names = ", ".join(sorted(nicknames)) or "(none)"
            raise ConfigError(
                f"profile {name!r} not found in {path}; "
                f"available profiles: {profile_names}; "
                f"available nicknames: {nickname_names}"
              )

    selected = profiles[name]
    if not isinstance(selected, dict):
        # Without this, dict() on a non-table raises a bare ValueError whose
        # message is about update sequences, not about the config file.
        raise ConfigError(
            f"{path}: profile {name!r} must be a table of settings, "
            f"got {type(selected).__name__}"
        )
    entry = dict(selected)

    # Validate and resolve secret fields
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

    # Build HMCConfig with correct precedence:
    #   explicit constructor args > HMC_* env vars > TOML profile values
    # pydantic-settings gives init-kwargs the highest priority, so we must NOT
    # pass TOML values as init-kwargs when a matching HMC_* env var is set —
    # otherwise the TOML value would win over the env var.
    env_prefix = "HMC_"
    filtered_entry = {
        k: v
        for k, v in entry.items()
        if (env_prefix + k.upper()) not in os.environ
    }
    return HMCConfig(_env_file=None, **filtered_entry)  # ty: ignore[unknown-argument]


def load_profile(
    profile: str | None = None,
    config_path: Path | None = None,
) -> HMCConfig:
    """Load and return an HMCConfig for the selected profile.

    Profile selection order:
      1. explicit ``profile`` argument
      2. ``HMC_PROFILE`` environment variable
      3. ``default_profile`` key in the TOML file
      4. ConfigError

    Precedence (highest to lowest):
      explicit HMCConfig constructor args > HMC_* env vars > TOML profile values

    Checkout-local .env files are NOT loaded.

    Args:
        profile: Profile name to select, or None to use env/TOML default.
        config_path: Override the config file path (for testing).

    Returns:
        HMCConfig populated from the selected profile with env-var overrides.

    Raises:
        ConfigError: When the file cannot be read, decoded, or parsed, when a
            table it needs is malformed, when no profile is selected, when the
            selected profile is absent, or when secret config is invalid.
    """
    path = _selected_config_path(config_path)
    doc: dict[str, Any] = {} if path is None else _read_config_document(path)
    return _load_profile_from_document(doc, path, profile)
