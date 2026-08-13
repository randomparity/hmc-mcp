"""SSH helpers for executing HMC CLI commands over SSH.

HMC CLI reference:
    https://www.ibm.com/docs/en/power10/7063-CR1?topic=hmc-commands
"""

from __future__ import annotations

import asyncio
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


def validate_lpar_description(description: str) -> None:
    """Raise ``ValueError`` if *description* is not printable ASCII.

    The HMC enforces printable ASCII-only partition descriptions (HSCLC63B).
    Control characters (NUL, LF, CR, ESC, …) are also rejected because they
    can corrupt the HMC CLI's CSV-like ``-i`` parser or be silently truncated
    at the C-string layer.

    Called at the MCP tool layer before UUID resolution and again inside
    :func:`set_lpar_description` as a defensive check.  Both call sites are
    intentional: the outer call provides fast rejection without REST
    round-trips; the inner call guards callers that bypass the MCP tool.
    """
    if not description.isascii() or any(
        ord(c) < 0x20 or ord(c) == 0x7F for c in description
    ):
        raise ValueError(
            "description contains non-ASCII or non-printable characters; "
            "the HMC only accepts printable ASCII partition descriptions (HSCLC63B)"
        )


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
        HMCCLIError: If the SSH connection fails, the command exits non-zero,
            or the command exceeds ``config.ssh_timeout``. The command's stderr
            is included when available.
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
        # asyncssh.connect is a plain function returning a Future that also
        # works as an async context manager (its __aenter__ awaits the
        # connection); wrapping the whole block in asyncio.timeout bounds the
        # connect phase, and conn.run(timeout=...) bounds the command itself.
        async with asyncio.timeout(config.ssh_timeout):
            async with asyncssh.connect(**connect_kwargs) as conn:
                result = await conn.run(cmd, check=True, timeout=config.ssh_timeout)
                return result.stdout
    except TimeoutError as exc:
        raise HMCCLIError(
            f"SSH command timed out after {config.ssh_timeout:.0f}s: {cmd!r}. "
            "The HMC CLI may be hung or the HMC may be under load."
        ) from exc
    except asyncssh.Error as exc:
        # ProcessError (non-zero exit) and connection/auth errors both derive
        # from asyncssh.Error; surface them as HMCCLIError so no caller needs
        # to import the SSH library just to name the exception type.
        # HMC CLI commands write error messages to stdout (not stderr) on
        # non-zero exit; prefer stderr, fall back to stdout, then str(exc).
        detail = (
            getattr(exc, "stderr", None)
            or getattr(exc, "stdout", None)
            or str(exc)
        )
        raise HMCCLIError(f"SSH command failed: {detail.strip()}") from exc


async def run_hmc_cli(cmd: str) -> str:
    """Run an HMC CLI command over SSH with env-configured credentials.

    Thin convenience wrapper around :func:`run_hmc_command` that builds the
    :class:`HMCConfig` from the environment, so tool bodies don't repeat
    ``run_hmc_command(HMCConfig(), cmd)`` inline.
    """
    return await run_hmc_command(HMCConfig(), cmd)


# ---------------------------------------------------------------------- #
# UUID -> CLI-name lookup (SSH fallback for the REST-based resolvers)
# ---------------------------------------------------------------------- #


async def _ssh_system_name(config: HMCConfig, system_uuid: str) -> str:
    """Look up a managed-system UUID's CLI SystemName over SSH.

    Runs ``lssyscfg -r sys -F UUID,SystemName`` and returns the row whose
    UUID column matches. Used as the fallback by the REST-based system-name
    resolver in :mod:`hmc_mcp._app` when the REST API is unreachable.

    Raises:
        HMCCLIError: If no row matches *system_uuid* in the command output.
    """
    raw = await run_hmc_command(config, "lssyscfg -r sys -F UUID,SystemName")
    return _match_uuid_name(raw, system_uuid, "system")


async def _ssh_lpar_name(
    config: HMCConfig,
    lpar_uuid: str,
    system_name: str | None = None,
) -> str:
    """Look up an LPAR UUID's CLI PartitionName over SSH.

    Runs ``lssyscfg -r lpar [-m <system_name>] -F UUID,PartitionName``, scoped
    to *system_name* when given and across all managed systems otherwise. Used
    as the fallback by the REST-based LPAR-name resolver in :mod:`hmc_mcp._app`
    when the REST API is unreachable.

    Raises:
        HMCCLIError: If no row matches *lpar_uuid* in the command output.
    """
    cmd = "lssyscfg -r lpar"
    if system_name:
        cmd += f" -m {shlex.quote(system_name)}"
    cmd += " -F UUID,PartitionName"
    raw = await run_hmc_command(config, cmd)
    return _match_uuid_name(raw, lpar_uuid, "LPAR")


