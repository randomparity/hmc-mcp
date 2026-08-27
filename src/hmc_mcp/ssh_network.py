"""PCI, SR-IOV, Fibre Channel, SEA, and vNIC SSH commands."""

from __future__ import annotations

import csv
import io
import shlex
from typing import Any, Literal, get_args

from .config import HMCConfig
from .ssh import HMCCLIError, run_hmc_command
from .ssh_commands import (
    _parse_lshwres_output,
    build_attribute_record,
    build_filter,
    parse_hmc_delimited_rows,
)

_IO_SLOT_PCI_CLASS = {
    "eth": "0200",
    "sas": "0104",
    "san": "0C04",
    "nvme": "0108",
}
PciClass = Literal["all", "eth", "sas", "san", "nvme"]
_VALID_PCI_CLASSES = frozenset(get_args(PciClass))


async def list_io_slots(
    config: HMCConfig,
    system_name: str,
    pci_class: PciClass = "all",
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
    if pci_class not in _VALID_PCI_CLASSES:
        valid = ", ".join(sorted(_VALID_PCI_CLASSES))
        raise ValueError(f"Invalid pci_class {pci_class!r}. Must be one of: {valid}")
    cmd = f"lshwres -r io --rsubtype slot -m {shlex.quote(system_name)}"
    if pci_class != "all":
        pci_code = _IO_SLOT_PCI_CLASS[pci_class]
        cmd += f" | grep pci_class={shlex.quote(pci_code)}"
    output = await run_hmc_command(config, cmd)
    return _parse_lshwres_output(output)


async def list_dedicated_pcie_slot_rows(
    config: HMCConfig,
    system_name: str,
) -> list[dict[str, str]]:
    """Read the exact dedicated-slot projection admitted by ADR 0053."""
    fields = ("drc_index", "description", "lpar_name")
    projection = ",".join(fields)
    command = (
        f"lshwres -r io --rsubtype slot -m {shlex.quote(system_name)} "
        f"-F {projection} --header"
    )
    output = await run_hmc_command(config, command)
    return parse_hmc_delimited_rows(output, fields)


def _parse_admitted_rows(output: str, fields: tuple[str, ...]) -> list[dict[str, str]]:
    if output.strip() == "No results were found.":
        return []
    return parse_hmc_delimited_rows(output, fields)


async def list_sriov_adapter_rows(
    config: HMCConfig, system_name: str
) -> list[dict[str, str]]:
    fields = (
        "adapter_id",
        "slot_id",
        "config_state",
        "functional_state",
        "phys_loc",
        "phys_ports",
        "logical_ports",
        "adapter_max_logical_ports",
        "sriov_status",
    )
    command = f"lshwres -r sriov --rsubtype adapter -m {shlex.quote(system_name)} -F {','.join(fields)} --header"
    return _parse_admitted_rows(await run_hmc_command(config, command), fields)


async def read_sriov_environment(
    config: HMCConfig, system_name: str
) -> tuple[str, str]:
    """Return the exact HMC release and managed-system model admission inputs."""
    version = (await run_hmc_command(config, "lshmc -V")).strip()
    model = (
        await run_hmc_command(
            config,
            f"lssyscfg -r sys -m {shlex.quote(system_name)} -F type_model",
        )
    ).strip()
    return version, model


async def list_sriov_physical_port_rows(
    config: HMCConfig, system_name: str, adapter_id: str
) -> list[dict[str, str]]:
    fields = (
        "adapter_id",
        "phys_port_id",
        "phys_port_type",
        "phys_port_loc",
        "state",
        "config_logical_ports",
        "phys_port_max_logical_ports",
        "curr_eth_logical_ports",
    )
    command = f"lshwres -r sriov --rsubtype physport -m {shlex.quote(system_name)} --level roce --filter {shlex.quote(build_filter([('adapter_ids', adapter_id)]))} -F {','.join(fields)} --header"
    return _parse_admitted_rows(await run_hmc_command(config, command), fields)


_SRIOV_LOGICAL_FIELDS = (
    "config_id",
    "lpar_name",
    "lpar_id",
    "lpar_state",
    "adapter_id",
    "logical_port_id",
    "logical_port_type",
    "phys_port_id",
    "functional_state",
    "capacity",
    "max_capacity",
)


async def list_sriov_configured_logical_port_rows(
    config: HMCConfig, system_name: str, adapter_id: str
) -> list[dict[str, str]]:
    command = f"lshwres -r sriov --rsubtype logport -m {shlex.quote(system_name)} --level eth --filter {shlex.quote(build_filter([('adapter_ids', adapter_id)]))} -F {','.join(_SRIOV_LOGICAL_FIELDS)} --header"
    return _parse_admitted_rows(
        await run_hmc_command(config, command), _SRIOV_LOGICAL_FIELDS
    )


async def list_sriov_unconfigured_logical_port_rows(
    config: HMCConfig, system_name: str
) -> list[dict[str, str]]:
    command = f"lshwres -r sriov --rsubtype logport -m {shlex.quote(system_name)}"
    return [
        dict(row)
        for row in _parse_lshwres_output(await run_hmc_command(config, command))
        if row.get("logical_port_type") == "unconfigured"
    ]


async def read_sriov_lpar_state(
    config: HMCConfig, system_name: str, lpar_name: str
) -> dict[str, str]:
    fields = ("name", "lpar_id", "state", "rmc_state")
    command = f"lssyscfg -r lpar -m {shlex.quote(system_name)} --filter {shlex.quote(build_filter([('lpar_names', lpar_name)]))} -F {','.join(fields)} --header"
    rows = _parse_admitted_rows(await run_hmc_command(config, command), fields)
    if len(rows) != 1:
        raise HMCCLIError(
            f"Expected one LPAR state row for {lpar_name!r}; got {len(rows)}"
        )
    return rows[0]


async def read_sriov_profile_ports(
    config: HMCConfig, system_name: str, lpar_name: str, profile_name: str
) -> dict[str, str]:
    fields = ("name", "sriov_eth_logical_ports")
    filters = build_filter([("lpar_names", lpar_name), ("profile_names", profile_name)])
    command = f"lssyscfg -r prof -m {shlex.quote(system_name)} --filter {shlex.quote(filters)} -F {','.join(fields)} --header"
    rows = _parse_admitted_rows(await run_hmc_command(config, command), fields)
    if len(rows) != 1:
        raise HMCCLIError(
            f"Expected one SR-IOV profile row for {profile_name!r}; got {len(rows)}"
        )
    return rows[0]


async def assign_sriov_logical_port_dynamic(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    adapter_id: str,
    physical_port_id: str,
    logical_port_id: str,
    capacity: str,
) -> str:
    record = build_attribute_record(
        [
            ("adapter_id", adapter_id),
            ("phys_port_id", physical_port_id),
            ("logical_port_id", logical_port_id),
            ("logical_port_type", "eth"),
            ("capacity", capacity),
        ]
    )
    command = f"chhwres -r sriov --rsubtype logport -m {shlex.quote(system_name)} -o a -p {shlex.quote(lpar_name)} -a {shlex.quote(record)}"
    return await run_hmc_command(config, command)


async def unassign_sriov_logical_port_profile(
    config: HMCConfig, system_name: str, lpar_name: str, profile_name: str
) -> str:
    record = build_attribute_record(
        [
            ("name", profile_name),
            ("lpar_name", lpar_name),
            ("sriov_eth_logical_ports", "none"),
        ]
    )
    command = f"chsyscfg -r prof -m {shlex.quote(system_name)} -i {shlex.quote(record)}"
    return await run_hmc_command(config, command)


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
    cmd = (
        f"lshwres -r virtualio --rsubtype fc --level lpar -m {shlex.quote(system_name)}"
    )
    if lpar_name:
        cmd += f" --filter {shlex.quote(build_filter([('lpar_names', lpar_name)]))}"
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
        cmd += f" --filter {shlex.quote(build_filter([('lpar_names', lpar_name)]))}"
    raw = await run_hmc_command(config, cmd)
    if not raw.strip():
        return []
    keys = fields.split(",")
    result: list[dict[str, str]] = []
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
        f" --filter {shlex.quote(build_filter([('lpar_names', lpar_name)]))}"
    )
    raw = await run_hmc_command(config, cmd)
    if not raw.strip():
        return []
    return _parse_lshwres_output(raw)


