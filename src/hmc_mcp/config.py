"""Configuration for hmc-mcp.

Settings are resolved in priority order:
  1. CLI options / explicit constructor args
  2. Environment variables (HMC_*)
  3. .env file in the current directory (auto-loaded)
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class HMCConfig(BaseSettings):
    """Connection settings for an IBM HMC."""

    model_config = SettingsConfigDict(
        env_prefix="HMC_",
        env_file=".env",
        env_file_encoding="utf-8",
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
