"""MCP tools that run HMC CLI commands over SSH (no REST equivalent).
"""

from __future__ import annotations

import shlex
from typing import Any

from ._app import (
    _DESTRUCTIVE,
    _READ_ONLY,
    _lpar_name,
    _run,
    _system_name,
    mcp,
)

from .common import client_from_env
from .config import HMCConfig
from .ssh import (
    list_io_slots,
    list_memory_pools,
    remove_memory_pool,
    run_hmc_command,
)



@mcp.tool(annotations=_READ_ONLY)
def hmc_get_lpar_description(system_uuid: str, lpar_uuid: str) -> str:
    """Get the description field of an LPAR via the HMC CLI.

    Runs ``lssyscfg -r lpar -m <system_name> --filter lpar_names=<lpar_name>
    -F description`` on the HMC via SSH and returns the raw output (the
    description string, or an empty line if none is set).

    This field is not available via the HMC REST API; it is the same
    description visible in the HMC GUI Partitions tab.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid)
        config = HMCConfig()
        cmd = f"lssyscfg -r lpar -m {shlex.quote(system_name)} --filter lpar_names={shlex.quote(lpar_name)} -F description"
        return await run_hmc_command(config, cmd)

    return _run(_go())


@mcp.tool
def hmc_set_lpar_description(system_uuid: str, lpar_uuid: str, description: str) -> str:
    """Set the description field of an LPAR via the HMC CLI.

    Runs ``chsyscfg -r lpar -m <system_name>
    -i "name=<lpar_name>,description=<description>"`` on the HMC via SSH.

    This field is not settable via the HMC REST API. The description appears
    in the HMC GUI Partitions tab and is useful for recording partition
    ownership, purpose, or current task.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs.

    WARNING: This modifies the LPAR configuration on the HMC. Confirm
    lpar_uuid and system_uuid before calling.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid)
        config = HMCConfig()
        payload = f"name={lpar_name},description={description}"
        cmd = f"chsyscfg -r lpar -m {shlex.quote(system_name)} -i {shlex.quote(payload)}"
        return await run_hmc_command(config, cmd)

    return _run(_go())


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_lpar_msp(system_uuid: str, lpar_uuid: str) -> bool:
    """Get the MSP (Migratable Service Partition) flag of an LPAR via the HMC CLI.

    Runs ``lssyscfg -r lpar -m <system_name> --filter lpar_names=<lpar_name>
    -F msp`` on the HMC via SSH and returns ``True`` if the flag is ``1``,
    ``False`` if ``0``.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid)
        config = HMCConfig()
        cmd = f"lssyscfg -r lpar -m {shlex.quote(system_name)} --filter lpar_names={shlex.quote(lpar_name)} -F msp"
        raw = await run_hmc_command(config, cmd)
        return raw.strip() == "1"

    return _run(_go())


@mcp.tool
def hmc_set_lpar_msp(system_uuid: str, lpar_uuid: str, enabled: bool) -> str:
    """Set the MSP (Migratable Service Partition) flag of an LPAR via the HMC CLI.

    Runs ``chsyscfg -r lpar -m <system_name>
    -i "name=<lpar_name>,msp=<0|1>"`` on the HMC via SSH.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs.

    WARNING: This modifies the LPAR configuration on the HMC. Confirm
    lpar_uuid and system_uuid before calling.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid)
        config = HMCConfig()
        value = "1" if enabled else "0"
        payload = f"name={lpar_name},msp={value}"
        cmd = f"chsyscfg -r lpar -m {shlex.quote(system_name)} -i {shlex.quote(payload)}"
        return await run_hmc_command(config, cmd)

    return _run(_go())




