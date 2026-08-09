"""Tests for shared memory pool tools (SSH CLI path)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hmc_mcp.server import hmc_list_memory_pools, hmc_remove_memory_pool
from hmc_mcp.ssh import HMCCLIError

from conftest import mock_uuid_resolution

SYSTEM_UUID = "22222222-2222-4222-8222-222222222222"
SYSTEM_NAME = "Server-9009-41A-SN12345"


def _make_ssh_mock(stdout: str = "") -> MagicMock:
    """Return a minimal asyncssh connection mock."""
    result = MagicMock()
    result.stdout = stdout

    conn = AsyncMock()
    conn.run = AsyncMock(return_value=result)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    return conn


def _hmc_env(monkeypatch):
    """Set env vars so HMCConfig() succeeds inside the tool."""
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


_POOL_OUTPUT_WITH_LPARS = (
    "pool_name=SharedMemPool1,size=4096,lpar_names=lpar1 lpar2,curr_lpar_names=lpar1,lpar2\n"
    "pool_name=SharedMemPool2,size=2048,lpar_names=,curr_lpar_names=\n"
)

_POOL_OUTPUT_SINGLE_EMPTY = (
    "pool_name=SharedMemPool1,size=4096,lpar_names=,curr_lpar_names=\n"
)


# ---------------------------------------------------------------------- #
# hmc_list_memory_pools
# ---------------------------------------------------------------------- #


def test_list_memory_pools_runs_correct_command(monkeypatch, mock_hmc):
    """hmc_list_memory_pools issues lshwres -r mempool -m <system_name>."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _make_ssh_mock(_POOL_OUTPUT_WITH_LPARS)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_memory_pools(SYSTEM_UUID)

    conn_mock.run.assert_called_once_with(
        f"lshwres -r mempool -m {SYSTEM_NAME}", check=True, timeout=300.0
    )
    assert isinstance(result, list)
    assert len(result) >= 1


def test_list_memory_pools_returns_parsed_dicts(monkeypatch, mock_hmc):
    """hmc_list_memory_pools parses key=value rows into list of dicts."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _make_ssh_mock("pool_name=Pool1,size=8192,curr_lpar_names=\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_memory_pools(SYSTEM_UUID)

    assert result[0]["pool_name"] == "Pool1"
    assert result[0]["size"] == "8192"


def test_list_memory_pools_empty_output(monkeypatch, mock_hmc):
    """hmc_list_memory_pools returns an empty list when there are no pools."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_memory_pools(SYSTEM_UUID)

    assert result == []


# ---------------------------------------------------------------------- #
# hmc_remove_memory_pool -- safety check (LPAR-present -> error path)
# ---------------------------------------------------------------------- #


def test_remove_memory_pool_blocks_when_lpars_assigned(monkeypatch, mock_hmc):
    """hmc_remove_memory_pool raises HMCCLIError and does NOT remove when LPARs are assigned."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)

    # lshwres output: SharedMemPool1 has curr_lpar_names=lpar1,lpar2
    pool_list_output = (
        "pool_name=SharedMemPool1,size=4096,curr_lpar_names=lpar1,lpar2\n"
    )
    list_result = MagicMock()
    list_result.stdout = pool_list_output

    conn_mock = AsyncMock()
    conn_mock.run = AsyncMock(return_value=list_result)
    conn_mock.__aenter__ = AsyncMock(return_value=conn_mock)
    conn_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        with pytest.raises(HMCCLIError) as exc_info:
            hmc_remove_memory_pool(SYSTEM_UUID, "SharedMemPool1")

    # Only the safety-check lshwres should have been called -- no chhwres.
    assert conn_mock.run.call_count == 1
    called_cmd = conn_mock.run.call_args[0][0]
    assert "lshwres" in called_cmd
    assert "chhwres" not in called_cmd

    # Exception must be a structured error naming the blocking LPARs.
    assert "lpar1" in str(exc_info.value)
    assert "lpar2" in str(exc_info.value)


def test_remove_memory_pool_proceeds_when_no_lpars(monkeypatch, mock_hmc):
    """hmc_remove_memory_pool issues chhwres when no LPARs are assigned."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)

    list_result = MagicMock()
    list_result.stdout = _POOL_OUTPUT_SINGLE_EMPTY
    remove_result = MagicMock()
    remove_result.stdout = "Operation successful.\n"

    conn_mock = AsyncMock()
    conn_mock.run = AsyncMock(side_effect=[list_result, remove_result])
    conn_mock.__aenter__ = AsyncMock(return_value=conn_mock)
    conn_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_remove_memory_pool(SYSTEM_UUID, "SharedMemPool1")

    assert conn_mock.run.call_count == 2
    remove_cmd = conn_mock.run.call_args_list[1][0][0]
    assert "chhwres" in remove_cmd
    assert "-r mempool" in remove_cmd
    assert "-o r" in remove_cmd
    assert "SharedMemPool1" in remove_cmd
    assert "Operation successful" in result


def test_remove_memory_pool_unknown_pool_raises(monkeypatch, mock_hmc):
    """hmc_remove_memory_pool raises HMCCLIError and does NOT remove when pool is not in the list."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)

    list_result = MagicMock()
    list_result.stdout = _POOL_OUTPUT_SINGLE_EMPTY

    conn_mock = AsyncMock()
    conn_mock.run = AsyncMock(return_value=list_result)
    conn_mock.__aenter__ = AsyncMock(return_value=conn_mock)
    conn_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        with pytest.raises(HMCCLIError) as exc_info:
            hmc_remove_memory_pool(SYSTEM_UUID, "MissingPool")

    # Only the safety-check lshwres should have been called -- no chhwres.
    assert conn_mock.run.call_count == 1
    called_cmd = conn_mock.run.call_args[0][0]
    assert "lshwres" in called_cmd
    assert "chhwres" not in called_cmd

    # Exception names the missing pool.
    assert "no pool with that name exists" in str(exc_info.value)
    assert "MissingPool" in str(exc_info.value)
