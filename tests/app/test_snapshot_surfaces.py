from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from hmc_mcp import cli
from hmc_mcp.operations.affinity import (
    AffinityAssessmentInput,
    assess_affinity,
)
from datetime import UTC, datetime
from hmc_mcp.cli_commands.snapshot import _publish
from hmc_mcp.server import TOOL_SECURITY
from hmc_mcp.server_tools.snapshot import hmc_snapshot_inspect
from hmc_mcp.server_tools.snapshot import hmc_snapshot_assess_affinity

RUNNER = CliRunner()


def test_snapshot_tools_have_read_only_security_contracts() -> None:
    assert TOOL_SECURITY["hmc_snapshot_capture"].effect == "read"
    assert TOOL_SECURITY["hmc_snapshot_validate"].effect == "read"
    assert TOOL_SECURITY["hmc_snapshot_inspect"].effect == "read"
    assert TOOL_SECURITY["hmc_snapshot_assess_affinity"].effect == "read"
    assert TOOL_SECURITY["hmc_snapshot_capture"].target_kind == "lpar"
    assert TOOL_SECURITY["hmc_snapshot_validate"].connection_argument is None
    assert TOOL_SECURITY["hmc_snapshot_assess_affinity"].connection_argument is None


def test_mcp_inspect_accepts_newer_version_without_validation() -> None:
    assert hmc_snapshot_inspect('{"format":"hmc-mcp.lpar-snapshot","version":2}') == {
        "format": "hmc-mcp.lpar-snapshot",
        "version": 2,
        "supported": False,
    }


def test_mcp_affinity_assessment_delegates_and_serializes(monkeypatch) -> None:
    expected = assess_affinity(
        AffinityAssessmentInput(
            captured_score=90,
            current_score=90,
            predicted_score=94,
            policy_state="absent",
            captured_policy_state="absent",
            configured_minimum=None,
            captured_minimum=None,
            captured_at=datetime(2026, 8, 24, 20, tzinfo=UTC),
            assessed_at=datetime(2026, 8, 24, 21, tzinfo=UTC),
            stale_after_seconds=7200,
            regression_threshold=5,
            optimization_threshold=5,
        )
    )

    async def fake_assessment(*args, **kwargs):
        return expected

    monkeypatch.setattr(
        "hmc_mcp.server_tools.snapshot.assess_snapshot_affinity", fake_assessment
    )
    result = hmc_snapshot_assess_affinity(
        "{}", 90, 94, regression_threshold=5, optimization_threshold=5
    )

    assert result["classification"] == "none"
    assert result["evidence"]["captured_score"] == 90


def test_cli_snapshot_group_has_no_replay_command() -> None:
    result = RUNNER.invoke(cli.app, ["snapshot", "--help"])
    assert result.exit_code == 0
    assert "capture" in result.stdout
    assert "validate" in result.stdout
    assert "inspect" in result.stdout
    assert "assess-affinity" in result.stdout
    assert "replay" not in result.stdout


def test_cli_affinity_assessment_prints_shared_result(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text("{}", encoding="utf-8")
    result_value = assess_affinity(
        AffinityAssessmentInput(
            captured_score=90,
            current_score=90,
            predicted_score=94,
            policy_state="absent",
            captured_policy_state="absent",
            configured_minimum=None,
            captured_minimum=None,
            captured_at=datetime(2026, 8, 24, 20, tzinfo=UTC),
            assessed_at=datetime(2026, 8, 24, 21, tzinfo=UTC),
            stale_after_seconds=7200,
            regression_threshold=5,
            optimization_threshold=5,
        )
    )

    async def fake_assessment(*args, **kwargs):
        return result_value

    monkeypatch.setattr(
        "hmc_mcp.cli_commands.snapshot.assess_snapshot_affinity", fake_assessment
    )
    result = RUNNER.invoke(
        cli.app,
        [
            "snapshot",
            "assess-affinity",
            str(path),
            "--current-score",
            "90",
            "--predicted-score",
            "94",
            "--regression-threshold",
            "5",
            "--optimization-threshold",
            "5",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["classification"] == "none"


def test_publish_refuses_existing_destination(tmp_path: Path) -> None:
    destination = tmp_path / "snapshot.json"
    destination.write_text("original", encoding="utf-8")
    try:
        _publish(destination, json.dumps({"new": True}))
    except FileExistsError:
        pass
    else:
        raise AssertionError("existing destination was replaced")
    assert destination.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.glob(".hmc-mcp-snapshot-*")) == []


def test_cli_inspect_reads_local_file(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text('{"format":"hmc-mcp.lpar-snapshot","version":2}', encoding="utf-8")
    result = RUNNER.invoke(cli.app, ["snapshot", "inspect", str(path)])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["supported"] is False


@pytest.mark.parametrize("command", ["inspect", "validate"])
def test_cli_snapshot_read_failures_are_concise(tmp_path: Path, command: str) -> None:
    missing = tmp_path / "missing.json"
    result = RUNNER.invoke(cli.app, ["snapshot", command, str(missing)])
    assert result.exit_code == 1
    assert result.exception is not None
    assert "Error: snapshot read failed" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_validate_malformed_snapshot_is_concise(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text("{", encoding="utf-8")
    result = RUNNER.invoke(cli.app, ["snapshot", "validate", str(path)])
    assert result.exit_code == 1
    assert "Error: snapshot read failed" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_inspect_oversized_snapshot_is_concise(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text("x" * 1_048_577, encoding="utf-8")
    result = RUNNER.invoke(cli.app, ["snapshot", "inspect", str(path)])
    assert result.exit_code == 1
    assert "Error: snapshot read failed" in result.stderr
    assert "1 MiB" in result.stderr


def test_cli_capture_existing_destination_is_concise(
    tmp_path: Path, monkeypatch
) -> None:
    destination = tmp_path / "snapshot.json"
    destination.write_text("original", encoding="utf-8")
    monkeypatch.setattr(
        "hmc_mcp.cli_commands.snapshot._run",
        lambda operation: SimpleNamespace(format="hmc-mcp.lpar-snapshot", version=1),
    )
    monkeypatch.setattr("hmc_mcp.cli_commands.snapshot.serialize_snapshot", lambda value: "{}")
    result = RUNNER.invoke(
        cli.app,
        ["snapshot", "capture", "sys", "aix", "default", "--output", str(destination)],
    )
    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert "Traceback" not in result.stderr
    assert destination.read_text(encoding="utf-8") == "original"
