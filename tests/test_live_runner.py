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
    assert json.loads(second_path.read_text())["context"]["system_uuid"] == "first-run-only"


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
