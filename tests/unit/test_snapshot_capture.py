from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from conftest import live_fixture

from hmcpctl.config import HMCConfig
from hmcpctl.operations.affinity.ssh import (
    MinimumAffinityPolicyResult,
    ResourceGroupAffinityResult,
)
from hmcpctl.snapshots.operations import _placement, capture_lpar_snapshot
from hmcpctl.ssh.affinity import MemoptResourceGroupSelector
from hmcpctl.xmlutil import parse_feed

PROFILE = "name=default,lpar_name=aix,min_mem=4096,desired_mem=8192,max_mem=16384,proc_mode=shared,min_proc_units=0.5,desired_proc_units=1.0,max_proc_units=2.0,min_procs=1,desired_procs=2,max_procs=4,sharing_mode=uncap"


@pytest.mark.asyncio
async def test_capture_separates_configuration_and_observations(monkeypatch) -> None:
    hmc = AsyncMock()
    hmc.config = HMCConfig.from_mapping({"host": "h", "user": "u", "password": "p"})
    hmc.find_system_by_name.return_value = {"UUID": "sys-1"}
    hmc.find_partition_by_name.return_value = {"UUID": "lpar-1"}
    hmc.get_console_info.return_value = {
        "UUID": "hmc-1",
        "Resource": {
            "ManagementConsoleName": "hmc\n",
            "VersionInfo": {"Version": "11", "Release": "1", "ServicePackName": "1110"},
        },
    }
    hmc.get_managed_system.return_value = {
        "UUID": "sys-1",
        "Resource": {
            "SystemName": "sys",
            "MachineTypeModelAndSerialNumber": {
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
            **_resources(memory=8192, units=1.0),
        },
    }
    monkeypatch.setattr(
        "hmcpctl.snapshots.operations.read_lpar_profile_record",
        AsyncMock(return_value=PROFILE),
    )
    monkeypatch.setattr(
        "hmcpctl.snapshots.operations.get_lpar_memopt_score",
        AsyncMock(
            return_value={
                "lpar_name": "aix",
                "lpar_id": "7",
                "curr_lpar_score": "95",
            }
        ),
    )
    monkeypatch.setattr(
        "hmcpctl.snapshots.operations.get_system_memopt_score",
        AsyncMock(return_value={"curr_sys_score": "90"}),
    )
    monkeypatch.setattr(
        "hmcpctl.snapshots.operations.plan_lpar_memopt_scores",
        AsyncMock(return_value=[{"predicted_lpar_score": "97"}]),
    )
    monkeypatch.setattr(
        "hmcpctl.snapshots.operations.plan_system_memopt_score",
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
        "hmcpctl.snapshots.operations.list_resource_group_memopt_scores",
        AsyncMock(return_value=result),
    )
    monkeypatch.setattr(
        "hmcpctl.snapshots.operations.plan_resource_group_memopt_scores",
        AsyncMock(return_value=result),
    )
    monkeypatch.setattr(
        "hmcpctl.snapshots.operations.get_minimum_affinity_policy",
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
        "hmcpctl.snapshots.operations.get_minimum_affinity_policy",
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
        "hmcpctl.snapshots.operations.read_lpar_profile_record",
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
            "MachineTypeModelAndSerialNumber": {
                "MachineType": "9080",
                "Model": "HEX",
                "SerialNumber": "ABC",
            },
        },
    }
    hmc.get_logical_partition.return_value = {
        "UUID": "lpar-1",
        "Resource": {"PartitionName": "aix", "PartitionID": 7},
    }
    monkeypatch.setattr(
        "hmcpctl.snapshots.operations.read_lpar_profile_record",
        AsyncMock(return_value=PROFILE),
    )
    monkeypatch.setattr(
        "hmcpctl.snapshots.operations.get_lpar_memopt_score",
        AsyncMock(side_effect=TimeoutError("timed out")),
    )
    with pytest.raises(TimeoutError, match="timed out"):
        await capture_lpar_snapshot(
            hmc,
            "sys",
            "aix",
            "default",
        )


def _resources(
    *, memory: object, units: object = None, processors: object = None
) -> dict[str, object]:
    """The nested V10R3 memory and processor containers a placement reads."""
    return {
        "PartitionMemoryConfiguration": {"CurrentMemory": memory},
        "PartitionProcessorConfiguration": {
            "CurrentHasDedicatedProcessors": "false" if processors is None else "true",
            "CurrentSharedProcessorConfiguration": {"CurrentProcessingUnits": units},
            "CurrentDedicatedProcessorConfiguration": {"CurrentProcessors": processors},
        },
    }


