"""Behavioural tests for the dedicated PCIe live-assignment arm (issue #217)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

LIVE_TEST_ROOT = Path(__file__).parents[2] / "scripts"
sys.path.insert(0, str(LIVE_TEST_ROOT))
from live_test import pcie  # noqa: E402
from live_test.observation import CallFailure  # noqa: E402
from live_test_runner import LiveTestArtifacts, LiveTestConfig, RunState  # noqa: E402

#: The arm's own settings. Everything else on `LiveTestConfig` keeps its
#: declared default: this arm reads only these four, and reads them from the
#: validated configuration rather than the environment (ADR 0115).
_CONFIG = {
    "dedicated_pcie_system_name": "sys-one",
    "dedicated_pcie_lpar_prefix": "live-",
}
_DRC = "21010020"
_ASSIGNED = f"{_DRC}/none/0"
#: What the HMC answers for a partition name it does not have
#: (`scripts/live_test_recovery.py:172-173`, ADR 0162).
_NOT_FOUND = CallFailure(
    "HMCCLIError",
    "HMCCLIError: HSCL8012 The partition named live-x-createtime was not found.",
    "",
    None,
    False,
)

_CONNECTION_LOST = CallFailure(
    "HMCCLIError", "HMCCLIError: connection lost", "", None, False
)
#: Same wording as HSCL8012, but IBM's only recovery for it is rebuilding the
#: managed system, so it is not read as "no such partition".
_SIBLING_NOT_FOUND = CallFailure(
    "HMCCLIError",
    "HMCCLIError: HSCL7002 The partition named live-x-createtime was not found.",
    "",
    None,
    False,
)


@pytest.mark.parametrize(
    ("status", "data", "expected"),
    [
        ("FAIL", _NOT_FOUND, True),
        # Only a failed call is the HMC's "no such partition" answer.
        ("PASS", _NOT_FOUND, False),
        ("FAIL", _CONNECTION_LOST, False),
        ("FAIL", _SIBLING_NOT_FOUND, False),
        ("FAIL", None, False),
        ("PASS", "description text", False),
    ],
)
def test_partition_not_found_reads_only_a_failed_hscl8012(status, data, expected):
    """HSCL8012 is the one "no such partition" answer; nothing else counts."""
    assert pcie.partition_not_found(status, data) is expected


class ScenarioState:
    """State seam recording every tool call in order, with per-call outcomes."""

    def __init__(
        self,
        responses: dict[str, Any],
        statuses: dict[str, Any] | None = None,
        config: LiveTestConfig | None = None,
    ) -> None:
        self.responses = responses
        self.statuses = statuses or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.results: list[tuple[int, str, str, Any]] = []
        self.context = SimpleNamespace(system_name="unused", lp3_name="unused")
        self.config = config if config is not None else LiveTestConfig(**_CONFIG)
        self.gaps: list[dict[str, Any]] = []
        # The production dataclass, not a stand-in: the arm records what it
        # created here and `live_test_recovery.py` reads those exact fields.
        self.artifacts = LiveTestArtifacts()
        self.tool_counts: dict[str, int] = {}
        self.cleanup_start: int | None = None

    async def call(
        self, _client: object, tool: str, **kwargs: Any
    ) -> tuple[str, Any]:
        index = self.tool_counts.get(tool, 0)
        self.tool_counts[tool] = index + 1
        self.calls.append((tool, kwargs))
        response = self.responses.get(tool)
        if callable(response):
            response = response(kwargs, index)
        # A response that is a failure is what the tool raised, so it is a FAIL.
        status = self.statuses.get(
            tool, "FAIL" if isinstance(response, CallFailure) else "PASS"
        )
        if callable(status):
            status = status(tool, kwargs, index)
        return status, response

    def record(
        self, subtask: int, tool: str, status: str, data: Any, note: str = ""
    ) -> None:
        # `RunState.record` carries the note in `note` and the payload in
        # `data`; keeping whichever is populated lets one assertion read both.
        self.results.append(
            (subtask, tool, status, data if data is not None else note)
        )

    def skip(self, subtask: int, tool: str, reason: str) -> None:
        self.results.append((subtask, tool, "SKIP", reason))

    # The production classifier, not a re-implementation of it. It reads only
    # `skip`, `record` and `gaps`, all of which this seam provides — so the
    # tests exercise the real declared-limitation matching rather than a copy
    # that can agree with a broken arm.
    record_with_expected = RunState.record_with_expected

    # -- views -------------------------------------------------------------

    def tools(self) -> list[str]:
        return [tool for tool, _ in self.calls]

    def commands(self) -> list[str]:
        return [k["cmd"] for t, k in self.calls if t == "hmc_run_command"]

    def cleanup_calls(self) -> list[tuple[str, dict[str, Any]]]:
        assert self.cleanup_start is not None, "cleanup never ran"
        return self.calls[self.cleanup_start :]

    def cleanup_commands(self) -> list[str]:
        return [k["cmd"] for t, k in self.cleanup_calls() if t == "hmc_run_command"]

    def cleanup_tools(self) -> list[str]:
        return [tool for tool, _ in self.cleanup_calls()]

    def row(self, needle: str) -> tuple[int, str, str, Any] | None:
        """The first recorded row whose tool label contains *needle*."""
        return next((r for r in self.results if needle in r[1]), None)


# ---------------------------------------------------------------------------
# Response / status helpers
# ---------------------------------------------------------------------------

_ADMITTED_VERSION = "Version: 10\nRelease: 3\nService Pack: 1060"
_ADMITTED_MODEL = "8375-42A"
#: A refused create-time probe as `RunState.call` delivers it: a classified
#: `CallFailure` that created nothing.
_REFUSAL = CallFailure(
    exception_type="PcieAssignmentUnavailableError",
    message="PcieAssignmentUnavailableError: refused before creation",
    traceback_text="",
    http_status=None,
    denied=False,
)


def _slot_inventory(owner: str = "") -> dict[str, Any]:
    return {
        "capability": "available",
        "items": [
            {"drc_index": _DRC, "description": "PCIe adapter", "owner_lpar": owner}
        ],
    }


def _is_probe(kwargs: dict[str, Any]) -> bool:
    return str(kwargs.get("name", "")).endswith("-createtime")


def _is_probe_name(kwargs: dict[str, Any]) -> bool:
    return str(kwargs.get("lpar_name_or_uuid", "")).endswith("-createtime")


def _probe_refused(_tool: str, kwargs: dict[str, Any], _index: int) -> str:
    """The default: the create-time probe creates nothing, the fixture create succeeds.

    Tests of the create-time path itself opt into a probe that exists.
    """
    return "FAIL" if _is_probe(kwargs) else "PASS"


def _happy_responses(
    marker_holder: dict[str, str],
    *,
    description: str | None = None,
    descriptions: list[str] | None = None,
    probe_description: Any = None,
    uuid_value: str | None = "fixture-uuid",
    uuids: list[str | None] | None = None,
    ownership_stamped: bool | None = True,
    hmc_version: str = _ADMITTED_VERSION,
    system_model: str = _ADMITTED_MODEL,
    inventory_owner: str = "",
    run_command: Any = None,
    probe_exists: bool = False,
) -> dict[str, Any]:
    """Responses for a run in which every step succeeds.

    ``descriptions`` and ``uuids``, when given, are consumed one per call so a
    test can make an identity drift *between* two reads — which is how Guard C
    is reached with a value Guard A already accepted. ``run_command`` replaces
    the default command model outright, for the two tests whose fault is a
    response property rather than a status.
    """
    slots = {"io_slots": "none"}

    def default_run_command(kwargs: dict[str, Any], _index: int) -> str:
        cmd = kwargs["cmd"]
        if cmd == "lshmc -V":
            return hmc_version
        if "-r sys " in cmd and "type_model" in cmd:
            return system_model
        if "io_slots+" in cmd:
            slots["io_slots"] = _ASSIGNED
            return ""
        if "io_slots-" in cmd:
            slots["io_slots"] = "none"
            return ""
        return slots["io_slots"]

    def get_description(kwargs: dict[str, Any], index: int) -> Any:
        # Keyed on which partition is being asked about, so a test can give the
        # probe a foreign token while the fixture keeps this run's — the only
        # way `_cleanup_probe_partition`'s comparison can be made to refuse.
        if _is_probe_name(kwargs):
            if probe_description is not None:
                return probe_description
            if not probe_exists:
                # The probe create was refused, so this partition does not
                # exist. Answering with the fixture's own stamp would tell the
                # arm's lost-response readback that a refused create had in
                # fact created something, which is the opposite of the truth.
                return _NOT_FOUND
        if descriptions is not None:
            return descriptions[min(index, len(descriptions) - 1)]
        if description is not None:
            return description
        token = marker_holder.get("marker", "")
        return f"[hmcpctl owner:hmcpctl created:2026-09-02] [caller {token}]"

    def get_lpar(_kwargs: dict[str, Any], index: int) -> dict[str, Any] | None:
        value = (
            uuids[min(index, len(uuids) - 1)] if uuids is not None else uuid_value
        )
        return {"UUID": value} if value else None

    def create_lpar(kwargs: dict[str, Any], _index: int) -> Any:
        if _is_probe(kwargs):
            return _REFUSAL
        return {
            "resource_created": True,
            "lpar": {"UUID": uuid_value} if uuid_value else None,
            "ownership_stamped": ownership_stamped,
            "warnings": [],
        }

    command_model = run_command or default_run_command

    def assign_slot(kwargs: dict[str, Any], index: int) -> None:
        # The operation issues the documented grammar itself (ADR 0166), so the
        # same profile model sees the write the arm's raw reads later observe.
        command_model({"cmd": _io_slots_plus(kwargs["lpar_name_or_uuid"])}, index)

    return {
        "hmc_list_dedicated_pcie_slots": lambda _k, _n: _slot_inventory(
            inventory_owner
        ),
        "hmc_create_lpar": create_lpar,
        "hmc_get_lpar": get_lpar,
        "hmc_get_lpar_description": get_description,
        "hmc_run_command": command_model,
        "hmc_delete_lpar": lambda _k, _n: "deleted",
        "hmc_assign_dedicated_pcie_slot": assign_slot,
    }


def _io_slots_plus(lpar_name: str) -> str:
    return f"chsyscfg -r prof -m sys-one -i name=default_profile,io_slots+={_DRC}//0,lpar_name={lpar_name}"


def _probe_create_succeeds(
    responses: dict[str, Any], *, assignment_lands: bool = True
) -> None:
    """Make the create-time probe create its partition, as the lifted gate does."""
    command_model = responses["hmc_run_command"]

    def create_lpar(kwargs: dict[str, Any], index: int) -> Any:
        probe = _is_probe(kwargs)
        if probe and assignment_lands:
            command_model({"cmd": _io_slots_plus(str(kwargs["name"]))}, index)
        return {
            "resource_created": True,
            "lpar": {"UUID": "probe-uuid" if probe else "fixture-uuid"},
            "ownership_stamped": True,
            "warnings": [],
        }

    responses["hmc_create_lpar"] = create_lpar


def _command_fails(needle: str) -> Any:
    """Status seam: fail every `hmc_run_command` whose text contains *needle*."""

    def status(_tool: str, kwargs: dict[str, Any], _index: int) -> str:
        return "FAIL" if needle in kwargs.get("cmd", "") else "PASS"

    return status


def _nth_matching_command_fails(needle: str, first: int) -> Any:
    """Status seam: fail matching commands from the *first*-th occurrence on.

    Counts matches itself rather than using the per-tool index, because
    `hmc_run_command` also carries the environment reads and the mutations.
    """
    seen = {"n": 0}

    def status(_tool: str, kwargs: dict[str, Any], _index: int) -> str:
        if needle not in kwargs.get("cmd", ""):
            return "PASS"
        seen["n"] += 1
        return "FAIL" if seen["n"] >= first else "PASS"

    return status


async def _run_arm(
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[str, Any],
    marker_holder: dict[str, str],
    statuses: dict[str, Any] | None = None,
    config: dict[str, str] | None = None,
) -> ScenarioState:
    live = LiveTestConfig(**(config if config is not None else _CONFIG))

    real_marker = pcie._new_run_marker

    def capture() -> str:
        marker = real_marker()
        marker_holder["marker"] = marker
        return marker

    monkeypatch.setattr(pcie, "_new_run_marker", capture)

    # The cleanup phase boundary: the arm reaches cleanup through this module
    # global, so wrapping it records where cleanup's calls begin.
    real_cleanup = pcie.cleanup_dedicated

    async def marking_cleanup(client: Any, st: Any, fixture: Any) -> None:
        st.cleanup_start = len(st.calls)
        await real_cleanup(client, st, fixture)

    monkeypatch.setattr(pcie, "cleanup_dedicated", marking_cleanup)

    merged: dict[str, Any] = {"hmc_create_lpar": _probe_refused}
    merged.update(statuses or {})
    state = ScenarioState(responses, merged, config=live)
    await pcie.exercise_dedicated_pcie_assignment(None, state)
    return state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_configuration_skips_without_any_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder: dict[str, str] = {}
    state = await _run_arm(monkeypatch, {}, holder, config={})
    assert state.calls == []
    assert [status for _, _, status, _ in state.results] == ["SKIP"]


@pytest.mark.asyncio
async def test_delimiter_bearing_config_skips_without_any_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A comma in the profile name would rewrite the HMC record grammar.

    The SKIP must happen before anything is created — a `build_filter` raise
    off a path where a partition already exists is what this test prevents.
    """
    holder: dict[str, str] = {}
    state = await _run_arm(
        monkeypatch,
        {},
        holder,
        config={**_CONFIG, "dedicated_pcie_profile_name": "prof,x"},
    )
    assert state.calls == []


