"""Tests for the MCP server transport security gate.

The streamable-HTTP transport is unauthenticated and exposes the full tool
surface, so `hmc-mcp serve --http` must refuse non-loopback binds unless the
operator explicitly opts in with --allow-remote.
"""

import logging
import socket
from unittest.mock import patch

from hmc_mcp import audit

import pytest
from click import unstyle
from fastmcp import FastMCP
from typer.testing import CliRunner

from hmc_mcp import server as server_app
from hmc_mcp.access_policy import DEFAULT_CONNECTION_TOKEN, AccessPolicy
from hmc_mcp.cli import app
from hmc_mcp.legacy_policy import compile_legacy_policy
from hmc_mcp.server import TOOL_SECURITY, _is_loopback

# ADR 0041 made --access-policy required, so every invocation below that expects a
# *start* has to select one. The `run` patch target moved with it: there is no
# module-level application left to reach through `type(server_app.mcp)`.
POLICY_NAME = "lab"
POLICY_ARGS = ["--access-policy", POLICY_NAME]
POLICY_FILE = """
[[policies.lab.grants]]
effects = ["read"]
connections = ["<default>"]
targets = "all-targets"
"""


def _legacy() -> AccessPolicy:
    return compile_legacy_policy(TOOL_SECURITY, (DEFAULT_CONNECTION_TOKEN,))


@pytest.fixture(autouse=True)
def selectable_policy(tmp_path, monkeypatch):
    """Make `--access-policy lab` resolvable without touching the real config dir.

    Autouse because every `serve` invocation in this module now needs it, and an
    invocation that quietly read the developer's own `access-policy.toml` would be a
    test whose result depends on the machine it runs on.
    """
    import hmc_mcp.access_policy as access_policy_module

    path = tmp_path / "access-policy.toml"
    path.write_text(POLICY_FILE, encoding="utf-8")
    monkeypatch.setattr(
        access_policy_module, "resolve_access_policy_path", lambda: path
    )
    return path


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
        patch.object(FastMCP, "run") as run,
    ):
        result = CliRunner().invoke(
            app, ["serve", "--http", "--listen-host", "localhost", *POLICY_ARGS]
        )

    assert result.exit_code == 2
    assert "binds beyond loopback" in unstyle(result.output)
    run.assert_not_called()


def test_serve_http_loopback_bind_is_allowed():
    """Loopback bind needs no --allow-remote (default is loopback)."""
    with patch("hmc_mcp.server.main_http") as main_http:
        result = CliRunner().invoke(app, ["serve", "--http", *POLICY_ARGS])
    assert result.exit_code == 0, result.output
    forwarded, kwargs = main_http.call_args.args, main_http.call_args.kwargs
    assert isinstance(forwarded[0], AccessPolicy) and forwarded[0].name == POLICY_NAME
    assert kwargs == {
        "host": "127.0.0.1",
        "port": 8000,
        "enable_arbitrary_command": False,
        "allow_remote": False,
        "audit_level": None,
    }


def test_serve_http_non_loopback_refuses_without_allow_remote():
    with patch.object(FastMCP, "run") as run:
        result = CliRunner().invoke(
            app, ["serve", "--http", "--listen-host", "0.0.0.0", *POLICY_ARGS]
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
            [
                "serve",
                "--http",
                "--listen-host",
                "0.0.0.0",
                "--allow-remote",
                *POLICY_ARGS,
            ],
        )
    assert result.exit_code == 0, result.output
    forwarded, kwargs = main_http.call_args.args, main_http.call_args.kwargs
    assert isinstance(forwarded[0], AccessPolicy)
    assert kwargs == {
        "host": "0.0.0.0",
        "port": 8000,
        "enable_arbitrary_command": False,
        "allow_remote": True,
        "audit_level": None,
    }


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
        result = CliRunner().invoke(app, ["serve", *POLICY_ARGS])

    assert result.exit_code == 0, result.output
    assert isinstance(main_stdio.call_args.args[0], AccessPolicy)
    assert main_stdio.call_args.kwargs == {
        "enable_arbitrary_command": False,
        "audit_level": None,
    }


@pytest.mark.parametrize("http", [False, True])
def test_serve_passes_arbitrary_command_opt_in(http):
    args = ["serve", "--enable-arbitrary-command", *POLICY_ARGS]
    target = "hmc_mcp.server.main_http" if http else "hmc_mcp.server.main_stdio"
    if http:
        args.append("--http")

    with patch(target) as entrypoint:
        result = CliRunner().invoke(app, args)

    assert result.exit_code == 0, result.output
    assert isinstance(entrypoint.call_args.args[0], AccessPolicy)
    if http:
        assert entrypoint.call_args.kwargs == {
            "host": "127.0.0.1",
            "port": 8000,
            "enable_arbitrary_command": True,
            "allow_remote": False,
            "audit_level": None,
        }
    else:
        assert entrypoint.call_args.kwargs == {
            "enable_arbitrary_command": True,
            "audit_level": None,
        }


