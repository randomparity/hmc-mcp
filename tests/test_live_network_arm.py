"""The network live arm (ST2 reads, ST9 round trips, #629) against a scripted HMC."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

_RUNNER_PATH = Path(__file__).parents[1] / "scripts" / "live_test_runner.py"
sys.path.insert(0, str(_RUNNER_PATH.parent))
from live_test import network  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("hmc_live_test_runner", _RUNNER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
runner = sys.modules.get(_SPEC.name) or importlib.util.module_from_spec(_SPEC)
if _SPEC.name not in sys.modules:
    sys.modules[_SPEC.name] = runner
    _SPEC.loader.exec_module(runner)

SYSTEM = "acmesys9"
LPAR = "acmesys9-lp3"
VIOS = "vios-A"
EXISTING = {"0000000E-0000-4000-8000-00000000000E": (3100, "existing-net")}
SCSI_TOWARD_LPAR = "vios-A,1,11,server,3,2\nacmesys9-lp3,3,2,client,1,11\n"
SCSI_ANY = "vios-A,1,12,server,any,any\n"
FC_SERVER = {
    "lpar_name": VIOS,
    "lpar_id": "1",
    "slot_num": "31",
    "adapter_type": "server",
    "remote_lpar_id": "3",
    "remote_slot_num": "31",
}
REMOTE_FIELDS = {
    "VirtualSCSIClientAdapter": ("RemoteLogicalPartitionID", "RemoteSlotNumber"),
    "VirtualFibreChannelClientAdapter": (
        "ConnectingPartitionID",
        "ConnectingVirtualSlotNumber",
    ),
}
MUTATIONS = (
    "hmc_create_virtual_network",
    "hmc_delete_virtual_network",
    "hmc_add_network_adapter",
    "hmc_add_vscsi_adapter",
    "hmc_add_vfc_adapter",
    "hmc_delete_adapter",
    "hmc_set_vios_fc_port_label",
    "hmc_remove_vios_fc_port_label",
    "hmc_create_vios_vfc_group_label",
    "hmc_update_vios_vfc_group_label",
    "hmc_remove_vios_vfc_group_label",
)


@dataclass
class FakeHMC:
    """An HMC answering the arm's calls; each knob injects one behaviour."""

    state: str = "Not Activated"
    scsi: str = SCSI_TOWARD_LPAR
    fc: list[dict[str, str]] = field(default_factory=lambda: [dict(FC_SERVER)])
    networks: dict[str, tuple[int, str]] = field(default_factory=lambda: dict(EXISTING))
    adapters: dict[str, dict[str, dict[str, Any]]] = field(
        default_factory=lambda: {t: {} for t in network._ADAPTER_TYPES}
    )
    fc_labels: dict[str, str] = field(default_factory=lambda: {"fcs0": ""})
    groups: set[str] = field(default_factory=set)
    create_status: str = "PASS"
    create_takes_effect: bool = True
    duplicate_vlan_accepted: bool = False
    network_delete_fails: bool = False
    adapter_delete_fails: bool = False
    absent_port_accepted: bool = False
    labels_refused: bool = False
    group_remove_fails: bool = False
    adds_twice: bool = False
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    counter: int = 0

    def _new_uuid(self) -> str:
        self.counter += 1
        return f"0000{self.counter:04d}-0000-4000-8000-000000000000"

    def answer(self, tool: str, kwargs: dict[str, Any]) -> tuple[str, Any]:
        self.calls.append((tool, kwargs))
        handler = getattr(self, f"_{tool}", None)
        if handler is None:
            return "PASS", []
        return handler(kwargs)

    def _hmc_run_command(self, kwargs):
        cmd = kwargs["cmd"]
        if cmd.startswith("lssyscfg"):
            return "PASS", f"3,{self.state}\n"
        if "--rsubtype scsi" in cmd:
            return "PASS", self.scsi or "No results were found.\n"
        return "FAIL", f"unscripted command {cmd}"

    def _hmc_list_fc_ports(self, _kwargs):
        return "PASS", [dict(row) for row in self.fc]

    def _hmc_list_virtual_switches(self, _kwargs):
        return "PASS", [{"UUID": "sw-0", "Resource": {"SwitchID": "0"}}]

    def _hmc_list_virtual_networks(self, _kwargs):
        return "PASS", [
            {"UUID": key, "Resource": {"NetworkVLANID": str(vlan), "NetworkName": name}}
            for key, (vlan, name) in self.networks.items()
        ]

    def _hmc_create_virtual_network(self, kwargs):
        vlan = kwargs["vlan_id"]
        taken = any(v == vlan for v, _ in self.networks.values())
        if taken and not self.duplicate_vlan_accepted:
            return "FAIL", "HSCL VLAN already in use"
        if self.create_takes_effect:
            self.networks[self._new_uuid()] = (vlan, kwargs["name"])
        return self.create_status, {} if self.create_status == "PASS" else "timed out"

    def _hmc_delete_virtual_network(self, kwargs):
        if self.network_delete_fails:
            return "FAIL", "refused"
        self.networks.pop(kwargs["network_uuid"], None)
        return "PASS", kwargs["network_uuid"]

    def _hmc_list_adapters(self, kwargs):
        return "PASS", [
            {"UUID": key, "Resource": dict(resource)}
            for key, resource in self.adapters[kwargs["adapter_type"]].items()
        ]

    def _hmc_add_network_adapter(self, kwargs):
        self.adapters["ClientNetworkAdapter"][self._new_uuid()] = {
            "PortVLANID": str(kwargs["port_vlan_id"])
        }
        return "PASS", {}

    def _add_client(self, adapter_type, kwargs):
        partition, slot = REMOTE_FIELDS[adapter_type]
        for _ in range(2 if self.adds_twice else 1):
            self.adapters[adapter_type][self._new_uuid()] = {
                partition: str(kwargs["vios_partition_id"]),
                slot: str(kwargs["vios_slot"]),
            }
        return "PASS", {}

    def _hmc_add_vscsi_adapter(self, kwargs):
        return self._add_client("VirtualSCSIClientAdapter", kwargs)

    def _hmc_add_vfc_adapter(self, kwargs):
        return self._add_client("VirtualFibreChannelClientAdapter", kwargs)

    def _hmc_delete_adapter(self, kwargs):
        listed = self.adapters[kwargs["adapter_type"]]
        if kwargs["adapter_uuid"] not in listed:
            return "FAIL", "HTTP 404"
        if self.adapter_delete_fails:
            return "FAIL", "refused"
        del listed[kwargs["adapter_uuid"]]
        return "PASS", "deleted"

    def _hmc_list_storage_mappings(self, _kwargs):
        return "PASS", [{"id": "vhost0/vtd0", "backing_name": "lv0"}]

    def _hmc_list_vios_fc_port_labels(self, _kwargs):
        if self.labels_refused:
            return "FAIL", "HSCL label administration is not supported"
        return "PASS", [
            {"name": VIOS, "lpar_id": "1", "port_name": port, "port_label": label}
            for port, label in self.fc_labels.items()
        ]

    def _hmc_set_vios_fc_port_label(self, kwargs):
        port = kwargs["port_name"]
        if port not in self.fc_labels and not self.absent_port_accepted:
            return "FAIL", "HSCL port not found"
        if port in self.fc_labels:
            self.fc_labels[port] = kwargs["label"]
        return "PASS", {}

    def _hmc_remove_vios_fc_port_label(self, kwargs):
        if kwargs["port_name"] in self.fc_labels:
            self.fc_labels[kwargs["port_name"]] = ""
        return "PASS", {}

    def _hmc_list_vios_vfc_group_labels(self, _kwargs):
        if self.labels_refused:
            return "FAIL", "HSCL label administration is not supported"
        return "PASS", [{"name": name, "resources": "vfc"} for name in self.groups]

    def _hmc_create_vios_vfc_group_label(self, kwargs):
        if kwargs["label"] in self.groups:
            return "FAIL", "HSCL label exists"
        self.groups.add(kwargs["label"])
        return "PASS", {}

    def _hmc_update_vios_vfc_group_label(self, kwargs):
        self.groups.discard(kwargs["label"])
        self.groups.add(kwargs["new_name"])
        return "PASS", {}

    def _hmc_remove_vios_vfc_group_label(self, kwargs):
        if self.group_remove_fails:
            return "FAIL", "refused"
        self.groups.discard(kwargs["label"])
        return "PASS", {}

    def mutations(self) -> list[str]:
        return [tool for tool, _ in self.calls if tool in MUTATIONS]


