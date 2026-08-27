"""Tests for LPAR MSP flag get/set tools (SSH CLI path)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hmc_mcp.config import HMCConfig
from hmc_mcp.server import hmc_get_lpar_msp, hmc_set_lpar_msp
from hmc_mcp.ssh_profiles import HMCCLIError, set_lpar_msp

from conftest import mock_uuid_resolution

SYSTEM_UUID = "22222222-2222-4222-8222-222222222222"
SYSTEM_NAME = "Server-9080-M9S-SN123456"
LPAR_UUID = "11111111-1111-4111-8111-111111111111"
LPAR_NAME = "test-lpar-01"


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
    """Set env vars so HMCConfig() resolves inside the tool."""
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


# ---------------------------------------------------------------------- #
# hmc_get_lpar_msp
# ---------------------------------------------------------------------- #


def test_get_lpar_msp_runs_correct_command(monkeypatch, mock_hmc):
    """hmc_get_lpar_msp issues lssyscfg with the correct arguments."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock("1\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_get_lpar_msp(SYSTEM_UUID, LPAR_UUID)

    expected_cmd = (
        f"lssyscfg -r lpar -m {SYSTEM_NAME} --filter lpar_names={LPAR_NAME} -F msp"
    )
    conn_mock.run.assert_called_once_with(expected_cmd, check=True, timeout=300.0)
    assert result is True


def test_get_lpar_msp_returns_true_when_enabled(monkeypatch, mock_hmc):
    """hmc_get_lpar_msp returns True when the HMC reports msp=1."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock("1\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_get_lpar_msp(SYSTEM_UUID, LPAR_UUID)

    assert result is True


def test_get_lpar_msp_returns_false_when_disabled(monkeypatch, mock_hmc):
    """hmc_get_lpar_msp returns False when the HMC reports msp=0."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock("0\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_get_lpar_msp(SYSTEM_UUID, LPAR_UUID)

    assert result is False


@pytest.mark.parametrize("raw", ["", "2\n", "enabled\n"])
def test_get_lpar_msp_rejects_unexpected_output(monkeypatch, mock_hmc, raw):
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock(raw)

    with (
        patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock),
        pytest.raises(HMCCLIError, match="expected '0' or '1'"),
    ):
        hmc_get_lpar_msp(SYSTEM_UUID, LPAR_UUID)


# ---------------------------------------------------------------------- #
# hmc_set_lpar_msp
# ---------------------------------------------------------------------- #


def _make_ssh_mock_seq(*stdouts: str) -> MagicMock:
    """Return an asyncssh connection mock that returns *stdouts* in sequence.

    Each positional argument is the stdout of one successive conn.run() call.
    Use when set_lpar_msp makes two SSH round-trips (lpar_env check + chsyscfg).
    """
    results = []
    for stdout in stdouts:
        r = MagicMock()
        r.stdout = stdout
        results.append(r)

    conn = AsyncMock()
    conn.run = AsyncMock(side_effect=results)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    return conn


def test_set_lpar_msp_enabled_runs_correct_command(monkeypatch, mock_hmc):
    """hmc_set_lpar_msp with enabled=True issues chsyscfg with msp=1 for VIOS."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock_seq("vioserver\n", "")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_set_lpar_msp(SYSTEM_UUID, LPAR_UUID, True)

    assert conn_mock.run.call_count == 2
    env_cmd = conn_mock.run.call_args_list[0][0][0]
    assert "lssyscfg" in env_cmd
    assert "lpar_env" in env_cmd
    assert LPAR_NAME in env_cmd

    chsyscfg_cmd = conn_mock.run.call_args_list[1][0][0]
    assert chsyscfg_cmd == (
        f"chsyscfg -r lpar -m {SYSTEM_NAME} -i name={LPAR_NAME},msp=1"
    )
    assert result == ""


def test_set_lpar_msp_disabled_runs_correct_command(monkeypatch, mock_hmc):
    """hmc_set_lpar_msp with enabled=False issues chsyscfg with msp=0 for VIOS."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock_seq("vioserver\n", "")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_set_lpar_msp(SYSTEM_UUID, LPAR_UUID, False)

    assert conn_mock.run.call_count == 2
    chsyscfg_cmd = conn_mock.run.call_args_list[1][0][0]
    assert chsyscfg_cmd == (
        f"chsyscfg -r lpar -m {SYSTEM_NAME} -i name={LPAR_NAME},msp=0"
    )
    assert result == ""


def test_set_lpar_msp_returns_cli_output(monkeypatch, mock_hmc):
    """hmc_set_lpar_msp returns the raw SSH stdout verbatim."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    RAW_OUTPUT = "0 objects successfully changed.\n"
    conn_mock = _make_ssh_mock_seq("vioserver\n", RAW_OUTPUT)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_set_lpar_msp(SYSTEM_UUID, LPAR_UUID, True)

    assert result == RAW_OUTPUT


def test_set_lpar_msp_rejects_aix_lpar(monkeypatch, mock_hmc):
    """hmc_set_lpar_msp raises HMCCLIError when the partition is AIX (not VIOS)."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock_seq("aixlinux\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        with pytest.raises(HMCCLIError, match="only valid for a VIOS"):
            hmc_set_lpar_msp(SYSTEM_UUID, LPAR_UUID, True)

    # chsyscfg must NOT have been called
    assert conn_mock.run.call_count == 1


def test_set_lpar_msp_rejects_linux_lpar(monkeypatch, mock_hmc):
    """hmc_set_lpar_msp raises HMCCLIError when the partition is Linux (lpar_env=aixlinux).

    The HMC CLI returns 'aixlinux' for both AIX and Linux partitions — it is the
    combined label for those two OS families.
    """
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock_seq("aixlinux\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        with pytest.raises(HMCCLIError, match="only valid for a VIOS"):
            hmc_set_lpar_msp(SYSTEM_UUID, LPAR_UUID, False)

    assert conn_mock.run.call_count == 1


# ---------------------------------------------------------------------- #
# set_lpar_msp (ssh-layer) — inner lpar_env guard
# ---------------------------------------------------------------------- #


def test_set_lpar_msp_rejects_partition_not_found(monkeypatch, mock_hmc):
    """hmc_set_lpar_msp raises HMCCLIError when lssyscfg returns empty (LPAR not found)."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock_seq("\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        with pytest.raises(HMCCLIError, match="not found"):
            hmc_set_lpar_msp(SYSTEM_UUID, LPAR_UUID, True)

    assert conn_mock.run.call_count == 1


def test_set_lpar_msp_ssh_layer_rejects_non_vios():
    """set_lpar_msp (ssh) raises HMCCLIError for non-VIOS — inner guard."""
    cfg = HMCConfig(host="hmc.test", user="hscroot", password="abc123", _env_file=None)
    env_result = MagicMock()
    env_result.stdout = "aixlinux\n"
    conn = AsyncMock()
    conn.run = AsyncMock(return_value=env_result)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        with pytest.raises(HMCCLIError, match="only valid for a VIOS"):
            asyncio.run(set_lpar_msp(cfg, "sys", "lpar", True))