_VNIC_FIELDS = (
    "lpar_name",
    "lpar_id",
    "slot_num",
    "desired_mode",
    "curr_mode",
    "auto_priority_failover",
    "port_vlan_id",
    "pvid_priority",
    "allowed_vlan_ids",
    "mac_addr",
    "allowed_os_mac_addrs",
    "backing_devices",
    "backing_device_states",
)
_VNIC_BACKING_FIELDS = (
    "lpar_name",
    "lpar_id",
    "type",
    "adapter_id",
    "physical_port_id",
    "logical_port_id",
    "capacity",
    "desired_capacity",
    "max_capacity",
    "desired_max_capacity",
    "failover_priority",
    "is_active",
    "status",
)
_VIOS_IDENTITY_FIELDS = ("name", "lpar_id", "lpar_env")


async def list_vnic_rows(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
) -> list[dict[str, str]]:
    """Return strict, version-admitted vNIC rows for one partition."""
    fields = ",".join(_VNIC_FIELDS)
    command = (
        "lshwres -r virtualio --rsubtype vnic --level lpar"
        f" -m {shlex.quote(system_name)}"
        f" --filter {shlex.quote(build_filter([('lpar_names', lpar_name)]))}"
        f" -F {fields} --header"
    )
    output = await run_hmc_command(config, command)
    return parse_hmc_delimited_rows(output, _VNIC_FIELDS)


