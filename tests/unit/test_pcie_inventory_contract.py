"""Stable Python, MCP, and CLI contract tests for normalized PCIe inventory."""

from __future__ import annotations

import json
from dataclasses import asdict, fields
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from hmc_mcp import api
from hmc_mcp.cli import app
from hmc_mcp.config import HMCConfig
from hmc_mcp.operations.io_virtualization.pcie import (
    DedicatedSlot,
    InventoryResult,
    InventorySelector,
    SriovAdapter,
    SriovLogicalPort,
    SriovPhysicalPort,
)
from hmc_mcp.server_tools.system_resources import (
    hmc_list_dedicated_pcie_slots,
    hmc_list_sriov_adapters,
    hmc_list_sriov_logical_ports,
    hmc_list_sriov_physical_ports,
    tool_security,
)


def _config() -> HMCConfig:
    return HMCConfig.from_mapping(
        {
            "host": "hmc.test",
            "user": "hscroot",
            "password": "test",  # pragma: allowlist secret
        }
    )


def test_model_fields_are_the_documented_stable_schema() -> None:
    assert [field.name for field in fields(InventoryResult)] == [
        "resource_kind",
        "capability",
        "system",
        "selector",
        "items",
        "unavailable_reason",
    ]
    assert [field.name for field in fields(InventorySelector)] == [
        "adapter_id",
        "physical_port_id",
        "logical_port_id",
    ]
    assert [field.name for field in fields(DedicatedSlot)] == [
        "system",
        "drc_index",
        "description",
        "owner_lpar",
        "availability",
    ]
    assert [field.name for field in fields(SriovAdapter)] == [
        "system",
        "adapter_id",
        "mode",
        "availability",
        "location_code",
        "owner_lpar",
        "logical_ports_in_use",
        "logical_ports_available",
    ]
    assert [field.name for field in fields(SriovPhysicalPort)] == [
        "system",
        "adapter_id",
        "physical_port_id",
        "availability",
        "location_code",
        "owner_lpar",
        "minimum_capacity_granularity_percent",
        "logical_ports_in_use",
        "logical_ports_available",
    ]
    assert [field.name for field in fields(SriovLogicalPort)] == [
        "system",
        "adapter_id",
        "physical_port_id",
        "logical_port_id",
        "availability",
        "owner_lpar",
        "owner_lpar_id",
        "capacity_percent",
        "maximum_capacity_percent",
        "compatibility",
    ]


def test_models_preserve_hierarchy_percentage_units_and_explicit_unknowns() -> None:
    physical = SriovPhysicalPort(
        "sys1", "a1", "p2", None, None, None, Decimal("0.25"), None, None
    )
    logical = SriovLogicalPort(
        "sys1",
        "a1",
        "p2",
        "l3",
        None,
        None,
        None,
        Decimal("10.25"),
        Decimal("20.50"),
        None,
    )
    assert physical.minimum_capacity_granularity_percent == Decimal("0.25")
    assert logical.capacity_percent == Decimal("10.25")
    assert logical.maximum_capacity_percent == Decimal("20.50")
    assert logical.availability is logical.compatibility is None
    assert asdict(logical)["physical_port_id"] == "p2"


def test_supported_api_exports_inventory_contract_directly() -> None:
    for name in (
        "DedicatedSlot",
        "InventoryResult",
        "InventorySelector",
        "SriovAdapter",
        "SriovLogicalPort",
        "SriovPhysicalPort",
        "list_dedicated_slots",
        "list_sriov_adapters",
        "list_sriov_logical_ports",
        "list_sriov_physical_ports",
    ):
        assert name in api.__all__
        assert getattr(api, name) is getattr(
            __import__("hmc_mcp.operations.io_virtualization.pcie", fromlist=[name]), name
        )


def test_mcp_tools_have_managed_system_read_metadata() -> None:
    expected = {
        "hmc_list_dedicated_pcie_slots": "pcie.list_dedicated_slots",
        "hmc_list_sriov_adapters": "pcie.list_sriov_adapters",
        "hmc_list_sriov_physical_ports": "pcie.list_sriov_physical_ports",
        "hmc_list_sriov_logical_ports": "pcie.list_sriov_logical_ports",
    }
    for name, operation in expected.items():
        security = tool_security()[name]
        assert security.effect == "read"
        assert security.operation == operation
        assert security.target_kind == "managed_system"


def test_mcp_logical_tool_forwards_every_selector(monkeypatch) -> None:
    monkeypatch.setenv("HMC_HOST", "hmc")
    monkeypatch.setenv("HMC_USER", "user")
    monkeypatch.setenv("HMC_PASSWORD", "password")
    result = InventoryResult(
        "sriov_logical_port",
        "capability-unavailable",
        "sys1",
        InventorySelector("a1", "p2", "l3"),
        [],
        "ADR 0053 admits selectors but no SR-IOV read projection",
    )
    with (
        patch(
            "hmc_mcp._app.build_config",
            return_value=_config(),
        ),
        patch(
            "hmc_mcp.server_tools.system_resources.list_sriov_logical_ports",
            AsyncMock(return_value=result),
        ) as operation,
    ):
        value = hmc_list_sriov_logical_ports("sys1", "a1", "p2", "l3")

    assert value == asdict(result)
    operation.assert_awaited_once()
    assert operation.await_args.args[2:] == ("a1", "p2", "l3")