@pytest.mark.asyncio
async def test_environment_outside_envelope_skips_arm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder: dict[str, str] = {}
    responses = _happy_responses(holder, system_model="9080-M9S")
    state = await _run_arm(monkeypatch, responses, holder)
    assert state.cleanup_start is None  # cleanup never ran (fixture not created)
    assert any(r[2] == "SKIP" for r in state.results)
    assert "hmc_list_dedicated_pcie_slots" not in state.tools()


@pytest.mark.asyncio
async def test_no_unassigned_slot_skips_arm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder: dict[str, str] = {}
    responses = _happy_responses(holder, inventory_owner="someone")
    state = await _run_arm(monkeypatch, responses, holder)
    assert state.cleanup_start is None
    skip_rows = [r for r in state.results if r[2] == "SKIP"]
    assert any("slot" in r[1].lower() or "slot" in str(r[3]).lower() for r in skip_rows)


@pytest.mark.asyncio
async def test_configured_drc_absent_from_inventory_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder: dict[str, str] = {}
    responses = _happy_responses(holder)
    state = await _run_arm(
        monkeypatch,
        responses,
        holder,
        config={**_CONFIG, "dedicated_pcie_drc_index": "999"},
    )
    assert state.cleanup_start is None
    assert any("drc_index" in str(r[3]).lower() for r in state.results if r[2] == "SKIP")


