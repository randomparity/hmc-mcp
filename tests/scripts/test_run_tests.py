"""Tests for the compact pytest output adapter."""

import contextlib
import importlib.util
import inspect
import io
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

MODULE_PATH = Path(__file__).parents[2] / "scripts" / "run_tests.py"
MODULE_SPEC = importlib.util.spec_from_file_location("run_tests", MODULE_PATH)
assert MODULE_SPEC is not None
assert MODULE_SPEC.loader is not None
run_tests = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(run_tests)


class TrackingTemporaryFile(io.BytesIO):
    """A binary temporary file that records copy reads and context closure."""

    def __init__(self) -> None:
        super().__init__()
        self.read_sizes: list[int | None] = []

    def read(self, size: int | None = -1) -> bytes:
        self.read_sizes.append(size)
        return super().read(size)


class RecordingBuffer(io.BytesIO):
    """A binary sink that records each replayed chunk."""

    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[bytes] = []

    def write(self, data: Any) -> int:
        chunk = bytes(data)
        self.chunks.append(chunk)
        return super().write(chunk)


class BinaryStderr:
    """Minimal stderr replacement exposing only its byte buffer."""

    def __init__(self) -> None:
        self.buffer = RecordingBuffer()


# A hang ceiling, not a latency budget: the poll loop below returns the moment
# the marker appears, so only a child that never becomes ready ever pays this.
_READINESS_TIMEOUT_SECONDS = 60.0

# Covers what `run_tests._settle_interrupted` does not bound -- the post-kill
# reap, the captured-output replay, and the parent's own teardown.
_INTERRUPT_COLLECTION_SLACK_SECONDS = 10.0


def _wait_for_process_marker(
    marker: Path,
    process: subprocess.Popen[bytes],
    timeout_seconds: float = _READINESS_TIMEOUT_SECONDS,
) -> None:
    """Wait until the child declares readiness or exits unexpectedly."""
    deadline = time.monotonic() + timeout_seconds
    while not marker.exists():
        if process.poll() is not None:
            pytest.fail(f"child exited with {process.returncode} before becoming ready")
        if time.monotonic() >= deadline:
            # start_new_session makes the child a group leader, so process.kill()
            # alone would strand the grandchild pytest.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            pytest.fail(f"child did not create readiness marker {marker}")
        time.sleep(0.01)


def _stub_pytest(
    monkeypatch: pytest.MonkeyPatch, output: bytes, returncode: int
) -> tuple[list[tuple[list[str], dict[str, object]]], TrackingTemporaryFile]:
    calls: list[tuple[list[str], dict[str, object]]] = []
    temporary_file = TrackingTemporaryFile()

    class FakeProcess:
        def __init__(self) -> None:
            self.returncode = returncode

        def wait(self, timeout: int | None = None) -> int:
            return self.returncode

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        captured_output = kwargs["stdout"]
        assert isinstance(captured_output, TrackingTemporaryFile)
        captured_output.write(output)
        calls.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(run_tests.tempfile, "TemporaryFile", lambda: temporary_file)
    monkeypatch.setattr(run_tests.subprocess, "Popen", fake_popen)
    return calls, temporary_file


def _assert_pytest_invocation(
    calls: list[tuple[list[str], dict[str, object]]], output: TrackingTemporaryFile
) -> None:
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == [sys.executable, "-m", "pytest"]
    assert kwargs["stdout"] is output
    assert kwargs["stderr"] is subprocess.STDOUT
    environment = kwargs["env"]
    assert isinstance(environment, dict)
    assert environment == {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTEST_ADDOPTS", "COVERAGE_RCFILE", "COVERAGE_FILE"}
    }
    assert "cwd" not in kwargs


def test_success_hides_noisy_pytest_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls, temporary_file = _stub_pytest(
        monkeypatch, b"================ noisy pytest output ================\n", 0
    )

    assert run_tests.main() == 0

    captured = capsys.readouterr()
    assert captured.out == "test: passed; configured coverage gate passed\n"
    assert "noisy pytest output" not in captured.out
    assert "noisy pytest output" not in captured.err
    _assert_pytest_invocation(calls, temporary_file)
    assert temporary_file.read_sizes == []
    assert temporary_file.closed


