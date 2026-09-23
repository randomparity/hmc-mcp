"""Both PCIe admission gates admit exactly ADR 0165's envelope, by the same fields."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from hmcpctl.config import HMCConfig
from hmcpctl.operations.virtualization.pcie import (
    PcieAssignmentUnavailableError,
    SriovLogicalPortCapabilityError,
    require_admitted_environment,
    require_dedicated_pcie_environment,
)

_CAPTURE = Path(__file__).parents[1] / "fixtures" / "pcie" / "power9-v10r3m1060-live-ioslots.json"
_MODEL = "8375-42A"
_LATER_SERVICE_PACK = (
    "version= Version: 10\n Release: 3\n Service Pack: 1070\n"
    "HMC Build level 2503010000\nMF71689 - HMC V10R3 M1060\nMF72001 - HMC V10R3 M1070\n"
)
_GATES = [
    pytest.param(require_admitted_environment, SriovLogicalPortCapabilityError, id="sriov"),
    pytest.param(
        require_dedicated_pcie_environment, PcieAssignmentUnavailableError, id="dedicated"
    ),
]


def _captured_version() -> str:
    record = json.loads(_CAPTURE.read_text())
    return {probe["name"]: probe for probe in record["probes"]}["hmc-version"]["stdout"]


async def _run(gate, version: str, model: str = _MODEL) -> None:
    config = HMCConfig.from_mapping({"host": "h", "user": "u", "password": "p"})
    with patch(
        "hmcpctl.operations.virtualization.pcie.read_sriov_environment",
        AsyncMock(return_value=(version, model)),
    ):
        await gate(config, "sys")


@pytest.mark.asyncio
@pytest.mark.parametrize(("gate", "error"), _GATES)
async def test_the_captured_v10r3_m1060_output_is_admitted(gate, error) -> None:
    await _run(gate, _captured_version())


@pytest.mark.asyncio
@pytest.mark.parametrize(("gate", "error"), _GATES)
@pytest.mark.parametrize(
    "version",
    [
        pytest.param(_LATER_SERVICE_PACK, id="later-sp-listing-an-m1060-fix"),
        pytest.param("Version: 10\nRelease: 3\nService Pack: 10600\n", id="sp-10600"),
        pytest.param("Version: 100\nRelease: 3\nService Pack: 1060\n", id="version-100"),
        pytest.param("Version: 10\nRelease: 30\nService Pack: 1060\n", id="release-30"),
        pytest.param("V10R3 M1060 build 2408210051\n", id="release-string-only"),
        pytest.param(
            "Version: 10\nVersion: 10\nRelease: 3\nService Pack: 1060\n",
            id="repeated-field",
        ),
    ],
)
async def test_anything_but_the_exact_fields_is_refused(gate, error, version: str) -> None:
    with pytest.raises(error, match="V10R3 M1060"):
        await _run(gate, version)


@pytest.mark.asyncio
@pytest.mark.parametrize(("gate", "error"), _GATES)
async def test_the_admitted_release_on_another_model_is_refused(gate, error) -> None:
    with pytest.raises(error, match="8375-42A"):
        await _run(gate, _captured_version(), "9009-42A")
