"""ST42 guard and recovery behavior against an offline VIOS model."""

from __future__ import annotations

import shlex
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import live_test_runner as runner
from live_test import storage_volume_group as vg

PV = "hdisk9"
PVID = "0000000000000009"
GROUP = "hpvg1234abcd"
BEFORE = {PV: (PVID, "None"), "hdisk0": ("0000000000000000", "rootvg")}
FREE = {PV: (PVID, "100000")}
GROUPS = frozenset({"rootvg"})


@pytest.mark.parametrize(
    "text,free",
    [
        (None, False),
        ("error: command refused", False),
        ("hdisk9:x:None", False),
        (f"{PV}:{PVID}:None\n{PV}:{PVID}:None", False),
        (f"{PV}:{PVID}:vg;exit", False),
        (f"{PV}:{PVID}:0", True),
        (f"{PV}:{PVID}:-1", True),
        (f"{PV}:{PVID}:N/A", True),
    ],
)
def test_unreadable_inventory_stops(text, free):
    assert vg.physical_volumes(text, free=free) is None


def test_delimited_inventory_preserves_identity():
    assert vg.physical_volumes(f"{PV}:{PVID}:None\n") == {PV: (PVID, "None")}
    assert vg.physical_volumes(f"{PV}:{PVID}:100000\n", free=True) == FREE
    assert vg.create_guard(PV, GROUP, BEFORE, FREE, GROUPS)


@pytest.mark.parametrize(
    "pv,group,inventory,free,groups",
    [
        ("", GROUP, BEFORE, FREE, GROUPS),
        (PV, "", BEFORE, FREE, GROUPS),
        ("hdisk8", GROUP, BEFORE, FREE, GROUPS),
        (PV, "rootvg", BEFORE, FREE, GROUPS),
        (PV, GROUP, BEFORE, {}, GROUPS),
        (PV, GROUP, BEFORE, None, GROUPS),
        (PV, GROUP, {PV: (PVID, "datavg")}, FREE, GROUPS),
        (PV, GROUP, BEFORE, {PV: ("0000000000000099", "100000")}, GROUPS),
        (PV, GROUP, BEFORE, FREE, GROUPS | {GROUP}),
        (PV, GROUP, None, FREE, GROUPS),
        (PV, GROUP, BEFORE, FREE, None),
        ("hdisk9;exit", GROUP, BEFORE, FREE, GROUPS),
    ],
)
def test_create_guard_refuses_unknown_or_unsafe_selection(
    pv, group, inventory, free, groups
):
    assert not vg.create_guard(pv, group, inventory, free, groups)


@pytest.mark.parametrize(
    "group,current,members,volumes",
    [
        ("rootvg", dict(BEFORE, hdisk9=(PVID, GROUP)), {PV}, set()),
        (GROUP, dict(BEFORE, hdisk9=("none", GROUP)), {PV}, set()),
        (GROUP, dict(BEFORE, hdisk9=(PVID, GROUP)), {PV, "hdisk8"}, set()),
        (GROUP, dict(BEFORE, hdisk9=(PVID, GROUP)), {PV}, {"op-disk"}),
        (GROUP, None, {PV}, set()),
        (GROUP, {PV: (PVID, GROUP)}, {PV}, set()),
    ],
)
def test_cleanup_guard_refuses_destructive_or_unreadable_state(
    group, current, members, volumes
):
    assert not vg.cleanup_guard(
        PV, group, BEFORE, current, frozenset(members), frozenset(volumes)
    )


