"""Shared-memory-pool commands over the SSH transport."""

from __future__ import annotations

import shlex
from typing import Any

from ..config import HMCConfig
from .transport import HMCCLIError, run_hmc_command
from .commands import _parse_lshwres_output, _validated_value

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
    # The `-a` value here is a bare pool name, not an attribute record
    # (ADR 0061); validate it against the same delimiter table before the
    # round trip so a bad name fails locally.
    _validated_value("pool_name", pool_name, surface="chhwres -a value")

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


