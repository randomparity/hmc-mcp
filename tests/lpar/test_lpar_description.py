"""Tests for LPAR description get/set tools (SSH CLI path)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


from hmc_mcp.server import hmc_get_lpar_description, hmc_set_lpar_description

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
# hmc_get_lpar_description
# ---------------------------------------------------------------------- #


def test_get_lpar_description_runs_correct_command(monkeypatch, mock_hmc):
    """hmc_get_lpar_description issues lssyscfg with the correct arguments."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock("production database server\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_get_lpar_description(SYSTEM_UUID, LPAR_UUID)

    expected_cmd = (
        f"lssyscfg -r lpar -m {SYSTEM_NAME} "
        f"--filter lpar_names={LPAR_NAME} -F description"
    )
    conn_mock.run.assert_called_once_with(expected_cmd, check=True, timeout=300.0)
    assert result == "production database server\n"


def test_get_lpar_description_returns_empty_when_none_set(monkeypatch, mock_hmc):
    """hmc_get_lpar_description returns empty string when no description is set."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock("\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_get_lpar_description(SYSTEM_UUID, LPAR_UUID)

    assert result == "\n"


def test_get_lpar_description_resolves_uuids_to_names(monkeypatch, mock_hmc):
    """hmc_get_lpar_description embeds the resolved system/lpar names in the command."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(
        mock_hmc, "22222222-2222-4222-8222-222222222222", "my-system", "11111111-1111-4111-8111-111111111111", "my-lpar"
    )
    conn_mock = _make_ssh_mock("owner: ops-team\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        hmc_get_lpar_description("22222222-2222-4222-8222-222222222222", "11111111-1111-4111-8111-111111111111")

    called_cmd = conn_mock.run.call_args[0][0]
    assert "-m my-system" in called_cmd
    assert "lpar_names=my-lpar" in called_cmd


# ---------------------------------------------------------------------- #
# hmc_set_lpar_description
# ---------------------------------------------------------------------- #


def test_set_lpar_description_runs_correct_command(monkeypatch, mock_hmc):
    """hmc_set_lpar_description issues chsyscfg with the correct arguments."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_set_lpar_description(
            SYSTEM_UUID, LPAR_UUID, "new description"
        )

    expected_cmd = (
        f"chsyscfg -r lpar -m {SYSTEM_NAME} "
        f"-i 'name={LPAR_NAME},description=new description'"
    )
    conn_mock.run.assert_called_once_with(expected_cmd, check=True, timeout=300.0)
    assert result == ""


def test_set_lpar_description_returns_cli_output(monkeypatch, mock_hmc):
    """hmc_set_lpar_description returns the raw SSH stdout verbatim."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    RAW_OUTPUT = "0 objects successfully changed.\n"
    conn_mock = _make_ssh_mock(RAW_OUTPUT)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_set_lpar_description(SYSTEM_UUID, LPAR_UUID, "some desc")

    assert result == RAW_OUTPUT


def test_set_lpar_description_embeds_description(monkeypatch, mock_hmc):
    """hmc_set_lpar_description includes the description value in the -i argument."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(
        mock_hmc, "22222222-2222-4222-8222-222222222222", "mysystem", "11111111-1111-4111-8111-111111111111", "mylpar"
    )
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        hmc_set_lpar_description(
            "22222222-2222-4222-8222-222222222222", "11111111-1111-4111-8111-111111111111", "owner=alice env=prod"
        )

    called_cmd = conn_mock.run.call_args[0][0]
    assert "chsyscfg" in called_cmd
    assert "-r lpar" in called_cmd
    assert "-m mysystem" in called_cmd
    assert "name=mylpar" in called_cmd
    assert "description=owner=alice env=prod" in called_cmd


# ---------------------------------------------------------------------- #
# hmc_set_lpar_description — ASCII validation
# ---------------------------------------------------------------------- #


def test_set_lpar_description_rejects_non_ascii(monkeypatch, mock_hmc):
    """hmc_set_lpar_description raises ValueError for non-ASCII descriptions."""
    import pytest

    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        with pytest.raises(ValueError, match="non-ASCII or non-printable"):
            hmc_set_lpar_description(SYSTEM_UUID, LPAR_UUID, "em\u2014dash")

    conn_mock.run.assert_not_called()


def test_set_lpar_description_rejects_non_ascii_various(monkeypatch, mock_hmc):
    """hmc_set_lpar_description rejects a range of non-ASCII characters."""
    import pytest

    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)

    for bad in ["\u2014", "\u00e9", "\u4e2d\u6587", "\u00a0"]:
        conn_mock = _make_ssh_mock("")
        with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
            with pytest.raises(ValueError, match="non-ASCII or non-printable"):
                hmc_set_lpar_description(SYSTEM_UUID, LPAR_UUID, f"desc{bad}")
        conn_mock.run.assert_not_called()


def test_set_lpar_description_rejects_control_characters(monkeypatch, mock_hmc):
    """hmc_set_lpar_description rejects ASCII control characters (NUL, LF, CR, ESC)."""
    import pytest

    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)

    for ctrl in ["\x00", "\n", "\r", "\x1b", "\x7f"]:
        conn_mock = _make_ssh_mock("")
        with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
            with pytest.raises(ValueError, match="non-ASCII or non-printable"):
                hmc_set_lpar_description(SYSTEM_UUID, LPAR_UUID, f"desc{ctrl}bad")
        conn_mock.run.assert_not_called()
