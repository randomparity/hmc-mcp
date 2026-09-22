"""Answer whether a live run would start, and what it would touch.

The runner already validates configuration and credentials before its first
dispatch. What it cannot do is answer the question without
being the run. This script asks the same validators the runner gates on, so
there is one definition of a valid configuration, and adds the two facts the
runner never establishes: what a selected arm will create, mutate and delete,
and whether the managed system is inside the ADR 0053 admitted envelope.

Usage:
    uv run --no-sync python scripts/live_test_preflight.py [--group NAME]
    uv run --no-sync python scripts/live_test_preflight.py --skip-hardware

Exit 0 means the runner would start. Non-zero means it would not, for a reason
named in the output.

**Configuration is blocking; hardware is advisory.** An unreachable HMC or an
out-of-envelope managed system is reported as a predicted SKIP and does not
change the exit status: the arm already SKIPs correctly on both, and a blocking
check here would be a second copy of an admission rule ADR 0053 moves.

The per-arm verdict is a prediction. An arm decides for itself at run time and
may SKIP where preflight said RUNNABLE.

No `HMC_*` value is ever printed — credentials report as present or absent.
Scenario settings are printed in full: naming the managed system a run will
create and delete partitions on is the point of the verdict.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import live_test_runner as runner
from live_test import pcie

from hmc_mcp.config import HMCConfig, env_var_value
from hmc_mcp.operations.virtualization.pcie import (
    require_admitted_environment,
)

#: The credential names the runner needs resolved before it dispatches. Reported
#: as present or absent and never by value: this output is meant to be pasted
#: into an issue when a run will not start.
_CREDENTIAL_KEYS = ("HMC_HOST", "HMC_USER", "HMC_PASSWORD")


@dataclass(frozen=True)
class ArmVerdict:
    """One arm's predicted runnability and the hardware it would mutate."""

    group: str
    runnable: bool
    reason: str
    mutates: tuple[str, ...] = ()
    #: The managed system whose envelope decides this arm, when it has one.
    system_name: str | None = None


def _dedicated_verdict(config: runner.LiveTestConfig) -> ArmVerdict:
    """Predict the dedicated PCIe arm from the predicate the arm itself uses.

    `pcie._dedicated_config` is called rather than restated: it applies the
    delimiter rejection and the no-fallback rule that decide whether the arm
    runs at all, and a second copy here would drift from the one that governs.
    """
    resolved = pcie._dedicated_config(config)
    if resolved is None:
        return ArmVerdict(
            "dedicated",
            False,
            "LIVE_TEST_DEDICATED_PCIE_SYSTEM_NAME and _LPAR_PREFIX must both be "
            "set, and no value may carry an HMC record delimiter",
        )
    return ArmVerdict(
        "dedicated",
        True,
        "configured",
        (
            f"managed system {resolved.system_name}",
            f"partitions named {resolved.lpar_prefix}* (created, then deleted)",
            f"profile {resolved.profile_name} io_slots (assigned, then restored)",
            f"dedicated slot {resolved.drc_index or '(first unassigned)'}",
        ),
        resolved.system_name,
    )


def _generic_verdict(group: str, config: runner.LiveTestConfig) -> ArmVerdict:
    """The arms whose settings `LiveTestConfig` supplies with defaults.

    These have no arm-specific admission predicate to import: `from_env_file`
    has already validated every key they read, so reaching here means they are
    configured. What they mutate is not enumerated, because the runner resolves
    it against the HMC at dispatch rather than from configuration.
    """
    return ArmVerdict(
        group,
        True,
        "configuration validated",
        (f"managed system {config.system_name}",),
        config.system_name,
    )


def arm_verdicts(
    config: runner.LiveTestConfig, group: str | None
) -> tuple[ArmVerdict, ...]:
    """Predict each selected arm. `None` selects every arm."""
    selected = tuple(runner.SUBTASK_GROUPS) if group is None else (group,)
    return tuple(
        _dedicated_verdict(config) if name == "dedicated" else _generic_verdict(name, config)
        for name in selected
        if name != "all"
    )


