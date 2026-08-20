"""Tests for the compact pytest output adapter."""

import importlib.util
import inspect
import io
import os
import signal
import time
from typing import Any
import subprocess
import sys
from pathlib import Path

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


def _stub_pytest(
    monkeypatch: pytest.MonkeyPatch, output: bytes, returncode: int
) -> tuple[list[tuple[list[str], dict[str, object]]], TrackingTemporaryFile]:
    calls: list[tuple[list[str], dict[str, object]]] = []
    temporary_file = TrackingTemporaryFile()

    class FakeProcess:
        stdout = io.BytesIO(output)

        def __init__(self) -> None:
            self.returncode = returncode

        def wait(self, timeout: int | None = None) -> int:
            return self.returncode

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
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
    assert kwargs["stdout"] is subprocess.PIPE
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

    class InterruptingStream(io.BytesIO):
        def __init__(self) -> None:
            super().__init__(output)
            self.read_count = 0

        def read(self, size: int | None = -1) -> bytes:
            self.read_count += 1
            if self.read_count == 2:
                raise KeyboardInterrupt
            return super().read(size)

    class InterruptedProcess:
        stdout = InterruptingStream()
        returncode = 2

        def wait(self, timeout: int | None = None) -> int:
            return self.returncode

    monkeypatch.setattr(run_tests.sys, "stderr", stderr)
    monkeypatch.setattr(run_tests.tempfile, "TemporaryFile", lambda: temporary_file)
    monkeypatch.setattr(
        run_tests.subprocess, "Popen", lambda _command, **_kwargs: InterruptedProcess()
    )

    assert run_tests.main() == 130

    assert stderr.buffer.getvalue() == output
    assert temporary_file.closed


def test_main_accepts_no_arguments() -> None:
    assert list(inspect.signature(run_tests.main).parameters) == []


def test_signal_return_code_maps_to_shell_status() -> None:
    assert run_tests._exit_status(-signal.SIGTERM) == 128 + signal.SIGTERM


def test_capture_limit_is_bounded() -> None:
    assert 0 < run_tests.CAPTURE_LIMIT <= 4 * 1024 * 1024


def test_capture_switches_to_live_output_at_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = b"prefix"
    overflow = b"overflow"
    stderr = BinaryStderr()
    output = TrackingTemporaryFile()
    monkeypatch.setattr(run_tests, "CAPTURE_LIMIT", len(prefix))
    monkeypatch.setattr(run_tests, "CHUNK_SIZE", len(prefix))
    monkeypatch.setattr(run_tests.sys, "stderr", stderr)

    assert run_tests._capture(io.BytesIO(prefix + overflow), output)

    assert output.getvalue() == prefix
    assert stderr.buffer.getvalue() == prefix + overflow


def test_real_interrupt_preserves_pytest_diagnostic(tmp_path: Path) -> None:
    tmp_path.joinpath("test_slow.py").write_text(
        "import time\n\ndef test_slow():\n    time.sleep(30)\n"
    )
    process = subprocess.Popen(
        [sys.executable, str(MODULE_PATH)],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    time.sleep(1)
    os.killpg(process.pid, signal.SIGINT)
    stdout, stderr = process.communicate(timeout=10)

    assert process.returncode == 130
    assert stdout == b""
    assert b"KeyboardInterrupt" in stderr
