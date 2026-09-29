"""Tests for the ledger-to-scenario gap report (scripts/scenario_gap_report.py)."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPTS = ROOT / "scripts"
# The report imports the runner, which imports `live_test` and its sibling scripts
# as top-level modules, exactly as running a script from `scripts/` would.
sys.path.insert(0, str(SCRIPTS))
MODULE_SPEC = importlib.util.spec_from_file_location(
    "scenario_gap_report", SCRIPTS / "scenario_gap_report.py"
)
assert MODULE_SPEC is not None
assert MODULE_SPEC.loader is not None
report = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = report
MODULE_SPEC.loader.exec_module(report)

SCHEMAS = {
    "hmc_list_lpars": {"properties": {"system": {"type": "string"}}},
    "hmc_get_lpar": {
        "properties": {"lpar": {"type": "string"}, "system": {"type": "string"}},
        "required": ["lpar"],
    },
}
OPERATIONS = [
    {"operation": "lpar.list", "tool": "hmc_list_lpars", "row_ids": ["rest:a"]},
    {"operation": "lpar.get", "tool": "hmc_get_lpar", "row_ids": ["rest:a", "rest:b"]},
]
ROWS = ["rest:a", "rest:b", "cli:c"]


def test_scan_reads_dispatches_operations_and_unreadable_sites():
    source = (
        "async def scenario(client, state, tool, extra):\n"
        '    await state.call(client, "hmc_list_lpars", system="s", expected=[X])\n'
        "    await state.call(client, tool)\n"
        '    await state.call(client, "hmc_get_lpar", **extra)\n'
        '    state.record_verified(1, "t", operation="lpar.get", scenario="st1-x")\n'
        "    state.record_verified(1, 't', operation=name)\n"
    )

    scan = report.scan_source(source, "m.py")

    assert scan.dispatches == (
        report.Dispatch("m.py:2", "hmc_list_lpars", ("system",)),
    )
    assert scan.verified == (("m.py:5", "lpar.get"),)
    assert scan.unreadable == (
        "m.py:3 dispatch with a non-literal tool or a ** splat",
        "m.py:4 dispatch with a non-literal tool or a ** splat",
        "m.py:6 record_verified without a literal operation",
    )


def test_report_lists_uncovered_operations_and_rows():
    scan = report.scan_source(
        'async def s(client, state):\n    await state.call(client, "hmc_list_lpars")\n',
        "m.py",
    )

    lines = report.build_report([scan], OPERATIONS, ROWS, SCHEMAS)

    assert lines == [
        "uncovered-operation: lpar.get",
        "uncovered-row: cli:c",
        "uncovered-row: rest:b",
        (
            "summary: 1 dispatches; operations 1/2 exercised; rows 1/3 exercised; "
            "0 unregistered; 0 dispatch mismatches; 0 unreadable"
        ),
    ]


def test_record_verified_operation_counts_as_exercised():
    scan = report.scan_source(
        'def s(state):\n    state.record_verified(1, "t", operation="lpar.get")\n',
        "m.py",
    )

    lines = report.build_report([scan], OPERATIONS, ROWS, SCHEMAS)

    assert "uncovered-operation: lpar.get" not in lines
    assert "uncovered-row: rest:b" not in lines


def test_report_lists_scenarios_that_left_the_registry():
    source = (
        "async def s(client, state):\n"
        '    await state.call(client, "hmc_removed_tool")\n'
        '    state.record_verified(1, "t", operation="lpar.removed")\n'
    )

    lines = report.build_report(
        [report.scan_source(source, "m.py")], OPERATIONS, ROWS, SCHEMAS
    )

    assert "unregistered: m.py:2 tool hmc_removed_tool" in lines
    assert not [line for line in lines if line.startswith("dispatch-mismatch:")]
    assert "unregistered: m.py:3 operation lpar.removed" in lines


def test_a_served_tool_missing_from_the_ledger_is_unregistered():
    schemas = {**SCHEMAS, "hmc_new_tool": {"properties": {}}}
    source = (
        'async def s(client, state):\n    await state.call(client, "hmc_new_tool")\n'
    )

    lines = report.build_report(
        [report.scan_source(source, "m.py")], OPERATIONS, ROWS, schemas
    )

    assert "unregistered: m.py:2 tool hmc_new_tool" in lines


def test_report_lists_unknown_and_missing_required_arguments():
    source = (
        "async def s(client, state):\n"
        '    await state.call(client, "hmc_get_lpar", system="s", bogus=1)\n'
    )

    lines = report.build_report(
        [report.scan_source(source, "m.py")], OPERATIONS, ROWS, SCHEMAS
    )

    assert [line for line in lines if line.startswith("dispatch-mismatch:")] == [
        "dispatch-mismatch: m.py:2 hmc_get_lpar: unknown argument bogus",
        "dispatch-mismatch: m.py:2 hmc_get_lpar: missing required argument lpar",
    ]


def test_exit_status_fails_only_on_dispatch_findings_under_the_flag():
    clean = ["uncovered-row: cli:c", "summary: ..."]

    for prefix in ("unregistered:", "dispatch-mismatch:", "unreadable:"):
        failing = [*clean, f"{prefix} m.py:2 x"]
        assert report.exit_status(failing, fail_on_dispatch=False) == 0
        assert report.exit_status(failing, fail_on_dispatch=True) == 1
    assert report.exit_status(clean, fail_on_dispatch=True) == 0


def test_report_over_the_repository_is_clean_and_summarised(capsys):
    assert report.main(["--fail-on-dispatch"]) == 0

    out = capsys.readouterr().out.splitlines()
    # A scan that read nothing would also be clean; the floor is what says it read.
    summary = re.fullmatch(
        r"summary: (\d+) dispatches; operations (\d+)/\d+ exercised; .*", out[-1]
    )
    assert summary is not None, out[-1]
    dispatches, operations = summary.groups()
    assert int(dispatches) > 0
    assert int(operations) > 0
    assert not [line for line in out if line.startswith(report._DISPATCH_PREFIXES)]
