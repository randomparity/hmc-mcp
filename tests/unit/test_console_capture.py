"""Tests for the bounded LPAR console capture (issue #385, ADR 0072).

asyncssh is mocked at the two seams the capture uses: a long-lived connection
whose ``create_process`` hosts the ``mkvterm`` stream, and the one-shot
``run_hmc_command`` that carries ``rmvterm``. Every exit path must end in a
release attempt, and every assertion about ``released`` mirrors the P2 rule:
only a clean independent-session mkvterm probe proves release.
"""

from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock, patch

import pytest

from conftest import make_config
from hmc_mcp.console_capture import (
    HELD_SENTINEL,
    MAX_CAPTURE_BYTES,
    MAX_CAPTURE_SECONDS,
    ConsoleCapture,
    ConsoleHeldError,
    _SealedStdin,
    _truncate,
    capture_lpar_console,
)

BANNER = b"\r\n Open in progress  \r\n "

# The exact recorded P1 contention output, quirks included.
CONTENTION = (
    b"\r\n A terminal session is already open for this partition. \r\n"
    b" Only one open session is allowed for a partition. \r\n Exiting.... "
)


class FakeStdout:
    """Replays scripted reads; a final ``None`` means 'never returns'."""

    def __init__(self, *chunks: bytes | None):
        self._chunks = list(chunks)

    async def read(self, size: int) -> bytes:
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0)
        if chunk is None:
            await asyncio.Event().wait()  # cancelled by the caller's timeout
        return chunk


class FakeProcess:
    def __init__(self, *chunks: bytes | None):
        self.stdout = FakeStdout(*chunks)


class FakeConnection:
    """Hands out scripted processes and records what was asked of it."""

    def __init__(self, processes: list[FakeProcess]):
        self._processes = list(processes)
        self.create_process_calls: list[dict] = []
        self.closed = False

    async def create_process(self, command: str, **kwargs):
        self.create_process_calls.append({"command": command, **kwargs})
        if not self._processes:
            raise AssertionError("unexpected extra create_process call")
        return self._processes.pop(0)

    def close(self) -> None:
        self.closed = True


def _capture_kwargs(**overrides):
    kwargs = {
        "duration_seconds": 5.0,
        "max_bytes": 65_536,
        "idle_timeout_seconds": 0.05,
    }
    kwargs.update(overrides)
    return kwargs


async def _run_capture(connection: FakeConnection, **overrides) -> ConsoleCapture:
    with (
        patch(
            "hmc_mcp.console_capture.open_hmc_connection",
            AsyncMock(return_value=connection),
        ),
        patch(
            "hmc_mcp.console_capture.run_hmc_command",
            AsyncMock(return_value="Close command sent"),
        ) as release_mock,
        patch("hmc_mcp.console_capture._RELEASE_PROBE_SECONDS", 0.2),
    ):
        capture = await capture_lpar_console(
            make_config(),
            "sys1",
            "lp1",
            **_capture_kwargs(**overrides),
        )
    # ConsoleCapture is frozen; the test seam rides on the side.
    object.__setattr__(capture, "release_calls", release_mock.await_args_list)
    return capture


# ---------------------------------------------------------------------------
# Bounds validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("duration_seconds", 0),
        ("duration_seconds", -1.0),
        ("duration_seconds", MAX_CAPTURE_SECONDS + 1),
        ("max_bytes", 0),
        ("max_bytes", MAX_CAPTURE_BYTES + 1),
        ("idle_timeout_seconds", 0),
    ],
)
async def test_out_of_range_bounds_are_rejected_before_any_ssh(field, value):
    kwargs = _capture_kwargs()
    kwargs[field] = value
    with patch(
        "hmc_mcp.console_capture.open_hmc_connection", AsyncMock()
    ) as connect_mock:
        with pytest.raises(ValueError, match=field):
            await capture_lpar_console(make_config(), "sys1", "lp1", **kwargs)
    connect_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# Contention (P1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contention_sentinel_raises_distinct_error_and_never_releases():
    connection = FakeConnection([FakeProcess(CONTENTION)])
    with (
        patch(
            "hmc_mcp.console_capture.open_hmc_connection",
            AsyncMock(return_value=connection),
        ),
        patch(
            "hmc_mcp.console_capture.run_hmc_command", AsyncMock()
        ) as release_mock,
    ):
        with pytest.raises(ConsoleHeldError) as excinfo:
            await capture_lpar_console(
                make_config(), "sys1", "lp1", **_capture_kwargs(idle_timeout_seconds=0.05)
            )
    assert "already holds" in str(excinfo.value)
    # Exit code was 0 on the real HMC; only the sentinel detects this. And
    # since we never held the vterm, releasing would close the other holder.
    release_mock.assert_not_awaited()
    assert connection.closed


