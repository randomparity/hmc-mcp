"""Tests for LPAR memory-optimization score access (lsmemopt over SSH)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import asyncssh
import pytest

from hmc_mcp.config import HMCConfig
from hmc_mcp.ssh_commands import (
    HMCCLIError,
    get_lpar_memopt_score,
    list_lpar_memopt_scores,
)

SYSTEM_NAME = "p9da10"
LPAR_NAME = "p9da10v1t"
SCORE_ROW = "lpar_name=p9da10v1t,lpar_id=1,curr_lpar_score=100"
NONE_ROW = "lpar_name=dalpar2rrd1t,lpar_id=17,curr_lpar_score=none"
SECOND_ROW = "lpar_name=dapurea1t,lpar_id=19,curr_lpar_score=100"
MULTI_ROW = f"{SCORE_ROW}\n{SECOND_ROW}\n"


def _config() -> HMCConfig:
    return HMCConfig(
        host="hmc.test", user="hscroot", password="abc123",  # pragma: allowlist secret
        _env_file=None,
    )


def _make_ssh_mock(stdout: str = "") -> MagicMock:
    """Return a minimal asyncssh connection mock."""
    result = MagicMock()
    result.stdout = stdout

    conn = AsyncMock()
    conn.run = AsyncMock(return_value=result)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    return conn


def _not_found_error(stderr: str) -> asyncssh.ProcessError:
    """Build the non-zero-exit outcome the HMC CLI produces for a bad name."""
    return asyncssh.ProcessError(
        env={},
        command="lsmemopt",
        subsystem=None,
        exit_status=1,
        exit_signal=None,
        returncode=1,
        stdout="",
        stderr=stderr,
    )


# ---------------------------------------------------------------------- #
# get_lpar_memopt_score (ssh layer)
# ---------------------------------------------------------------------- #


def test_get_lpar_memopt_score_runs_correct_command():
    """get_lpar_memopt_score issues lsmemopt with the single-LPAR filter."""
    cfg = _config()
    conn = _make_ssh_mock(SCORE_ROW)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        result = asyncio.run(get_lpar_memopt_score(cfg, SYSTEM_NAME, LPAR_NAME))

    expected_cmd = (
        f"lsmemopt -m {SYSTEM_NAME} -r lpar -o currscore "
        f"--filter lpar_names={LPAR_NAME}"
    )
    conn.run.assert_called_once_with(expected_cmd, check=True, timeout=300.0)
    assert result == {
        "lpar_name": "p9da10v1t",
        "lpar_id": "1",
        "curr_lpar_score": "100",
    }


def test_get_lpar_memopt_score_preserves_none_score():
    """The literal HMC score 'none' is passed through unchanged."""
    cfg = _config()
    conn = _make_ssh_mock(NONE_ROW)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        result = asyncio.run(get_lpar_memopt_score(cfg, SYSTEM_NAME, "dalpar2rrd1t"))

    assert result["curr_lpar_score"] == "none"


def test_get_lpar_memopt_score_quotes_selectors():
    """Names needing shell quoting are shlex-quoted in the command."""
    cfg = _config()
    conn = _make_ssh_mock(SCORE_ROW)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        asyncio.run(get_lpar_memopt_score(cfg, "sys one", "my lpar"))

    expected_cmd = (
        "lsmemopt -m 'sys one' -r lpar -o currscore --filter lpar_names='my lpar'"
    )
    conn.run.assert_called_once_with(expected_cmd, check=True, timeout=300.0)


@pytest.mark.parametrize("bad_name", ["", "   "])
def test_get_lpar_memopt_score_rejects_empty_lpar_name(bad_name):
    """An empty LPAR selector fails before any SSH round-trip."""
    cfg = _config()
    conn = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        with pytest.raises(ValueError, match="lpar_name"):
            asyncio.run(get_lpar_memopt_score(cfg, SYSTEM_NAME, bad_name))

    conn.run.assert_not_called()


def test_get_lpar_memopt_score_raises_when_no_row_reported():
    """Exit-0 output without a score row is anomalous and raises HMCCLIError."""
    cfg = _config()
    conn = _make_ssh_mock("")

    with (
        patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn),
        pytest.raises(HMCCLIError, match="no memory-optimization score"),
    ):
        asyncio.run(get_lpar_memopt_score(cfg, SYSTEM_NAME, LPAR_NAME))


def test_get_lpar_memopt_score_unknown_lpar_raises_hmcclierror():
    """A non-zero HMC CLI exit (unknown LPAR) surfaces as HMCCLIError."""
    cfg = _config()
    conn = _make_ssh_mock("")
    conn.run = AsyncMock(
        side_effect=_not_found_error("The partition named doesnotexist was not found.")
    )
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn),
        pytest.raises(HMCCLIError, match="doesnotexist"),
    ):
        asyncio.run(get_lpar_memopt_score(cfg, SYSTEM_NAME, "doesnotexist"))


# ---------------------------------------------------------------------- #
# list_lpar_memopt_scores (ssh layer)
# ---------------------------------------------------------------------- #


def test_list_lpar_memopt_scores_runs_correct_command_without_filter():
    """Without an LPAR selector the command carries no --filter option."""
    cfg = _config()
    conn = _make_ssh_mock(MULTI_ROW)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        result = asyncio.run(list_lpar_memopt_scores(cfg, SYSTEM_NAME, None))

    expected_cmd = f"lsmemopt -m {SYSTEM_NAME} -r lpar -o currscore"
    conn.run.assert_called_once_with(expected_cmd, check=True, timeout=300.0)
    assert result == [
        {"lpar_name": "p9da10v1t", "lpar_id": "1", "curr_lpar_score": "100"},
        {"lpar_name": "dapurea1t", "lpar_id": "19", "curr_lpar_score": "100"},
    ]


def test_list_lpar_memopt_scores_with_lpar_filter():
    """An LPAR selector appends the single-value --filter form."""
    cfg = _config()
    conn = _make_ssh_mock(SCORE_ROW)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        result = asyncio.run(list_lpar_memopt_scores(cfg, SYSTEM_NAME, LPAR_NAME))

    expected_cmd = (
        f"lsmemopt -m {SYSTEM_NAME} -r lpar -o currscore "
        f"--filter lpar_names={LPAR_NAME}"
    )
    conn.run.assert_called_once_with(expected_cmd, check=True, timeout=300.0)
    assert len(result) == 1
    assert result[0]["lpar_name"] == "p9da10v1t"


def test_list_lpar_memopt_scores_empty_output_returns_empty_list():
    """A system reporting no scores yields an empty list."""
    cfg = _config()
    conn = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        result = asyncio.run(list_lpar_memopt_scores(cfg, SYSTEM_NAME, None))

    assert result == []


def test_list_lpar_memopt_scores_rejects_empty_filter_name():
    """An empty (but supplied) LPAR selector fails before any SSH round-trip."""
    cfg = _config()
    conn = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        with pytest.raises(ValueError, match="lpar_name"):
            asyncio.run(list_lpar_memopt_scores(cfg, SYSTEM_NAME, "  "))

    conn.run.assert_not_called()
