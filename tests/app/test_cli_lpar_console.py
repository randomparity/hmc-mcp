"""CLI contract for ``hmcpctl lpars capture-console`` (issue #959, ADR 0175)."""

from __future__ import annotations

import errno
import inspect
import io
from typing import Self
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from hmcpctl import cli
from hmcpctl.cli_commands import runtime as cli_runtime
from hmcpctl.cli_commands.lpar import console as cli_console
from hmcpctl.server_tools.console import hmc_capture_lpar_console
from hmcpctl.ssh.console import (
    MAX_CAPTURE_BYTES,
    MAX_CAPTURE_SECONDS,
    ConsoleCapture,
    ConsoleHeldError,
)

RUNNER = CliRunner()
DATA = b"boot\n\x1b[31m\xff\x00"
COMMAND = ["lpars", "capture-console", "aix-db", "--system", "system-a"]


class _Client:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


def _capture(
    *, stop_reason: str = "idle", released: bool = True, error: str | None = None
) -> ConsoleCapture:
    return ConsoleCapture(
        system="system-a",
        lpar="aix-db",
        data=DATA,
        stop_reason=stop_reason,  # type: ignore[arg-type]
        released=released,
        error=error,
    )


@pytest.fixture
def client(monkeypatch) -> _Client:
    hmc = _Client()
    monkeypatch.setattr(cli_runtime, "client", lambda: hmc)
    return hmc


@pytest.fixture
def capture(monkeypatch, client) -> AsyncMock:
    mock = AsyncMock(return_value=_capture())
    monkeypatch.setattr(cli_console, "capture_lpar_console_by_selector", mock)
    return mock


def test_options_reach_the_capture_and_bytes_reach_stdout(client, capture):
    result = RUNNER.invoke(
        cli.app,
        [
            *COMMAND[:3],
            "-s",
            "system-a",
            "--duration",
            "12.5",
            "--max-bytes",
            "4096",
            "--idle-timeout",
            "3.5",
        ],
    )

    assert result.exit_code == 0, result.stderr
    capture.assert_awaited_once_with(
        client,
        "aix-db",
        "system-a",
        duration_seconds=12.5,
        max_bytes=4096,
        idle_timeout_seconds=3.5,
    )
    assert result.stdout_bytes == DATA
    assert f"stop reason: idle; bytes: {len(DATA)}; released: true" in result.stderr


def test_defaults_match_the_mcp_tool(client, capture):
    result = RUNNER.invoke(cli.app, COMMAND)

    assert result.exit_code == 0, result.stderr
    tool = inspect.signature(hmc_capture_lpar_console).parameters
    assert capture.await_args.kwargs == {
        name: tool[name].default
        for name in ("duration_seconds", "max_bytes", "idle_timeout_seconds")
    }


def test_output_file_receives_the_bytes(capture, tmp_path):
    target = tmp_path / "console.log"

    result = RUNNER.invoke(cli.app, [*COMMAND, "--output", str(target)])

    assert result.exit_code == 0, result.stderr
    assert target.read_bytes() == DATA
    assert result.stdout_bytes == b""


def test_output_file_is_allowed_when_stdout_is_a_terminal(
    capture, tmp_path, monkeypatch
):
    monkeypatch.setattr(cli_console, "_stdout_is_terminal", lambda: True)
    target = tmp_path / "console.log"

    result = RUNNER.invoke(cli.app, [*COMMAND, "--output", str(target)])

    assert result.exit_code == 0, result.stderr
    assert target.read_bytes() == DATA


@pytest.mark.parametrize(
    "extra",
    [
        ["--duration", "0"],
        ["--duration", "nan"],
        ["--duration", str(MAX_CAPTURE_SECONDS + 1)],
        ["--max-bytes", "0"],
        ["--max-bytes", str(MAX_CAPTURE_BYTES + 1)],
        ["--idle-timeout", "0"],
        ["--idle-timeout", "inf"],
    ],
)
def test_out_of_range_bound_is_a_usage_error_before_any_capture(capture, extra):
    result = RUNNER.invoke(cli.app, [*COMMAND, *extra])

    assert result.exit_code == 2
    assert extra[0] in result.stderr
    capture.assert_not_awaited()


