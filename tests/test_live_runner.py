"""Lifecycle tests for the executable live integration runner."""

from __future__ import annotations

import ast
import json
import importlib.util
import os
import re
import sys
from pathlib import Path

import pytest

from hmc_mcp.config import HMCConfig
from hmc_mcp import ssh_affinity
from hmc_mcp.server import TOOL_SECURITY


_RUNNER_PATH = Path(__file__).parents[1] / "scripts" / "live_test_runner.py"
sys.path.insert(0, str(_RUNNER_PATH.parent))
from live_test import inventory, lifecycle, results, vmedia  # noqa: E402

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

    async def configure(enabled, application, *, permits=None, authorize=None) -> None:
        assert enabled is True
        # The old assertion here was `application is runner.mcp`. ADR 0041 removed that
        # module-level object, and the identity check had nothing left to compare
        # against — so this asserts the property the runner actually needs instead:
        # the toggle is handed the gates of the policy the runner composed, and that
        # policy grants the escape hatch. Called with neither, `permits=None` would
        # register the tool whatever the policy said and `authorize=None` would leave
        # its handler unwrapped, which is how a live run stops being evidence.
        assert permits is not None and authorize is not None
        assert permits("hmc_run_command") is True
        return None

    monkeypatch.setattr(runner, "configure_arbitrary_command_tool", configure)


def _clear(monkeypatch, name: str) -> None:
    """Delete every casing of *name*, not just the canonical one.

    The runner reads its `HMC_*` variables the way `HMCConfig` does, so a test
    that isolates one spelling does not isolate the variable: an ambient
    `hmc_schema_version` in a developer's shell or on a CI runner reaches the
    code under test and the assertion fails saying nothing about casing (#543).
    """
    for spelling in [k for k in list(os.environ) if k.lower() == name.lower()]:
        monkeypatch.delenv(spelling, raising=False)


def _isolated_environ(monkeypatch) -> None:
    """Give the test its own ``os.environ``, restored on teardown.

    The runner mutates the process environment directly — it injects, and now
    deletes — so a key the code under test creates is not one ``monkeypatch``
    recorded, and teardown cannot take it back. Swapping the mapping itself is
    what keeps a runner test from leaking an `HMC_*` variable into every test
    that runs after it.
    """
    monkeypatch.setattr(os, "environ", dict(os.environ))


def test_schema_preflight_is_explicit_and_actionable(monkeypatch, capsys):
    _clear(monkeypatch, "HMC_SCHEMA_VERSION")
    monkeypatch.setattr(runner, "_load_dotenv", lambda: None)

    with pytest.raises(SystemExit) as exc_info:
        runner._ensure_schema_version()

    assert exc_info.value.code == 1
    assert "Add 'HMC_SCHEMA_VERSION=V1_0'" in capsys.readouterr().out


def test_schema_preflight_accepts_a_case_variant_the_loader_reads(monkeypatch):
    """#543. It must not refuse to start on a value the server will send."""
    _clear(monkeypatch, "HMC_SCHEMA_VERSION")
    monkeypatch.setattr(runner, "_load_dotenv", lambda: None)
    monkeypatch.setenv("hmc_schema_version", "V1_0")

    assert HMCConfig(host="h", user="u", password="p").schema_version == "V1_0"
    runner._ensure_schema_version()  # returns rather than exiting 1


def test_the_iso_allowlist_merge_reaches_the_field_and_is_idempotent(monkeypatch):
    """#543 / ADR 0050. The merged value has to be the one the loader resolves.

    Three post-conditions, because the runner cannot diagnose their absence: it
    prints the allowlist it believes it set, and if a case variant still outranks
    the canonical spelling, ADR 0050 refuses every upload in the run while the
    printed evidence says the host is permitted.
    """
    name = "HMC_ISO_URL_ALLOWLIST"
    _isolated_environ(monkeypatch)
    _clear(monkeypatch, name)
    # Two casings, two *different* values: identical ones would leave an
    # exact-case read and a folded one returning the same string, and the test
    # could not tell which one it was running against.
    monkeypatch.setenv(name, "canonical.example.com")
    monkeypatch.setenv("hmc_iso_url_allowlist", "variant.example.com")

    vmedia._allow_iso_host()

    merged = os.environ[name]
    assert [k for k in os.environ if k.lower() == name.lower()] == [name]
    assert merged.split(",") == ["variant.example.com", vmedia._ISO_HOST]
    assert HMCConfig(host="h", user="u", password="p").iso_url_allowlist == merged

    vmedia._allow_iso_host()
    assert os.environ[name] == merged


