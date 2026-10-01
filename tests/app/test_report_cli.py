"""CLI contracts for ``hmcpctl report utilization`` (ADR 0184)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hmcpctl import cli
from hmcpctl.cli_commands import report
from hmcpctl.operations.inventory.utilization import (
    CpuFigures,
    FleetSurvey,
    MemoryFigures,
    PartitionFigures,
    ProfileFailure,
    SystemReading,
)

RUNNER = CliRunner()
EXPECTED_COLUMNS = [
    "row_type", "profiles", "system", "machine_type", "model", "serial", "firmware",
    "state", "systems", "cpu_installed", "cpu_configurable",
    "cpu_vios", "cpu_client_active", "cpu_idle_reserved", "cpu_other_reserved",
    "cpu_free", "cpu_dedicated", "cpu_shared", "cpu_allocated", "cpu_util_pct",
    "shared_pools",
    "mem_installed_mib", "mem_configurable_mib", "mem_hypervisor_mib", "mem_vios_mib",
    "mem_client_active_mib", "mem_idle_reserved_mib", "mem_other_reserved_mib",
    "mem_free_mib", "mem_allocated_mib", "mem_util_pct", "partitions_running",
    "partitions_not_activated",
    "partitions_other", "profile_claims", "profile_claim_mem_mib", "profile_claim_cpu",
    "notes",
]  # fmt: skip


def _reading(profile: str, serial: str, *, vios: int | None = 1024) -> SystemReading:
    return SystemReading(
        profile=profile,
        name=f"system-{serial[-1]}",
        machine_type="9080",
        model="HEX",
        serial=serial,
        firmware="FW1120.00 (1)",
        state="operating",
        cpu=CpuFigures(48.0, 48.0, 2.0, 8.0, 4.0, 0.0, 34.0, 12.0, 2.0),
        memory=MemoryFigures(65536, 65536, 2048, vios, 8192, 4096, 0, 50176),
        partitions=PartitionFigures(2, 1, 0, 0, 0, 0),
        shared_pools=(0,),
        gaps=() if vios is not None else ("VirtualIOServer feed: boom (HTTP 500)",),
    )


SURVEY = FleetSurvey(
    profiles=("hmc-a", "hmc-b", "hmc-c"),
    readings=(
        _reading("hmc-a", "SER0001"),
        _reading("hmc-b", "SER0001", vios=None),
        _reading("hmc-b", "SER0002"),
    ),
    failures=(ProfileFailure("hmc-c", "no answer within 300 s"),),
)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.delenv("HMC_HOST", raising=False)
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    names = ["hmc-a", "hmc-b", "hmc-c"]
    monkeypatch.setattr(
        report,
        "config_inventory",
        lambda: {"profiles": [{"name": n} for n in names], "config_file": "c.toml"},
    )
    calls: dict = {}

    async def fake_survey(profiles, open_client, *, concurrency, hmc_timeout):
        calls.update(
            profiles=list(profiles),
            open_client=open_client,
            concurrency=concurrency,
            hmc_timeout=hmc_timeout,
        )
        return SURVEY

    monkeypatch.setattr(report, "survey_fleet", fake_survey)
    return calls


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_csv_header_is_the_stable_column_list(configured, tmp_path) -> None:
    out = tmp_path / "report.csv"
    result = RUNNER.invoke(cli.app, ["report", "utilization", "--csv", str(out)])

    assert result.exit_code == 0, result.output
    with out.open(newline="", encoding="utf-8") as handle:
        assert next(csv.reader(handle)) == EXPECTED_COLUMNS
    assert list(report.COLUMNS) == EXPECTED_COLUMNS


def test_report_writes_system_hmc_fleet_and_failure_rows(configured, tmp_path) -> None:
    out = tmp_path / "report.csv"
    result = RUNNER.invoke(cli.app, ["report", "utilization", "--csv", str(out)])

    assert result.exit_code == 0, result.output
    rows = _rows(out)
    assert [row["row_type"] for row in rows] == [
        "system", "system", "hmc", "hmc", "fleet", "failure",
    ]  # fmt: skip
    dual = rows[0]
    assert (dual["serial"], dual["profiles"]) == ("SER0001", "hmc-a;hmc-b")
    assert dual["mem_vios_mib"] == "1024"
    assert (dual["cpu_allocated"], dual["cpu_util_pct"]) == ("14", "29.2")
    assert dual["mem_allocated_mib"] == "15360"
    assert rows[1]["mem_vios_mib"] == "1024"
    hmc_b = rows[3]
    assert (hmc_b["profiles"], hmc_b["systems"], hmc_b["mem_vios_mib"]) == (
        "hmc-b",
        "2",
        "1024",
    )
    assert hmc_b["mem_configurable_mib"] == "131072"
    assert hmc_b["notes"] == "mem_vios_mib: 1 of 2 systems unknown"
    fleet = rows[4]
    assert (fleet["systems"], fleet["notes"]) == ("2", "")
    assert (fleet["mem_configurable_mib"], fleet["mem_util_pct"]) == ("131072", "23.4")
    assert fleet["mem_allocated_mib"] == "30720"
    assert fleet["system"] == ""
    assert rows[5] == {
        **{column: "" for column in EXPECTED_COLUMNS},
        "row_type": "failure",
        "profiles": "hmc-c",
        "notes": "no answer within 300 s",
    }
    assert "hmc-c" in result.stderr
    assert "Wrote 2 systems from 2 of 3 profiles" in result.stderr


def test_unknown_vios_renders_unknown_not_zero(
    configured, tmp_path, monkeypatch
) -> None:
    out = tmp_path / "report.csv"
    configured_survey = FleetSurvey(
        ("hmc-b",), (_reading("hmc-b", "SER0003", vios=None),), ()
    )

    async def fake(profiles, open_client, *, concurrency, hmc_timeout):
        return configured_survey

    monkeypatch.setattr(report, "survey_fleet", fake)
    result = RUNNER.invoke(
        cli.app, ["report", "utilization", "--csv", str(out), "--profile", "hmc-b"]
    )

    assert result.exit_code == 0, result.output
    system = _rows(out)[0]
    assert system["mem_vios_mib"] == "unknown"
    assert system["notes"] == "VirtualIOServer feed: boom (HTTP 500)"


def test_default_selects_every_profile_and_opens_each_with_load_profile(
    configured, tmp_path, monkeypatch
) -> None:
    loaded: list[str] = []
    monkeypatch.setattr(report, "load_profile", lambda name: loaded.append(name))
    monkeypatch.setattr(report, "HMCClient", lambda config: ("client", config))
    out = tmp_path / "report.csv"

    result = RUNNER.invoke(
        cli.app,
        ["report", "utilization", "--csv", str(out), "--concurrency", "2",
         "--hmc-timeout", "30"],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert configured["profiles"] == ["hmc-a", "hmc-b", "hmc-c"]
    assert (configured["concurrency"], configured["hmc_timeout"]) == (2, 30.0)
    configured["open_client"]("hmc-b")
    assert loaded == ["hmc-b"]


def test_unknown_profile_is_a_usage_error(configured, tmp_path) -> None:
    result = RUNNER.invoke(
        cli.app,
        ["report", "utilization", "--csv", str(tmp_path / "r.csv"), "--profile", "x"],
    )

    assert result.exit_code == 2
    assert "unknown profile(s) x" in result.stderr
    assert "hmc-a, hmc-b, hmc-c" in result.stderr


def test_hmc_host_env_is_refused(configured, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HMC_HOST", "elsewhere.test")
    result = RUNNER.invoke(
        cli.app, ["report", "utilization", "--csv", str(tmp_path / "r.csv")]
    )

    assert result.exit_code == 2
    assert "unset HMC_HOST" in result.stderr
    assert "profiles" not in configured


def test_root_connection_option_is_refused(configured, tmp_path) -> None:
    result = RUNNER.invoke(
        cli.app,
        ["--profile", "hmc-a", "report", "utilization", "--csv",
         str(tmp_path / "r.csv")],
    )  # fmt: skip

    assert result.exit_code == 2
    assert "drop the global" in result.stderr


def test_write_failure_keeps_failure_lines_and_leaves_no_file(
    configured, tmp_path, monkeypatch
) -> None:
    def broken(path, rows):
        path.write_text("partial", encoding="utf-8")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(report, "write_csv", broken)
    result = RUNNER.invoke(
        cli.app, ["report", "utilization", "--csv", str(tmp_path / "r.csv")]
    )

    assert result.exit_code == 1
    assert "hmc-c" in result.stderr
    assert "No space left on device" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_missing_directory_is_a_usage_error_before_surveying(
    configured, tmp_path
) -> None:
    result = RUNNER.invoke(
        cli.app,
        ["report", "utilization", "--csv", str(tmp_path / "absent" / "r.csv")],
    )

    assert result.exit_code == 2
    assert "cannot write the report beside" in result.stderr
    assert "profiles" not in configured


def test_no_configured_profiles_fails(configured, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        report, "config_inventory", lambda: {"profiles": [], "config_file": None}
    )
    result = RUNNER.invoke(
        cli.app, ["report", "utilization", "--csv", str(tmp_path / "r.csv")]
    )

    assert result.exit_code == 1
    assert "no connection profiles are configured" in result.stderr