@pytest.mark.asyncio
async def test_refused_probe_create_is_a_fail_row_and_the_fixture_proceeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder: dict[str, str] = {}
    responses = _happy_responses(holder)
    state = await _run_arm(monkeypatch, responses, holder)

    # No SKIP is declared for the create-time path any more (ADR 0166): a
    # refusal inside the envelope is a finding.
    probe_row = state.row("create-time dedicated assignment")
    assert probe_row is not None and probe_row[2] == "FAIL"
    # The readback answers HSCL8012 for the probe, so its absence is confirmed.
    assert state.row("create-time probe partition not confirmed absent") is None
    creates = [k for t, k in state.calls if t == "hmc_create_lpar"]
    assert any(not _is_probe(k) for k in creates), "fixture create must have been called"
    assert not any(
        str(k.get("lpar_name_or_uuid", "")).endswith("-createtime")
        for t, k in state.calls
        if t == "hmc_delete_lpar"
    )


def _index_of(state: ScenarioState, predicate: Any) -> int:
    return next(i for i, (t, k) in enumerate(state.calls) if predicate(t, k))


_LOOKUP_LOST = CallFailure("HMCCLIError", "HMCCLIError: connection lost", "", None, False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "probe_description",
    [pytest.param(_LOOKUP_LOST, id="unread"), pytest.param("", id="unstamped")],
)
async def test_probe_absence_that_cannot_be_confirmed_is_a_recovery_row(
    monkeypatch: pytest.MonkeyPatch, probe_description: Any
) -> None:
    """A failed or unstamped readback cannot rule out a partition the create made."""
    holder: dict[str, str] = {}
    responses = _happy_responses(holder, probe_description=probe_description)
    state = await _run_arm(monkeypatch, responses, holder)

    check_row = state.row("create-time probe partition not confirmed absent")
    assert check_row is not None and check_row[2] == "FAIL"
    assert "MANUAL RECOVERY REQUIRED" in str(check_row[3])
    assert "-createtime" in str(check_row[3])
    creates = [k for t, k in state.calls if t == "hmc_create_lpar"]
    assert any(not _is_probe(k) for k in creates), "fixture create must have been called"


