"""Tests for the read-only capture sweep (scripts/live_capture_sweep.py, issue #1202).

Nothing here reaches an HMC: the transport seams are replaced by recorders, and the
MCP client by a fake that serves tool definitions and answers.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncssh
import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
import live_capture_sweep as sweep
from live_test.capture import capture

from hmcpctl.client.core import HMCClient

# --- guard ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "allowed"),
    [
        ("GET", "/rest/api/uom/ManagedSystem", True),
        ("get", "/rest/api/uom/ManagedSystem", True),
        ("PUT", "/rest/api/web/Logon", True),
        ("DELETE", "/rest/api/web/Logon", True),
        ("POST", "/rest/api/web/Logon", False),
        ("PUT", "/rest/api/web/Logon/jobs", False),
        ("PUT", "/rest/api/uom/LogicalPartition/x/do/PowerOn", False),
        ("POST", "/rest/api/uom/ManagedSystem/x", False),
        ("DELETE", "/rest/api/uom/LogicalPartition/x", False),
    ],
)
def test_rest_allowed(method: str, path: str, allowed: bool) -> None:
    assert sweep.rest_allowed(method, path) is allowed


@pytest.mark.parametrize(
    ("command", "allowed"),
    [
        ("lssyscfg -r lpar -m sys1", True),
        ("lshmc -V", True),
        ("lssyscfg -r lpar -m 'a b' --filter 'lpar_names=x'", True),
        ("chsysstate -r lpar -o on -n x", False),
        ("rmsyscfg -r lpar -n x", False),
        ("lssyscfg -r lpar; rmsyscfg -r lpar -n x", False),
        ("lssyscfg -r lpar && chsyscfg", False),
        ("lssyscfg -r lpar | tee x", False),
        ("lssyscfg $(rmsyscfg)", False),
        ("lssyscfg -r lpar\nrmsyscfg", False),
        ("", False),
        (None, False),
    ],
)
def test_command_allowed(command: Any, allowed: bool) -> None:
    assert sweep.command_allowed(command) is allowed


class _Seams:
    """Replaces the transport originals with recorders before the guard captures them."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.sent: list[Any] = []

        async def request(client: Any, method: str, path: str, **kwargs: Any) -> str:
            self.sent.append((method, path))
            return "response"

        async def run(conn: Any, *args: Any, **kwargs: Any) -> str:
            self.sent.append(args[0] if args else kwargs.get("command"))
            return "result"

        monkeypatch.setattr(HMCClient, "_request", request)
        monkeypatch.setattr(asyncssh.SSHClientConnection, "run", run)
        monkeypatch.setattr(asyncssh.SSHClientConnection, "create_process", run)


def test_guard_refuses_before_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    seams = _Seams(monkeypatch)

    async def drive() -> None:
        with sweep.read_only_guard():
            assert await HMCClient._request(None, "GET", "/a") == "response"
            with pytest.raises(sweep.ReadOnlyViolation):
                await HMCClient._request(None, "POST", "/a")
            assert await asyncssh.SSHClientConnection.run(None, "lshmc -V") == "result"
            with pytest.raises(sweep.ReadOnlyViolation):
                await asyncssh.SSHClientConnection.run(None, command="rmsyscfg -n x")
            with pytest.raises(sweep.ReadOnlyViolation):
                await asyncssh.SSHClientConnection.create_process(None)

    asyncio.run(drive())
    assert seams.sent == [("GET", "/a"), "lshmc -V"]


def test_guard_restores_the_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    _Seams(monkeypatch)
    before = (HMCClient._request, asyncssh.SSHClientConnection.run)
    with pytest.raises(RuntimeError), sweep.read_only_guard():
        raise RuntimeError("boom")
    assert (HMCClient._request, asyncssh.SSHClientConnection.run) == before