async def list_vnic_backing_rows(
    config: HMCConfig,
    system_name: str,
) -> list[dict[str, str]]:
    """Return strict, system-wide vNIC backing-device rows."""
    fields = ",".join(_VNIC_BACKING_FIELDS)
    command = (
        "lshwres -r virtualio --rsubtype vnicbkdev"
        f" -m {shlex.quote(system_name)} -F {fields} --header"
    )
    output = await run_hmc_command(config, command)
    if output.strip() == "No results were found.":
        return []
    return parse_hmc_delimited_rows(output, _VNIC_BACKING_FIELDS)


async def read_vios_identity(
    config: HMCConfig,
    system_name: str,
    vios_name: str,
) -> dict[str, str]:
    """Return the unique strict identity row for a named VIOS candidate."""
    fields = ",".join(_VIOS_IDENTITY_FIELDS)
    command = (
        f"lssyscfg -r lpar -m {shlex.quote(system_name)}"
        f" --filter {shlex.quote(build_filter([('lpar_names', vios_name)]))}"
        f" -F {fields} --header"
    )
    rows = parse_hmc_delimited_rows(
        await run_hmc_command(config, command), _VIOS_IDENTITY_FIELDS
    )
    if len(rows) != 1:
        raise ValueError(
            f"VIOS identity read for {vios_name!r} returned {len(rows)} rows; expected 1"
        )
    return rows[0]


async def add_vnic_backing(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    backing_device: str,
    port_vlan_id: int,
) -> str:
    """Add one vNIC via ``chhwres -r virtualio --rsubtype vnic -o a``.

    *backing_device* is a ``/``-delimited SR-IOV device spec, or a
    comma-separated list of them; a value carrying a comma renders as the
    IBM quoted pair ``"backing_devices=dev1,dev2"`` so the list survives the
    record grammar (ADR 0061).  Any other record delimiter in the value is
    refused before the command is built.
    """
    payload = build_attribute_record(
        [("port_vlan_id", port_vlan_id), ("backing_devices", backing_device)],
        quoted=("backing_devices",),
        # Not spelled `chhwres -a ...`: a plain string opening with the
        # command name would itself trip the recurrence guard's -a scan.
        surface="`chhwres -a`",
    )
    command = (
        "chhwres -r virtualio --rsubtype vnic -o a"
        f" -m {shlex.quote(system_name)} -p {shlex.quote(lpar_name)}"
        f" -a {shlex.quote(payload)}"
    )
    return await run_hmc_command(config, command)


async def remove_vnic_slot(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    slot_num: str,
) -> str:
    """Remove one vNIC by its admitted partition-local slot identity."""
    command = (
        "chhwres -r virtualio --rsubtype vnic -o r"
        f" -m {shlex.quote(system_name)} -p {shlex.quote(lpar_name)}"
        f" -s {shlex.quote(slot_num)}"
    )
    return await run_hmc_command(config, command)


SriovMode = Literal["sriov", "dedicated"]
_VALID_SRIOV_MODES = frozenset(get_args(SriovMode))


def validate_sriov_mode(mode: SriovMode) -> SriovMode:
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


# ---------------------------------------------------------------------- #
# LPAR profile backup/restore/sync and I/O slot assignment (bkprofdata /
# rstprofdata / chsyscfg — no REST equivalent)
# ---------------------------------------------------------------------- #