@pytest.mark.asyncio
async def test_create_time_assignment_is_verified_and_removed_before_the_fixture_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder: dict[str, str] = {}
    responses = _happy_responses(holder, probe_exists=True)
    _probe_create_succeeds(responses)

    state = await _run_arm(
        monkeypatch, responses, holder, statuses={"hmc_create_lpar": "PASS"}
    )

    row = state.row("create-time assignment profile readback")
    assert row is not None and row[2] == "PASS"
    probe_removal = _index_of(
        state,
        lambda t, k: t == "hmc_run_command"
        and "io_slots-" in k["cmd"]
        and "-createtime" in k["cmd"],
    )
    probe_delete = _index_of(
        state, lambda t, k: t == "hmc_delete_lpar" and k["lpar_name_or_uuid"] == "probe-uuid"
    )
    fixture_create = _index_of(
        state, lambda t, k: t == "hmc_create_lpar" and not _is_probe(k)
    )
    assert probe_removal < probe_delete < fixture_create
    # Final cleanup has only the fixture left to remove.
    deletes = [k for t, k in state.cleanup_calls() if t == "hmc_delete_lpar"]
    assert [k["lpar_name_or_uuid"] for k in deletes] == ["fixture-uuid"]


@pytest.mark.asyncio
async def test_probe_whose_assignment_did_not_land_is_deleted_without_a_removal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder: dict[str, str] = {}
    responses = _happy_responses(holder, probe_exists=True)
    _probe_create_succeeds(responses, assignment_lands=False)

    state = await _run_arm(
        monkeypatch, responses, holder, statuses={"hmc_create_lpar": "PASS"}
    )

    row = state.row("create-time assignment profile readback")
    assert row is not None and row[2] == "FAIL"
    assert not [c for c in state.commands() if "io_slots-" in c and "-createtime" in c]
    assert any(
        t == "hmc_delete_lpar" and k["lpar_name_or_uuid"] == "probe-uuid"
        for t, k in state.calls
    )
    assert any(t == "hmc_create_lpar" and not _is_probe(k) for t, k in state.calls)


