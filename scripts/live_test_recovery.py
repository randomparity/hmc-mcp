"""Confirm from outside a run that the dedicated PCIe arm left nothing behind.

The arm's cleanup guards refuse to mutate on a mismatch and emit a manual-
recovery row, which is correct. But the only witness that recovery happened is
the same run that failed to complete it. This script is the outside witness.

Usage:
    uv run --no-sync python scripts/live_test_recovery.py
    uv run --no-sync python scripts/live_test_recovery.py --results PATH

Exit 0 means nothing is stranded. Exit 1 means something is, and the output
names it with the command that clears it. Exit 2 means the state could not be
read, which is not the same as clean.

**This never remediates.** It issues no mutating call: a remediator acting on
a partial read strands exactly what the arm's cleanup guards exist to refuse.
Every command it prints is for a human to run and check.

Its inputs come from the run's own results document — `artifacts.pcie_run_marker`
and the three beside it. The marker is per-run random (`pcie-<8 hex>`), so a
run's traces cannot be identified without them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import live_test_runner as runner
from live_test.pcie import _io_slots_contains

from hmc_mcp.authorization.access_policy import DEFAULT_CONNECTION_TOKEN
from hmc_mcp.cli_commands.legacy_policy import compile_legacy_policy
from hmc_mcp.operations.lpar.ownership import parse_lpar_ownership_caller_token
from hmc_mcp.server import TOOL_SECURITY, create_mcp

#: Every tool this script may call. Enforced on the call path rather than left
#: to review: the whole point of the script is that it cannot make things worse
#: while checking whether they are bad.
_READ_ONLY_TOOLS = frozenset(
    {"hmc_list_dedicated_pcie_slots", "hmc_get_lpar_description", "hmc_run_command"}
)

#: `hmc_run_command` is read-only only for the command given. `lssyscfg` lists;
#: `chsyscfg` would mutate, and shares the tool.
_READ_ONLY_COMMAND_PREFIX = "lssyscfg"


class MutatingCallRefused(RuntimeError):
    """Raised when a call would leave the read-only surface."""


@dataclass(frozen=True)
class RecoveryInputs:
    """What one run created, as recorded in its results document."""

    system_name: str
    run_marker: str
    fixture_lpar: str
    drc_index: str | None
    baseline_io_slots: str | None
    profile_name: str


@dataclass(frozen=True)
class Finding:
    """One thing a run left behind, and the command that clears it."""

    what: str
    detail: str
    remedy: str


def inputs_from_document(document: Any) -> RecoveryInputs | None:
    """Read a run's recovery inputs, or `None` when it recorded none.

    A run that never reached ST29 created nothing, so an absent marker means
    there is nothing to look for — not that the check could not run.
    """
    if not isinstance(document, dict):
        return None
    artifacts = document.get("artifacts")
    config = document.get("config")
    if not isinstance(artifacts, dict) or not isinstance(config, dict):
        return None
    marker = artifacts.get("pcie_run_marker")
    fixture = artifacts.get("pcie_fixture_lpar")
    system = config.get("dedicated_pcie_system_name")
    if not marker or not fixture or not system:
        return None
    return RecoveryInputs(
        system_name=str(system),
        run_marker=str(marker),
        fixture_lpar=str(fixture),
        drc_index=artifacts.get("pcie_drc_index"),
        baseline_io_slots=artifacts.get("pcie_baseline_io_slots"),
        profile_name=str(config.get("dedicated_pcie_profile_name") or "default"),
    )


def guard_read_only(tool: str, arguments: dict[str, Any]) -> None:
    """Refuse a call that could change the managed system."""
    if tool not in _READ_ONLY_TOOLS:
        raise MutatingCallRefused(f"{tool} is not on the read-only allowlist")
    command = arguments.get("cmd", "")
    if tool == "hmc_run_command" and not str(command).strip().startswith(
        _READ_ONLY_COMMAND_PREFIX
    ):
        raise MutatingCallRefused(
            f"hmc_run_command is read-only only for {_READ_ONLY_COMMAND_PREFIX}; "
            f"refused: {str(command).split()[0] if command else '(empty)'}"
        )


async def _surviving_fixture(call, inputs: RecoveryInputs) -> Finding | None:
    """Whether a partition this run created is still there.

    Ownership is confirmed by the run marker before reporting, so a partition
    that merely shares the name is never attributed to this run — the same rule
    the arm applies before it will delete anything.
    """
    status, data = await call(
        "hmc_get_lpar_description",
        system_name_or_uuid=inputs.system_name,
        lpar_name_or_uuid=inputs.fixture_lpar,
    )
    if status != "PASS" or not isinstance(data, str):
        return None
    if parse_lpar_ownership_caller_token(data) != inputs.run_marker:
        return None
    return Finding(
        "surviving partition",
        f"{inputs.fixture_lpar} on {inputs.system_name} still exists and carries "
        f"this run's marker {inputs.run_marker}",
        f"hmc rmsyscfg -m {inputs.system_name} -r lpar -n {inputs.fixture_lpar}",
    )


async def _stranded_slot(call, inputs: RecoveryInputs) -> Finding | None:
    """Whether the dedicated slot is still owned rather than back in the pool."""
    if inputs.drc_index is None:
        return None
    status, data = await call(
        "hmc_list_dedicated_pcie_slots", system_name_or_uuid=inputs.system_name
    )
    if status != "PASS" or not isinstance(data, dict):
        return None
    for item in data.get("items") or []:
        if not isinstance(item, dict) or item.get("drc_index") != inputs.drc_index:
            continue
        owner = (item.get("owner_lpar") or "").strip()
        if not owner or owner == "null":
            return None
        return Finding(
            "stranded slot",
            f"dedicated slot {inputs.drc_index} on {inputs.system_name} is owned "
            f"by {owner}; the run left it assigned",
            f"hmc chsyscfg -m {inputs.system_name} -r prof -i "
            f'"name={inputs.profile_name},lpar_name={owner},'
            f'io_slots-={inputs.drc_index}//0"',
        )
    return None


async def _profile_drift(call, inputs: RecoveryInputs) -> Finding | None:
    """Whether the fixture profile's io_slots still differs from its baseline."""
    if inputs.baseline_io_slots is None or inputs.drc_index is None:
        return None
    status, data = await call(
        "hmc_run_command",
        cmd=(
            f"lssyscfg -m {inputs.system_name} -r prof "
            f'--filter "lpar_names={inputs.fixture_lpar}" -F io_slots'
        ),
    )
    if status != "PASS" or not isinstance(data, str):
        return None
    records = [line.strip() for line in data.splitlines() if line.strip()]
    if len(records) != 1:
        return None
    observed = records[0]
    if observed == inputs.baseline_io_slots:
        return None
    still_assigned = _io_slots_contains(observed, inputs.drc_index)
    return Finding(
        "profile drift",
        f"profile io_slots for {inputs.fixture_lpar} is {observed!r}, not the "
        f"captured baseline {inputs.baseline_io_slots!r}"
        + (f"; slot {inputs.drc_index} is still listed" if still_assigned else ""),
        f"hmc chsyscfg -m {inputs.system_name} -r prof -i "
        f'"name={inputs.profile_name},lpar_name={inputs.fixture_lpar},'
        f'io_slots={inputs.baseline_io_slots}"',
    )


