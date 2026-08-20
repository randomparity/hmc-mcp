from __future__ import annotations

import shlex

import pytest

from hmc_mcp.config import HMCConfig
from hmc_mcp.ssh_commands import (
    add_vnic_backing,
    list_vnic_backing_rows,
    list_vnic_rows,
    read_vios_identity,
    remove_vnic_slot,
)


@pytest.fixture
def config() -> HMCConfig:
    return HMCConfig(host="hmc", user="user", _env_file=None)


@pytest.mark.asyncio
async def test_list_vnic_rows_requests_exact_fields(monkeypatch, config) -> None:
    fields = (
        "lpar_name,lpar_id,slot_num,desired_mode,curr_mode,auto_priority_failover,"
        "port_vlan_id,pvid_priority,allowed_vlan_ids,mac_addr,allowed_os_mac_addrs,"
        "backing_devices,backing_device_states"
    )
    output = (
        fields
        + "\nclient,3,2,ded,ded,1,0,0,all,eaad,all,sriov/vios/100/1/1/27004003/2.0/2.0/50/100.0/100.0,sriov/27004003/1/Operational\n"
    )
    calls: list[str] = []

    async def fake_run(_config, command: str) -> str:
        calls.append(command)
        return output

    monkeypatch.setattr("hmc_mcp.ssh_commands.run_hmc_command", fake_run)
    rows = await list_vnic_rows(config, "system one", "client one")

    assert rows[0]["slot_num"] == "2"
    assert shlex.split(calls[0]) == [
        "lshwres",
        "-r",
        "virtualio",
        "--rsubtype",
        "vnic",
        "--level",
        "lpar",
        "-m",
        "system one",
        "--filter",
        "lpar_names=client one",
        "-F",
        fields,
        "--header",
    ]


@pytest.mark.asyncio
async def test_list_vnic_backing_rows_accepts_hmc_empty_result(
    monkeypatch, config
) -> None:
    async def fake_run(_config, _command: str) -> str:
        return "No results were found.\n"

    monkeypatch.setattr("hmc_mcp.ssh_commands.run_hmc_command", fake_run)
    assert await list_vnic_backing_rows(config, "system") == []


@pytest.mark.asyncio
async def test_list_vnic_backing_rows_requests_exact_fields(
    monkeypatch, config
) -> None:
    fields = (
        "lpar_name,lpar_id,type,adapter_id,physical_port_id,logical_port_id,capacity,"
        "desired_capacity,max_capacity,desired_max_capacity,failover_priority,is_active,status"
    )
    calls: list[str] = []

    async def fake_run(_config, command: str) -> str:
        calls.append(command)
        return fields + "\n"

    monkeypatch.setattr("hmc_mcp.ssh_commands.run_hmc_command", fake_run)
    assert await list_vnic_backing_rows(config, "system one") == []
    assert shlex.split(calls[0]) == [
        "lshwres",
        "-r",
        "virtualio",
        "--rsubtype",
        "vnicbkdev",
        "-m",
        "system one",
        "-F",
        fields,
        "--header",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("output", ["bad=value\n", "lpar_name,lpar_id\nvios\n"])
async def test_collectors_reject_malformed_rows(
    monkeypatch, config, output: str
) -> None:
    async def fake_run(_config, _command: str) -> str:
        return output

    monkeypatch.setattr("hmc_mcp.ssh_commands.run_hmc_command", fake_run)
    with pytest.raises(ValueError):
        await list_vnic_backing_rows(config, "system")


@pytest.mark.asyncio
async def test_read_vios_identity_requires_exactly_one_row(monkeypatch, config) -> None:
    calls: list[str] = []

    async def fake_run(_config, _command: str) -> str:
        calls.append(_command)
        return "name,lpar_id,lpar_env\nvios,100,vioserver\n"

    monkeypatch.setattr("hmc_mcp.ssh_commands.run_hmc_command", fake_run)
    assert await read_vios_identity(config, "system", "vios") == {
        "name": "vios",
        "lpar_id": "100",
        "lpar_env": "vioserver",
    }
    assert shlex.split(calls[0]) == [
        "lssyscfg",
        "-r",
        "lpar",
        "-m",
        "system",
        "--filter",
        "lpar_names=vios",
        "-F",
        "name,lpar_id,lpar_env",
        "--header",
    ]


@pytest.mark.asyncio
async def test_add_vnic_uses_p_and_quotes_whole_payload(monkeypatch, config) -> None:
    calls: list[str] = []

    async def fake_run(_config, command: str) -> str:
        calls.append(command)
        return "added"

    monkeypatch.setattr("hmc_mcp.ssh_commands.run_hmc_command", fake_run)
    result = await add_vnic_backing(
        config, "system one", "client one", "sriov/vios name/100/1/1/2; touch nope", 7
    )
    assert result == "added"
    assert shlex.split(calls[0]) == [
        "chhwres",
        "-r",
        "virtualio",
        "--rsubtype",
        "vnic",
        "-o",
        "a",
        "-m",
        "system one",
        "-p",
        "client one",
        "-a",
        "port_vlan_id=7,backing_devices=sriov/vios name/100/1/1/2; touch nope",
    ]


@pytest.mark.asyncio
async def test_remove_vnic_uses_p_and_s(monkeypatch, config) -> None:
    calls: list[str] = []

    async def fake_run(_config, command: str) -> str:
        calls.append(command)
        return "removed"

    monkeypatch.setattr("hmc_mcp.ssh_commands.run_hmc_command", fake_run)
    assert (
        await remove_vnic_slot(config, "system one", "client one", "slot one")
        == "removed"
    )
    assert shlex.split(calls[0]) == [
        "chhwres",
        "-r",
        "virtualio",
        "--rsubtype",
        "vnic",
        "-o",
        "r",
        "-m",
        "system one",
        "-p",
        "client one",
        "-s",
        "slot one",
    ]