@pytest.mark.asyncio
async def test_unfinished_probe_cleanup_blocks_the_fixture_and_is_retried_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe's profile is unreadable at its ST30 cleanup, readable after.

    The fixture must not be created while the probe may still list the slot,
    and `probe_created` must stay set so the final cleanup retries the probe.
    """
    holder: dict[str, str] = {}
    responses = _happy_responses(holder, probe_exists=True)
    _probe_create_succeeds(responses)
    probe_reads = {"n": 0}

    def status(_tool: str, kwargs: dict[str, Any], _index: int) -> str:
        cmd = kwargs.get("cmd", "")
        if "-F io_slots" in cmd and "-createtime" in cmd:
            probe_reads["n"] += 1
            return "FAIL" if probe_reads["n"] == 2 else "PASS"
        return "PASS"

    state = await _run_arm(
        monkeypatch,
        responses,
        holder,
        statuses={"hmc_create_lpar": "PASS", "hmc_run_command": status},
    )

    row = state.row("probe profile unreadable")
    assert row is not None and row[2] == "FAIL"
    assert not any(t == "hmc_create_lpar" and not _is_probe(k) for t, k in state.calls)
    deletes = [k for t, k in state.cleanup_calls() if t == "hmc_delete_lpar"]
    assert [k["lpar_name_or_uuid"] for k in deletes] == ["probe-uuid"]
    assert [c for c in state.cleanup_commands() if "io_slots-" in c]


@pytest.mark.asyncio
async def test_probe_carrying_a_foreign_token_is_never_deleted_and_blocks_the_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder: dict[str, str] = {}
    responses = _happy_responses(
        holder,
        probe_description="[hmcpctl owner:hmcpctl created:2026-09-02] [caller someone-else]",
    )
    _probe_create_succeeds(responses)

    state = await _run_arm(
        monkeypatch, responses, holder, statuses={"hmc_create_lpar": "PASS"}
    )

    rows = [r for r in state.results if "probe run-marker mismatch" in r[1]]
    assert [r[2] for r in rows] == ["FAIL", "FAIL"], "ST30 attempt plus one retry"
    assert "MANUAL RECOVERY REQUIRED" in str(rows[0][3])
    assert "hmc_delete_lpar" not in state.tools()
    assert not [c for c in state.commands() if "io_slots-" in c]
    assert not any(t == "hmc_create_lpar" and not _is_probe(k) for t, k in state.calls)


@pytest.mark.asyncio
async def test_st31_assigns_through_the_operation_and_issues_no_raw_add(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder: dict[str, str] = {}
    state = await _run_arm(monkeypatch, _happy_responses(holder), holder)

    calls = [k for t, k in state.calls if t == "hmc_assign_dedicated_pcie_slot"]
    assert len(calls) == 1
    assert calls[0]["drc_index"] == _DRC
    assert calls[0]["lpar_name_or_uuid"].startswith("live-")
    op_row = state.row("hmc_assign_dedicated_pcie_slot")
    assert op_row is not None and op_row[2] == "PASS"
    readback = state.row("profile io_slots readback (post-assign)")
    assert readback is not None and readback[2] == "PASS"
    # Only ST33's reassign issues the raw grammar.
    assert len([c for c in state.commands() if "io_slots+" in c]) == 1


@pytest.mark.asyncio
async def test_happy_path_removes_slot_then_deletes_by_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder: dict[str, str] = {}
    state = await _run_arm(monkeypatch, _happy_responses(holder), holder)

    order = [
        index
        for index, (tool, kwargs) in enumerate(state.cleanup_calls())
        if tool == "hmc_delete_lpar"
        or (tool == "hmc_run_command" and "io_slots-" in kwargs["cmd"])
    ]
    assert order, "expected a cleanup removal and a delete"
    assert state.cleanup_calls()[order[-1]][0] == "hmc_delete_lpar"

    deletes = [k for t, k in state.calls if t == "hmc_delete_lpar"]
    assert len(deletes) == 1, "the probe created nothing, so only the fixture is deleted"
    assert deletes[0]["lpar_name_or_uuid"] == "fixture-uuid"
    assert "ownership_override" not in deletes[0]


@pytest.mark.asyncio
async def test_foreign_caller_token_blocks_every_cleanup_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder: dict[str, str] = {}
    responses = _happy_responses(
        holder,
        description="[hmcpctl owner:hmcpctl created:2026-09-02] [caller someone-else]",
    )
    state = await _run_arm(monkeypatch, responses, holder)

    assert "hmc_delete_lpar" not in state.cleanup_tools()
    assert not [c for c in state.cleanup_commands() if "io_slots-" in c]
    row = state.row("run-marker mismatch")
    assert row is not None and row[2] == "FAIL"
    assert "MANUAL RECOVERY REQUIRED" in str(row[3])


@pytest.mark.asyncio
async def test_unreadable_identity_blocks_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard A: caller_token is None when the description read fails."""
    holder: dict[str, str] = {}
    state = await _run_arm(
        monkeypatch,
        _happy_responses(holder),
        holder,
        statuses={"hmc_get_lpar_description": "FAIL"},
    )

    assert "hmc_delete_lpar" not in state.cleanup_tools()
    row = state.row("run-marker mismatch")
    assert row is not None and row[2] == "FAIL"
    assert "MANUAL RECOVERY REQUIRED" in str(row[3])


@pytest.mark.asyncio
async def test_uuid_drift_between_reads_blocks_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard A: UUID changes between fixture create and cleanup read."""
    holder: dict[str, str] = {}
    # The fixture is created with fixture-uuid; the second hmc_get_lpar call
    # (Guard A's read inside _read_dedicated_state) returns other-uuid.
    responses = _happy_responses(holder, uuids=["fixture-uuid", "other-uuid"])
    state = await _run_arm(monkeypatch, responses, holder)

    assert "hmc_delete_lpar" not in state.cleanup_tools()
    row = state.row("uuid mismatch")
    assert row is not None and row[2] == "FAIL"
    assert "MANUAL RECOVERY REQUIRED" in str(row[3])


@pytest.mark.asyncio
async def test_no_uuid_resolved_blocks_arm_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ST30: no UUID means no hardware mutation and no stranded slot."""
    holder: dict[str, str] = {}
    state = await _run_arm(monkeypatch, _happy_responses(holder, uuid_value=None), holder)

    # No io_slots mutations were issued
    assert not any("io_slots+" in cmd for cmd in state.commands())
    # The fixture was still created and cleaned up (Guard A refuses the delete)
    assert state.cleanup_start is not None


@pytest.mark.asyncio
async def test_ownership_stamp_not_landed_blocks_hardware_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ST30: ownership_stamped=False means the caller segment was also lost.

    Guard A reads None from hmc_get_lpar_description and refuses the delete.
    No io_slots mutations must occur.
    """
    holder: dict[str, str] = {}
    # ownership_stamped=False → the caller segment is lost too (per lifecycle.py)
    responses = _happy_responses(
        holder,
        ownership_stamped=False,
        description="[hmcpctl owner:hmcpctl created:2026-09-02]",  # no caller segment
    )
    state = await _run_arm(monkeypatch, responses, holder)

    # No chsyscfg mutations (no io_slots+ or io_slots-)
    assert not any(
        ("io_slots+" in cmd or "io_slots-" in cmd) for cmd in state.commands()
    )

    # Guard A refuses the delete, with a recovery row
    assert "hmc_delete_lpar" not in state.cleanup_tools()
    row = state.row("run-marker mismatch")
    assert row is not None and row[2] == "FAIL"
    assert "MANUAL RECOVERY REQUIRED" in str(row[3])


@pytest.mark.asyncio
async def test_profile_drift_to_unknown_value_blocks_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard B: io_slots is neither the baseline nor the value this run applied."""
    holder: dict[str, str] = {}

    # Full run: assign+unassign+reassign completes. applied_io_slots=_ASSIGNED.
    # Guard A's profile read returns a third value (neither baseline "none" nor
    # applied _ASSIGNED). Guard B must emit "profile drift" FAIL and refuse the
    # delete. The key: apply the slots model normally (so applied_io_slots gets
    # set), but switch to a third value starting from profile read #6 (Guard A).
    profile_read_count = {"n": 0}
    slots = {"io_slots": "none"}

    def run_cmd_third_value(kwargs: dict[str, Any], _index: int) -> str:
        cmd = kwargs["cmd"]
        if cmd == "lshmc -V":
            return _ADMITTED_VERSION
        if "-r sys " in cmd and "type_model" in cmd:
            return _ADMITTED_MODEL
        if "io_slots+" in cmd:
            slots["io_slots"] = _ASSIGNED
            return ""
        if "io_slots-" in cmd:
            slots["io_slots"] = "none"
            return ""
        # Profile reads: return normally for reads 1–5, then "99999999//0" from #6 on.
        # Read #6 is Guard A's profile read in the full happy path.
        profile_read_count["n"] += 1
        if profile_read_count["n"] >= 6:
            return "99999999//0"
        return slots["io_slots"]

    responses = _happy_responses(holder, run_command=run_cmd_third_value)
    state = await _run_arm(monkeypatch, responses, holder)

    assert "hmc_delete_lpar" not in state.cleanup_tools()
    row = state.row("profile drift")
    assert row is not None and row[2] == "FAIL"
    assert "MANUAL RECOVERY REQUIRED" in str(row[3])