@mcp.tool(annotations=_READ_ONLY)
def hmc_get_proc_compat_modes(system_uuid: str) -> list[str]:
    """Get processor compatibility modes supported by a managed system.

    Runs ``lssyscfg -r sys -m <system_name> -F lpar_proc_compat_modes``
    on the HMC via SSH and returns a list of supported mode strings.

    The system UUID is resolved to its CLI name via REST before the command
    runs.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
        config = HMCConfig()
        cmd = f"lssyscfg -r sys -m {shlex.quote(system_name)} -F lpar_proc_compat_modes"
        raw = await run_hmc_command(config, cmd)
        if not raw.strip():
            return []
        return [mode.strip() for mode in raw.strip().split(",") if mode.strip()]

    return _run(_go())


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_lpar_proc_compat(system_uuid: str, lpar_uuid: str) -> dict[str, str]:
    """Get the current and pending processor compatibility modes for an LPAR.

    Runs ``lssyscfg -r lpar -m <system_name> --filter lpar_names=<lpar_name>
    -F pend_lpar_proc_compat_mode,curr_lpar_proc_compat_mode`` on the HMC via SSH.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs.

    Returns a dict with keys "pend" and "curr".

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid)
        config = HMCConfig()
        cmd = f"lssyscfg -r lpar -m {shlex.quote(system_name)} --filter lpar_names={shlex.quote(lpar_name)} -F pend_lpar_proc_compat_mode,curr_lpar_proc_compat_mode"
        raw = await run_hmc_command(config, cmd)
        if not raw.strip():
            return {"pend": "", "curr": ""}
        parts = raw.strip().split(",")
        pend = parts[0].strip() if len(parts) > 0 else ""
        curr = parts[1].strip() if len(parts) > 1 else ""
        return {"pend": pend, "curr": curr}

    return _run(_go())


@mcp.tool
def hmc_set_lpar_proc_compat(system_uuid: str, lpar_uuid: str, mode: str) -> str:
    """Set the processor compatibility mode of an LPAR.

    Runs ``chsyscfg -r lpar -m <system_name> -i "name=<lpar_name>,lpar_proc_compat_mode=<mode>"``
    on the HMC via SSH.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs.

    WARNING: This modifies the LPAR configuration on the HMC. Confirm
    lpar_uuid, system_uuid, and mode before calling.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid)
        config = HMCConfig()
        payload = f"name={lpar_name},lpar_proc_compat_mode={mode}"
        cmd = f"chsyscfg -r lpar -m {shlex.quote(system_name)} -i {shlex.quote(payload)}"
        return await run_hmc_command(config, cmd)

    return _run(_go())




@mcp.tool(annotations=_READ_ONLY)
def hmc_list_io_slots(
    system_uuid: str,
    adapter_type: str = "all",
) -> list[dict[str, Any]]:
    """List physical I/O slots on a managed system via the HMC CLI.

    Runs ``lshwres -r io --rsubtype slot -m <system_name>`` on the HMC via
    SSH and returns one dict per slot.  Each dict includes fields such as
    ``drc_name``, ``pci_class``, ``feature_codes``, and ``lpar_name``
    (empty string when the slot is unassigned).

    The system UUID is resolved to its CLI name via REST before the command
    runs.

    adapter_type filters by PCI class:
      - ``"all"``   — return every slot (default)
      - ``"eth"``   — Ethernet adapters (PCI class 0200)
      - ``"sas"``   — SAS/SCSI adapters (PCI class 0104)
      - ``"san"``   — Fibre Channel / SAN adapters (PCI class 0C04)
      - ``"nvme"``  — NVMe adapters (PCI class 0108)

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
        config = HMCConfig()
        return await list_io_slots(config, system_name, adapter_type)

    return _run(_go())




@mcp.tool(annotations=_READ_ONLY)
def hmc_list_memory_pools(system_uuid: str) -> list[dict[str, Any]]:
    """List shared memory pools on a managed system via the HMC CLI.

    Runs ``lshwres -r mempool -m <system_name>`` on the HMC via SSH and
    returns one dict per pool with fields such as ``pool_name``, ``size``,
    ``lpar_names``, and ``curr_lpar_names`` (comma-separated).

    The system UUID is resolved to its CLI name via REST before the command
    runs.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
        return await list_memory_pools(HMCConfig(), system_name)

    return _run(_go())


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_remove_memory_pool(system_uuid: str, pool_name: str) -> str:
    """Remove a shared memory pool from a managed system via the HMC CLI.

    Before issuing the remove command, fetches the current pool list and
    checks whether any LPARs are still assigned to *pool_name*.  If any are
    found the command is **not** executed and an ``HMCCLIError`` naming the
    blocking LPARs is raised instead.

    Runs ``chhwres -r mempool -m <system_name> -o r -a <pool_name>`` on
    the HMC via SSH when no LPARs are assigned.

    The system UUID is resolved to its CLI name via REST before the command
    runs.

    WARNING: This permanently removes the pool — confirm system_uuid and
    pool_name before calling. Returns the HMC CLI output (immediate delete —
    no job to poll).

    Auth: same env-var configuration as hmc_run_command (see module docstring).

    Raises:
        HMCCLIError: If *pool_name* still has LPARs assigned to it.
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
        return await remove_memory_pool(HMCConfig(), system_name, pool_name)

    return _run(_go())


