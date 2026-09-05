"""Lifecycle tests for the executable live integration runner."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from hmc_mcp.config import HMCConfig
from hmc_mcp.server import TOOL_SECURITY
from hmc_mcp.ssh import affinity as ssh_affinity

_RUNNER_PATH = Path(__file__).parents[1] / "scripts" / "live_test_runner.py"
sys.path.insert(0, str(_RUNNER_PATH.parent))
from live_test import (  # noqa: E402
    connectivity,
    escape_hatch,
    inventory,
    lpar,
    metrics,
    network,
    pcie,
    profiles,
    provisioning,
    results,
    storage,
    users,
    vmedia,
)

LIVE_WORKFLOW_MODULES = (
    connectivity,
    escape_hatch,
    inventory,
    lpar,
    metrics,
    network,
    pcie,
    profiles,
    provisioning,
    storage,
    users,
    vmedia,
)

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


def test_sriov_baseline_helpers_require_healthy_adapter() -> None:
    """Baseline predicates reject wrong mode/availability and accept healthy data."""
    assert pcie._adapter_is_healthy(
        {"items": [{"adapter_id": 17, "mode": "sriov", "availability": "1"}]}, 17
    )
    assert not pcie._adapter_is_healthy(
        {"items": [{"adapter_id": 17, "mode": "ded", "availability": "1"}]}, 17
    )


def test_sriov_baseline_helpers_compute_capacity_and_configuration() -> None:
    """Capacity and clean-port predicates handle unconfigured rows and reject malformed data."""
    data = {
        "items": [
            {"capacity_percent": "25", "availability": "1"},
            {"capacity_percent": "bad", "availability": "1"},
            {"capacity_percent": "50", "availability": "unconfigured"},
        ]
    }
    with pytest.raises(ValueError, match="row 1.*capacity_percent"):
        pcie._available_capacity(data)
    assert not pcie._logical_port_is_configured({"items": []}, 917003)
    assert pcie._logical_port_is_configured(
        {"items": [{"logical_port_id": 917003, "availability": "1"}]},
        917003,
    )


@pytest.mark.asyncio
async def test_sriov_orchestrator_runs_phases_in_order_and_cleans_up() -> None:
    """A successful round trip invokes every phase and always reaches cleanup."""
    calls: list[str] = []

    def phase(name: str):
        def run(*_args) -> bool:
            calls.append(name)
            return True

        return run

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(
            pcie, "capture_sriov_baseline", AsyncMock(side_effect=phase("baseline"))
        )
        monkeypatch.setattr(
            pcie, "assign_sriov_to_lp3", AsyncMock(side_effect=phase("assign"))
        )
        monkeypatch.setattr(
            pcie, "verify_sriov_assigned", AsyncMock(side_effect=phase("verify"))
        )
        monkeypatch.setattr(
            pcie, "unassign_sriov_from_lp3", AsyncMock(side_effect=phase("unassign"))
        )
        monkeypatch.setattr(
            pcie, "reassign_sriov_to_lp3", AsyncMock(side_effect=phase("reassign"))
        )
        monkeypatch.setattr(
            pcie, "cleanup_sriov", AsyncMock(side_effect=phase("cleanup"))
        )

        class State:
            def skip(self, *_args) -> None:
                raise AssertionError("successful orchestration must not skip a phase")

        await pcie.exercise_sriov_assignment(object(), State())
    finally:
        monkeypatch.undo()

    assert calls == ["baseline", "assign", "verify", "unassign", "reassign", "cleanup"]


@pytest.mark.asyncio
async def test_sriov_orchestrator_stops_after_baseline_but_runs_cleanup() -> None:
    """A failed baseline prevents mutations while preserving the cleanup arm."""
    calls: list[str] = []

    async def baseline(*_args) -> bool:
        calls.append("baseline")
        return False

    async def cleanup(*_args) -> None:
        calls.append("cleanup")

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(pcie, "capture_sriov_baseline", baseline)
        monkeypatch.setattr(pcie, "cleanup_sriov", cleanup)
        for name in (
            "assign_sriov_to_lp3",
            "verify_sriov_assigned",
            "unassign_sriov_from_lp3",
            "reassign_sriov_to_lp3",
        ):
            monkeypatch.setattr(pcie, name, AsyncMock(side_effect=AssertionError(name)))

        class State:
            pass

        await pcie.exercise_sriov_assignment(object(), State())
    finally:
        monkeypatch.undo()

    assert calls == ["baseline", "cleanup"]


@pytest.mark.asyncio
async def test_sriov_orchestrator_skips_mutations_after_assign_failure() -> None:
    """Assignment failure still verifies state, skips later mutations, and cleans up."""
    calls: list[str] = []

    async def baseline(*_args) -> bool:
        calls.append("baseline")
        return True

    async def assign(*_args) -> bool:
        calls.append("assign")
        return False

    async def verify(*_args) -> bool:
        calls.append("verify")
        return True

    async def cleanup(*_args) -> None:
        calls.append("cleanup")

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(pcie, "capture_sriov_baseline", baseline)
        monkeypatch.setattr(pcie, "assign_sriov_to_lp3", assign)
        monkeypatch.setattr(pcie, "verify_sriov_assigned", verify)
        monkeypatch.setattr(pcie, "cleanup_sriov", cleanup)
        monkeypatch.setattr(
            pcie, "unassign_sriov_from_lp3", AsyncMock(side_effect=AssertionError)
        )
        monkeypatch.setattr(
            pcie, "reassign_sriov_to_lp3", AsyncMock(side_effect=AssertionError)
        )

        class State:
            def skip(self, *_args) -> None:
                calls.append("skip")

        await pcie.exercise_sriov_assignment(object(), State())
    finally:
        monkeypatch.undo()

    assert calls == ["baseline", "assign", "verify", "skip", "skip", "cleanup"]


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

    context = runner.LiveTestContext()
    vmedia._allow_iso_host(context)

    merged = os.environ[name]
    assert [k for k in os.environ if k.lower() == name.lower()] == [name]
    assert merged.split(",") == ["variant.example.com", context.iso_host]
    assert HMCConfig(host="h", user="u", password="p").iso_url_allowlist == merged

    vmedia._allow_iso_host(context)
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

    context = runner.LiveTestContext()
    vmedia._allow_iso_host(context)

    assert os.environ[name].split(",") == ["operator.example.com", context.iso_host]


def test_live_context_reads_the_complete_example_and_ignores_exports(
    monkeypatch, tmp_path
) -> None:
    """The checked-in example is a complete, authoritative live-test mapping."""
    example = Path(__file__).parents[1] / ".env.example"
    config_path = tmp_path / ".env"
    config_path.write_text(example.read_text())
    monkeypatch.setenv("LIVE_TEST_SYSTEM_NAME", "ambient-target")

    context = runner.LiveTestContext.from_env_file(config_path)

    assert context.system_name == "example-lt-609-system"
    assert context.sriov_logical_port_id == 917003
    assert context.iso_url == "http://iso.example.test:18090/example-lt-609.iso"
    assert context.protected_lpar_names == (
        "example-lt-609-protected-a",
        "example-lt-609-protected-b",
    )


@pytest.mark.asyncio
async def test_main_rejects_missing_live_test_file_before_creating_mcp(
    monkeypatch, tmp_path
) -> None:
    """Programmatic invocation cannot bypass required local live-test settings."""
    monkeypatch.setattr(runner, "_ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(
        runner, "create_mcp", lambda *_args, **_kwargs: pytest.fail("created MCP")
    )

    assert await runner.main(results_path=str(tmp_path / "results.json")) == 1


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


def test_bootstrap_propagates_unexpected_profile_loader_failure(monkeypatch):
    """Only a configuration rejection authorizes the legacy dotenv fallback."""

    def fail_to_load_profile():
        raise RuntimeError("profile loader defect")

    fallback = pytest.fail
    monkeypatch.setattr("hmc_mcp.config.load_profile", fail_to_load_profile)
    monkeypatch.setattr(runner, "_load_dotenv", fallback)

    with pytest.raises(RuntimeError, match="profile loader defect"):
        runner._bootstrap_config()


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


@pytest.mark.asyncio
async def test_call_reports_unexpected_result_parser_failure(monkeypatch):
    def fail_to_parse(_text):
        raise TypeError("parser bug")

    monkeypatch.setattr(runner.json, "loads", fail_to_parse)

    status, data = await runner.RunState().call(
        _ScriptedClient(result=_ToolResult(content=[_TextBlock("plain text")])), "tool"
    )

    assert status == "FAIL"
    assert "TypeError: parser bug" in data


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


@pytest.mark.parametrize("document", ["not JSON", "[]", '{"context": []}'])
def test_restore_context_reports_expected_results_file_failures(
    tmp_path, capsys, document
):
    results_path = tmp_path / "previous.json"
    results_path.write_text(document)

    runner._restore_ctx_from_results(runner.RunState(), str(results_path))

    assert "Could not restore context" in capsys.readouterr().out


def test_restore_context_propagates_unexpected_restoration_defects(
    tmp_path, monkeypatch
):
    results_path = tmp_path / "previous.json"
    results_path.write_text('{"context": {}}')
    monkeypatch.setattr(
        runner, "asdict", lambda _context: (_ for _ in ()).throw(RuntimeError("defect"))
    )

    with pytest.raises(RuntimeError, match="defect"):
        runner._restore_ctx_from_results(runner.RunState(), str(results_path))


@pytest.mark.parametrize(
    "argv",
    [
        ["--unknown"],
        ["--group"],
        ["--results-file"],
        ["10", "--group", "round2"],
        ["999"],
    ],
)
def test_live_runner_rejects_malformed_arguments(argv):
    with pytest.raises(SystemExit) as error:
        runner._parse_arguments(argv)

    assert error.value.code == 2


def test_live_runner_rejects_arguments_before_bootstrap(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_bootstrap_config",
        lambda: (_ for _ in ()).throw(AssertionError("bootstrap reached")),
    )

    with pytest.raises(SystemExit) as error:
        runner._run_from_arguments(["--unknown"])

    assert error.value.code == 2


def test_live_runner_parses_selection_and_result_defaults():
    assert runner._parse_arguments([]) == runner.RunnerArguments(
        subtask=None,
        group=None,
        results_path="test-results-round2.json",
    )
    assert runner._parse_arguments(["10"]) == runner.RunnerArguments(
        subtask=10,
        group=None,
        results_path="test-results-round2.json",
    )
    assert runner._parse_arguments(["--group", "vmedia"]) == runner.RunnerArguments(
        subtask=None,
        group="vmedia",
        results_path="test-results-vmedia.json",
    )
    assert runner._parse_arguments(
        ["--group", "all", "--results-file", "custom.json"]
    ) == runner.RunnerArguments(
        subtask=None,
        group="all",
        results_path="custom.json",
    )


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
async def test_baseline_capture_runs_cohesive_phases_in_order(monkeypatch):
    events: list[str] = []

    def phase(name):
        async def run(_client, _state):
            events.append(name)

        return run

    monkeypatch.setattr(inventory, "_capture_lpar_properties", phase("properties"))
    monkeypatch.setattr(inventory, "_capture_adapter_topology", phase("adapters"))
    monkeypatch.setattr(inventory, "_capture_vios_identity", phase("vios"))
    monkeypatch.setattr(inventory, "_capture_lpar_cli_dump", phase("cli"))
    monkeypatch.setattr(
        inventory, "_print_baseline_summary", lambda _state: events.append("summary")
    )

    await inventory.capture_lpar_baseline(object(), object())

    assert events == ["properties", "adapters", "vios", "cli", "summary"]


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
            {
                "system_name_or_uuid": "example-lt-609-system",
                "lpar_name_or_uuid": "example-lt-609-lpar",
            },
        ),
        (
            "hmc_list_lpar_memopt_scores",
            {"system_name_or_uuid": "example-lt-609-system"},
        ),
        (
            "hmc_get_system_memopt_score",
            {"system_name_or_uuid": "example-lt-609-system"},
        ),
        (
            "hmc_plan_lpar_memopt_scores",
            {"system_name_or_uuid": "example-lt-609-system"},
        ),
        (
            "hmc_plan_system_memopt_score",
            {"system_name_or_uuid": "example-lt-609-system"},
        ),
        (
            "hmc_list_resource_group_memopt_scores",
            {"system_name_or_uuid": "example-lt-609-system"},
        ),
        (
            "hmc_plan_resource_group_memopt_scores",
            {"system_name_or_uuid": "example-lt-609-system"},
        ),
        (
            "hmc_get_minimum_affinity_policy",
            {
                "system_name_or_uuid": "example-lt-609-system",
                "lpar_name_or_uuid": "example-lt-609-lpar",
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
    config = HMCConfig(host="h", user="u")
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
            raise AssertionError(  # noqa: TRY004 - AssertionError is this guard's contract, asserted at tests/test_live_runner.py:630
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
            for module in LIVE_WORKFLOW_MODULES
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


def test_every_live_workflow_dispatch_has_exactly_client_and_tool_arguments():
    """A duplicated client argument turns a live stage into an immediate TypeError."""
    invalid: list[str] = []
    for module in LIVE_WORKFLOW_MODULES:
        source = Path(module.__file__).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "call"
                and len(node.args) != 2
            ):
                invalid.append(f"{Path(module.__file__).name}:{node.lineno}")

    assert invalid == []


def _configure_vmedia_context(state, values):
    for name, value in values.items():
        setattr(state.context, name, value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("workflow", "context", "expected_tools"),
    [
        (
            runner.vmedia_bootstrap_and_create_repo,
            {},
            [
                "hmc_list_vios",
                "hmc_get_lpar",
                "hmc_list_volume_groups",
                "hmc_create_media_repository",
                "hmc_get_media_repository",
            ],
        ),
        (
            runner.vmedia_short_repo_lifecycle,
            {"vmedia_repo_created": True, "vios_uuid": "vios", "vg_uuid": "vg"},
            [
                "hmc_delete_media_repository",
                "hmc_create_media_repository",
                "hmc_get_media_repository",
                "hmc_list_optical_media",
                "hmc_delete_media_repository",
                "hmc_get_media_repository",
                "hmc_create_media_repository",
            ],
        ),
        (
            runner.vmedia_upload_iso,
            {"vmedia_repo_created": True, "vios_uuid": "vios", "vg_uuid": "vg"},
            [
                "hmc_upload_iso",
                "hmc_list_optical_media",
                "hmc_upload_iso",
                "hmc_list_optical_media",
                "hmc_delete_optical_media",
                "hmc_list_optical_media",
                "hmc_upload_iso",
            ],
        ),
        (
            runner.vmedia_mount_unmount,
            {
                "vmedia_iso_name": "test.iso",
                "vios_uuid": "vios",
                "vg_uuid": "vg",
            },
            [
                "hmc_mount_optical_media",
                "hmc_list_optical_mappings",
                "hmc_delete_optical_media",
                "hmc_unmount_optical_media",
                "hmc_list_optical_mappings",
                "hmc_delete_optical_media",
                "hmc_list_optical_media",
            ],
        ),
        (
            runner.vmedia_boot_verification,
            {
                "vmedia_repo_created": True,
                "vios_uuid": "vios",
                "vg_uuid": "vg",
                "lp3_uuid": "lp3",
            },
            [
                "hmc_upload_iso",
                "hmc_power_off_lpar",
                "hmc_mount_optical_media",
                "hmc_read_lpar_boot_order",
                "hmc_set_lpar_boot_order",
                "hmc_power_on_lpar",
                "hmc_lpar_summary",
                "hmc_power_off_lpar",
                "hmc_unmount_optical_media",
                "hmc_set_lpar_boot_order",
                "hmc_read_lpar_boot_order",
            ],
        ),
        (
            runner.vmedia_mapping_crossvalidation,
            {"vios_uuid": "vios"},
            [
                "hmc_list_storage_mappings",
                "hmc_list_optical_mappings",
                "hmc_list_storage_mappings",
                "hmc_list_optical_mappings",
            ],
        ),
        (
            runner.vmedia_teardown,
            {
                "vmedia_repo_created": True,
                "vios_uuid": "vios",
                "vg_uuid": "vg",
                "lp3_uuid": "lp3",
                "vmedia_orig_boot_order": ["disk"],
            },
            [
                "hmc_set_lpar_boot_order",
                "hmc_list_optical_mappings",
                "hmc_unmount_optical_media",
                "hmc_list_optical_media",
                "hmc_delete_optical_media",
                "hmc_delete_media_repository",
                "hmc_get_media_repository",
                "hmc_list_volume_groups",
            ],
        ),
    ],
)
async def test_vmedia_workflows_execute_their_behavioral_contracts(
    monkeypatch, workflow, context, expected_tools
):
    calls = []
    counts = {}

    async def scripted_call(_state, _client, tool, **kwargs):
        calls.append((tool, kwargs))
        counts[tool] = counts.get(tool, 0) + 1
        if tool == "hmc_list_vios":
            return "PASS", [{"UUID": "vios", "Resource": {"PartitionID": "2"}}]
        if tool == "hmc_get_lpar":
            return "PASS", {"uuid": "lp3"}
        if tool == "hmc_list_volume_groups":
            return "PASS", [{"UUID": "vg", "Resource": {"FreeSpace": "8000"}}]
        if tool == "hmc_get_media_repository":
            return "PASS", {"UUID": "repo"}
        if tool == "hmc_list_optical_media":
            return "PASS", [{"MediaName": "test.iso"}]
        if tool == "hmc_upload_iso":
            return "PASS", {"status": "uploaded", "media_name": "test.iso"}
        if tool == "hmc_mount_optical_media":
            return "PASS", {"mapping_uuid": "mapping"}
        if (
            tool == "hmc_delete_optical_media"
            and workflow is runner.vmedia_mount_unmount
            and counts[tool] == 1
        ):
            return "FAIL", "media is mapped"
        if tool == "hmc_read_lpar_boot_order":
            return "PASS", {"pending_boot_string": "disk,network"}
        if tool == "hmc_list_optical_mappings" and workflow is runner.vmedia_teardown:
            return "PASS", [{"UUID": "mapping"}]
        return "PASS", {}

    monkeypatch.setattr(runner.RunState, "call", scripted_call)
    monkeypatch.setattr(vmedia.Path, "is_file", lambda _path: True)
    state = runner.RunState()
    monkeypatch.setattr(state.iso_http_server, "start", lambda _context: None)
    _configure_vmedia_context(state, context)

    await workflow(None, state)

    assert [tool for tool, _ in calls] == expected_tools
    assert not [result for result in state.results if result["status"] == "FAIL"]


def test_vmedia_behavioral_inventory_covers_every_registered_stage():
    covered = {
        runner.vmedia_bootstrap_and_create_repo,
        runner.vmedia_short_repo_lifecycle,
        runner.vmedia_upload_iso,
        runner.vmedia_mount_unmount,
        runner.vmedia_boot_verification,
        runner.vmedia_mapping_crossvalidation,
        runner.vmedia_teardown,
    }

    assert {
        runner.SUBTASKS[number] for number in runner.SUBTASK_GROUPS["vmedia"]
    } == covered


@pytest.mark.asyncio
async def test_vmedia_boot_failure_still_restores_boot_order_and_unmounts(monkeypatch):
    calls = []

    async def scripted_call(_state, _client, tool, **kwargs):
        calls.append((tool, kwargs))
        if tool == "hmc_upload_iso":
            return "PASS", {"media_name": "test.iso"}
        if tool == "hmc_mount_optical_media":
            return "PASS", {"mapping_uuid": "mapping"}
        if tool == "hmc_read_lpar_boot_order":
            return "PASS", {"pending_boot_string": "disk,network"}
        if tool == "hmc_power_on_lpar":
            return "FAIL", "boot job failed"
        return "PASS", {}

    monkeypatch.setattr(runner.RunState, "call", scripted_call)
    state = runner.RunState()
    monkeypatch.setattr(state.iso_http_server, "start", lambda _context: None)
    _configure_vmedia_context(
        state,
        {
            "vmedia_repo_created": True,
            "vios_uuid": "vios",
            "vg_uuid": "vg",
            "lp3_uuid": "lp3",
        },
    )

    await runner.vmedia_boot_verification(None, state)

    tools = [tool for tool, _ in calls]
    assert "hmc_unmount_optical_media" in tools
    assert tools.count("hmc_set_lpar_boot_order") == 2
    assert state.context.vmedia_mapping_uuid is None
    assert state.context.vmedia_orig_boot_order == []


@pytest.mark.asyncio
async def test_vmedia_teardown_continues_after_orphan_unmount_failure(monkeypatch):
    calls = []

    async def scripted_call(_state, _client, tool, **kwargs):
        calls.append((tool, kwargs))
        if tool == "hmc_list_optical_mappings":
            return "PASS", [{"UUID": "mapping"}]
        if tool == "hmc_unmount_optical_media":
            return "FAIL", "unmount failed"
        if tool == "hmc_list_optical_media":
            return "PASS", [{"MediaName": "test.iso"}]
        return "PASS", {}

    monkeypatch.setattr(runner.RunState, "call", scripted_call)
    state = runner.RunState()
    _configure_vmedia_context(
        state,
        {"vmedia_repo_created": True, "vios_uuid": "vios", "vg_uuid": "vg"},
    )

    await runner.vmedia_teardown(None, state)

    tools = [tool for tool, _ in calls]
    assert "hmc_delete_optical_media" in tools
    assert "hmc_delete_media_repository" in tools
    assert "hmc_list_volume_groups" in tools


@pytest.mark.asyncio
async def test_main_uses_fresh_state_for_repeated_runs(monkeypatch, tmp_path):
    _isolate_runner(monkeypatch)
    seen_states = []
    closed_servers = []
    initial_system_uuids = []

    async def fake_subtask(_client, state):
        seen_states.append(state)
        state.iso_http_server.close = lambda: closed_servers.append(
            state.iso_http_server
        )
        initial_system_uuids.append(state.context.system_uuid)
        state.context.system_uuid = "first-run-only"
        state.record(0, "fake", "PASS", {})

    monkeypatch.setattr(runner, "SUBTASKS", {0: fake_subtask})
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"

    assert (
        await runner.main(
            results_path=str(first_path), context=runner.LiveTestContext()
        )
        == 0
    )
    seen_states[0].context.system_uuid = "mutated-after-run"
    assert (
        await runner.main(
            results_path=str(second_path), context=runner.LiveTestContext()
        )
        == 0
    )

    assert seen_states[0] is not seen_states[1]
    assert initial_system_uuids == [None, None]
    assert len(seen_states[1].results) == 1
    assert closed_servers == [
        seen_states[0].iso_http_server,
        seen_states[1].iso_http_server,
    ]
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

    assert (
        await runner.main(
            results_path=str(results_path), context=runner.LiveTestContext()
        )
        == 1
    )
    saved = json.loads(results_path.read_text())
    assert saved["results"][0]["status"] == "FAIL"


@pytest.mark.asyncio
async def test_main_rejects_unknown_numeric_workflow(monkeypatch, tmp_path):
    _isolate_runner(monkeypatch)
    results_path = tmp_path / "unknown.json"

    assert (
        await runner.main(999, str(results_path), context=runner.LiveTestContext()) == 1
    )

    saved = json.loads(results_path.read_text())
    assert saved["results"][0]["tool"] == "runner"
    assert saved["results"][0]["data"] == "Unknown sub-task 999"


@pytest.mark.asyncio
async def test_connectivity_inventory_forwards_selectors_and_captures_context(
    monkeypatch,
):
    calls = []

    async def scripted_call(_state, _client, tool, **kwargs):
        calls.append((tool, kwargs))
        responses = {
            "hmc_get_console_info": {"uuid": "console-uuid"},
            "hmc_list_systems": [
                {
                    "UUID": "system-uuid",
                    "Resource": {"SystemName": "example-lt-609-system"},
                }
            ],
            "hmc_get_lpar": {"UUID": "lpar-uuid"},
            "hmc_list_vios": [{"UUID": "vios-uuid", "Resource": {"PartitionID": "7"}}],
            "hmc_list_recent_jobs": [{"UUID": "job-uuid"}],
        }
        return "PASS", responses.get(tool, {})

    monkeypatch.setattr(runner.RunState, "call", scripted_call)
    state = runner.RunState()

    await connectivity.inventory_connectivity(None, state)

    assert [tool for tool, _ in calls] == [
        "hmc_get_console_info",
        "hmc_list_systems",
        "hmc_get_system",
        "hmc_list_lpars",
        "hmc_get_lpar",
        "hmc_list_vios",
        "hmc_capacity_report",
        "hmc_find_placement",
        "hmc_get_system",
        "hmc_list_resources",
        "hmc_list_recent_jobs",
        "hmc_system_summary",
        "hmc_lpar_summary",
    ]
    assert calls[2][1] == {"system_name_or_uuid": "example-lt-609-system"}
    assert calls[4][1] == {"lpar_name_or_uuid": "example-lt-609-lpar"}
    assert calls[7][1] == {"desired_memory_mib": 3072}
    assert calls[9][1] == {"resource_type": "LogicalPartition"}
    assert calls[10][1] == {"limit": 10}
    assert state.context.console_uuid == "console-uuid"
    assert state.context.system_uuid == "system-uuid"
    assert state.context.lp3_uuid == "lpar-uuid"
    assert state.context.vios_uuid == "vios-uuid"
    assert state.context.vios_partition_id == 7
    assert state.context.job_uuid_sample == "job-uuid"


@pytest.mark.asyncio
async def test_metrics_template_inventory_records_expected_limitation_and_continues(
    monkeypatch,
):
    calls = []

    async def scripted_call(_state, _client, tool, **kwargs):
        calls.append((tool, kwargs))
        if tool == "hmc_get_pcm_preferences":
            return "PASS", {"long_term_monitor": True}
        if tool == "hmc_processed_metric_links":
            return "FAIL", "PCM is not licensed"
        return "PASS", []

    monkeypatch.setattr(runner.RunState, "call", scripted_call)
    state = runner.RunState()

    await metrics.inspect_metrics_templates(None, state)

    assert [tool for tool, _ in calls] == [
        "hmc_get_pcm_preferences",
        "hmc_processed_metric_links",
        "hmc_aggregated_metric_links",
        "hmc_list_partition_templates",
    ]
    assert calls[0][1] == {
        "category": "ManagedSystem",
        "resource_name_or_uuid": state.context.system_name,
    }
    assert calls[1][1]["start_ts"] == "2026-01-01T00:00:00.000Z"
    assert state.context.lp3_baseline["pcm_prefs"] == {"long_term_monitor": True}
    assert state.results[1]["status"] == "SKIP"


@pytest.mark.asyncio
async def test_user_inventory_classifies_unsupported_endpoint(monkeypatch):
    calls = []

    async def scripted_call(_state, _client, tool, **kwargs):
        calls.append((tool, kwargs))
        return "FAIL", "REST000E: endpoint unavailable"

    monkeypatch.setattr(runner.RunState, "call", scripted_call)
    state = runner.RunState()

    await users.inventory_users(None, state)

    assert calls == [("hmc_list_users", {})]
    assert state.results[0]["status"] == "SKIP"


@pytest.mark.asyncio
async def test_cli_escape_hatch_runs_both_bounded_commands_after_failure(monkeypatch):
    calls = []

    async def scripted_call(_state, _client, tool, **kwargs):
        calls.append((tool, kwargs))
        if len(calls) == 1:
            return "FAIL", "first command failed"
        return "PASS", "systems"

    monkeypatch.setattr(runner.RunState, "call", scripted_call)
    state = runner.RunState()

    await escape_hatch.exercise_cli_escape_hatch(None, state)

    assert calls == [
        ("hmc_run_command", {"cmd": "lshmc -V"}),
        ("hmc_run_command", {"cmd": "lssyscfg -r sys"}),
    ]
    assert [result["status"] for result in state.results] == ["FAIL", "PASS"]


@pytest.mark.asyncio
@pytest.mark.parametrize("create_status", ["PASS", "FAIL"])
async def test_user_administration_cleans_up_only_a_created_user(
    monkeypatch, create_status
):
    calls = []

    async def scripted_call(_state, _client, tool, **kwargs):
        calls.append((tool, kwargs))
        if tool == "hmc_create_user":
            return create_status, {} if create_status == "PASS" else "REST000E"
        return "PASS", []

    monkeypatch.setattr(runner.RunState, "call", scripted_call)
    state = runner.RunState()

    await users.administer_test_user(None, state)

    expected = ["hmc_create_user", "hmc_list_users"]
    if create_status == "PASS":
        expected.extend(["hmc_modify_user", "hmc_delete_user"])
    expected.append("hmc_list_users")
    assert [tool for tool, _ in calls] == expected
    assert calls[0][1]["name"] == state.context.test_user
    if create_status == "PASS":
        assert calls[2][1]["description"].endswith("updated")
        assert calls[3][1] == {"name": state.context.test_user}
    else:
        skipped = [
            result["tool"] for result in state.results if result["status"] == "SKIP"
        ]
        assert skipped == ["hmc_create_user", "hmc_modify_user", "hmc_delete_user"]


@pytest.mark.asyncio
async def test_metrics_jobs_restores_disabled_preference_and_forwards_job_options(
    monkeypatch,
):
    calls = []

    async def scripted_call(_state, _client, tool, **kwargs):
        calls.append((tool, kwargs))
        if tool == "hmc_get_pcm_preferences":
            return "PASS", {"long_term_monitor": False}
        return "PASS", {}

    monkeypatch.setattr(runner.RunState, "call", scripted_call)
    state = runner.RunState()
    state.context.job_uuid_sample = "job-uuid"

    await metrics.inspect_metrics_jobs(None, state)

    assert [tool for tool, _ in calls] == [
        "hmc_get_pcm_preferences",
        "hmc_set_pcm_preferences",
        "hmc_get_pcm_preferences",
        "hmc_set_pcm_preferences",
        "hmc_get_job",
        "hmc_wait_for_job",
        "hmc_list_recent_jobs",
    ]
    set_calls = [kwargs for tool, kwargs in calls if tool == "hmc_set_pcm_preferences"]
    assert [kwargs["long_term_monitor"] for kwargs in set_calls] == [True, False]
    wait_call = next(kwargs for tool, kwargs in calls if tool == "hmc_wait_for_job")
    assert wait_call == {
        "job_uuid": "job-uuid",
        "timeout_seconds": 10,
        "poll_interval": 2,
    }


@pytest.mark.asyncio
async def test_network_inventory_hands_identifiers_to_mutation(monkeypatch):
    calls = []

    async def scripted_call(_state, _client, tool, **kwargs):
        calls.append((tool, kwargs))
        if tool == "hmc_list_virtual_switches":
            return "PASS", [{"Resource": {"SwitchID": "7"}}]
        if tool == "hmc_list_virtual_networks" and len(calls) < 7:
            return "PASS", [{"Resource": {"NetworkVLANID": "3100"}}]
        return "PASS", {}

    monkeypatch.setattr(runner.RunState, "call", scripted_call)
    state = runner.RunState()

    await runner.inventory_network(None, state)
    await runner.mutate_virtual_networking(None, state)

    create_call = next(
        item for item in calls if item[0] == "hmc_create_virtual_network"
    )
    assert state.context.test_vswitch_id == 7
    assert state.context.test_vlan_id == 3101
    assert create_call[1]["vlan_id"] == 3101
    assert create_call[1]["virtual_switch_id"] == 7


@pytest.mark.asyncio
async def test_malformed_vlan_inventory_blocks_network_mutation(monkeypatch):
    calls = []

    async def scripted_call(_state, _client, tool, **kwargs):
        calls.append((tool, kwargs))
        if tool == "hmc_list_virtual_networks":
            return "PASS", [{"Resource": {"NetworkVLANID": "not-a-vlan"}}]
        return "PASS", {}

    monkeypatch.setattr(runner.RunState, "call", scripted_call)
    state = runner.RunState()

    await runner.inventory_network(None, state)
    await runner.mutate_virtual_networking(None, state)

    assert state.context.test_vlan_id is None
    assert not any(tool == "hmc_create_virtual_network" for tool, _ in calls)
    result = next(
        item for item in state.results if item["tool"] == "hmc_list_virtual_networks"
    )
    assert result["status"] == "FAIL"
    assert "not-a-vlan" in result["data"]


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
async def test_malformed_inventory_capacity_blocks_storage_mutation(monkeypatch):
    calls = []

    async def scripted_call(_state, _client, tool, **kwargs):
        calls.append((tool, kwargs))
        if tool == "hmc_list_volume_groups":
            return "PASS", [
                {
                    "UUID": "vg-uuid",
                    "Resource": {
                        "GroupName": "example-lt-609-vg",
                        "VirtualDisks": {
                            "VirtualDisk": {
                                "DiskName": "example-lt-609-disk",
                                "DiskCapacity": "not-a-capacity",
                            }
                        },
                    },
                }
            ]
        return "PASS", {}

    monkeypatch.setattr(runner.RunState, "call", scripted_call)
    state = runner.RunState()
    state.context.vios_uuid = "vios-uuid"

    await runner.inventory_storage(None, state)
    await runner.exercise_storage_provisioning(None, state)

    failure = next(
        result
        for result in state.results
        if result["tool"] == "parse virtual disk capacity"
    )
    assert failure["status"] == "FAIL"
    assert state.context.vdisk_size_mib is None
    assert not any(tool == "hmc_create_virtual_disk" for tool, _ in calls)


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
    reason = lpar._unrestorable_description(baseline)
    assert isinstance(reason, str) and reason


@pytest.mark.parametrize("baseline", ["", "plain text", "[hmc-mcp owner:a created:x]"])
def test_restorable_description_is_not_blocked(baseline):
    """An ordinary baseline description is restored, not skipped."""
    assert lpar._unrestorable_description(baseline) is None


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
