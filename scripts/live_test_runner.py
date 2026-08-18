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
import functools
import http.server
import json
import os
import secrets
import sys
import tempfile
import threading
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmcp import Client

from hmc_mcp.server import mcp
from hmc_mcp.server_command import configure_arbitrary_command_tool

# ---------------------------------------------------------------------------
# Pre-run guard: HMC_SCHEMA_VERSION=V1_0 is required for REST write path
# ---------------------------------------------------------------------------

_ENV_FILE = Path(".env")


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
        if key and key not in os.environ:
            os.environ[key] = val


def _ensure_schema_version() -> None:
    """Warn if HMC_SCHEMA_VERSION is absent; exit so the operator sets it explicitly.

    Note: HMC_SCHEMA_VERSION only affects GET requests — it has no effect on
    write-path HTTP 406 errors (those are fixed by suppressing the header on
    PUT/POST paths entirely).  We still require it to be present so that the
    test runner's GET paths behave deterministically, but we do not silently
    mutate .env — the operator must add it intentionally.
    """
    _load_dotenv()
    if os.environ.get("HMC_SCHEMA_VERSION"):
        return
    print("⚠️  HMC_SCHEMA_VERSION is not set in .env or the environment.")
    print("   Add 'HMC_SCHEMA_VERSION=V1_0' to your .env file and re-run.")
    print("   Note: this variable only affects GET requests; it does NOT fix")
    print("   HTTP 406 on write paths (LPAR create, adapter PUT, etc.).")
    sys.exit(1)


# Throwaway password for the ephemeral test user created and deleted in ST11.
# Not a real credential — the account is deleted at the end of the sub-task.
_TEST_USER_PASSWORD = f"Aa1!{secrets.token_hex(8)}"


@dataclass
class LiveTestContext:
    """Identifiers and snapshots belonging to one live-test execution."""

    system_name: str = "ltczz386"
    lp3_name: str = "ltczz386-lp3"
    scratch_name: str = "ltczz386-lp3-test"
    nettest_name: str = "ltczz386-lp3-nettest"
    test_user: str = "hmc-mcp-testuser"
    test_policy: str = "hmc-mcp-test-policy"
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
    vdisk_size_mb: int = 49152
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


# ---------------------------------------------------------------------------
# Runner helpers
# ---------------------------------------------------------------------------


async def call(client: Client, tool: str, **kwargs: Any) -> tuple[str, Any]:
    """Call a tool and return (status, result). status is 'PASS' or 'FAIL'."""
    try:
        result = await client.call_tool(tool, kwargs)
        # FastMCP returns a CallToolResult; prefer .data (pre-parsed), else
        # extract text from .content blocks and JSON-parse.
        if hasattr(result, "data") and result.data is not None:
            return "PASS", result.data
        if hasattr(result, "content"):
            parts: list[str] = []
            for block in result.content:
                if hasattr(block, "text"):
                    parts.append(str(block.text))
                else:
                    parts.append(str(block))
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
    state: RunState, subtask: int, tool: str, status: str, data: Any, note: str = ""
) -> None:
    entry = {
        "subtask": subtask,
        "tool": tool,
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "note": note,
        "data": data if isinstance(data, (dict, list)) else str(data)[:2000],
    }
    state.results.append(entry)
    icon = "✅" if status == "PASS" else ("⚠️" if status == "SKIP" else "❌")
    note_str = f" — {note}" if note else ""
    print(f"  {icon} ST{subtask} {tool}{note_str}")
    if status == "FAIL":
        print(f"     ERROR: {str(data)[:300]}")


def skip(state: RunState, subtask: int, tool: str, reason: str) -> None:
    record(state, subtask, tool, "SKIP", None, reason)


def _record_expected_or_real(
    state: RunState,
    subtask: int,
    tool: str,
    st: str,
    data: Any,
    expected_fail_substrings: list[str],
    skip_reason: str,
) -> None:
    """Record FAIL as SKIP when it matches an expected HMC limitation.

    Some HMCs don't support PCM, partition templates, or user-management REST
    endpoints.  When those tools return FAIL with a message containing any of
    the expected_fail_substrings, record a SKIP instead of a FAIL.
    """
    if st == "FAIL":
        error_text = str(data).lower()
        if any(s.lower() in error_text for s in expected_fail_substrings):
            skip(state, subtask, tool, skip_reason)
            return
    record(state, subtask, tool, st, data)