class FakeVIOS:
    def __init__(self):
        self.inventory = dict(BEFORE)
        self.free = dict(FREE)
        self.groups = {"rootvg"}
        self.calls = []
        self.create_status = "PASS"
        self.create_effect = True
        self.cleanup_effect = True
        self.post_readable = True
        self.extra_member = False
        self.lv = False
        self.pvid_lost = False
        self.size_drift = False

    async def call(self, _client, tool, **kwargs):
        self.calls.append((tool, kwargs))
        created = any(name == "hmc_create_volume_group" for name, _ in self.calls)
        if tool == "hmc_list_volume_groups":
            if created and not self.post_readable:
                return "FAIL", None
            return "PASS", [
                {"name": name, "uuid": "vg-uuid"} for name in sorted(self.groups)
            ]
        if tool == "hmc_create_volume_group":
            assert kwargs["physical_volumes"] == [PV]
            assert kwargs["name"] == GROUP
            assert kwargs["vios_name_or_uuid"] == "vios-A"
            assert kwargs["system_name_or_uuid"] == "sys-A"
            if self.create_effect:
                self.groups.add(GROUP)
                self.free.pop(PV)
                self.inventory[PV] = ("none" if self.pvid_lost else PVID, GROUP)
            return self.create_status, {}
        assert tool == "hmc_run_command"
        cmd = shlex.split(kwargs["cmd"])[-1]
        if cmd == "lspv -field pvname pvid vgname -fmt :":
            return "PASS", "".join(
                f"{name}:{pvid}:{group}\n"
                for name, (pvid, group) in sorted(self.inventory.items())
            )
        if cmd == "lspv -free -field pvname pvid size -fmt :":
            return "PASS", "".join(
                f"{name}:{pvid}:{size}\n"
                for name, (pvid, size) in sorted(self.free.items())
            )
        if cmd == "lsvg":
            return "PASS", "".join(f"{name}\n" for name in sorted(self.groups))
        if cmd == f"lsvg -pv -field pvname -fmt : {GROUP}":
            return "PASS", PV + ("\nhdisk8" if self.extra_member else "") + "\n"
        if cmd == f"lsvg -lv {GROUP}":
            return (
                "PASS",
                f"{GROUP}:\nLV NAME TYPE LPs PPs PVs LV STATE MOUNT POINT\n"
                + ("op-disk jfs2 1 1 1 open/syncd N/A\n" if self.lv else ""),
            )
        assert cmd == f"reducevg {GROUP} {PV}"
        if self.cleanup_effect:
            self.groups.remove(GROUP)
            self.inventory[PV] = (PVID, "None")
            self.free[PV] = (PVID, "99999" if self.size_drift else "100000")
            return "PASS", "Volume group removed"
        return "FAIL", "cleanup refused"


@pytest.fixture
def scratch(monkeypatch):
    config = replace(
        runner.LiveTestConfig(),
        system_name="sys-A",
        scratch_pv_name=PV,
        scratch_vg_name=GROUP,
    )
    state = runner.RunState(config=config, group="storage")
    state.artifacts.vios_uuid = "vios-A"
    state.artifacts.vios_partition_id = 1
    fake = FakeVIOS()
    monkeypatch.setattr(runner.RunState, "call", fake.call)
    return state, fake


def mutations(fake):
    return [
        (tool, args)
        for tool, args in fake.calls
        if tool == "hmc_create_volume_group" or "reducevg " in str(args.get("cmd", ""))
    ]


def observation(state):
    assert len(state.observations) == 1
    return state.observations[0]["observation"]


@pytest.mark.asyncio
async def test_roundtrip_creates_once_and_restores_snapshot(scratch):
    state, fake = scratch
    await vg.exercise_volume_group(None, state)
    assert fake.inventory == BEFORE and fake.free == FREE and fake.groups == set(GROUPS)
    assert len(mutations(fake)) == 2
    assert state.artifacts.storage_volume_group_name is None
    assert observation(state)["result"] == "passed"
    assert observation(state)["cleanup"] == "passed"


@pytest.mark.parametrize(
    "fields",
    [
        {"scratch_pv_name": ""},
        {"scratch_vg_name": ""},
        {"scratch_pv_name": "hdisk8"},
        {"scratch_vg_name": "rootvg"},
        {"scratch_pv_name": "hdisk9;exit"},
        {"scratch_vg_name": "hpvgABCDef12"},
        {"system_name": "-system"},
    ],
)
@pytest.mark.asyncio
async def test_bad_configuration_never_mutates(scratch, fields):
    state, fake = scratch
    state.config = replace(state.config, **fields)
    await vg.exercise_volume_group(None, state)
    assert not mutations(fake) and not state.observations


