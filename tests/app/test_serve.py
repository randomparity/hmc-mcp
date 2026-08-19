"""Tests for the MCP server transport security gate.

The streamable-HTTP transport is unauthenticated and exposes the full tool
surface, so `hmc-mcp serve --http` must refuse non-loopback binds unless the
operator explicitly opts in with --allow-remote.
"""

import socket
from unittest.mock import patch

import pytest
from click import unstyle
from typer.testing import CliRunner

from hmc_mcp import server as server_app
from hmc_mcp.cli import app
from hmc_mcp.server import _is_loopback


def _address_info(address, family=socket.AF_INET):
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 0))


def test_is_loopback_accepts_only_loopback_resolution():
    addresses = [_address_info("127.0.0.2"), _address_info("::1", socket.AF_INET6)]
    with patch("hmc_mcp.server.socket.getaddrinfo", return_value=addresses):
        assert _is_loopback("localhost")


@pytest.mark.parametrize(
    "addresses",
    [
        [_address_info("127.0.0.1"), _address_info("192.168.1.10")],
        [],
        [_address_info("not-an-address")],
    ],
)
def test_is_loopback_rejects_unsafe_resolution(addresses):
    with patch("hmc_mcp.server.socket.getaddrinfo", return_value=addresses):
        assert not _is_loopback("localhost")


def test_is_loopback_rejects_resolution_failure():
    with patch(
        "hmc_mcp.server.socket.getaddrinfo", side_effect=socket.gaierror("unknown")
    ):
        assert not _is_loopback("missing.example")


def test_serve_http_mixed_address_refuses_without_allow_remote():
    addresses = [_address_info("127.0.0.1"), _address_info("203.0.113.5")]
    with (
        patch("hmc_mcp.server.socket.getaddrinfo", return_value=addresses),
        patch.object(type(server_app.mcp), "run") as run,
    ):
        result = CliRunner().invoke(
            app, ["serve", "--http", "--listen-host", "localhost"]
        )

    assert result.exit_code == 2
    assert "binds beyond loopback" in unstyle(result.output)
    run.assert_not_called()


def test_serve_http_loopback_bind_is_allowed():
    """Loopback bind needs no --allow-remote (default is loopback)."""
    with patch("hmc_mcp.server.main_http") as main_http:
        result = CliRunner().invoke(app, ["serve", "--http"])
    assert result.exit_code == 0
    main_http.assert_called_once_with(
        host="127.0.0.1",
        port=8000,
        enable_arbitrary_command=False,
        allow_remote=False,
        access_policy=None,
    )


def test_serve_http_non_loopback_refuses_without_allow_remote():
    with patch.object(type(server_app.mcp), "run") as run:
        result = CliRunner().invoke(
            app, ["serve", "--http", "--listen-host", "0.0.0.0"]
        )
    output = unstyle(result.output)
    assert result.exit_code == 2  # usage error
    assert "binds beyond loopback" in output
    assert "no authentication" in output
    assert "--allow-remote" in output
    run.assert_not_called()


def test_serve_http_non_loopback_allowed_with_explicit_opt_in():
    with patch("hmc_mcp.server.main_http") as main_http:
        result = CliRunner().invoke(
            app,
            ["serve", "--http", "--listen-host", "0.0.0.0", "--allow-remote"],
        )
    assert result.exit_code == 0
    main_http.assert_called_once_with(
        host="0.0.0.0",
        port=8000,
        enable_arbitrary_command=False,
        allow_remote=True,
        access_policy=None,
    )


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
    main_stdio.assert_called_once_with(
        enable_arbitrary_command=False, access_policy=None
    )


@pytest.mark.parametrize("http", [False, True])
def test_serve_passes_arbitrary_command_opt_in(http):
    args = ["serve", "--enable-arbitrary-command"]
    target = "hmc_mcp.server.main_http" if http else "hmc_mcp.server.main_stdio"
    if http:
        args.append("--http")

    with patch(target) as entrypoint:
        result = CliRunner().invoke(app, args)

    assert result.exit_code == 0
    if http:
        entrypoint.assert_called_once_with(
            host="127.0.0.1",
            port=8000,
            enable_arbitrary_command=True,
            allow_remote=False,
            access_policy=None,
        )
    else:
        entrypoint.assert_called_once_with(
            enable_arbitrary_command=True, access_policy=None
        )


@pytest.mark.parametrize("enabled", [False, True])
def test_stdio_entry_point_gates_the_escape_hatch(enabled, monkeypatch):
    calls = []

    async def _record(flag, application, *, permits=None):
        calls.append((flag, permits))

    monkeypatch.setattr(server_app, "configure_arbitrary_command_tool", _record)
    with patch.object(type(server_app.mcp), "run") as run:
        server_app.main_stdio(enable_arbitrary_command=enabled)

    assert calls == [(enabled, None)]
    run.assert_called_once_with()


@pytest.mark.parametrize("enabled", [False, True])
def test_http_entry_point_gates_the_escape_hatch(enabled, monkeypatch):
    calls = []

    async def _record(flag, application, *, permits=None):
        calls.append((flag, permits))

    monkeypatch.setattr(server_app, "configure_arbitrary_command_tool", _record)
    with patch.object(type(server_app.mcp), "run") as run:
        server_app.main_http(
            host="127.0.0.1", port=9000, enable_arbitrary_command=enabled
        )

    assert calls == [(enabled, None)]
    run.assert_called_once_with(
        transport="streamable-http", host="127.0.0.1", port=9000
    )


def test_http_entrypoint_refuses_remote_bind_without_authorization():
    with patch.object(type(server_app.mcp), "run") as run:
        with pytest.raises(ValueError, match="binds beyond loopback"):
            server_app.main_http(host="0.0.0.0")

    run.assert_not_called()


def test_http_entrypoint_accepts_remote_bind_with_authorization():
    with patch.object(type(server_app.mcp), "run") as run:
        server_app.main_http(host="0.0.0.0", allow_remote=True)

    run.assert_called_once_with(transport="streamable-http", host="0.0.0.0", port=8000)
