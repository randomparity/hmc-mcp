"""SSH reference-code reads for partitions on a managed system."""

from __future__ import annotations

import shlex

from ..config import HMCConfig
from .commands import build_filter, parse_hmc_delimited_rows
from .transport import HMCCLIError, run_hmc_command

MAX_REFCODE_COUNT = 100
REFCODE_FIELDS = ("lpar_name", "time_stamp", "refcode")
_NO_RESULTS = "No results were found."


async def list_lpar_refcodes(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    count: int = 1,
) -> list[dict[str, str]]:
    """Read the *count* most recent reference codes for one partition.

    Rows come back most-recent first.  *count* is bounded because the whole
    result is returned to the caller in one message; ``count=1`` reproduces
    the HMC's own default of the current code alone.  Two guards protect two
    parsers, neither substituting for the other: :func:`build_filter` keeps
    the ``--filter`` record's ``,``, ``=`` and ``"`` structure meaningful to
    the HMC (ADR 0061), and :func:`shlex.quote` keeps each value one word for
    the remote shell, which runs first.

    Raises:
        TypeError: If *count* is not an ``int``.  ``bool`` is rejected too:
            it is an ``int`` subclass, so ``True`` would otherwise reach the
            command string as ``-n 1``.
        ValueError: If *count* is outside ``1..MAX_REFCODE_COUNT``.
        HMCCLIError: If the selector carries a record delimiter, the response
            header does not name the three fields, or the HMC refuses the
            command.
    """
    if isinstance(count, bool) or not isinstance(count, int):
        raise TypeError(f"count must be an int, got {type(count).__name__}")
    if not 1 <= count <= MAX_REFCODE_COUNT:
        raise ValueError(f"count must be between 1 and {MAX_REFCODE_COUNT}, got {count}")
    selector = build_filter([("lpar_names", lpar_name)])
    command = (
        f"lsrefcode -r lpar -m {shlex.quote(system_name)}"
        f" --filter {shlex.quote(selector)}"
        f" -n {count} -F {','.join(REFCODE_FIELDS)} --header"
    )
    raw = await run_hmc_command(config, command)
    # An empty HMC read exits 0 and prints a sentinel, not an empty string
    # (docs/HMC_HINTS.md); ssh/sriov.py, ssh/vnic.py and ssh/vios_labels.py
    # each guard the same literal.
    if not raw.strip() or raw.strip() == _NO_RESULTS:
        return []
    try:
        return parse_hmc_delimited_rows(raw, REFCODE_FIELDS)
    except ValueError as error:
        raise HMCCLIError(
            "lsrefcode response did not match the expected "
            f"{','.join(REFCODE_FIELDS)} fields"
        ) from error
