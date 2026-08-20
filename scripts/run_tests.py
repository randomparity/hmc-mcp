"""Run pytest with compact success output and complete failure diagnostics."""

import os
import shutil
import subprocess
import sys
import tempfile
from typing import IO, Any, BinaryIO

CAPTURE_LIMIT = 1024 * 1024
CHUNK_SIZE = 64 * 1024
INTERRUPT_GRACE_SECONDS = 3
_PYTEST_ENVIRONMENT_OVERRIDES = {"PYTEST_ADDOPTS", "COVERAGE_RCFILE", "COVERAGE_FILE"}


def _replay(output: BinaryIO) -> None:
    output.seek(0)
    shutil.copyfileobj(output, sys.stderr.buffer, length=CHUNK_SIZE)


def _capture(stream: IO[Any], output: BinaryIO, *, passthrough: bool = False) -> bool:
    """Capture a bounded prefix, switching to live diagnostics at the limit."""
    captured = output.tell()
    while chunk := stream.read(CHUNK_SIZE):
        if not passthrough and captured + len(chunk) <= CAPTURE_LIMIT:
            output.write(chunk)
            captured += len(chunk)
            continue
        if not passthrough:
            _replay(output)
            passthrough = True
        sys.stderr.buffer.write(chunk)
        sys.stderr.buffer.flush()
    return passthrough


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
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert process.stdout is not None
        passthrough = False
        interrupted = False
        try:
            passthrough = _capture(process.stdout, output)
            process.wait()
        except KeyboardInterrupt:
            interrupted = True
            _settle_interrupted(process)
            passthrough = _capture(process.stdout, output, passthrough=passthrough)

        if process.returncode == 0 and not interrupted:
            print("test: passed; configured coverage gate passed")
            return 0
        if not passthrough:
            _replay(output)
        return 130 if interrupted else _exit_status(process.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