@pytest.mark.parametrize(
    "case",
    [
        "nonfree",
        "ingroup",
        "collision",
        "pvidmismatch",
        "noidentity",
        "oldartifact",
        "wrongarm",
    ],
)
@pytest.mark.asyncio
async def test_preconditions_never_mutate(scratch, case):
    state, fake = scratch
    if case == "nonfree":
        fake.free.clear()
    if case == "ingroup":
        fake.inventory[PV] = (PVID, "datavg")
    if case == "collision":
        fake.groups.add(GROUP)
    if case == "pvidmismatch":
        fake.free[PV] = ("0000000000000099", "100000")
    if case == "noidentity":
        state.artifacts.vios_partition_id = None
    if case == "oldartifact":
        state.artifacts.storage_volume_group_name = GROUP
    if case == "wrongarm":
        state.group = "all"
    await vg.exercise_volume_group(None, state)
    assert not mutations(fake) and not state.observations


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.asyncio
async def test_refused_create_readback_prevents_retry(scratch, partial):
    state, fake = scratch
    fake.create_status = "FAIL"
    fake.create_effect = partial
    await vg.exercise_volume_group(None, state)
    assert sum(tool == "hmc_create_volume_group" for tool, _ in fake.calls) == 1
    assert len(mutations(fake)) == (2 if partial else 1)
    assert observation(state)["result"] == "failed"
    assert observation(state)["cleanup"] == "passed"
    assert state.artifacts.storage_volume_group_name is None


@pytest.mark.parametrize("fault", ["post_readable", "extra_member", "lv", "pvid_lost"])
@pytest.mark.asyncio
async def test_unsafe_poststate_never_cleans_or_retries(scratch, fault):
    state, fake = scratch
    setattr(fake, fault, fault != "post_readable")
    await vg.exercise_volume_group(None, state)
    assert len(mutations(fake)) == 1
    assert state.artifacts.storage_volume_group_name == GROUP
    assert observation(state)["cleanup"] == "failed"
    assert any("MANUAL RECOVERY REQUIRED" in row["note"] for row in state.results)


@pytest.mark.parametrize("fault", ["cleanup_effect", "size_drift"])
@pytest.mark.asyncio
async def test_unconfirmed_restoration_keeps_artifact(scratch, fault):
    state, fake = scratch
    setattr(fake, fault, fault != "cleanup_effect")
    await vg.exercise_volume_group(None, state)
    assert len(mutations(fake)) == 2
    assert state.artifacts.storage_volume_group_name == GROUP
    assert observation(state)["cleanup"] == "failed"


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        [{}],
        [{"name": "vg;exit"}],
        [{"name": "datavg"}, {"name": "datavg"}],
        ["datavg"],
    ],
)
def test_rest_listing_refuses_filtered_or_ambiguous_data(data):
    assert vg.rest_group_names(data) is None


def test_rest_listing_accepts_both_normalized_and_resource_shapes():
    assert vg.rest_group_names(
        [{"name": "rootvg"}, {"Resource": {"GroupName": "datavg"}}]
    ) == frozenset({"rootvg", "datavg"})


def test_unidentified_physical_volume_is_never_created():
    assert not vg.create_guard(
        PV, GROUP, {PV: ("none", "None")}, {PV: ("none", "100000")}, GROUPS
    )


def test_optin_registry_and_legacy_documents():
    import json
    from dataclasses import asdict

    assert runner.SUBTASKS[42] is vg.exercise_volume_group
    assert runner.SUBTASK_GROUPS["storage"] == [0, 3, 40, 42]
    assert 42 not in runner.SUBTASK_GROUPS["all"]
    assert 42 not in runner.SUBTASK_GROUPS["round2"]
    old_config = json.loads(json.dumps(asdict(runner.LiveTestConfig())))
    old_config.pop("scratch_pv_name")
    old_config.pop("scratch_vg_name")
    assert runner._decode_saved_config(old_config) == runner.LiveTestConfig()
    artifacts = asdict(runner.LiveTestArtifacts(storage_volume_group_name=GROUP))
    assert runner._decode_artifacts(artifacts).storage_volume_group_name == GROUP
    artifacts.pop("storage_volume_group_name")
    assert runner._decode_artifacts(artifacts).storage_volume_group_name is None


@pytest.mark.parametrize("kind", ["config", "artifact"])
def test_new_saved_fields_remain_strict(kind):
    import json
    from dataclasses import asdict

    data = (
        json.loads(json.dumps(asdict(runner.LiveTestConfig())))
        if kind == "config"
        else asdict(runner.LiveTestArtifacts())
    )
    decode = (
        runner._decode_saved_config if kind == "config" else runner._decode_artifacts
    )
    field = "scratch_pv_name" if kind == "config" else "storage_volume_group_name"
    data[field] = 123
    with pytest.raises(TypeError):
        decode(data)
    data.pop(field)
    data["unknown_field"] = "unexpected"
    with pytest.raises(ValueError):
        decode(data)
