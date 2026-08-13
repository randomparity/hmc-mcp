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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmcp import Client

from hmc_mcp.server import mcp

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
    """Load .env and ensure HMC_SCHEMA_VERSION=V1_0 is present."""
    _load_dotenv()
    if os.environ.get("HMC_SCHEMA_VERSION"):
        return
    print("⚠️  HMC_SCHEMA_VERSION is not set in .env or the environment.")
    if _ENV_FILE.exists():
        content = _ENV_FILE.read_text()
        if "HMC_SCHEMA_VERSION" not in content:
            _ENV_FILE.write_text(content.rstrip("\n") + "\nHMC_SCHEMA_VERSION=V1_0\n")
            # Reload so this run picks it up
            os.environ["HMC_SCHEMA_VERSION"] = "V1_0"
            print("  → Added HMC_SCHEMA_VERSION=V1_0 to .env and environment.")
            return
    print("  → No .env file found.  Create one with HMC_SCHEMA_VERSION=V1_0.")
    sys.exit(1)


_ensure_schema_version()

# ---------------------------------------------------------------------------
# Global context — filled in as sub-tasks run
# ---------------------------------------------------------------------------

RESULTS: list[dict] = []

# Throwaway password for the ephemeral test user created and deleted in ST11.
# Not a real credential — the account is deleted at the end of the sub-task.
_TEST_USER_PASSWORD = "Mcp1T3stUs3r!"  # pragma: allowlist secret

CTX: dict[str, Any] = {
    # --- well-known names ---
    "system_name": "ltczz386",
    "lp3_name": "ltczz386-lp3",
    "scratch_name": "ltczz386-lp3-test",
    "nettest_name": "ltczz386-lp3-nettest",
    "test_user": "hmc-mcp-testuser",
    "test_policy": "hmc-mcp-test-policy",
    # --- populated at runtime ---
    "system_uuid": None,
    "lp3_uuid": None,
    "scratch_uuid": None,
    "vios_uuid": None,
    "vios_partition_id": None,
    "console_uuid": None,
    "test_vlan_id": None,
    "test_vswitch_id": None,
    "test_network_uuid": None,
    "test_adapter_uuid": None,
    "nettest_uuid": None,
    "job_uuid_sample": None,
    # --- storage context for ST14 ---
    "vg_uuid": None,
    "vdisk_name": "VG1-lp3",
    "vdisk_size_mb": 49152,  # 48 GB default; overwritten by actual API value in ST3
    # --- lp3 snapshot captured in ST0 ---
    "lp3_baseline": {},
}

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
            parts = []
            for block in result.content:
                if hasattr(block, "text"):
                    parts.append(block.text)
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


def record(subtask: int, tool: str, status: str, data: Any, note: str = "") -> None:
    entry = {
        "subtask": subtask,
        "tool": tool,
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "note": note,
        "data": data if isinstance(data, (dict, list)) else str(data)[:2000],
    }
    RESULTS.append(entry)
    icon = "✅" if status == "PASS" else ("⚠️" if status == "SKIP" else "❌")
    note_str = f" — {note}" if note else ""
    print(f"  {icon} ST{subtask} {tool}{note_str}")
    if status == "FAIL":
        print(f"     ERROR: {str(data)[:300]}")


def skip(subtask: int, tool: str, reason: str) -> None:
    record(subtask, tool, "SKIP", None, reason)


