"""Lifecycle tests for the executable live integration runner."""

from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path

import pytest


_RUNNER_PATH = Path(__file__).parents[1] / "scripts" / "live_test_runner.py"
_SPEC = importlib.util.spec_from_file_location("hmc_live_test_runner", _RUNNER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
runner = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = runner
_SPEC.loader.exec_module(runner)


class _FakeClient:
    def __init__(self, _server):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _ToolResult:
    def __init__(self, *, data=None, content=None):
        self.data = data
        self.content = content or []


class _TextBlock:
    def __init__(self, text):
        self.text = text


class _ScriptedClient:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    async def call_tool(self, _tool, _kwargs):
        if self.error is not None:
            raise self.error
        return self.result


def _isolate_runner(monkeypatch) -> None:
    monkeypatch.setattr(runner, "Client", _FakeClient)

    async def register() -> None:
        return None

    monkeypatch.setattr(runner, "register_arbitrary_command_tool", register)


def test_schema_preflight_is_explicit_and_actionable(monkeypatch, capsys):
    monkeypatch.delenv("HMC_SCHEMA_VERSION", raising=False)
    monkeypatch.setattr(runner, "_load_dotenv", lambda: None)

    with pytest.raises(SystemExit) as exc_info:
        runner._ensure_schema_version()

    assert exc_info.value.code == 1
    assert "Add 'HMC_SCHEMA_VERSION=V1_0'" in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (_ToolResult(data={"UUID": "one"}), {"UUID": "one"}),
        (_ToolResult(content=[_TextBlock('[{"UUID": "two"}]')]), [{"UUID": "two"}]),
        (_ToolResult(content=[_TextBlock("plain text")]), "plain text"),
    ],
)
async def test_call_normalizes_fastmcp_result_shapes(result, expected):
    assert await runner.call(_ScriptedClient(result=result), "tool") == (
        "PASS",
        expected,
    )


@pytest.mark.asyncio
async def test_call_returns_traceable_failure():
    status, data = await runner.call(
        _ScriptedClient(error=RuntimeError("transport failed")), "tool"
    )

    assert status == "FAIL"
    assert "RuntimeError: transport failed" in data
    assert "Traceback" in data


def test_expected_hmc_limitation_is_classified_as_skip():
    state = runner.RunState()

    runner._record_expected_or_real(
        state,
        5,
        "optional_tool",
        "FAIL",
        "HTTP 406 Not Acceptable",
        ["406"],
        "feature unavailable",
    )

    assert state.results[0]["status"] == "SKIP"
    assert state.results[0]["note"] == "feature unavailable"


def test_result_helpers_preserve_resource_shapes():
    entries = [{"Resource": {"UUID": "nested"}}]

    assert runner._entries(entries) is entries
    assert runner._entries({"entries": entries}) is entries
    assert runner._entries("invalid") == []
    assert runner._resource(entries[0]) == {"UUID": "nested"}
    assert runner._resource({"UUID": "flat"}) == {"UUID": "flat"}


def test_restore_context_restores_identifiers_and_baseline(tmp_path):
    results_path = tmp_path / "previous.json"
    results_path.write_text(
        json.dumps(
            {
                "context": {
                    "system_uuid": "system-1",
                    "vios_uuid": "vios-1",
                    "lp3_baseline": {"description": "original"},
                }
            }
        )
    )
    state = runner.RunState()

    runner._restore_ctx_from_results(state, str(results_path))

    assert state.context.system_uuid == "system-1"
    assert state.context.vios_uuid == "vios-1"
    assert state.context.lp3_baseline == {"description": "original"}


def test_live_context_has_no_mapping_facade():
    context = runner.LiveTestContext()

    assert not hasattr(context, "__getitem__")
    assert not hasattr(context, "get")


def test_numeric_dispatch_uses_intent_revealing_workflow_names():
    assert runner.SUBTASKS[0] is runner.capture_lpar_baseline
    assert runner.SUBTASKS[2] is runner.inventory_network
    assert runner.SUBTASKS[9] is runner.mutate_virtual_networking
    assert runner.SUBTASKS[15] is runner.restore_lpar_baseline


@pytest.mark.asyncio
async def test_main_uses_fresh_state_for_repeated_runs(monkeypatch, tmp_path):
    _isolate_runner(monkeypatch)
    seen_states = []
    initial_system_uuids = []

    async def fake_subtask(_client, state):
        seen_states.append(state)
        initial_system_uuids.append(state.context.system_uuid)
        state.context.system_uuid = "first-run-only"
        runner.record(state, 0, "fake", "PASS", {})

    monkeypatch.setattr(runner, "SUBTASKS", {0: fake_subtask})
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"

    assert await runner.main(results_path=str(first_path)) == 0
    seen_states[0].context.system_uuid = "mutated-after-run"
    assert await runner.main(results_path=str(second_path)) == 0

    assert seen_states[0] is not seen_states[1]
    assert initial_system_uuids == [None, None]
    assert len(seen_states[1].results) == 1
    assert (
        json.loads(second_path.read_text())["context"]["system_uuid"]
        == "first-run-only"
    )


@pytest.mark.asyncio
async def test_main_returns_failure_and_persists_results(monkeypatch, tmp_path):
    _isolate_runner(monkeypatch)

    async def failing_subtask(_client, state):
        runner.record(state, 0, "fake", "FAIL", "expected failure")

    monkeypatch.setattr(runner, "SUBTASKS", {0: failing_subtask})
    results_path = tmp_path / "results.json"

    assert await runner.main(results_path=str(results_path)) == 1
    saved = json.loads(results_path.read_text())
    assert saved["results"][0]["status"] == "FAIL"


@pytest.mark.asyncio
async def test_main_rejects_unknown_numeric_workflow(monkeypatch, tmp_path):
    _isolate_runner(monkeypatch)
    results_path = tmp_path / "unknown.json"

    assert await runner.main(999, str(results_path)) == 1

    saved = json.loads(results_path.read_text())
    assert saved["results"][0]["tool"] == "runner"
    assert saved["results"][0]["data"] == "Unknown sub-task 999"


@pytest.mark.asyncio
async def test_network_inventory_hands_identifiers_to_mutation(monkeypatch):
    calls = []

    async def scripted_call(_client, tool, **kwargs):
        calls.append((tool, kwargs))
        if tool == "hmc_list_virtual_switches":
            return "PASS", [{"Resource": {"SwitchID": "7"}}]
        if tool == "hmc_list_virtual_networks" and len(calls) < 7:
            return "PASS", [{"Resource": {"NetworkVLANID": "3000"}}]
        return "PASS", {}

    monkeypatch.setattr(runner, "call", scripted_call)
    state = runner.RunState()

    await runner.inventory_network(None, state)
    await runner.mutate_virtual_networking(None, state)

    create_call = next(
        item for item in calls if item[0] == "hmc_create_virtual_network"
    )
    assert state.context.test_vswitch_id == 7
    assert state.context.test_vlan_id == 3001
    assert create_call[1]["vlan_id"] == 3001
    assert create_call[1]["vswitch_id"] == 7