def test_capture_records_the_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The guard sits below the harness, so a refused call still leaves a record."""
    seams = _Seams(monkeypatch)
    destination = tmp_path / "sweep.capture.jsonl"

    async def drive() -> None:
        with (
            sweep.read_only_guard(),
            capture(destination),
            pytest.raises(sweep.ReadOnlyViolation),
        ):
            await HMCClient._request(None, "PUT", "/rest/api/uom/x/do/PowerOff")

    asyncio.run(drive())
    record = json.loads(destination.read_text())
    assert record["method"] == "PUT"
    assert record["exception"].startswith("ReadOnlyViolation: refused PUT")
    assert seams.sent == []


# --- planning -------------------------------------------------------------------


def _spec(name: str, required: tuple[str, ...] = (), optional: tuple[str, ...] = ()):
    return sweep.ToolSpec(name, required, required + optional)


def _context(**values: Any) -> sweep.Context:
    return sweep.Context(start_ts="2026-09-30T00:00:00Z", **values)


def test_required_parameters_come_from_the_context() -> None:
    spec = _spec(
        "hmc_get_lpar", ("system_name_or_uuid", "lpar_name_or_uuid"), ("profile",)
    )
    calls, missing = sweep.plan_calls(spec, _context(system="S", lpar="L"))
    assert calls == [{"system_name_or_uuid": "S", "lpar_name_or_uuid": "L"}]
    assert missing == []


def test_unresolvable_parameter_is_reported_not_guessed() -> None:
    calls, missing = sweep.plan_calls(_spec("hmc_get_job", ("job_id",)), _context())
    assert calls == []
    assert missing == ["job_id"]


def test_undiscovered_context_is_reported() -> None:
    calls, missing = sweep.plan_calls(
        _spec("hmc_list_vnics", ("lpar_name_or_uuid",)), _context()
    )
    assert calls == []
    assert missing == ["lpar_name_or_uuid"]


def test_optional_filters_are_left_unset() -> None:
    spec = _spec("hmc_list_clusters", optional=("system_name_or_uuid",))
    assert sweep.plan_calls(spec, _context(system="S")) == ([{}], [])


def test_adapter_id_is_filled_when_discovered() -> None:
    spec = _spec(
        "hmc_list_sriov_physical_ports", ("system_name_or_uuid",), ("adapter_id",)
    )
    calls, _ = sweep.plan_calls(spec, _context(system="S", adapter="1"))
    assert calls == [{"system_name_or_uuid": "S", "adapter_id": "1"}]


def test_variants_run_each_form() -> None:
    calls, missing = sweep.plan_calls(
        _spec("hmc_list_lpars", optional=("system_name_or_uuid", "state")),
        _context(system="S"),
    )
    assert calls == [{"system_name_or_uuid": "S"}, {}, {"state": "not activated"}]
    assert missing == []


def test_variant_with_an_undiscovered_value_is_dropped() -> None:
    calls, missing = sweep.plan_calls(
        _spec("hmc_get_lpar_state", ("lpar_name_or_uuid",)), _context(lpar="L")
    )
    assert calls == [{"lpar_name_or_uuid": "L"}]
    assert missing == ["lpar_name_or_uuid"]


def test_pcm_tools_run_per_category() -> None:
    spec = _spec(
        "hmc_processed_metrics",
        ("category", "resource_name_or_uuid", "start_ts"),
        ("system_name_or_uuid",),
    )
    calls, _ = sweep.plan_calls(spec, _context(system="S", lpar="L"))
    assert calls == [
        {
            "start_ts": "2026-09-30T00:00:00Z",
            "category": "ManagedSystem",
            "resource_name_or_uuid": "S",
            "system_name_or_uuid": "S",
        },
        {
            "start_ts": "2026-09-30T00:00:00Z",
            "category": "LogicalPartition",
            "resource_name_or_uuid": "L",
            "system_name_or_uuid": "S",
        },
    ]


# --- discovery ----------------------------------------------------------------------


def test_discovery_prefers_operating_systems_and_running_lpars() -> None:
    context = _context()
    systems = [
        {"UUID": "s-off", "Resource": {"State": "power off"}},
        {"UUID": "s-on", "Resource": {"State": {"#text": "operating"}}},
    ]
    lpars = [
        {"UUID": "l-off", "Resource": {"PartitionState": "not activated"}},
        {"UUID": "l-on", "Resource": {"PartitionState": "running"}},
    ]
    sweep.discover(context, "hmc_list_systems", systems)
    sweep.discover(context, "hmc_list_lpars", lpars)
    sweep.discover(context, "hmc_list_volume_groups", [{"uuid": "vg-1"}])
    sweep.discover(context, "hmc_list_sriov_adapters", {"items": [{"adapter_id": "1"}]})
    assert (context.system, context.lpar, context.vg, context.adapter) == (
        "s-on",
        "l-on",
        "vg-1",
        "1",
    )


def test_discovery_never_overrides_the_operator() -> None:
    context = _context(system="mine")
    sweep.discover(context, "hmc_list_systems", [{"UUID": "other"}])
    assert context.system == "mine"


def test_discovery_skips_unavailable_ids() -> None:
    context = _context()
    sweep.discover(
        context, "hmc_list_sriov_adapters", {"items": [{"adapter_id": "unavailable"}]}
    )
    assert context.adapter is None


# --- driving --------------------------------------------------------------------------


def _tool(name: str, read_only: bool, required: tuple[str, ...] = ()) -> Any:
    return SimpleNamespace(
        name=name,
        annotations=SimpleNamespace(read_only_hint=read_only),
        input_schema={
            "required": list(required),
            "properties": {name: {} for name in required},
        },
    )


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[Any]:
        return [
            _tool("hmc_get_system", True, ("system_name_or_uuid",)),
            _tool("hmc_power_on_lpar", False, ("lpar_name_or_uuid",)),
            _tool("hmc_list_systems", True),
            _tool("hmc_get_job", True, ("job_id",)),
            _tool("hmc_get_console_info", True),
        ]

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append((name, args))
        if name == "hmc_get_console_info":
            raise RuntimeError("refused")
        data = [{"UUID": "sys-1", "Resource": {"State": "operating"}}]
        return SimpleNamespace(data=data, structured_content=None)


def test_sweep_tools_runs_read_only_tools_after_discovery(tmp_path: Path) -> None:
    client = _Client()
    log = sweep.ToolLog(tmp_path / "tools.capture.jsonl")
    steps: list[str] = []
    try:
        calls, skips = asyncio.run(
            sweep.sweep_tools(client, _context(), log, steps.append)
        )
    finally:
        log.close()
    names = [name for name, _ in client.calls]
    assert "hmc_power_on_lpar" not in names
    assert names[:3] == ["hmc_list_systems", "hmc_list_systems", "hmc_get_console_info"]
    assert ("hmc_get_system", {"system_name_or_uuid": "sys-1"}) in client.calls
    assert (calls, skips) == (4, 1)
    records = [
        json.loads(line)
        for line in (tmp_path / "tools.capture.jsonl").read_text().splitlines()
    ]
    assert {
        "kind": "skip",
        "step": "hmc_get_job",
        "tool": "hmc_get_job",
        "missing": ["job_id"],
    } in records
    failed = [r for r in records if r.get("ok") is False]
    assert failed[0]["error"] == "RuntimeError: refused"
    assert steps == names


def test_tool_log_is_private(tmp_path: Path) -> None:
    path = tmp_path / "tools.capture.jsonl"
    path.write_text("")
    path.chmod(0o644)
    log = sweep.ToolLog(path)
    log.close()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_tool_log_refuses_a_committable_destination() -> None:
    inside = Path(__file__).parents[2] / "tools-not-ignored.json"
    with pytest.raises(ValueError, match="not git-ignored"):
        sweep.ToolLog(inside)
    assert not inside.exists()


def test_tool_log_refuses_a_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("")
    link = tmp_path / "tools.capture.jsonl"
    link.symlink_to(target)
    with pytest.raises(OSError):
        sweep.ToolLog(link)


def test_output_directory_is_private(tmp_path: Path) -> None:
    directory = sweep.prepare_output(tmp_path / "a" / "b")
    assert stat.S_IMODE(os.stat(directory).st_mode) == 0o700


# --- raw probes --------------------------------------------------------------------


def test_names_fill_quotes_for_shell_and_url() -> None:
    names = sweep.Names(sys_uuid="u-1", system_name="sys one")
    assert names.fill("lssyscfg -r lpar -m {system_name}", shell=True) == (
        "lssyscfg -r lpar -m 'sys one'"
    )
    assert names.fill("/rest/api/uom/ManagedSystem/{sys_uuid}", shell=False) == (
        "/rest/api/uom/ManagedSystem/u-1"
    )
    assert names.fill("/rest/api/uom/VirtualIOServer/{vios_uuid}", shell=False) is None
    assert (
        names.fill("/x/(SystemName=={system_name})", shell=False)
        == "/x/(SystemName==sys%20one)"
    )


def test_raw_commands_pass_the_guard() -> None:
    names = sweep.Names(sys_uuid="u", system_name="s; rmsyscfg")
    for _, template in sweep.RAW_COMMANDS:
        filled = names.fill(template, shell=True)
        assert filled is not None
        assert filled.startswith("ls")
    # A hostile name is quoted, and the guard still refuses its metacharacter.
    assert not sweep.command_allowed(
        names.fill("lssyscfg -m {system_name}", shell=True)
    )


def test_raw_gets_are_reads() -> None:
    assert all(path.startswith("/rest/api/") for _, path, _ in sweep.RAW_GETS)


def test_sweep_raw_skips_unknown_placeholders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, Any]] = []

    class _Hmc:
        config = object()

        async def _request(self, method: str, path: str, **kwargs: Any) -> None:
            sent.append((method, path))
            if path.endswith("Enumerations.xsd"):
                raise RuntimeError("recorded by the harness")

    async def run(config: Any, command: str) -> str:
        sent.append(("ssh", command))
        return ""

    monkeypatch.setattr("hmcpctl.ssh.transport.run_hmc_command", run)
    log = sweep.ToolLog(tmp_path / "tools.capture.jsonl")
    try:
        count = asyncio.run(
            sweep.sweep_raw(_Hmc(), sweep.Names("u", "s"), log, lambda _: None)
        )
    finally:
        log.close()
    assert count == len(sent)
    assert ("ssh", "lssyscfg -r sys -m s") in sent
    assert all("{" not in str(item) for item in sent)
    skips = (tmp_path / "tools.capture.jsonl").read_text()
    assert "lpar-quick-all" in skips