def test_other_mcp_inventory_tools_return_serialized_results(monkeypatch) -> None:
    monkeypatch.setenv("HMC_HOST", "hmc")
    monkeypatch.setenv("HMC_USER", "user")
    monkeypatch.setenv("HMC_PASSWORD", "password")
    cases = (
        (hmc_list_dedicated_pcie_slots, "list_dedicated_slots", "dedicated_slot"),
        (hmc_list_sriov_adapters, "list_sriov_adapters", "sriov_adapter"),
        (
            hmc_list_sriov_physical_ports,
            "list_sriov_physical_ports",
            "sriov_physical_port",
        ),
    )
    for tool, operation_name, resource_kind in cases:
        result = InventoryResult(
            resource_kind, "available", "sys1", InventorySelector(), [], None
        )
        with (
            patch(
                "hmc_mcp._app.build_config",
                return_value=_config(),
            ),
            patch(
                f"hmc_mcp.server_tools.system_resources.{operation_name}",
                AsyncMock(return_value=result),
            ),
        ):
            assert tool("sys1") == asdict(result)


def test_cli_logical_inventory_forwards_selectors_and_prints_json() -> None:
    result = InventoryResult(
        "sriov_logical_port",
        "capability-unavailable",
        "sys1",
        InventorySelector("a1", "p2", "l3"),
        [],
        "ADR 0053 admits selectors but no SR-IOV read projection",
    )
    with (
        patch("hmc_mcp.cli_commands.pcie.ssh_config", return_value=_config()),
        patch(
            "hmc_mcp.cli_commands.pcie.list_sriov_logical_ports",
            AsyncMock(return_value=result),
        ) as operation,
    ):
        response = CliRunner().invoke(
            app,
            [
                "network",
                "list-sriov-logical-ports",
                "sys1",
                "--adapter-id",
                "a1",
                "--physical-port-id",
                "p2",
                "--logical-port-id",
                "l3",
                "--json",
            ],
        )

    assert response.exit_code == 0, response.output
    assert json.loads(response.stdout) == asdict(result)
    assert operation.await_args.args[2:] == ("a1", "p2", "l3")


def test_cli_text_mode_reports_unavailable_capability() -> None:
    result = InventoryResult(
        "sriov_adapter",
        "capability-unavailable",
        "sys1",
        InventorySelector(),
        [],
        "ADR 0053 admits selectors but no SR-IOV read projection",
    )
    with (
        patch("hmc_mcp.cli_commands.pcie.ssh_config", return_value=_config()),
        patch(
            "hmc_mcp.cli_commands.pcie.list_sriov_adapters",
            AsyncMock(return_value=result),
        ),
    ):
        response = CliRunner().invoke(app, ["network", "list-sriov-adapters", "sys1"])

    assert response.exit_code == 0, response.output
    assert "Capability unavailable" in response.stdout
    assert result.unavailable_reason in response.stdout
    assert not response.stdout.lstrip().startswith("{")


def test_cli_text_mode_distinguishes_available_empty_and_records() -> None:
    empty = InventoryResult(
        "dedicated_slot", "available", "sys1", InventorySelector(), [], None
    )
    item = DedicatedSlot("sys1", "21010003", "PCIe slot", "lpar1", None)
    populated = InventoryResult(
        "dedicated_slot", "available", "sys1", InventorySelector(), [item], None
    )
    operation = AsyncMock(side_effect=[empty, populated])
    with (
        patch("hmc_mcp.cli_commands.pcie.ssh_config", return_value=_config()),
        patch("hmc_mcp.cli_commands.pcie.list_dedicated_slots", operation),
    ):
        empty_response = CliRunner().invoke(
            app, ["network", "list-dedicated-pcie-slots", "sys1"]
        )
        item_response = CliRunner().invoke(
            app, ["network", "list-dedicated-pcie-slots", "sys1"]
        )

    assert empty_response.exit_code == 0
    assert "available; no items found" in empty_response.stdout
    assert item_response.exit_code == 0
    assert "21010003" in item_response.stdout
    assert "lpar1" in item_response.stdout


def test_cli_registers_all_normalized_inventory_commands() -> None:
    response = CliRunner().invoke(app, ["network", "--help"])
    assert response.exit_code == 0
    for name in (
        "list-dedicated-pcie-slots",
        "list-sriov-adapters",
        "list-sriov-physical-ports",
        "list-sriov-logical-ports",
    ):
        assert name in response.stdout
