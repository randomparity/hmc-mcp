from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from hmc_mcp.config import HMCConfig
from hmc_mcp.operations_snapshot import capture_lpar_snapshot
from hmc_mcp.operations_ssh_network import ResourceGroupAffinityResult
from hmc_mcp.ssh_commands import MemoptResourceGroupSelector


PROFILE = "name=default,lpar_name=aix,min_mem=4096,desired_mem=8192,max_mem=16384,proc_mode=shared,min_proc_units=0.5,desired_proc_units=1.0,max_proc_units=2.0,min_procs=1,desired_procs=2,max_procs=4,sharing_mode=uncap"


@pytest.mark.asyncio
async def test_capture_separates_configuration_and_observations(monkeypatch) -> None:
    hmc = AsyncMock()
    hmc.find_system_by_name.return_value = {"UUID": "sys-1"}
    hmc.find_partition_by_name.return_value = {"UUID": "lpar-1"}
    hmc.get_console_info.return_value = {"UUID": "hmc-1", "Resource": {"HostName": "hmc", "Version": "V11R1M1110"}}
    hmc.get_managed_system.return_value = {"UUID": "sys-1", "Resource": {"SystemName": "sys", "MachineTypeModelSerialNumber": {"MachineType": "9080", "Model": "HEX", "SerialNumber": "ABC"}}}
    hmc.get_logical_partition.return_value = {"UUID": "lpar-1", "Resource": {"PartitionName": "aix", "PartitionID": 7, "PartitionState": "running", "ResourceMonitoringControlState": "active", "CurrentMemory": 8192, "CurrentProcessingUnits": 1.0, "HasDedicatedProcessors": "false"}}
    monkeypatch.setattr("hmc_mcp.operations_snapshot.read_lpar_profile_record", AsyncMock(return_value=PROFILE))
    monkeypatch.setattr("hmc_mcp.operations_snapshot.get_lpar_memopt_score", AsyncMock(return_value={"curr_lpar_score": "95"}))
    monkeypatch.setattr("hmc_mcp.operations_snapshot.get_system_memopt_score", AsyncMock(return_value={"curr_sys_score": "90"}))
    monkeypatch.setattr("hmc_mcp.operations_snapshot.plan_lpar_memopt_scores", AsyncMock(return_value=[{"predicted_lpar_score": "97"}]))
    monkeypatch.setattr("hmc_mcp.operations_snapshot.plan_system_memopt_score", AsyncMock(return_value={"predicted_sys_score": "92"}))
    result = ResourceGroupAffinityResult(capability="capability-unavailable", mode="current", system="sys", selector=MemoptResourceGroupSelector(all=True), items=[], unavailable_reason="unsupported")
    monkeypatch.setattr("hmc_mcp.operations_snapshot.list_resource_group_memopt_scores", AsyncMock(return_value=result))
    monkeypatch.setattr("hmc_mcp.operations_snapshot.plan_resource_group_memopt_scores", AsyncMock(return_value=result))
    snapshot = await capture_lpar_snapshot(hmc, HMCConfig(host="h", user="u", password="p", _env_file=None), "sys", "aix", "default")
    payload = snapshot.model_dump(mode="json")
    assert "scores" not in payload["configuration"]
    assert payload["observations"]["runtime_placement"]["data"]["current_memory_mib"] == 8192
    assert payload["observations"]["scores"]["data"]["resource_groups"]["current"]["capability"] == "capability-unavailable"


@pytest.mark.asyncio
async def test_capture_propagates_observation_failure(monkeypatch) -> None:
    hmc = AsyncMock()
    hmc.find_system_by_name.return_value = {"UUID": "sys-1"}
    hmc.find_partition_by_name.return_value = {"UUID": "lpar-1"}
    hmc.get_console_info.return_value = {"UUID": "hmc-1", "Resource": {}}
    hmc.get_managed_system.return_value = {"UUID": "sys-1", "Resource": {"SystemName": "sys", "MachineTypeModelSerialNumber": "9080-HEX*ABC"}}
    hmc.get_logical_partition.return_value = {"UUID": "lpar-1", "Resource": {"PartitionName": "aix", "PartitionID": 7}}
    monkeypatch.setattr("hmc_mcp.operations_snapshot.read_lpar_profile_record", AsyncMock(return_value=PROFILE))
    monkeypatch.setattr("hmc_mcp.operations_snapshot.get_lpar_memopt_score", AsyncMock(side_effect=TimeoutError("timed out")))
    with pytest.raises(TimeoutError, match="timed out"):
        await capture_lpar_snapshot(hmc, HMCConfig(host="h", user="u", password="p", _env_file=None), "sys", "aix", "default")