def _entries(data: Any) -> list[dict]:
    """Normalise a tool result to a flat list of entry dicts."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("entries", [])
    return []


def _res(entry: dict) -> dict:
    """Return the Resource sub-dict of an Atom entry, or the entry itself."""
    return entry.get("Resource") or entry


# ---------------------------------------------------------------------------
# ST0 — Capture ltczz386-lp3 Baseline
# ---------------------------------------------------------------------------

async def subtask_0(client: Client) -> None:
    print("\n=== ST0: Capture ltczz386-lp3 Baseline ===")

    # 1. Basic LPAR info
    st, data = await call(client, "hmc_lpars", lpar_name_or_uuid=CTX["lp3_name"])
    record(0, "hmc_lpars (baseline)", st, data)
    if st == "PASS" and isinstance(data, dict):
        CTX["lp3_uuid"] = data.get("uuid") or data.get("UUID")
        CTX["lp3_baseline"]["lpars"] = data

    # 2. Composite summary
    st, data = await call(client, "hmc_lpar_summary", lpar_name_or_uuid=CTX["lp3_name"])
    record(0, "hmc_lpar_summary (baseline)", st, data)
    if st == "PASS":
        CTX["lp3_baseline"]["summary"] = data

    # 3. Description
    st, data = await call(client, "hmc_get_lpar_description",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"])
    record(0, "hmc_get_lpar_description (baseline)", st, data)
    if st == "PASS":
        CTX["lp3_baseline"]["description"] = data

    # 4. MSP flag
    st, data = await call(client, "hmc_get_lpar_msp",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"])
    record(0, "hmc_get_lpar_msp (baseline)", st, data)
    if st == "PASS":
        CTX["lp3_baseline"]["msp"] = data

    # 5. Proc compat
    st, data = await call(client, "hmc_get_lpar_proc_compat",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"])
    record(0, "hmc_get_lpar_proc_compat (baseline)", st, data)
    if st == "PASS":
        CTX["lp3_baseline"]["proc_compat"] = data

    # 6. CNA adapters — capture PVID and vswitch ID for ST14
    st, data = await call(client, "hmc_list_adapters",
                          lpar_name_or_uuid=CTX["lp3_name"],
                          adapter_type="ClientNetworkAdapter")
    record(0, "hmc_list_adapters CNA (baseline)", st, data)
    if st == "PASS":
        CTX["lp3_baseline"]["cna_adapters"] = data
        for e in _entries(data):
            r = _res(e)
            pvid = r.get("PortVLANID") or r.get("port_vlan_id")
            if pvid:
                CTX["lp3_baseline"]["pvid"] = int(pvid)
                CTX["lp3_baseline"]["vswitch_id"] = int(r.get("VirtualSwitchID") or r.get("virtual_switch_id") or 0)
                break

    # 7. vSCSI adapters — capture VIOS partition ID and VIOS server slot for ST14
    st, data = await call(client, "hmc_list_adapters",
                          lpar_name_or_uuid=CTX["lp3_name"],
                          adapter_type="VirtualSCSIClientAdapter")
    record(0, "hmc_list_adapters vSCSI (baseline)", st, data)
    if st == "PASS":
        CTX["lp3_baseline"]["vscsi_adapters"] = data
        for e in _entries(data):
            r = _res(e)
            # The HMC REST API uses RemoteLogicalPartitionID/RemoteSlotNumber
            # on the client adapter to describe the VIOS side.  The nested
            # ServerAdapter block holds the VIOS's VirtualSlotNumber.
            vios_pid = (
                r.get("RemoteLogicalPartitionID") or r.get("remote_logical_partition_id")
                or r.get("ServerPartitionID") or r.get("server_partition_id")
            )
            server_adapter = r.get("ServerAdapter") or {}
            vios_slot = (
                server_adapter.get("VirtualSlotNumber") or server_adapter.get("virtual_slot_number")
                or r.get("RemoteSlotNumber") or r.get("remote_slot_number")
                or r.get("ServerAdapterID") or r.get("server_adapter_id")
            )
            if vios_pid is not None:
                CTX["lp3_baseline"]["vios_partition_id"] = int(vios_pid)
            if vios_slot is not None:
                CTX["lp3_baseline"]["vios_slot"] = int(vios_slot)
            break

    # 8. VIOS — capture UUID and numeric PartitionID scoped to our managed system
    st, data = await call(client, "hmc_vios",
                          system_name_or_uuid=CTX["system_name"])
    record(0, "hmc_vios (baseline)", st, data)
    if st == "PASS":
        for e in _entries(data):
            r = _res(e)
            uuid = e.get("UUID") or e.get("uuid")
            pid = r.get("PartitionID") or r.get("partition_id")
            if uuid:
                CTX["vios_uuid"] = uuid
                CTX["vios_partition_id"] = int(pid) if pid is not None else None
                break

    # 9. Full CLI dump
    st, data = await call(client, "hmc_run_command",
                          cmd=f"lssyscfg -r lpar -m {CTX['system_name']}"
                              f" --filter \"lpar_names={CTX['lp3_name']}\"")
    record(0, "hmc_run_command lssyscfg (baseline)", st, data)
    if st == "PASS":
        CTX["lp3_baseline"]["lssyscfg"] = data

    print(f"  lp3 UUID: {CTX.get('lp3_uuid')}")
    print(f"  VIOS UUID: {CTX.get('vios_uuid')}  PartitionID: {CTX.get('vios_partition_id')}")
    print(f"  lp3 PVID: {CTX['lp3_baseline'].get('pvid')}  "
          f"vSCSI VIOS slot: {CTX['lp3_baseline'].get('vios_slot')}")
    print(f"  Baseline keys: {list(CTX['lp3_baseline'].keys())}")


# ---------------------------------------------------------------------------
# ST1 — Connectivity & Inventory
# ---------------------------------------------------------------------------

async def subtask_1(client: Client) -> None:
    print("\n=== ST1: Connectivity & Inventory ===")

    st, data = await call(client, "hmc_console_info")
    record(1, "hmc_console_info", st, data)
    if st == "PASS" and isinstance(data, dict):
        CTX["console_uuid"] = data.get("uuid") or data.get("UUID")

    st, data = await call(client, "hmc_systems")
    record(1, "hmc_systems (list)", st, data)
    if st == "PASS":
        for e in _entries(data):
            r = _res(e)
            if CTX["system_name"].lower() in (r.get("SystemName") or "").lower():
                CTX["system_uuid"] = e.get("UUID")
                break
        if not CTX["system_uuid"]:
            first = _entries(data)
            if first:
                CTX["system_uuid"] = first[0].get("UUID")

    st, data = await call(client, "hmc_systems", system_name_or_uuid=CTX["system_name"])
    record(1, "hmc_systems (single)", st, data)
    # Fall back: extract system UUID from the single-system lookup if the list
    # returned empty (e.g. HMC firmware bug on unfiltered ManagedSystem feed)
    if st == "PASS" and isinstance(data, dict) and not CTX["system_uuid"]:
        CTX["system_uuid"] = data.get("UUID") or data.get("uuid")
    print(f"  System UUID: {CTX.get('system_uuid')}")

    st, data = await call(client, "hmc_lpars")
    record(1, "hmc_lpars (list)", st, data)

    st, data = await call(client, "hmc_lpars", lpar_name_or_uuid=CTX["lp3_name"])
    record(1, "hmc_lpars (single lp3)", st, data)
    if st == "PASS" and isinstance(data, dict) and not CTX["lp3_uuid"]:
        CTX["lp3_uuid"] = data.get("uuid") or data.get("UUID")

    st, data = await call(client, "hmc_vios",
                          system_name_or_uuid=CTX["system_name"])
    record(1, "hmc_vios", st, data)
    if st == "PASS" and not CTX["vios_uuid"]:
        for e in _entries(data):
            r = _res(e)
            uuid = e.get("UUID") or e.get("uuid")
            pid = r.get("PartitionID") or r.get("partition_id")
            if uuid:
                CTX["vios_uuid"] = uuid
                CTX["vios_partition_id"] = int(pid) if pid is not None else None
                break
    print(f"  VIOS UUID: {CTX.get('vios_uuid')}  PartitionID: {CTX.get('vios_partition_id')}")

    st, data = await call(client, "hmc_capacity_report")
    record(1, "hmc_capacity_report", st, data)

    st, data = await call(client, "hmc_find_placement", desired_memory_mb=1024)
    record(1, "hmc_find_placement", st, data)

    st, data = await call(client, "hmc_find_system", name=CTX["system_name"])
    record(1, "hmc_find_system", st, data)

    st, data = await call(client, "hmc_list_resources", resource_type="LogicalPartition")
    record(1, "hmc_list_resources", st, data)

    st, data = await call(client, "hmc_recent_jobs", limit=10)
    record(1, "hmc_recent_jobs", st, data)
    if st == "PASS":
        for e in _entries(data):
            if isinstance(e, dict) and e.get("type") != "error":
                CTX["job_uuid_sample"] = e.get("UUID") or e.get("uuid")
                break

    st, data = await call(client, "hmc_system_summary", system_name_or_uuid=CTX["system_name"])
    record(1, "hmc_system_summary", st, data)

    st, data = await call(client, "hmc_lpar_summary", lpar_name_or_uuid=CTX["lp3_name"])
    record(1, "hmc_lpar_summary", st, data)


# ---------------------------------------------------------------------------
# ST2 — Network Inventory
# ---------------------------------------------------------------------------

async def subtask_2(client: Client) -> None:
    print("\n=== ST2: Network Inventory ===")

    st, data = await call(client, "hmc_list_virtual_switches",
                          system_name_or_uuid=CTX["system_name"])
    record(2, "hmc_list_virtual_switches", st, data)
    if st == "PASS":
        for e in _entries(data):
            r = _res(e)
            sid = r.get("SwitchID") or r.get("switch_id")
            if sid is not None:
                CTX["test_vswitch_id"] = int(sid)
                break
        if CTX["test_vswitch_id"] is None:
            CTX["test_vswitch_id"] = 0

    st, data = await call(client, "hmc_list_virtual_networks",
                          system_name_or_uuid=CTX["system_name"])
    record(2, "hmc_list_virtual_networks", st, data)
    if st == "PASS":
        used_vlans: set[int] = set()
        for e in _entries(data):
            r = _res(e)
            vlan = r.get("NetworkVLANID") or r.get("VLANId") or r.get("vlan_id")
            if vlan is not None:
                try:
                    used_vlans.add(int(vlan))
                except (TypeError, ValueError):
                    pass
        for candidate in range(3000, 3100):
            if candidate not in used_vlans:
                CTX["test_vlan_id"] = candidate
                break
    print(f"  Test VLAN ID: {CTX.get('test_vlan_id')}  VSwitch ID: {CTX.get('test_vswitch_id')}")

    st, data = await call(client, "hmc_list_network_bridges",
                          system_name_or_uuid=CTX["system_name"])
    record(2, "hmc_list_network_bridges", st, data)

    st, data = await call(client, "hmc_list_fc_ports",
                          system_name_or_uuid=CTX["system_name"])
    record(2, "hmc_list_fc_ports", st, data)

    st, data = await call(client, "hmc_list_sea_adapters",
                          system_name_or_uuid=CTX["system_name"])
    record(2, "hmc_list_sea_adapters", st, data)

    st, data = await call(client, "hmc_list_adapters",
                          lpar_name_or_uuid=CTX["lp3_name"],
                          adapter_type="ClientNetworkAdapter")
    record(2, "hmc_list_adapters (CNA lp3)", st, data)


# ---------------------------------------------------------------------------
# ST3 — Storage & SSP Inventory
# ---------------------------------------------------------------------------

async def subtask_3(client: Client) -> None:
    print("\n=== ST3: Storage & SSP Inventory ===")

    if CTX["vios_uuid"]:
        st, data = await call(client, "hmc_list_volume_groups",
                              vios_name_or_uuid=CTX["vios_uuid"])
        record(3, "hmc_list_volume_groups", st, data)
        if st == "PASS":
            for vg in _entries(data):
                r = _res(vg)
                vg_name = r.get("GroupName") or r.get("group_name") or ""
                uuid = vg.get("UUID") or vg.get("uuid")
                # Pick VG1 specifically; fall back to first VG if none named VG1
                if "VG1" in vg_name or not CTX["vg_uuid"]:
                    CTX["vg_uuid"] = uuid
                    # Hunt for the VG1-lp3 virtual disk to capture its size
                    vdisks = r.get("VirtualDisks") or r.get("virtual_disks") or []
                    if isinstance(vdisks, dict):
                        # May be wrapped: {"VirtualDisk": [...]} or {"VirtualDisk": {...}}
                        vdisks = vdisks.get("VirtualDisk") or []
                    if isinstance(vdisks, dict):
                        vdisks = [vdisks]
                    for vd in (vdisks if isinstance(vdisks, list) else []):
                        vd_r = _res(vd)
                        if vd_r.get("DiskName") == CTX["vdisk_name"]:
                            raw = vd_r.get("DiskCapacity") or vd_r.get("disk_capacity")
                            if raw is not None:
                                try:
                                    CTX["vdisk_size_mb"] = int(float(raw))
                                except (TypeError, ValueError):
                                    pass
                    if "VG1" in vg_name:
                        break  # found the right VG; stop
        print(f"  VG UUID: {CTX.get('vg_uuid')}  vdisk_size_mb: {CTX.get('vdisk_size_mb')}")
    else:
        skip(3, "hmc_list_volume_groups", "no VIOS UUID in context (ST0/ST1 failed)")

    st, data = await call(client, "hmc_list_clusters")
    record(3, "hmc_list_clusters", st, data)

    st, data = await call(client, "hmc_shared_storage_pools")
    record(3, "hmc_shared_storage_pools", st, data)

    st, data = await call(client, "hmc_list_io_slots",
                          system_name_or_uuid=CTX["system_name"])
    record(3, "hmc_list_io_slots", st, data)

    st, data = await call(client, "hmc_list_memory_pools",
                          system_name_or_uuid=CTX["system_name"])
    record(3, "hmc_list_memory_pools", st, data)


# ---------------------------------------------------------------------------
# ST4 — LPAR Properties & Profile Inventory
# ---------------------------------------------------------------------------

async def subtask_4(client: Client) -> None:
    print("\n=== ST4: LPAR Properties & Profile Inventory ===")

    st, data = await call(client, "hmc_get_lpar_description",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"])
    record(4, "hmc_get_lpar_description", st, data)

    st, data = await call(client, "hmc_get_lpar_msp",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"])
    record(4, "hmc_get_lpar_msp", st, data)

    st, data = await call(client, "hmc_get_proc_compat_modes",
                          system_name_or_uuid=CTX["system_name"])
    record(4, "hmc_get_proc_compat_modes", st, data)

    st, data = await call(client, "hmc_get_lpar_proc_compat",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"])
    record(4, "hmc_get_lpar_proc_compat", st, data)

    st, data = await call(client, "hmc_list_vnics",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"])
    record(4, "hmc_list_vnics", st, data)


# ---------------------------------------------------------------------------
# ST5 — Metrics & Templates
# ---------------------------------------------------------------------------

async def subtask_5(client: Client) -> None:
    print("\n=== ST5: Metrics & Templates ===")

    st, data = await call(client, "hmc_get_pcm_preferences",
                          category="ManagedSystem",
                          resource_name_or_uuid=CTX["system_name"])
    record(5, "hmc_get_pcm_preferences", st, data)
    if st == "PASS":
        CTX["lp3_baseline"]["pcm_prefs"] = data

    st, data = await call(client, "hmc_processed_metrics",
                          category="ManagedSystem",
                          resource_name_or_uuid=CTX["system_name"],
                          start_ts="2026-01-01T00:00:00.000Z",
                          mode="links")
    record(5, "hmc_processed_metrics (links)", st, data)

    st, data = await call(client, "hmc_aggregated_metrics",
                          category="ManagedSystem",
                          resource_name_or_uuid=CTX["system_name"],
                          start_ts="2026-01-01T00:00:00.000Z",
                          mode="links")
    record(5, "hmc_aggregated_metrics (links)", st, data)

    st, data = await call(client, "hmc_partition_templates")
    record(5, "hmc_partition_templates", st, data)


# ---------------------------------------------------------------------------
# ST6 — User & Policy Inventory
# ---------------------------------------------------------------------------

async def subtask_6(client: Client) -> None:
    print("\n=== ST6: User & Policy Inventory ===")

    st, data = await call(client, "hmc_users")
    record(6, "hmc_users", st, data)

    st, data = await call(client, "hmc_list_password_policies")
    record(6, "hmc_list_password_policies", st, data)

    st, data = await call(client, "hmc_get_ldap_config")
    record(6, "hmc_get_ldap_config", st, data)


# ---------------------------------------------------------------------------
# ST7 — CLI Escape Hatch
# ---------------------------------------------------------------------------

async def subtask_7(client: Client) -> None:
    print("\n=== ST7: CLI Escape Hatch ===")

    st, data = await call(client, "hmc_run_command", cmd="lshmc -V")
    record(7, "hmc_run_command (lshmc -V)", st, data)

    st, data = await call(client, "hmc_run_command", cmd="lssyscfg -r sys")
    record(7, "hmc_run_command (lssyscfg -r sys)", st, data)


# ---------------------------------------------------------------------------
# ST8 — LPAR Lifecycle (scratch LPAR ltczz386-lp3-test)
# ---------------------------------------------------------------------------

async def subtask_8(client: Client) -> None:
    print("\n=== ST8: LPAR Lifecycle ===")

    if not CTX["system_uuid"]:
        st2, d2 = await call(client, "hmc_systems", system_name_or_uuid=CTX["system_name"])
        if st2 == "PASS" and isinstance(d2, dict):
            CTX["system_uuid"] = d2.get("UUID") or d2.get("uuid")

    st, data = await call(client, "hmc_create_lpar",
                          system_name_or_uuid=CTX["system_name"],
                          name=CTX["scratch_name"],
                          desired_memory=512,
                          max_memory=1024,
                          desired_vcpus=1,
                          max_vcpus=2)
    record(8, "hmc_create_lpar", st, data)
    if st == "PASS" and isinstance(data, dict):
        CTX["scratch_uuid"] = data.get("uuid") or data.get("UUID")

    st, data = await call(client, "hmc_lpars", lpar_name_or_uuid=CTX["scratch_name"])
    record(8, "hmc_lpars (confirm created)", st, data)
    if st == "PASS" and isinstance(data, dict) and not CTX["scratch_uuid"]:
        CTX["scratch_uuid"] = data.get("uuid") or data.get("UUID")

    st, data = await call(client, "hmc_modify_lpar",
                          lpar_name_or_uuid=CTX["scratch_name"],
                          desired_memory=768,
                          max_memory=1536)
    record(8, "hmc_modify_lpar", st, data)

    st, data = await call(client, "hmc_lpar_summary", lpar_name_or_uuid=CTX["scratch_name"])
    record(8, "hmc_lpar_summary (post-modify)", st, data)

    st, data = await call(client, "hmc_power_on_lpar",
                          lpar_name_or_uuid=CTX["scratch_name"],
                          wait=True)
    record(8, "hmc_power_on_lpar", st, data, "boot failure expected — no OS installed")
    if st == "PASS" and isinstance(data, dict):
        CTX["job_uuid_sample"] = (
            data.get("job_uuid") or data.get("UUID") or CTX.get("job_uuid_sample")
        )

    st, data = await call(client, "hmc_power_off_lpar",
                          lpar_name_or_uuid=CTX["scratch_name"],
                          immediate=True,
                          wait=True)
    record(8, "hmc_power_off_lpar", st, data)
    if st == "PASS" and isinstance(data, dict) and not CTX.get("job_uuid_sample"):
        CTX["job_uuid_sample"] = data.get("job_uuid") or data.get("UUID")

    st, data = await call(client, "hmc_delete_lpar",
                          lpar_name_or_uuid=CTX["scratch_name"])
    record(8, "hmc_delete_lpar", st, data)
    if st == "PASS":
        CTX["scratch_uuid"] = None

    st, data = await call(client, "hmc_lpars")
    record(8, "hmc_lpars (confirm deleted)", st, data)


# ---------------------------------------------------------------------------
# ST9 — Virtual Networking Mutations
# ---------------------------------------------------------------------------

async def subtask_9(client: Client) -> None:
    print("\n=== ST9: Virtual Networking Mutations ===")

    if CTX["test_vlan_id"] is None:
        for name in [
            "hmc_create_virtual_network",
            "hmc_create_lpar (nettest)",
            "hmc_add_network_adapter",
            "hmc_list_adapters (post-add)",
            "hmc_delete_adapter",
            "hmc_delete_virtual_network",
            "hmc_delete_lpar (nettest)",
        ]:
            skip(9, name, "no unused VLAN ID found in ST2")
        return

    vswitch_id = CTX["test_vswitch_id"] if CTX["test_vswitch_id"] is not None else 0

    st, data = await call(client, "hmc_create_virtual_network",
                          system_name_or_uuid=CTX["system_name"],
                          name=f"mcp-test-vlan{CTX['test_vlan_id']}",
                          vlan_id=CTX["test_vlan_id"],
                          vswitch_id=vswitch_id,
                          tagged=False)
    record(9, "hmc_create_virtual_network", st, data)
    if st == "PASS" and isinstance(data, dict):
        CTX["test_network_uuid"] = data.get("uuid") or data.get("UUID")

    st, data = await call(client, "hmc_list_virtual_networks",
                          system_name_or_uuid=CTX["system_name"])
    record(9, "hmc_list_virtual_networks (post-create)", st, data)
    if st == "PASS" and not CTX["test_network_uuid"]:
        for e in _entries(data):
            r = _res(e)
            vlan = r.get("NetworkVLANID") or r.get("VLANId") or r.get("vlan_id")
            if str(vlan) == str(CTX["test_vlan_id"]):
                CTX["test_network_uuid"] = e.get("UUID") or e.get("uuid")
                break

    st, data = await call(client, "hmc_create_lpar",
                          system_name_or_uuid=CTX["system_name"],
                          name=CTX["nettest_name"],
                          desired_memory=256,
                          max_memory=512,
                          desired_vcpus=1,
                          max_vcpus=1)
    record(9, "hmc_create_lpar (nettest)", st, data)
    if st == "PASS" and isinstance(data, dict):
        CTX["nettest_uuid"] = data.get("uuid") or data.get("UUID")

    st, data = await call(client, "hmc_add_network_adapter",
                          lpar_name_or_uuid=CTX["nettest_name"],
                          port_vlan_id=CTX["test_vlan_id"],
                          virtual_switch_id=vswitch_id)
    record(9, "hmc_add_network_adapter", st, data)

    st, data = await call(client, "hmc_list_adapters",
                          lpar_name_or_uuid=CTX["nettest_name"],
                          adapter_type="ClientNetworkAdapter")
    record(9, "hmc_list_adapters (post-add)", st, data)
    if st == "PASS":
        for e in _entries(data):
            CTX["test_adapter_uuid"] = e.get("UUID") or e.get("uuid")
            break

    if CTX["test_adapter_uuid"]:
        st, data = await call(client, "hmc_delete_adapter",
                              lpar_name_or_uuid=CTX["nettest_name"],
                              adapter_type="ClientNetworkAdapter",
                              adapter_uuid=CTX["test_adapter_uuid"])
        record(9, "hmc_delete_adapter", st, data)
    else:
        skip(9, "hmc_delete_adapter", "no adapter UUID captured")

    if CTX["test_network_uuid"]:
        st, data = await call(client, "hmc_delete_virtual_network",
                              system_name_or_uuid=CTX["system_name"],
                              network_uuid=CTX["test_network_uuid"])
        record(9, "hmc_delete_virtual_network", st, data)
        if st == "PASS":
            CTX["test_network_uuid"] = None
    else:
        skip(9, "hmc_delete_virtual_network", "no network UUID captured")

    st, data = await call(client, "hmc_delete_lpar",
                          lpar_name_or_uuid=CTX["nettest_name"])
    record(9, "hmc_delete_lpar (nettest)", st, data)
    if st == "PASS":
        CTX["nettest_uuid"] = None


# ---------------------------------------------------------------------------
# ST10 — LPAR Properties Mutations (SSH/CLI)
# ---------------------------------------------------------------------------

async def subtask_10(client: Client) -> None:
    print("\n=== ST10: LPAR Properties Mutations ===")

    orig_desc = CTX["lp3_baseline"].get("description", "")
    if isinstance(orig_desc, dict):
        orig_desc = orig_desc.get("description") or ""

    # Description set/verify/restore — ASCII-only test string (fix #100)
    test_desc = "MCP live-test probe R2 safe to clear"
    st, data = await call(client, "hmc_set_lpar_description",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"],
                          description=test_desc)
    record(10, "hmc_set_lpar_description", st, data)

    st, data = await call(client, "hmc_get_lpar_description",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"])
    record(10, "hmc_get_lpar_description (verify)", st, data)

    st, data = await call(client, "hmc_set_lpar_description",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"],
                          description=str(orig_desc) if orig_desc else "")
    record(10, "hmc_set_lpar_description (restore)", st, data)

    # Determine lp3 partition environment
    st_env, data_env = await call(
        client, "hmc_run_command",
        cmd=f"lssyscfg -r lpar -m {CTX['system_name']}"
            f" --filter lpar_names={CTX['lp3_name']} -F lpar_env",
    )
    record(10, "lssyscfg lpar_env check", st_env, data_env)
    lp3_env = (data_env or "").strip() if st_env == "PASS" else ""

    if lp3_env == "vioserver":
        # Full toggle/verify/restore (only valid on VIOS partitions)
        orig_msp = CTX["lp3_baseline"].get("msp")
        if isinstance(orig_msp, dict):
            orig_msp = orig_msp.get("msp") or orig_msp.get("enabled")
        new_msp = not bool(orig_msp)

        st, data = await call(client, "hmc_set_lpar_msp",
                              system_name_or_uuid=CTX["system_name"],
                              lpar_name_or_uuid=CTX["lp3_name"],
                              enabled=new_msp)
        record(10, "hmc_set_lpar_msp (toggle)", st, data)

        st, data = await call(client, "hmc_get_lpar_msp",
                              system_name_or_uuid=CTX["system_name"],
                              lpar_name_or_uuid=CTX["lp3_name"])
        record(10, "hmc_get_lpar_msp (verify)", st, data)

        st, data = await call(client, "hmc_set_lpar_msp",
                              system_name_or_uuid=CTX["system_name"],
                              lpar_name_or_uuid=CTX["lp3_name"],
                              enabled=bool(orig_msp))
        record(10, "hmc_set_lpar_msp (restore)", st, data)
    else:
        # AIX/Linux partition — fix #102 should reject cleanly before SSH
        st_bad, data_bad = await call(client, "hmc_set_lpar_msp",
                                      system_name_or_uuid=CTX["system_name"],
                                      lpar_name_or_uuid=CTX["lp3_name"],
                                      enabled=True)
        rejection_text = str(data_bad).lower()
        if st_bad == "FAIL" and (
            "only valid for a vios" in rejection_text
            or "vioserver" in rejection_text
            or "not found" in rejection_text
        ):
            record(10, "hmc_set_lpar_msp (non-VIOS rejection — expected)",
                   "PASS", f"correctly rejected: {str(data_bad)[:200]}")
        else:
            record(10, "hmc_set_lpar_msp (non-VIOS rejection)", st_bad, data_bad,
                   f"lpar_env={lp3_env!r}")
        skip(10, "hmc_set_lpar_msp (toggle/verify/restore)",
             f"lp3 is not a VIOS (lpar_env={lp3_env!r})")

    # Proc compat — set to current mode (idempotent; fix #101)
    orig_compat = CTX["lp3_baseline"].get("proc_compat")
    if isinstance(orig_compat, dict):
        mode = orig_compat.get("current") or orig_compat.get("mode") or "default"
    else:
        mode = "default"

    st, data = await call(client, "hmc_set_lpar_proc_compat",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"],
                          mode=mode)
    record(10, "hmc_set_lpar_proc_compat", st, data)

    st, data = await call(client, "hmc_get_lpar_proc_compat",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"])
    record(10, "hmc_get_lpar_proc_compat (verify)", st, data)

    # Profile sync
    st, data = await call(client, "hmc_sync_lpar_profile",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"])
    record(10, "hmc_sync_lpar_profile", st, data)

    # Profile backup with force=True (fix #103)
    st, data = await call(client, "hmc_backup_lpar_profiles",
                          system_name_or_uuid=CTX["system_name"],
                          file_path="/tmp/mcp-lp3-profiles-r2",
                          force=True)
    record(10, "hmc_backup_lpar_profiles (force=True)", st, data)


# ---------------------------------------------------------------------------
# ST11 — User Administration
# ---------------------------------------------------------------------------

async def subtask_11(client: Client) -> None:
    print("\n=== ST11: User Administration ===")

    st, data = await call(client, "hmc_create_user",
                          name=CTX["test_user"],
                          taskrole="viewer",
                          password=_TEST_USER_PASSWORD,
                          description="MCP live test user R2")
    record(11, "hmc_create_user", st, data)

    st, data = await call(client, "hmc_users")
    record(11, "hmc_users (confirm created)", st, data)

    st, data = await call(client, "hmc_modify_user",
                          name=CTX["test_user"],
                          description="MCP live test user R2 — updated")
    record(11, "hmc_modify_user", st, data)

    st, data = await call(client, "hmc_create_password_policy",
                          policy_name=CTX["test_policy"],
                          min_length=10)
    record(11, "hmc_create_password_policy", st, data)

    st, data = await call(client, "hmc_list_password_policies")
    record(11, "hmc_list_password_policies (confirm)", st, data)

    st, data = await call(client, "hmc_modify_password_policy",
                          policy_name=CTX["test_policy"],
                          min_length=12)
    record(11, "hmc_modify_password_policy", st, data)

    st, data = await call(client, "hmc_delete_user", name=CTX["test_user"])
    record(11, "hmc_delete_user", st, data)

    st, data = await call(client, "hmc_delete_password_policy",
                          policy_name=CTX["test_policy"])
    record(11, "hmc_delete_password_policy", st, data)

    st, data = await call(client, "hmc_users")
    record(11, "hmc_users (confirm deleted)", st, data)

    st, data = await call(client, "hmc_list_password_policies")
    record(11, "hmc_list_password_policies (confirm deleted)", st, data)


# ---------------------------------------------------------------------------
# ST12 — PCM Metrics & Job Monitoring
# ---------------------------------------------------------------------------

async def subtask_12(client: Client) -> None:
    print("\n=== ST12: PCM Metrics & Job Monitoring ===")

    st, data = await call(client, "hmc_get_pcm_preferences",
                          category="ManagedSystem",
                          resource_name_or_uuid=CTX["system_name"])
    record(12, "hmc_get_pcm_preferences", st, data)
    current_ltm = None
    if st == "PASS" and isinstance(data, dict):
        current_ltm = data.get("long_term_monitor") or data.get("LongTermMonitorEnabled")

    if current_ltm is not None:
        new_ltm = not bool(current_ltm)
        st, data = await call(client, "hmc_set_pcm_preferences",
                              category="ManagedSystem",
                              resource_name_or_uuid=CTX["system_name"],
                              long_term_monitor=new_ltm)
        record(12, "hmc_set_pcm_preferences (toggle)", st, data)

        st, data = await call(client, "hmc_get_pcm_preferences",
                              category="ManagedSystem",
                              resource_name_or_uuid=CTX["system_name"])
        record(12, "hmc_get_pcm_preferences (verify)", st, data)

        st, data = await call(client, "hmc_set_pcm_preferences",
                              category="ManagedSystem",
                              resource_name_or_uuid=CTX["system_name"],
                              long_term_monitor=bool(current_ltm))
        record(12, "hmc_set_pcm_preferences (restore)", st, data)
    else:
        skip(12, "hmc_set_pcm_preferences", "PCM not licensed/enabled on this HMC")

    job_uuid = CTX.get("job_uuid_sample")
    if job_uuid:
        st, data = await call(client, "hmc_get_job", job_uuid=job_uuid)
        record(12, "hmc_get_job", st, data)

        st, data = await call(client, "hmc_wait_for_job",
                              job_uuid=job_uuid,
                              timeout_seconds=10,
                              poll_interval=2)
        record(12, "hmc_wait_for_job", st, data)
    else:
        skip(12, "hmc_get_job", "no job UUID captured (ST8 may have failed)")
        skip(12, "hmc_wait_for_job", "no job UUID")

    st, data = await call(client, "hmc_recent_jobs", limit=20)
    record(12, "hmc_recent_jobs (post-tests)", st, data)
    # Opportunistically capture a job UUID if we still don't have one
    if not CTX.get("job_uuid_sample") and st == "PASS":
        for e in _entries(data):
            if isinstance(e, dict) and e.get("type") != "error":
                CTX["job_uuid_sample"] = e.get("UUID") or e.get("uuid")
                break


# ---------------------------------------------------------------------------
# ST13 — Provision Dry Run
# ---------------------------------------------------------------------------

async def subtask_13(client: Client) -> None:
    print("\n=== ST13: Provision Dry Run ===")

    # Prefer lp3's own PVID (always present on the system); fall back to test VLAN
    pvid = CTX["lp3_baseline"].get("pvid") or CTX.get("test_vlan_id")
    vios_uuid = CTX.get("vios_uuid")
    vios_pid = CTX.get("vios_partition_id") or CTX["lp3_baseline"].get("vios_partition_id")
    vios_slot = CTX["lp3_baseline"].get("vios_slot") or 2

    if not vios_uuid or not pvid:
        reason = "no VIOS UUID" if not vios_uuid else "no PVID or test VLAN ID"
        skip(13, "hmc_provision_lpar (dry_run)", reason)
        return

    st, data = await call(client, "hmc_provision_lpar",
                          dry_run=True,
                          system_name_or_uuid=CTX["system_name"],
                          name="ltczz386-lp3-dry",
                          port_vlan_id=int(pvid),
                          vios_uuid=vios_uuid,
                          vios_partition_id=int(vios_pid or 2),
                          vios_slot=int(vios_slot),
                          storage_name="test-dry-disk",
                          desired_memory=512)
    record(13, "hmc_provision_lpar (dry_run)", st, data)
    if st == "PASS" and isinstance(data, dict):
        steps = data.get("steps") or []
        all_dry = all(s.get("status") == "dry_run" for s in steps)
        print(f"  dry_run steps: {[s.get('step') for s in steps]}")
        print(f"  all status=dry_run: {all_dry}")


# ---------------------------------------------------------------------------
# ST14 — Storage Lifecycle + Full Live Provision of ltczz386-lp3
# ---------------------------------------------------------------------------

async def subtask_14(client: Client) -> None:
    print("\n=== ST14: Storage Lifecycle + Full Live Provision of ltczz386-lp3 ===")

    baseline = CTX["lp3_baseline"]
    vios_uuid = CTX.get("vios_uuid")
    vg_uuid = CTX.get("vg_uuid")
    vdisk_size_mb = CTX.get("vdisk_size_mb") or 49152
    pvid = baseline.get("pvid")
    vios_slot = baseline.get("vios_slot")
    vios_pid = CTX.get("vios_partition_id") or baseline.get("vios_partition_id")

    missing = [k for k, v in {
        "vios_uuid": vios_uuid,
        "vg_uuid": vg_uuid,
        "pvid": pvid,
        "vios_slot": vios_slot,
        "vios_pid": vios_pid,
    }.items() if not v]
    if missing:
        record(14, "pre-flight check", "FAIL",
               f"Missing required context keys: {missing}. "
               f"Re-run ST0 and ST3 before ST14.")
        for name in [
            "hmc_power_off_lpar",
            "hmc_delete_lpar",
            "hmc_list_volume_groups (pre-create)",
            "hmc_create_virtual_disk",
            "hmc_list_volume_groups (post-create)",
            "hmc_provision_lpar (live)",
            "hmc_lpar_summary (post-provision)",
        ]:
            skip(14, name, "pre-flight failed")
        return

    record(14, "pre-flight check", "PASS",
           f"vios_uuid={vios_uuid} vg_uuid={vg_uuid} pvid={pvid} "
           f"vios_slot={vios_slot} vios_pid={vios_pid} vdisk_mb={vdisk_size_mb}")

    # Step 1 — Power off lp3
    st, data = await call(client, "hmc_power_off_lpar",
                          lpar_name_or_uuid=CTX["lp3_name"],
                          immediate=True,
                          wait=True)
    record(14, "hmc_power_off_lpar", st, data)

    # Step 2 — Delete lp3
    st, data = await call(client, "hmc_delete_lpar",
                          lpar_name_or_uuid=CTX["lp3_name"])
    record(14, "hmc_delete_lpar", st, data)

    # Confirm gone
    st, data = await call(client, "hmc_lpars")
    record(14, "hmc_lpars (confirm lp3 gone)", st, data)

    # Step 3 — Audit VG1 after lp3 deletion (VG1-lp3 LV now unmapped)
    st, data = await call(client, "hmc_list_volume_groups",
                          vios_name_or_uuid=vios_uuid)
    record(14, "hmc_list_volume_groups (pre-create)", st, data)

    # Step 4 — Create new VG1-lp3 virtual disk
    # Note: there is no standalone delete-virtual-disk tool; the old LV becomes
    # a free LV on the VIOS once the mapping is removed by deleting lp3.
    # Creating a new disk with the same name recreates the storage contract.
    st, data = await call(client, "hmc_create_virtual_disk",
                          vios_name_or_uuid=vios_uuid,
                          vg_uuid=vg_uuid,
                          disk_name=CTX["vdisk_name"],
                          capacity_mb=int(vdisk_size_mb))
    record(14, "hmc_create_virtual_disk (VG1-lp3)", st, data)

    # Confirm new disk visible
    st, data = await call(client, "hmc_list_volume_groups",
                          vios_name_or_uuid=vios_uuid)
    record(14, "hmc_list_volume_groups (post-create)", st, data)

    # Extract memory/CPU from baseline lpar dict
    baseline_lpar = baseline.get("lpars") or {}
    r = _res(baseline_lpar) if isinstance(baseline_lpar, dict) else {}
    min_mem  = int(r.get("MinimumMemory")           or r.get("minimum_memory")            or 256)
    des_mem  = int(r.get("DesiredMemory")            or r.get("desired_memory")            or 1024)
    max_mem  = int(r.get("MaximumMemory")            or r.get("maximum_memory")            or 2048)
    des_vcpu = int(r.get("DesiredVirtualProcessors") or r.get("desired_virtual_processors") or 1)
    max_vcpu = int(r.get("MaximumVirtualProcessors") or r.get("maximum_virtual_processors") or 2)

    # Step 5 — Full live provision
    st, data = await call(client, "hmc_provision_lpar",
                          system_name_or_uuid=CTX["system_name"],
                          name=CTX["lp3_name"],
                          port_vlan_id=int(pvid),
                          vios_uuid=vios_uuid,
                          vios_partition_id=int(vios_pid),
                          vios_slot=int(vios_slot),
                          storage_name=CTX["vdisk_name"],
                          storage_kind="VirtualDisk",
                          vg_uuid=vg_uuid,
                          min_memory=min_mem,
                          desired_memory=des_mem,
                          max_memory=max_mem,
                          desired_vcpus=des_vcpu,
                          max_vcpus=max_vcpu,
                          partition_type="AIX/Linux",
                          power_on=True,
                          dry_run=False)
    record(14, "hmc_provision_lpar (live)", st, data)
    if st == "PASS" and isinstance(data, dict):
        for step in (data.get("steps") or []):
            step_status = step.get("status", "unknown")
            step_name   = step.get("step", "?")
            icon = "✅" if step_status == "ok" else "❌"
            print(f"    {icon} provision step [{step_name}]: {step_status}")

    # Confirm lp3 is back
    st, data = await call(client, "hmc_lpars", lpar_name_or_uuid=CTX["lp3_name"])
    record(14, "hmc_lpars (post-provision)", st, data)
    if st == "PASS" and isinstance(data, dict):
        CTX["lp3_uuid"] = data.get("uuid") or data.get("UUID")

    st, data = await call(client, "hmc_lpar_summary", lpar_name_or_uuid=CTX["lp3_name"])
    record(14, "hmc_lpar_summary (post-provision)", st, data)


# ---------------------------------------------------------------------------
# ST15 — Restore ltczz386-lp3 to Baseline
# ---------------------------------------------------------------------------

async def subtask_15(client: Client) -> None:
    print("\n=== ST15: Restore ltczz386-lp3 to Baseline ===")

    st, data = await call(client, "hmc_lpar_summary", lpar_name_or_uuid=CTX["lp3_name"])
    record(15, "hmc_lpar_summary (post-test)", st, data)

    baseline = CTX["lp3_baseline"]

    # Restore description (ASCII-safe; fix #100)
    orig_desc = baseline.get("description", "")
    if isinstance(orig_desc, dict):
        orig_desc = orig_desc.get("description") or ""
    st, data = await call(client, "hmc_set_lpar_description",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"],
                          description=str(orig_desc) if orig_desc else "")
    record(15, "hmc_set_lpar_description (restore)", st, data)

    # Restore proc compat (fix #101)
    orig_compat = baseline.get("proc_compat")
    if isinstance(orig_compat, dict):
        mode = orig_compat.get("current") or orig_compat.get("mode") or "default"
    else:
        mode = "default"
    st, data = await call(client, "hmc_set_lpar_proc_compat",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"],
                          mode=mode)
    record(15, "hmc_set_lpar_proc_compat (restore)", st, data)

    # Final adapter audit
    st, data = await call(client, "hmc_list_adapters",
                          lpar_name_or_uuid=CTX["lp3_name"],
                          adapter_type="ClientNetworkAdapter")
    record(15, "hmc_list_adapters (final audit)", st, data)

    # Profile sync
    st, data = await call(client, "hmc_sync_lpar_profile",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"])
    record(15, "hmc_sync_lpar_profile", st, data)

    # Final CLI dump
    st, data = await call(client, "hmc_run_command",
                          cmd=f"lssyscfg -r lpar -m {CTX['system_name']}"
                              f" --filter \"lpar_names={CTX['lp3_name']}\"")
    record(15, "hmc_run_command lssyscfg (final)", st, data)

    # Final summary
    st, data = await call(client, "hmc_lpar_summary", lpar_name_or_uuid=CTX["lp3_name"])
    record(15, "hmc_lpar_summary (final confirm)", st, data)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

SUBTASKS = {
    0: subtask_0,
    1: subtask_1,
    2: subtask_2,
    3: subtask_3,
    4: subtask_4,
    5: subtask_5,
    6: subtask_6,
    7: subtask_7,
    8: subtask_8,
    9: subtask_9,
    10: subtask_10,
    11: subtask_11,
    12: subtask_12,
    13: subtask_13,
    14: subtask_14,
    15: subtask_15,
}


def _restore_ctx_from_results(results_path: str = "test-results-round2.json") -> None:
    """Pre-seed CTX from the previous results file when running a single sub-task.

    This allows sub-tasks run in isolation (e.g. `python runner.py 3`) to use
    context captured by earlier sub-tasks (VIOS UUID, system UUID, etc.).
    """
    p = Path(results_path)
    if not p.exists():
        return
    try:
        saved = json.loads(p.read_text())
        saved_ctx = saved.get("context") or {}
        for key in list(CTX.keys()):
            if CTX[key] is None and saved_ctx.get(key) is not None:
                CTX[key] = saved_ctx[key]
            elif key == "lp3_baseline" and not CTX[key] and saved_ctx.get(key):
                CTX[key] = saved_ctx[key]
        print(f"  ℹ  Context restored from {results_path} "
              f"(vios_uuid={CTX.get('vios_uuid')}, "
              f"system_uuid={CTX.get('system_uuid')}, "
              f"vg_uuid={CTX.get('vg_uuid')})")
    except Exception as e:
        print(f"  ⚠️  Could not restore context from {results_path}: {e}")


async def main(subtask_filter: int | None = None) -> None:
    print(f"Starting live integration tests (Round 2) at "
          f"{datetime.now(timezone.utc).isoformat()}")
    print(f"HMC_SCHEMA_VERSION={os.environ.get('HMC_SCHEMA_VERSION', '(not set)')}")

    if subtask_filter is not None:
        _restore_ctx_from_results()

    async with Client(mcp) as client:
        tasks = [subtask_filter] if subtask_filter is not None else sorted(SUBTASKS.keys())
        for n in tasks:
            fn = SUBTASKS.get(n)
            if fn:
                await fn(client)
            else:
                print(f"  ⚠️  Unknown sub-task {n}")

    out = "test-results-round2.json"
    with open(out, "w") as f:
        json.dump({"context": CTX, "results": RESULTS}, f, indent=2, default=str)

    total   = len(RESULTS)
    passed  = sum(1 for r in RESULTS if r["status"] == "PASS")
    failed  = sum(1 for r in RESULTS if r["status"] == "FAIL")
    skipped = sum(1 for r in RESULTS if r["status"] == "SKIP")
    print(f"\n{'='*60}")
    print(f"TOTAL: {total}  ✅ PASS: {passed}  ❌ FAIL: {failed}  ⚠️  SKIP: {skipped}")
    print(f"Results written to {out}")

    if failed:
        print("\nFailed tests:")
        for r in RESULTS:
            if r["status"] == "FAIL":
                print(f"  ST{r['subtask']} {r['tool']}")
                print(f"    {str(r['data'])[:200]}")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    subtask_num = int(arg) if arg is not None else None
    asyncio.run(main(subtask_num))
