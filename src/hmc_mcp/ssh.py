"""SSH transport and session mechanics for HMC CLI execution."""

from __future__ import annotations

import asyncio

import asyncssh

from .config import HMCConfig
from .errors import HMCError


class HMCCLIError(HMCError):
    """An HMC CLI operation failed or was refused before execution."""


async def run_hmc_command(config: HMCConfig, cmd: str) -> str:
    """Execute one HMC CLI command over SSH and return its stdout."""
    config.validate_credentials(require_password=not config.ssh_key_file)
    connect_kwargs: dict = {
        "host": config.host,
        "username": config.user,
        "known_hosts": None,
    }
    if config.ssh_key_file:
        connect_kwargs["client_keys"] = [config.ssh_key_file]
        connect_kwargs["password"] = None
    else:
        connect_kwargs["password"] = config.password

    try:
        async with asyncio.timeout(config.ssh_timeout):
            async with asyncssh.connect(**connect_kwargs) as connection:
                result = await connection.run(
                    cmd, check=True, timeout=config.ssh_timeout
                )
                stdout = result.stdout
                if isinstance(stdout, bytes):
                    return stdout.decode()
                return stdout or ""
    except TimeoutError as exc:
        raise HMCCLIError(
            f"SSH command timed out after {config.ssh_timeout:.0f}s: {cmd!r}. "
            "The HMC CLI may be hung or the HMC may be under load."
        ) from exc
    except asyncssh.Error as exc:
        detail = (
            getattr(exc, "stderr", None) or getattr(exc, "stdout", None) or str(exc)
        )
        raise HMCCLIError(f"SSH command failed: {detail.strip()}") from exc


async def run_hmc_cli(cmd: str, config: HMCConfig | None = None) -> str:
    """Execute a command using an explicit or environment-derived config."""
    return await run_hmc_command(config if config is not None else HMCConfig(), cmd)
