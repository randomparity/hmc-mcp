"""Contract tests for the post-run recovery check.

Every case drives the checks through a stub call path. The stub refuses any
tool outside the read-only allowlist, so a mutating call added later fails
every case rather than one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_ROOT = Path(__file__).parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))
import live_test_recovery as recovery  # noqa: E402

_MARKER = "pcie-deadbeef"
_SYSTEM = "sys-R1"
_LPAR = f"live-pcie-{_MARKER}"
_DRC = "553713664"
_BASELINE = "none"


def _stamped(token: str) -> str:
    """An ADR 0064 ownership stamp as the HMC returns it in a description."""
    return (
        "{'UUID': '11111111-2222-3333-4444-555555555555'} "
        f"[hmc-mcp owner:hmc-mcp created:2026-09-21] [caller {token}]"
    )

_INPUTS = recovery.RecoveryInputs(
    system_name=_SYSTEM,
    run_marker=_MARKER,
    fixture_lpar=_LPAR,
    drc_index=_DRC,
    baseline_io_slots=_BASELINE,
    profile_name="default",
)


def _caller(responses: dict[str, object], seen: list[str] | None = None):
    """A stub call path that enforces the same guard the real one does."""

    async def call(tool: str, **arguments):
        recovery.guard_read_only(tool, arguments)
        if seen is not None:
            seen.append(tool)
        response = responses.get(tool)
        if response is None:
            return "FAIL", None
        return "PASS", response

    return call


#: A system with nothing left behind: no partition, slot unowned, profile at
#: its baseline.
_CLEAN = {
    "hmc_get_lpar_description": "not found",
    "hmc_list_dedicated_pcie_slots": {
        "items": [{"drc_index": _DRC, "owner_lpar": None}]
    },
    "hmc_run_command": f"{_BASELINE}\n",
}


def _responses(**overrides) -> dict:
    return {**_CLEAN, **overrides}


# ---------------------------------------------------------------------------
# Each stranded condition is detected (Validation 9)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_clean_system_yields_no_findings():
    assert await recovery.check(_caller(_responses()), _INPUTS) == []


@pytest.mark.asyncio
async def test_a_surviving_marked_partition_is_reported():
    responses = _responses(
        hmc_get_lpar_description=_stamped(_MARKER)
    )

    findings = await recovery.check(_caller(responses), _INPUTS)

    assert [f.what for f in findings] == ["surviving partition"]
    assert _LPAR in findings[0].detail
    assert "rmsyscfg" in findings[0].remedy


@pytest.mark.asyncio
async def test_a_partition_of_the_same_name_from_another_run_is_not_claimed():
    """The marker is the ownership proof; a name collision is not this run's."""
    responses = _responses(
        hmc_get_lpar_description=_stamped("pcie-someoneelse")
    )

    assert await recovery.check(_caller(responses), _INPUTS) == []


@pytest.mark.asyncio
async def test_a_stranded_slot_is_reported():
    responses = _responses(
        hmc_list_dedicated_pcie_slots={
            "items": [{"drc_index": _DRC, "owner_lpar": _LPAR}]
        }
    )

    findings = await recovery.check(_caller(responses), _INPUTS)

    assert [f.what for f in findings] == ["stranded slot"]
    assert _DRC in findings[0].detail
    assert "io_slots-" in findings[0].remedy


@pytest.mark.asyncio
async def test_a_slot_owned_by_the_literal_string_null_reads_as_unowned():
    """The HMC spells an unowned slot `null` in some responses."""
    responses = _responses(
        hmc_list_dedicated_pcie_slots={
            "items": [{"drc_index": _DRC, "owner_lpar": "null"}]
        }
    )

    assert await recovery.check(_caller(responses), _INPUTS) == []


@pytest.mark.asyncio
async def test_profile_drift_from_the_baseline_is_reported():
    findings = await recovery.check(
        _caller(_responses(hmc_run_command=f"{_DRC}//0\n")), _INPUTS
    )

    assert [f.what for f in findings] == ["profile drift"]
    assert "still listed" in findings[0].detail
    assert _BASELINE in findings[0].remedy


@pytest.mark.asyncio
async def test_profile_drift_to_an_unrelated_value_is_still_reported():
    """Drift is drift; the arm's own Guard B refuses on any mismatch."""
    findings = await recovery.check(
        _caller(_responses(hmc_run_command="21010020//0\n")), _INPUTS
    )

    assert [f.what for f in findings] == ["profile drift"]
    assert "still listed" not in findings[0].detail


@pytest.mark.asyncio
async def test_every_condition_at_once_is_reported_together():
    responses = _responses(
        hmc_get_lpar_description=_stamped(_MARKER),
        hmc_list_dedicated_pcie_slots={
            "items": [{"drc_index": _DRC, "owner_lpar": _LPAR}]
        },
        hmc_run_command=f"{_DRC}//0\n",
    )

    findings = await recovery.check(_caller(responses), _INPUTS)

    assert [f.what for f in findings] == [
        "surviving partition",
        "stranded slot",
        "profile drift",
    ]


@pytest.mark.asyncio
async def test_an_unreadable_profile_is_not_reported_as_drift():
    """A failed read is not evidence of a clean profile, nor of a dirty one."""
    responses = _responses(hmc_run_command="")

    assert await recovery.check(_caller(responses), _INPUTS) == []


