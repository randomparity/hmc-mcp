"""SR-IOV inventory reads admit exactly the captured (HMC level, model) pairs (ADR 0183)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from conftest import live_fixture

from hmcpctl.config import HMCConfig
from hmcpctl.operations.virtualization.pcie import (
    SriovLogicalPortCapabilityError,
    list_sriov_adapters,
    list_sriov_logical_ports,
    list_sriov_physical_ports,
    require_admitted_environment,
    require_sriov_read_environment,
)
from hmcpctl.ssh.commands import _parse_lshwres_output

V10R3 = live_fixture("cli-lshmc-version")["stdout"]
V11R2 = live_fixture("cli-lshmc-version-v11r2")["stdout"]
ALL_READS = ("adapter", "physical_port", "logical_port")
# Each pair is admitted for the reads its capture shows. Both POWER11 systems
# hold only dedicated-mode adapters, so only adapter inventory is captured.
ADMITTED = [
    pytest.param(V10R3, "8375-42A", ALL_READS, id="v10r3-8375-42A"),
    pytest.param(
        V11R2,
        live_fixture("cli-type-model-v11r2-p9-9009-42a")["stdout"].strip(),
        ALL_READS,
        id="v11r2-9009-42A",
    ),
    pytest.param(
        V11R2,
        live_fixture("cli-type-model-v11r2-p11-9824-42a")["stdout"].strip(),
        ("adapter",),
        id="v11r2-9824-42A",
    ),
    pytest.param(
        V11R2,
        live_fixture("cli-type-model-v11r2-p11-9242-21b")["stdout"].strip(),
        ("adapter",),
        id="v11r2-9242-21B",
    ),
]


async def _run(gate, version: str, model: str, *args: str) -> None:
    config = HMCConfig.from_mapping({"host": "h", "user": "u", "password": "p"})
    with patch(
        "hmcpctl.operations.virtualization.pcie.read_sriov_environment",
        AsyncMock(return_value=(version, model)),
    ):
        await gate(config, "sys", *args)


@pytest.mark.asyncio
@pytest.mark.parametrize(("version", "model", "reads"), ADMITTED)
async def test_each_captured_pair_admits_exactly_its_captured_reads(
    version: str, model: str, reads: tuple[str, ...]
) -> None:
    for read in ALL_READS:
        if read in reads:
            await _run(require_sriov_read_environment, version, model, read)
        else:
            with pytest.raises(SriovLogicalPortCapabilityError, match="ADR 0183"):
                await _run(require_sriov_read_environment, version, model, read)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version", "model"),
    [
        pytest.param(V11R2, "8375-42A", id="v11r2-uncaptured-model"),
        pytest.param(V10R3, "9009-42A", id="v10r3-uncaptured-model"),
        pytest.param(
            "Version: 11\nRelease: 2\nService Pack: 1130\n",
            "9009-42A",
            id="uncaptured-service-pack",
        ),
    ],
)
async def test_an_uncaptured_pair_is_refused_for_every_read(
    version: str, model: str
) -> None:
    for read in ALL_READS:
        with pytest.raises(SriovLogicalPortCapabilityError, match="ADR 0183"):
            await _run(require_sriov_read_environment, version, model, read)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model", ["9009-42A", "9824-42A", "9242-21B"], ids=lambda model: model
)
async def test_mutations_stay_on_the_v10r3_8375_envelope(model: str) -> None:
    # No V11R2 capture holds an SR-IOV mutation, so ADR 0183 widens reads only.
    with pytest.raises(SriovLogicalPortCapabilityError, match="8375-42A"):
        await _run(require_admitted_environment, V11R2, model)


@pytest.mark.asyncio
async def test_inventory_answers_per_admitted_read_on_a_power11_pair() -> None:
    hmc = SimpleNamespace(
        config=HMCConfig.from_mapping({"host": "h", "user": "u", "password": "p"})
    )
    environment = AsyncMock(
        return_value=(
            V11R2,
            live_fixture("cli-type-model-v11r2-p11-9242-21b")["stdout"].strip(),
        )
    )
    pcie = "hmcpctl.operations.virtualization.pcie"
    with (
        patch(f"{pcie}._system_name", AsyncMock(return_value="sys")),
        patch(f"{pcie}.read_sriov_environment", environment),
        patch(f"{pcie}.list_sriov_adapter_rows", AsyncMock(return_value=[])),
    ):
        adapters = await list_sriov_adapters(hmc, "sys")
        ports = await list_sriov_physical_ports(hmc, "sys", "1")
        logical = await list_sriov_logical_ports(hmc, "sys", "1")

    assert adapters.capability == "available"
    for result in (ports, logical):
        assert result.capability == "capability-unavailable"
        assert "no SR-IOV-mode adapter captured on this model" in (
            result.unavailable_reason or ""
        )


def test_the_captures_show_the_evidence_adr_0183_rests_on() -> None:
    def rows(name: str) -> list[dict[str, str]]:
        return _parse_lshwres_output(live_fixture(name)["stdout"])

    # The 9009-42A adapter lists ports at two levels, with disjoint port IDs.
    levels = {
        level: [
            (row["adapter_id"], row["phys_port_id"], row["phys_port_type"])
            for row in rows(f"cli-sriov-physport-{level}-default-v11r2-p9")
        ]
        for level in ("roce", "ethc", "eth")
    }
    assert levels == {
        "roce": [],
        "ethc": [("2", "0", "ethc"), ("2", "1", "ethc")],
        "eth": [("2", "2", "eth"), ("2", "3", "eth")],
    }
    assert [
        row["config_state"] for row in rows("cli-sriov-adapter-default-v11r2-p9")
    ] == [
        "sriov",
        "dedicated",
    ]
    # Neither POWER11 system holds an SR-IOV-mode adapter.
    for model in ("9824-42a", "9242-21b"):
        adapters = rows(f"cli-sriov-adapter-default-v11r2-p11-{model}")
        assert adapters
        assert {(row["adapter_id"], row["config_state"]) for row in adapters} == {
            ("null", "dedicated")
        }


def _replay(*names: str) -> AsyncMock:
    """Answer each captured command with the stdout the HMC printed for it."""
    answers = {
        live_fixture(name)["command"]: live_fixture(name)["stdout"] for name in names
    }

    async def run(_config: HMCConfig, command: str) -> str:
        return answers[command]

    return AsyncMock(side_effect=run)


@pytest.mark.asyncio
async def test_v11r2_9009_42a_inventory_replays_the_captured_projections() -> None:
    hmc = SimpleNamespace(
        config=HMCConfig.from_mapping({"host": "h", "user": "u", "password": "p"})
    )
    environment = AsyncMock(
        return_value=(
            V11R2,
            live_fixture("cli-type-model-v11r2-p9-9009-42a")["stdout"].strip(),
        )
    )
    run = _replay(
        "cli-sriov-adapters-v11r2-p9",
        "cli-sriov-physport-roce-v11r2-p9",
        "cli-sriov-physport-ethc-v11r2-p9",
        "cli-sriov-physport-eth-v11r2-p9",
        "cli-sriov-logport-eth-v11r2-p9",
        "cli-sriov-logport-default-v11r2-p9",
    )
    pcie = "hmcpctl.operations.virtualization.pcie"
    with (
        patch(f"{pcie}._system_name", AsyncMock(return_value="sys-2")),
        patch(f"{pcie}.read_sriov_environment", environment),
        patch("hmcpctl.ssh.sriov.run_hmc_command", run),
    ):
        adapters = await list_sriov_adapters(hmc, "sys-2")
        ports = await list_sriov_physical_ports(hmc, "sys-2", "2")
        logical = await list_sriov_logical_ports(hmc, "sys-2", "2")

    assert [(item.adapter_id, item.mode) for item in adapters.items] == [
        ("2", "sriov"),
        (None, "dedicated"),
    ]
    assert [
        (
            port.physical_port_id,
            port.availability,
            port.minimum_capacity_granularity_percent,
        )
        for port in ports.items
    ] == [
        ("0", "up", Decimal("2.0")),
        ("1", "up", Decimal("2.0")),
        ("2", "down", Decimal("2.0")),
        ("3", "down", Decimal("2.0")),
    ]
    configured = [item for item in logical.items if item.availability != "unconfigured"]
    assert [(item.logical_port_id, item.physical_port_id) for item in configured] == [
        ("27008001", "0"),
        ("27008002", "0"),
        ("27008006", "0"),
        ("27008003", "1"),
        ("27008004", "2"),
    ]
    # Every unconfigured port's location joins to the T1 port, physical port 0.
    unconfigured = [
        item for item in logical.items if item.availability == "unconfigured"
    ]
    assert len(unconfigured) == 43
    assert {item.physical_port_id for item in unconfigured} == {"0"}
