"""Live integration test runner for a configured HMC test plan — Round 2.

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

import argparse
import asyncio
import json
import os
import sys
import traceback
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from fastmcp import Client
from live_test.connectivity import inventory_connectivity
from live_test.escape_hatch import exercise_cli_escape_hatch
from live_test.inventory import capture_lpar_baseline
from live_test.lpar import (
    exercise_lpar_lifecycle,
    mutate_lpar_properties,
    restore_lpar_baseline,
)
from live_test.metrics import inspect_metrics_jobs, inspect_metrics_templates
from live_test.network import inventory_network, mutate_virtual_networking
from live_test.pcie import exercise_sriov_assignment
from live_test.profiles import inventory_lpar_profiles
from live_test.provisioning import (
    exercise_storage_provisioning,
    validate_provisioning_dry_run,
)
from live_test.storage import inventory_storage
from live_test.users import administer_test_user, inventory_users
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

from hmc_mcp.authorization.access_policy import DEFAULT_CONNECTION_TOKEN
from hmc_mcp.cli_commands.legacy_policy import compile_legacy_policy
from hmc_mcp.config import HMCConfig, env_var_value
from hmc_mcp.server import TOOL_SECURITY, _gates, create_mcp
from hmc_mcp.server_tools.command import configure_arbitrary_command_tool

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
    """Return whether *name* is already set using HMCConfig's case-insensitive lookup."""
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
    """Warn when HMC_SCHEMA_VERSION is absent; the operator must set it explicitly."""
    _load_dotenv()
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

    system_name: str = "example-lt-609-system"
    lp3_name: str = "example-lt-609-lpar"
    scratch_name: str = "example-lt-609-scratch"
    nettest_name: str = "example-lt-609-network"
    test_user: str = "example-lt-609-user"
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
    vdisk_name: str = "example-lt-609-disk"
    vdisk_vg_name: str | None = None
    vdisk_size_mib: int | None = None
    scratch_create_desired_memory_mib: int = 1536
    scratch_create_max_memory_mib: int = 3072
    scratch_create_desired_vcpus: int = 3
    scratch_create_max_vcpus: int = 6
    scratch_modify_desired_memory_mib: int = 2304
    scratch_modify_max_memory_mib: int = 4608
    dry_run_lpar_name: str = "example-lt-609-dry-run"
    dry_run_storage_name: str = "example-lt-609-dry-disk"
    vdisk_volume_group_name: str = "example-lt-609-vg"
    dry_run_vios_slot: int = 17
    dry_run_vios_partition_id: int = 307
    dry_run_memory_mib: int = 1536
    provision_min_memory_mib: int = 1536
    provision_desired_memory_mib: int = 3072
    provision_max_memory_mib: int = 6144
    provision_desired_vcpus: int = 3
    provision_max_vcpus: int = 6
    protected_lpar_names: tuple[str, ...] = (
        "example-lt-609-protected-a",
        "example-lt-609-protected-b",
    )
    sriov_adapter_id: int = 17
    sriov_physical_port_id: int = 9
    sriov_logical_port_id: int = 917003
    sriov_capacity_percent: float = 7.5
    sriov_profile_name: str = "example-lt-609-profile"
    iso_path: str = "/srv/example-lt-609/example-lt-609.iso"
    iso_media_name: str = "example-lt-609.iso"
    iso_http_media_name: str = "example-lt-609-http.iso"
    iso_bind_host: str = "0.0.0.0"
    iso_advertised_host: str = "iso.example.test"
    iso_http_port: int = 18090
    vmedia_repository_size_mib: int = 6144
    vmedia_short_repository_size_mib: int = 1536
    placement_memory_mib: int = 3072
    vlan_range_start: int = 3100
    vlan_range_end: int = 3199
    lp3_baseline: dict[str, Any] = field(default_factory=dict)
    # Virtual-media round (ST16–ST22)
    vmedia_repo_created: bool = False
    vmedia_iso_name: str | None = None
    vmedia_mapping_uuid: str | None = None
    vmedia_orig_boot_order: list[str] = field(default_factory=list)

    @property
    def iso_filename(self) -> str:
        """Return the file name published by this run's ISO server."""
        return Path(self.iso_path).name

    @property
    def iso_host(self) -> str:
        """Return the host and port visible to the HMC."""
        return f"{self.iso_advertised_host}:{self.iso_http_port}"

    @property
    def iso_url(self) -> str:
        """Return the HMC-visible URL for the configured ISO."""
        return f"http://{self.iso_host}/{self.iso_filename}"

    _CONFIG_FIELDS: ClassVar[dict[str, str]] = {
        "LIVE_TEST_SYSTEM_NAME": "system_name",
        "LIVE_TEST_LPAR_NAME": "lp3_name",
        "LIVE_TEST_SCRATCH_LPAR_NAME": "scratch_name",
        "LIVE_TEST_NETWORK_TEST_LPAR_NAME": "nettest_name",
        "LIVE_TEST_TEST_USER_NAME": "test_user",
        "LIVE_TEST_VDISK_NAME": "vdisk_name",
        "LIVE_TEST_SCRATCH_CREATE_DESIRED_MEMORY_MIB": "scratch_create_desired_memory_mib",
        "LIVE_TEST_SCRATCH_CREATE_MAX_MEMORY_MIB": "scratch_create_max_memory_mib",
        "LIVE_TEST_SCRATCH_CREATE_DESIRED_VCPUS": "scratch_create_desired_vcpus",
        "LIVE_TEST_SCRATCH_CREATE_MAX_VCPUS": "scratch_create_max_vcpus",
        "LIVE_TEST_SCRATCH_MODIFY_DESIRED_MEMORY_MIB": "scratch_modify_desired_memory_mib",
        "LIVE_TEST_SCRATCH_MODIFY_MAX_MEMORY_MIB": "scratch_modify_max_memory_mib",
        "LIVE_TEST_DRY_RUN_LPAR_NAME": "dry_run_lpar_name",
        "LIVE_TEST_DRY_RUN_STORAGE_NAME": "dry_run_storage_name",
        "LIVE_TEST_VDISK_VOLUME_GROUP_NAME": "vdisk_volume_group_name",
        "LIVE_TEST_DRY_RUN_VIOS_SLOT": "dry_run_vios_slot",
        "LIVE_TEST_DRY_RUN_VIOS_PARTITION_ID": "dry_run_vios_partition_id",
        "LIVE_TEST_DRY_RUN_MEMORY_MIB": "dry_run_memory_mib",
        "LIVE_TEST_PROVISION_MIN_MEMORY_MIB": "provision_min_memory_mib",
        "LIVE_TEST_PROVISION_DESIRED_MEMORY_MIB": "provision_desired_memory_mib",
        "LIVE_TEST_PROVISION_MAX_MEMORY_MIB": "provision_max_memory_mib",
        "LIVE_TEST_PROVISION_DESIRED_VCPUS": "provision_desired_vcpus",
        "LIVE_TEST_PROVISION_MAX_VCPUS": "provision_max_vcpus",
        "LIVE_TEST_PROTECTED_LPAR_NAMES": "protected_lpar_names",
        "LIVE_TEST_SRIOV_ADAPTER_ID": "sriov_adapter_id",
        "LIVE_TEST_SRIOV_PHYSICAL_PORT_ID": "sriov_physical_port_id",
        "LIVE_TEST_SRIOV_LOGICAL_PORT_ID": "sriov_logical_port_id",
        "LIVE_TEST_SRIOV_CAPACITY_PERCENT": "sriov_capacity_percent",
        "LIVE_TEST_SRIOV_PROFILE_NAME": "sriov_profile_name",
        "LIVE_TEST_ISO_PATH": "iso_path",
        "LIVE_TEST_ISO_MEDIA_NAME": "iso_media_name",
        "LIVE_TEST_ISO_HTTP_MEDIA_NAME": "iso_http_media_name",
        "LIVE_TEST_ISO_BIND_HOST": "iso_bind_host",
        "LIVE_TEST_ISO_ADVERTISED_HOST": "iso_advertised_host",
        "LIVE_TEST_ISO_HTTP_PORT": "iso_http_port",
        "LIVE_TEST_VMEDIA_REPOSITORY_SIZE_MIB": "vmedia_repository_size_mib",
        "LIVE_TEST_VMEDIA_SHORT_REPOSITORY_SIZE_MIB": "vmedia_short_repository_size_mib",
        "LIVE_TEST_PLACEMENT_MEMORY_MIB": "placement_memory_mib",
        "LIVE_TEST_VLAN_RANGE_START": "vlan_range_start",
        "LIVE_TEST_VLAN_RANGE_END": "vlan_range_end",
    }

    @classmethod
    def from_env_file(cls, path: Path | None = None) -> LiveTestContext:
        """Load required live-test identifiers from one authoritative local file."""
        path = path or _ENV_FILE
        if not path.is_file():
            raise ValueError(f"live-test configuration file not found: {path}")
        values: dict[str, str] = {}
        duplicates: list[str] = []
        for line_number, raw in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if not key.startswith("LIVE_TEST_"):
                continue
            if key not in cls._CONFIG_FIELDS:
                duplicates.append(f"unknown setting {key} (line {line_number})")
                continue
            if key in values:
                duplicates.append(f"{key} (line {line_number})")
            values[key] = value
        errors = [key for key in cls._CONFIG_FIELDS if not values.get(key)] + duplicates
        if errors:
            raise ValueError("invalid live-test configuration: " + ", ".join(errors))
        try:
            parsed: dict[str, Any] = {
                field: values[key] for key, field in cls._CONFIG_FIELDS.items()
            }
            for key in cls._CONFIG_FIELDS:
                if key.endswith(
                    (
                        "_MIB",
                        "_VCPUS",
                        "_SLOT",
                        "_PARTITION_ID",
                        "_PORT",
                        "_ID",
                        "_START",
                        "_END",
                    )
                ):
                    parsed[cls._CONFIG_FIELDS[key]] = int(values[key])
            parsed["sriov_capacity_percent"] = float(
                values["LIVE_TEST_SRIOV_CAPACITY_PERCENT"]
            )
            parsed["protected_lpar_names"] = tuple(
                name.strip()
                for name in values["LIVE_TEST_PROTECTED_LPAR_NAMES"].split(",")
                if name.strip()
            )
        except ValueError as exc:
            raise ValueError(f"invalid live-test configuration: {exc}") from exc
        numeric_fields = (
            "scratch_create_desired_memory_mib",
            "scratch_create_max_memory_mib",
            "scratch_create_desired_vcpus",
            "scratch_create_max_vcpus",
            "scratch_modify_desired_memory_mib",
            "scratch_modify_max_memory_mib",
            "dry_run_vios_slot",
            "dry_run_vios_partition_id",
            "dry_run_memory_mib",
            "provision_min_memory_mib",
            "provision_desired_memory_mib",
            "provision_max_memory_mib",
            "provision_desired_vcpus",
            "provision_max_vcpus",
            "sriov_adapter_id",
            "sriov_physical_port_id",
            "sriov_logical_port_id",
            "sriov_capacity_percent",
            "iso_http_port",
            "vmedia_repository_size_mib",
            "vmedia_short_repository_size_mib",
            "placement_memory_mib",
            "vlan_range_start",
            "vlan_range_end",
        )
        invalid = [name for name in numeric_fields if parsed[name] <= 0]
        if not parsed["protected_lpar_names"]:
            invalid.append("LIVE_TEST_PROTECTED_LPAR_NAMES")
        if parsed["iso_http_port"] > 65535:
            invalid.append("LIVE_TEST_ISO_HTTP_PORT")
        if (
            parsed["vlan_range_start"] > parsed["vlan_range_end"]
            or parsed["vlan_range_end"] > 4094
        ):
            invalid.append("LIVE_TEST_VLAN_RANGE_START/LIVE_TEST_VLAN_RANGE_END")
        if (
            parsed["scratch_create_desired_memory_mib"]
            > parsed["scratch_create_max_memory_mib"]
            or parsed["scratch_create_desired_vcpus"]
            > parsed["scratch_create_max_vcpus"]
            or parsed["scratch_modify_desired_memory_mib"]
            > parsed["scratch_modify_max_memory_mib"]
            or not (
                parsed["provision_min_memory_mib"]
                <= parsed["provision_desired_memory_mib"]
                <= parsed["provision_max_memory_mib"]
            )
            or parsed["provision_desired_vcpus"] > parsed["provision_max_vcpus"]
        ):
            invalid.append("inconsistent resource limits")
        if invalid:
            raise ValueError("invalid live-test configuration: " + ", ".join(invalid))
        return cls(**parsed)


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
            except json.JSONDecodeError:
                data = text
            return "PASS", data
        except Exception as exc:  # noqa: BLE001 - the harness records any tool failure as a FAIL row; totality is the contract
            return "FAIL", f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"

    def record(
        self, subtask: int, tool: str, status: str, data: Any, note: str = ""
    ) -> None:
        """Append and print one result entry."""
        entry = {
            "subtask": subtask,
            "tool": tool,
            "status": status,
            "timestamp": datetime.now(UTC).isoformat(),
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
# ST0 — Capture baseline LPAR state
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
    23: exercise_sriov_assignment,
}