def test_sentinel_matches_the_recorded_p1_bytes():
    assert HELD_SENTINEL in CONTENTION


@pytest.mark.asyncio
async def test_contention_is_detected_when_it_arrives_midstream():
    # The sentinel may only show up after other bytes; detection must not
    # depend on it being the first chunk.
    connection = FakeConnection([FakeProcess(BANNER, CONTENTION)])
    with (
        patch(
            "hmc_mcp.console_capture.open_hmc_connection",
            AsyncMock(return_value=connection),
        ),
        patch("hmc_mcp.console_capture.run_hmc_command", AsyncMock()),
    ):
        with pytest.raises(ConsoleHeldError):
            await capture_lpar_console(
                make_config(),
                "sys1",
                "lp1",
                **_capture_kwargs(duration_seconds=0.2, idle_timeout_seconds=0.2),
            )


# ---------------------------------------------------------------------------
# The three client-side bounds and the remaining stop reasons (P8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duration_bound_fires_on_a_silent_stream():
    connection = FakeConnection([FakeProcess(BANNER, None)])
    capture = await _run_capture(
        connection, duration_seconds=0.15, idle_timeout_seconds=5.0
    )
    assert capture.stop_reason == "duration"
    assert capture.data == BANNER


@pytest.mark.asyncio
async def test_idle_bound_fires_after_silence_since_last_byte():
    connection = FakeConnection([FakeProcess(BANNER, None)])
    capture = await _run_capture(connection, duration_seconds=10.0)
    assert capture.stop_reason == "idle"


@pytest.mark.asyncio
async def test_remote_close_stops_the_capture():
    connection = FakeConnection([FakeProcess(BANNER, B"tail")])
    capture = await _run_capture(connection, duration_seconds=10.0)
    assert capture.stop_reason == "remote-close"
    assert capture.data == BANNER + B"tail"


@pytest.mark.asyncio
async def test_transport_failure_yields_error_stop_reason_and_still_releases():
    class ExplodingStdout(FakeStdout):
        async def read(self, size: int) -> bytes:
            raise ConnectionResetError("TCP dropped")

    connection = FakeConnection([FakeProcess()])
    connection._processes[0].stdout = ExplodingStdout()
    capture = await _run_capture(connection)
    assert capture.stop_reason == "error"
    assert capture.data == b""
    # P3: the HMC does not auto-release after a transport failure.
    assert len(capture.release_calls) >= 1


# ---------------------------------------------------------------------------
# Release honesty (P2/P3/P4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_proven_by_clean_probe_mkterm():
    # Capture ends by remote close; the probe's mkvterm gets the banner (no
    # sentinel) -> slot free -> released True. Both rmvterm calls happen.
    connection = FakeConnection([FakeProcess(BANNER), FakeProcess(BANNER)])
    capture = await _run_capture(connection)
    assert capture.stop_reason == "remote-close"
    assert capture.released is True
    commands = [call.args[1] for call in capture.release_calls]
    assert any(cmd.startswith("rmvterm -m sys1") for cmd in commands)
    assert len(commands) == 2  # our release + the probe session's teardown


@pytest.mark.asyncio
async def test_rmvterm_exit_zero_alone_is_not_proof():
    # P2: rmvterm says "Close command sent" (exit 0) but the slot is still
    # held — the probe sees the sentinel, so released stays False.
    connection = FakeConnection([FakeProcess(BANNER), FakeProcess(CONTENTION)])
    capture = await _run_capture(connection)
    assert capture.released is False


@pytest.mark.asyncio
async def test_failed_rmvterm_still_probes_and_reports_honestly():
    # rmvterm fails (HMCCLIError, as run_hmc_command raises); the probe then
    # finds the sentinel — released stays False instead of being asserted.
    from hmc_mcp.ssh import HMCCLIError

    connection = FakeConnection([FakeProcess(BANNER), FakeProcess(CONTENTION)])
    with (
        patch(
            "hmc_mcp.console_capture.open_hmc_connection",
            AsyncMock(return_value=connection),
        ),
        patch(
            "hmc_mcp.console_capture.run_hmc_command",
            AsyncMock(side_effect=HMCCLIError("rmvterm failed")),
        ),
    ):
        capture = await capture_lpar_console(
            make_config(), "sys1", "lp1", **_capture_kwargs()
        )
    assert capture.released is False


