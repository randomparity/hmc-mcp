"""Run pytest with compact success output and complete failure diagnostics."""

import os
import shutil
import subprocess
import sys
import tempfile
from typing import BinaryIO

CHUNK_SIZE = 64 * 1024
INTERRUPT_GRACE_SECONDS = 300
TERMINATE_GRACE_SECONDS = 3
TEST_TIMEOUT_SECONDS = 17 * 60
_PYTEST_ENVIRONMENT_OVERRIDES = {"PYTEST_ADDOPTS", "COVERAGE_RCFILE", "COVERAGE_FILE"}


def _replay(output: BinaryIO) -> None:
    output.seek(0)
    shutil.copyfileobj(output, sys.stderr.buffer, length=CHUNK_SIZE)


def _stop(process: subprocess.Popen[bytes]) -> None:
    """Stop a child that will not exit on its own, escalating to SIGKILL.

    A `KeyboardInterrupt` here is a further Ctrl-C asking to stop now, so it
    escalates exactly as an expired wait does.
    """
    process.terminate()
    try:
        process.wait(timeout=TERMINATE_GRACE_SECONDS)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        process.kill()
        try:
            process.wait()
        except KeyboardInterrupt:
            pass


def _settle_interrupted(process: subprocess.Popen[bytes]) -> None:
    """Give an interrupted pytest time to emit diagnostics, then stop it.

    The window is a ceiling, not a latency budget: the wait returns the moment
    the child exits, so only a child that has not exited pays it. ADR 0130
    sizes it against the suite this script wraps, and records why a second
    Ctrl-C, not the number, is what bounds an interactive run.
    """
    try:
        process.wait(timeout=INTERRUPT_GRACE_SECONDS)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        _stop(process)


def _exit_status(returncode: int) -> int:
    return 128 + abs(returncode) if returncode < 0 else returncode


def main() -> int:
    """Run the configured pytest suite and report its result compactly."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in _PYTEST_ENVIRONMENT_OVERRIDES
    }
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "pytest"],
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        interrupted = False
        timed_out = False
        try:
            process.wait(timeout=TEST_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out = True
            _stop(process)
        except KeyboardInterrupt:
            interrupted = True
            _settle_interrupted(process)

        if process.returncode == 0 and not interrupted and not timed_out:
            print("test: passed; configured coverage gate passed")
            return 0
        try:
            _replay(output)
        except KeyboardInterrupt:
            return 130
        if interrupted:
            return 130
        if timed_out:
            print(
                f"test: timed out after {TEST_TIMEOUT_SECONDS}s", file=sys.stderr
            )
            return 124
        return _exit_status(process.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
