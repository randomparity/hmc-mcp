"""Tests for shared memory pool tools (SSH CLI path)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import live_fixture, live_process_error, mock_uuid_resolution

from hmcpctl.server_tools.systems.resources import (
    hmc_list_memory_pools,
    hmc_remove_memory_pool,
)
from hmcpctl.ssh.transport import HMCCLIError

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


# A synthetic pool row. The real row's field names are uncaptured: no swept
# system had a pool (#1245), so nothing here may be read as the HMC's shape.
_POOL_ROW = "pool_mem=4096,max_pool_mem=8192\n"


def _run_results(*stdouts: str) -> list[MagicMock]:
    results = []
    for stdout in stdouts:
        result = MagicMock()
        result.stdout = stdout
        results.append(result)
    return results


def _sequenced_ssh_mock(*stdouts: str) -> MagicMock:
    conn = _make_ssh_mock()
    conn.run = AsyncMock(side_effect=_run_results(*stdouts))
    return conn


def _sent(conn: MagicMock) -> list[str]:
    return [call[0][0] for call in conn.run.call_args_list]


# `lpar_name` and `mem_mode` are captured `lshwres -r mem --level lpar` field
# names on every swept system (tests/fixtures/live/vocabulary/); `ded` is the
# captured value and `shared` the documented one (lslparutil, mem_mode).
_DEDICATED_PARTITIONS = (
    "lpar_name=lpar-a,lpar_id=1,mem_mode=ded,curr_mem=4096\n"
    "lpar_name=vios-a,lpar_id=2,mem_mode=ded,curr_mem=8192\n"
)


# ---------------------------------------------------------------------- #
# hmc_list_memory_pools
# ---------------------------------------------------------------------- #


def test_list_memory_pools_runs_correct_command(monkeypatch, mock_hmc):
    """hmc_list_memory_pools issues lshwres -r mempool -m <system_name>."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _make_ssh_mock(_POOL_ROW)

    with patch("hmcpctl.ssh.transport.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_memory_pools(SYSTEM_UUID)

    conn_mock.run.assert_called_once_with(
        f"lshwres -r mempool -m {SYSTEM_NAME}", check=True, timeout=300.0
    )
    assert result == [{"pool_mem": "4096", "max_pool_mem": "8192"}]


def test_list_memory_pools_empty_output(monkeypatch, mock_hmc):
    """No pool answers ``No results were found.`` with exit 0: an empty list.

    Captured on a V10R3 POWER9 that is Active Memory Sharing capable.
    """
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _make_ssh_mock(live_fixture("cli-mempool-empty")["stdout"])

    with patch("hmcpctl.ssh.transport.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_memory_pools(SYSTEM_UUID)

    assert result == []


def _refusing_ssh_mock(capture: str) -> MagicMock:
    conn = _make_ssh_mock()
    conn.run.side_effect = live_process_error(capture)
    return conn


def test_list_memory_pools_reads_ams_unsupported_as_no_pools(monkeypatch, mock_hmc):
    """A system without Active Memory Sharing refuses with HSCLA4A0: it has no pools.

    Captured on both V11R2 POWER11 systems; a POWER9 answers the sentinel instead.
    """
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _refusing_ssh_mock("cli-mempool-ams-unsupported")

    with patch("hmcpctl.ssh.transport.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_memory_pools(SYSTEM_UUID)

    assert result == []


def test_list_memory_pools_raises_other_refusals(monkeypatch, mock_hmc):
    """Only HSCLA4A0 means no pools; any other refusal still fails the read."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _refusing_ssh_mock("cli-lpar-no-m")

    with (
        patch("hmcpctl.ssh.transport.asyncssh.connect", return_value=conn_mock),
        pytest.raises(HMCCLIError, match="missing a required parameter"),
    ):
        hmc_list_memory_pools(SYSTEM_UUID)


# ---------------------------------------------------------------------- #
# hmc_remove_memory_pool
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "conn_factory",
    [
        lambda: _make_ssh_mock(live_fixture("cli-mempool-empty")["stdout"]),
        lambda: _refusing_ssh_mock("cli-mempool-ams-unsupported"),
    ],
    ids=["no-results", "ams-unsupported"],
)
def test_remove_memory_pool_without_a_pool_refuses_before_chhwres(
    monkeypatch, mock_hmc, conn_factory
):
    """With no pool on the system, the remove refuses after the one list read."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = conn_factory()

    with (
        patch("hmcpctl.ssh.transport.asyncssh.connect", return_value=conn_mock),
        pytest.raises(HMCCLIError, match="has no shared memory pool"),
    ):
        hmc_remove_memory_pool(SYSTEM_UUID)

    assert _sent(conn_mock) == [f"lshwres -r mempool -m {SYSTEM_NAME}"]


def test_remove_memory_pool_sends_the_documented_remove(monkeypatch, mock_hmc):
    """With a pool and every partition dedicated, the remove names no pool."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _sequenced_ssh_mock(_POOL_ROW, _DEDICATED_PARTITIONS, "")

    with patch("hmcpctl.ssh.transport.asyncssh.connect", return_value=conn_mock):
        result = hmc_remove_memory_pool(SYSTEM_UUID)

    assert _sent(conn_mock) == [
        f"lshwres -r mempool -m {SYSTEM_NAME}",
        f"lshwres -r mem --level lpar -m {SYSTEM_NAME}",
        f"chhwres -r mempool -m {SYSTEM_NAME} -o r",
    ]
    assert result == ""


@pytest.mark.parametrize(
    "row,named",
    [
        ("lpar_name=lpar-s,lpar_id=3,mem_mode=shared", "lpar-s (mem_mode=shared)"),
        ("lpar_name=lpar-u,lpar_id=4,curr_mem=2048", "lpar-u (mem_mode=<absent>)"),
        ("lpar_name=lpar-x,lpar_id=5,mem_mode=", "lpar-x (mem_mode=)"),
    ],
    ids=["shared", "field-absent", "field-empty"],
)
def test_remove_memory_pool_refuses_unless_every_partition_is_dedicated(
    monkeypatch, mock_hmc, row, named
):
    """A partition not confirmed as ``mem_mode=ded`` blocks the remove."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _sequenced_ssh_mock(_POOL_ROW, _DEDICATED_PARTITIONS + row + "\n")

    with (
        patch("hmcpctl.ssh.transport.asyncssh.connect", return_value=conn_mock),
        pytest.raises(HMCCLIError) as exc_info,
    ):
        hmc_remove_memory_pool(SYSTEM_UUID)

    assert named in str(exc_info.value)
    assert "lpar-a" not in str(exc_info.value)
    assert not any("chhwres" in cmd for cmd in _sent(conn_mock))
