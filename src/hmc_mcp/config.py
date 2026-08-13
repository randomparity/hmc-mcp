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

import os
import sys
import tomllib
import warnings
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
            "hmc-mcp/<agent_id>. Used for multi-agent LPAR ownership attribution. "
            "Must be 1–64 printable ASCII characters with no commas, = signs, or "
            "square brackets. (HMC_AGENT_ID)"
        ),
    )

    @field_validator("agent_id")
    @classmethod
    def _validate_agent_id_field(cls, v: str | None) -> str | None:
        if v is not None and v != "":
            from .ssh import validate_agent_id  # deferred — ssh imports config; avoid circular
            validate_agent_id(v)
        return v

    @model_validator(mode="after")
    def _warn_audit_memento_override(self) -> "HMCConfig":
        """Warn when HMC_AGENT_ID is set and HMC_AUDIT_MEMENTO has been customised.

        When both are set, effective_audit_memento returns ``hmc-mcp/<agent_id>``
        and ignores the custom audit_memento.  Emitting a warning at construction
        time prevents silent surprises in HMC audit logs.
        """
        if self.agent_id and self.audit_memento != "hmc-mcp":
            warnings.warn(
                f"HMC_AGENT_ID is set ({self.agent_id!r}); the custom "
                f"HMC_AUDIT_MEMENTO value ({self.audit_memento!r}) will be "
                "ignored — X-Audit-Memento is always sent as "
                f"hmc-mcp/{self.agent_id}",
                UserWarning,
                stacklevel=2,
            )
        return self

    @property
    def effective_audit_memento(self) -> str:
        """Audit memento value sent in the X-Audit-Memento header.

        Returns ``hmc-mcp/<agent_id>`` when ``agent_id`` is set and non-empty;
        otherwise returns ``audit_memento`` (default ``"hmc-mcp"``).

        Note: when ``agent_id`` is set, ``audit_memento`` is ignored — the prefix
        is always ``hmc-mcp``.  An operator who has customised ``HMC_AUDIT_MEMENTO``
        and then sets ``HMC_AGENT_ID`` will see the audit prefix revert to
        ``hmc-mcp``.
        """
        if self.agent_id:
            return f"hmc-mcp/{self.agent_id}"
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


def list_profiles(config_path: Path | None = None) -> list[str]:
    """Return profile names from the config file; empty list when absent.

    Never resolves secrets — safe for tab-completion and diagnostics.
    """
    path = config_path if config_path is not None else resolve_config_path()
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
        ConfigError: When the file cannot be parsed, no profile is selected,
            the selected profile is absent, or secret config is invalid.
    """
    path = config_path if config_path is not None else resolve_config_path()

    # Determine selected profile name
    name = profile or os.environ.get("HMC_PROFILE")
    doc: dict = {}

    if path is not None and path.exists():
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
        raise ConfigError(
            f"profile {name!r} not found in {path}; check the [profiles] table"
        )

    entry = dict(profiles[name])

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