async def _probe_environment(system_name: str) -> str:
    """Report the ADR 0053 envelope for one managed system, as a display string.

    Delegates to `require_admitted_environment`, the predicate the operations
    layer enforces, so preflight cannot admit a system the operation refuses.
    Any failure below that — DNS, TLS, auth, an absent system — is reported as
    unknown rather than raised: hardware findings are advisory here.
    """
    try:
        await require_admitted_environment(HMCConfig(), system_name)
    except Exception as exc:  # noqa: BLE001 - every hardware failure is advisory
        return f"not admitted or unreachable ({type(exc).__name__})"
    return "admitted (ADR 0053 envelope)"


def _check_configuration() -> tuple[runner.LiveTestConfig | None, str]:
    """Run the runner's own `.env` validator and report its verdict."""
    try:
        return runner.LiveTestConfig.from_env_file(), ""
    except ValueError as exc:
        return None, str(exc)


def _check_credentials() -> tuple[bool, dict[str, bool]]:
    """Resolve credentials the way the runner does, reporting presence only.

    `_bootstrap_config` prints its own diagnostics and, on the TOML path,
    announces the profile it loaded. That output is captured and discarded
    rather than relayed: the caller gets a per-key present/absent map, which
    says what to fix without disclosing what resolved.
    """
    with contextlib.redirect_stdout(io.StringIO()):
        resolved = runner._bootstrap_config()
        # The same pair, in the same order, as the runner's own startup gate:
        # `_bootstrap_config` reads `.env` only when the TOML profile fails, so
        # without this a profile-resolved host reports a `.env`-only `HMC_*`
        # value as absent and names a request environment the run will not use.
        runner._load_dotenv()
    return resolved, {key: env_var_value(key) is not None for key in _CREDENTIAL_KEYS}


def _print_arms(verdicts: tuple[ArmVerdict, ...], envelopes: dict[str, str]) -> None:
    print("\npredicted arms — the run decides; a RUNNABLE arm may still SKIP")
    for verdict in verdicts:
        state = "RUNNABLE" if verdict.runnable else "SKIP"
        print(f"  {verdict.group:<10} {state:<8} {verdict.reason}")
        for target in verdict.mutates:
            print(f"    will mutate: {target}")
        envelope = envelopes.get(verdict.system_name or "")
        if envelope is not None:
            print(f"    envelope:    {envelope}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--group",
        choices=tuple(runner.SUBTASK_GROUPS),
        help="predict one arm instead of every arm",
    )
    parser.add_argument(
        "--skip-hardware",
        action="store_true",
        help="predict from configuration alone, contacting no HMC",
    )
    args = parser.parse_args(argv)

    config, config_error = _check_configuration()
    credentials_ok, present = _check_credentials()
    verdicts = arm_verdicts(config, args.group) if config is not None else ()

    envelopes: dict[str, str] = {}
    if verdicts and credentials_ok and not args.skip_hardware:
        systems = {v.system_name for v in verdicts if v.system_name and v.runnable}
        for system in sorted(systems):
            envelopes[system] = asyncio.run(_probe_environment(system))

    print("live-test preflight")
    print("=" * 60)
    if config is None:
        print(f"configuration  FAIL  {config_error}")
    else:
        print("configuration  OK    .env validated by LiveTestConfig.from_env_file")
    detail = " ".join(f"{k}={'set' if v else 'MISSING'}" for k, v in present.items())
    print(f"credentials    {'OK  ' if credentials_ok else 'FAIL'}  {detail}")
    # Reported, never gated: the variable is opt-in and a run starts either way
    # (#875). It is on its own row because `MISSING` on the credentials row read
    # as a defect to fix, which is what made the runner demand it.
    pinned = "set" if env_var_value("HMC_SCHEMA_VERSION") else "not set"
    print(
        f"schema version INFO  HMC_SCHEMA_VERSION={pinned} — optional; recorded "
        "so a run's evidence names its request environment (docs/compatibility.md)"
    )
    if verdicts:
        _print_arms(verdicts, envelopes)

    if config is None or not credentials_ok:
        print("\nthe runner would not start — fix the FAIL rows above")
        return 1
    print("\nthe runner would start")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
