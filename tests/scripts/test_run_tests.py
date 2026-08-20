"""Tests for the compact pytest output adapter."""

import importlib.util
import inspect
import io
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

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
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return super().read(size)


class RecordingBuffer(io.BytesIO):
    """A binary sink that records each replayed chunk."""

    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> int:
        self.chunks.append(data)
        return super().write(data)


class BinaryStderr:
    """Minimal stderr replacement exposing only its byte buffer."""

    def __init__(self) -> None:
        self.buffer = RecordingBuffer()


def _stub_pytest(
    monkeypatch: pytest.MonkeyPatch, output: bytes, returncode: int
) -> tuple[list[tuple[list[str], dict[str, object]]], TrackingTemporaryFile]:
    calls: list[tuple[list[str], dict[str, object]]] = []
    temporary_file = TrackingTemporaryFile()

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        captured_output = kwargs["stdout"]
        assert isinstance(captured_output, TrackingTemporaryFile)
        captured_output.write(output)
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(run_tests.tempfile, "TemporaryFile", lambda: temporary_file)
    monkeypatch.setattr(run_tests.subprocess, "run", fake_run)
    return calls, temporary_file


def _assert_pytest_invocation(
    calls: list[tuple[list[str], dict[str, object]]], output: TrackingTemporaryFile
) -> None:
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == [sys.executable, "-m", "pytest"]
    assert kwargs["check"] is False
    assert kwargs["stdout"] is output
    assert kwargs["stderr"] is subprocess.STDOUT
    assert "env" not in kwargs


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


def test_main_accepts_no_arguments() -> None:
    assert list(inspect.signature(run_tests.main).parameters) == []