async def check(call, inputs: RecoveryInputs) -> list[Finding]:
    """Every stranded condition, in the order an operator should clear them."""
    found = [
        await _surviving_fixture(call, inputs),
        await _stranded_slot(call, inputs),
        await _profile_drift(call, inputs),
    ]
    return [finding for finding in found if finding is not None]


def _read_only_caller(client, state: runner.RunState):
    """A call path that refuses anything off the read-only surface."""

    async def call(tool: str, **arguments: Any) -> tuple[str, Any]:
        guard_read_only(tool, arguments)
        return await state.call(client, tool, **arguments)

    return call


async def _run_checks(inputs: RecoveryInputs) -> list[Finding]:
    from fastmcp import Client

    policy = compile_legacy_policy(TOOL_SECURITY, (DEFAULT_CONNECTION_TOKEN,))
    state = runner.RunState(config=runner.LiveTestConfig())
    async with Client(create_mcp(policy)) as client:
        return await check(_read_only_caller(client, state), inputs)


def _report(inputs: RecoveryInputs, findings: list[Finding]) -> None:
    print(f"recovery check for run marker {inputs.run_marker}")
    print("=" * 60)
    if not findings:
        print(f"CLEAN  nothing carrying {inputs.run_marker} survives on "
              f"{inputs.system_name}")
        return
    for finding in findings:
        print(f"STRANDED  {finding.what}")
        print(f"          {finding.detail}")
        print(f"  clear with:  {finding.remedy}")
    print("\nThis script issues no mutating call. Run the commands above "
          "yourself, then re-run this check.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=Path("test-results-dedicated.json"),
        help="the run's results document (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    try:
        document = json.loads(args.results.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        print(f"ERROR: cannot read {args.results}: {error}", file=sys.stderr)
        return 2

    inputs = inputs_from_document(document)
    if inputs is None:
        print(
            f"{args.results} records no dedicated PCIe fixture — that run created "
            "nothing to recover."
        )
        return 0

    if not runner._bootstrap_config():
        print("ERROR: no HMC credentials; cannot check the system", file=sys.stderr)
        return 2

    try:
        findings = asyncio.run(_run_checks(inputs))
    except MutatingCallRefused as refused:
        print(f"ERROR: refused a mutating call: {refused}", file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001 - an unreadable system is not a clean one
        print(f"ERROR: could not read the managed system: {error}", file=sys.stderr)
        return 2

    _report(inputs, findings)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
