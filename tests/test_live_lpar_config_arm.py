"""The lpar-config live arm (ST39, #1345) against a scripted HMC."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

_RUNNER_PATH = Path(__file__).parents[1] / "scripts" / "live_test_runner.py"
sys.path.insert(0, str(_RUNNER_PATH.parent))
from live_test import lpar_config  # noqa: E402
from live_test.observation import CallFailure  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("hmc_live_test_runner", _RUNNER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
runner = sys.modules.get(_SPEC.name) or importlib.util.module_from_spec(_SPEC)
if _SPEC.name not in sys.modules:
    sys.modules[_SPEC.name] = runner
    _SPEC.loader.exec_module(runner)

SYSTEM = "sys-A"
OTHER = "lpar-A"
SCRATCH_UUID = "0A1B2C3D-0000-4000-8000-000000000039"
PROFILE_UUID = "0A1B2C3D-0000-4000-8000-0000000000F1"
REGION = 256
MUTATIONS = frozenset(
    {
        "hmc_modify_lpar",
        "hmc_dlpar_mem",
        "hmc_dlpar_proc",
        "hmc_rename_lpar",
        "hmc_set_lpar_boot_order",
        "hmc_clear_lpar_boot_order",
        "hmc_power_on_lpar",
        "hmc_power_off_lpar",
        "hmc_delete_lpar",
    }
)
_MEMORY = {"min_memory": "MinimumMemory", "desired_memory": "DesiredMemory"}
_MEMORY |= {"max_memory": "MaximumMemory"}
_SHARED = {
    "desired_procs": "DesiredProcessingUnits",
    "max_procs": "MaximumProcessingUnits",
    "desired_vcpus": "DesiredVirtualProcessors",
    "max_vcpus": "MaximumVirtualProcessors",
}


def _failure(message: str, status: int | None = None) -> CallFailure:
    return CallFailure("HMCError", message, "", status, False)


@dataclass
class FakeHMC:
    """One managed system holding OTHER and, once created, the scratch partition."""

    existing_scratch: bool = False
    ignore_memory_write: bool = False
    modify_406: bool = False
    foreign_token: bool = False
    pool_drift: bool = False
    pool_lag: bool = False
    activated_dlpar_refused: bool = True
    never_activates: bool = False
    create_without_uuid: bool = False
    power_off_refused: bool = False
    stale_absence: bool = False
    name: str | None = None
    token: str | None = None
    values: dict[str, float] = field(default_factory=dict)
    state: str = "Not Activated"
    pending: str | None = None
    deleted: bool = False
    pool_reads: int = 0
    stale_served: bool = False
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def answer(self, tool: str, kwargs: dict[str, Any]) -> tuple[str, Any]:
        self.calls.append((tool, kwargs))
        return getattr(self, "_" + tool)(kwargs)

    def exists(self) -> bool:
        return self.name is not None and not self.deleted

    def is_scratch(self, selector: object) -> bool:
        return self.exists() and selector in {SCRATCH_UUID, self.name}

    def _hmc_run_command(self, kwargs):
        cmd = kwargs["cmd"]
        if cmd.startswith("lssyscfg"):
            names = [OTHER] + (
                ["hmcpctl-live-lpar-00000000"] if self.existing_scratch else []
            )
            names += [self.name] if self.exists() else []
            return "PASS", "\n".join(names) + "\n"
        self.pool_reads += 1 if "proc" in cmd else 0
        drift = self.deleted and (
            self.pool_drift or (self.pool_lag and self.pool_reads == 2)
        )
        if "proc" in cmd:
            return "PASS", "7.5\n" if drift else "8.0\n"
        return "PASS", f"100000,{REGION}\n"

    def _hmc_create_lpar(self, kwargs):
        self.name, self.token = kwargs["name"], kwargs["caller_token"]
        resources = kwargs["resources"]
        self.values = {key: float(resources[key]) for key in _MEMORY | _SHARED}
        lpar = {} if self.create_without_uuid else {"UUID": SCRATCH_UUID}
        return "PASS", {"lpar": lpar, "steps": []}

    def _resource(self) -> dict[str, Any]:
        memory = {_MEMORY[k]: str(self.values[k]) for k in _MEMORY}
        shared = {_SHARED[k]: str(self.values[k]) for k in _SHARED}
        return {
            "PartitionMemoryConfiguration": memory,
            "PartitionProcessorConfiguration": {"SharedProcessorConfiguration": shared},
            "AssociatedPartitionProfile": {
                "href": f"https://hmc.example.test/rest/api/uom/x/{PROFILE_UUID}"
            },
        }

    def _hmc_get_lpar(self, kwargs):
        if not self.is_scratch(kwargs["lpar_name_or_uuid"]):
            return "FAIL", _failure("LPAR not found")
        return "PASS", {"UUID": SCRATCH_UUID, "Resource": self._resource()}

    def _hmc_get_lpar_description(self, kwargs):
        if not self.is_scratch(kwargs["lpar_name_or_uuid"]):
            if self.stale_absence and not self.stale_served:
                self.stale_served = True
                return "FAIL", _failure("Connection reset")
            return "FAIL", _failure("HSCL8012 The partition named x was not found")
        token = "lparcfg-ffffffff" if self.foreign_token else self.token
        return "PASS", f"[hmcpctl owner:agent created:2026-10-06] [caller {token}]"

    def _hmc_get_lpar_state(self, kwargs):
        return "PASS", self.state

    def _change(self, resources: dict[str, Any]) -> tuple[str, Any]:
        if not resources:
            return "FAIL", _failure("Nothing to change: pass at least one field")
        if self.state != "Not Activated" and self.activated_dlpar_refused:
            return "FAIL", _failure("HSCL294C no RMC connection")
        for key, value in resources.items():
            self.values[key] = float(value)
        return "PASS", {"change_location": {"lives_in": "current-configuration"}}

    def _hmc_modify_lpar(self, kwargs):
        if self.modify_406:
            return "FAIL", _failure("HTTP 406 Not Acceptable", 406)
        return self._change(kwargs["resources"])

    def _hmc_dlpar_mem(self, kwargs):
        if self.ignore_memory_write and kwargs["resources"]:
            return "PASS", {}
        return self._change(kwargs["resources"])

    def _hmc_dlpar_proc(self, kwargs):
        return self._change(kwargs["resources"])

    def _hmc_rename_lpar(self, kwargs):
        self.name = kwargs["new_name"]
        return "PASS", {}

    def _hmc_read_lpar_boot_order(self, kwargs):
        return "PASS", {"pending_boot_string": self.pending}

    def _hmc_set_lpar_boot_order(self, kwargs):
        self.pending = " ".join(kwargs["devices"])
        return "PASS", {}

    def _hmc_clear_lpar_boot_order(self, kwargs):
        return "FAIL", _failure(
            "Refusing to clear the pending boot order: no value tried on a V10R3 HMC"
        )

    def _hmc_power_on_lpar(self, kwargs):
        if not self.never_activates:
            self.state = "Open Firmware"
        return "PASS", {"job": {"status": "COMPLETED_OK"}}

    def _hmc_power_off_lpar(self, kwargs):
        if self.power_off_refused:
            return "FAIL", _failure("HSCL0001 refused")
        self.state = "Not Activated"
        return "PASS", {}

    def _hmc_delete_lpar(self, kwargs):
        self.deleted = True
        return "PASS", "deleted"


@pytest.fixture(autouse=True)
def _no_waits(monkeypatch):
    for name in (
        "_ABSENCE_REREAD_DELAY_S",
        "_STATE_POLL_DELAY_S",
        "_COMPARE_REREAD_DELAY_S",
    ):
        monkeypatch.setattr(lpar_config, name, 0)
    monkeypatch.setattr(lpar_config, "_STATE_POLL_ATTEMPTS", 2)


async def _run(monkeypatch, hmc: FakeHMC, group: str | None = "lpar-config"):
    async def scripted(_state, _client, tool, **kwargs):
        return hmc.answer(tool, kwargs)

    monkeypatch.setattr(runner.RunState, "call", scripted)
    state = runner.RunState(
        config=runner.LiveTestConfig(system_name=SYSTEM), group=group
    )
    await lpar_config.exercise_lpar_config(object(), state)
    return state


def _results(state) -> dict[str, str]:
    return {e["operation"]: e["observation"]["result"] for e in state.observations}


def _held(state, operation: str) -> list[str]:
    (entry,) = [e for e in state.observations if e["operation"] == operation]
    return entry["observation"]["assertions"]


def _rows(state, tool: str) -> list[dict[str, Any]]:
    return [row for row in state.results if row["tool"] == tool]


_ALL_PASSED = {
    "lpar.modify": "passed",
    "lpar.dlpar_mem": "passed",
    "lpar.dlpar_proc": "passed",
    "lpar.rename": "passed",
    "boot_order.set": "passed",
    "boot_order.clear": "passed",
}


@pytest.mark.asyncio
async def test_clean_run_passes_every_observation(monkeypatch):
    hmc = FakeHMC()
    state = await _run(monkeypatch, hmc)

    assert _results(state) == _ALL_PASSED
    assert {e["observation"]["cleanup"] for e in state.observations} == {"passed"}
    assert hmc.deleted
    assert hmc.name.startswith("hmcpctl-live-lpar-")
    assert hmc.token == "lparcfg-" + hmc.name.removeprefix("hmcpctl-live-lpar-")


@pytest.mark.asyncio
async def test_outside_its_group_it_calls_nothing(monkeypatch):
    hmc = FakeHMC()
    state = await _run(monkeypatch, hmc, group="round2")

    assert hmc.calls == []
    assert state.results[-1]["status"] == "SKIP"
    assert state.observations == []


@pytest.mark.asyncio
async def test_existing_scratch_partition_blocks_the_run(monkeypatch):
    hmc = FakeHMC(existing_scratch=True)
    state = await _run(monkeypatch, hmc)

    assert not any(tool == "hmc_create_lpar" for tool, _ in hmc.calls)
    assert "live_test_recovery.py" in state.results[-1]["note"]


@pytest.mark.asyncio
async def test_every_mutation_targets_the_scratch_partition(monkeypatch):
    hmc = FakeHMC()
    await _run(monkeypatch, hmc)

    mutations = [kwargs for tool, kwargs in hmc.calls if tool in MUTATIONS]
    assert mutations
    assert {kwargs["lpar_name_or_uuid"] for kwargs in mutations} == {SCRATCH_UUID}


@pytest.mark.asyncio
async def test_unapplied_memory_change_fails_dlpar_mem(monkeypatch):
    state = await _run(monkeypatch, FakeHMC(ignore_memory_write=True))

    assert _results(state)["lpar.dlpar_mem"] == "failed"
    assert "small-change-read-back" not in _held(state, "lpar.dlpar_mem")
    assert "empty-request-refused" in _held(state, "lpar.dlpar_mem")


@pytest.mark.asyncio
async def test_modify_refusal_is_a_failed_observation(monkeypatch):
    state = await _run(monkeypatch, FakeHMC(modify_406=True))

    assert _results(state)["lpar.modify"] == "failed"
    assert state.gaps == []
    assert _rows(state, "hmc_modify_lpar (memory and units)")[0]["status"] == "FAIL"


@pytest.mark.asyncio
async def test_foreign_token_is_not_deleted(monkeypatch):
    hmc = FakeHMC(foreign_token=True)
    state = await _run(monkeypatch, hmc)

    assert not any(tool == "hmc_delete_lpar" for tool, _ in hmc.calls)
    assert "MANUAL RECOVERY REQUIRED" in str(_rows(state, "scratch partition teardown"))
    assert "rmsyscfg -r lpar -m sys-A -n" in str(state.results)


@pytest.mark.asyncio
async def test_pool_drift_fails_cleanup(monkeypatch):
    state = await _run(monkeypatch, FakeHMC(pool_drift=True))

    assert {e["observation"]["cleanup"] for e in state.observations} == {"failed"}
    assert set(_results(state).values()) == {"failed"}


@pytest.mark.asyncio
async def test_pool_lag_is_reread(monkeypatch):
    state = await _run(monkeypatch, FakeHMC(pool_lag=True))

    (compare,) = _rows(state, "system baseline compare")
    assert compare["status"] == "PASS"
    assert len(compare["data"]["reads"]) == 2
    assert _results(state) == _ALL_PASSED


@pytest.mark.asyncio
async def test_activated_refusal_is_a_gap_row(monkeypatch):
    state = await _run(monkeypatch, FakeHMC())

    (row,) = _rows(state, "hmc_dlpar_mem (activated)")
    assert row["status"] == "FAIL"
    assert "active RMC connection" in row["note"]
    assert not any(
        e["operation"] == "lpar.dlpar_mem" and "activated" in str(e)
        for e in state.observations
    )


@pytest.mark.asyncio
async def test_activated_acceptance_makes_no_rmc_claim(monkeypatch):
    state = await _run(monkeypatch, FakeHMC(activated_dlpar_refused=False))

    for tool in ("hmc_dlpar_mem (activated)", "hmc_dlpar_proc (activated)"):
        (row,) = _rows(state, tool)
        assert row["status"] == "PASS"
        assert "RMC" not in row["note"]
        assert row["data"]["read_back"]["memory"]
    assert _results(state) == _ALL_PASSED


@pytest.mark.asyncio
async def test_activation_timeout_skips_dlpar(monkeypatch):
    hmc = FakeHMC(never_activates=True)
    state = await _run(monkeypatch, hmc)

    assert _rows(state, "hmc_dlpar_mem (activated)") == []
    assert _rows(state, "activated DLPAR")[0]["status"] == "SKIP"
    assert hmc.deleted


@pytest.mark.asyncio
async def test_create_without_uuid_is_found_by_token(monkeypatch):
    hmc = FakeHMC(create_without_uuid=True)
    state = await _run(monkeypatch, hmc)

    assert _results(state) == _ALL_PASSED
    assert hmc.deleted


@pytest.mark.asyncio
async def test_power_off_refused_leaves_recovery_row(monkeypatch):
    hmc = FakeHMC(power_off_refused=True)
    state = await _run(monkeypatch, hmc)

    assert not hmc.deleted
    (row,) = _rows(state, "scratch partition teardown")
    assert "did not reach Not Activated" in row["data"]
    assert "chsysstate -m sys-A -r lpar -n" in row["data"]
    assert {e["observation"]["cleanup"] for e in state.observations} == {"failed"}


@pytest.mark.asyncio
async def test_absence_rereads_once(monkeypatch):
    hmc = FakeHMC(stale_absence=True)
    state = await _run(monkeypatch, hmc)

    assert hmc.stale_served
    assert "old-name-absent" in _held(state, "lpar.rename")
    assert _results(state) == _ALL_PASSED


def test_scratch_partitions_selects_only_the_reserved_prefix():
    names = [
        "hmcpctl-live-lpar-0a1b2c3d",
        "lpar-A",
        "hmcpctl-live-x",
        "hmcpctl-live-lpar-1-rn",
    ]

    assert lpar_config.scratch_partitions(names) == [
        "hmcpctl-live-lpar-0a1b2c3d",
        "hmcpctl-live-lpar-1-rn",
    ]
