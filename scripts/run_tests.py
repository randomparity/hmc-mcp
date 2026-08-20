"""Run pytest with compact success output and complete failure diagnostics."""

import os
import shutil
import subprocess
import sys
import tempfile
from typing import BinaryIO

_PYTEST_ENVIRONMENT_OVERRIDES = {"PYTEST_ADDOPTS", "COVERAGE_RCFILE", "COVERAGE_FILE"}


def _replay(output: BinaryIO) -> None:
    output.seek(0)
    shutil.copyfileobj(output, sys.stderr.buffer, length=64 * 1024)


def main() -> int:
    """Run the configured pytest suite and report its result compactly."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in _PYTEST_ENVIRONMENT_OVERRIDES
    }
    with tempfile.TemporaryFile() as output:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest"],
                check=False,
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        except KeyboardInterrupt:
            _replay(output)
            return 130
        if result.returncode == 0:
            print("test: passed; configured coverage gate passed")
            return 0

        _replay(output)
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
