from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from hmc_mcp.config import HMCConfig
from hmc_mcp.snapshots.operations import capture_lpar_snapshot
from hmc_mcp.snapshots.operations import _placement
from hmc_mcp.operations.ssh_network import (
    MinimumAffinityPolicyResult,
    ResourceGroupAffinityResult,
)
from hmc_mcp.ssh.affinity import MemoptResourceGroupSelector


PROFILE = "name=default,lpar_name=aix,min_mem=4096,desired_mem=8192,max_mem=16384,proc_mode=shared,min_proc_units=0.5,desired_proc_units=1.0,max_proc_units=2.0,min_procs=1,desired_procs=2,max_procs=4,sharing_mode=uncap"


@pytest.mark.asyncio
async def test_capture_separates_configuration_and_observations(monkeypatch) -> None:
    hmc = AsyncMock()
    hmc.config = HMCConfig.from_mapping({"host": "h", "user": "u", "password": "p"})
    hmc.find_system_by_name.return_value = {"UUID": "sys-1"}
    hmc.find_partition_by_name.return_value = {"UUID": "lpar-1"}
    hmc.get_console_info.return_value = {
        "UUID": "hmc-1",
        "Resource": {"HostName": "hmc", "Version": "V11R1M1110"},
    }
    hmc.get_managed_system.return_value = {
        "UUID": "sys-1",
        "Resource": {
            "SystemName": "sys",
            "MachineTypeModelSerialNumber": {
                "MachineType": "9080",
                "Model": "HEX",
                "SerialNumber": "ABC",
            },
        },
    }
    hmc.get_logical_partition.return_value = {
        "UUID": "lpar-1",
        "Resource": {
            "PartitionName": "aix",
            "PartitionID": 7,
            "PartitionState": "running",
            "ResourceMonitoringControlState": "active",
            "CurrentMemory": 8192,
            "CurrentProcessingUnits": 1.0,
            "HasDedicatedProcessors": "false",
        },
    }
    monkeypatch.setattr(
        "hmc_mcp.snapshots.operations.read_lpar_profile_record",
        AsyncMock(return_value=PROFILE),
    )
    monkeypatch.setattr(
        "hmc_mcp.snapshots.operations.get_lpar_memopt_score",
        AsyncMock(
            return_value={
                "lpar_name": "aix",
                "lpar_id": "7",
                "curr_lpar_score": "95",
            }
        ),
    )
    monkeypatch.setattr(
        "hmc_mcp.snapshots.operations.get_system_memopt_score",
        AsyncMock(return_value={"curr_sys_score": "90"}),
    )
    monkeypatch.setattr(
        "hmc_mcp.snapshots.operations.plan_lpar_memopt_scores",
        AsyncMock(return_value=[{"predicted_lpar_score": "97"}]),
    )
    monkeypatch.setattr(
        "hmc_mcp.snapshots.operations.plan_system_memopt_score",
        AsyncMock(return_value={"predicted_sys_score": "92"}),
    )
    result = ResourceGroupAffinityResult(
        capability="capability-unavailable",
        mode="current",
        system="sys",
        selector=MemoptResourceGroupSelector(all=True),
        items=[],
        unavailable_reason="unsupported",
    )
    monkeypatch.setattr(
        "hmc_mcp.snapshots.operations.list_resource_group_memopt_scores",
        AsyncMock(return_value=result),
    )
    monkeypatch.setattr(
        "hmc_mcp.snapshots.operations.plan_resource_group_memopt_scores",
        AsyncMock(return_value=result),
    )
    monkeypatch.setattr(
        "hmc_mcp.snapshots.operations.get_minimum_affinity_policy",
        AsyncMock(
            return_value=MinimumAffinityPolicyResult(
                "available", "sys", "aix", 80, "warn", None
            )
        ),
    )
    snapshot = await capture_lpar_snapshot(
        hmc,
        "sys",
        "aix",
        "default",
    )
    payload = snapshot.model_dump(mode="json")
    assert "scores" not in payload["configuration"]
    assert (
        payload["observations"]["runtime_placement"]["data"]["current_memory_mib"]
        == 8192
    )
    assert (
        payload["observations"]["scores"]["data"]["resource_groups"]["current"][
            "capability"
        ]
        == "capability-unavailable"
    )
    assert payload["observations"]["minimum_affinity_policy"]["data"] == {
        "min_affinity_score": 80,
        "min_affinity_score_action": "warn",
    }
    monkeypatch.setattr(
        "hmc_mcp.snapshots.operations.get_minimum_affinity_policy",
        AsyncMock(
            return_value=MinimumAffinityPolicyResult(
                "capability-unavailable",
                "sys",
                "aix",
                None,
                None,
                "upgrade system firmware",
            )
        ),
    )
    unsupported = await capture_lpar_snapshot(
        hmc,
        "sys",
        "aix",
        "default",
    )
    unsupported_payload = unsupported.model_dump(mode="json", exclude_none=True)
    assert "minimum_affinity_policy" not in unsupported_payload["observations"]
    assert unsupported_payload["capabilities"][2] == {
        "name": "minimum-affinity-policy",
        "version": 1,
        "supported": False,
        "collection": "hmc-cli",
        "unavailable_reason": "upgrade system firmware",
    }
    monkeypatch.setattr(
        "hmc_mcp.snapshots.operations.read_lpar_profile_record",
        AsyncMock(return_value=PROFILE + ",padding=" + ("x" * 1_048_576)),
    )
    with pytest.raises(ValueError, match="1 MiB"):
        await capture_lpar_snapshot(
            hmc,
            "sys",
            "aix",
            "default",
        )


