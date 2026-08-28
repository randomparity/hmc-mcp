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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from conftest import make_config
from hmc_mcp.client import HMCClient
from hmc_mcp.ssh.console import (
    HELD_SENTINEL,
    MAX_CAPTURE_BYTES,
    MAX_CAPTURE_SECONDS,
    ConsoleCapture,
    ConsoleHeldError,
    _SealedStdin,
    _probe_released,
    _truncate,
    capture_lpar_console,
)
from hmc_mcp.server_tools import console as server_console

BANNER = b"\r\n Open in progress  \r\n "


def _client() -> HMCClient:
    return HMCClient(make_config())


# The exact recorded P1 contention output, quirks included.
CONTENTION = (
    b"\r\n A terminal session is already open for this partition. \r\n"
    b" Only one open session is allowed for a partition. \r\n Exiting.... "
)


class FakeStdout:
    """Replays scripted reads; a final ``None`` means 'never returns'."""

    def __init__(
        self,
        *chunks: bytes | None,
        blocked_read_started: asyncio.Event | None = None,
    ):
        self._chunks = list(chunks)
        self._blocked_read_started = blocked_read_started

    async def read(self, size: int) -> bytes:
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0)
        if chunk is None:
            if self._blocked_read_started is not None:
                self._blocked_read_started.set()
            await asyncio.Event().wait()  # cancelled by the caller's timeout
        return chunk


class FakeProcess:
    def __init__(
        self,
        *chunks: bytes | None,
        blocked_read_started: asyncio.Event | None = None,
    ):
        self.stdout = FakeStdout(
            *chunks,
            blocked_read_started=blocked_read_started,
        )


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
            "hmc_mcp.ssh.console.open_hmc_connection",
            AsyncMock(return_value=connection),
        ),
        patch(
            "hmc_mcp.ssh.console.run_hmc_command",
            AsyncMock(return_value="Close command sent"),
        ) as release_mock,
        patch("hmc_mcp.ssh.console._RELEASE_PROBE_SECONDS", 0.2),
    ):
        capture = await capture_lpar_console(
            _client(),
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
    with patch("hmc_mcp.ssh.console.open_hmc_connection", AsyncMock()) as connect_mock:
        with pytest.raises(ValueError, match=field):
            await capture_lpar_console(_client(), "sys1", "lp1", **kwargs)
    connect_mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["duration_seconds", "idle_timeout_seconds"])