@pytest.mark.asyncio
async def test_cancellation_still_runs_release_to_completion():
    # P4: cancelling the task mid-stream must not leak the vterm; the shielded
    # release runs to completion before the cancellation propagates.
    connection = FakeConnection([FakeProcess(BANNER, None)])
    seen_commands: list[str] = []

    async def fake_run_command(config, cmd):
        seen_commands.append(cmd)
        return "Close command sent"

    with (
        patch(
            "hmc_mcp.console_capture.open_hmc_connection",
            AsyncMock(return_value=connection),
        ),
        patch(
            "hmc_mcp.console_capture.run_hmc_command",
            AsyncMock(side_effect=fake_run_command),
        ),
    ):
        task = asyncio.ensure_future(
            capture_lpar_console(
                make_config(),
                "sys1",
                "lp1",
                **_capture_kwargs(duration_seconds=30.0, idle_timeout_seconds=30.0),
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert any(cmd.startswith("rmvterm ") for cmd in seen_commands)


# ---------------------------------------------------------------------------
# Sealed stdin (P7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stdin_is_a_pipe_write_end_that_nothing_can_write():
    connection = FakeConnection([FakeProcess(BANNER), FakeProcess(BANNER)])
    capture = await _run_capture(connection)
    assert capture.released is True
    stream_call = connection.create_process_calls[0]
    read_fd = stream_call["stdin"]
    assert isinstance(read_fd, int) and read_fd >= 0  # the pipe's read end
    assert stream_call["command"].startswith("mkvterm -m ")
    assert not hasattr(capture, "stdin")
    writer = _SealedStdin()
    assert not any(
        name.startswith("write") or name.startswith("send")
        for name in dir(writer)
        if not name.startswith("_")
    )
    writer.close()


# ---------------------------------------------------------------------------
# Byte integrity at max_bytes (P6)
# ---------------------------------------------------------------------------


def test_truncation_never_splits_multibyte_utf8():
    data = "abcé".encode()  # é = 0xC3 0xA9 straddling a 4-byte cut
    cut = _truncate(data, 4)
    assert cut == b"abc"


def test_truncation_never_splits_an_incomplete_csi_sequence():
    data = b"ok\x1b[31mred\x1b[0m"
    # Cut inside "\x1b[31m": backtracks to before the ESC.
    assert _truncate(data, 6) == b"ok"
    # The completed sequence plus following text cuts normally.
    assert _truncate(data, 11) == b"ok\x1b[31mred"


def test_truncation_backtracks_over_partial_utf8_then_esc():
    data = "aé".encode() + b"\x1b[1;2Hx"
    # The limit lands inside é and inside the CSI sequence: both backtrack.
    assert _truncate(data, 2) == "a".encode() == b"a"
    # The CSI sequence completes at 'H' (index 8), but the cut must fall
    # before the whole incomplete sequence, i.e. before the ESC at index 3.
    assert _truncate(data, 8) == "aé".encode()
    assert _truncate(data, len(data)) == data


def test_truncation_of_pure_ascii_cuts_at_the_limit():
    data = b"x" * 100
    assert _truncate(data, 40) == b"x" * 40


def test_truncation_handles_bare_esc_and_string_sequences():
    assert _truncate(b"ab\x1bcd", 3) == b"ab"  # bare ESC at the cut
    dcs = b"ab\x1bP1;2qdata\x1b\\tail"
    assert _truncate(dcs, 8) == b"ab"  # DCS string not yet terminated
    assert _truncate(dcs, len(dcs)) == dcs  # terminated: no backtrack


@pytest.mark.asyncio
async def test_max_bytes_bound_truncates_to_a_safe_boundary():
    payload = BANNER * 2000  # ~48k of pure ASCII
    connection = FakeConnection([FakeProcess(payload)])
    capture = await _run_capture(connection, max_bytes=1024)
    assert capture.stop_reason == "max_bytes"
    assert len(capture.data) <= 1024


# ---------------------------------------------------------------------------
# MCP surface wiring
# ---------------------------------------------------------------------------


def test_capture_tool_result_carries_contract_fields():
    from hmc_mcp.server import TOOL_SECURITY

    security = TOOL_SECURITY["hmc_capture_lpar_console"]
    assert security.operation == "lpar.capture_console"
    assert security.effect == "mutate"
    assert security.exhaustive_targets is True
    arguments = {(t.kind, t.argument) for t in security.targets}
    assert ("lpar", "lpar_name_or_uuid") in arguments
    assert ("managed_system", "system_name_or_uuid") in arguments


def test_base64_round_trip_preserves_raw_bytes():
    raw = bytes(range(256))
    encoded = base64.b64encode(raw).decode("ascii")
    assert base64.b64decode(encoded) == raw
