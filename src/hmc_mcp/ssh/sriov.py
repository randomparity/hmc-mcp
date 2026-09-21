"""SSH commands for SR-IOV inventory and logical-port mutation."""

from __future__ import annotations

import shlex
from typing import Literal, get_args

from ..config import HMCConfig
from .commands import (
    _parse_lshwres_output,
    build_attribute_record,
    build_filter,
    parse_hmc_delimited_rows,
)
from .transport import HMCCLIError, run_hmc_command

SriovMode = Literal["sriov", "dedicated"]
_VALID_SRIOV_MODES = frozenset(get_args(SriovMode))
_SRIOV_LOGICAL_FIELDS = ("config_id", "lpar_name", "lpar_id", "lpar_state", "adapter_id", "logical_port_id", "logical_port_type", "phys_port_id", "functional_state", "capacity", "max_capacity")


def validate_sriov_mode(mode: SriovMode) -> SriovMode:
    if mode not in _VALID_SRIOV_MODES:
        raise ValueError(f"Invalid mode {mode!r}. Must be one of: {', '.join(sorted(_VALID_SRIOV_MODES))}")
    return mode


def _parse_admitted_rows(output: str, fields: tuple[str, ...]) -> list[dict[str, str]]:
    if output.strip() == "No results were found.":
        return []
    try:
        return parse_hmc_delimited_rows(output, fields)
    except ValueError as exc:
        raise HMCCLIError(f"SR-IOV inventory response did not match the expected {','.join(fields)} fields") from exc


async def list_sriov_adapter_rows(config: HMCConfig, system_name: str) -> list[dict[str, str]]:
    fields = ("adapter_id", "slot_id", "config_state", "functional_state", "phys_loc", "phys_ports", "logical_ports", "adapter_max_logical_ports", "sriov_status")
    command = f"lshwres -r sriov --rsubtype adapter -m {shlex.quote(system_name)} -F {','.join(fields)} --header"
    return _parse_admitted_rows(await run_hmc_command(config, command), fields)


async def read_sriov_environment(config: HMCConfig, system_name: str) -> tuple[str, str]:
    return (await run_hmc_command(config, "lshmc -V")).strip(), (await run_hmc_command(config, f"lssyscfg -r sys -m {shlex.quote(system_name)} -F type_model")).strip()


async def list_sriov_physical_port_rows(config: HMCConfig, system_name: str, adapter_id: str) -> list[dict[str, str]]:
    fields = ("adapter_id", "phys_port_id", "phys_port_type", "phys_port_loc", "state", "config_logical_ports", "phys_port_max_logical_ports", "curr_eth_logical_ports")
    commands = [f"lshwres -r sriov --rsubtype physport -m {shlex.quote(system_name)} --level {level} --filter {shlex.quote(build_filter([('adapter_ids', adapter_id)]))} -F {','.join(fields)} --header" for level in ("roce", "ethc")]
    if not adapter_id.isascii() or not adapter_id.isdecimal() or int(adapter_id) <= 0:
        raise ValueError(f"adapter_id must be a positive decimal ID, got {adapter_id!r}")
    roce_output = await run_hmc_command(config, commands[0])
    ethc_output = await run_hmc_command(config, commands[1])
    roce_rows = _parse_admitted_rows(roce_output, fields)
    ethc_rows = _parse_admitted_rows(ethc_output, fields)
    if roce_rows and ethc_rows:
        raise HMCCLIError("physical port query returned both roce and ethc rows")
    result = roce_rows or ethc_rows
    if any(row["adapter_id"] != adapter_id for row in result):
        raise HMCCLIError(f"physical port row adapter_id does not match {adapter_id!r}")
    return result


async def list_sriov_configured_logical_port_rows(config: HMCConfig, system_name: str, adapter_id: str) -> list[dict[str, str]]:
    command = f"lshwres -r sriov --rsubtype logport -m {shlex.quote(system_name)} --level eth --filter {shlex.quote(build_filter([('adapter_ids', adapter_id)]))} -F {','.join(_SRIOV_LOGICAL_FIELDS)} --header"
    return _parse_admitted_rows(await run_hmc_command(config, command), _SRIOV_LOGICAL_FIELDS)


async def list_sriov_unconfigured_logical_port_rows(config: HMCConfig, system_name: str) -> list[dict[str, str]]:
    rows = _parse_lshwres_output(await run_hmc_command(config, f"lshwres -r sriov --rsubtype logport -m {shlex.quote(system_name)}"))
    return [dict(row) for row in rows if row.get("logical_port_type") == "unconfigured"]


async def read_sriov_lpar_state(config: HMCConfig, system_name: str, lpar_name: str) -> dict[str, str]:
    fields = ("name", "lpar_id", "state", "rmc_state")
    command = f"lssyscfg -r lpar -m {shlex.quote(system_name)} --filter {shlex.quote(build_filter([('lpar_names', lpar_name)]))} -F {','.join(fields)} --header"
    rows = _parse_admitted_rows(await run_hmc_command(config, command), fields)
    if len(rows) != 1:
        raise HMCCLIError(f"Expected one LPAR state row for {lpar_name!r}; got {len(rows)}")
    return rows[0]


async def read_sriov_profile_ports(config: HMCConfig, system_name: str, lpar_name: str, profile_name: str) -> dict[str, str]:
    fields = ("name", "sriov_eth_logical_ports")
    command = f"lssyscfg -r prof -m {shlex.quote(system_name)} --filter {shlex.quote(build_filter([('lpar_names', lpar_name), ('profile_names', profile_name)]))} -F {','.join(fields)}"
    lines = [line.strip() for line in (await run_hmc_command(config, command)).splitlines() if line.strip()]
    if len(lines) != 1:
        raise HMCCLIError(f"Expected one SR-IOV profile row for {profile_name!r}; got {len(lines)}")
    name, _, ports = lines[0].partition(",")
    return {"name": name.strip(), "sriov_eth_logical_ports": ports.strip()}


async def assign_sriov_logical_port_dynamic(config: HMCConfig, system_name: str, lpar_name: str, adapter_id: str, physical_port_id: str, logical_port_id: str, capacity: str) -> str:
    record = build_attribute_record([("adapter_id", adapter_id), ("phys_port_id", physical_port_id), ("logical_port_id", logical_port_id), ("logical_port_type", "eth"), ("capacity", capacity)])
    return await run_hmc_command(config, f"chhwres -r sriov --rsubtype logport -m {shlex.quote(system_name)} -o a -p {shlex.quote(lpar_name)} -a {shlex.quote(record)}")


async def unassign_sriov_logical_port_profile(config: HMCConfig, system_name: str, lpar_name: str, profile_name: str) -> str:
    record = build_attribute_record([("name", profile_name), ("lpar_name", lpar_name), ("sriov_eth_logical_ports", "none")])
    return await run_hmc_command(config, f"chsyscfg -r prof -m {shlex.quote(system_name)} -i {shlex.quote(record)}")