def _state(monkeypatch, hmc: FakeHMC, group: str | None = "network"):
    async def scripted(_state, _client, tool, **kwargs):
        return hmc.answer(tool, kwargs)

    monkeypatch.setattr(runner.RunState, "call", scripted)
    config = runner.LiveTestConfig(system_name=SYSTEM, lp3_name=LPAR)
    return runner.RunState(config=config, group=group)


async def _run(monkeypatch, hmc: FakeHMC, group: str | None = "network"):
    state = _state(monkeypatch, hmc, group)
    await network.mutate_virtual_networking(object(), state)
    return state


def _results(state) -> dict[str, str]:
    return {
        entry["operation"]: entry["observation"]["result"]
        for entry in state.observations
    }


def _observation(state, operation: str) -> dict[str, Any]:
    return next(
        entry["observation"]
        for entry in state.observations
        if entry["operation"] == operation
    )


def _manual(state) -> list[str]:
    return [
        str(row["data"])
        for row in state.results
        if "MANUAL RECOVERY REQUIRED" in str(row["data"])
    ]


ST9_OPERATIONS = {
    "network.create_network",
    "network.delete_network",
    "adapter.add_network",
    "adapter.delete",
    "adapter.add_vscsi",
    "adapter.add_vfc",
    "vios_label.set_fc_port",
    "vios_label.remove_fc_port",
    "vios_label.create_vfc_group",
    "vios_label.update_vfc_group",
    "vios_label.remove_vfc_group",
}


