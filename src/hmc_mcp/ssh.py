"""SSH helpers for executing HMC CLI commands over SSH.

HMC CLI reference:
    https://www.ibm.com/docs/en/power10/7063-CR1?topic=hmc-commands
"""

from __future__ import annotations

import csv
import io
import shlex
from typing import Any

import asyncssh

from .config import HMCConfig
from .errors import HMCError


class HMCCLIError(HMCError):
    """An HMC CLI operation failed or was refused before the command ran.

    Subclasses :class:`HMCError` so callers can handle REST and CLI failures
    uniformly with a single ``except HMCError``.
    """


async def run_hmc_command(config: HMCConfig, cmd: str) -> str:
    """Execute an HMC CLI command over SSH and return its stdout.

    Command stderr is not returned: on a non-zero exit the command's stderr is
    folded into the raised :class:`HMCCLIError` message.

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
        HMCCLIError: If the SSH connection fails or the command exits
            non-zero. The command's stderr is included when available.
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

    try:
        async with asyncssh.connect(**connect_kwargs) as conn:
            result = await conn.run(cmd, check=True)
            return result.stdout
    except asyncssh.Error as exc:
        # ProcessError (non-zero exit) and connection/auth errors both derive
        # from asyncssh.Error; surface them as HMCCLIError so no caller needs
        # to import the SSH library just to name the exception type.
        detail = getattr(exc, "stderr", None) or str(exc)
        raise HMCCLIError(f"SSH command failed: {detail.strip()}") from exc


async def run_hmc_cli(cmd: str) -> str:
    """Run an HMC CLI command over SSH with env-configured credentials.

    Thin convenience wrapper around :func:`run_hmc_command` that builds the
    :class:`HMCConfig` from the environment, so tool bodies don't repeat
    ``run_hmc_command(HMCConfig(), cmd)`` inline.
    """
    return await run_hmc_command(HMCConfig(), cmd)


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


async def list_fc_ports(
    config: HMCConfig,
    system_name: str,
    lpar_name: str | None = None,
) -> list[dict[str, str]]:
    """List Virtual Fibre Channel (NPIV) adapters via SSH.

    Runs ``lshwres -r virtualio --rsubtype fc --level lpar -m <system_name>``
    and parses the CSV output rows (lpar_name, slot_num, wwpns, ...).  Pass
    *lpar_name* to restrict results to a single partition.
    """
    cmd = f"lshwres -r virtualio --rsubtype fc --level lpar -m {shlex.quote(system_name)}"
    if lpar_name:
        cmd += f" --filter lpar_names={shlex.quote(lpar_name)}"
    raw = await run_hmc_command(config, cmd)
    if not raw.strip():
        return []
    reader = csv.DictReader(io.StringIO(raw.strip()))
    return [dict(row) for row in reader]


async def list_sea_adapters(
    config: HMCConfig,
    system_name: str,
    lpar_name: str | None = None,
) -> list[dict[str, str]]:
    """List Shared Ethernet Adapter (SEA) virtual Ethernet ports via SSH.

    Runs ``lshwres -r virtualio --rsubtype eth --level lpar -m <system_name>
    -F lpar_name,port_vlan_id,vswitch,state,trunk_priority`` and returns one
    dict with those five fields per port.  Pass *lpar_name* to restrict
    results to a single partition.
    """
    fields = "lpar_name,port_vlan_id,vswitch,state,trunk_priority"
    cmd = (
        f"lshwres -r virtualio --rsubtype eth --level lpar -m {shlex.quote(system_name)}"
        f" -F {fields}"
    )
    if lpar_name:
        cmd += f" --filter lpar_names={shlex.quote(lpar_name)}"
    raw = await run_hmc_command(config, cmd)
    if not raw.strip():
        return []
    keys = fields.split(",")
    result = []
    for line in raw.strip().splitlines():
        values = line.split(",", len(keys) - 1)
        result.append(dict(zip(keys, values)))
    return result


async def list_vnics(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
) -> list[dict[str, Any]]:
    """List vNICs (SR-IOV-backed Virtual NICs) on an LPAR via SSH.

    Runs ``lshwres -r virtualio --rsubtype vnic --level lpar -m <system_name>
    --filter lpar_names=<lpar_name>`` and returns one dict per vNIC parsed
    from the key=value rows, with fields such as ``vnic_id``, ``capacity``,
    ``vswitch_name``, ``port_vlan_id``, and ``backing_devices``.
    """
    cmd = (
        f"lshwres -r virtualio --rsubtype vnic --level lpar -m {shlex.quote(system_name)}"
        f" --filter lpar_names={shlex.quote(lpar_name)}"
    )
    raw = await run_hmc_command(config, cmd)
    if not raw.strip():
        return []
    return _parse_lshwres_output(raw)


async def list_memory_pools(
    config: HMCConfig,
    system_name: str,
) -> list[dict[str, Any]]:
    """List shared memory pools on *system_name* via SSH.

    Runs ``lshwres -r mempool -m <system_name>`` and returns one dict per
    pool parsed from the key=value rows, with fields such as ``pool_name``,
    ``size``, ``lpar_names``, and ``curr_lpar_names`` (comma-separated).
    """
    output = await run_hmc_command(
        config, f"lshwres -r mempool -m {shlex.quote(system_name)}"
    )
    return _parse_lshwres_output(output)


async def remove_memory_pool(
    config: HMCConfig,
    system_name: str,
    pool_name: str,
) -> str:
    """Remove a shared memory pool from *system_name* via SSH.

    Before issuing the remove command, fetches the current pool list and
    checks whether any LPARs are still assigned to *pool_name*.  If any are
    found the command is **not** executed and an ``HMCCLIError`` naming the
    blocking LPARs is raised instead.

    Runs ``chhwres -r mempool -m <system_name> -o r -a <pool_name>`` on
    the HMC via SSH when no LPARs are assigned.

    Returns the HMC CLI output (immediate delete — no job to poll).

    Raises:
        HMCCLIError: If *pool_name* still has LPARs assigned to it.
    """
    # Safety check: list pools and look for LPAR assignments.
    pools = await list_memory_pools(config, system_name)

    for pool in pools:
        if pool.get("pool_name") == pool_name:
            # curr_lpar_names may be a comma-separated string or empty.
            assigned = pool.get("curr_lpar_names", "").strip()
            if assigned:
                lpar_list = [lp.strip() for lp in assigned.split(",") if lp.strip()]
                raise HMCCLIError(
                    f"Cannot remove memory pool '{pool_name}' on "
                    f"'{system_name}' — the following LPARs are still "
                    f"assigned to it: {', '.join(lpar_list)}. Reassign or "
                    "remove them from the pool before retrying."
                )
            break

    cmd = f"chhwres -r mempool -m {shlex.quote(system_name)} -o r -a {shlex.quote(pool_name)}"
    return await run_hmc_command(config, cmd)