@pytest.mark.asyncio
async def test_a_multi_record_profile_answer_is_treated_as_unreadable():
    responses = _responses(hmc_run_command="none\n21010020//0\n")

    assert await recovery.check(_caller(responses), _INPUTS) == []


@pytest.mark.asyncio
async def test_a_run_with_no_drc_index_checks_only_the_partition():
    inputs = recovery.RecoveryInputs(
        _SYSTEM, _MARKER, _LPAR, None, None, "default"
    )
    seen: list[str] = []

    await recovery.check(_caller(_responses(), seen), inputs)

    assert seen == ["hmc_get_lpar_description"]


# ---------------------------------------------------------------------------
# No mutating call is ever issued (Validation 10)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_tool_the_checks_call_is_on_the_read_only_allowlist():
    seen: list[str] = []

    await recovery.check(_caller(_responses(), seen), _INPUTS)

    assert set(seen) <= recovery._READ_ONLY_TOOLS
    assert seen  # the checks did run


@pytest.mark.parametrize(
    "tool", ["hmc_delete_lpar", "hmc_create_lpar", "hmc_assign_dedicated_slot"]
)
def test_a_mutating_tool_is_refused(tool):
    with pytest.raises(recovery.MutatingCallRefused, match="read-only allowlist"):
        recovery.guard_read_only(tool, {})


@pytest.mark.parametrize(
    "command",
    ["chsyscfg -r prof -i x", "rmsyscfg -r lpar -n x", "mksyscfg -r lpar", ""],
)
def test_run_command_is_refused_for_anything_but_lssyscfg(command):
    """`hmc_run_command` shares one tool between reads and mutations."""
    with pytest.raises(recovery.MutatingCallRefused, match="read-only only for"):
        recovery.guard_read_only("hmc_run_command", {"cmd": command})


def test_run_command_is_allowed_for_lssyscfg():
    recovery.guard_read_only("hmc_run_command", {"cmd": "lssyscfg -r prof -F io_slots"})


@pytest.mark.asyncio
async def test_the_guard_is_on_the_call_path_not_only_in_review():
    """A check that reached for a mutation would raise, not silently succeed."""

    async def call(tool: str, **arguments):
        recovery.guard_read_only(tool, arguments)
        return "PASS", None

    with pytest.raises(recovery.MutatingCallRefused):
        await call("hmc_delete_lpar", lpar_name_or_uuid=_LPAR)


# ---------------------------------------------------------------------------
# Inputs come from the results document
# ---------------------------------------------------------------------------


def _document(**artifact_overrides) -> dict:
    return {
        "config": {
            "dedicated_pcie_system_name": _SYSTEM,
            "dedicated_pcie_profile_name": "default",
        },
        "artifacts": {
            "pcie_run_marker": _MARKER,
            "pcie_fixture_lpar": _LPAR,
            "pcie_drc_index": _DRC,
            "pcie_baseline_io_slots": _BASELINE,
            **artifact_overrides,
        },
    }


def test_inputs_are_read_from_the_documents_artifact_fields():
    inputs = recovery.inputs_from_document(_document())

    assert inputs == _INPUTS


def test_a_document_recording_no_fixture_yields_no_inputs():
    """A run that never reached ST29 created nothing to look for."""
    assert recovery.inputs_from_document(_document(pcie_run_marker=None)) is None


@pytest.mark.parametrize(
    "document", ["not a dict", {}, {"artifacts": {}}, {"config": {}}, []]
)
def test_a_malformed_document_yields_no_inputs(document):
    assert recovery.inputs_from_document(document) is None


def test_a_run_that_stopped_before_the_baseline_still_yields_inputs():
    """The ST29 fields alone are enough to look for a surviving partition."""
    inputs = recovery.inputs_from_document(_document(pcie_baseline_io_slots=None))

    assert inputs is not None
    assert inputs.run_marker == _MARKER
    assert inputs.baseline_io_slots is None


def test_a_document_with_no_fixture_exits_zero_without_contacting_the_hmc(
    tmp_path, monkeypatch, capsys
):
    def forbidden():
        raise AssertionError("bootstrapped credentials with nothing to check")

    monkeypatch.setattr(recovery.runner, "_bootstrap_config", forbidden)
    path = tmp_path / "results.json"
    path.write_text(json.dumps(_document(pcie_run_marker=None)), encoding="utf-8")

    assert recovery.main(["--results", str(path)]) == 0
    assert "created nothing to recover" in capsys.readouterr().out


def test_an_unreadable_document_exits_two_not_zero(tmp_path, capsys):
    """Could-not-determine is not clean."""
    assert recovery.main(["--results", str(tmp_path / "absent.json")]) == 2
    assert "cannot read" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_a_clean_report_names_the_marker_it_matched_on(capsys):
    recovery._report(_INPUTS, [])

    output = capsys.readouterr().out
    assert "CLEAN" in output
    assert _MARKER in output


def test_a_stranded_report_prints_the_command_and_disclaims_acting(capsys):
    finding = recovery.Finding("stranded slot", "detail here", "hmc chsyscfg ...")

    recovery._report(_INPUTS, [finding])

    output = capsys.readouterr().out
    assert "STRANDED" in output
    assert "hmc chsyscfg ..." in output
    assert "issues no mutating call" in output