SUBTASK_GROUPS: dict[str, list[int]] = {
    "round2": list(range(16)),
    "vmedia": list(range(16, 23)),
    "sriov": [23],
    "all": list(range(24)),
}


@dataclass(frozen=True)
class RunnerArguments:
    """Validated live-run selection and result destination."""

    subtask: int | None
    group: str | None
    results_path: str


def _parse_arguments(argv: list[str] | None = None) -> RunnerArguments:
    """Parse the live-run selection without performing configuration or HMC work."""
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "subtask",
        nargs="?",
        type=int,
        choices=sorted(SUBTASKS),
        help="run one numbered subtask",
    )
    selection.add_argument(
        "--group",
        choices=tuple(SUBTASK_GROUPS),
        help="run one named subtask group",
    )
    parser.add_argument(
        "--results-file",
        help="write results to this path instead of the selection-specific default",
    )
    parsed = parser.parse_args(argv)
    results_path = parsed.results_file
    if results_path is None:
        results_path = (
            f"test-results-{parsed.group}.json"
            if parsed.group is not None
            else "test-results-round2.json"
        )
    return RunnerArguments(parsed.subtask, parsed.group, results_path)


def _run_from_arguments(argv: list[str] | None = None) -> int:
    """Validate arguments, then bootstrap configuration and execute the live run."""
    arguments = _parse_arguments(argv)
    try:
        context = LiveTestContext.from_env_file()
    except ValueError as exc:
        print(f"❌ {exc}")
        return 1
    _bootstrap_config()
    _ensure_schema_version()
    return asyncio.run(
        main(
            subtask_filter=arguments.subtask,
            results_path=arguments.results_path,
            group=arguments.group,
            context=context,
        )
    )


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
    context: LiveTestContext | None = None,
) -> int:
    if context is None:
        try:
            context = LiveTestContext.from_env_file()
        except ValueError as exc:
            print(f"❌ {exc}")
            return 1
    state = RunState(context=context)
    context = state.context
    print(f"Starting live integration tests at {datetime.now(UTC).isoformat()}")
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
    raise SystemExit(_run_from_arguments())
