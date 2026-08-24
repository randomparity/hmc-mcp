"""Run pytest with compact success output and complete failure diagnostics."""

import os
import shutil
import subprocess
import sys
import tempfile
from typing import BinaryIO

CHUNK_SIZE = 64 * 1024
INTERRUPT_GRACE_SECONDS = 3
_PYTEST_ENVIRONMENT_OVERRIDES = {"PYTEST_ADDOPTS", "COVERAGE_RCFILE", "COVERAGE_FILE"}


def _replay(output: BinaryIO) -> None:
    output.seek(0)
    shutil.copyfileobj(output, sys.stderr.buffer, length=CHUNK_SIZE)


def _settle_interrupted(process: subprocess.Popen[bytes]) -> None:
    """Give an interrupted pytest time to emit diagnostics, then stop it."""
    try:
        process.wait(timeout=INTERRUPT_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=INTERRUPT_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


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
        try:
            process.wait()
        except KeyboardInterrupt:
            interrupted = True
            _settle_interrupted(process)

        if process.returncode == 0 and not interrupted:
            print("test: passed; configured coverage gate passed")
            return 0
        _replay(output)
        return 130 if interrupted else _exit_status(process.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