def test_the_iso_allowlist_merge_keeps_a_variant_only_operator_entry(monkeypatch):
    """#543 / ADR 0050. The deletion loop must never run on an empty merge.

    This is the case the deletion makes destructive: with only a variant set, an
    exact-case read yields nothing, `entries` becomes the runner's own host
    alone, and the loop then deletes the key that held the operator's ISO
    servers. They would be gone from the run with no diagnostic — the banner
    prints a one-entry allowlist that looks deliberate.
    """
    name = "HMC_ISO_URL_ALLOWLIST"
    _isolated_environ(monkeypatch)
    _clear(monkeypatch, name)
    monkeypatch.setenv("hmc_iso_url_allowlist", "operator.example.com")

    vmedia._allow_iso_host()

    assert os.environ[name].split(",") == ["operator.example.com", vmedia._ISO_HOST]


def test_a_dotenv_entry_never_outranks_a_case_variant_export(monkeypatch, tmp_path):
    """#543. `_bootstrap_config`'s priority 1 beats its priority 3 in any casing.

    The exact-case membership test injected the canonical spelling beside an
    exported variant; a newly created key lands last in `os.environ` order, so
    the committed `.env` won and an operator who exported a lab host ran the
    destructive suite against the one `.env` names.
    """
    _isolated_environ(monkeypatch)
    for name in ("HMC_HOST", "HMC_PASSWORD", "HMC_SCHEMA_VERSION"):
        _clear(monkeypatch, name)
    monkeypatch.setenv("hmc_host", "lab-hmc.example.com")
    env_file = tmp_path / ".env"
    env_file.write_text("HMC_HOST=prod-hmc.example.com\nHMC_SCHEMA_VERSION=V1_0\n")
    monkeypatch.setattr(runner, "_ENV_FILE", env_file)

    runner._load_dotenv()

    # `host` is left to the environment deliberately: a constructor argument
    # outranks every environment source, which is the one precedence this test
    # must not use.
    config = HMCConfig(user="u", password="p")
    assert config.host == "lab-hmc.example.com"
    # A name the environment does not carry in any casing is still injected.
    assert config.schema_version == "V1_0"


def test_a_case_variant_of_an_exact_case_reader_does_not_suppress_its_dotenv_line(
    monkeypatch, tmp_path
):
    """#543. Only the names `HMCConfig` folds may be matched case-blind here.

    `HMC_PROFILE` carries the prefix but is not an `HMCConfig` field:
    `load_profile()` looks it up in `os.environ` directly, so a `hmc_profile`
    export selects no profile. Folding it would let that inert variant suppress
    the `.env` line spelling it canonically — the same silent misrouting this
    sweep closes, running the other way.
    """
    _isolated_environ(monkeypatch)
    for name in ("HMC_PROFILE", "HMC_HOST"):
        _clear(monkeypatch, name)
    monkeypatch.setenv("hmc_profile", "read-by-nothing")
    monkeypatch.setenv("hmc_host", "lab-hmc.example.com")
    env_file = tmp_path / ".env"
    env_file.write_text("HMC_PROFILE=lab\nHMC_HOST=prod-hmc.example.com\n")
    monkeypatch.setattr(runner, "_ENV_FILE", env_file)

    runner._load_dotenv()

    assert os.environ["HMC_PROFILE"] == "lab"
    assert HMCConfig(user="u", password="p").host == "lab-hmc.example.com"


def test_the_already_set_gate_folds_down_like_the_loader(monkeypatch, tmp_path):
    """#543. `_already_set` must decide with the relation the lookup uses.

    Over Unicode `str.lower()` and `str.upper()` are different relations, so the
    direction is load-bearing rather than cosmetic. `hmc_ssh_\u212aey_file`
    (Kelvin sign) lowers onto `ssh_key_file` and is therefore a name `HMCConfig`
    reads, while its upper-fold is not `HMC_SSH_KEY_FILE`. Spelled that way in a
    `.env`, an upper-folding gate calls it a name nothing folds, falls through to
    the exact-case test and injects it — and a newly created key lands last in
    `os.environ` order, so the `.env` value takes the field from the operator's
    export. That is the priority inversion this sweep exists to close, re-opened
    for exactly the names the gate covers.
    """
    _isolated_environ(monkeypatch)
    kelvin = "hmc_ssh_\u212aey_file"
    # The premise, asserted rather than assumed: the loader reads that spelling
    # and an upper-fold does not recognise it.
    assert kelvin.lower() == "hmc_ssh_key_file"
    assert kelvin.upper() != "HMC_SSH_KEY_FILE"

    _clear(monkeypatch, "HMC_SSH_KEY_FILE")
    monkeypatch.setenv("HMC_SSH_KEY_FILE", "/home/op/exported")
    env_file = tmp_path / ".env"
    env_file.write_text(f"{kelvin}=/home/op/from-dotenv\n")
    monkeypatch.setattr(runner, "_ENV_FILE", env_file)

    runner._load_dotenv()

    config = HMCConfig(host="h", user="u", password="p")
    assert config.ssh_key_file == "/home/op/exported"


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
    state = runner.RunState()
    assert await state.call(_ScriptedClient(result=result), "tool") == (
        "PASS",
        expected,
    )


