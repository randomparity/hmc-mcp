"""Live integration test runner for the ltczz386 test plan — Round 2.

Calls HMC MCP tools via the in-process FastMCP client against the real HMC
configured in .env.  Results are printed to stdout as they complete and
written to test-results-round2.json on exit.

Usage:
    uv run python scripts/live_test_runner.py [SUBTASK_NUMBER]

If SUBTASK_NUMBER is omitted, all sub-tasks (ST0–ST15) are run in order.
If a specific number is given (0-15), only that sub-task runs.

Pre-run requirement: HMC_SCHEMA_VERSION=V1_0 must be set in .env.
The script warns and patches .env automatically if it is missing, then exits
so the updated environment is loaded on restart.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmcp import Client


from hmc_mcp.access_policy import DEFAULT_CONNECTION_TOKEN
from hmc_mcp.config import HMCConfig, env_var_value
from hmc_mcp.cli_commands.legacy_policy import compile_legacy_policy
from hmc_mcp.server import TOOL_SECURITY, _gates, create_mcp
from hmc_mcp.server_tools.command import configure_arbitrary_command_tool
from live_test.inventory import (
    capture_lpar_baseline,
    exercise_cli_escape_hatch,
    inspect_metrics_templates,
    inventory_connectivity,
    inventory_lpar_profiles,
    inventory_network,
    inventory_storage,
    inventory_users,
)
from live_test.lifecycle import (
    administer_test_user,
    exercise_lpar_lifecycle,
    exercise_storage_provisioning,
    inspect_metrics_jobs,
    mutate_lpar_properties,
    mutate_virtual_networking,
    restore_lpar_baseline,
    validate_provisioning_dry_run,
)
from live_test.vmedia import (
    IsoHttpServer,
    vmedia_boot_verification,
    vmedia_bootstrap_and_create_repo,
    vmedia_mapping_crossvalidation,
    vmedia_mount_unmount,
    vmedia_short_repo_lifecycle,
    vmedia_teardown,
    vmedia_upload_iso,
)

# ---------------------------------------------------------------------------
# Pre-run guard: HMC_SCHEMA_VERSION=V1_0 is required for REST write path
# ---------------------------------------------------------------------------

_ENV_FILE = Path(".env")

#: The `HMC_*` names whose reader folds their casing: `HMCConfig`'s own fields,
#: and only those. `HMC_PROFILE` and a profile's `password_env` target carry the
#: prefix but are looked up in `os.environ` directly (see the "Variable names are
#: matched without regard to case" section of docs/environment-variables.md), so
#: treating a `hmc_profile` export as already-set would suppress the `.env` line
#: spelling it canonically while nothing ever read the variant.
#:
#: Held folded **down**, and matched that way below, because that is the relation
#: pydantic-settings uses and the one `env_var_value` looks names up with. Over
#: Unicode the two directions are different relations: `hmc_ssh_Key_file`
#: lowers onto `ssh_key_file` and so is a name the loader reads, while its
#: upper-fold is not `HMC_SSH_KEY_FILE` — an upper-cased gate would miss it, fall
#: through to the exact-case test, and re-open the `.env`-outranks-the-export
#: inversion for exactly the names this set exists to cover.
_FOLDED_ENV_NAMES = frozenset(
    f"hmc_{field.lower()}" for field in HMCConfig.model_fields
)


def _already_set(name: str) -> bool:
    """Whether the environment already carries *name*, matched as its reader matches it.

    This is what makes an exported variable outrank `.env` and `config.toml` — the
    priority `_bootstrap_config` documents — for a case variant as well as the
    canonical spelling. An exact-case membership test did not recognise an
    exported `hmc_host` as an already-set `HMC_HOST`, so it injected the canonical
    name; a newly created key lands last in `os.environ` order and therefore wins
    the fold, and an operator who exported a lab host ran the destructive suite
    against the HMC `.env` named (#543).
    """
    if name.lower() in _FOLDED_ENV_NAMES:
        return env_var_value(name) is not None
    return name in os.environ


def _load_dotenv() -> None:
    """Load key=value pairs from .env into os.environ (simple, no deps)."""
    if not _ENV_FILE.exists():
        return
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and not _already_set(key):
            os.environ[key] = val


def _bootstrap_config() -> None:
    """Populate HMC_* env vars from config.toml profile, then .env fallback.

    Priority (highest first):
      1. Already-set HMC_* environment variables
      2. ~/.config/hmc-mcp/config.toml default profile
      3. Local .env file (legacy key=value pairs)

    Exits with a clear message when no usable credentials are found.
    """
    from hmc_mcp.config import ConfigError, load_profile, resolve_config_path

    # Try the TOML config first.
    try:
        cfg = load_profile()
        # Inject values that are not already set by the environment.
        mapping = {
            "HMC_HOST": cfg.host,
            "HMC_PORT": str(cfg.port),
            "HMC_USER": cfg.user,
            "HMC_PASSWORD": cfg.password,
            "HMC_VERIFY_SSL": str(cfg.verify_ssl).lower(),
            "HMC_SCHEMA_VERSION": cfg.schema_version,
        }
        for key, val in mapping.items():
            if val and not _already_set(key):
                os.environ[key] = val
        config_path = resolve_config_path()
        print(f"  Credentials loaded from {config_path} (profile: {cfg.host})")
        return
    except ConfigError as exc:
        print(f"  ⚠️  config.toml: {exc} — falling back to .env")

    # Fallback: local .env
    _load_dotenv()

    # env_var_value, not os.environ.get: this predicts whether `HMCConfig` will
    # resolve a password, and `HMCConfig` reads the name case-blind. An exact-case
    # test refused to start on a lower- or mixed-case `hmc_password` export that
    # would have connected (#543).
    if not env_var_value("HMC_PASSWORD"):
        print("❌  No HMC credentials found.")
        print("   Configure ~/.config/hmc-mcp/config.toml or a local .env file.")
        sys.exit(1)


def _ensure_schema_version() -> None:
    """Warn if HMC_SCHEMA_VERSION is absent; exit so the operator sets it explicitly.

    Note: HMC_SCHEMA_VERSION only affects GET requests — it has no effect on
    write-path HTTP 406 errors (those are fixed by suppressing the header on
    PUT/POST paths entirely).  We still require it to be present so that the
    test runner's GET paths behave deterministically, but we do not silently
    mutate .env — the operator must add it intentionally.
    """
    _load_dotenv()
    # env_var_value for the same reason as the credential pre-check above:
    # `schema_version` is an `HMCConfig` field, so an exact-case probe exits 1
    # telling the operator to set a variable a case variant has already set and
    # the server is already sending (#543).
    if env_var_value("HMC_SCHEMA_VERSION"):
        return
    print("⚠️  HMC_SCHEMA_VERSION is not set in .env or the environment.")
    print("   Add 'HMC_SCHEMA_VERSION=V1_0' to your .env file and re-run.")
    print("   Note: this variable only affects GET requests; it does NOT fix")
    print("   HTTP 406 on write paths (LPAR create, adapter PUT, etc.).")
    sys.exit(1)


@dataclass
class LiveTestContext:
    """Identifiers and snapshots belonging to one live-test execution."""

    system_name: str = "ltczz386"
    lp3_name: str = "ltczz386-lp3"
    scratch_name: str = "ltczz386-lp3-test"
    nettest_name: str = "ltczz386-lp3-nettest"
    test_user: str = "hmc-mcp-testuser"
    system_uuid: str | None = None
    lp3_uuid: str | None = None
    scratch_uuid: str | None = None
    vios_uuid: str | None = None
    vios_partition_id: int | None = None
    console_uuid: str | None = None
    test_vlan_id: int | None = None
    test_vswitch_id: int | None = None
    test_network_uuid: str | None = None
    test_adapter_uuid: str | None = None
    nettest_uuid: str | None = None
    job_uuid_sample: str | None = None
    vg_uuid: str | None = None
    vdisk_name: str = "VG1-lp3"
    vdisk_vg_name: str | None = None
    vdisk_size_mb: int | None = None
    lp3_baseline: dict[str, Any] = field(default_factory=dict)
    # Virtual-media round (ST16–ST22)
    vmedia_repo_created: bool = False
    vmedia_iso_name: str | None = None
    vmedia_mapping_uuid: str | None = None
    vmedia_orig_boot_order: list[str] = field(default_factory=list)


@dataclass
class RunState:
    """Mutable output owned by a single invocation of the live runner."""

    context: LiveTestContext = field(default_factory=LiveTestContext)
    results: list[dict[str, Any]] = field(default_factory=list)
    iso_http_server: IsoHttpServer = field(default_factory=IsoHttpServer)

    async def call(self, client: Client, tool: str, **kwargs: Any) -> tuple[str, Any]:
        """Call a tool and return a PASS or FAIL result without raising."""
        try:
            result = await client.call_tool(tool, kwargs)
            if hasattr(result, "data") and result.data is not None:
                return "PASS", result.data
            if hasattr(result, "content"):
                parts = [
                    str(block.text) if hasattr(block, "text") else str(block)
                    for block in result.content
                ]
                text = "\n".join(parts)
            else:
                text = str(result)
            try:
                data = json.loads(text)
            except Exception:
                data = text
            return "PASS", data
        except Exception as exc:
            return "FAIL", f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"

    def record(
        self, subtask: int, tool: str, status: str, data: Any, note: str = ""
    ) -> None:
        """Append and print one result entry."""
        entry = {
            "subtask": subtask,
            "tool": tool,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "note": note,
            "data": data if isinstance(data, (dict, list)) else str(data)[:2000],
        }
        self.results.append(entry)
        icon = "✅" if status == "PASS" else ("⚠️" if status == "SKIP" else "❌")
        note_str = f" — {note}" if note else ""
        print(f"  {icon} ST{subtask} {tool}{note_str}")
        if status == "FAIL":
            print(f"     ERROR: {str(data)[:300]}")

    def skip(self, subtask: int, tool: str, reason: str) -> None:
        """Record a skipped operation."""
        self.record(subtask, tool, "SKIP", None, reason)

    def record_expected_or_real(
        self,
        subtask: int,
        tool: str,
        status: str,
        data: Any,
        expected_fail_substrings: list[str],
        skip_reason: str,
    ) -> None:
        """Turn a known HMC limitation into SKIP; record other outcomes verbatim."""
        if status == "FAIL" and any(
            text.lower() in str(data).lower() for text in expected_fail_substrings
        ):
            self.skip(subtask, tool, skip_reason)
            return
        self.record(subtask, tool, status, data)


# ---------------------------------------------------------------------------
# ST0 — Capture ltczz386-lp3 Baseline
# ---------------------------------------------------------------------------


SUBTASKS = {
    0: capture_lpar_baseline,
    1: inventory_connectivity,
    2: inventory_network,
    3: inventory_storage,
    4: inventory_lpar_profiles,
    5: inspect_metrics_templates,
    6: inventory_users,
    7: exercise_cli_escape_hatch,
    8: exercise_lpar_lifecycle,
    9: mutate_virtual_networking,
    10: mutate_lpar_properties,
    11: administer_test_user,
    12: inspect_metrics_jobs,
    13: validate_provisioning_dry_run,
    14: exercise_storage_provisioning,
    15: restore_lpar_baseline,
    16: vmedia_bootstrap_and_create_repo,
    17: vmedia_short_repo_lifecycle,
    18: vmedia_upload_iso,
    19: vmedia_mount_unmount,
    20: vmedia_boot_verification,
    21: vmedia_mapping_crossvalidation,
    22: vmedia_teardown,
}

SUBTASK_GROUPS: dict[str, list[int]] = {
    "round2": list(range(0, 16)),
    "vmedia": list(range(16, 23)),
    "all": list(range(0, 23)),
}


def _restore_ctx_from_results(
    state: RunState,
    results_path: str = "test-results-round2.json",
) -> None:
    """Pre-seed context from the previous results file when running a single sub-task.

    This allows sub-tasks run in isolation (e.g. `python runner.py 3`) to use
    context captured by earlier sub-tasks (VIOS UUID, system UUID, etc.).
    """
    p = Path(results_path)
    try:
        if not p.exists():
            return
        saved = json.loads(p.read_text())
        if not isinstance(saved, dict):
            raise TypeError("results document must be a JSON object")
        saved_ctx = saved.get("context")
        if saved_ctx is None:
            saved_ctx = {}
        elif not isinstance(saved_ctx, dict):
            raise TypeError("results context must be a JSON object")
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, KeyError) as exc:
        print(f"  ⚠️  Could not restore context from {results_path}: {exc}")
        return

    context = state.context
    for key, current in asdict(context).items():
        if current is None and saved_ctx.get(key) is not None:
            setattr(context, key, saved_ctx[key])
        elif key == "lp3_baseline" and not current and saved_ctx.get(key):
            context.lp3_baseline = saved_ctx[key]
        elif key == "vmedia_orig_boot_order" and not current and saved_ctx.get(key):
            context.vmedia_orig_boot_order = saved_ctx[key]
    print(
        f"  ℹ  Context restored from {results_path} "
        f"(vios_uuid={context.vios_uuid}, "
        f"system_uuid={context.system_uuid}, "
        f"vg_uuid={context.vg_uuid})"
    )


async def main(
    subtask_filter: int | None = None,
    results_path: str = "test-results-round2.json",
    group: str | None = None,
) -> int:
    state = RunState()
    context = state.context
    print(
        f"Starting live integration tests at {datetime.now(timezone.utc).isoformat()}"
    )
    schema_version = env_var_value("HMC_SCHEMA_VERSION") or "(not set)"
    print(f"HMC_SCHEMA_VERSION={schema_version}")

    # Determine which sub-tasks to run
    if subtask_filter is not None:
        tasks = [subtask_filter]
    elif group is not None:
        tasks = SUBTASK_GROUPS.get(group, [])
        if not tasks:
            print(f"Unknown group {group!r}. Valid groups: {', '.join(SUBTASK_GROUPS)}")
            return 1
    else:
        tasks = sorted(SUBTASKS.keys())

    # Restore prior context when running a subset
    if subtask_filter is not None or group is not None:
        # Try vmedia results first, then round2
        for prior in ["test-results-vmedia.json", "test-results-round2.json"]:
            if Path(prior).exists():
                _restore_ctx_from_results(state, prior)
                break

    # The escape hatch is opted in because this harness drives `hmc_run_command`
    # against a real HMC. `permits` and `authorize` come from the policy just composed:
    # calling the toggle with neither would register the tool whatever the policy says
    # and leave its handler unwrapped, so the live run would stop being evidence about
    # the path an operator actually takes.
    policy = compile_legacy_policy(
        TOOL_SECURITY, (DEFAULT_CONNECTION_TOKEN,), include_arbitrary_command=True
    )
    mcp = create_mcp(policy)
    permits, authorize = _gates(policy)
    await configure_arbitrary_command_tool(
        True, mcp, permits=permits, authorize=authorize
    )
    try:
        async with Client(mcp) as client:
            for n in tasks:
                fn = SUBTASKS.get(n)
                if fn:
                    await fn(client, state)
                else:
                    state.record(n, "runner", "FAIL", f"Unknown sub-task {n}")
    finally:
        state.iso_http_server.close()

    Path(results_path).write_text(
        json.dumps(
            {"context": asdict(context), "results": state.results},
            indent=2,
            default=str,
        )
    )

    total = len(state.results)
    passed = sum(1 for r in state.results if r["status"] == "PASS")
    failed = sum(1 for r in state.results if r["status"] == "FAIL")
    skipped = sum(1 for r in state.results if r["status"] == "SKIP")
    print(f"\n{'=' * 60}")
    print(f"TOTAL: {total}  ✅ PASS: {passed}  ❌ FAIL: {failed}  ⚠️  SKIP: {skipped}")
    print(f"Results written to {results_path}")

    if failed:
        print("\nFailed tests:")
        for r in state.results:
            if r["status"] == "FAIL":
                print(f"  ST{r['subtask']} {r['tool']}")
                print(f"    {str(r['data'])[:200]}")
    return 1 if failed else 0


if __name__ == "__main__":
    _bootstrap_config()
    _ensure_schema_version()
    # Parse args: optional positional subtask number, optional --group <name>,
    # optional --results-file <path>
    args = sys.argv[1:]
    subtask_num: int | None = None
    group_name: str | None = None
    results_file = "test-results-round2.json"

    i = 0
    while i < len(args):
        if args[i] == "--group" and i + 1 < len(args):
            group_name = args[i + 1]
            if results_file == "test-results-round2.json":
                results_file = f"test-results-{group_name}.json"
            i += 2
        elif args[i] == "--results-file" and i + 1 < len(args):
            results_file = args[i + 1]
            i += 2
        elif args[i].lstrip("-").isdigit():
            subtask_num = int(args[i])
            i += 1
        else:
            i += 1

    raise SystemExit(
        asyncio.run(
            main(
                subtask_filter=subtask_num, results_path=results_file, group=group_name
            )
        )
    )
