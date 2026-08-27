"""Contract tests for the presentation-neutral ``installios`` operations.

ADR 0013 assigns the orchestration to ``operations_install``; ADR 0070 fixes the
mechanism as a detached HMC CLI submission, so the operations return the bridge's
detach handle rather than an HMC job identifier (there is no job on this path).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import get_type_hints
from unittest.mock import AsyncMock, patch

import pytest

from conftest import make_config

from hmc_mcp import api, audit_sink
from hmc_mcp.operations_install import InstallHandle, install_lpar_os, install_vios
from hmc_mcp.ssh import HMCCLIError
from hmc_mcp.ssh_commands import INSTALLIOS_PID_PREFIX, build_installios_command

LPAR_UUID = "11111111-1111-4111-8111-111111111111"
SYSTEM_UUID = "22222222-2222-4222-8222-222222222222"

_REQUEST = {
    "install_source": "/extra/viosimages/VIOS_4.1/dvdimage.v1.iso",
    "client_ip": "192.168.1.30",
    "subnet_mask": "255.255.255.0",
    "gateway": "192.168.1.1",
    "vlan_id": "100",
}


def _hmc(**resolutions) -> AsyncMock:
    """A duck-typed client whose name lookups resolve to the test fixtures."""
    hmc = AsyncMock()
    hmc.config = make_config()
    hmc.find_system_by_name.return_value = {"UUID": SYSTEM_UUID}
    hmc.find_partition_by_name.return_value = {"UUID": LPAR_UUID}
    hmc.find_vios_by_name.return_value = {"UUID": LPAR_UUID}
    for name, value in resolutions.items():
        getattr(hmc, name).return_value = value
    return hmc


class _Ssh:
    """Records every SSH command and answers the ones the operations issue."""

    def __init__(self, *, pid: int = 4242, name_rows: str = "") -> None:
        self.commands: list[str] = []
        self._pid = pid
        self._name_rows = name_rows

    async def __call__(self, config, command: str) -> str:
        self.commands.append(command)
        if command.startswith("lssyscfg"):
            return self._name_rows
        return f"{INSTALLIOS_PID_PREFIX}{self._pid}\n"


def _patch_ssh(ssh: _Ssh):
    return patch("hmc_mcp.ssh_commands.run_hmc_command", new=ssh)


@pytest.mark.parametrize(
    ("operation", "finder"),
    [(install_lpar_os, "find_partition_by_name"), (install_vios, "find_vios_by_name")],
)
@pytest.mark.asyncio
async def test_operation_submits_the_composed_installios_command(operation, finder):
    """Names resolve, the command is composed, and the detach handle comes back."""
    hmc = _hmc()
    ssh = _Ssh()

    with _patch_ssh(ssh):
        result = await operation(hmc, "target1", "sys1", **_REQUEST)

    expected, log_path = build_installios_command(
        system_name="sys1",
        partition_name="target1",
        profile_name="default",
        **_REQUEST,
    )
    assert ssh.commands == [expected]
    assert set(result) == set(get_type_hints(InstallHandle))
    assert (result["system"], result["partition"], result["pid"]) == (
        "sys1",
        "target1",
        4242,
    )
    assert result["log_path"] == log_path
    assert "no HMC job exists on this path" in result["message"]
    assert log_path in result["message"]
    getattr(hmc, finder).assert_awaited_once_with("target1", system_uuid=SYSTEM_UUID)


@pytest.mark.parametrize("operation", [install_lpar_os, install_vios])
@pytest.mark.asyncio
async def test_operation_returns_without_polling_for_completion(operation):
    """Submit-and-detach: exactly one SSH round trip, and no job polling."""
    hmc = _hmc()
    ssh = _Ssh()

    with _patch_ssh(ssh):
        await asyncio.wait_for(operation(hmc, "target1", "sys1", **_REQUEST), 5)

    assert len(ssh.commands) == 1
    hmc.get_job.assert_not_awaited()
    hmc.submit_job.assert_not_awaited()


@pytest.mark.parametrize("operation", [install_lpar_os, install_vios])
@pytest.mark.asyncio
async def test_operation_resolves_uuid_targets_to_cli_names(operation):
    """A UUID target is named over REST for the system and SSH for the partition."""
    hmc = _hmc(get_managed_system={"Resource": {"SystemName": "sys1"}})
    ssh = _Ssh(name_rows=f"{LPAR_UUID},target1\n")

    with _patch_ssh(ssh):
        result = await operation(hmc, LPAR_UUID, SYSTEM_UUID, **_REQUEST)

    assert result["system"] == "sys1"
    assert result["partition"] == "target1"
    assert ssh.commands[0] == "lssyscfg -r lpar -m sys1 -F UUID,PartitionName"
    hmc.get_managed_system.assert_awaited_once_with(SYSTEM_UUID)


@pytest.mark.parametrize("operation", [install_lpar_os, install_vios])
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("install_source", "-rf", "installios flag"),
        ("client_ip", "999.9.9.9", "IPv4"),
        ("subnet_mask", "255.0.255.0", "subnet mask"),
        ("gateway", "not-an-ip", "IPv4"),
        ("vlan_id", "4095", "VLAN"),
        ("mac_address", "zz:zz:zz:zz:zz:zz", "MAC"),
        ("profile_name", "", "profile_name"),
        ("profile_name", "bad\nname", "profile_name"),
    ],
)
@pytest.mark.asyncio
async def test_operation_rejects_invalid_input_before_any_io(
    operation, field, value, message
):
    """Validation runs ahead of the first REST call, so nothing is contacted."""
    hmc = _hmc()
    ssh = _Ssh()

    with _patch_ssh(ssh):
        with pytest.raises(ValueError, match=message):
            await operation(hmc, "target1", "sys1", **{**_REQUEST, field: value})

    assert ssh.commands == []
    hmc.find_system_by_name.assert_not_awaited()


@pytest.mark.parametrize(
    ("operation", "finder", "message"),
    [
        (install_lpar_os, "find_partition_by_name", "No LPAR named"),
        (install_vios, "find_vios_by_name", "No VIOS named"),
    ],
)
@pytest.mark.asyncio
async def test_operation_fails_before_submission_for_an_unknown_target(
    operation, finder, message
):
    hmc = _hmc(**{finder: None})
    ssh = _Ssh()

    with _patch_ssh(ssh):
        with pytest.raises(ValueError, match=message):
            await operation(hmc, "nosuchtarget", "sys1", **_REQUEST)

    assert ssh.commands == []


@pytest.mark.parametrize("operation", [install_lpar_os, install_vios])
@pytest.mark.asyncio
async def test_operation_surfaces_a_failed_submission(operation):
    hmc = _hmc()

    async def fail(config, command):
        raise HMCCLIError(f"SSH command {command!r} failed with exit status 127")

    with patch("hmc_mcp.ssh_commands.run_hmc_command", new=fail):
        with pytest.raises(HMCCLIError, match="exit status 127"):
            await operation(hmc, "target1", "sys1", **_REQUEST)


@pytest.mark.parametrize("operation", [install_lpar_os, install_vios])
@pytest.mark.asyncio
async def test_unresolvable_uuid_target_raises_before_submitting(operation):
    """An HMCCLIError from name resolution must leave nothing submitted."""
    hmc = _hmc(get_managed_system={"Resource": {"SystemName": "sys1"}})
    ssh = _Ssh(name_rows="99999999-9999-4999-8999-999999999999,other\n")

    with _patch_ssh(ssh):
        with pytest.raises(HMCCLIError, match="Could not resolve"):
            await operation(hmc, LPAR_UUID, SYSTEM_UUID, **_REQUEST)

    assert ssh.commands == ["lssyscfg -r lpar -m sys1 -F UUID,PartitionName"]


def _install_records(text: str) -> list[dict]:
    """Every ``install-attempted`` record a consumer would parse out of *text*."""
    records = []
    for line in text.splitlines():
        try:
            candidate = json.loads(line)
        except ValueError:
            continue
        if isinstance(candidate, dict) and candidate.get("event") == "install-attempted":
            records.append(candidate)
    return records


def _one_install_record(text: str) -> dict:
    records = _install_records(text)
    assert len(records) == 1, f"expected one record, got {len(records)}: {text!r}"
    return records[0]


@pytest.mark.parametrize("operation", [install_lpar_os, install_vios])
@pytest.mark.asyncio
async def test_a_submission_is_recorded_on_the_served_path(operation, capsys):
    """#469, ADR 0102. Only ``install_audit_sink`` is configured — no ``basicConfig``.

    That is what ``server._serve_application`` does and all it does for this
    package's own namespace, so this is the served MCP deployment's real state.
    Before ADR 0102 the submission's only trace was an ``INFO`` record on the
    unconfigured ``hmc_mcp.operations_install`` logger, whose effective level is
    the root's ``WARNING`` — dropped before formatting, and below
    ``logging.lastResort``'s threshold too.
    """
    audit_sink.install_audit_sink()
    hmc = _hmc()
    hmc.config = make_config(host="hmc.test", agent_id="agent-7")

    with _patch_ssh(_Ssh()):
        result = await operation(hmc, "target1", "sys1", **_REQUEST)

    assert audit_sink._SINK.drain(audit_sink._DRAIN_TIMEOUT), "the sink did not settle"
    captured = capsys.readouterr()
    assert captured.out == "", "an audit record must never reach the JSON-RPC stream"
    record = _one_install_record(captured.err)
    assert (record["system"], record["partition"]) == ("sys1", "target1")
    assert record["log_path"] == result["log_path"]
    assert record["host"] == "hmc.test"
    assert record["attribution"]["claim"] == "agent-7"


@pytest.mark.parametrize("operation", [install_lpar_os, install_vios])
@pytest.mark.asyncio
async def test_a_submission_is_recorded_for_a_bare_api_consumer(operation, capsys):
    """The other half of #469: a process that configures no logging at all.

    A ``hmc_mcp.api`` consumer calls no ``install_audit_sink``, so the reserved
    logger has no handler and does not propagate — which is exactly when
    ``Logger.callHandlers`` consults ``logging.lastResort``. It drops anything
    below ``WARNING``, which is why ADR 0102 §3 fixes the record's level there.
    """
    # `audit` closes propagation at import (#272); the autouse isolation fixture
    # reopens it, so this restores the shipped state rather than configuring it.
    logging.getLogger(audit_sink.AUDIT_LOGGER_NAME).propagate = False
    saved_root = list(logging.root.handlers)
    logging.root.handlers.clear()
    hmc = _hmc()
    try:
        with _patch_ssh(_Ssh()):
            await operation(hmc, "target1", "sys1", **_REQUEST)
        captured = capsys.readouterr()
    finally:
        logging.root.handlers[:] = saved_root

    assert captured.out == ""
    record = _one_install_record(captured.err)
    assert (record["system"], record["partition"]) == ("sys1", "target1")


@pytest.mark.parametrize("operation", [install_lpar_os, install_vios])
@pytest.mark.asyncio
async def test_a_failed_submission_is_still_recorded(operation, capsys):
    """The record is written *before* the submit, which is the case it exists for.

    ``HMCCLIError`` does not say whether an ``installios`` was started (both
    operations' ``Raises:`` blocks say so), so this is where an operator most
    needs the partition and the log path — and where a record written after a
    successful submit would not exist.
    """
    audit_sink.install_audit_sink()
    hmc = _hmc()

    async def fail(config, command):
        raise HMCCLIError("SSH command failed with exit status 127")

    with patch("hmc_mcp.ssh_commands.run_hmc_command", new=fail):
        with pytest.raises(HMCCLIError):
            await operation(hmc, "target1", "sys1", **_REQUEST)

    assert audit_sink._SINK.drain(audit_sink._DRAIN_TIMEOUT), "the sink did not settle"
    record = _one_install_record(capsys.readouterr().err)
    assert (record["system"], record["partition"]) == ("sys1", "target1")


@pytest.mark.parametrize("operation", [install_lpar_os, install_vios])
@pytest.mark.asyncio
async def test_nothing_is_recorded_when_the_request_never_reaches_a_submit(
    operation, capsys
):
    """A request refused by validation or name resolution submits nothing, so it
    is not an attempt against any partition's disks and leaves no record."""
    audit_sink.install_audit_sink()

    with _patch_ssh(_Ssh()):
        with pytest.raises(ValueError, match="IPv4"):
            await operation(
                _hmc(), "target1", "sys1", **{**_REQUEST, "gateway": "not-an-ip"}
            )
        with pytest.raises(ValueError, match="No "):
            await operation(
                _hmc(find_partition_by_name=None, find_vios_by_name=None),
                "nosuchtarget",
                "sys1",
                **_REQUEST,
            )

    assert audit_sink._SINK.drain(audit_sink._DRAIN_TIMEOUT), "the sink did not settle"
    assert _install_records(capsys.readouterr().err) == []


@pytest.mark.parametrize("name", ["install_lpar_os", "install_vios"])
def test_operations_are_exported_from_the_facade(name):
    """ADR 0029: every selected operation is part of the supported manifest."""
    assert name in api.__all__
    assert getattr(api, name) is globals()[name]


def test_detach_handle_is_the_declared_return_type():
    """#468: the handle is a named owned type, so its keys reach the digest.

    ``dict[str, Any]`` renders identically however the five keys are spelled, so
    the ADR 0029 signature digest could not see a rename and ``py.typed``
    resolved every value to ``Any``. Naming the type in the annotation fixes
    both: the digest text now carries ``InstallHandle``, and the key set below
    is the same object the runtime assertion above compares the payload against.
    """
    assert "InstallHandle" in api.__all__
    assert api.InstallHandle is InstallHandle
    assert get_type_hints(InstallHandle) == {
        "system": str,
        "partition": str,
        "pid": int,
        "log_path": str,
        "message": str,
    }
    assert InstallHandle.__optional_keys__ == frozenset()
    for operation in (install_lpar_os, install_vios):
        assert get_type_hints(operation)["return"] is InstallHandle
