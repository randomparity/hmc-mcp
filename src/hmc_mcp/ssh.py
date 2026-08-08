"""SSH helpers for executing HMC CLI commands over SSH.

HMC CLI reference:
    https://www.ibm.com/docs/en/power10/7063-CR1?topic=hmc-commands
"""

from __future__ import annotations

import asyncssh

from .config import HMCConfig


async def run_hmc_command(config: HMCConfig, cmd: str) -> str:
    """Execute an HMC CLI command over SSH and return its stdout + stderr.

    Authentication:
    - If ``config.ssh_key_file`` is set, key-based auth is attempted using
      that private-key file (passphrase-protected keys are not supported).
    - Otherwise password auth is used via ``config.password``.

    Args:
        config: HMC connection settings.
        cmd: The HMC CLI command string to run.

    Returns:
        The combined stdout of the command.

    Raises:
        asyncssh.Error: On SSH connection or command execution failure.
    """
    connect_kwargs: dict = {
        "host": config.host,
        "username": config.user,
        "known_hosts": None,  # HMC hosts are not in known_hosts by default
    }
    if config.ssh_key_file:
        connect_kwargs["client_keys"] = [config.ssh_key_file]
        connect_kwargs["password"] = None
    else:
        connect_kwargs["password"] = config.password

    async with asyncssh.connect(**connect_kwargs) as conn:
        result = await conn.run(cmd, check=True)
        return result.stdout