@pytest.mark.asyncio
async def test_capture_propagates_observation_failure(monkeypatch) -> None:
    hmc = AsyncMock()
    hmc.config = HMCConfig.from_mapping({"host": "h", "user": "u", "password": "p"})
    hmc.find_system_by_name.return_value = {"UUID": "sys-1"}
    hmc.find_partition_by_name.return_value = {"UUID": "lpar-1"}
    hmc.get_console_info.return_value = {"UUID": "hmc-1", "Resource": {}}
    hmc.get_managed_system.return_value = {
        "UUID": "sys-1",
        "Resource": {
            "SystemName": "sys",
            "MachineTypeModelSerialNumber": "9080-HEX*ABC",
        },
    }
    hmc.get_logical_partition.return_value = {
        "UUID": "lpar-1",
        "Resource": {"PartitionName": "aix", "PartitionID": 7},
    }
    monkeypatch.setattr(
        "hmc_mcp.snapshots.operations.read_lpar_profile_record",
        AsyncMock(return_value=PROFILE),
    )
    monkeypatch.setattr(
        "hmc_mcp.snapshots.operations.get_lpar_memopt_score",
        AsyncMock(side_effect=TimeoutError("timed out")),
    )
    with pytest.raises(TimeoutError, match="timed out"):
        await capture_lpar_snapshot(
            hmc,
            "sys",
            "aix",
            "default",
        )


def test_placement_maps_inactive_zero_allocations_to_null() -> None:
    assert _placement(
        {
            "PartitionState": "not activated",
            "HasDedicatedProcessors": "false",
            "CurrentMemory": "0",
            "CurrentProcessingUnits": "0.0",
        }
    ) == {
        "state": "not activated",
        "rmc_state": None,
        "processor_mode": "shared",
        "current_memory_mib": None,
        "current_processor_units": None,
        "dedicated_processors": None,
    }


def test_placement_rejects_boolean_integer_fields() -> None:
    with pytest.raises(ValueError, match="integer current memory"):
        _placement(
            {
                "PartitionState": "running",
                "HasDedicatedProcessors": "false",
                "CurrentMemory": False,
                "CurrentProcessingUnits": "1.0",
            }
        )


@pytest.mark.parametrize("value", [7.9, "7.9"])
def test_placement_rejects_fractional_integer_fields(value: object) -> None:
    with pytest.raises(ValueError, match="integer current memory"):
        _placement(
            {
                "PartitionState": "running",
                "HasDedicatedProcessors": "false",
                "CurrentMemory": value,
                "CurrentProcessingUnits": "1.0",
            }
        )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1"])
def test_placement_rejects_invalid_processor_units(value: str) -> None:
    with pytest.raises(ValueError, match="positive finite"):
        _placement(
            {
                "PartitionState": "running",
                "HasDedicatedProcessors": "false",
                "CurrentMemory": "1024",
                "CurrentProcessingUnits": value,
            }
        )