def test_success_hides_output_larger_than_one_mebibyte(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _calls, temporary_file = _stub_pytest(monkeypatch, b"x" * (2 * 1024 * 1024), 0)

    assert run_tests.main() == 0

    captured = capsys.readouterr()
    assert captured.out == "test: passed; configured coverage gate passed\n"
    assert captured.err == ""
    assert temporary_file.closed


def test_failure_replays_pytest_output_as_bytes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = b"FAILED test_example.py::test_case\n"
    stderr = BinaryStderr()
    monkeypatch.setattr(run_tests.sys, "stderr", stderr)
    calls, temporary_file = _stub_pytest(monkeypatch, output, 1)

    assert run_tests.main() == 1

    assert stderr.buffer.getvalue() == output
    assert capsys.readouterr().out == ""
    _assert_pytest_invocation(calls, temporary_file)
    assert temporary_file.closed


def test_failure_preserves_invalid_utf8_without_decoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = b"FAILED test_example.py::test_case \xff\n"
    stderr = BinaryStderr()
    monkeypatch.setattr(run_tests.sys, "stderr", stderr)
    _calls, temporary_file = _stub_pytest(monkeypatch, output, 1)

    assert run_tests.main() == 1

    assert stderr.buffer.getvalue() == output
    assert temporary_file.closed


def test_failure_replays_multiple_bounded_binary_chunks_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk_size = 64 * 1024
    output = b"a" * chunk_size + b"b" * chunk_size + b"\xfftail"
    stderr = BinaryStderr()
    monkeypatch.setattr(run_tests.sys, "stderr", stderr)
    _calls, temporary_file = _stub_pytest(monkeypatch, output, 1)

    assert run_tests.main() == 1

    assert stderr.buffer.getvalue() == output
    assert stderr.buffer.chunks == [
        b"a" * chunk_size,
        b"b" * chunk_size,
        b"\xfftail",
    ]
    assert temporary_file.read_sizes == [chunk_size] * 4
    assert temporary_file.closed


def test_interruption_replays_captured_output_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = b"partial pytest diagnostic before SIGINT \xff\n"
    stderr = BinaryStderr()
    temporary_file = TrackingTemporaryFile()

    class InterruptedProcess:
        returncode = 2
        wait_count = 0

        def wait(self, timeout: int | None = None) -> int:
            self.wait_count += 1
            if self.wait_count == 1:
                temporary_file.write(output)
                raise KeyboardInterrupt
            return self.returncode

    monkeypatch.setattr(run_tests.sys, "stderr", stderr)
    monkeypatch.setattr(run_tests.tempfile, "TemporaryFile", lambda: temporary_file)
    monkeypatch.setattr(
        run_tests.subprocess, "Popen", lambda _command, **_kwargs: InterruptedProcess()
    )

    assert run_tests.main() == 130

    assert stderr.buffer.getvalue() == output
    assert temporary_file.closed


def test_timeout_terminates_pytest_and_returns_timeout_status(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    temporary_file = TrackingTemporaryFile()

    class TimedOutProcess:
        returncode = 143
        wait_count = 0

        def wait(self, timeout: int | None = None) -> int:
            self.wait_count += 1
            if self.wait_count == 1:
                temporary_file.write(b"pytest stalled\n")
                raise subprocess.TimeoutExpired(["pytest"], timeout)
            return self.returncode

        def terminate(self) -> None:
            return None

        def kill(self) -> None:
            return None

    process = TimedOutProcess()
    monkeypatch.setattr(run_tests.tempfile, "TemporaryFile", lambda: temporary_file)
    monkeypatch.setattr(run_tests.subprocess, "Popen", lambda _command, **_kwargs: process)

    assert run_tests.main() == 124

    error_output = capsys.readouterr().err
    assert "pytest stalled" in error_output
    assert "timed out" in error_output
    assert process.wait_count == 2
    assert temporary_file.closed


def test_main_accepts_no_arguments() -> None:
    assert list(inspect.signature(run_tests.main).parameters) == []


def test_signal_return_code_maps_to_shell_status() -> None:
    assert run_tests._exit_status(-signal.SIGTERM) == 128 + signal.SIGTERM


def test_real_interrupt_preserves_pytest_diagnostic(tmp_path: Path) -> None:
    ready = tmp_path / "pytest-ready"
    tmp_path.joinpath("test_slow.py").write_text(
        "import pathlib\nimport time\n\ndef test_slow():\n"
        "    pathlib.Path('pytest-ready').touch()\n    time.sleep(30)\n"
    )
    process = subprocess.Popen(
        [sys.executable, str(MODULE_PATH)],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    _wait_for_process_marker(ready, process)
    os.killpg(process.pid, signal.SIGINT)
    signalled_at = time.monotonic()
    grace = run_tests.INTERRUPT_GRACE_SECONDS
    budget = 2 * grace + _INTERRUPT_COLLECTION_SLACK_SECONDS
    try:
        stdout, stderr = process.communicate(timeout=budget)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        _stdout, stderr = process.communicate()
        pytest.fail(
            f"child did not exit within {budget}s of SIGINT; "
            f"stderr tail: {stderr[-2000:]!r}"
        )
    settled_in = time.monotonic() - signalled_at

    assert process.returncode == 130
    assert stdout == b""
    # The settle interval and the grace it ran against are the evidence a reader
    # needs to tell a slow host from a regression. The test does not attribute:
    # a regression that lowered INTERRUPT_GRACE_SECONDS would satisfy any
    # `settled_in >= grace` guard trivially and be reported as host slowness.
    assert b"KeyboardInterrupt" in stderr, (
        f"no KeyboardInterrupt after {settled_in:.1f}s settling "
        f"(run_tests.INTERRUPT_GRACE_SECONDS is {grace}s); "
        f"stderr tail: {stderr[-2000:]!r}"
    )
