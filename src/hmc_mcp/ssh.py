"""SSH helpers for executing HMC CLI commands over SSH.

HMC CLI reference:
    https://www.ibm.com/docs/en/power10/7063-CR1?topic=hmc-commands
"""

from __future__ import annotations

import shlex
from typing import Any

import asyncssh

from .config import HMCConfig


class HMCCLIError(Exception):
    """An HMC CLI operation failed or was refused before the command ran."""


async def run_hmc_command(config: HMCConfig, cmd: str) -> str:
    """Execute an HMC CLI command over SSH and return its stdout + stderr.

    Authentication:
    - If ``config.ssh_key_file`` is set, key-based auth is attempted using
      that private-key file (passphrase-protected keys are not supported); the
      password is not required in this mode.
    - Otherwise password auth is used via ``config.password``.

    Args:
        config: HMC connection settings.
        cmd: The HMC CLI command string to run.

    Returns:
        The combined stdout of the command.

    Raises:
        ValueError: If required connection settings (host/user, and password
            when no SSH key is configured) are missing — the same actionable
            message the REST client uses.
        asyncssh.Error: On SSH connection or command execution failure.
    """
    config.validate_credentials(require_password=not config.ssh_key_file)

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


def _parse_lshwres_output(text: str) -> list[dict[str, Any]]:
    """Parse ``lshwres`` key=value output into a list of dicts.

    Each non-empty line is expected to be a comma-separated sequence of
    ``key=value`` pairs (the default ``lshwres`` output format).  Values that
    are absent (empty string) are included as empty strings so callers can
    distinguish missing from absent fields.
    """
    results = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        row: dict[str, Any] = {}
        last_key: str | None = None
        for pair in line.split(","):
            if "=" in pair:
                key, _, value = pair.partition("=")
                last_key = key.strip()
                row[last_key] = value.strip()
            elif last_key is not None:
                # bare token — comma is part of the previous value (e.g. LPAR name lists)
                row[last_key] = row[last_key] + "," + pair.strip()
            else:
                # bare token with no prior key — store as-is
                row[pair.strip()] = ""
        if row:
            results.append(row)
    return results


# PCI class codes used by lshwres -r io --rsubtype slot.
_IO_SLOT_PCI_CLASS = {
    "eth": "0200",
    "sas": "0104",
    "san": "0C04",
    "nvme": "0108",
}


async def list_io_slots(
    config: HMCConfig,
    system_name: str,
    adapter_type: str = "all",
) -> list[dict[str, Any]]:
    """List physical I/O slots on *system_name* via SSH.

    Runs ``lshwres -r io --rsubtype slot -m <system_name>`` and optionally
    filters by PCI class using ``grep pci_class=<code>``.

    adapter_type may be one of:
      - ``"all"``   — return every slot (default, no filter)
      - ``"eth"``   — Ethernet adapters (PCI class 0200)
      - ``"sas"``   — SAS/SCSI adapters (PCI class 0104)
      - ``"san"``   — Fibre Channel / SAN adapters (PCI class 0C04)
      - ``"nvme"``  — NVMe adapters (PCI class 0108)

    Returns a list of dicts parsed from the key=value HMC output rows, with
    fields such as ``drc_name``, ``pci_class``, ``feature_codes``, and
    ``lpar_name`` (empty string when the slot is unassigned).

    Raises:
        ValueError: If *adapter_type* is not one of the recognised values.
    """
    if adapter_type != "all" and adapter_type not in _IO_SLOT_PCI_CLASS:
        valid = ", ".join(["all"] + sorted(_IO_SLOT_PCI_CLASS))
        raise ValueError(
            f"Invalid adapter_type {adapter_type!r}. Must be one of: {valid}"
        )
    cmd = f"lshwres -r io --rsubtype slot -m {shlex.quote(system_name)}"
    if adapter_type != "all":
        pci_class = _IO_SLOT_PCI_CLASS[adapter_type]
        cmd += f" | grep pci_class={shlex.quote(pci_class)}"
    output = await run_hmc_command(config, cmd)
    return _parse_lshwres_output(output)