@pytest.mark.asyncio
async def test_other_groups_skip_without_any_call(monkeypatch):
    hmc = FakeHMC()

    state = await _run(monkeypatch, hmc, group="round2")

    assert hmc.calls == []
    assert [row["status"] for row in state.results] == ["SKIP"]


@pytest.mark.asyncio
async def test_an_activated_partition_changes_nothing(monkeypatch):
    hmc = FakeHMC(state="Running")

    state = await _run(monkeypatch, hmc)

    assert hmc.mutations() == []
    assert state.observations == []


@pytest.mark.asyncio
async def test_round_trips_promote_every_st9_operation_and_restore_the_baseline(
    monkeypatch,
):
    hmc = FakeHMC(fc_labels={"fcs0": "prod-a", "fcs1": ""})

    state = await _run(monkeypatch, hmc)

    assert _results(state) == dict.fromkeys(ST9_OPERATIONS, "passed")
    assert hmc.networks == EXISTING
    assert all(not listed for listed in hmc.adapters.values())
    assert hmc.fc_labels == {"fcs0": "prod-a", "fcs1": ""}
    assert hmc.groups == set()
    compares = [r for r in state.results if r["tool"].startswith("network baseline")]
    assert compares and all(r["status"] == "PASS" for r in compares)
    # The existing network is never a delete target.
    deleted = [
        k["network_uuid"] for t, k in hmc.calls if t == "hmc_delete_virtual_network"
    ]
    assert set(deleted).isdisjoint(EXISTING)
    create = next(k for t, k in hmc.calls if t == "hmc_create_virtual_network")
    assert create["vlan_id"] == 3101
    assert _manual(state) == []


@pytest.mark.asyncio
async def test_a_refused_vlan_create_is_a_failure_not_a_skip(monkeypatch):
    """The 406 declaration is gone: the live run settles it."""
    hmc = FakeHMC(create_status="FAIL", create_takes_effect=False)

    state = await _run(monkeypatch, hmc)

    observation = _observation(state, "network.create_network")
    assert observation["result"] == "failed"
    assert "create-accepted" not in observation["assertions"]
    assert "network.delete_network" not in _results(state)
    assert "hmc_add_network_adapter" not in hmc.mutations()
    # The other round trips still run.
    assert _results(state)["adapter.add_vscsi"] == "passed"