@pytest.mark.asyncio
async def test_removal_does_not_restore_baseline_blocks_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard B: slot is removed but post-removal read does not equal baseline."""
    holder: dict[str, str] = {}

    # The io_slots- command returns PASS but the model stays at _ASSIGNED
    slots = {"io_slots": "none"}

    def run_cmd_stuck(kwargs: dict[str, Any], _index: int) -> str:
        cmd = kwargs["cmd"]
        if cmd == "lshmc -V":
            return _ADMITTED_VERSION
        if "-r sys " in cmd and "type_model" in cmd:
            return _ADMITTED_MODEL
        if "io_slots+" in cmd:
            slots["io_slots"] = _ASSIGNED
            return ""
        # io_slots- does NOT clear: the removal appears to succeed but the
        # HMC did not actually change the profile
        if "io_slots-" in cmd:
            return ""
        return slots["io_slots"]

    responses = _happy_responses(holder, run_command=run_cmd_stuck)
    state = await _run_arm(monkeypatch, responses, holder)

    assert "hmc_delete_lpar" not in state.cleanup_tools()
    row = state.row("baseline not restored")
    assert row is not None and row[2] == "FAIL"
    assert "MANUAL RECOVERY REQUIRED" in str(row[3])


@pytest.mark.asyncio
async def test_cleanup_removes_slot_whose_assign_response_was_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The operation's write applied and then reported FAIL.

    `assign_dedicated_slot` must read back anyway, so `applied_io_slots`
    records what the profile holds. Restore the early `return` before that
    readback and cleanup can no longer prove the deviation is its own: it
    refuses the delete, and this test goes red.
    """
    holder: dict[str, str] = {}
    state = await _run_arm(
        monkeypatch,
        _happy_responses(holder),
        holder,
        statuses={"hmc_assign_dedicated_pcie_slot": "FAIL"},
    )

    assert [c for c in state.cleanup_commands() if "io_slots-" in c]
    assert "hmc_delete_lpar" in state.cleanup_tools()
    order = [
        index
        for index, (tool, kwargs) in enumerate(state.cleanup_calls())
        if tool == "hmc_delete_lpar"
        or (tool == "hmc_run_command" and "io_slots-" in kwargs["cmd"])
    ]
    assert state.cleanup_calls()[order[-1]][0] == "hmc_delete_lpar"


@pytest.mark.asyncio
async def test_cleanup_refuses_when_confirming_read_was_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ST30's baseline read succeeds; every profile read after it fails.

    So the assign applies, `applied_io_slots` is never set, and the live
    `io_slots` at cleanup is unreadable. Guard B must enter on the baseline
    comparison and refuse — a Guard B gated on `applied_io_slots is not None`
    would skip the branch and delete, and this test goes red.
    """
    holder: dict[str, str] = {}
    state = await _run_arm(
        monkeypatch,
        _happy_responses(holder),
        holder,
        statuses={
            "hmc_run_command": _nth_matching_command_fails("-F io_slots", 2)
        },
    )

    assert "hmc_delete_lpar" not in state.cleanup_tools()
    assert not [c for c in state.cleanup_commands() if "io_slots-" in c]
    row = state.row("profile drift")
    assert row is not None and row[2] == "FAIL"


@pytest.mark.asyncio
async def test_identity_drift_before_delete_blocks_guard_c(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard C: caller token changes between slot removal and deletion.

    The happy path makes exactly three description reads *of the fixture*:
      - ST32's _read_dedicated_state (verify_dedicated_assigned)
      - Guard A's _read_dedicated_state
      - Guard C's _read_dedicated_state
    The foreign stamp must be at index 2 so Guard A sees the correct token
    and Guard C sees the foreign one. Reads of the *probe* name are the
    refused-create absence check and are counted separately, so adding or
    removing one cannot silently shift which read gets the foreign stamp.
    """
    holder: dict[str, str] = {}

    def make_description() -> list[str]:
        # Two matching stamps, then the foreign one on the third read.
        # The index clamps at the last entry, so read 3 onward is the foreign one.
        base = "[hmcpctl owner:hmcpctl created:2026-09-02] [caller {token}]"
        foreign = "[hmcpctl owner:hmcpctl created:2026-09-02] [caller someone-else]"
        return [base, base, foreign]

    descriptions = make_description()
    fixture_reads = {"n": 0}

    def get_description(kwargs: dict[str, Any], _index: int) -> Any:
        if _is_probe_name(kwargs):
            return _NOT_FOUND
        token = holder.get("marker", "")
        entry = descriptions[min(fixture_reads["n"], len(descriptions) - 1)]
        fixture_reads["n"] += 1
        return entry.format(token=token)

    responses = _happy_responses(holder)
    responses["hmc_get_lpar_description"] = get_description

    state = await _run_arm(monkeypatch, responses, holder)

    assert "hmc_delete_lpar" not in state.cleanup_tools()
    row = state.row("identity changed before delete")
    assert row is not None and row[2] == "FAIL"
    assert "MANUAL RECOVERY REQUIRED" in str(row[3])


