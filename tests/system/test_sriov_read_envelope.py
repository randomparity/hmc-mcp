"""SR-IOV inventory reads admit exactly the captured (HMC level, model) pairs (ADR 0183)."""

from __future__ import annotations

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
        assert "9009-42A" in result.unavailable_reason
        assert "9242-21B" not in result.unavailable_reason