def test_serve_rejects_an_unknown_audit_level():
    """A misspelled --audit-level is a usage error naming the valid levels."""
    with patch("hmc_mcp.server.main_stdio") as main_stdio:
        result = CliRunner().invoke(
            app, ["serve", "--audit-level", "LOUD", *POLICY_ARGS]
        )

    assert result.exit_code == 2
    output = unstyle(result.output)
    assert "LOUD" in output
    for name in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        assert name in output
    main_stdio.assert_not_called()


@pytest.mark.parametrize("level_name", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
@pytest.mark.parametrize("http", [False, True])
def test_serve_forwards_the_audit_level(http, level_name):
    args = ["serve", "--audit-level", level_name, *POLICY_ARGS]
    target = "hmc_mcp.server.main_http" if http else "hmc_mcp.server.main_stdio"
    if http:
        args.append("--http")

    with patch(target) as entrypoint:
        result = CliRunner().invoke(app, args)

    assert result.exit_code == 0, result.output
    expected = getattr(logging, level_name)
    assert entrypoint.call_args.kwargs["audit_level"] == expected


def test_an_explicit_audit_level_is_set_before_the_sink_installs():
    """#270: the operator's level survives install_audit_sink's NOTSET default.

    Order is the whole feature: a level set after the install would be
    indistinguishable from the sink's own default.
    """
    logger = logging.getLogger(audit.AUDIT_LOGGER_NAME)
    logger.setLevel(logging.NOTSET)
    server_app._serve_application(False, _legacy(), audit_level=logging.WARNING)

    assert logger.level == logging.WARNING
    assert len(logger.handlers) == 1, "the sink still installed exactly once"


def test_omitting_the_audit_level_leaves_the_documented_default():
    """No flag, no setLevel: the sink's own NOTSET rule picks INFO."""
    logger = logging.getLogger(audit.AUDIT_LOGGER_NAME)
    logger.setLevel(logging.NOTSET)
    with patch.object(FastMCP, "run"):
        server_app.main_stdio(_legacy())

    assert logger.level == logging.INFO


@pytest.mark.parametrize("enabled", [False, True])
def test_stdio_entry_point_gates_the_escape_hatch(enabled, monkeypatch):
    calls = []

    async def _record(flag, application, *, permits=None, authorize=None):
        calls.append((flag, permits, authorize))

    monkeypatch.setattr(server_app, "configure_arbitrary_command_tool", _record)
    with patch.object(FastMCP, "run") as run:
        server_app.main_stdio(_legacy(), enable_arbitrary_command=enabled)

    # Both gates, never None: a site given one without the other registers tools it
    # does not authorize, and ADR 0041 removed the composition where None meant
    # "no policy" rather than "a bug".
    [(flag, permits, authorize)] = calls
    assert flag is enabled
    assert permits is not None and authorize is not None
    run.assert_called_once_with()


@pytest.mark.parametrize("enabled", [False, True])
def test_http_entry_point_gates_the_escape_hatch(enabled, monkeypatch):
    calls = []

    async def _record(flag, application, *, permits=None, authorize=None):
        calls.append((flag, permits, authorize))

    monkeypatch.setattr(server_app, "configure_arbitrary_command_tool", _record)
    with patch.object(FastMCP, "run") as run:
        server_app.main_http(
            _legacy(), host="127.0.0.1", port=9000, enable_arbitrary_command=enabled
        )

    [(flag, permits, authorize)] = calls
    assert flag is enabled
    assert permits is not None and authorize is not None
    run.assert_called_once_with(
        transport="streamable-http",
        host="127.0.0.1",
        port=9000,
        uvicorn_config={"log_config": None},
    )


def test_http_entrypoint_refuses_remote_bind_without_authorization():
    with patch.object(FastMCP, "run") as run:
        with pytest.raises(ValueError, match="binds beyond loopback"):
            server_app.main_http(_legacy(), host="0.0.0.0")

    run.assert_not_called()


def test_http_entrypoint_accepts_remote_bind_with_authorization():
    with patch.object(FastMCP, "run") as run:
        server_app.main_http(_legacy(), host="0.0.0.0", allow_remote=True)

    run.assert_called_once_with(
        transport="streamable-http",
        host="0.0.0.0",
        port=8000,
        uvicorn_config={"log_config": None},
    )


def test_serve_http_without_a_policy_refuses_before_binding():
    """ADR 0041's refusal is not stdio-only: --http reaches it too, and binds nothing.

    tests/app/test_fail_closed_startup.py pins the stdio path and the exit codes; this
    pins that the HTTP transport cannot slip past the same gate, which matters more
    there — that listener is unauthenticated.
    """
    with patch("hmc_mcp.server.main_http") as main_http:
        result = CliRunner().invoke(app, ["serve", "--http"])

    assert result.exit_code == 2
    assert "config init-access-policy" in unstyle(result.output)
    main_http.assert_not_called()
