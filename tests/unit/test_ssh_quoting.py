"""Regression tests: SSH-backed tools shell-quote every interpolated value.

``run_hmc_command`` executes the built command string via the remote shell, so
any user-controlled value interpolated without quoting is an injection vector
(``; id``, ``$(...)``, backticks, etc.).  These tests pin the ``shlex.quote``
discipline: hostile values must land shell-quoted in the built command so the
remote shell passes them as a single argument instead of interpreting
metacharacters.

``hmc_run_command`` is deliberately exempt — it is the documented arbitrary-
command escape hatch.
"""

from __future__ import annotations

import shlex
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hmc_mcp.config import HMCConfig
from hmc_mcp.server import (
    hmc_add_vnic,
    hmc_backup_lpar_profiles,
    hmc_list_memory_pools,
    hmc_remove_memory_pool,
    hmc_restore_vios,
    hmc_set_lpar_description,
)
from hmc_mcp.ssh_commands import list_io_slots

from conftest import mock_uuid_resolution

SYSTEM_UUID = "22222222-2222-4222-8222-222222222222"
SYSTEM_NAME = "Server-9080-M9S-SN12345"
LPAR_UUID = "11111111-1111-4111-8111-111111111111"
LPAR_NAME = "my-lpar"

# Semicolon + space: without quoting the remote shell would run `id` as a
# second command; with quoting it must stay inside a single argument.
HOSTILE = "x; id"


def _make_ssh_mock(stdout: str = "") -> MagicMock:
    """Return a minimal asyncssh connection mock."""
    result = MagicMock()
    result.stdout = stdout

    conn = AsyncMock()
    conn.run = AsyncMock(return_value=result)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    return conn


def _hmc_env(monkeypatch) -> None:
    """Set env vars so HMCConfig() succeeds inside the tools."""
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


def _captured_cmd(conn_mock) -> str:
    """Return the command string of the last conn.run() call."""
    return conn_mock.run.call_args[0][0]


def _arg_after(args: list[str], option: str) -> str:
    """Return the argument following *option* in a shlex-split argv list."""
    return args[args.index(option) + 1]


# ---------------------------------------------------------------------- #
# ssh.py: list_io_slots (standalone -m value)
# ---------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_io_slots_quotes_hostile_system_name():
    """A hostile system name is shell-quoted in the lshwres command."""
    conn = _make_ssh_mock("")
    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        await list_io_slots(HMCConfig(host="h", user="u", password="p"), HOSTILE)

    cmd = _captured_cmd(conn)
    assert f"-m {HOSTILE}" not in cmd  # raw value never appears unquoted
    assert f"-m {shlex.quote(HOSTILE)}" in cmd


# ---------------------------------------------------------------------- #
# server.py tools
# ---------------------------------------------------------------------- #


def test_add_vnic_quotes_hostile_vswitch(monkeypatch, mock_hmc):
    """hmc_add_vnic keeps a hostile virtual_switch_name inside the quoted -a payload."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        hmc_add_vnic(
            system_name_or_uuid=SYSTEM_UUID,
            lpar_name_or_uuid=LPAR_UUID,
            capacity=2,
            virtual_switch_name=HOSTILE,
            port_vlan_id=100,
        )

    payload = f"capacity=2,vswitch_name={HOSTILE},port_vlan_id=100"
    args = shlex.split(_captured_cmd(conn))
    assert _arg_after(args, "-a") == payload


def test_add_vnic_quotes_hostile_backing_devices(monkeypatch, mock_hmc):
    """hmc_add_vnic keeps hostile backing_devices inside the quoted -a payload."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        hmc_add_vnic(
            system_name_or_uuid=SYSTEM_UUID,
            lpar_name_or_uuid=LPAR_UUID,
            capacity=2,
            virtual_switch_name="ETHERNET0",
            port_vlan_id=100,
            backing_devices=f"U78DA.001.XYZ {HOSTILE}",
        )

    payload = f"capacity=2,vswitch_name=ETHERNET0,port_vlan_id=100,backing_devices=U78DA.001.XYZ {HOSTILE}"
    args = shlex.split(_captured_cmd(conn))
    assert _arg_after(args, "-a") == payload


def test_set_lpar_description_quotes_hostile_description(monkeypatch, mock_hmc):
    """hmc_set_lpar_description keeps a hostile description inside the -i payload."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        hmc_set_lpar_description(
            SYSTEM_UUID, LPAR_UUID, HOSTILE, ownership_override=True
        )

    payload = f"name={LPAR_NAME},description={HOSTILE}"
    args = shlex.split(_captured_cmd(conn))
    assert _arg_after(args, "-i") == payload


def test_remove_memory_pool_quotes_hostile_pool_name(monkeypatch, mock_hmc):
    """hmc_remove_memory_pool quotes a hostile pool_name in the chhwres command."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    # Pool list contains the hostile name (unassigned) so the safety check
    # finds it and proceeds to the remove command.
    conn = _make_ssh_mock("pool_name=x; id,size=4096,curr_lpar_names=\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        hmc_remove_memory_pool(SYSTEM_UUID, HOSTILE)

    cmd = _captured_cmd(conn)  # last run() call is the chhwres remove
    assert "chhwres" in cmd
    args = shlex.split(cmd)
    assert _arg_after(args, "-a") == HOSTILE


def test_backup_lpar_profiles_quotes_hostile_file_path(monkeypatch, mock_hmc):
    """hmc_backup_lpar_profiles shell-quotes a hostile file_path."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        hmc_backup_lpar_profiles(SYSTEM_UUID, "/tmp/bak;id")

    cmd = _captured_cmd(conn)
    assert shlex.quote("/tmp/bak;id") in cmd


def test_restore_vios_quotes_hostile_backup_name(monkeypatch):
    """hmc_restore_vios shell-quotes a hostile backup_name (no REST resolution).

    The hostile value carries a shell metacharacter but no path separator: ADR
    0044's containment guard refuses a separator-bearing name before the command
    is built, so a value with one would never reach the quoting this asserts.
    Quoting and containment are separate controls and this proves the first.
    """
    _hmc_env(monkeypatch)
    conn = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        hmc_restore_vios(SYSTEM_UUID, "vios;id")

    cmd = _captured_cmd(conn)
    assert shlex.quote("vios;id") in cmd


def test_resolved_system_name_is_quoted_too(monkeypatch, mock_hmc):
    """REST-resolved names are quoted too — the HMC could return metacharacters.

    Even though system/lpar names come from the HMC rather than the caller,
    they are interpolated into the shell command, so they must be quoted as
    uniformly as direct user input.
    """
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, HOSTILE)  # hostile system name
    conn = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        hmc_list_memory_pools(SYSTEM_UUID)

    cmd = _captured_cmd(conn)
    assert f"-m {HOSTILE}" not in cmd
    assert f"-m {shlex.quote(HOSTILE)}" in cmd
