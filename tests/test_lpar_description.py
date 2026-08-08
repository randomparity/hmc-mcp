"""Tests for LPAR description get/set tools (SSH CLI path)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hmc_mcp.server import hmc_get_lpar_description, hmc_set_lpar_description

LPAR_NAME = "test-lpar-01"
SYSTEM_NAME = "Server-9080-M9S-SN123456"


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
# hmc_get_lpar_description
# ---------------------------------------------------------------------- #


def test_get_lpar_description_runs_correct_command(monkeypatch):
    """hmc_get_lpar_description issues lssyscfg with the correct arguments."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("production database server\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_get_lpar_description(LPAR_NAME, SYSTEM_NAME)

    expected_cmd = (
        f"lssyscfg -r lpar -m {SYSTEM_NAME} "
        f"--filter lpar_names={LPAR_NAME} -F description"
    )
    conn_mock.run.assert_called_once_with(expected_cmd, check=True)
    assert result == "production database server\n"


def test_get_lpar_description_returns_empty_when_none_set(monkeypatch):
    """hmc_get_lpar_description returns empty string when no description is set."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_get_lpar_description(LPAR_NAME, SYSTEM_NAME)

    assert result == "\n"


def test_get_lpar_description_passes_system_and_lpar(monkeypatch):
    """hmc_get_lpar_description embeds both system_name and lpar_name in the command."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("owner: ops-team\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock) as mock_connect:
        hmc_get_lpar_description("my-lpar", "my-system")

    called_cmd = conn_mock.run.call_args[0][0]
    assert "-m my-system" in called_cmd
    assert "lpar_names=my-lpar" in called_cmd


# ---------------------------------------------------------------------- #
# hmc_set_lpar_description
# ---------------------------------------------------------------------- #


def test_set_lpar_description_runs_correct_command(monkeypatch):
    """hmc_set_lpar_description issues chsyscfg with the correct arguments."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_set_lpar_description(LPAR_NAME, SYSTEM_NAME, "new description")

    expected_cmd = (
        f'chsyscfg -r lpar -m {SYSTEM_NAME} '
        f'-i "name={LPAR_NAME},description=new description"'
    )
    conn_mock.run.assert_called_once_with(expected_cmd, check=True)
    assert result == ""


def test_set_lpar_description_returns_cli_output(monkeypatch):
    """hmc_set_lpar_description returns the raw SSH stdout verbatim."""
    _hmc_env(monkeypatch)
    RAW_OUTPUT = "0 objects successfully changed.\n"
    conn_mock = _make_ssh_mock(RAW_OUTPUT)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_set_lpar_description(LPAR_NAME, SYSTEM_NAME, "some desc")

    assert result == RAW_OUTPUT


def test_set_lpar_description_embeds_description(monkeypatch):
    """hmc_set_lpar_description includes the description value in the -i argument."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        hmc_set_lpar_description("mylpar", "mysystem", "owner=alice env=prod")

    called_cmd = conn_mock.run.call_args[0][0]
    assert "chsyscfg" in called_cmd
    assert "-r lpar" in called_cmd
    assert "-m mysystem" in called_cmd
    assert "name=mylpar" in called_cmd
    assert "description=owner=alice env=prod" in called_cmd