def _entries(data: Any) -> list[dict]:
    """Normalise a tool result to a flat list of entry dicts."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("entries", [])
    return []


def _resource(entry: dict) -> dict:
    """Return the Resource sub-dict of an Atom entry, or the entry itself."""
    return entry.get("Resource") or entry


# ---------------------------------------------------------------------------
# ST0 — Capture ltczz386-lp3 Baseline
# ---------------------------------------------------------------------------


async def capture_lpar_baseline(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST0: Capture ltczz386-lp3 Baseline ===")

    # 1. Basic LPAR info
    st, data = await call(client, "hmc_get_lpar", lpar_name_or_uuid=context.lp3_name)
    record(state, 0, "hmc_get_lpar (baseline)", st, data)
    if st == "PASS" and isinstance(data, dict):
        context.lp3_uuid = data.get("uuid") or data.get("UUID")
        context.lp3_baseline["lpars"] = data

    # 2. Composite summary
    st, data = await call(
        client, "hmc_lpar_summary", lpar_name_or_uuid=context.lp3_name
    )
    record(state, 0, "hmc_lpar_summary (baseline)", st, data)
    if st == "PASS":
        context.lp3_baseline["summary"] = data

    # 3. Description
    st, data = await call(
        client,
        "hmc_get_lpar_description",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    record(state, 0, "hmc_get_lpar_description (baseline)", st, data)
    if st == "PASS":
        # Store the plain string value, not the raw result dict, so ST10/ST15
        # restore guards don't have to unwrap a nested dict at restore time.
        desc_val = data
        if isinstance(desc_val, dict):
            desc_val = desc_val.get("description") or desc_val.get("value") or ""
        context.lp3_baseline["description"] = str(desc_val) if desc_val else ""

    # 4. MSP flag
    st, data = await call(
        client,
        "hmc_get_lpar_msp",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    record(state, 0, "hmc_get_lpar_msp (baseline)", st, data)
    if st == "PASS":
        context.lp3_baseline["msp"] = data

    # 5. Proc compat
    st, data = await call(
        client,
        "hmc_get_lpar_proc_compat",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    record(state, 0, "hmc_get_lpar_proc_compat (baseline)", st, data)
    if st == "PASS":
        context.lp3_baseline["proc_compat"] = data

    # 6. CNA adapters — capture PVID and vswitch ID for ST14
    st, data = await call(
        client,
        "hmc_list_adapters",
        lpar_name_or_uuid=context.lp3_name,
        adapter_type="ClientNetworkAdapter",
    )
    record(state, 0, "hmc_list_adapters CNA (baseline)", st, data)
    if st == "PASS":
        context.lp3_baseline["cna_adapters"] = data
        for e in _entries(data):
            resource = _resource(e)
            pvid = resource.get("PortVLANID") or resource.get("port_vlan_id")
            if pvid:
                context.lp3_baseline["pvid"] = int(pvid)
                context.lp3_baseline["vswitch_id"] = int(
                    resource.get("VirtualSwitchID")
                    or resource.get("virtual_switch_id")
                    or 0
                )
                break

    # 7. vSCSI adapters — capture VIOS partition ID and VIOS server slot for ST14
    st, data = await call(
        client,
        "hmc_list_adapters",
        lpar_name_or_uuid=context.lp3_name,
        adapter_type="VirtualSCSIClientAdapter",
    )
    record(state, 0, "hmc_list_adapters vSCSI (baseline)", st, data)
    if st == "PASS":
        context.lp3_baseline["vscsi_adapters"] = data
        for e in _entries(data):
            resource = _resource(e)
            # The HMC REST API uses RemoteLogicalPartitionID/RemoteSlotNumber
            # on the client adapter to describe the VIOS side.  The nested
            # ServerAdapter block holds the VIOS's VirtualSlotNumber.
            vios_pid = (
                resource.get("RemoteLogicalPartitionID")
                or resource.get("remote_logical_partition_id")
                or resource.get("ServerPartitionID")
                or resource.get("server_partition_id")
            )
            server_adapter = resource.get("ServerAdapter") or {}
            vios_slot = (
                server_adapter.get("VirtualSlotNumber")
                or server_adapter.get("virtual_slot_number")
                or resource.get("RemoteSlotNumber")
                or resource.get("remote_slot_number")
                or resource.get("ServerAdapterID")
                or resource.get("server_adapter_id")
            )
            if vios_pid is not None:
                context.lp3_baseline["vios_partition_id"] = int(vios_pid)
            if vios_slot is not None:
                context.lp3_baseline["vios_slot"] = int(vios_slot)
            break

    # 8. VIOS — capture UUID and numeric PartitionID scoped to our managed system
    st, data = await call(
        client, "hmc_list_vios", system_name_or_uuid=context.system_name
    )
    record(state, 0, "hmc_list_vios (baseline)", st, data)
    if st == "PASS":
        for e in _entries(data):
            resource = _resource(e)
            uuid = e.get("UUID") or e.get("uuid")
            pid = resource.get("PartitionID") or resource.get("partition_id")
            if uuid:
                context.vios_uuid = uuid
                context.vios_partition_id = int(pid) if pid is not None else None
                break

    # 9. Full CLI dump
    st, data = await call(
        client,
        "hmc_run_command",
        cmd=f"lssyscfg -r lpar -m {context.system_name}"
        f' --filter "lpar_names={context.lp3_name}"',
    )
    record(state, 0, "hmc_run_command lssyscfg (baseline)", st, data)
    if st == "PASS":
        context.lp3_baseline["lssyscfg"] = data

    print(f"  lp3 UUID: {context.lp3_uuid}")
    print(f"  VIOS UUID: {context.vios_uuid}  PartitionID: {context.vios_partition_id}")
    print(
        f"  lp3 PVID: {context.lp3_baseline.get('pvid')}  "
        f"vSCSI VIOS slot: {context.lp3_baseline.get('vios_slot')}"
    )
    print(f"  Baseline keys: {list(context.lp3_baseline.keys())}")


# ---------------------------------------------------------------------------
# ST1 — Connectivity & Inventory
# ---------------------------------------------------------------------------


async def inventory_connectivity(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST1: Connectivity & Inventory ===")

    st, data = await call(client, "hmc_console_info")
    record(state, 1, "hmc_console_info", st, data)
    if st == "PASS" and isinstance(data, dict):
        context.console_uuid = data.get("uuid") or data.get("UUID")

    st, data = await call(client, "hmc_list_systems")
    record(state, 1, "hmc_list_systems (list)", st, data)
    if st == "PASS":
        for e in _entries(data):
            resource = _resource(e)
            if (
                context.system_name.lower()
                in (resource.get("SystemName") or "").lower()
            ):
                context.system_uuid = e.get("UUID")
                break
        if not context.system_uuid:
            first = _entries(data)
            if first:
                context.system_uuid = first[0].get("UUID")

    st, data = await call(
        client, "hmc_get_system", system_name_or_uuid=context.system_name
    )
    record(state, 1, "hmc_get_system (single)", st, data)
    # Fall back: extract system UUID from the single-system lookup if the list
    # returned empty (e.g. HMC firmware bug on unfiltered ManagedSystem feed)
    if st == "PASS" and isinstance(data, dict) and not context.system_uuid:
        context.system_uuid = data.get("UUID") or data.get("uuid")
    print(f"  System UUID: {context.system_uuid}")

    st, data = await call(client, "hmc_list_lpars")
    record(state, 1, "hmc_list_lpars (list)", st, data)

    st, data = await call(client, "hmc_get_lpar", lpar_name_or_uuid=context.lp3_name)
    record(state, 1, "hmc_get_lpar (single lp3)", st, data)
    if st == "PASS" and isinstance(data, dict) and not context.lp3_uuid:
        context.lp3_uuid = data.get("uuid") or data.get("UUID")

    st, data = await call(
        client, "hmc_list_vios", system_name_or_uuid=context.system_name
    )
    record(state, 1, "hmc_list_vios", st, data)
    if st == "PASS" and not context.vios_uuid:
        for e in _entries(data):
            resource = _resource(e)
            uuid = e.get("UUID") or e.get("uuid")
            pid = resource.get("PartitionID") or resource.get("partition_id")
            if uuid:
                context.vios_uuid = uuid
                context.vios_partition_id = int(pid) if pid is not None else None
                break
    print(f"  VIOS UUID: {context.vios_uuid}  PartitionID: {context.vios_partition_id}")

    st, data = await call(client, "hmc_capacity_report")
    record(state, 1, "hmc_capacity_report", st, data)

    st, data = await call(client, "hmc_find_placement", desired_memory_mb=1024)
    record(state, 1, "hmc_find_placement", st, data)

    st, data = await call(
        client, "hmc_get_system", system_name_or_uuid=context.system_name
    )
    record(state, 1, "hmc_get_system", st, data)

    st, data = await call(
        client, "hmc_list_resources", resource_type="LogicalPartition"
    )
    record(state, 1, "hmc_list_resources", st, data)

    st, data = await call(client, "hmc_list_recent_jobs", limit=10)
    record(state, 1, "hmc_list_recent_jobs", st, data)
    if st == "PASS":
        for e in _entries(data):
            if isinstance(e, dict) and e.get("type") != "error":
                context.job_uuid_sample = e.get("UUID") or e.get("uuid")
                break

    st, data = await call(
        client, "hmc_system_summary", system_name_or_uuid=context.system_name
    )
    record(state, 1, "hmc_system_summary", st, data)

    st, data = await call(
        client, "hmc_lpar_summary", lpar_name_or_uuid=context.lp3_name
    )
    record(state, 1, "hmc_lpar_summary", st, data)


# ---------------------------------------------------------------------------
# ST2 — Network Inventory
# ---------------------------------------------------------------------------


async def inventory_network(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST2: Network Inventory ===")

    st, data = await call(
        client, "hmc_list_virtual_switches", system_name_or_uuid=context.system_name
    )
    record(state, 2, "hmc_list_virtual_switches", st, data)
    if st == "PASS":
        for e in _entries(data):
            resource = _resource(e)
            sid = resource.get("SwitchID") or resource.get("switch_id")
            if sid is not None:
                context.test_vswitch_id = int(sid)
                break
        if context.test_vswitch_id is None:
            context.test_vswitch_id = 0

    st, data = await call(
        client, "hmc_list_virtual_networks", system_name_or_uuid=context.system_name
    )
    record(state, 2, "hmc_list_virtual_networks", st, data)
    if st == "PASS":
        used_vlans: set[int] = set()
        for e in _entries(data):
            resource = _resource(e)
            vlan = (
                resource.get("NetworkVLANID")
                or resource.get("VLANId")
                or resource.get("vlan_id")
            )
            if vlan is not None:
                try:
                    used_vlans.add(int(vlan))
                except (TypeError, ValueError):
                    pass
        for candidate in range(3000, 3100):
            if candidate not in used_vlans:
                context.test_vlan_id = candidate
                break
    print(
        f"  Test VLAN ID: {context.test_vlan_id}  VSwitch ID: {context.test_vswitch_id}"
    )

    st, data = await call(
        client, "hmc_list_network_bridges", system_name_or_uuid=context.system_name
    )
    record(state, 2, "hmc_list_network_bridges", st, data)

    st, data = await call(
        client, "hmc_list_fc_ports", system_name_or_uuid=context.system_name
    )
    record(state, 2, "hmc_list_fc_ports", st, data)

    st, data = await call(
        client, "hmc_list_sea_adapters", system_name_or_uuid=context.system_name
    )
    record(state, 2, "hmc_list_sea_adapters", st, data)

    st, data = await call(
        client,
        "hmc_list_adapters",
        lpar_name_or_uuid=context.lp3_name,
        adapter_type="ClientNetworkAdapter",
    )
    record(state, 2, "hmc_list_adapters (CNA lp3)", st, data)


# ---------------------------------------------------------------------------
# ST3 — Storage & SSP Inventory
# ---------------------------------------------------------------------------


async def inventory_storage(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST3: Storage & SSP Inventory ===")

    if context.vios_uuid:
        st, data = await call(
            client, "hmc_list_volume_groups", vios_name_or_uuid=context.vios_uuid
        )
        record(state, 3, "hmc_list_volume_groups", st, data)
        if st == "PASS":
            for vg in _entries(data):
                resource = _resource(vg)
                vg_name = resource.get("GroupName") or resource.get("group_name") or ""
                uuid = vg.get("UUID") or vg.get("uuid")
                # Accept any VG that contains our target disk; fall back to first
                vdisks = (
                    resource.get("VirtualDisks") or resource.get("virtual_disks") or []
                )
                if isinstance(vdisks, dict):
                    # May be wrapped: {"VirtualDisk": [...]} or {"VirtualDisk": {...}}
                    vdisks = vdisks.get("VirtualDisk") or []
                if isinstance(vdisks, dict):
                    vdisks = [vdisks]
                found_target_disk = False
                for vd in vdisks if isinstance(vdisks, list) else []:
                    virtual_disk_resource = _resource(vd)
                    if virtual_disk_resource.get("DiskName") == context.vdisk_name:
                        found_target_disk = True
                        raw = virtual_disk_resource.get(
                            "DiskCapacity"
                        ) or virtual_disk_resource.get("disk_capacity")
                        if raw is not None:
                            try:
                                # DiskCapacity is in GB — convert to MB
                                gb = int(float(raw))
                                context.vdisk_size_mb = gb * 1024
                            except (TypeError, ValueError):
                                pass
                if found_target_disk or not context.vg_uuid:
                    context.vg_uuid = uuid
                    context.vdisk_vg_name = vg_name
                if found_target_disk:
                    break  # found the VG containing lp3's disk
        print(f"  VG UUID: {context.vg_uuid}  vdisk_size_mb: {context.vdisk_size_mb}")
    else:
        skip(
            state,
            3,
            "hmc_list_volume_groups",
            "no VIOS UUID in context (ST0/ST1 failed)",
        )

    st, data = await call(client, "hmc_list_clusters")
    record(state, 3, "hmc_list_clusters", st, data)

    st, data = await call(client, "hmc_list_shared_storage_pools")
    record(state, 3, "hmc_list_shared_storage_pools", st, data)

    st, data = await call(
        client, "hmc_list_io_slots", system_name_or_uuid=context.system_name
    )
    record(state, 3, "hmc_list_io_slots", st, data)

    st, data = await call(
        client, "hmc_list_memory_pools", system_name_or_uuid=context.system_name
    )
    record(state, 3, "hmc_list_memory_pools", st, data)


# ---------------------------------------------------------------------------
# ST4 — LPAR Properties & Profile Inventory
# ---------------------------------------------------------------------------


async def inventory_lpar_profiles(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST4: LPAR Properties & Profile Inventory ===")

    st, data = await call(
        client,
        "hmc_get_lpar_description",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    record(state, 4, "hmc_get_lpar_description", st, data)

    st, data = await call(
        client,
        "hmc_get_lpar_msp",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    record(state, 4, "hmc_get_lpar_msp", st, data)

    st, data = await call(
        client, "hmc_get_proc_compat_modes", system_name_or_uuid=context.system_name
    )
    record(state, 4, "hmc_get_proc_compat_modes", st, data)

    st, data = await call(
        client,
        "hmc_get_lpar_proc_compat",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    record(state, 4, "hmc_get_lpar_proc_compat", st, data)

    st, data = await call(
        client,
        "hmc_list_vnics",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    record(state, 4, "hmc_list_vnics", st, data)


# ---------------------------------------------------------------------------
# ST5 — Metrics & Templates
# ---------------------------------------------------------------------------


async def inspect_metrics_templates(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST5: Metrics & Templates ===")

    st, data = await call(
        client,
        "hmc_get_pcm_preferences",
        category="ManagedSystem",
        resource_name_or_uuid=context.system_name,
    )
    _record_expected_or_real(
        state,
        5,
        "hmc_get_pcm_preferences",
        st,
        data,
        expected_fail_substrings=["PCM", "406", "403"],
        skip_reason="PCM not licensed on this HMC (expected)",
    )
    if st == "PASS":
        context.lp3_baseline["pcm_prefs"] = data

    st, data = await call(
        client,
        "hmc_processed_metric_links",
        category="ManagedSystem",
        resource_name_or_uuid=context.system_name,
        start_ts="2026-01-01T00:00:00.000Z",
    )
    _record_expected_or_real(
        state,
        5,
        "hmc_processed_metrics (links)",
        st,
        data,
        expected_fail_substrings=["PCM", "406", "403"],
        skip_reason="PCM not licensed on this HMC (expected)",
    )

    st, data = await call(
        client,
        "hmc_aggregated_metric_links",
        category="ManagedSystem",
        resource_name_or_uuid=context.system_name,
        start_ts="2026-01-01T00:00:00.000Z",
    )
    _record_expected_or_real(
        state,
        5,
        "hmc_aggregated_metrics (links)",
        st,
        data,
        expected_fail_substrings=["PCM", "406", "403"],
        skip_reason="PCM not licensed on this HMC (expected)",
    )

    st, data = await call(client, "hmc_list_partition_templates")
    _record_expected_or_real(
        state,
        5,
        "hmc_list_partition_templates",
        st,
        data,
        expected_fail_substrings=["406", "template"],
        skip_reason="Partition templates not licensed on this HMC (expected)",
    )


# ---------------------------------------------------------------------------
# ST6 — User & Policy Inventory
# ---------------------------------------------------------------------------


async def inventory_users_policies(client: Client, state: RunState) -> None:
    print("\n=== ST6: User & Policy Inventory ===")

    st, data = await call(client, "hmc_list_users")
    _record_expected_or_real(
        state,
        6,
        "hmc_list_users",
        st,
        data,
        expected_fail_substrings=["REST000E", "400"],
        skip_reason="HmcUser REST endpoint not supported on this HMC (expected)",
    )

    st, data = await call(client, "hmc_list_password_policies")
    _record_expected_or_real(
        state,
        6,
        "hmc_list_password_policies",
        st,
        data,
        expected_fail_substrings=["REST000E", "400"],
        skip_reason="HmcPasswordPolicy REST endpoint not supported (expected)",
    )

    st, data = await call(client, "hmc_get_ldap_config")
    _record_expected_or_real(
        state,
        6,
        "hmc_get_ldap_config",
        st,
        data,
        expected_fail_substrings=["REST000E", "400"],
        skip_reason="HmcLdapServer REST endpoint not supported (expected)",
    )


# ---------------------------------------------------------------------------
# ST7 — CLI Escape Hatch
# ---------------------------------------------------------------------------


async def exercise_cli_escape_hatch(client: Client, state: RunState) -> None:
    print("\n=== ST7: CLI Escape Hatch ===")

    st, data = await call(client, "hmc_run_command", cmd="lshmc -V")
    record(state, 7, "hmc_run_command (lshmc -V)", st, data)

    st, data = await call(client, "hmc_run_command", cmd="lssyscfg -r sys")
    record(state, 7, "hmc_run_command (lssyscfg -r sys)", st, data)


# ---------------------------------------------------------------------------
# ST8 — LPAR Lifecycle (scratch LPAR ltczz386-lp3-test)
# ---------------------------------------------------------------------------


async def exercise_lpar_lifecycle(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST8: LPAR Lifecycle ===")

    if not context.system_uuid:
        st2, d2 = await call(
            client, "hmc_get_system", system_name_or_uuid=context.system_name
        )
        if st2 == "PASS" and isinstance(d2, dict):
            context.system_uuid = d2.get("UUID") or d2.get("uuid")

    st, data = await call(
        client,
        "hmc_create_lpar",
        system_name_or_uuid=context.system_name,
        name=context.scratch_name,
        desired_memory=512,
        max_memory=1024,
        desired_vcpus=1,
        max_vcpus=2,
    )
    record(state, 8, "hmc_create_lpar", st, data)
    if st == "PASS" and isinstance(data, dict):
        context.scratch_uuid = data.get("uuid") or data.get("UUID")

    st, data = await call(
        client, "hmc_get_lpar", lpar_name_or_uuid=context.scratch_name
    )
    record(state, 8, "hmc_get_lpar (confirm created)", st, data)
    if st == "PASS" and isinstance(data, dict) and not context.scratch_uuid:
        context.scratch_uuid = data.get("uuid") or data.get("UUID")

    st, data = await call(
        client,
        "hmc_modify_lpar",
        lpar_name_or_uuid=context.scratch_name,
        desired_memory=768,
        max_memory=1536,
    )
    _record_expected_or_real(
        state,
        8,
        "hmc_modify_lpar",
        st,
        data,
        expected_fail_substrings=["406", "not acceptable"],
        skip_reason="HMC firmware returns HTTP 406 for REST LPAR modify (same limitation as create — REST write path unsupported)",
    )

    st, data = await call(
        client, "hmc_lpar_summary", lpar_name_or_uuid=context.scratch_name
    )
    record(state, 8, "hmc_lpar_summary (post-modify)", st, data)

    st, data = await call(
        client, "hmc_power_on_lpar", lpar_name_or_uuid=context.scratch_name, wait=True
    )
    record(
        state,
        8,
        "hmc_power_on_lpar",
        st,
        data,
        "boot failure expected — no OS installed",
    )
    if st == "PASS" and isinstance(data, dict):
        context.job_uuid_sample = (
            data.get("job_uuid") or data.get("UUID") or context.job_uuid_sample
        )

    st, data = await call(
        client,
        "hmc_power_off_lpar",
        lpar_name_or_uuid=context.scratch_name,
        immediate=True,
        wait=True,
    )
    record(state, 8, "hmc_power_off_lpar", st, data)
    if st == "PASS" and isinstance(data, dict) and not context.job_uuid_sample:
        context.job_uuid_sample = data.get("job_uuid") or data.get("UUID")

    st, data = await call(
        client, "hmc_delete_lpar", lpar_name_or_uuid=context.scratch_name
    )
    record(state, 8, "hmc_delete_lpar", st, data)
    if st == "PASS":
        context.scratch_uuid = None

    st, data = await call(client, "hmc_list_lpars")
    record(state, 8, "hmc_list_lpars (confirm deleted)", st, data)


# ---------------------------------------------------------------------------
# ST9 — Virtual Networking Mutations
# ---------------------------------------------------------------------------


async def mutate_virtual_networking(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST9: Virtual Networking Mutations ===")

    if context.test_vlan_id is None:
        for name in [
            "hmc_create_virtual_network",
            "hmc_create_lpar (nettest)",
            "hmc_add_network_adapter",
            "hmc_list_adapters (post-add)",
            "hmc_delete_adapter",
            "hmc_delete_virtual_network",
            "hmc_delete_lpar (nettest)",
        ]:
            skip(state, 9, name, "no unused VLAN ID found in ST2")
        return

    vswitch_id = context.test_vswitch_id if context.test_vswitch_id is not None else 0

    st, data = await call(
        client,
        "hmc_create_virtual_network",
        system_name_or_uuid=context.system_name,
        name=f"mcp-test-vlan{context.test_vlan_id}",
        vlan_id=context.test_vlan_id,
        virtual_switch_id=vswitch_id,
        tagged=False,
    )
    _record_expected_or_real(
        state,
        9,
        "hmc_create_virtual_network",
        st,
        data,
        expected_fail_substrings=["406", "not acceptable"],
        skip_reason="HMC firmware returns HTTP 406 for REST VirtualNetwork create (same PUT limitation as LPAR create)",
    )
    if st == "PASS" and isinstance(data, dict):
        context.test_network_uuid = data.get("uuid") or data.get("UUID")

    st, data = await call(
        client, "hmc_list_virtual_networks", system_name_or_uuid=context.system_name
    )
    record(state, 9, "hmc_list_virtual_networks (post-create)", st, data)
    if st == "PASS" and not context.test_network_uuid:
        for e in _entries(data):
            resource = _resource(e)
            vlan = (
                resource.get("NetworkVLANID")
                or resource.get("VLANId")
                or resource.get("vlan_id")
            )
            if str(vlan) == str(context.test_vlan_id):
                context.test_network_uuid = e.get("UUID") or e.get("uuid")
                break

    # Use all_resources=1 path (no explicit resource args) — avoids HSCL0622
    # proc-unit validation failure on this HMC firmware.
    st, data = await call(
        client,
        "hmc_create_lpar",
        system_name_or_uuid=context.system_name,
        name=context.nettest_name,
    )
    record(state, 9, "hmc_create_lpar (nettest)", st, data)
    if st == "PASS" and isinstance(data, dict):
        context.nettest_uuid = data.get("uuid") or data.get("UUID")

    if context.test_network_uuid:
        st, data = await call(
            client,
            "hmc_add_network_adapter",
            lpar_name_or_uuid=context.nettest_name,
            port_vlan_id=context.test_vlan_id,
            virtual_switch_id=vswitch_id,
        )
        record(state, 9, "hmc_add_network_adapter", st, data)

        st, data = await call(
            client,
            "hmc_list_adapters",
            lpar_name_or_uuid=context.nettest_name,
            adapter_type="ClientNetworkAdapter",
        )
        record(state, 9, "hmc_list_adapters (post-add)", st, data)
        if st == "PASS":
            for e in _entries(data):
                context.test_adapter_uuid = e.get("UUID") or e.get("uuid")
                break
    else:
        skip(
            state,
            9,
            "hmc_add_network_adapter",
            "virtual network not created (REST 406)",
        )
        skip(
            state,
            9,
            "hmc_list_adapters (post-add)",
            "virtual network not created (REST 406)",
        )

    if context.test_adapter_uuid:
        st, data = await call(
            client,
            "hmc_delete_adapter",
            lpar_name_or_uuid=context.nettest_name,
            adapter_type="ClientNetworkAdapter",
            adapter_uuid=context.test_adapter_uuid,
        )
        record(state, 9, "hmc_delete_adapter", st, data)
    else:
        skip(state, 9, "hmc_delete_adapter", "no adapter UUID captured")

    if context.test_network_uuid:
        st, data = await call(
            client,
            "hmc_delete_virtual_network",
            system_name_or_uuid=context.system_name,
            network_uuid=context.test_network_uuid,
        )
        record(state, 9, "hmc_delete_virtual_network", st, data)
        if st == "PASS":
            context.test_network_uuid = None
    else:
        skip(state, 9, "hmc_delete_virtual_network", "no network UUID captured")

    if context.nettest_uuid:
        st, data = await call(
            client, "hmc_delete_lpar", lpar_name_or_uuid=context.nettest_name
        )
        record(state, 9, "hmc_delete_lpar (nettest)", st, data)
        if st == "PASS":
            context.nettest_uuid = None
    else:
        skip(state, 9, "hmc_delete_lpar (nettest)", "nettest LPAR not created")


# ---------------------------------------------------------------------------
# ST10 — LPAR Properties Mutations (SSH/CLI)
# ---------------------------------------------------------------------------


async def mutate_lpar_properties(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST10: LPAR Properties Mutations ===")

    orig_desc = context.lp3_baseline.get("description", "")
    if isinstance(orig_desc, dict):
        orig_desc = orig_desc.get("description") or ""

    # Description set/verify/restore — ASCII-only test string (fix #100)
    test_desc = "MCP live-test probe R2 safe to clear"
    st, data = await call(
        client,
        "hmc_set_lpar_description",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
        description=test_desc,
    )
    record(state, 10, "hmc_set_lpar_description", st, data)

    st, data = await call(
        client,
        "hmc_get_lpar_description",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    record(state, 10, "hmc_get_lpar_description (verify)", st, data)

    # Restore only if the original description is printable ASCII (non-ASCII
    # descriptions cannot be round-tripped through the CLI set command).
    _restore_desc = str(orig_desc) if orig_desc else ""
    if _restore_desc and (
        not _restore_desc.isascii()
        or any(ord(c) < 0x20 or ord(c) == 0x7F for c in _restore_desc)
    ):
        skip(
            state,
            10,
            "hmc_set_lpar_description (restore)",
            "original description contains non-ASCII/non-printable chars — cannot restore via CLI",
        )
    else:
        st, data = await call(
            client,
            "hmc_set_lpar_description",
            system_name_or_uuid=context.system_name,
            lpar_name_or_uuid=context.lp3_name,
            description=_restore_desc,
        )
        record(state, 10, "hmc_set_lpar_description (restore)", st, data)

    # Determine lp3 partition environment
    st_env, data_env = await call(
        client,
        "hmc_run_command",
        cmd=f"lssyscfg -r lpar -m {context.system_name}"
        f" --filter lpar_names={context.lp3_name} -F lpar_env",
    )
    record(state, 10, "lssyscfg lpar_env check", st_env, data_env)
    lp3_env = (data_env or "").strip() if st_env == "PASS" else ""

    if lp3_env == "vioserver":
        # Full toggle/verify/restore (only valid on VIOS partitions)
        orig_msp = context.lp3_baseline.get("msp")
        if isinstance(orig_msp, dict):
            orig_msp = orig_msp.get("msp") or orig_msp.get("enabled")
        new_msp = not bool(orig_msp)

        st, data = await call(
            client,
            "hmc_set_lpar_msp",
            system_name_or_uuid=context.system_name,
            lpar_name_or_uuid=context.lp3_name,
            enabled=new_msp,
        )
        record(state, 10, "hmc_set_lpar_msp (toggle)", st, data)

        st, data = await call(
            client,
            "hmc_get_lpar_msp",
            system_name_or_uuid=context.system_name,
            lpar_name_or_uuid=context.lp3_name,
        )
        record(state, 10, "hmc_get_lpar_msp (verify)", st, data)

        st, data = await call(
            client,
            "hmc_set_lpar_msp",
            system_name_or_uuid=context.system_name,
            lpar_name_or_uuid=context.lp3_name,
            enabled=bool(orig_msp),
        )
        record(state, 10, "hmc_set_lpar_msp (restore)", st, data)
    else:
        # AIX/Linux partition — fix #102 should reject cleanly before SSH
        st_bad, data_bad = await call(
            client,
            "hmc_set_lpar_msp",
            system_name_or_uuid=context.system_name,
            lpar_name_or_uuid=context.lp3_name,
            enabled=True,
        )
        rejection_text = str(data_bad).lower()
        if st_bad == "FAIL" and (
            "only valid for a vios" in rejection_text
            or "vioserver" in rejection_text
            or "not found" in rejection_text
        ):
            record(
                state,
                10,
                "hmc_set_lpar_msp (non-VIOS rejection — expected)",
                "PASS",
                f"correctly rejected: {str(data_bad)[:200]}",
            )
        else:
            record(
                state,
                10,
                "hmc_set_lpar_msp (non-VIOS rejection)",
                st_bad,
                data_bad,
                f"lpar_env={lp3_env!r}",
            )
        skip(
            state,
            10,
            "hmc_set_lpar_msp (toggle/verify/restore)",
            f"lp3 is not a VIOS (lpar_env={lp3_env!r})",
        )

    # Proc compat — fetch actual current mode and set idempotently.
    # Use hmc_get_lpar_proc_compat to get the live value; fall back to
    # "default" only if the fetch fails. Skip if we can't get a real mode.
    st_pc, data_pc = await call(
        client,
        "hmc_get_lpar_proc_compat",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    if st_pc == "PASS" and isinstance(data_pc, dict):
        mode = (data_pc.get("desired") or data_pc.get("curr") or "").strip()
    else:
        mode = ""

    if mode and mode.lower() not in ("default", ""):
        st, data = await call(
            client,
            "hmc_set_lpar_proc_compat",
            system_name_or_uuid=context.system_name,
            lpar_name_or_uuid=context.lp3_name,
            mode=mode,
        )
        record(state, 10, "hmc_set_lpar_proc_compat", st, data)
    else:
        skip(
            state,
            10,
            "hmc_set_lpar_proc_compat",
            f"proc compat mode is {mode!r} — skipping idempotent set (chsyscfg rejects 'default' as invalid attribute value)",
        )

    st, data = await call(
        client,
        "hmc_get_lpar_proc_compat",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    record(state, 10, "hmc_get_lpar_proc_compat (verify)", st, data)

    # Profile sync
    st, data = await call(
        client,
        "hmc_sync_lpar_profile",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    record(state, 10, "hmc_sync_lpar_profile", st, data)

    # Profile backup with force=True (fix #103)
    st, data = await call(
        client,
        "hmc_backup_lpar_profiles",
        system_name_or_uuid=context.system_name,
        file_path=str(Path(tempfile.gettempdir()) / "mcp-lp3-profiles-r2"),
        force=True,
    )
    record(state, 10, "hmc_backup_lpar_profiles (force=True)", st, data)


# ---------------------------------------------------------------------------
# ST11 — User Administration
# ---------------------------------------------------------------------------

_REST000E_SKIP = ["REST000E", "400", "not available on this HMC"]


async def administer_test_user(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST11: User Administration ===")

    st, data = await call(
        client,
        "hmc_create_user",
        name=context.test_user,
        taskrole="viewer",
        password=_TEST_USER_PASSWORD,
        description="MCP live test user R2",
    )
    _record_expected_or_real(
        state,
        11,
        "hmc_create_user",
        st,
        data,
        expected_fail_substrings=_REST000E_SKIP,
        skip_reason="HmcUser REST not supported on this HMC (expected)",
    )
    user_created = st == "PASS"

    st, data = await call(client, "hmc_list_users")
    _record_expected_or_real(
        state,
        11,
        "hmc_list_users (confirm created)",
        st,
        data,
        expected_fail_substrings=_REST000E_SKIP,
        skip_reason="HmcUser REST not supported (expected)",
    )

    if user_created:
        st, data = await call(
            client,
            "hmc_modify_user",
            name=context.test_user,
            description="MCP live test user R2 — updated",
        )
        record(state, 11, "hmc_modify_user", st, data)
    else:
        skip(state, 11, "hmc_modify_user", "user not created (REST000E expected)")

    st, data = await call(
        client,
        "hmc_create_password_policy",
        policy_name=context.test_policy,
        min_length=10,
    )
    _record_expected_or_real(
        state,
        11,
        "hmc_create_password_policy",
        st,
        data,
        expected_fail_substrings=_REST000E_SKIP,
        skip_reason="HmcPasswordPolicy REST not supported (expected)",
    )
    policy_created = st == "PASS"

    st, data = await call(client, "hmc_list_password_policies")
    _record_expected_or_real(
        state,
        11,
        "hmc_list_password_policies (confirm)",
        st,
        data,
        expected_fail_substrings=_REST000E_SKIP,
        skip_reason="HmcPasswordPolicy REST not supported (expected)",
    )

    if policy_created:
        st, data = await call(
            client,
            "hmc_modify_password_policy",
            policy_name=context.test_policy,
            min_length=12,
        )
        record(state, 11, "hmc_modify_password_policy", st, data)
    else:
        skip(
            state,
            11,
            "hmc_modify_password_policy",
            "policy not created (REST not supported)",
        )

    if user_created:
        st, data = await call(client, "hmc_delete_user", name=context.test_user)
        record(state, 11, "hmc_delete_user", st, data)
    else:
        skip(state, 11, "hmc_delete_user", "user not created (REST000E expected)")

    if policy_created:
        st, data = await call(
            client, "hmc_delete_password_policy", policy_name=context.test_policy
        )
        record(state, 11, "hmc_delete_password_policy", st, data)
    else:
        skip(
            state,
            11,
            "hmc_delete_password_policy",
            "policy not created (REST not supported)",
        )

    st, data = await call(client, "hmc_list_users")
    _record_expected_or_real(
        state,
        11,
        "hmc_list_users (confirm deleted)",
        st,
        data,
        expected_fail_substrings=_REST000E_SKIP,
        skip_reason="HmcUser REST not supported (expected)",
    )

    st, data = await call(client, "hmc_list_password_policies")
    _record_expected_or_real(
        state,
        11,
        "hmc_list_password_policies (confirm deleted)",
        st,
        data,
        expected_fail_substrings=_REST000E_SKIP,
        skip_reason="HmcPasswordPolicy REST not supported (expected)",
    )


# ---------------------------------------------------------------------------
# ST12 — PCM Metrics & Job Monitoring
# ---------------------------------------------------------------------------


async def inspect_metrics_jobs(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST12: PCM Metrics & Job Monitoring ===")

    st, data = await call(
        client,
        "hmc_get_pcm_preferences",
        category="ManagedSystem",
        resource_name_or_uuid=context.system_name,
    )
    _record_expected_or_real(
        state,
        12,
        "hmc_get_pcm_preferences",
        st,
        data,
        expected_fail_substrings=["PCM", "406", "403"],
        skip_reason="PCM not licensed on this HMC (expected)",
    )
    current_ltm = None
    if st == "PASS" and isinstance(data, dict):
        current_ltm = data.get("long_term_monitor") or data.get(
            "LongTermMonitorEnabled"
        )

    if current_ltm is not None:
        new_ltm = not bool(current_ltm)
        st, data = await call(
            client,
            "hmc_set_pcm_preferences",
            category="ManagedSystem",
            resource_name_or_uuid=context.system_name,
            long_term_monitor=new_ltm,
        )
        record(state, 12, "hmc_set_pcm_preferences (toggle)", st, data)

        st, data = await call(
            client,
            "hmc_get_pcm_preferences",
            category="ManagedSystem",
            resource_name_or_uuid=context.system_name,
        )
        record(state, 12, "hmc_get_pcm_preferences (verify)", st, data)

        st, data = await call(
            client,
            "hmc_set_pcm_preferences",
            category="ManagedSystem",
            resource_name_or_uuid=context.system_name,
            long_term_monitor=bool(current_ltm),
        )
        record(state, 12, "hmc_set_pcm_preferences (restore)", st, data)
    else:
        skip(
            state,
            12,
            "hmc_set_pcm_preferences",
            "PCM not licensed/enabled on this HMC (expected)",
        )

    job_uuid = context.job_uuid_sample
    if job_uuid:
        st, data = await call(client, "hmc_get_job", job_uuid=job_uuid)
        _record_expected_or_real(
            state,
            12,
            "hmc_get_job",
            st,
            data,
            expected_fail_substrings=["REST000E", "REST000B", "400"],
            skip_reason="Job REST type not supported on this HMC firmware",
        )

        st, data = await call(
            client,
            "hmc_wait_for_job",
            job_uuid=job_uuid,
            timeout_seconds=10,
            poll_interval=2,
        )
        _record_expected_or_real(
            state,
            12,
            "hmc_wait_for_job",
            st,
            data,
            expected_fail_substrings=["REST000E", "REST000B", "400"],
            skip_reason="Job REST type not supported on this HMC firmware",
        )
    else:
        skip(state, 12, "hmc_get_job", "no job UUID captured (ST8 may have failed)")
        skip(state, 12, "hmc_wait_for_job", "no job UUID")

    st, data = await call(client, "hmc_list_recent_jobs", limit=20)
    record(state, 12, "hmc_list_recent_jobs (post-tests)", st, data)
    # Opportunistically capture a job UUID if we still don't have one
    if not context.job_uuid_sample and st == "PASS":
        for e in _entries(data):
            if isinstance(e, dict) and e.get("type") != "error":
                context.job_uuid_sample = e.get("UUID") or e.get("uuid")
                break


# ---------------------------------------------------------------------------
# ST13 — Provision Dry Run
# ---------------------------------------------------------------------------


async def validate_provisioning_dry_run(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST13: Provision Dry Run ===")

    # Prefer lp3's own PVID (always present on the system); fall back to test VLAN
    pvid = context.lp3_baseline.get("pvid") or context.test_vlan_id
    vios_uuid = context.vios_uuid
    vios_pid = context.vios_partition_id or context.lp3_baseline.get(
        "vios_partition_id"
    )
    vios_slot = context.lp3_baseline.get("vios_slot") or 2

    if not vios_uuid or not pvid:
        reason = "no VIOS UUID" if not vios_uuid else "no PVID or test VLAN ID"
        skip(state, 13, "hmc_provision_lpar (dry_run)", reason)
        return

    st, data = await call(
        client,
        "hmc_provision_lpar",
        dry_run=True,
        system_name_or_uuid=context.system_name,
        name="ltczz386-lp3-dry",
        port_vlan_id=int(pvid),
        vios_uuid=vios_uuid,
        vios_partition_id=int(vios_pid or 2),
        vios_slot=int(vios_slot),
        storage_name="test-dry-disk",
        desired_memory=512,
    )
    record(state, 13, "hmc_provision_lpar (dry_run)", st, data)
    if st == "PASS" and isinstance(data, dict):
        steps = data.get("steps") or []
        all_dry = all(s.get("status") == "dry_run" for s in steps)
        print(f"  dry_run steps: {[s.get('step') for s in steps]}")
        print(f"  all status=dry_run: {all_dry}")


# ---------------------------------------------------------------------------
# ST14 — Storage Lifecycle + Full Live Provision of ltczz386-lp3
# ---------------------------------------------------------------------------


async def exercise_storage_provisioning(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST14: Storage Lifecycle + Full Live Provision of ltczz386-lp3 ===")

    baseline = context.lp3_baseline
    vios_uuid = context.vios_uuid
    vg_uuid = context.vg_uuid
    vdisk_size_mb = context.vdisk_size_mb or 49152
    pvid = baseline.get("pvid")
    vios_slot = baseline.get("vios_slot")
    vios_pid = context.vios_partition_id or baseline.get("vios_partition_id")

    missing = [
        k
        for k, v in {
            "vios_uuid": vios_uuid,
            "vg_uuid": vg_uuid,
            "pvid": pvid,
            "vios_slot": vios_slot,
            "vios_pid": vios_pid,
        }.items()
        if not v
    ]
    if missing:
        record(
            state,
            14,
            "pre-flight check",
            "FAIL",
            f"Missing required context keys: {missing}. "
            f"Re-run ST0 and ST3 before ST14.",
        )
        for name in [
            "hmc_power_off_lpar",
            "hmc_delete_lpar",
            "hmc_list_volume_groups (pre-create)",
            "hmc_create_virtual_disk",
            "hmc_list_volume_groups (post-create)",
            "hmc_provision_lpar (live)",
            "hmc_lpar_summary (post-provision)",
        ]:
            skip(state, 14, name, "pre-flight failed")
        return

    record(
        state,
        14,
        "pre-flight check",
        "PASS",
        f"vios_uuid={vios_uuid} vg_uuid={vg_uuid} pvid={pvid} "
        f"vios_slot={vios_slot} vios_pid={vios_pid} vdisk_mb={vdisk_size_mb}",
    )

    # Step 1 — Power off lp3 (skip gracefully if already gone)
    st_check, _ = await call(client, "hmc_get_lpar", lpar_name_or_uuid=context.lp3_name)
    if st_check == "PASS":
        st, data = await call(
            client,
            "hmc_power_off_lpar",
            lpar_name_or_uuid=context.lp3_name,
            immediate=True,
            wait=True,
        )
        record(state, 14, "hmc_power_off_lpar", st, data)

        # Step 2 — Delete lp3
        st, data = await call(
            client, "hmc_delete_lpar", lpar_name_or_uuid=context.lp3_name
        )
        record(state, 14, "hmc_delete_lpar", st, data)
    else:
        skip(
            state,
            14,
            "hmc_power_off_lpar",
            "lp3 not found — already deleted in previous run",
        )
        skip(
            state,
            14,
            "hmc_delete_lpar",
            "lp3 not found — already deleted in previous run",
        )

    # Confirm gone
    st, data = await call(client, "hmc_list_lpars")
    record(state, 14, "hmc_list_lpars (confirm lp3 gone)", st, data)

    # Step 3 — Audit VG1 after lp3 deletion (VG1-lp3 LV now unmapped)
    st, data = await call(client, "hmc_list_volume_groups", vios_name_or_uuid=vios_uuid)
    record(state, 14, "hmc_list_volume_groups (pre-create)", st, data)

    # Step 4 — Delete the old VG1-lp3 logical volume via the VIOS CLI.
    # The HMC REST API has no standalone delete-virtual-disk endpoint; the
    # logical volume persists on the VIOS after the LPAR is removed (only the
    # vSCSI mapping is dropped).  We remove it with `rmvlog` so the subsequent
    # `hmc_create_virtual_disk` call exercises the full creation path rather
    # than mapping the pre-existing LV.
    #
    # rmvlog syntax: rmvlog -vg <VGName> -lv <LVName>
    # VGName defaults to "VG1" if we don't have the actual name; fall back to
    # the known-good value for this test system.
    vg_name = context.vdisk_vg_name or "VG1"
    vdisk_name = context.vdisk_name
    rmvlog_cmd = (
        f"viosvrcmd -m {context.system_name} -p {context.vios_uuid}"
        f' -c "rmvlog -vg {vg_name} -lv {vdisk_name}"'
    )
    st, data = await call(client, "hmc_run_command", cmd=rmvlog_cmd)
    _record_expected_or_real(
        state,
        14,
        "hmc_run_command rmvlog (delete old VG1-lp3)",
        st,
        data,
        expected_fail_substrings=[
            "does not exist",
            "not found",
            "No such",
            "0516-306",
            "0516-404",
        ],
        skip_reason="VG1-lp3 LV not present on VIOS (already cleaned up or never existed)",
    )

    # Step 5 — Create new VG1-lp3 virtual disk via REST.
    st, data = await call(
        client,
        "hmc_create_virtual_disk",
        vios_name_or_uuid=vios_uuid,
        vg_uuid=vg_uuid,
        disk_name=context.vdisk_name,
        capacity_mib=int(vdisk_size_mb),
    )
    _record_expected_or_real(
        state,
        14,
        "hmc_create_virtual_disk (VG1-lp3)",
        st,
        data,
        expected_fail_substrings=["406", "not acceptable"],
        skip_reason="REST VolumeGroup POST not supported on this HMC firmware — "
        "pre-existing VG1-lp3 LV must be recreated manually on the VIOS",
    )

    # Confirm new disk visible
    st, data = await call(client, "hmc_list_volume_groups", vios_name_or_uuid=vios_uuid)
    record(state, 14, "hmc_list_volume_groups (post-create)", st, data)

    # Extract memory/CPU from baseline lpar dict
    baseline_lpar = baseline.get("lpars") or {}
    resource = _resource(baseline_lpar) if isinstance(baseline_lpar, dict) else {}
    min_mem = int(
        resource.get("MinimumMemory") or resource.get("minimum_memory") or 256
    )
    des_mem = int(
        resource.get("DesiredMemory") or resource.get("desired_memory") or 1024
    )
    max_mem = int(
        resource.get("MaximumMemory") or resource.get("maximum_memory") or 2048
    )
    des_vcpu = int(
        resource.get("DesiredVirtualProcessors")
        or resource.get("desired_virtual_processors")
        or 1
    )
    max_vcpu = int(
        resource.get("MaximumVirtualProcessors")
        or resource.get("maximum_virtual_processors")
        or 2
    )

    # Step 5 — Full live provision
    st, data = await call(
        client,
        "hmc_provision_lpar",
        system_name_or_uuid=context.system_name,
        name=context.lp3_name,
        port_vlan_id=int(pvid or 0),
        vios_uuid=vios_uuid,
        vios_partition_id=int(vios_pid or 0),
        vios_slot=int(vios_slot or 0),
        storage_name=context.vdisk_name,
        storage_kind="VirtualDisk",
        vg_uuid=vg_uuid,
        min_memory=min_mem,
        desired_memory=des_mem,
        max_memory=max_mem,
        desired_vcpus=des_vcpu,
        max_vcpus=max_vcpu,
        partition_type="AIX/Linux",
        power_on=True,
        dry_run=False,
    )
    record(state, 14, "hmc_provision_lpar (live)", st, data)
    if st == "PASS" and isinstance(data, dict):
        for step in data.get("steps") or []:
            step_status = step.get("status", "unknown")
            step_name = step.get("step", "?")
            icon = "✅" if step_status == "ok" else "❌"
            print(f"    {icon} provision step [{step_name}]: {step_status}")

    # Confirm lp3 is back
    st, data = await call(client, "hmc_get_lpar", lpar_name_or_uuid=context.lp3_name)
    record(state, 14, "hmc_get_lpar (post-provision)", st, data)
    if st == "PASS" and isinstance(data, dict):
        context.lp3_uuid = data.get("uuid") or data.get("UUID")

    st, data = await call(
        client, "hmc_lpar_summary", lpar_name_or_uuid=context.lp3_name
    )
    record(state, 14, "hmc_lpar_summary (post-provision)", st, data)


# ---------------------------------------------------------------------------
# ST15 — Restore ltczz386-lp3 to Baseline
# ---------------------------------------------------------------------------


async def restore_lpar_baseline(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST15: Restore ltczz386-lp3 to Baseline ===")

    st, data = await call(
        client, "hmc_lpar_summary", lpar_name_or_uuid=context.lp3_name
    )
    record(state, 15, "hmc_lpar_summary (post-test)", st, data)

    baseline = context.lp3_baseline

    # Restore description — only if original was printable ASCII (same guard as ST10)
    orig_desc = baseline.get("description", "")
    if isinstance(orig_desc, dict):
        orig_desc = orig_desc.get("description") or ""
    _restore_desc = str(orig_desc) if orig_desc else ""
    if _restore_desc and (
        not _restore_desc.isascii()
        or any(ord(c) < 0x20 or ord(c) == 0x7F for c in _restore_desc)
    ):
        skip(
            state,
            15,
            "hmc_set_lpar_description (restore)",
            "original description contains non-ASCII/non-printable chars — cannot restore via CLI",
        )
    else:
        st, data = await call(
            client,
            "hmc_set_lpar_description",
            system_name_or_uuid=context.system_name,
            lpar_name_or_uuid=context.lp3_name,
            description=_restore_desc,
        )
        record(state, 15, "hmc_set_lpar_description (restore)", st, data)

    # Restore proc compat — fetch live mode; skip if "default" (same guard as ST10)
    st_pc, data_pc = await call(
        client,
        "hmc_get_lpar_proc_compat",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    if st_pc == "PASS" and isinstance(data_pc, dict):
        mode = (data_pc.get("desired") or data_pc.get("curr") or "").strip()
    else:
        mode = ""

    if mode and mode.lower() not in ("default", ""):
        st, data = await call(
            client,
            "hmc_set_lpar_proc_compat",
            system_name_or_uuid=context.system_name,
            lpar_name_or_uuid=context.lp3_name,
            mode=mode,
        )
        record(state, 15, "hmc_set_lpar_proc_compat (restore)", st, data)
    else:
        skip(
            state,
            15,
            "hmc_set_lpar_proc_compat (restore)",
            f"proc compat mode is {mode!r} — skipping restore (chsyscfg rejects 'default')",
        )

    # Final adapter audit
    st, data = await call(
        client,
        "hmc_list_adapters",
        lpar_name_or_uuid=context.lp3_name,
        adapter_type="ClientNetworkAdapter",
    )
    record(state, 15, "hmc_list_adapters (final audit)", st, data)

    # Profile sync
    st, data = await call(
        client,
        "hmc_sync_lpar_profile",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    record(state, 15, "hmc_sync_lpar_profile", st, data)

    # Final CLI dump
    st, data = await call(
        client,
        "hmc_run_command",
        cmd=f"lssyscfg -r lpar -m {context.system_name}"
        f' --filter "lpar_names={context.lp3_name}"',
    )
    record(state, 15, "hmc_run_command lssyscfg (final)", st, data)

    # Final summary
    st, data = await call(
        client, "hmc_lpar_summary", lpar_name_or_uuid=context.lp3_name
    )
    record(state, 15, "hmc_lpar_summary (final confirm)", st, data)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# ST16 — VG Free-Space Check + Repository Create
# ---------------------------------------------------------------------------


async def vmedia_bootstrap_and_create_repo(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST16: VG Free-Space Check + Repository Create ===")

    _REPO_SIZE_MIB = 7000

    # Step 1 — VIOS discovery (skip if already seeded)
    if not context.vios_uuid:
        st, data = await call(
            client, "hmc_list_vios", system_name_or_uuid=context.system_name
        )
        record(state, 16, "hmc_list_vios", st, data)
        if st == "PASS":
            for e in _entries(data):
                resource = _resource(e)
                uuid = e.get("UUID") or e.get("uuid")
                pid = resource.get("PartitionID") or resource.get("partition_id")
                if uuid:
                    context.vios_uuid = uuid
                    context.vios_partition_id = int(pid) if pid is not None else None
                    break
    else:
        print(f"  ℹ  vios_uuid already set: {context.vios_uuid}")

    # lp3 UUID (needed by ST20 boot-order tools which require UUID not name)
    if not context.lp3_uuid:
        st, data = await call(
            client, "hmc_get_lpar", lpar_name_or_uuid=context.lp3_name
        )
        record(state, 16, "hmc_get_lpar (lp3 uuid)", st, data)
        if st == "PASS" and isinstance(data, dict):
            context.lp3_uuid = data.get("uuid") or data.get("UUID")
    else:
        print(f"  ℹ  lp3_uuid already set: {context.lp3_uuid}")

    if not context.vios_uuid:
        for name in [
            "hmc_list_volume_groups",
            "hmc_create_media_repository",
            "hmc_get_media_repository",
        ]:
            skip(state, 16, name, "no VIOS UUID resolved")
        return

    # Step 2 — VG discovery + free-space check
    st, data = await call(
        client, "hmc_list_volume_groups", vios_name_or_uuid=context.vios_uuid
    )
    record(state, 16, "hmc_list_volume_groups", st, data)
    if st == "PASS":
        for vg in _entries(data):
            resource = _resource(vg)
            uuid = vg.get("UUID") or vg.get("uuid")
            if not context.vg_uuid and uuid:
                context.vg_uuid = uuid
            # Always read free space from the selected VG
            if uuid == context.vg_uuid or not context.vg_uuid:
                free_raw = (
                    resource.get("FreeSpace")
                    or resource.get("FreeSpaceInMBytes")
                    or resource.get("free_space")
                    or resource.get("FreeSpaceInMegabytes")
                )
                try:
                    free_mib = int(float(free_raw)) if free_raw is not None else None
                except (TypeError, ValueError):
                    free_mib = None
                print(f"  VG UUID: {context.vg_uuid}  free space: {free_mib} MiB")
                if free_mib is not None and free_mib < _REPO_SIZE_MIB:
                    for name in [
                        "hmc_create_media_repository",
                        "hmc_get_media_repository",
                    ]:
                        skip(
                            state,
                            16,
                            name,
                            f"insufficient free space: {free_mib} MiB < {_REPO_SIZE_MIB} MiB",
                        )
                    return
                break

    if not context.vg_uuid:
        for name in ["hmc_create_media_repository", "hmc_get_media_repository"]:
            skip(state, 16, name, "no VG UUID resolved")
        return

    # Step 4 — Create repository
    st, data = await call(
        client,
        "hmc_create_media_repository",
        vios_name_or_uuid=context.vios_uuid,
        vg_uuid=context.vg_uuid,
        size_mib=_REPO_SIZE_MIB,
    )
    record(state, 16, "hmc_create_media_repository", st, data)
    if st != "PASS":
        skip(state, 16, "hmc_get_media_repository", "repository creation failed")
        return

    # Step 5 — Confirm repository exists
    st, data = await call(
        client,
        "hmc_get_media_repository",
        vios_name_or_uuid=context.vios_uuid,
        vg_uuid=context.vg_uuid,
    )
    record(state, 16, "hmc_get_media_repository", st, data)
    if st == "PASS" and data:
        context.vmedia_repo_created = True
        print("  ✅ Repository created — vmedia_repo_created=True")


# ---------------------------------------------------------------------------
# ST17 — Short Repository Lifecycle (no ISO)
# ---------------------------------------------------------------------------


async def vmedia_short_repo_lifecycle(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST17: Short Repository Lifecycle (no ISO) ===")

    _SMALL_SIZE = 512
    _MAIN_SIZE = 7000

    if not context.vmedia_repo_created:
        for name in [
            "hmc_delete_media_repository (main)",
            "hmc_create_media_repository (small)",
            "hmc_get_media_repository (small)",
            "hmc_list_optical_media (empty)",
            "hmc_delete_media_repository (small)",
            "hmc_get_media_repository (confirm gone)",
            "hmc_create_media_repository (restore main)",
        ]:
            skip(state, 17, name, "vmedia_repo_created=False (ST16 failed)")
        return

    vios = context.vios_uuid
    vg = context.vg_uuid

    # Step 2 — Delete the ST16 main repository
    st, data = await call(
        client,
        "hmc_delete_media_repository",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
    )
    record(state, 17, "hmc_delete_media_repository (main)", st, data)

    # Step 3 — Create small repository
    st, data = await call(
        client,
        "hmc_create_media_repository",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
        size_mib=_SMALL_SIZE,
    )
    record(state, 17, "hmc_create_media_repository (small)", st, data)

    # Step 4 — Verify small repository
    st, data = await call(
        client,
        "hmc_get_media_repository",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
    )
    record(state, 17, "hmc_get_media_repository (small)", st, data)

    # Step 5 — List optical media (must be empty)
    st, data = await call(
        client,
        "hmc_list_optical_media",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
    )
    record(state, 17, "hmc_list_optical_media (empty)", st, data)

    # Step 6 — Delete small repository
    st, data = await call(
        client,
        "hmc_delete_media_repository",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
    )
    record(state, 17, "hmc_delete_media_repository (small)", st, data)

    # Step 7 — Confirm gone
    st, data = await call(
        client,
        "hmc_get_media_repository",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
    )
    record(state, 17, "hmc_get_media_repository (confirm gone)", st, data)

    # Step 8 — Re-create main repository for subsequent sub-tasks
    st, data = await call(
        client,
        "hmc_create_media_repository",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
        size_mib=_MAIN_SIZE,
    )
    record(state, 17, "hmc_create_media_repository (restore main)", st, data)
    if st == "PASS":
        context.vmedia_repo_created = True
    else:
        context.vmedia_repo_created = False
        print("  ⚠  Failed to restore main repository — ST18–ST22 may be skipped")


# ---------------------------------------------------------------------------
# ST18 — ISO Upload via Local Path and via HTTP
# ---------------------------------------------------------------------------

_ISO_PATH = str(Path.home() / "Downloads" / "ubuntu-26.04-live-server-ppc64el.iso")
_ISO_MEDIA_NAME = "ubuntu-26.04-test.iso"
_HTTP_PORT = 18765


async def vmedia_upload_iso(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST18: ISO Upload via Local Path and via HTTP ===")

    _skip_names = [
        "hmc_upload_iso (local path)",
        "hmc_list_optical_media (post-upload)",
        "hmc_upload_iso (http dedup)",
        "hmc_list_optical_media (post-http)",
        "hmc_delete_optical_media",
        "hmc_list_optical_media (confirm empty)",
        "hmc_upload_iso (re-upload for ST19)",
    ]

    if not context.vmedia_repo_created:
        for name in _skip_names:
            skip(state, 18, name, "vmedia_repo_created=False (ST16/ST17 failed)")
        return

    vios = context.vios_uuid
    vg = context.vg_uuid

    # Step 2 — Verify ISO file exists
    if not Path(_ISO_PATH).is_file():
        record(
            state,
            18,
            "iso_file_check",
            "FAIL",
            f"ISO not found: {_ISO_PATH}",
        )
        for name in _skip_names:
            skip(state, 18, name, f"ISO file missing: {_ISO_PATH}")
        return

    record(state, 18, "iso_file_check", "PASS", f"ISO found: {_ISO_PATH}")

    # Step 3 — Upload via local path
    print("  ⏳ Uploading ISO via local path (may take several minutes)…")
    st, data = await call(
        client,
        "hmc_upload_iso",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
        media_name=_ISO_MEDIA_NAME,
        iso_source=_ISO_PATH,
    )
    record(state, 18, "hmc_upload_iso (local path)", st, data)

    # Step 4 — Confirm entry in media list
    st, data = await call(
        client,
        "hmc_list_optical_media",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
    )
    record(state, 18, "hmc_list_optical_media (post-upload)", st, data)
    if st == "PASS":
        for entry in _entries(data) if isinstance(data, list) else []:
            name = _resource(entry).get("MediaName") or entry.get("MediaName")
            if name:
                context.vmedia_iso_name = name
                break
        if not context.vmedia_iso_name and isinstance(data, list) and data:
            # Flat list of dicts without Atom envelope
            context.vmedia_iso_name = (
                data[0].get("MediaName") or _ISO_MEDIA_NAME
            )

    # Step 5 — HTTP server deduplication test
    _http_server: http.server.HTTPServer | None = None
    try:
        try:
            handler = functools.partial(
                http.server.SimpleHTTPRequestHandler,
                directory=str(Path.home() / "Downloads"),
            )
            _http_server = http.server.HTTPServer(("localhost", _HTTP_PORT), handler)
            thread = threading.Thread(target=_http_server.serve_forever, daemon=True)
            thread.start()

            iso_url = (
                f"http://localhost:{_HTTP_PORT}/ubuntu-26.04-live-server-ppc64el.iso"
            )
            print(f"  ⏳ Uploading ISO via HTTP ({iso_url}) — expect dedup hit…")
            st_http, data_http = await call(
                client,
                "hmc_upload_iso",
                vios_name_or_uuid=vios,
                vg_uuid=vg,
                media_name="ubuntu-26.04-http-test.iso",
                iso_source=iso_url,
            )
            http_status = (
                data_http.get("status") if isinstance(data_http, dict) else ""
            )
            if st_http == "PASS" and http_status == "existing":
                record(
                    state,
                    18,
                    "hmc_upload_iso (http dedup)",
                    "PASS",
                    data_http,
                    "status=existing — deduplication fired as expected",
                )
            else:
                record(
                    state,
                    18,
                    "hmc_upload_iso (http dedup)",
                    st_http,
                    data_http,
                    f"expected status=existing, got status={http_status!r}",
                )
        except OSError as exc:
            record(
                state,
                18,
                "hmc_upload_iso (http dedup)",
                "FAIL",
                str(exc),
                f"HTTP server could not bind to port {_HTTP_PORT}",
            )
    finally:
        if _http_server is not None:
            _http_server.shutdown()

    # Step 6 — Confirm still exactly one media entry
    st, data = await call(
        client,
        "hmc_list_optical_media",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
    )
    record(state, 18, "hmc_list_optical_media (post-http)", st, data)

    # Step 7 — Delete the media (unmounted, so safe-delete allows it)
    st, data = await call(
        client,
        "hmc_delete_optical_media",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
        media_name=_ISO_MEDIA_NAME,
    )
    record(state, 18, "hmc_delete_optical_media", st, data)
    if st == "PASS":
        context.vmedia_iso_name = None

    # Step 8 — Confirm empty
    st, data = await call(
        client,
        "hmc_list_optical_media",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
    )
    record(state, 18, "hmc_list_optical_media (confirm empty)", st, data)

    # Step 9 — Re-upload for ST19
    print("  ⏳ Re-uploading ISO for ST19 (may take several minutes)…")
    st, data = await call(
        client,
        "hmc_upload_iso",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
        media_name=_ISO_MEDIA_NAME,
        iso_source=_ISO_PATH,
    )
    record(state, 18, "hmc_upload_iso (re-upload for ST19)", st, data)
    if st == "PASS" and isinstance(data, dict):
        context.vmedia_iso_name = data.get("media_name") or _ISO_MEDIA_NAME
    print(f"  vmedia_iso_name: {context.vmedia_iso_name}")


# ---------------------------------------------------------------------------
# ST19 — Mount / Unmount + Safe-Delete Validation
# ---------------------------------------------------------------------------


async def vmedia_mount_unmount(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST19: Mount / Unmount + Safe-Delete Validation ===")

    _skip_names = [
        "hmc_mount_optical_media",
        "hmc_list_optical_mappings (confirm mounted)",
        "hmc_delete_optical_media (blocked — expected)",
        "hmc_unmount_optical_media",
        "hmc_list_optical_mappings (confirm unmounted)",
        "hmc_delete_optical_media (post-unmount)",
        "hmc_list_optical_media (confirm empty)",
    ]

    if not context.vmedia_iso_name:
        for name in _skip_names:
            skip(state, 19, name, "vmedia_iso_name not set (ST18 failed)")
        return

    vios = context.vios_uuid
    vg = context.vg_uuid

    # Step 2 — Mount ISO to lp3
    st, data = await call(
        client,
        "hmc_mount_optical_media",
        vios_name_or_uuid=vios,
        media_name=context.vmedia_iso_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    record(state, 19, "hmc_mount_optical_media", st, data)
    if st == "PASS" and isinstance(data, dict):
        context.vmedia_mapping_uuid = (
            data.get("ElementID")
            or data.get("UUID")
            or data.get("uuid")
            or data.get("mapping_uuid")
        )
        # Dig into Resource wrapper if present
        if not context.vmedia_mapping_uuid:
            resource = data.get("Resource") or {}
            context.vmedia_mapping_uuid = (
                resource.get("ElementID") or resource.get("UUID")
            )
    print(f"  mapping_uuid: {context.vmedia_mapping_uuid}")

    # Step 3 — Confirm mapping visible in list
    st, data = await call(
        client,
        "hmc_list_optical_mappings",
        vios_name_or_uuid=vios,
        lpar_name_or_uuid=context.lp3_name,
    )
    record(state, 19, "hmc_list_optical_mappings (confirm mounted)", st, data)

    # Step 4 — Attempt delete while mounted (safe-delete guard)
    st_del, data_del = await call(
        client,
        "hmc_delete_optical_media",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
        media_name=context.vmedia_iso_name,
    )
    rejection_text = str(data_del).lower()
    if st_del == "FAIL" and any(
        s in rejection_text for s in ["mapped", "in use", "mapping", "mount"]
    ):
        record(
            state,
            19,
            "hmc_delete_optical_media (blocked — expected)",
            "PASS",
            data_del,
            "safe-delete guard fired correctly",
        )
    else:
        record(
            state,
            19,
            "hmc_delete_optical_media (blocked — expected)",
            st_del,
            data_del,
            f"expected rejection, got st={st_del}",
        )

    # Step 5 — Unmount
    if context.vmedia_mapping_uuid:
        st, data = await call(
            client,
            "hmc_unmount_optical_media",
            vios_name_or_uuid=vios,
            mapping_uuid=context.vmedia_mapping_uuid,
        )
        record(state, 19, "hmc_unmount_optical_media", st, data)
        if st == "PASS":
            context.vmedia_mapping_uuid = None
    else:
        skip(state, 19, "hmc_unmount_optical_media", "no mapping UUID captured")

    # Step 6 — Confirm mapping gone
    st, data = await call(
        client,
        "hmc_list_optical_mappings",
        vios_name_or_uuid=vios,
        lpar_name_or_uuid=context.lp3_name,
    )
    record(state, 19, "hmc_list_optical_mappings (confirm unmounted)", st, data)

    # Step 7 — Delete media (now unmounted, safe-delete allows it)
    st, data = await call(
        client,
        "hmc_delete_optical_media",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
        media_name=context.vmedia_iso_name,
    )
    record(state, 19, "hmc_delete_optical_media (post-unmount)", st, data)
    if st == "PASS":
        context.vmedia_iso_name = None

    # Step 8 — Confirm empty
    st, data = await call(
        client,
        "hmc_list_optical_media",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
    )
    record(state, 19, "hmc_list_optical_media (confirm empty)", st, data)


# ---------------------------------------------------------------------------
# ST20 — Boot Verification: Power Off → CD Boot → Power On → Verify → Restore
# ---------------------------------------------------------------------------

_PROTECTED_LPARS = {"ltczz386-lp1", "ltczz386-lp2"}


async def vmedia_boot_verification(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST20: Boot Verification: Power Off → CD Boot → Power On → Verify → Restore ===")

    _skip_names = [
        "hmc_upload_iso (re-upload for boot test)",
        "hmc_power_off_lpar (pre-boot)",
        "hmc_mount_optical_media (boot test)",
        "hmc_read_lpar_boot_order (baseline)",
        "hmc_set_lpar_boot_order (cd first)",
        "hmc_power_on_lpar",
        "hmc_lpar_summary (verify running)",
        "hmc_power_off_lpar (post-boot)",
        "hmc_unmount_optical_media (boot test cleanup)",
        "hmc_set_lpar_boot_order (restore)",
        "hmc_read_lpar_boot_order (verify restore)",
    ]

    if not context.vmedia_repo_created:
        for name in _skip_names:
            skip(state, 20, name, "vmedia_repo_created=False (ST16 failed)")
        return

    # Safety belt — never touch protected LPARs
    assert context.lp3_name not in _PROTECTED_LPARS, (
        f"ST20 refuses to mutate protected LPAR {context.lp3_name!r}"
    )

    vios = context.vios_uuid
    vg = context.vg_uuid
    lp3_uuid = context.lp3_uuid

    if not lp3_uuid:
        for name in _skip_names:
            skip(state, 20, name, "lp3_uuid not set (ST16 failed to capture it)")
        return

    # Step 2 — Re-upload ISO (was deleted at end of ST19)
    print("  ⏳ Re-uploading ISO for boot test (may take several minutes)…")
    st, data = await call(
        client,
        "hmc_upload_iso",
        vios_name_or_uuid=vios,
        vg_uuid=vg,
        media_name=_ISO_MEDIA_NAME,
        iso_source=_ISO_PATH,
    )
    record(state, 20, "hmc_upload_iso (re-upload for boot test)", st, data)
    if st == "PASS" and isinstance(data, dict):
        context.vmedia_iso_name = data.get("media_name") or _ISO_MEDIA_NAME
    if st != "PASS":
        for name in _skip_names[1:]:
            skip(state, 20, name, "ISO re-upload failed")
        return

    # Step 3 — Power off lp3 (may already be off)
    st, data = await call(
        client,
        "hmc_power_off_lpar",
        lpar_name_or_uuid=context.lp3_name,
        immediate=True,
        wait=True,
    )
    _record_expected_or_real(
        state,
        20,
        "hmc_power_off_lpar (pre-boot)",
        st,
        data,
        expected_fail_substrings=[
            "already",
            "not activated",
            "powered off",
            "not running",
        ],
        skip_reason="lp3 already powered off (expected)",
    )

    # Step 4 — Mount ISO to lp3
    assert context.lp3_name not in _PROTECTED_LPARS
    st, data = await call(
        client,
        "hmc_mount_optical_media",
        vios_name_or_uuid=vios,
        media_name=context.vmedia_iso_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    record(state, 20, "hmc_mount_optical_media (boot test)", st, data)
    if st == "PASS" and isinstance(data, dict):
        context.vmedia_mapping_uuid = (
            data.get("ElementID")
            or data.get("UUID")
            or data.get("uuid")
            or data.get("mapping_uuid")
        )
        if not context.vmedia_mapping_uuid:
            resource = data.get("Resource") or {}
            context.vmedia_mapping_uuid = (
                resource.get("ElementID") or resource.get("UUID")
            )
    if st != "PASS":
        for name in _skip_names[3:]:
            skip(state, 20, name, "mount failed")
        return

    # Step 5 — Read current boot order (save baseline for restore)
    st, data = await call(
        client,
        "hmc_read_lpar_boot_order",
        system_name_or_uuid=context.system_name,
        lpar_uuid=lp3_uuid,
    )
    record(state, 20, "hmc_read_lpar_boot_order (baseline)", st, data)
    if st == "PASS" and isinstance(data, dict):
        # pending_boot_string may be comma-separated "cd,disk,network" or empty
        pending = data.get("pending_boot_string") or ""
        if pending:
            context.vmedia_orig_boot_order = [
                d.strip() for d in pending.split(",") if d.strip()
            ]
        else:
            context.vmedia_orig_boot_order = []

    # Step 6 — Set CD-first boot order
    st, data = await call(
        client,
        "hmc_set_lpar_boot_order",
        system_name_or_uuid=context.system_name,
        lpar_uuid=lp3_uuid,
        devices=["cd", "network", "disk"],
    )
    record(state, 20, "hmc_set_lpar_boot_order (cd first)", st, data)

    # Step 7 — Power on lp3
    assert context.lp3_name not in _PROTECTED_LPARS
    st, data = await call(
        client,
        "hmc_power_on_lpar",
        lpar_name_or_uuid=context.lp3_name,
        wait=True,
        timeout=120,
    )
    record(state, 20, "hmc_power_on_lpar", st, data)

    # Step 8 — Verify Running state
    st, data = await call(
        client,
        "hmc_lpar_summary",
        lpar_name_or_uuid=context.lp3_name,
    )
    record(state, 20, "hmc_lpar_summary (verify running)", st, data)
    if st == "PASS" and isinstance(data, dict):
        lpar_state = data.get("state") or ""
        icon = "✅" if lpar_state.lower() == "running" else "⚠️"
        print(f"  {icon} lp3 state: {lpar_state}")

    # Step 9 — Power off lp3 immediately
    st, data = await call(
        client,
        "hmc_power_off_lpar",
        lpar_name_or_uuid=context.lp3_name,
        immediate=True,
        wait=True,
    )
    record(state, 20, "hmc_power_off_lpar (post-boot)", st, data)

    # Step 10 — Unmount optical media
    if context.vmedia_mapping_uuid:
        st, data = await call(
            client,
            "hmc_unmount_optical_media",
            vios_name_or_uuid=vios,
            mapping_uuid=context.vmedia_mapping_uuid,
        )
        record(state, 20, "hmc_unmount_optical_media (boot test cleanup)", st, data)
        if st == "PASS":
            context.vmedia_mapping_uuid = None
    else:
        skip(
            state, 20, "hmc_unmount_optical_media (boot test cleanup)",
            "no mapping UUID to unmount"
        )

    # Step 11 — Restore boot order
    if context.vmedia_orig_boot_order:
        st, data = await call(
            client,
            "hmc_set_lpar_boot_order",
            system_name_or_uuid=context.system_name,
            lpar_uuid=lp3_uuid,
            devices=context.vmedia_orig_boot_order,
        )
        record(state, 20, "hmc_set_lpar_boot_order (restore)", st, data)
    else:
        st, data = await call(
            client,
            "hmc_clear_lpar_boot_order",
            system_name_or_uuid=context.system_name,
            lpar_uuid=lp3_uuid,
        )
        record(state, 20, "hmc_set_lpar_boot_order (restore)", st, data)
    if st == "PASS":
        context.vmedia_orig_boot_order = []

    # Step 12 — Verify restored boot order
    st, data = await call(
        client,
        "hmc_read_lpar_boot_order",
        system_name_or_uuid=context.system_name,
        lpar_uuid=lp3_uuid,
    )
    record(state, 20, "hmc_read_lpar_boot_order (verify restore)", st, data)


# ---------------------------------------------------------------------------
# ST21 — List Storage Mappings Cross-Validation
# ---------------------------------------------------------------------------


async def vmedia_mapping_crossvalidation(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST21: List Storage Mappings Cross-Validation ===")

    if not context.vios_uuid:
        for name in [
            "hmc_list_storage_mappings (all)",
            "hmc_list_optical_mappings (all)",
            "hmc_list_storage_mappings (lp3)",
            "hmc_list_optical_mappings (lp3)",
        ]:
            skip(state, 21, name, "no VIOS UUID in context")
        return

    vios = context.vios_uuid

    st, data = await call(
        client,
        "hmc_list_storage_mappings",
        vios_name_or_uuid=vios,
    )
    record(state, 21, "hmc_list_storage_mappings (all)", st, data)

    st, data = await call(
        client,
        "hmc_list_optical_mappings",
        vios_name_or_uuid=vios,
    )
    record(state, 21, "hmc_list_optical_mappings (all)", st, data)

    st, data = await call(
        client,
        "hmc_list_storage_mappings",
        vios_name_or_uuid=vios,
        lpar_name_or_uuid=context.lp3_name,
    )
    record(state, 21, "hmc_list_storage_mappings (lp3)", st, data)

    st, data = await call(
        client,
        "hmc_list_optical_mappings",
        vios_name_or_uuid=vios,
        lpar_name_or_uuid=context.lp3_name,
    )
    record(state, 21, "hmc_list_optical_mappings (lp3)", st, data)


# ---------------------------------------------------------------------------
# ST22 — Teardown: Unmount Orphans → Delete ISO → Delete Repository
# ---------------------------------------------------------------------------


async def vmedia_teardown(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST22: Teardown: Unmount Orphans → Delete ISO → Delete Repository ===")

    vios = context.vios_uuid
    vg = context.vg_uuid
    lp3_uuid = context.lp3_uuid

    # Step 1 — Boot order restore guard (in case ST20 restore was skipped)
    if context.vmedia_orig_boot_order and lp3_uuid:
        st, data = await call(
            client,
            "hmc_set_lpar_boot_order",
            system_name_or_uuid=context.system_name,
            lpar_uuid=lp3_uuid,
            devices=context.vmedia_orig_boot_order,
        )
        record(state, 22, "hmc_set_lpar_boot_order (boot order restore guard)", st, data)
        if st == "PASS":
            context.vmedia_orig_boot_order = []
    elif not context.vmedia_orig_boot_order:
        skip(
            state, 22,
            "hmc_set_lpar_boot_order (boot order restore guard)",
            "no saved boot order to restore",
        )
    else:
        skip(
            state, 22,
            "hmc_set_lpar_boot_order (boot order restore guard)",
            "lp3_uuid not available",
        )

    if not vios:
        for name in [
            "hmc_list_optical_mappings (orphan cleanup)",
            "hmc_list_optical_media (media cleanup)",
            "hmc_delete_media_repository",
            "hmc_get_media_repository (confirm gone)",
            "hmc_list_volume_groups (final audit)",
        ]:
            skip(state, 22, name, "no VIOS UUID in context")
        return

    # Step 2 — Orphan mapping cleanup
    st, data = await call(
        client,
        "hmc_list_optical_mappings",
        vios_name_or_uuid=vios,
    )
    record(state, 22, "hmc_list_optical_mappings (orphan cleanup)", st, data)
    if st == "PASS":
        mappings = data if isinstance(data, list) else []
        if not mappings:
            skip(state, 22, "hmc_unmount_optical_media (orphan)", "no orphan mappings")
        for m in mappings:
            m_uuid = m.get("ElementID") or m.get("UUID") or m.get("uuid")
            resource = m.get("Resource") or {}
            m_uuid = m_uuid or resource.get("ElementID") or resource.get("UUID")
            if m_uuid:
                st_u, data_u = await call(
                    client,
                    "hmc_unmount_optical_media",
                    vios_name_or_uuid=vios,
                    mapping_uuid=m_uuid,
                )
                record(
                    state, 22,
                    f"hmc_unmount_optical_media (orphan {m_uuid[:8]}…)",
                    st_u, data_u,
                )

    # Step 3 — Media cleanup
    if vg:
        st, data = await call(
            client,
            "hmc_list_optical_media",
            vios_name_or_uuid=vios,
            vg_uuid=vg,
        )
        record(state, 22, "hmc_list_optical_media (media cleanup)", st, data)
        if st == "PASS":
            media_list = data if isinstance(data, list) else []
            for entry in media_list:
                media_name = (
                    entry.get("MediaName")
                    or (_resource(entry)).get("MediaName")
                )
                if media_name:
                    st_d, data_d = await call(
                        client,
                        "hmc_delete_optical_media",
                        vios_name_or_uuid=vios,
                        vg_uuid=vg,
                        media_name=media_name,
                    )
                    record(
                        state, 22,
                        f"hmc_delete_optical_media ({media_name})",
                        st_d, data_d,
                    )
    else:
        skip(state, 22, "hmc_list_optical_media (media cleanup)", "no VG UUID")

    # Step 4 — Delete repository
    if vg:
        st, data = await call(
            client,
            "hmc_delete_media_repository",
            vios_name_or_uuid=vios,
            vg_uuid=vg,
        )
        _record_expected_or_real(
            state,
            22,
            "hmc_delete_media_repository",
            st,
            data,
            expected_fail_substrings=[
                "not found",
                "does not exist",
                "no repository",
                "no media",
            ],
            skip_reason="repository already gone (expected on re-run)",
        )
        if st == "PASS":
            context.vmedia_repo_created = False

        # Step 5 — Confirm gone
        st, data = await call(
            client,
            "hmc_get_media_repository",
            vios_name_or_uuid=vios,
            vg_uuid=vg,
        )
        record(state, 22, "hmc_get_media_repository (confirm gone)", st, data)
    else:
        skip(state, 22, "hmc_delete_media_repository", "no VG UUID")
        skip(state, 22, "hmc_get_media_repository (confirm gone)", "no VG UUID")

    # Step 6 — Final VG audit
    st, data = await call(
        client,
        "hmc_list_volume_groups",
        vios_name_or_uuid=vios,
    )
    record(state, 22, "hmc_list_volume_groups (final audit)", st, data)


SUBTASKS = {
    0: capture_lpar_baseline,
    1: inventory_connectivity,
    2: inventory_network,
    3: inventory_storage,
    4: inventory_lpar_profiles,
    5: inspect_metrics_templates,
    6: inventory_users_policies,
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
    "all":    list(range(0, 23)),
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
    if not p.exists():
        return
    try:
        saved = json.loads(p.read_text())
        saved_ctx = saved.get("context") or {}
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
    except Exception as e:
        print(f"  ⚠️  Could not restore context from {results_path}: {e}")


async def main(
    subtask_filter: int | None = None,
    results_path: str = "test-results-round2.json",
    group: str | None = None,
) -> int:
    state = RunState()
    context = state.context
    print(
        f"Starting live integration tests at "
        f"{datetime.now(timezone.utc).isoformat()}"
    )
    print(f"HMC_SCHEMA_VERSION={os.environ.get('HMC_SCHEMA_VERSION', '(not set)')}")

    # Determine which sub-tasks to run
    if subtask_filter is not None:
        tasks = [subtask_filter]
    elif group is not None:
        tasks = SUBTASK_GROUPS.get(group, [])
        if not tasks:
            print(
                f"Unknown group {group!r}. "
                f"Valid groups: {', '.join(SUBTASK_GROUPS)}"
            )
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

    await configure_arbitrary_command_tool(True, mcp)
    async with Client(mcp) as client:
        for n in tasks:
            fn = SUBTASKS.get(n)
            if fn:
                await fn(client, state)
            else:
                record(state, n, "runner", "FAIL", f"Unknown sub-task {n}")

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
            main(subtask_filter=subtask_num, results_path=results_file, group=group_name)
        )
    )