@pytest.mark.asyncio
async def test_a_create_that_failed_but_took_effect_is_reconciled_away(monkeypatch):
    hmc = FakeHMC(create_status="FAIL")

    state = await _run(monkeypatch, hmc)

    assert hmc.networks == EXISTING
    observation = _observation(state, "network.create_network")
    assert observation["result"] == "failed"
    assert observation["cleanup"] == "passed"
    assert _results(state)["network.delete_network"] == "passed"


@pytest.mark.asyncio
async def test_an_accepted_duplicate_vlan_fails_its_assertion_and_is_deleted(
    monkeypatch,
):
    hmc = FakeHMC(duplicate_vlan_accepted=True)

    state = await _run(monkeypatch, hmc)

    observation = _observation(state, "network.create_network")
    assert "duplicate-vlan-refused" not in observation["assertions"]
    assert observation["result"] == "failed"
    assert hmc.networks == EXISTING


@pytest.mark.asyncio
async def test_a_failed_adapter_reversal_stops_every_later_mutation(monkeypatch):
    hmc = FakeHMC(adapter_delete_fails=True)

    state = await _run(monkeypatch, hmc)

    mutations = hmc.mutations()
    assert "hmc_add_vscsi_adapter" not in mutations
    assert "hmc_set_vios_fc_port_label" not in mutations
    assert "hmc_delete_virtual_network" not in mutations
    assert len(_manual(state)) == 2
    assert any(row["tool"] == "remaining round trips" for row in state.results)


@pytest.mark.asyncio
async def test_a_failed_network_delete_records_manual_recovery_and_stops(monkeypatch):
    hmc = FakeHMC(network_delete_fails=True)

    state = await _run(monkeypatch, hmc)

    assert _results(state)["network.delete_network"] == "failed"
    assert "hmc_add_vscsi_adapter" not in hmc.mutations()
    assert any("hmcpctl-live-vlan3101" in text for text in _manual(state))


@pytest.mark.asyncio
async def test_vscsi_prefers_a_server_slot_open_to_any_partition(monkeypatch):
    hmc = FakeHMC(scsi=SCSI_TOWARD_LPAR + SCSI_ANY)

    await _run(monkeypatch, hmc)

    add = next(k for t, k in hmc.calls if t == "hmc_add_vscsi_adapter")
    assert (add["vios_partition_id"], add["vios_slot"]) == (1, 12)


@pytest.mark.asyncio
async def test_two_new_adapters_fail_the_add_and_are_both_removed(monkeypatch):
    hmc = FakeHMC(adds_twice=True)

    state = await _run(monkeypatch, hmc)

    observation = _observation(state, "adapter.add_vscsi")
    assert observation["result"] == "failed"
    assert "adapter-added" not in observation["assertions"]
    assert observation["cleanup"] == "passed"
    assert all(not listed for listed in hmc.adapters.values())


@pytest.mark.asyncio
async def test_a_vfc_server_toward_another_partition_is_not_used(monkeypatch):
    hmc = FakeHMC(fc=[{**FC_SERVER, "remote_lpar_id": "7"}])

    state = await _run(monkeypatch, hmc)

    assert "hmc_add_vfc_adapter" not in hmc.mutations()
    assert "adapter.add_vfc" not in _results(state)
    assert _results(state)["adapter.add_vscsi"] == "passed"


@pytest.mark.asyncio
async def test_two_serving_vioses_leave_only_the_vlan_round_trip(monkeypatch):
    hmc = FakeHMC(scsi=SCSI_TOWARD_LPAR + "vios-B,2,11,server,3,4\n")

    state = await _run(monkeypatch, hmc)

    assert set(_results(state)) == {
        "network.create_network",
        "network.delete_network",
        "adapter.add_network",
        "adapter.delete",
    }


@pytest.mark.asyncio
async def test_an_empty_original_label_is_restored_by_removal(monkeypatch):
    hmc = FakeHMC()

    await _run(monkeypatch, hmc)

    sets = [k["label"] for t, k in hmc.calls if t == "hmc_set_vios_fc_port_label"]
    assert all(label.startswith("hmcpctl-live-") for label in sets)
    assert hmc.fc_labels == {"fcs0": ""}


