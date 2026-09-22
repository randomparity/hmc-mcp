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
from live_test.observation import CallFailure  # noqa: E402

_MARKER = "pcie-deadbeef"
_SYSTEM = "sys-R1"
_LPAR = f"live-pcie-{_MARKER}"
_DRC = "553713664"
_BASELINE = "none"


def _stamped(token: str) -> str:
    """An ADR 0064 ownership stamp as the HMC returns it in a description."""
    return (
        "{'UUID': '11111111-2222-3333-4444-555555555555'} "
        f"[hmcpctl owner:hmcpctl created:2026-09-21] [caller {token}]"
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
        if isinstance(response, CallFailure):
            return "FAIL", response
        return "PASS", response

    return call


#: How the HMC answers a lookup for a partition it does not have: a failing
#: command carrying HSCL8012 (ADR 0162), not a successful empty answer.
_NOT_FOUND = CallFailure(
    "HMCCLIError",
    f"HMCCLIError: HSCL8012 The partition named {_LPAR} was not found.",
    "",
    None,
    False,
)

#: A system with nothing left behind: no partition, slot unowned, profile at
#: its baseline.
_CLEAN = {
    "hmc_get_lpar_description": _NOT_FOUND,
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
    """Drift is only reachable while the fixture survives: see `_surviving`."""
    responses = _responses(
        hmc_get_lpar_description=_stamped(_MARKER),
        hmc_run_command=f"{_DRC}//0\n",
    )

    findings = await recovery.check(_caller(responses), _INPUTS)

    drift = [f for f in findings if f.what == "profile drift"]
    assert len(drift) == 1
    assert "still listed" in drift[0].detail
    assert _BASELINE in drift[0].remedy


@pytest.mark.asyncio
async def test_profile_drift_to_an_unrelated_value_is_still_reported():
    """Drift is drift; the arm's own Guard B refuses on any mismatch."""
    responses = _responses(
        hmc_get_lpar_description=_stamped(_MARKER),
        hmc_run_command="21010020//0\n",
    )

    findings = await recovery.check(_caller(responses), _INPUTS)

    drift = [f for f in findings if f.what == "profile drift"]
    assert len(drift) == 1
    assert "still listed" not in drift[0].detail


@pytest.mark.asyncio
async def test_a_deleted_fixture_is_not_asked_for_its_profile():
    """A profile dies with its partition; asking answers HSCL8012.

    Without this gate every clean run would read that refusal as an unreadable
    system and exit 2.
    """
    seen: list[str] = []

    findings = await recovery.check(_caller(_responses(), seen), _INPUTS)

    assert findings == []
    assert "hmc_run_command" not in seen


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
async def test_an_unreadable_profile_is_not_clean():
    """A failed read is not evidence of a clean profile. Exit 2, not exit 0."""
    responses = _responses(
        hmc_get_lpar_description=_stamped(_MARKER), hmc_run_command=None
    )

    with pytest.raises(recovery.StateUnreadable, match="could not read profile"):
        await recovery.check(_caller(responses), _INPUTS)


@pytest.mark.asyncio
async def test_a_multi_record_profile_answer_is_treated_as_unreadable():
    responses = _responses(
        hmc_get_lpar_description=_stamped(_MARKER),
        hmc_run_command="none\n21010020//0\n",
    )

    with pytest.raises(recovery.StateUnreadable, match="2 records"):
        await recovery.check(_caller(responses), _INPUTS)


@pytest.mark.asyncio
async def test_an_unreadable_system_carries_the_findings_already_confirmed():
    """Exit 2 must not swallow what was found before the read failed."""
    responses = _responses(
        hmc_get_lpar_description=_stamped(_MARKER), hmc_run_command=None
    )

    with pytest.raises(recovery.StateUnreadable) as raised:
        await recovery.check(_caller(responses), _INPUTS)

    assert [f.what for f in raised.value.findings] == ["surviving partition"]


@pytest.mark.asyncio
async def test_a_partition_lookup_failing_for_another_reason_is_not_clean():
    """Only HSCL8012 means "no such partition"; any other failure is unreadable.

    Reading every failed lookup as "gone" reported a system clean after, say, an
    authentication refusal, while a partition carrying this run's marker survived.
    """
    lost = CallFailure("HMCCLIError", "HMCCLIError: connection lost", "", None, False)
    responses = _responses(hmc_get_lpar_description=lost)

    with pytest.raises(recovery.StateUnreadable, match="could not look up"):
        await recovery.check(_caller(responses), _INPUTS)


@pytest.mark.asyncio
async def test_an_unreadable_partition_lookup_keeps_the_stranded_slot():
    lost = CallFailure("HMCCLIError", "HMCCLIError: connection lost", "", None, False)
    responses = _responses(
        hmc_get_lpar_description=lost,
        hmc_list_dedicated_pcie_slots={
            "items": [{"drc_index": _DRC, "owner_lpar": "someone"}]
        },
    )

    with pytest.raises(recovery.StateUnreadable) as raised:
        await recovery.check(_caller(responses), _INPUTS)

    assert [f.what for f in raised.value.findings] == ["stranded slot"]


@pytest.mark.asyncio
async def test_a_partition_lookup_answering_no_description_is_not_clean():
    responses = _responses(hmc_get_lpar_description={"unexpected": "shape"})

    with pytest.raises(recovery.StateUnreadable, match="could not look up"):
        await recovery.check(_caller(responses), _INPUTS)


@pytest.mark.asyncio
async def test_an_unlistable_system_is_not_clean():
    """The slot listing is the reachability probe; failing it is never clean."""
    responses = _responses(hmc_list_dedicated_pcie_slots=None)

    with pytest.raises(recovery.StateUnreadable, match="could not list"):
        await recovery.check(_caller(responses), _INPUTS)


@pytest.mark.asyncio
async def test_a_run_with_no_drc_index_still_probes_then_checks_the_partition():
    inputs = recovery.RecoveryInputs(
        _SYSTEM, _MARKER, _LPAR, None, None, "default"
    )
    seen: list[str] = []

    await recovery.check(_caller(_responses(), seen), inputs)

    assert seen == ["hmc_list_dedicated_pcie_slots", "hmc_get_lpar_description"]


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
# The profile read is the arm's own, not a second spelling of it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_profile_read_filters_on_the_profile_name_too():
    """A partition may carry several profiles.

    Filtering on `lpar_names` alone answers one record per profile, which the
    "exactly one record" rule then calls an unreadable system. A real VIOS
    partition with two profiles is where this surfaced.
    """
    sent: list[str] = []

    async def call(tool: str, **arguments):
        recovery.guard_read_only(tool, arguments)
        if tool == "hmc_run_command":
            sent.append(arguments["cmd"])
            return "PASS", f"{_BASELINE}\n"
        return "PASS", _CLEAN[tool]

    inputs = recovery.RecoveryInputs(
        _SYSTEM, _MARKER, _LPAR, _DRC, _BASELINE, "fixture-profile"
    )
    await recovery._profile_drift(call, inputs)

    assert "profile_names=fixture-profile" in sent[0]
    assert f"lpar_names={_LPAR}" in sent[0]


def test_recovery_reads_the_profile_with_the_arms_own_command_builder():
    """One definition of the admitted read, so the two cannot drift apart."""
    from live_test import pcie

    assert recovery.profile_io_slots_command is pcie.profile_io_slots_command


def test_an_unset_profile_name_falls_back_to_the_arms_default():
    """The run records an empty string; the fallback must be the arm's.

    A second spelling would query, and tell the operator to repair, a profile
    the arm never touched.
    """
    from live_test import pcie

    document = _document()
    document["config"]["dedicated_pcie_profile_name"] = ""

    inputs = recovery.inputs_from_document(document)

    assert inputs.profile_name == pcie._DEFAULT_DEDICATED_PROFILE


# ---------------------------------------------------------------------------
# The allowlisted tools exist on the server the checks actually talk to
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_read_only_tool_is_registered_on_the_composed_server():
    """An allowlist of tools the server does not serve guards nothing.

    `hmc_run_command` is an opt-in escape hatch. Composing without it left the
    profile-drift check calling a tool that answered "Unknown tool", which the
    check then read as no drift — a clean verdict on an unexamined system.
    Every case above stubs the call path, so only this one sees it.
    """
    from fastmcp import Client

    async with Client(await recovery._compose_server()) as client:
        registered = {tool.name for tool in await client.list_tools()}

    assert recovery._READ_ONLY_TOOLS <= registered


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