@pytest.mark.asyncio
async def test_call_returns_traceable_failure():
    status, data = await runner.RunState().call(
        _ScriptedClient(error=RuntimeError("transport failed")), "tool"
    )

    assert status == "FAIL"
    assert "RuntimeError: transport failed" in data
    assert "Traceback" in data


def test_expected_hmc_limitation_is_classified_as_skip():
    state = runner.RunState()

    state.record_expected_or_real(
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

    assert results.entries(entries) is entries
    assert results.entries({"entries": entries}) is entries
    assert results.entries("invalid") == []
    assert results.resource(entries[0]) == {"UUID": "nested"}
    assert results.resource({"UUID": "flat"}) == {"UUID": "flat"}


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
async def test_lpar_inventory_calls_all_read_only_affinity_operations(monkeypatch):
    calls = []

    async def scripted_call(_state, _client, tool, **kwargs):
        calls.append((tool, kwargs))
        return "PASS", {}

    monkeypatch.setattr(runner.RunState, "call", scripted_call)
    state = runner.RunState()
    await runner.inventory_lpar_profiles(None, state)

    affinity_calls = [
        call
        for call in calls
        if "memopt" in call[0] or "minimum_affinity_policy" in call[0]
    ]
    assert affinity_calls == [
        (
            "hmc_get_lpar_memopt_score",
            {"system_name_or_uuid": "ltczz386", "lpar_name_or_uuid": "ltczz386-lp3"},
        ),
        ("hmc_list_lpar_memopt_scores", {"system_name_or_uuid": "ltczz386"}),
        ("hmc_get_system_memopt_score", {"system_name_or_uuid": "ltczz386"}),
        ("hmc_plan_lpar_memopt_scores", {"system_name_or_uuid": "ltczz386"}),
        ("hmc_plan_system_memopt_score", {"system_name_or_uuid": "ltczz386"}),
        ("hmc_list_resource_group_memopt_scores", {"system_name_or_uuid": "ltczz386"}),
        ("hmc_plan_resource_group_memopt_scores", {"system_name_or_uuid": "ltczz386"}),
        (
            "hmc_get_minimum_affinity_policy",
            {
                "system_name_or_uuid": "ltczz386",
                "lpar_name_or_uuid": "ltczz386-lp3",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_affinity_live_paths_use_only_current_and_calcscore_commands(monkeypatch):
    commands = []
    outputs = iter(
        [
            "curr_sys_score=70",
            "lpar_name=web,lpar_id=3,curr_lpar_score=60,predicted_lpar_score=80",
            "curr_sys_score=70,predicted_sys_score=85",
        ]
    )

    async def capture(_config, command):
        commands.append(command)
        return next(outputs)

    monkeypatch.setattr(ssh_affinity, "run_hmc_command", capture)
    config = HMCConfig(host="h", user="u", _env_file=None)
    await ssh_affinity.get_system_memopt_score(config, "sys1")
    await ssh_affinity.plan_lpar_memopt_scores(config, "sys1")
    await ssh_affinity.plan_system_memopt_score(config, "sys1")

    assert commands == [
        "lsmemopt -m sys1 -r sys -o currscore",
        "lsmemopt -m sys1 -r lpar -o calcscore",
        "lsmemopt -m sys1 -r sys -o calcscore",
    ]


def test_live_runner_contains_no_executable_optmem_command():
    source = _RUNNER_PATH.read_text(encoding="utf-8")
    assert re.search(r"(?<![\w-])optmem(?![\w-])", source) is None


def _dispatched_tool_names(source: str) -> set[str]:
    """Every tool name ``source`` hands to the runner's ``call`` dispatcher.

    A dispatch whose tool argument is not a string literal cannot be read here,
    and skipping it would silently shrink the guard's coverage, so it fails
    instead.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "call"):
            continue
        tool = node.args[1] if len(node.args) > 1 else None
        if not (isinstance(tool, ast.Constant) and isinstance(tool.value, str)):
            raise AssertionError(
                f"line {node.lineno}: call() dispatches a tool name this guard "
                "cannot read — pass a string literal"
            )
        names.add(tool.value)
    return names


def test_every_dispatched_tool_name_is_registered():
    """The runner is a mirror of the tool registry; a removed tool must not linger."""
    dispatched = set().union(
        *(
            _dispatched_tool_names(Path(module.__file__).read_text(encoding="utf-8"))
            for module in (inventory, lifecycle, vmedia)
        )
    )

    assert dispatched, "no dispatches found — the guard would pass vacuously"
    assert sorted(dispatched - set(TOOL_SECURITY)) == []


def test_dispatch_guard_reports_a_tool_missing_from_the_registry():
    """The guard bites: a dispatch of an unregistered name is reported, not ignored."""
    source = (
        "async def workflow(client):\n"
        '    await state.call(client, "hmc_list_lpars")\n'
        '    await state.call(client, "hmc_list_password_policies")\n'
    )

    assert _dispatched_tool_names(source) - set(TOOL_SECURITY) == {
        "hmc_list_password_policies"
    }


def test_dispatch_guard_refuses_a_tool_name_it_cannot_read():
    source = "async def workflow(client, tool):\n    await state.call(client, tool)\n"

    with pytest.raises(AssertionError, match="cannot read"):
        _dispatched_tool_names(source)


@pytest.mark.asyncio
async def test_main_uses_fresh_state_for_repeated_runs(monkeypatch, tmp_path):
    _isolate_runner(monkeypatch)
    seen_states = []
    initial_system_uuids = []

    async def fake_subtask(_client, state):
        seen_states.append(state)
        initial_system_uuids.append(state.context.system_uuid)
        state.context.system_uuid = "first-run-only"
        state.record(0, "fake", "PASS", {})

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
        state.record(0, "fake", "FAIL", "expected failure")

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

    async def scripted_call(_state, _client, tool, **kwargs):
        calls.append((tool, kwargs))
        if tool == "hmc_list_virtual_switches":
            return "PASS", [{"Resource": {"SwitchID": "7"}}]
        if tool == "hmc_list_virtual_networks" and len(calls) < 7:
            return "PASS", [{"Resource": {"NetworkVLANID": "3000"}}]
        return "PASS", {}

    monkeypatch.setattr(runner.RunState, "call", scripted_call)
    state = runner.RunState()

    await runner.inventory_network(None, state)
    await runner.mutate_virtual_networking(None, state)

    create_call = next(
        item for item in calls if item[0] == "hmc_create_virtual_network"
    )
    assert state.context.test_vswitch_id == 7
    assert state.context.test_vlan_id == 3001
    assert create_call[1]["vlan_id"] == 3001
    assert create_call[1]["virtual_switch_id"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("workflow", "configure", "expected_tool"),
    [
        (
            runner.mutate_virtual_networking,
            lambda _context: None,
            "hmc_create_virtual_network",
        ),
        (
            runner.validate_provisioning_dry_run,
            lambda context: setattr(context, "test_vlan_id", 100),
            "hmc_provision_lpar (dry_run)",
        ),
        (
            runner.exercise_storage_provisioning,
            lambda _context: None,
            "pre-flight check",
        ),
    ],
)
async def test_mutating_workflows_stop_when_inventory_context_is_missing(
    monkeypatch, workflow, configure, expected_tool
):
    calls = []

    async def unexpected_call(_state, _client, tool, **kwargs):
        calls.append((tool, kwargs))
        return "PASS", {}

    monkeypatch.setattr(runner.RunState, "call", unexpected_call)
    state = runner.RunState()
    configure(state.context)

    await workflow(None, state)

    assert calls == []
    matching = [result for result in state.results if result["tool"] == expected_tool]
    assert matching
    assert matching[0]["status"] in {"FAIL", "SKIP"}


@pytest.mark.asyncio
async def test_lpar_lifecycle_sequences_create_power_and_cleanup(monkeypatch):
    calls = []

    async def scripted_call(_state, _client, tool, **kwargs):
        calls.append((tool, kwargs))
        if tool == "hmc_create_lpar":
            return "PASS", {"UUID": "scratch-uuid"}
        if tool in {"hmc_power_on_lpar", "hmc_power_off_lpar"}:
            return "PASS", {"UUID": "job-uuid"}
        return "PASS", {}

    monkeypatch.setattr(runner.RunState, "call", scripted_call)
    state = runner.RunState()
    state.context.system_uuid = "system-uuid"

    await runner.exercise_lpar_lifecycle(None, state)

    assert [tool for tool, _ in calls] == [
        "hmc_create_lpar",
        "hmc_get_lpar",
        "hmc_modify_lpar",
        "hmc_lpar_summary",
        "hmc_power_on_lpar",
        "hmc_power_off_lpar",
        "hmc_delete_lpar",
        "hmc_list_lpars",
    ]
    assert state.context.scratch_uuid is None
    assert state.context.job_uuid_sample == "job-uuid"


@pytest.mark.parametrize(
    "baseline",
    ["web tier, prod", "owner=alice", "em—dash", "desc\x00bad"],
)
def test_unrestorable_description_names_the_reason(baseline):
    """A baseline the CLI cannot round-trip is reported, not silently retried.

    The runner defers to the server's validator, so the ``-i`` record grammar
    of ADR 0045 skips the restore instead of failing it.
    """
    reason = lifecycle._unrestorable_description(baseline)
    assert isinstance(reason, str) and reason


@pytest.mark.parametrize("baseline", ["", "plain text", "[hmc-mcp owner:a created:x]"])
def test_restorable_description_is_not_blocked(baseline):
    """An ordinary baseline description is restored, not skipped."""
    assert lifecycle._unrestorable_description(baseline) is None


@pytest.mark.asyncio
async def test_lpar_property_workflow_skips_an_unrestorable_description(monkeypatch):
    """A baseline carrying a record delimiter is skipped rather than rewritten."""
    calls = []

    async def scripted_call(_state, _client, tool, **kwargs):
        calls.append((tool, kwargs))
        if tool == "hmc_run_command":
            return "PASS", "aixlinux"
        return "PASS", {}

    monkeypatch.setattr(runner.RunState, "call", scripted_call)
    state = runner.RunState()
    state.context.lp3_baseline["description"] = "web tier, prod"

    await runner.mutate_lpar_properties(None, state)

    descriptions = [
        kwargs["description"]
        for tool, kwargs in calls
        if tool == "hmc_set_lpar_description"
    ]
    assert descriptions == ["MCP live-test probe R2 safe to clear"]


@pytest.mark.asyncio
async def test_lpar_property_workflow_restores_description(monkeypatch):
    calls = []

    async def scripted_call(_state, _client, tool, **kwargs):
        calls.append((tool, kwargs))
        if tool == "hmc_run_command":
            return "PASS", "aixlinux"
        if tool == "hmc_set_lpar_msp":
            return "FAIL", "only valid for a VIOS partition"
        if tool == "hmc_get_lpar_proc_compat":
            return "PASS", {"desired": "POWER10"}
        return "PASS", {}

    monkeypatch.setattr(runner.RunState, "call", scripted_call)
    state = runner.RunState()
    state.context.lp3_baseline["description"] = "original description"

    await runner.mutate_lpar_properties(None, state)

    descriptions = [
        kwargs["description"]
        for tool, kwargs in calls
        if tool == "hmc_set_lpar_description"
    ]
    assert descriptions == [
        "MCP live-test probe R2 safe to clear",
        "original description",
    ]
    proc_set = next(
        kwargs for tool, kwargs in calls if tool == "hmc_set_lpar_proc_compat"
    )
    assert proc_set["mode"] == "POWER10"


@pytest.mark.asyncio
async def test_final_restore_replays_baseline_and_audits(monkeypatch):
    calls = []

    async def scripted_call(_state, _client, tool, **kwargs):
        calls.append((tool, kwargs))
        if tool == "hmc_get_lpar_proc_compat":
            return "PASS", {"curr": "POWER9"}
        return "PASS", {}

    monkeypatch.setattr(runner.RunState, "call", scripted_call)
    state = runner.RunState()
    state.context.lp3_baseline["description"] = "baseline"

    await runner.restore_lpar_baseline(None, state)

    assert (
        next(kwargs for tool, kwargs in calls if tool == "hmc_set_lpar_description")[
            "description"
        ]
        == "baseline"
    )
    assert (
        next(kwargs for tool, kwargs in calls if tool == "hmc_set_lpar_proc_compat")[
            "mode"
        ]
        == "POWER9"
    )
    assert [tool for tool, _ in calls][-2:] == [
        "hmc_run_command",
        "hmc_lpar_summary",
    ]
