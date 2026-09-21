"""Tests for the shared PCIe assignment JSON boundary."""

from __future__ import annotations

import json

import pytest
import typer

from hmc_mcp.cli_commands.lpar.assignment_input import load_pcie_assignments


def test_missing_path_returns_empty_assignments() -> None:
    result = load_pcie_assignments(None)
    assert result.dedicated == ()
    assert result.sriov == ()
    assert result.vnics == ()


def test_valid_json_is_deserialized(tmp_path) -> None:
    path = tmp_path / "assignments.json"
    path.write_text(json.dumps({"dedicated": [{"profile_name": "p", "drc_index": "1"}]}))

    result = load_pcie_assignments(path)

    assert result.dedicated[0].profile_name == "p"
    assert result.dedicated[0].drc_index == "1"


@pytest.mark.parametrize(
    "content",
    ["not json", json.dumps({"sriov": [{"profile_name": "missing fields"}]})],
)
def test_invalid_json_or_schema_exits_with_usage_error(tmp_path, content: str) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(content)

    with pytest.raises(typer.Exit) as error:
        load_pcie_assignments(path)

    assert error.value.exit_code == 2


def test_missing_file_exits_with_usage_error(tmp_path) -> None:
    with pytest.raises(typer.Exit) as error:
        load_pcie_assignments(tmp_path / "missing.json")

    assert error.value.exit_code == 2