def test_existing_output_file_is_refused_before_any_capture(capture, tmp_path):
    target = tmp_path / "console.log"
    target.write_bytes(b"keep me")

    result = RUNNER.invoke(cli.app, [*COMMAND, "--output", str(target)])

    assert result.exit_code == 2
    assert "already exists" in result.stderr
    assert target.read_bytes() == b"keep me"
    capture.assert_not_awaited()


def test_terminal_stdout_without_output_is_refused_before_any_capture(
    capture, monkeypatch
):
    monkeypatch.setattr(cli_console, "_stdout_is_terminal", lambda: True)

    result = RUNNER.invoke(cli.app, COMMAND)

    assert result.exit_code == 2
    assert "--output" in result.stderr
    capture.assert_not_awaited()


def test_contention_exits_1_and_leaves_no_output_file(capture, tmp_path):
    capture.side_effect = ConsoleHeldError("mkvterm found the console held")
    target = tmp_path / "console.log"

    result = RUNNER.invoke(cli.app, [*COMMAND, "--output", str(target)])

    assert result.exit_code == 1
    assert "mkvterm found the console held" in result.stderr
    assert "stop reason" not in result.stderr
    assert not target.exists()


def test_interrupted_capture_removes_the_output_file(capture, tmp_path):
    capture.side_effect = KeyboardInterrupt
    target = tmp_path / "console.log"

    RUNNER.invoke(cli.app, [*COMMAND, "--output", str(target)])

    assert not target.exists()


@pytest.mark.parametrize(
    ("stop_reason", "released", "error", "code"),
    [
        ("duration", True, None, 0),
        ("max_bytes", True, None, 0),
        ("remote-close", True, None, 0),
        ("error", True, "channel reset", 1),
        ("idle", False, None, 3),
        ("error", False, "channel reset", 3),
    ],
)
def test_exit_code_follows_the_capture_outcome_and_bytes_are_written(
    capture, stop_reason, released, error, code
):
    capture.return_value = _capture(
        stop_reason=stop_reason, released=released, error=error
    )

    result = RUNNER.invoke(cli.app, COMMAND)

    assert result.exit_code == code
    assert result.stdout_bytes == DATA
    assert (
        f"stop reason: {stop_reason}; bytes: {len(DATA)}; released: {str(released).lower()}"
        in (result.stderr)
    )
    if error:
        assert f"error: {error}" in result.stderr
    if not released:
        assert "rmvterm" in result.stderr


class _FullDisk(io.RawIOBase):
    def writable(self) -> bool:
        return True

    def write(self, _data) -> int:
        raise OSError(errno.ENOSPC, "No space left on device")


@pytest.mark.parametrize(("released", "code"), [(True, 1), (False, 3)])
def test_failed_write_removes_the_output_file(
    capture, tmp_path, monkeypatch, released, code
):
    capture.return_value = _capture(released=released)
    target = tmp_path / "console.log"
    create = cli_console._create_exclusive

    def full_disk(path):
        create(path).close()
        return io.BufferedWriter(_FullDisk())

    monkeypatch.setattr(cli_console, "_create_exclusive", full_disk)

    result = RUNNER.invoke(cli.app, [*COMMAND, "--output", str(target)])

    assert result.exit_code == code
    assert "No space left on device" in result.stderr
    assert not target.exists()


def test_output_file_is_owner_only(capture, tmp_path):
    target = tmp_path / "console.log"

    result = RUNNER.invoke(cli.app, [*COMMAND, "--output", str(target)])

    assert result.exit_code == 0, result.stderr
    assert target.stat().st_mode & 0o777 == 0o600


def test_rmvterm_hint_quotes_names(capture):
    capture.return_value = ConsoleCapture(
        system="system a", lpar="db;x", data=b"", stop_reason="idle", released=False
    )

    result = RUNNER.invoke(cli.app, COMMAND)

    assert result.exit_code == 3
    assert "rmvterm -m 'system a' -p 'db;x'" in result.stderr


def test_help_lists_every_option():
    result = RUNNER.invoke(cli.app, ["lpars", "capture-console", "--help"])

    assert result.exit_code == 0
    for option in (
        "--system",
        "--duration",
        "--max-bytes",
        "--idle-timeout",
        "--output",
    ):
        assert option in result.stdout
