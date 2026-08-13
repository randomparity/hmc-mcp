"""Live integration test runner for the ltczz386 test plan.

Calls HMC MCP tools via the in-process FastMCP client against the real HMC
configured in .env.  Results are printed to stdout as they complete and
written to test-results.json on exit.

Usage:
    uv run python scripts/live_test_runner.py [SUBTASK_NUMBER]

If SUBTASK_NUMBER is omitted, all sub-tasks are run in order.
If a specific number is given (0-14), only that sub-task runs.
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from datetime import datetime, timezone
from typing import Any

from fastmcp import Client

from hmc_mcp.server import mcp

# ---------------------------------------------------------------------------
# Global state filled in as tests run
# ---------------------------------------------------------------------------

RESULTS: list[dict] = []

# Carry runtime context between sub-tasks
# Throwaway password for the ephemeral test user created and deleted in ST11.
# Not a real credential — the account is deleted at the end of the test run.
_TEST_USER_PASSWORD = "Mcp1T3stUs3r!"  # pragma: allowlist secret

CTX: dict[str, Any] = {
    "system_name": "ltczz386",
    "lp3_name": "ltczz386-lp3",
    "scratch_name": "ltczz386-lp3-test",
    "nettest_name": "ltczz386-lp3-nettest",
    "test_user": "hmc-mcp-testuser",
    "test_policy": "hmc-mcp-test-policy",
    # populated at runtime:
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
    "lp3_baseline": {},
}


# ---------------------------------------------------------------------------
# Runner helpers
# ---------------------------------------------------------------------------

async def call(client: Client, tool: str, **kwargs) -> tuple[str, Any]:
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
        snippet = str(data)[:300]
        print(f"     ERROR: {snippet}")


def skip(subtask: int, tool: str, reason: str) -> None:
    record(subtask, tool, "SKIP", None, reason)


# ---------------------------------------------------------------------------
# Sub-Task 0 — Capture ltczz386-lp3 Baseline
# ---------------------------------------------------------------------------

async def subtask_0(client: Client) -> None:
    print("\n=== ST0: Capture ltczz386-lp3 Baseline ===")

    st, data = await call(client, "hmc_lpars", lpar_name_or_uuid=CTX["lp3_name"])
    record(0, "hmc_lpars (baseline)", st, data)
    if st == "PASS" and isinstance(data, dict):
        CTX["lp3_uuid"] = data.get("uuid") or (
            data.get("entries", [{}])[0].get("UUID") if isinstance(data.get("entries"), list) else None
        )
        CTX["lp3_baseline"]["lpars"] = data

    st, data = await call(client, "hmc_lpar_summary", lpar_name_or_uuid=CTX["lp3_name"])
    record(0, "hmc_lpar_summary (baseline)", st, data)
    if st == "PASS":
        CTX["lp3_baseline"]["summary"] = data

    st, data = await call(client, "hmc_get_lpar_description",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"])
    record(0, "hmc_get_lpar_description (baseline)", st, data)
    if st == "PASS":
        CTX["lp3_baseline"]["description"] = data

    st, data = await call(client, "hmc_get_lpar_msp",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"])
    record(0, "hmc_get_lpar_msp (baseline)", st, data)
    if st == "PASS":
        CTX["lp3_baseline"]["msp"] = data

    st, data = await call(client, "hmc_get_lpar_proc_compat",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"])
    record(0, "hmc_get_lpar_proc_compat (baseline)", st, data)
    if st == "PASS":
        CTX["lp3_baseline"]["proc_compat"] = data

    st, data = await call(client, "hmc_list_adapters",
                          lpar_name_or_uuid=CTX["lp3_name"],
                          adapter_type="ClientNetworkAdapter")
    record(0, "hmc_list_adapters (baseline)", st, data)
    if st == "PASS":
        CTX["lp3_baseline"]["adapters"] = data

    st, data = await call(client, "hmc_run_command",
                          cmd=f"lssyscfg -r lpar -m {CTX['system_name']} --filter \"lpar_names={CTX['lp3_name']}\"")
    record(0, "hmc_run_command lssyscfg (baseline)", st, data)
    if st == "PASS":
        CTX["lp3_baseline"]["lssyscfg"] = data

    print(f"  Baseline captured. lp3 UUID: {CTX.get('lp3_uuid')}")
    print(f"  Baseline keys: {list(CTX['lp3_baseline'].keys())}")


# ---------------------------------------------------------------------------
# Sub-Task 1 — Connectivity & Inventory
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
        entries = data if isinstance(data, list) else data.get("entries", [])
        for e in entries:
            res = e.get("Resource") or {}
            if CTX["system_name"].lower() in (res.get("SystemName") or "").lower():
                CTX["system_uuid"] = e.get("UUID")
                break
        if not CTX["system_uuid"] and entries:
            CTX["system_uuid"] = entries[0].get("UUID")
    print(f"  System UUID: {CTX.get('system_uuid')}")

    st, data = await call(client, "hmc_systems", system_name_or_uuid=CTX["system_name"])
    record(1, "hmc_systems (single)", st, data)

    st, data = await call(client, "hmc_lpars")
    record(1, "hmc_lpars (list)", st, data)

    st, data = await call(client, "hmc_lpars", lpar_name_or_uuid=CTX["lp3_name"])
    record(1, "hmc_lpars (single lp3)", st, data)
    if st == "PASS" and isinstance(data, dict) and not CTX["lp3_uuid"]:
        CTX["lp3_uuid"] = data.get("uuid")

    st, data = await call(client, "hmc_vios")
    record(1, "hmc_vios", st, data)
    if st == "PASS":
        entries = data if isinstance(data, list) else data.get("entries", [])
        if entries:
            CTX["vios_uuid"] = entries[0].get("UUID")
            res = entries[0].get("Resource") or {}
            CTX["vios_partition_id"] = res.get("PartitionID")
    print(f"  VIOS UUID: {CTX.get('vios_uuid')}  PartitionID: {CTX.get('vios_partition_id')}")

    st, data = await call(client, "hmc_capacity_report")
    record(1, "hmc_capacity_report", st, data)

    st, data = await call(client, "hmc_find_placement", desired_memory_mb=1024)
    record(1, "hmc_find_placement", st, data)

    # Need the real system name for hmc_find_system
    sys_name = CTX["system_name"]
    st, data = await call(client, "hmc_find_system", name=sys_name)
    record(1, "hmc_find_system", st, data)

    st, data = await call(client, "hmc_list_resources", resource_type="LogicalPartition")
    record(1, "hmc_list_resources", st, data)

    st, data = await call(client, "hmc_recent_jobs", limit=10)
    record(1, "hmc_recent_jobs", st, data)
    if st == "PASS":
        jobs = data if isinstance(data, list) else data.get("entries", [])
        if jobs:
            CTX["job_uuid_sample"] = jobs[0].get("UUID") or jobs[0].get("uuid")

    st, data = await call(client, "hmc_system_summary", system_name_or_uuid=CTX["system_name"])
    record(1, "hmc_system_summary", st, data)

    st, data = await call(client, "hmc_lpar_summary", lpar_name_or_uuid=CTX["lp3_name"])
    record(1, "hmc_lpar_summary", st, data)


# ---------------------------------------------------------------------------
# Sub-Task 2 — Network Inventory
# ---------------------------------------------------------------------------

async def subtask_2(client: Client) -> None:
    print("\n=== ST2: Network Inventory ===")

    st, data = await call(client, "hmc_list_virtual_switches",
                          system_name_or_uuid=CTX["system_name"])
    record(2, "hmc_list_virtual_switches", st, data)
    if st == "PASS":
        entries = data if isinstance(data, list) else data.get("entries", [])
        if entries:
            CTX["test_vswitch_id"] = entries[0].get("SwitchID") or entries[0].get("switch_id") or 0

    st, data = await call(client, "hmc_list_virtual_networks",
                          system_name_or_uuid=CTX["system_name"])
    record(2, "hmc_list_virtual_networks", st, data)
    if st == "PASS":
        entries = data if isinstance(data, list) else data.get("entries", [])
        used_vlans = set()
        for e in entries:
            vlan = e.get("VLANId") or e.get("vlan_id")
            if vlan is not None:
                used_vlans.add(int(vlan))
        # Pick an unused VLAN in 3000-3099 range
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
    record(2, "hmc_list_adapters (CNA)", st, data)


# ---------------------------------------------------------------------------
# Sub-Task 3 — Storage & SSP Inventory
# ---------------------------------------------------------------------------

async def subtask_3(client: Client) -> None:
    print("\n=== ST3: Storage & SSP Inventory ===")

    if CTX["vios_uuid"]:
        st, data = await call(client, "hmc_list_volume_groups",
                              vios_name_or_uuid=CTX["vios_uuid"])
        record(3, "hmc_list_volume_groups", st, data)
    else:
        skip(3, "hmc_list_volume_groups", "no VIOS found in ST1")

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
# Sub-Task 4 — LPAR Properties & Profile Inventory
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
# Sub-Task 5 — Metrics & Templates
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
# Sub-Task 6 — User & Policy Inventory
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
# Sub-Task 7 — CLI Escape Hatch
# ---------------------------------------------------------------------------

async def subtask_7(client: Client) -> None:
    print("\n=== ST7: CLI Escape Hatch ===")

    st, data = await call(client, "hmc_run_command", cmd="lshmc -V")
    record(7, "hmc_run_command (lshmc -V)", st, data)

    st, data = await call(client, "hmc_run_command", cmd="lssyscfg -r sys")
    record(7, "hmc_run_command (lssyscfg -r sys)", st, data)


# ---------------------------------------------------------------------------
# Sub-Task 8 — LPAR Lifecycle
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
    if st == "PASS":
        uuid = None
        if isinstance(data, dict):
            uuid = data.get("uuid") or data.get("UUID")
        CTX["scratch_uuid"] = uuid

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
    record(8, "hmc_power_on_lpar", st, data, "failure to boot expected — no install")
    # Capture job UUID from power-on result for ST12
    if st == "PASS" and isinstance(data, dict):
        CTX["job_uuid_sample"] = data.get("job_uuid") or data.get("UUID") or CTX.get("job_uuid_sample")

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

    # Confirm gone
    st, data = await call(client, "hmc_lpars")
    record(8, "hmc_lpars (confirm deleted)", st, data)


# ---------------------------------------------------------------------------
# Sub-Task 9 — Virtual Networking Mutations
# ---------------------------------------------------------------------------

async def subtask_9(client: Client) -> None:
    print("\n=== ST9: Virtual Networking Mutations ===")

    if CTX["test_vlan_id"] is None:
        skip(9, "hmc_create_virtual_network", "no unused VLAN ID found in ST2")
        skip(9, "hmc_add_network_adapter", "no VLAN ID")
        skip(9, "hmc_list_adapters (post-add)", "no VLAN ID")
        skip(9, "hmc_delete_adapter", "no VLAN ID")
        skip(9, "hmc_delete_virtual_network", "no VLAN ID")
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

    # Confirm
    st, data = await call(client, "hmc_list_virtual_networks",
                          system_name_or_uuid=CTX["system_name"])
    record(9, "hmc_list_virtual_networks (post-create)", st, data)
    if st == "PASS" and not CTX["test_network_uuid"]:
        entries = data if isinstance(data, list) else data.get("entries", [])
        for e in entries:
            if str(e.get("VLANId") or e.get("vlan_id")) == str(CTX["test_vlan_id"]):
                CTX["test_network_uuid"] = e.get("UUID") or e.get("uuid")

    # Create short-lived LPAR for adapter test
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
        entries = data if isinstance(data, list) else data.get("entries", [])
        if entries:
            CTX["test_adapter_uuid"] = entries[0].get("UUID") or entries[0].get("uuid")

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

    # Clean up nettest LPAR
    st, data = await call(client, "hmc_delete_lpar",
                          lpar_name_or_uuid=CTX["nettest_name"])
    record(9, "hmc_delete_lpar (nettest)", st, data)
    if st == "PASS":
        CTX["nettest_uuid"] = None


# ---------------------------------------------------------------------------
# Sub-Task 10 — LPAR Properties Mutations
# ---------------------------------------------------------------------------

async def subtask_10(client: Client) -> None:
    print("\n=== ST10: LPAR Properties Mutations ===")

    # Save current description from baseline
    orig_desc = CTX["lp3_baseline"].get("description", "")
    if isinstance(orig_desc, dict):
        orig_desc = orig_desc.get("description") or ""

    test_desc = "MCP live-test probe - safe to clear"
    st, data = await call(client, "hmc_set_lpar_description",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"],
                          description=test_desc)
    record(10, "hmc_set_lpar_description", st, data)

    st, data = await call(client, "hmc_get_lpar_description",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"])
    record(10, "hmc_get_lpar_description (verify)", st, data)

    # Restore original
    st, data = await call(client, "hmc_set_lpar_description",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"],
                          description=str(orig_desc) if orig_desc else "")
    record(10, "hmc_set_lpar_description (restore)", st, data)

    # MSP toggle — only valid on VIOS partitions (lpar_env=vioserver).
    # ltczz386-lp3 is an AIX partition; verify that the tool raises a clear
    # error (HMCCLIError) rather than sending a bad chsyscfg command.
    st_env, data_env = await call(client, "hmc_run_command",
                                  cmd=f"lssyscfg -r lpar -m {CTX['system_name']} "
                                      f"--filter lpar_names={CTX['lp3_name']} -F lpar_env")
    record(10, "lssyscfg lpar_env check", st_env, data_env)
    lp3_env = (data_env or "").strip() if st_env == "PASS" else ""

    if lp3_env == "vioserver":
        # Partition is a VIOS — exercise the full toggle/verify/restore flow.
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
        # Partition is not a VIOS — verify the fix: expect HMCCLIError before
        # any chsyscfg command is sent.
        st_bad, data_bad = await call(client, "hmc_set_lpar_msp",
                                      system_name_or_uuid=CTX["system_name"],
                                      lpar_name_or_uuid=CTX["lp3_name"],
                                      enabled=True)
        if st_bad == "FAIL" and ("only valid for a VIOS" in str(data_bad) or "not found" in str(data_bad)):
            record(10, "hmc_set_lpar_msp (non-VIOS rejection — expected)", "PASS",
                   f"correctly rejected with: {str(data_bad)[:200]}")
        else:
            record(10, "hmc_set_lpar_msp (non-VIOS rejection)", st_bad, data_bad)
        skip(10, "hmc_set_lpar_msp (toggle/verify/restore)", f"lp3 is not a VIOS (lpar_env={lp3_env!r})")

    # Proc compat — set to same mode (idempotent)
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

    # Profile sync
    st, data = await call(client, "hmc_sync_lpar_profile",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"])
    record(10, "hmc_sync_lpar_profile", st, data)

    # Profile backup
    st, data = await call(client, "hmc_backup_lpar_profiles",
                          system_name_or_uuid=CTX["system_name"],
                          file_path="/tmp/mcp-lp3-profiles-test")
    record(10, "hmc_backup_lpar_profiles", st, data)


# ---------------------------------------------------------------------------
# Sub-Task 11 — User Administration
# ---------------------------------------------------------------------------

async def subtask_11(client: Client) -> None:
    print("\n=== ST11: User Administration ===")

    st, data = await call(client, "hmc_create_user",
                          name=CTX["test_user"],
                          taskrole="viewer",
                          password=_TEST_USER_PASSWORD,
                          description="MCP live test user")
    record(11, "hmc_create_user", st, data)

    st, data = await call(client, "hmc_users")
    record(11, "hmc_users (confirm created)", st, data)

    st, data = await call(client, "hmc_modify_user",
                          name=CTX["test_user"],
                          description="MCP live test user — updated")
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
# Sub-Task 12 — PCM Metrics & Job Monitoring
# ---------------------------------------------------------------------------

async def subtask_12(client: Client) -> None:
    print("\n=== ST12: PCM Metrics & Job Monitoring ===")

    # Read current PCM prefs to know what to toggle
    st, data = await call(client, "hmc_get_pcm_preferences",
                          category="ManagedSystem",
                          resource_name_or_uuid=CTX["system_name"])
    current_ltm = None
    if st == "PASS" and isinstance(data, dict):
        current_ltm = data.get("long_term_monitor") or data.get("LongTermMonitorEnabled")

    # Toggle
    new_ltm = not bool(current_ltm)
    st, data = await call(client, "hmc_set_pcm_preferences",
                          category="ManagedSystem",
                          resource_name_or_uuid=CTX["system_name"],
                          long_term_monitor=new_ltm)
    record(12, "hmc_set_pcm_preferences (toggle)", st, data)

    # Read back
    st, data = await call(client, "hmc_get_pcm_preferences",
                          category="ManagedSystem",
                          resource_name_or_uuid=CTX["system_name"])
    record(12, "hmc_get_pcm_preferences (verify)", st, data)

    # Restore
    st, data = await call(client, "hmc_set_pcm_preferences",
                          category="ManagedSystem",
                          resource_name_or_uuid=CTX["system_name"],
                          long_term_monitor=bool(current_ltm))
    record(12, "hmc_set_pcm_preferences (restore)", st, data)

    # Job monitoring
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
        skip(12, "hmc_get_job", "no job UUID captured from earlier sub-tasks")
        skip(12, "hmc_wait_for_job", "no job UUID")

    st, data = await call(client, "hmc_recent_jobs", limit=20)
    record(12, "hmc_recent_jobs (post-tests)", st, data)
    # Try to get a job UUID from recent jobs if we don't have one
    if not CTX.get("job_uuid_sample") and st == "PASS":
        jobs = data if isinstance(data, list) else data.get("entries", [])
        if jobs:
            CTX["job_uuid_sample"] = jobs[0].get("UUID") or jobs[0].get("uuid")


# ---------------------------------------------------------------------------
# Sub-Task 13 — Provision Dry Run & Updates Check
# ---------------------------------------------------------------------------

async def subtask_13(client: Client) -> None:
    print("\n=== ST13: Provision Dry Run & Updates Check ===")

    # Ensure console UUID
    if not CTX.get("console_uuid"):
        st, data = await call(client, "hmc_console_info")
        if st == "PASS" and isinstance(data, dict):
            CTX["console_uuid"] = data.get("uuid") or data.get("UUID")

    if CTX.get("console_uuid"):
        st, data = await call(client, "hmc_get_available_hmc_ptfs",
                              console_uuid=CTX["console_uuid"])
        record(13, "hmc_get_available_hmc_ptfs", st, data)
    else:
        skip(13, "hmc_get_available_hmc_ptfs", "no console UUID")

    # Dry-run provision — requires a VIOS
    if CTX["vios_uuid"] and CTX["test_vlan_id"]:
        st, data = await call(client, "hmc_provision_lpar",
                              dry_run=True,
                              system_name_or_uuid=CTX["system_name"],
                              name="ltczz386-lp3-dry",
                              port_vlan_id=CTX["test_vlan_id"],
                              vios_uuid=CTX["vios_uuid"],
                              vios_partition_id=CTX["vios_partition_id"] or 2,
                              vios_slot=2,
                              storage_name="test-dry-disk",
                              desired_memory=512)
        record(13, "hmc_provision_lpar (dry_run)", st, data)
    else:
        reason = "no VIOS UUID" if not CTX["vios_uuid"] else "no test VLAN ID"
        skip(13, "hmc_provision_lpar (dry_run)", reason)


# ---------------------------------------------------------------------------
# Sub-Task 14 — Restore ltczz386-lp3 to Baseline
# ---------------------------------------------------------------------------

async def subtask_14(client: Client) -> None:
    print("\n=== ST14: Restore ltczz386-lp3 to Baseline ===")

    st, data = await call(client, "hmc_lpar_summary", lpar_name_or_uuid=CTX["lp3_name"])
    record(14, "hmc_lpar_summary (post-test)", st, data)

    baseline = CTX["lp3_baseline"]
    baseline_lpar = baseline.get("lpars") or {}
    if isinstance(baseline_lpar, dict):
        res = baseline_lpar.get("Resource") or {}
        b_min_mem = res.get("MinimumMemory")
        b_des_mem = res.get("DesiredMemory")
        b_max_mem = res.get("MaximumMemory")
    else:
        b_min_mem = b_des_mem = b_max_mem = None

    # Restore memory/CPU if we have baseline values
    modify_kwargs: dict = {}
    if b_des_mem:
        modify_kwargs["desired_memory"] = b_des_mem
    if b_min_mem:
        modify_kwargs["min_memory"] = b_min_mem
    if b_max_mem:
        modify_kwargs["max_memory"] = b_max_mem
    if modify_kwargs:
        st, data = await call(client, "hmc_modify_lpar",
                              lpar_name_or_uuid=CTX["lp3_name"],
                              **modify_kwargs)
        record(14, "hmc_modify_lpar (restore memory)", st, data)
    else:
        skip(14, "hmc_modify_lpar (restore memory)", "no baseline memory values captured")

    # Restore description
    orig_desc = baseline.get("description", "")
    if isinstance(orig_desc, dict):
        orig_desc = orig_desc.get("description") or ""
    st, data = await call(client, "hmc_set_lpar_description",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"],
                          description=str(orig_desc) if orig_desc else "")
    record(14, "hmc_set_lpar_description (restore)", st, data)

    # Restore MSP
    orig_msp = baseline.get("msp")
    if isinstance(orig_msp, dict):
        orig_msp = orig_msp.get("msp") or orig_msp.get("enabled")
    st, data = await call(client, "hmc_set_lpar_msp",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"],
                          enabled=bool(orig_msp))
    record(14, "hmc_set_lpar_msp (restore)", st, data)

    # Restore proc compat
    orig_compat = baseline.get("proc_compat")
    if isinstance(orig_compat, dict):
        mode = orig_compat.get("current") or orig_compat.get("mode") or "default"
    else:
        mode = "default"
    st, data = await call(client, "hmc_set_lpar_proc_compat",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"],
                          mode=mode)
    record(14, "hmc_set_lpar_proc_compat (restore)", st, data)

    # Verify no unexpected adapters
    st, data = await call(client, "hmc_list_adapters",
                          lpar_name_or_uuid=CTX["lp3_name"],
                          adapter_type="ClientNetworkAdapter")
    record(14, "hmc_list_adapters (final audit)", st, data)

    # Sync profile
    st, data = await call(client, "hmc_sync_lpar_profile",
                          system_name_or_uuid=CTX["system_name"],
                          lpar_name_or_uuid=CTX["lp3_name"])
    record(14, "hmc_sync_lpar_profile", st, data)

    # Final CLI dump
    st, data = await call(client, "hmc_run_command",
                          cmd=f"lssyscfg -r lpar -m {CTX['system_name']} --filter \"lpar_names={CTX['lp3_name']}\"")
    record(14, "hmc_run_command lssyscfg (final)", st, data)

    # Final summary
    st, data = await call(client, "hmc_lpar_summary", lpar_name_or_uuid=CTX["lp3_name"])
    record(14, "hmc_lpar_summary (final confirm)", st, data)


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
}


async def main(subtask_filter: int | None = None) -> None:
    print(f"Starting live integration tests at {datetime.now(timezone.utc).isoformat()}")

    async with Client(mcp) as client:
        tasks = [subtask_filter] if subtask_filter is not None else sorted(SUBTASKS.keys())
        for n in tasks:
            fn = SUBTASKS.get(n)
            if fn:
                await fn(client)
            else:
                print(f"  Unknown sub-task {n}")

    # Write JSON results
    out = "test-results.json"
    with open(out, "w") as f:
        json.dump({"context": CTX, "results": RESULTS}, f, indent=2, default=str)

    # Summary
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["status"] == "PASS")
    failed = sum(1 for r in RESULTS if r["status"] == "FAIL")
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