@pytest.mark.asyncio
async def test_an_unrestorable_original_label_skips_the_label_round_trip(monkeypatch):
    hmc = FakeHMC(fc_labels={"fcs0": "   "})

    state = await _run(monkeypatch, hmc)

    assert "hmc_set_vios_fc_port_label" not in hmc.mutations()
    assert "vios_label.set_fc_port" not in _results(state)


@pytest.mark.asyncio
async def test_an_accepted_absent_port_label_is_removed_and_fails_the_negative(
    monkeypatch,
):
    hmc = FakeHMC(absent_port_accepted=True)

    state = await _run(monkeypatch, hmc)

    observation = _observation(state, "vios_label.set_fc_port")
    assert "unknown-port-refused" not in observation["assertions"]
    assert any(
        t == "hmc_remove_vios_fc_port_label" and k["port_name"] == network.ABSENT_PORT
        for t, k in hmc.calls
    )


@pytest.mark.asyncio
async def test_refused_label_reads_skip_only_the_label_round_trips(monkeypatch):
    hmc = FakeHMC(labels_refused=True)

    state = await _run(monkeypatch, hmc)

    assert not any(op.startswith("vios_label.") for op in _results(state))
    assert _results(state)["adapter.add_vfc"] == "passed"


@pytest.mark.asyncio
async def test_a_group_label_that_will_not_go_away_is_manual_recovery(monkeypatch):
    hmc = FakeHMC(group_remove_fails=True)

    state = await _run(monkeypatch, hmc)

    assert _results(state)["vios_label.remove_vfc_group"] == "failed"
    assert any("vFC group labels" in text for text in _manual(state))


# ---------------------------------------------------------------------------
# ST2
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inventory_verifies_non_empty_reads_and_never_promotes_empty_ones(
    monkeypatch,
):
    hmc = FakeHMC(fc=[], groups=set())
    state = _state(monkeypatch, hmc, group="round2")

    await network.inventory_network(object(), state)

    results = _results(state)
    assert results["network.list_switches"] == "passed"
    assert results["network.list_networks"] == "passed"
    assert results["vios_label.list_fc_ports"] == "passed"
    assert "network.list_fc_ports" not in results
    assert "vios_label.list_vfc_groups" not in results
    assert "adapter.list" not in results
    empty = [r["tool"] for r in state.results if r["tool"].endswith("(empty)")]
    assert "hmc_list_fc_ports (empty)" in empty
    assert all(r["result"] == "observed" for r in state.results if r["tool"] in empty)
    assert hmc.mutations() == []


@pytest.mark.asyncio
async def test_inventory_reads_all_four_adapter_types(monkeypatch):
    hmc = FakeHMC()
    hmc.adapters["VirtualSCSIClientAdapter"][
        "0000000A-0000-4000-8000-000000000000"
    ] = {}
    state = _state(monkeypatch, hmc, group="round2")

    await network.inventory_network(object(), state)

    listed = [k["adapter_type"] for t, k in hmc.calls if t == "hmc_list_adapters"]
    assert listed == list(network._ADAPTER_TYPES)
    assert _results(state)["adapter.list"] == "passed"


@pytest.mark.asyncio
async def test_a_failed_read_is_a_failed_observation(monkeypatch):
    hmc = FakeHMC(labels_refused=True)
    state = _state(monkeypatch, hmc, group="round2")

    await network.inventory_network(object(), state)

    observation = _observation(state, "vios_label.list_fc_ports")
    assert observation["result"] == "failed"
    assert observation["assertions"] == []


@pytest.mark.asyncio
async def test_an_out_of_range_vlan_fails_the_network_read(monkeypatch):
    hmc = FakeHMC(networks={"0000000E-0000-4000-8000-00000000000E": (4095, "bad")})
    state = _state(monkeypatch, hmc, group="round2")

    await network.inventory_network(object(), state)

    assert _results(state)["network.list_networks"] == "failed"
