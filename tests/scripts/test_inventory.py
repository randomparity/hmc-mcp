"""Behavioral tests for the live-runner inventory scenarios."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

LIVE_TEST_ROOT = Path(__file__).parents[2] / "scripts"
sys.path.insert(0, str(LIVE_TEST_ROOT))
from live_test import inventory  # noqa: E402


class ScenarioState:
    """Small state seam that records inventory calls and context mutations."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.results: list[tuple[int, str, str, Any]] = []
        self.context = SimpleNamespace(
            system_name="system one",
            lp3_name="lp three",
            lp3_uuid=None,
            lp3_baseline={},
            vios_uuid=None,
            vios_partition_id=None,
            test_vswitch_id=None,
            test_vlan_id=None,
            vdisk_name="disk-one",
            vdisk_size_mib=None,
            vg_uuid=None,
            vdisk_vg_name=None,
        )

    async def call(self, _client: object, tool: str, **kwargs: Any) -> tuple[str, Any]:
        self.calls.append((tool, kwargs))
        response = self.responses.get(tool, {})
        if callable(response):
            response = response(kwargs)
        return "PASS", response

    def record(self, stage: int, tool: str, status: str, data: Any) -> None:
        self.results.append((stage, tool, status, data))

    def skip(self, stage: int, tool: str, reason: str) -> None:
        self.results.append((stage, tool, "SKIP", reason))


@pytest.mark.asyncio
async def test_baseline_capture_preserves_identity_and_adapter_topology() -> None:
    def adapters(kwargs: dict[str, Any]) -> list[dict[str, Any]]:
        if kwargs["adapter_type"] == "ClientNetworkAdapter":
            return [
                {"Resource": {"PortVLANID": "42", "VirtualSwitchID": "7"}}
            ]
        return [
            {
                "Resource": {
                    "RemoteLogicalPartitionID": "2",
                    "ServerAdapter": {"VirtualSlotNumber": "11"},
                }
            }
        ]

    state = ScenarioState(
        {
            "hmc_get_lpar": {"UUID": "lpar-uuid"},
            "hmc_lpar_summary": {"state": "running"},
            "hmc_get_lpar_description": {"description": "baseline"},
            "hmc_get_lpar_msp": True,
            "hmc_get_lpar_proc_compat": "POWER10",
            "hmc_list_adapters": adapters,
            "hmc_list_vios": [
                {"UUID": "vios-uuid", "Resource": {"PartitionID": "2"}}
            ],
            "hmc_run_command": "name=lp three",
        }
    )

    await inventory.capture_lpar_baseline(None, state)

    assert state.context.lp3_uuid == "lpar-uuid"
    assert state.context.vios_uuid == "vios-uuid"
    assert state.context.vios_partition_id == 2
    assert state.context.lp3_baseline["description"] == "baseline"
    assert state.context.lp3_baseline["pvid"] == 42
    assert state.context.lp3_baseline["vswitch_id"] == 7
    assert state.context.lp3_baseline["vios_partition_id"] == 2
    assert state.context.lp3_baseline["vios_slot"] == 11
    command = next(kwargs["cmd"] for tool, kwargs in state.calls if tool == "hmc_run_command")
    assert "-m 'system one'" in command
    assert "lpar_names=lp three" in command


@pytest.mark.asyncio
async def test_network_inventory_selects_unused_vlan_and_switch() -> None:
    state = ScenarioState(
        {
            "hmc_list_virtual_switches": [
                {"Resource": {"SwitchID": "9"}}
            ],
            "hmc_list_virtual_networks": [
                {"Resource": {"NetworkVLANID": "3000"}},
                {"Resource": {"NetworkVLANID": "3002"}},
            ],
        }
    )

    await inventory.inventory_network(None, state)

    assert state.context.test_vswitch_id == 9
    assert state.context.test_vlan_id == 3001
    assert [tool for tool, _ in state.calls] == [
        "hmc_list_virtual_switches",
        "hmc_list_virtual_networks",
        "hmc_list_network_bridges",
        "hmc_list_fc_ports",
        "hmc_list_sea_adapters",
        "hmc_list_adapters",
    ]


@pytest.mark.asyncio
async def test_storage_inventory_finds_disk_capacity_and_owning_group() -> None:
    state = ScenarioState(
        {
            "hmc_list_volume_groups": [
                {
                    "UUID": "vg-uuid",
                    "Resource": {
                        "GroupName": "rootvg",
                        "VirtualDisks": {
                            "VirtualDisk": {
                                "DiskName": "disk-one",
                                "DiskCapacity": "8",
                            }
                        },
                    },
                }
            ]
        }
    )
    state.context.vios_uuid = "vios-uuid"

    await inventory.inventory_storage(None, state)

    assert state.context.vg_uuid == "vg-uuid"
    assert state.context.vdisk_vg_name == "rootvg"
    assert state.context.vdisk_size_mib == 8192
    assert [tool for tool, _ in state.calls] == [
        "hmc_list_volume_groups",
        "hmc_list_clusters",
        "hmc_list_shared_storage_pools",
        "hmc_list_io_slots",
        "hmc_list_memory_pools",
    ]