@pytest.mark.asyncio
async def test_profile_drift_before_delete_blocks_guard_c(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard C: profile io_slots changes after Guard A and Guard B pass.

    A foreign value only on the last profile read means Guard C's comparison
    fails even though Guard A saw the baseline.
    """
    holder: dict[str, str] = {}
    # In the full happy-path run, Guard C's _read_dedicated_state calls
    # _read_profile_io_slots on profile read #13 (1-indexed). Returning a foreign
    # value there while Guard A (#11) and Guard B's confirming read (#12) both see
    # the baseline means only Guard C's comparison fails.
    profile_read_count = {"n": 0}
    slots = {"io_slots": "none"}

    def run_cmd_guard_c_drift(kwargs: dict[str, Any], _index: int) -> str:
        cmd = kwargs["cmd"]
        if cmd == "lshmc -V":
            return _ADMITTED_VERSION
        if "-r sys " in cmd and "type_model" in cmd:
            return _ADMITTED_MODEL
        if "io_slots+" in cmd:
            slots["io_slots"] = _ASSIGNED
            return ""
        if "io_slots-" in cmd:
            slots["io_slots"] = "none"
            return ""
        profile_read_count["n"] += 1
        # Profile read #8 is Guard C's in the full happy-path run:
        # #1 ST30-baseline, #2 ST31-post-assign, #3 ST32-verify,
        # #4 ST33-unassign-confirm, #5 ST33-post-reassign, #6 Guard-A,
        # #7 Guard-B-confirm, #8 Guard-C.
        if profile_read_count["n"] == 8:
            return "foreign-slot//0"
        return slots["io_slots"]

    responses = _happy_responses(holder, run_command=run_cmd_guard_c_drift)
    state = await _run_arm(monkeypatch, responses, holder)

    assert "hmc_delete_lpar" not in state.cleanup_tools()
    row = state.row("profile changed before delete")
    assert row is not None and row[2] == "FAIL"
    assert "MANUAL RECOVERY REQUIRED" in str(row[3])


@pytest.mark.asyncio
async def test_never_assigned_no_cleanup_slot_removal_delete_happens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When io_slots+ is a no-op the profile stays at baseline.

    Guard B must NOT issue an io_slots- (the baseline comparison is satisfied),
    and the delete must still happen.
    """
    holder: dict[str, str] = {}

    def run_cmd_noop_assign(kwargs: dict[str, Any], _index: int) -> str:
        cmd = kwargs["cmd"]
        if cmd == "lshmc -V":
            return _ADMITTED_VERSION
        if "-r sys " in cmd and "type_model" in cmd:
            return _ADMITTED_MODEL
        # io_slots+ does nothing — profile stays "none"
        return "none"

    responses = _happy_responses(holder, run_command=run_cmd_noop_assign)
    state = await _run_arm(monkeypatch, responses, holder)

    # No cleanup slot removal (the profile was never drifted)
    assert not [c for c in state.cleanup_commands() if "io_slots-" in c]
    # The fixture is still deleted
    assert "hmc_delete_lpar" in state.cleanup_tools()


@pytest.mark.asyncio
async def test_run_marker_uniqueness(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two calls to _new_run_marker must return distinct values."""
    m1 = pcie._new_run_marker()
    m2 = pcie._new_run_marker()
    assert m1 != m2
    assert m1.startswith("pcie-")
    assert len(m1) == len("pcie-") + 8


@pytest.mark.asyncio
async def test_fixture_lpar_name_starts_with_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder: dict[str, str] = {}
    state = await _run_arm(monkeypatch, _happy_responses(holder), holder)
    creates = [k for t, k in state.calls if t == "hmc_create_lpar" and not _is_probe(k)]
    assert creates
    assert creates[0]["name"].startswith("live-")


@pytest.mark.asyncio
async def test_arm_records_what_it_created_into_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery reads these four from the results document, not from prose."""
    holder: dict[str, str] = {}
    state = await _run_arm(monkeypatch, _happy_responses(holder), holder)

    assert state.artifacts.pcie_run_marker == holder["marker"]
    assert state.artifacts.pcie_fixture_lpar == f"live-{holder['marker']}"
    assert state.artifacts.pcie_drc_index == _DRC
    assert state.artifacts.pcie_baseline_io_slots is not None


@pytest.mark.asyncio
async def test_a_fixture_abandoned_before_the_baseline_is_still_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run that ends between ST29 and ST30 is the one recovery is for.

    The partition exists and cleanup could not confirm it away, so the marker
    and name have to reach the document even though no baseline was captured.
    """
    holder: dict[str, str] = {}
    state = await _run_arm(monkeypatch, _happy_responses(holder, uuid_value=None), holder)

    assert state.artifacts.pcie_run_marker == holder["marker"]
    assert state.artifacts.pcie_fixture_lpar == f"live-{holder['marker']}"
    assert state.artifacts.pcie_drc_index == _DRC
    assert state.artifacts.pcie_baseline_io_slots is None


@pytest.mark.asyncio
async def test_a_skipped_arm_records_no_pcie_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing was created, so recovery must not be told to look for one."""
    holder: dict[str, str] = {}
    responses = _happy_responses(holder, system_model="9080-M9S")
    state = await _run_arm(monkeypatch, responses, holder)

    assert state.artifacts.pcie_run_marker is None
    assert state.artifacts.pcie_fixture_lpar is None


# ---------------------------------------------------------------------------
# A create whose response was lost has still created the partition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("io_slots", "drc", "expected"),
    [
        ("none", "21010020", False),
        ("21010020//0", "21010020", True),
        ("21010020/none/0", "21010020", True),
        ("21010020//0,21030030//1", "21030030", True),
        # The substring traps: a longer DRC index that merely contains the one
        # under test, and a run of characters spanning the `/` and `,` joins.
        ("210100201//0", "21010020", False),
        ("121010020//0", "21010020", False),
        ("21010//0,20999//1", "0,2099", False),
    ],
)
def test_io_slots_membership_is_by_entry_not_substring(
    io_slots: str, drc: str, expected: bool
) -> None:
    """A DRC index counts as present only as a whole first field of an entry."""
    assert pcie._io_slots_contains(io_slots, drc) is expected


@pytest.mark.asyncio
async def test_fixture_create_reporting_failure_but_creating_is_cleaned_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timed-out fixture create that in fact created must not orphan it.

    The SSH transport raises when its timeout expires, after the HMC has
    already made the partition. Believing a failed create created nothing is
    the belief that leaves a partition behind with no manual-recovery row.
    """
    holder: dict[str, str] = {}
    responses = _happy_responses(holder)
    state = await _run_arm(
        monkeypatch,
        responses,
        holder,
        # Both creates report failure; the description read still finds the
        # fixture carrying this run's marker.
        statuses={"hmc_create_lpar": "FAIL"},
    )
    row = state.row("created a partition")
    assert row is not None and row[2] == "FAIL"
    assert "MANUAL RECOVERY REQUIRED" in str(row[3])
    # The arm still SKIPs, but cleanup ran and deleted what was created.
    assert state.cleanup_start is not None
    assert "hmc_delete_lpar" in state.cleanup_tools()


@pytest.mark.asyncio
async def test_fixture_create_failure_with_no_partition_skips_without_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The readback's other answer: nothing was created, so nothing is cleaned."""
    holder: dict[str, str] = {}
    responses = _happy_responses(holder)
    # The HMC has no partition of either name.
    responses["hmc_get_lpar_description"] = lambda _k, _n: _NOT_FOUND
    state = await _run_arm(
        monkeypatch, responses, holder, statuses={"hmc_create_lpar": "FAIL"}
    )
    assert state.cleanup_start is None
    assert "hmc_delete_lpar" not in [t for t, _ in state.calls]
    skip = state.row("dedicated fixture create")
    assert skip is not None and skip[2] == "SKIP"
    assert "nothing to clean up" in str(skip[3])


@pytest.mark.asyncio
async def test_fixture_create_failure_with_unconfirmed_absence_is_a_recovery_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lookup that failed some other way cannot say nothing was created."""
    holder: dict[str, str] = {}
    responses = _happy_responses(holder)
    responses["hmc_get_lpar_description"] = lambda _k, _n: _LOOKUP_LOST
    state = await _run_arm(
        monkeypatch, responses, holder, statuses={"hmc_create_lpar": "FAIL"}
    )
    row = state.row("fixture partition not confirmed absent")
    assert row is not None and row[2] == "FAIL"
    assert "MANUAL RECOVERY REQUIRED" in str(row[3])
    assert "live-" in str(row[3])
    assert "nothing to clean up" not in " ".join(str(r[3]) for r in state.results)
    assert "hmc_delete_lpar" not in [t for t, _ in state.calls]


@pytest.mark.asyncio
async def test_probe_create_reporting_failure_but_creating_is_cleaned_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe may hold the dedicated slot, so a lost response is worse there."""
    holder: dict[str, str] = {}
    # The probe exists despite its create reporting failure.
    responses = _happy_responses(holder, probe_exists=True)
    state = await _run_arm(monkeypatch, responses, holder)
    row = state.row("create-time probe created a partition despite")
    assert row is not None and row[2] == "FAIL"
    assert "MANUAL RECOVERY REQUIRED" in str(row[3])
    deletes = [k for t, k in state.calls if t == "hmc_delete_lpar"]
    assert any(
        str(k.get("lpar_name_or_uuid", "")).endswith("-createtime") for k in deletes
    ), "the probe partition must be deleted, not just the fixture"


@pytest.mark.asyncio
async def test_foreign_partition_of_the_same_name_is_never_adopted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The readback claims a partition only when it carries this run's marker."""
    holder: dict[str, str] = {}
    responses = _happy_responses(holder)
    responses["hmc_get_lpar_description"] = lambda _k, _n: (
        "[hmcpctl owner:hmcpctl created:2026-09-02] [caller someone-else]"
    )
    state = await _run_arm(
        monkeypatch, responses, holder, statuses={"hmc_create_lpar": "FAIL"}
    )
    assert state.cleanup_start is None
    assert "hmc_delete_lpar" not in [t for t, _ in state.calls]
    # A partition of that name held by someone else rules out one of this run's.
    assert state.row("create-time probe partition not confirmed absent") is None
