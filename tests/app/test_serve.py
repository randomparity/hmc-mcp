"""Tests for the MCP server transport security gate.

The streamable-HTTP transport is unauthenticated and exposes the full tool
surface, so `hmc-mcp serve --http` must refuse non-loopback binds unless the
operator explicitly opts in with --allow-remote.
"""

from unittest.mock import patch

import pytest
from click import unstyle
from typer.testing import CliRunner

from hmc_mcp.cli import _is_loopback, app


def test_is_loopback_accepts_loopback_names():
    assert _is_loopback("localhost")
    assert _is_loopback("127.0.0.1")
    assert _is_loopback("::1")


def test_is_loopback_rejects_non_loopback():
    assert not _is_loopback("0.0.0.0")
    assert not _is_loopback("192.168.1.10")
    assert not _is_loopback("hmc.example.com")


def test_serve_http_loopback_bind_is_allowed():
    """Loopback bind needs no --allow-remote (default is loopback)."""
    with patch("hmc_mcp.server.main_http") as main_http:
        result = CliRunner().invoke(app, ["serve", "--http"])
    assert result.exit_code == 0
    main_http.assert_called_once_with(host="127.0.0.1", port=8000)


def test_serve_http_non_loopback_refuses_without_allow_remote():
    with patch("hmc_mcp.server.main_http") as main_http:
        result = CliRunner().invoke(
            app, ["serve", "--http", "--listen-host", "0.0.0.0"]
        )
    output = unstyle(result.output)
    assert result.exit_code == 2  # usage error
    assert "binds beyond loopback" in output
    assert "no authentication" in output
    assert "--allow-remote" in output
    main_http.assert_not_called()


def test_serve_http_non_loopback_allowed_with_explicit_opt_in():
    with patch("hmc_mcp.server.main_http") as main_http:
        result = CliRunner().invoke(
            app,
            ["serve", "--http", "--listen-host", "0.0.0.0", "--allow-remote"],
        )
    assert result.exit_code == 0
    main_http.assert_called_once_with(host="0.0.0.0", port=8000)


@pytest.mark.parametrize(
    "root_option",
    [
        ["--host", "hmc.example.com"],
        ["--user", "operator"],
        ["--password", "secret"],
        ["--verify-ssl"],
        ["--profile", "production"],
    ],
)
def test_serve_rejects_command_line_hmc_options(root_option):
    with patch("hmc_mcp.server.main_stdio") as main_stdio:
        result = CliRunner().invoke(app, [*root_option, "serve"])

    assert result.exit_code == 2
    assert "serve does not accept HMC connection options" in unstyle(result.output)
    main_stdio.assert_not_called()


def test_serve_allows_environment_hmc_options(monkeypatch):
    monkeypatch.setenv("HMC_HOST", "hmc.example.com")
    with patch("hmc_mcp.server.main_stdio") as main_stdio:
        result = CliRunner().invoke(app, ["serve"])

    assert result.exit_code == 0
    main_stdio.assert_called_once_with()
