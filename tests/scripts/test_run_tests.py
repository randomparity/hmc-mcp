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
from typing import Any, BinaryIO, cast

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


class InterruptingProcess:
    """A child whose first `interrupts` waits raise `KeyboardInterrupt`.

    One stub covers the whole escalation ladder: one interrupt is the ordinary
    Ctrl-C, two reach `terminate()`, three reach `kill()`. `payload` stands in
    for whatever pytest managed to write before the first interrupt.
    """

    returncode = 2

    def __init__(self, interrupts: int, capture: BinaryIO, payload: bytes) -> None:
        self.interrupts = interrupts
        self.capture = capture
        self.payload = payload
        self.wait_count = 0
        self.terminated = False
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        self.wait_count += 1
        if self.wait_count == 1:
            self.capture.write(self.payload)
        if self.wait_count <= self.interrupts:
            raise KeyboardInterrupt
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


# A hang ceiling, not a latency budget: the poll loop below returns the moment
# the marker appears, so only a child that never becomes ready ever pays this.
_READINESS_TIMEOUT_SECONDS = 60.0

# Covers what `run_tests._settle_interrupted` does not bound -- the post-kill
# reap, the captured-output replay, and the parent's own teardown.
_INTERRUPT_COLLECTION_SLACK_SECONDS = 10.0


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    """Kill the child and everything it started.

    `start_new_session=True` makes the child a group leader, so a bare
    `process.kill()` strands the grandchild pytest. Mirrors `_kill_group` in
    `scripts/check_generated_docs.py`, fallback included: the direct child is
    covered by the group kill except when signalling the group was refused.
    """
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError):
        process.kill()


def _wait_for_process_marker(
    marker: Path,
    process: subprocess.Popen[bytes],
    timeout_seconds: float = _READINESS_TIMEOUT_SECONDS,
) -> float:
    """Wait for the child to declare readiness, returning how long that took.

    That figure is what the caller reports. Both it and the post-interrupt
    settle interval move with host load -- ADR 0130 measured them tracking each
    other once `run_tests`'s old 3-second clamp stopped hiding the relation --
    so readiness is reported beside the settle interval, never instead of it.
    """
    started = time.monotonic()
    deadline = started + timeout_seconds
    while not marker.exists():
        if process.poll() is not None:
            pytest.fail(f"child exited with {process.returncode} before becoming ready")
        if time.monotonic() >= deadline:
            _kill_process_group(process)
            pytest.fail(f"child did not create readiness marker {marker}")
        time.sleep(0.01)
    return time.monotonic() - started


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
    process = InterruptingProcess(1, temporary_file, output)

    monkeypatch.setattr(run_tests.sys, "stderr", stderr)
    monkeypatch.setattr(run_tests.tempfile, "TemporaryFile", lambda: temporary_file)
    monkeypatch.setattr(
        run_tests.subprocess, "Popen", lambda _command, **_kwargs: process
    )

    assert run_tests.main() == 130

    assert stderr.buffer.getvalue() == output
    assert temporary_file.closed


def test_diagnostic_window_covers_the_readiness_ceiling() -> None:
    """ADR 0130: no host clearing the readiness ceiling may lose the diagnostic.

    The window is floored by the ceiling so the script and its test move
    together; dropping it below reinstates issue #728 on a slow enough host.
    """
    assert run_tests.INTERRUPT_GRACE_SECONDS >= _READINESS_TIMEOUT_SECONDS
    assert run_tests.TERMINATE_GRACE_SECONDS < run_tests.INTERRUPT_GRACE_SECONDS


@pytest.mark.parametrize(("interrupts", "killed"), [(2, False), (3, True)])
def test_further_interrupts_escalate_without_escaping_main(
    monkeypatch: pytest.MonkeyPatch, interrupts: int, killed: bool
) -> None:
    """A further Ctrl-C escalates; it must not escape `main` and strand the child."""
    payload = b"pytest report before the next interrupt \xff\n"
    stderr = BinaryStderr()
    temporary_file = TrackingTemporaryFile()
    process = InterruptingProcess(interrupts, temporary_file, payload)

    monkeypatch.setattr(run_tests.sys, "stderr", stderr)
    monkeypatch.setattr(run_tests.tempfile, "TemporaryFile", lambda: temporary_file)
    monkeypatch.setattr(
        run_tests.subprocess, "Popen", lambda _command, **_kwargs: process
    )

    assert run_tests.main() == 130

    assert process.terminated
    assert process.killed is killed
    assert stderr.buffer.getvalue() == payload
    assert temporary_file.closed


