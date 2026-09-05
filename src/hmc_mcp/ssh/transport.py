"""SSH transport and session mechanics for HMC CLI execution."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import asyncssh

from ..config import HMCConfig
from ..errors import HMCError


class HMCCLIError(HMCError):
    """An HMC CLI operation failed or was refused before execution."""


def _connect_kwargs(config: HMCConfig) -> dict[str, Any]:
    """Build the ``asyncssh.connect`` keyword arguments for *config*.

    When authenticating with a password (no ``ssh_key_file`` set) we suppress
    all local key attempts and request password-only auth.  HMC appliances
    enforce a low ``MaxAuthTries`` limit; exhausting it with every agent key
    before the password attempt triggers a lockout (HMC_ACCESS.md).
    """
    config.validate_credentials(require_password=not config.ssh_key_file)
    connect_kwargs: dict[str, Any] = {
        "host": config.host,
        "username": config.user,
        "known_hosts": (
            str(Path.home() / ".ssh" / "known_hosts") if config.ssh_verify_host_key else None
        ),
    }
    if not config.ssh_verify_host_key:
        logging.getLogger(__name__).warning(
            "SSH host-key verification disabled for %s (ssh_verify_host_key=false)", config.host
        )
    if config.ssh_key_file:
        connect_kwargs["client_keys"] = [config.ssh_key_file]
        connect_kwargs["password"] = None
    else:
        connect_kwargs["password"] = config.password
        connect_kwargs["client_keys"] = []
        connect_kwargs["preferred_auth"] = "password"
    return connect_kwargs


async def run_hmc_command(config: HMCConfig, cmd: str) -> str:
    """Execute one HMC CLI command over SSH and return its stdout."""
    try:
        async with asyncio.timeout(config.ssh_timeout):
            async with asyncssh.connect(**_connect_kwargs(config)) as connection:
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
    except OSError as exc:
        raise HMCCLIError(
            f"SSH command connection failed for {cmd!r}: {exc}"
        ) from exc
    except asyncssh.ProcessError as exc:
        detail = exc.stderr or exc.stdout or str(exc)
        if exc.exit_status is not None:
            termination = f"exit status {exc.exit_status}"
        elif exc.exit_signal:
            termination = f"signal {exc.exit_signal}"
        else:
            termination = f"return code {exc.returncode}"
        raise HMCCLIError(
            f"SSH command {cmd!r} failed with {termination}: {detail.strip()}"
        ) from exc
    except asyncssh.Error as exc:
        detail = (
            getattr(exc, "stderr", None) or getattr(exc, "stdout", None) or str(exc)
        )
        raise HMCCLIError(f"SSH command failed: {detail.strip()}") from exc


async def open_hmc_connection(config: HMCConfig) -> asyncssh.SSHClientConnection:
    """Open one long-lived SSH connection hosting a streaming HMC process.

    Unlike :func:`run_hmc_command` this connection stays open when the call
    returns and no command timeout bounds it: its caller streams a remote
    process that never exits on its own (the partition console). The caller
    owns the lifetime and must close the connection. The connect itself stays
    bounded by ``config.ssh_timeout`` so an unreachable HMC fails actionably.
    """
    try:
        async with asyncio.timeout(config.ssh_timeout):
            return await asyncssh.connect(**_connect_kwargs(config))
    except TimeoutError as exc:
        raise HMCCLIError(
            f"SSH connection to {config.host} timed out after "
            f"{config.ssh_timeout:.0f}s."
        ) from exc
    except OSError as exc:
        raise HMCCLIError(
            f"SSH connection to {config.host} failed: {exc}"
        ) from exc
    except asyncssh.Error as exc:
        raise HMCCLIError(f"SSH connection failed: {str(exc).strip()}") from exc


async def run_hmc_cli(cmd: str, config: HMCConfig | None = None) -> str:
    """Execute a command using an explicit or environment-derived config."""
    return await run_hmc_command(config if config is not None else HMCConfig(), cmd)