async def test_nan_time_bounds_are_rejected_before_any_ssh(field):
    kwargs = _capture_kwargs()
    kwargs[field] = float("nan")
    with patch("hmc_mcp.ssh.console.open_hmc_connection", AsyncMock()) as connect_mock:
        with pytest.raises(ValueError, match=field):
            await capture_lpar_console(_client(), "sys1", "lp1", **kwargs)
    connect_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# Contention (P1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contention_sentinel_raises_distinct_error_and_never_releases():
    connection = FakeConnection([FakeProcess(CONTENTION)])
    with (
        patch(
            "hmc_mcp.ssh.console.open_hmc_connection",
            AsyncMock(return_value=connection),
        ),
        patch("hmc_mcp.ssh.console.run_hmc_command", AsyncMock()) as release_mock,
    ):
        with pytest.raises(ConsoleHeldError) as excinfo:
            await capture_lpar_console(
                _client(), "sys1", "lp1", **_capture_kwargs(idle_timeout_seconds=0.05)
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
            "hmc_mcp.ssh.console.open_hmc_connection",
            AsyncMock(return_value=connection),
        ),
        patch("hmc_mcp.ssh.console.run_hmc_command", AsyncMock()),
    ):
        with pytest.raises(ConsoleHeldError):
            await capture_lpar_console(
                _client(),
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
    connection = FakeConnection([FakeProcess(BANNER, b"tail")])
    capture = await _run_capture(connection, duration_seconds=10.0)
    assert capture.stop_reason == "remote-close"
    assert capture.data == BANNER + b"tail"
    assert capture.error is None


@pytest.mark.asyncio
async def test_transport_failure_yields_error_stop_reason_and_still_releases():
    class ExplodingStdout(FakeStdout):
        def __init__(self):
            super().__init__(BANNER)

        async def read(self, size: int) -> bytes:
            if self._chunks:
                return await super().read(size)
            raise ConnectionResetError("TCP dropped")

    connection = FakeConnection([FakeProcess()])
    connection._processes[0].stdout = ExplodingStdout()
    capture = await _run_capture(connection)
    assert capture.stop_reason == "error"
    assert capture.data == BANNER
    assert capture.error == "ConnectionResetError: TCP dropped"
    # P3: the HMC does not auto-release after a transport failure.
    assert len(capture.release_calls) >= 1


@pytest.mark.asyncio
async def test_transport_error_detail_is_single_line_and_bounded():
    class ExplodingStdout(FakeStdout):
        def __init__(self):
            super().__init__(BANNER)

        async def read(self, size: int) -> bytes:
            if self._chunks:
                return await super().read(size)
            raise ConnectionResetError("TCP dropped\n\x1b[31m" + "x" * 400)

    connection = FakeConnection([FakeProcess()])
    connection._processes[0].stdout = ExplodingStdout()

    capture = await _run_capture(connection)

    assert capture.error is not None
    assert capture.error.startswith("ConnectionResetError: TCP dropped")
    assert len(capture.error) == 256
    assert "\n" not in capture.error
    assert "\x1b" not in capture.error


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
async def test_release_probe_teardown_failure_is_not_reported_as_released():
    from hmc_mcp.ssh.transport import HMCCLIError

    connection = FakeConnection([FakeProcess(BANNER), FakeProcess(BANNER)])
    release = AsyncMock(
        side_effect=["Close command sent", HMCCLIError("probe teardown failed")]
    )
    with (
        patch(
            "hmc_mcp.ssh.console.open_hmc_connection",
            AsyncMock(return_value=connection),
        ),
        patch("hmc_mcp.ssh.console.run_hmc_command", release),
        patch("hmc_mcp.ssh.console._RELEASE_PROBE_SECONDS", 0.2),
    ):
        capture = await capture_lpar_console(
            _client(), "sys1", "lp1", **_capture_kwargs()
        )

    assert release.await_count == 2
    assert capture.released is False


@pytest.mark.asyncio
async def test_rmvterm_exit_zero_alone_is_not_proof():
    # P2: rmvterm says "Close command sent" (exit 0) but the slot is still
    # held — the probe sees the sentinel, so released stays False.
    connection = FakeConnection([FakeProcess(BANNER), FakeProcess(CONTENTION)])
    capture = await _run_capture(connection)
    assert capture.released is False


@pytest.mark.asyncio
async def test_fragmented_probe_contention_sentinel_is_not_acquisition():
    split = len(HELD_SENTINEL) // 2
    contention_chunks = (
        b"\r\n " + HELD_SENTINEL[:split],
        HELD_SENTINEL[split:] + b" \r\n Exiting.... ",
    )
    connection = FakeConnection([FakeProcess(BANNER), FakeProcess(*contention_chunks)])

    capture = await _run_capture(connection)

    assert capture.released is False


@pytest.mark.asyncio
async def test_failed_rmvterm_still_probes_and_reports_honestly():
    # rmvterm fails (HMCCLIError, as run_hmc_command raises); the probe then
    # finds the sentinel — released stays False instead of being asserted.
    from hmc_mcp.ssh.transport import HMCCLIError

    connection = FakeConnection([FakeProcess(BANNER), FakeProcess(CONTENTION)])
    with (
        patch(
            "hmc_mcp.ssh.console.open_hmc_connection",
            AsyncMock(return_value=connection),
        ),
        patch(
            "hmc_mcp.ssh.console.run_hmc_command",
            AsyncMock(side_effect=HMCCLIError("rmvterm failed")),
        ),
    ):
        capture = await capture_lpar_console(
            _client(), "sys1", "lp1", **_capture_kwargs()
        )
    assert capture.released is False


@pytest.mark.asyncio
async def test_release_probe_closes_connection_when_process_start_fails():
    connection = FakeConnection([])
    with patch(
        "hmc_mcp.ssh.console.open_hmc_connection",
        AsyncMock(return_value=connection),
    ):
        released = await _probe_released(make_config(), "sys1", "lp1")

    assert released is False
    assert connection.closed is True


@pytest.mark.asyncio
async def test_release_probe_timeout_without_output_does_not_issue_rmvterm():
    connection = FakeConnection([FakeProcess(None)])
    release = AsyncMock()
    with (
        patch(
            "hmc_mcp.ssh.console.open_hmc_connection",
            AsyncMock(return_value=connection),
        ),
        patch("hmc_mcp.ssh.console.run_hmc_command", release),
        patch("hmc_mcp.ssh.console._RELEASE_PROBE_SECONDS", 0.01),
    ):
        released = await _probe_released(make_config(), "sys1", "lp1")

    assert released is False
    assert connection.closed is True
    release.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_still_runs_release_to_completion():
    # P4: cancelling the task mid-stream must not leak the vterm; the shielded
    # release runs to completion before the cancellation propagates.
    capture_started = asyncio.Event()
    connection = FakeConnection(
        [FakeProcess(BANNER, None, blocked_read_started=capture_started)]
    )
    seen_commands: list[str] = []

    async def fake_run_command(config, cmd):
        seen_commands.append(cmd)
        return "Close command sent"

    with (
        patch(
            "hmc_mcp.ssh.console.open_hmc_connection",
            AsyncMock(return_value=connection),
        ),
        patch(
            "hmc_mcp.ssh.console.run_hmc_command",
            AsyncMock(side_effect=fake_run_command),
        ),
    ):
        task = asyncio.ensure_future(
            capture_lpar_console(
                _client(),
                "sys1",
                "lp1",
                **_capture_kwargs(duration_seconds=30.0, idle_timeout_seconds=30.0),
            )
        )
        await capture_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert any(cmd.startswith("rmvterm ") for cmd in seen_commands)


@pytest.mark.asyncio
async def test_cancellation_during_acquisition_releases_proven_session():
    entered = asyncio.Event()
    finish = asyncio.Event()
    process = FakeProcess(BANNER, None)
    connection = FakeConnection([])

    async def blocked_create_process(command: str, **kwargs):
        connection.create_process_calls.append({"command": command, **kwargs})
        entered.set()
        await finish.wait()
        return process

    connection.create_process = blocked_create_process
    release = AsyncMock(return_value=True)
    with (
        patch(
            "hmc_mcp.ssh.console.open_hmc_connection",
            AsyncMock(return_value=connection),
        ),
        patch("hmc_mcp.ssh.console._release_uncancellable", release),
    ):
        task = asyncio.create_task(
            capture_lpar_console(
                _client(),
                "sys1",
                "lp1",
                **_capture_kwargs(duration_seconds=30.0, idle_timeout_seconds=30.0),
            )
        )
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    release.assert_awaited_once_with(make_config(), "sys1", "lp1")
    assert connection.closed is True


@pytest.mark.asyncio
async def test_cancellation_during_contended_acquisition_never_releases_holder():
    entered = asyncio.Event()
    finish = asyncio.Event()
    connection = FakeConnection([])

    async def blocked_create_process(command: str, **kwargs):
        connection.create_process_calls.append({"command": command, **kwargs})
        entered.set()
        await finish.wait()
        return FakeProcess(CONTENTION)

    connection.create_process = blocked_create_process
    release = AsyncMock(return_value=True)
    with (
        patch(
            "hmc_mcp.ssh.console.open_hmc_connection",
            AsyncMock(return_value=connection),
        ),
        patch("hmc_mcp.ssh.console._release_uncancellable", release),
    ):
        task = asyncio.create_task(
            capture_lpar_console(
                _client(),
                "sys1",
                "lp1",
                **_capture_kwargs(duration_seconds=30.0, idle_timeout_seconds=30.0),
            )
        )
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    release.assert_not_awaited()
    assert connection.closed is True


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


@pytest.mark.parametrize("introducer", [b"P", b"X", b"^", b"_"])
def test_truncation_keeps_string_sequence_terminated_at_limit(
    introducer: bytes,
) -> None:
    complete = b"ab\x1b" + introducer + b"payload\x1b\\"
    data = complete + b"tail"

    assert _truncate(data, len(complete)) == complete


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


@pytest.mark.parametrize(
    ("system_selector", "lpar_selector", "system_name", "lpar_name"),
    [
        ("system-a", "aix-db", "system-a", "aix-db"),
        (
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
            "resolved-system",
            "resolved-lpar",
        ),
    ],
)
def test_capture_tool_resolves_identity_and_forwards_bounds(
    system_selector: str,
    lpar_selector: str,
    system_name: str,
    lpar_name: str,
):
    client = MagicMock()
    client.config = MagicMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)
    resolve_system_uuid = AsyncMock(return_value="system-uuid")
    resolve_lpar_uuid = AsyncMock(return_value="lpar-uuid")
    resolve_system_name = AsyncMock(return_value="resolved-system")
    resolve_lpar_name = AsyncMock(return_value="resolved-lpar")
    capture = AsyncMock(
        return_value=ConsoleCapture(
            system=system_name,
            lpar=lpar_name,
            data=b"\x00console\xff",
            stop_reason="idle",
            released=True,
        )
    )

    with (
        patch("hmc_mcp._app.client_from_env", return_value=context) as factory,
        patch.object(server_console, "resolve_system_uuid", resolve_system_uuid),
        patch.object(server_console, "resolve_lpar_uuid", resolve_lpar_uuid),
        patch.object(server_console, "resolve_system_name", resolve_system_name),
        patch.object(server_console, "resolve_lpar_cli_name", resolve_lpar_name),
        patch.object(server_console, "capture_lpar_console", capture),
    ):
        result = server_console.hmc_capture_lpar_console(
            lpar_selector,
            system_selector,
            duration_seconds=12.5,
            max_bytes=4096,
            idle_timeout_seconds=3.5,
            profile="lab",
        )

    factory.assert_called_once_with("lab")
    resolve_system_uuid.assert_awaited_once_with(client, system_selector)
    resolve_lpar_uuid.assert_awaited_once_with(
        client, lpar_selector, system_name_or_uuid="system-uuid"
    )
    capture.assert_awaited_once_with(
        client,
        system_name,
        lpar_name,
        duration_seconds=12.5,
        max_bytes=4096,
        idle_timeout_seconds=3.5,
    )
    context.__aexit__.assert_awaited_once()
    assert resolve_system_name.await_count == int(system_selector != system_name)
    assert resolve_lpar_name.await_count == int(lpar_selector != lpar_name)
    assert result == {
        "system": system_name,
        "partition": lpar_name,
        "stop_reason": "idle",
        "released": True,
        "error": None,
        "bytes_captured": 9,
        "data_base64": base64.b64encode(b"\x00console\xff").decode("ascii"),
    }