def test_a_wedged_child_is_terminated_then_killed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child that never exits is escalated through both rungs, not waited on.

    This is the only test driving the window's and the reap's timeout branches
    rather than their interrupt branches, so it is what proves an interrupted
    run still fails on a bounded budget instead of stalling.
    """
    temporary_file = TrackingTemporaryFile()

    class WedgedProcess:
        returncode = -9

        def __init__(self) -> None:
            self.timeouts: list[float | None] = []
            self.terminated = False
            self.killed = False

        def wait(self, timeout: float | None = None) -> int:
            self.timeouts.append(timeout)
            if len(self.timeouts) == 1:
                raise KeyboardInterrupt
            if timeout is not None:
                raise subprocess.TimeoutExpired(["pytest"], timeout)
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

    process = WedgedProcess()
    monkeypatch.setattr(run_tests.sys, "stderr", BinaryStderr())
    monkeypatch.setattr(run_tests.tempfile, "TemporaryFile", lambda: temporary_file)
    monkeypatch.setattr(
        run_tests.subprocess, "Popen", lambda _command, **_kwargs: process
    )

    assert run_tests.main() == 130

    assert process.terminated
    assert process.killed
    # Pins the routing, both constants, and both escalation rungs at once.
    assert process.timeouts == [
        run_tests.TEST_TIMEOUT_SECONDS,
        run_tests.INTERRUPT_GRACE_SECONDS,
        run_tests.TERMINATE_GRACE_SECONDS,
        None,
    ]


def test_timeout_terminates_pytest_and_returns_timeout_status(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    temporary_file = TrackingTemporaryFile()

    class TimedOutProcess:
        returncode = 143

        def __init__(self) -> None:
            self.wait_count = 0
            self.timeouts: list[float | None] = []

        def wait(self, timeout: float | None = None) -> int:
            self.wait_count += 1
            self.timeouts.append(timeout)
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
    # The timeout arm escalates straight away: nothing has asked this child to
    # stop, so a diagnostic window would only delay the report (ADR 0130).
    assert process.timeouts == [
        run_tests.TEST_TIMEOUT_SECONDS,
        run_tests.TERMINATE_GRACE_SECONDS,
    ]
    assert temporary_file.closed


def test_a_refused_group_kill_still_kills_the_direct_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`killpg` can be refused, and then nothing has killed the direct child."""
    killed: list[int] = []

    class RefusedProcess:
        pid = 4321

        def kill(self) -> None:
            killed.append(self.pid)

    def refused(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("operation not permitted")

    monkeypatch.setattr(os, "killpg", refused)

    _kill_process_group(cast(subprocess.Popen[bytes], RefusedProcess()))

    assert killed == [4321]


def test_killing_a_group_that_has_already_gone_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both signals raise when nothing is left, and neither may escape."""

    def gone(*_args: object, **_kwargs: object) -> None:
        raise ProcessLookupError("no such process")

    class GoneProcess:
        pid = 4322

        def kill(self) -> None:
            raise ProcessLookupError("no such process")

    monkeypatch.setattr(os, "killpg", gone)

    _kill_process_group(cast(subprocess.Popen[bytes], GoneProcess()))


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
    ready_in = _wait_for_process_marker(ready, process)
    # A group that has already gone means the child exited on its own; the
    # returncode assertion below reports that far better than an errno would.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGINT)
    signalled_at = time.monotonic()
    window = run_tests.INTERRUPT_GRACE_SECONDS
    reap = run_tests.TERMINATE_GRACE_SECONDS
    budget = window + reap + _INTERRUPT_COLLECTION_SLACK_SECONDS
    try:
        stdout, stderr = process.communicate(timeout=budget)
    except subprocess.TimeoutExpired as expired:
        _kill_process_group(process)
        # Whatever the timed-out call had already read, so a drain that cannot
        # finish still reports something. The drain is bounded rather than bare:
        # an unbounded wait here would replace the budget it just enforced.
        stderr = expired.stderr or b""
        with contextlib.suppress(subprocess.TimeoutExpired):
            _stdout, stderr = process.communicate(timeout=5)
        pytest.fail(
            f"child did not exit within {budget}s of SIGINT "
            f"(window {window}s, reap {reap}s) "
            f"after becoming ready in {ready_in:.1f}s; "
            f"stderr tail: {stderr[-2000:]!r}"
        )
    settled_in = time.monotonic() - signalled_at

    assert process.returncode == 130
    assert stdout == b""
    # Report, do not attribute. Both figures now move with load: ADR 0130
    # measured the settle interval tracking readiness at 0.95x once the old
    # 3-second clamp stopped hiding it. That is why neither is evidence on its
    # own -- a regression that lowered INTERRUPT_GRACE_SECONDS would satisfy any
    # `settled_in >= window` guard trivially and be reported as host slowness.
    assert b"KeyboardInterrupt" in stderr, (
        f"no KeyboardInterrupt: the child became ready in {ready_in:.1f}s and "
        f"settled in {settled_in:.1f}s against a window of {window}s "
        f"and a reap of {reap}s; "
        f"stderr tail: {stderr[-2000:]!r}"
    )