@pytest.mark.asyncio
async def test_capture_reads_captured_console_partition_and_profile(
    monkeypatch,
) -> None:
    """Every HMC answer here is a V10R3 capture; only the system is a stub."""
    hmc = AsyncMock()
    hmc.config = HMCConfig.from_mapping({"host": "h", "user": "u", "password": "p"})
    hmc.find_system_by_name.return_value = {"UUID": "sys-1"}
    lpar = parse_feed(live_fixture("rest-lpar-entry-lp3")["body"])[0]
    hmc.find_partition_by_name.return_value = lpar
    hmc.get_console_info.return_value = parse_feed(
        live_fixture("rest-management-console")["body"]
    )[0]
    hmc.get_managed_system.return_value = {
        "UUID": "sys-1",
        "Resource": {
            "SystemName": "sys-R1",
            "MachineTypeModelAndSerialNumber": {
                "MachineType": "8375",
                "Model": "42A",
                "SerialNumber": "ABC",
            },
        },
    }
    hmc.get_logical_partition.return_value = lpar
    monkeypatch.setattr(
        "hmcpctl.snapshots.operations.read_lpar_profile_record",
        AsyncMock(return_value=live_fixture("cli-prof-lp3")["stdout"].strip()),
    )
    unavailable = ResourceGroupAffinityResult(
        capability="capability-unavailable",
        mode="current",
        system="sys-R1",
        selector=MemoptResourceGroupSelector(all=True),
        items=[],
        unavailable_reason="unsupported",
    )
    for name, value in {
        "get_lpar_memopt_score": None,
        "get_system_memopt_score": None,
        "plan_lpar_memopt_scores": [],
        "plan_system_memopt_score": None,
        "list_resource_group_memopt_scores": unavailable,
        "plan_resource_group_memopt_scores": unavailable,
        "get_minimum_affinity_policy": MinimumAffinityPolicyResult(
            "capability-unavailable", "sys-R1", "sys-R1-lp3", None, None, "POWER9"
        ),
    }.items():
        monkeypatch.setattr(
            f"hmcpctl.snapshots.operations.{name}", AsyncMock(return_value=value)
        )

    snapshot = await capture_lpar_snapshot(
        hmc, "sys-R1", "sys-R1-lp3", "default_profile"
    )

    assert snapshot.source.hmc.name == "hmc.test"
    assert snapshot.source.hmc.version == "V10R3M1060"
    assert snapshot.source.lpar.partition_id == 1
    assert snapshot.configuration.normalized.processors.desired == 0.3
    placement = snapshot.observations.runtime_placement
    assert placement is not None
    assert placement.data == {
        "state": "not activated",
        "rmc_state": "inactive",
        "processor_mode": "shared",
        "current_memory_mib": None,
        "current_processor_units": None,
        "dedicated_processors": None,
    }


def test_placement_reads_nested_current_figures() -> None:
    resource = parse_feed(live_fixture("rest-lpar-entry")["body"])[0]["Resource"]
    assert _placement(resource) == {
        "state": "not activated",
        "rmc_state": "inactive",
        "processor_mode": "shared",
        "current_memory_mib": 2048,
        "current_processor_units": 0.2,
        "dedicated_processors": None,
    }


def test_placement_reads_dedicated_processor_count() -> None:
    placement = _placement(
        {"PartitionState": "running", **_resources(memory="4096", processors="2")}
    )
    assert placement["processor_mode"] == "dedicated"
    assert placement["dedicated_processors"] == 2
    assert placement["current_processor_units"] is None


def test_placement_maps_inactive_zero_allocations_to_null() -> None:
    assert _placement(
        {"PartitionState": "not activated", **_resources(memory="0", units="0.0")}
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
            {"PartitionState": "running", **_resources(memory=False, units="1.0")}
        )


@pytest.mark.parametrize("value", [7.9, "7.9"])
def test_placement_rejects_fractional_integer_fields(value: object) -> None:
    with pytest.raises(ValueError, match="integer current memory"):
        _placement(
            {"PartitionState": "running", **_resources(memory=value, units="1.0")}
        )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1"])
def test_placement_rejects_invalid_processor_units(value: str) -> None:
    with pytest.raises(ValueError, match="positive finite"):
        _placement(
            {"PartitionState": "running", **_resources(memory="1024", units=value)}
        )