def _match_uuid_name(raw: str, uuid: str, what: str) -> str:
    """Return the name on the ``UUID,<name>`` line matching *uuid*.

    Non-matching lines are skipped; a matching line with an empty name column
    (malformed row) is not returned.
    """
    for line in raw.splitlines():
        row_uuid, _, name = line.partition(",")
        if row_uuid.strip() == uuid:
            name = name.strip()
            if name:
                return name
    raise HMCCLIError(
        f"Could not resolve {what} UUID {uuid!r} to a CLI name over SSH. "
        "No matching row in the lssyscfg UUID,name output."
    )


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
    pci_class: str = "all",
) -> list[dict[str, Any]]:
    """List physical I/O slots on *system_name* via SSH.

    Runs ``lshwres -r io --rsubtype slot -m <system_name>`` and optionally
    filters by PCI class using ``grep pci_class=<code>``.

    pci_class may be one of:
      - ``"all"``   — return every slot (default, no filter)
      - ``"eth"``   — Ethernet adapters (PCI class 0200)
      - ``"sas"``   — SAS/SCSI adapters (PCI class 0104)
      - ``"san"``   — Fibre Channel / SAN adapters (PCI class 0C04)
      - ``"nvme"``  — NVMe adapters (PCI class 0108)

    Returns a list of dicts parsed from the key=value HMC output rows, with
    fields such as ``drc_name``, ``pci_class``, ``feature_codes``, and
    ``lpar_name`` (empty string when the slot is unassigned).

    Raises:
        ValueError: If *pci_class* is not one of the recognised values.
    """
    if pci_class != "all" and pci_class not in _IO_SLOT_PCI_CLASS:
        valid = ", ".join(["all"] + sorted(_IO_SLOT_PCI_CLASS))
        raise ValueError(
            f"Invalid pci_class {pci_class!r}. Must be one of: {valid}"
        )
    cmd = f"lshwres -r io --rsubtype slot -m {shlex.quote(system_name)}"
    if pci_class != "all":
        pci_code = _IO_SLOT_PCI_CLASS[pci_class]
        cmd += f" | grep pci_class={shlex.quote(pci_code)}"
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
    checks that *pool_name* exists and that no LPARs are still assigned to
    it.  If a pool with that name is missing, or any LPARs are still
    assigned, the command is **not** executed and an ``HMCCLIError``
    describing the problem is raised instead.

    Runs ``chhwres -r mempool -m <system_name> -o r -a <pool_name>`` on
    the HMC via SSH when the pool exists and no LPARs are assigned.

    Returns the HMC CLI output (immediate delete — no job to poll).

    Raises:
        HMCCLIError: If *pool_name* has LPARs still assigned to it, or if
            no pool with that name exists on *system_name*.
    """
    # Safety check: list pools and look for LPAR assignments.
    pools = await list_memory_pools(config, system_name)

    found = False
    for pool in pools:
        if pool.get("pool_name") == pool_name:
            found = True
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
    if not found:
        raise HMCCLIError(
            f"Cannot remove memory pool '{pool_name}' on '{system_name}' — "
            f"no pool with that name exists in the current pool list. "
            f"Use hmc_list_memory_pools to see the available pools."
        )

    cmd = f"chhwres -r mempool -m {shlex.quote(system_name)} -o r -a {shlex.quote(pool_name)}"
    return await run_hmc_command(config, cmd)


# ---------------------------------------------------------------------- #
# LPAR description and MSP (lssyscfg / chsyscfg — no REST equivalent)
# ---------------------------------------------------------------------- #


async def get_lpar_description(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
) -> str:
    """Get the description field of *lpar_name* on *system_name* via SSH.

    Runs ``lssyscfg -r lpar -m <system_name> --filter lpar_names=<lpar_name>
    -F description`` and returns the raw output (the description string, or an
    empty line if none is set). The description is not exposed via the HMC REST
    API; it is the same text shown in the HMC GUI Partitions tab.
    """
    cmd = (
        f"lssyscfg -r lpar -m {shlex.quote(system_name)} "
        f"--filter lpar_names={shlex.quote(lpar_name)} -F description"
    )
    return await run_hmc_command(config, cmd)


async def set_lpar_description(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    description: str,
) -> str:
    """Set the description field of *lpar_name* via SSH.

    Runs ``chsyscfg -r lpar -m <system_name>
    -i "name=<lpar_name>,description=<description>"`` and returns the raw
    command output.

    Raises ``ValueError`` if *description* is not printable ASCII; see
    :func:`validate_lpar_description` for the constraint and error code.
    """
    validate_lpar_description(description)
    cmd = (
        f"chsyscfg -r lpar -m {shlex.quote(system_name)} -i "
        f"{shlex.quote(f'name={lpar_name},description={description}')}"
    )
    return await run_hmc_command(config, cmd)


async def get_lpar_msp(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
) -> bool:
    """Get the MSP (Migratable Service Partition) flag of *lpar_name* via SSH.

    Runs ``lssyscfg -r lpar -m <system_name> --filter lpar_names=<lpar_name>
    -F msp`` and returns ``True`` when the flag is ``1``, ``False`` when ``0``.
    """
    cmd = (
        f"lssyscfg -r lpar -m {shlex.quote(system_name)} "
        f"--filter lpar_names={shlex.quote(lpar_name)} -F msp"
    )
    raw = await run_hmc_command(config, cmd)
    return raw.strip() == "1"


async def set_lpar_msp(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    enabled: bool,
) -> str:
    """Set the MSP (Migratable Service Partition) flag of *lpar_name* via SSH.

    Checks that *lpar_name* is a VIOS partition (``lpar_env=vioserver``) before
    issuing the command.  The HMC rejects ``msp=...`` for AIX/Linux partitions
    with a confusing generic error; this guard surfaces a clear diagnostic
    before the SSH round-trip.

    Note: the ``lpar_env`` probe and the ``chsyscfg`` write are two separate
    SSH connections (each ``run_hmc_command`` call opens its own connection).
    The guard is not atomic with the write; the HMC itself enforces the
    VIOS-only invariant and returns an error if the race were to occur.

    Runs ``chsyscfg -r lpar -m <system_name> -i "name=<lpar_name>,msp=<0|1>"``
    and returns the raw command output.

    Raises:
        HMCCLIError: If the partition is not found on the system, or if its
            ``lpar_env`` is not ``vioserver``.
    """
    env_cmd = (
        f"lssyscfg -r lpar -m {shlex.quote(system_name)} "
        f"--filter lpar_names={shlex.quote(lpar_name)} -F lpar_env"
    )
    lpar_env = (await run_hmc_command(config, env_cmd)).strip()
    if not lpar_env:
        raise HMCCLIError(
            f"Cannot set MSP on '{lpar_name}': lssyscfg returned no output — "
            f"partition not found on system '{system_name}'. "
            "Check the partition name with hmc_lpars."
        )
    if lpar_env != "vioserver":
        raise HMCCLIError(
            f"Cannot set MSP on '{lpar_name}': the msp attribute is only valid "
            f"for a VIOS partition (lpar_env=vioserver), but '{lpar_name}' has "
            f"lpar_env='{lpar_env}'. Use hmc_vios to confirm the partition type."
        )
    value = "1" if enabled else "0"
    cmd = (
        f"chsyscfg -r lpar -m {shlex.quote(system_name)} -i "
        f"{shlex.quote(f'name={lpar_name},msp={value}')}"
    )
    return await run_hmc_command(config, cmd)


# ---------------------------------------------------------------------- #
# Processor compatibility (lssyscfg / chsyscfg)
# ---------------------------------------------------------------------- #


async def get_proc_compat_modes(
    config: HMCConfig,
    system_name: str,
) -> list[str]:
    """List processor compatibility modes supported by *system_name* via SSH.

    Runs ``lssyscfg -r sys -m <system_name> -F lpar_proc_compat_modes`` and
    returns the comma-separated modes as a list of stripped strings.
    """
    cmd = f"lssyscfg -r sys -m {shlex.quote(system_name)} -F lpar_proc_compat_modes"
    raw = await run_hmc_command(config, cmd)
    if not raw.strip():
        return []
    return [mode.strip() for mode in raw.strip().split(",") if mode.strip()]


async def get_lpar_proc_compat(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
) -> dict[str, str]:
    """Get the current and desired processor compatibility modes of an LPAR.

    Runs ``lssyscfg -r lpar -m <system_name> --filter lpar_names=<lpar_name>
    -F desired_lpar_proc_compat_mode,curr_lpar_proc_compat_mode`` and returns a
    dict with keys ``"desired"`` and ``"curr"``.

    Note: ``pend_lpar_proc_compat_mode`` is not a valid HMC CLI attribute;
    ``desired_lpar_proc_compat_mode`` is the correct field name.
    """
    cmd = (
        f"lssyscfg -r lpar -m {shlex.quote(system_name)} "
        f"--filter lpar_names={shlex.quote(lpar_name)} "
        "-F desired_lpar_proc_compat_mode,curr_lpar_proc_compat_mode"
    )
    raw = await run_hmc_command(config, cmd)
    if not raw.strip():
        return {"desired": "", "curr": ""}
    parts = raw.strip().split(",")
    desired = parts[0].strip() if len(parts) > 0 else ""
    curr = parts[1].strip() if len(parts) > 1 else ""
    return {"desired": desired, "curr": curr}


async def set_lpar_proc_compat(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    mode: str,
) -> str:
    """Set the processor compatibility mode of *lpar_name* via SSH.

    Runs ``chsyscfg -r lpar -m <system_name>
    -i "name=<lpar_name>,lpar_proc_compat_mode=<mode>"`` and returns the raw
    command output.
    """
    cmd = (
        f"chsyscfg -r lpar -m {shlex.quote(system_name)} -i "
        f"{shlex.quote(f'name={lpar_name},lpar_proc_compat_mode={mode}')}"
    )
    return await run_hmc_command(config, cmd)


# ---------------------------------------------------------------------- #
# SR-IOV adapter mode and vNICs (chhwres)
# ---------------------------------------------------------------------- #

_VALID_SRIOV_MODES = frozenset({"sriov", "dedicated"})


def validate_sriov_mode(mode: str) -> str:
    """Return *mode* if it is a recognised SR-IOV adapter mode, else raise.

    Shared by :func:`set_sriov_adapter_mode` and the CLI pre-confirmation
    guard so the valid-mode set is defined once.

    Raises:
        ValueError: If *mode* is not one of ``"sriov"`` or ``"dedicated"``.
    """
    if mode not in _VALID_SRIOV_MODES:
        raise ValueError(
            f"Invalid mode {mode!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_SRIOV_MODES))}"
        )
    return mode


async def set_sriov_adapter_mode(
    config: HMCConfig,
    system_name: str,
    adapter_id: str,
    mode: str,
) -> str:
    """Toggle a physical SR-IOV adapter between SR-IOV and dedicated mode.

    Runs ``chhwres -r sriov -m <system_name> -o s --id <adapter_id>
    -a "sriov_adapter_mode=<mode>"`` and returns the raw command output.

    *mode* must be one of ``"sriov"`` (shared virtual functions) or
    ``"dedicated"`` (passthrough); any other value raises :class:`ValueError`
    before a command is issued.

    Raises:
        ValueError: If *mode* is not a recognised SR-IOV adapter mode.
    """
    validate_sriov_mode(mode)
    cmd = (
        f"chhwres -r sriov -m {shlex.quote(system_name)} -o s --id {shlex.quote(adapter_id)}"
        f" -a {shlex.quote(f'sriov_adapter_mode={mode}')}"
    )
    return await run_hmc_command(config, cmd)


async def add_vnic(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    capacity: int,
    vswitch_name: str,
    port_vlan_id: int,
    backing_devices: str | None = None,
) -> str:
    """Add a vNIC (SR-IOV-backed Virtual NIC) to *lpar_name* via SSH.

    Runs ``chhwres -r virtualio --rsubtype vnic -o a -m <system_name>
    --filter lpar_names=<lpar_name> -a "<attrs>"`` and returns the raw command
    output.

    **V1 scope boundary:** Only ``capacity``, ``vswitch_name``,
    ``port_vlan_id``, and ``backing_devices`` (optional, opaque string passed
    verbatim) are supported.  Complex backing-device topology (multi-adapter
    failover, per-device SR-IOV physical port IDs, capacity weights) is a
    follow-up.

    Raises:
        HMCCLIError: If the HMC command fails, e.g. because the underlying
            SR-IOV adapter is not in SR-IOV mode.
    """
    attrs = f"capacity={capacity},vswitch_name={vswitch_name},port_vlan_id={port_vlan_id}"
    if backing_devices is not None:
        attrs += f",backing_devices={backing_devices}"

    cmd = (
        f"chhwres -r virtualio --rsubtype vnic -o a -m {shlex.quote(system_name)}"
        f" --filter lpar_names={shlex.quote(lpar_name)}"
        f" -a {shlex.quote(attrs)}"
    )
    try:
        return await run_hmc_command(config, cmd)
    except HMCCLIError as exc:
        raise HMCCLIError(
            f"Failed to add vNIC to '{lpar_name}' on '{system_name}': {exc}. "
            f"Ensure the underlying SR-IOV adapter is in sriov mode "
            f"(see hmc_set_sriov_adapter_mode)."
        ) from exc


async def remove_vnic(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    vnic_id: str,
) -> str:
    """Remove a vNIC from *lpar_name* via SSH.

    Runs ``chhwres -r virtualio --rsubtype vnic -o r -m <system_name>
    --filter lpar_names=<lpar_name> -a "vnic_id=<vnic_id>"`` and returns the
    raw command output.

    *vnic_id* is the numeric ID reported by :func:`list_vnics`.
    """
    cmd = (
        f"chhwres -r virtualio --rsubtype vnic -o r -m {shlex.quote(system_name)}"
        f" --filter lpar_names={shlex.quote(lpar_name)}"
        f" -a {shlex.quote(f'vnic_id={vnic_id}')}"
    )
    return await run_hmc_command(config, cmd)


# ---------------------------------------------------------------------- #
# LPAR profile backup/restore/sync and I/O slot assignment (bkprofdata /
# rstprofdata / chsyscfg — no REST equivalent)
# ---------------------------------------------------------------------- #


async def backup_lpar_profiles(
    config: HMCConfig,
    system_name: str,
    file_path: str,
    *,
    force: bool = False,
) -> str:
    """Backup all LPAR profiles on *system_name* to *file_path* via SSH.

    Runs ``bkprofdata -m <system_name> -f <file_path>`` and returns the raw
    command output. *file_path* is on the HMC filesystem, not the local
    machine; the backup file is created at that path on the HMC host.

    When *force* is ``True``, ``--force`` is appended to the command so that
    an existing file at *file_path* is overwritten instead of raising an error.
    """
    cmd = f"bkprofdata -m {shlex.quote(system_name)} -f {shlex.quote(file_path)}"
    if force:
        cmd += " --force"
    return await run_hmc_command(config, cmd)


async def restore_lpar_profiles(
    config: HMCConfig,
    system_name: str,
    file_path: str,
) -> str:
    """Restore LPAR profiles from *file_path* on *system_name* via SSH.

    Runs ``rstprofdata -m <system_name> -f <file_path>`` and returns the raw
    command output. *file_path* must already exist on the HMC filesystem.
    Restoring overwrites the current LPAR profile configuration.
    """
    cmd = f"rstprofdata -m {shlex.quote(system_name)} -f {shlex.quote(file_path)}"
    return await run_hmc_command(config, cmd)


async def sync_lpar_profile(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
) -> str:
    """Sync *lpar_name*'s running configuration back to its current profile.

    Runs ``chsyscfg -r lpar -m <system_name>
    -i "name=<lpar_name>,sync_curr_profile=1"`` and returns the raw command
    output. This saves the LPAR's current running configuration to its
    current named profile, overwriting the previous profile definition.
    """
    cmd = (
        f"chsyscfg -r lpar -m {shlex.quote(system_name)} -i "
        f"{shlex.quote(f'name={lpar_name},sync_curr_profile=1')}"
    )
    return await run_hmc_command(config, cmd)


async def assign_profile_io_slot(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    profile_name: str,
    drc_index: str,
) -> str:
    """Add a physical I/O slot DRC index to *profile_name* via SSH.

    Runs ``chsyscfg -r prof -m <system_name>
    -i "name=<profile_name>,io_slots+=<drc_index>//0,lpar_name=<lpar_name>"
    --force`` and returns the raw command output. Appends the slot to the
    profile's I/O slot list; ``--force`` overrides any conflicts.
    """
    cmd = (
        f"chsyscfg -r prof -m {shlex.quote(system_name)} -i "
        f"{shlex.quote(f'name={profile_name},io_slots+={drc_index}//0,lpar_name={lpar_name}')} --force"
    )
    return await run_hmc_command(config, cmd)
