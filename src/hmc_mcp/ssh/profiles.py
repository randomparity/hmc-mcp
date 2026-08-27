"""LPAR property and profile commands over the SSH transport."""

from __future__ import annotations

import csv
import re
import shlex

from ..config import HMCConfig
from .transport import HMCCLIError, run_hmc_command
from .commands import build_attribute_record, build_filter
from .description_validation import DESCRIPTION_TARGET_UNSAFE, validate_lpar_description

async def get_lpar_description(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
) -> str:
    """Get the description field of *lpar_name* on *system_name* via SSH.

    Runs ``lssyscfg -r lpar -m <system_name> --filter lpar_names=<lpar_name>
    -F description`` and returns the raw output (the description string, or an
    empty line if none is set). It is the same text shown in the HMC GUI
    Partitions tab.

    The description *is* exposed via the HMC REST API — an earlier revision of
    this docstring claimed otherwise. The #374 live-REST survey found it
    inlined in the bulk list feed ``GET
    /rest/api/uom/ManagedSystem/<uuid>/LogicalPartition`` (and in
    per-partition detail), byte-for-byte identical to this CLI output, present
    since REST schema version V1_2_0, with an empty description signaled by
    element absence rather than an empty element. Bulk ownership reads use
    that feed (``hmc_mcp.operations.lpar_ownership.list_lpar_ownership``); this SSH read
    stays for the CLI-name-keyed write flows that share this module's
    transport.
    """
    cmd = (
        f"lssyscfg -r lpar -m {shlex.quote(system_name)} "
        f"--filter {shlex.quote(build_filter([('lpar_names', lpar_name)]))} -F description"
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

    Raises ``ValueError`` if *description* is not printable ASCII or carries a
    character the record treats as structure; see
    :func:`validate_lpar_description` for the constraint and error code.

    Raises :class:`HMCCLIError` if *lpar_name* contains a character that would
    corrupt the ``chsyscfg -i`` attribute record; see
    :func:`build_attribute_record`, which enforces the record grammar for both
    fields so the guard cannot be present at one and absent at its neighbour.
    A space or a semicolon in *lpar_name* is refused too — a restriction this
    function has always carried and that ADR 0045 deliberately kept here rather
    than extending to the other records, where it would refuse HMC-legal names.
    """
    validate_lpar_description(description)
    for character, (name, reason) in DESCRIPTION_TARGET_UNSAFE.items():
        if character in lpar_name:
            raise HMCCLIError(
                f"LPAR name {lpar_name!r} contains {name} ({character!r}); "
                f"cannot safely write description via chsyscfg -i ({reason})"
            )
    record = build_attribute_record([("name", lpar_name), ("description", description)])
    cmd = f"chsyscfg -r lpar -m {shlex.quote(system_name)} -i {shlex.quote(record)}"
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
        f"--filter {shlex.quote(build_filter([('lpar_names', lpar_name)]))} -F msp"
    )
    raw = await run_hmc_command(config, cmd)
    value = raw.strip()
    if value == "1":
        return True
    if value == "0":
        return False
    raise HMCCLIError(
        f"Unexpected MSP value {value!r} for LPAR {lpar_name!r} "
        f"on system {system_name!r}; expected '0' or '1'"
    )


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
        f"--filter {shlex.quote(build_filter([('lpar_names', lpar_name)]))} -F lpar_env"
    )
    lpar_env = (await run_hmc_command(config, env_cmd)).strip()
    if not lpar_env:
        raise HMCCLIError(
            f"Cannot set MSP on '{lpar_name}': lssyscfg returned no output — "
            f"partition not found on system '{system_name}'. "
            "Check the partition name with hmc_list_lpars."
        )
    if lpar_env != "vioserver":
        raise HMCCLIError(
            f"Cannot set MSP on '{lpar_name}': the msp attribute is only valid "
            f"for a VIOS partition (lpar_env=vioserver), but '{lpar_name}' has "
            f"lpar_env='{lpar_env}'. Use hmc_list_vios to confirm the partition type."
        )
    value = "1" if enabled else "0"
    record = build_attribute_record([("name", lpar_name), ("msp", value)])
    cmd = f"chsyscfg -r lpar -m {shlex.quote(system_name)} -i {shlex.quote(record)}"
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
    try:
        values = list(csv.reader([raw.strip()], strict=True))[0]
    except csv.Error as error:
        raise HMCCLIError(
            f"malformed processor compatibility mode output: {error}"
        ) from error
    return [
        mode.strip() for value in values for mode in value.split(",") if mode.strip()
    ]


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
        f"--filter {shlex.quote(build_filter([('lpar_names', lpar_name)]))} "
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

    Raises:
        HMCCLIError: If *lpar_name* or *mode* contains a character the ``-i``
            record's parser treats as structure.
    """
    record = build_attribute_record(
        [("name", lpar_name), ("lpar_proc_compat_mode", mode)]
    )
    cmd = f"chsyscfg -r lpar -m {shlex.quote(system_name)} -i {shlex.quote(record)}"
    return await run_hmc_command(config, cmd)


# ---------------------------------------------------------------------- #
# SR-IOV adapter mode and vNICs (chhwres)
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
        cmd += " --force"  # literal flag — not a user value, no quoting needed
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
    # NOTE: no empty file_path guard here; see backup_lpar_profiles for the
    # guard pattern. A blank path produces an opaque HMC error rather than a
    # clear ValueError — tracked as a follow-on improvement.
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

    Raises:
        HMCCLIError: If *lpar_name* contains a character the ``-i`` record's
            parser treats as structure.
    """
    record = build_attribute_record([("name", lpar_name), ("sync_curr_profile", 1)])
    cmd = f"chsyscfg -r lpar -m {shlex.quote(system_name)} -i {shlex.quote(record)}"
    return await run_hmc_command(config, cmd)


async def assign_profile_io_slot(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    profile_name: str,
    drc_index: str,
) -> str:
    """Add a physical I/O slot DRC index to *profile_name* without force.

    Raises:
        HMCCLIError: If *profile_name*, *drc_index*, or *lpar_name* contains a
            character the ``-i`` record's parser treats as structure.  The
            ``//0`` suffix is record-safe, so validating the whole ``io_slots``
            value covers *drc_index*.
    """
    return await _change_profile_io_slot(
        config, system_name, lpar_name, profile_name, drc_index, add=True
    )


async def unassign_profile_io_slot(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    profile_name: str,
    drc_index: str,
) -> str:
    """Remove a physical I/O slot DRC index from a profile without force."""
    return await _change_profile_io_slot(
        config, system_name, lpar_name, profile_name, drc_index, add=False
    )


async def _change_profile_io_slot(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    profile_name: str,
    drc_index: str,
    *,
    add: bool,
) -> str:
    operator = "io_slots+" if add else "io_slots-"
    record = build_attribute_record(
        [
            ("name", profile_name),
            (operator, f"{drc_index}//0"),
            ("lpar_name", lpar_name),
        ]
    )
    command = f"chsyscfg -r prof -m {shlex.quote(system_name)} -i {shlex.quote(record)}"
    return await run_hmc_command(config, command)


async def read_lpar_profile_record(
    config: HMCConfig, system_name: str, lpar_name: str, profile_name: str
) -> str:
    """Read exactly one native LPAR profile attribute record."""
    filters = build_filter([("lpar_names", lpar_name), ("profile_names", profile_name)])
    command = (
        f"lssyscfg -r prof -m {shlex.quote(system_name)} "
        f"--filter {shlex.quote(filters)}"
    )
    output = await run_hmc_command(config, command)
    records = [line for line in output.splitlines() if line]
    if len(records) != 1:
        raise HMCCLIError(
            "lssyscfg profile capture expected exactly one record; "
            f"received {len(records)}"
        )
    return records[0]


# ---------------------------------------------------------------------- #
# NIM install via the HMC CLI ``installios`` command (ADR 0070)
# ---------------------------------------------------------------------- #

_IPV4_OCTET = r"(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
_IPV4_PATTERN = re.compile(rf"^{_IPV4_OCTET}(\.{_IPV4_OCTET}){{3}}$")
_MAC_ADDRESS_PATTERN = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")
_VLAN_MIN, _VLAN_MAX = 0, 4094
_LOG_SLUG_PATTERN = re.compile(r"[^A-Za-z0-9._-]")
_INSTALLIOS_LOG_TEMPLATE = "/tmp/hmc-mcp-installios-{slug}.log"
