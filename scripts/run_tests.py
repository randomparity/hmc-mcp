"""Run pytest with compact success output and complete failure diagnostics."""

import shutil
import subprocess
import sys
import tempfile
from typing import BinaryIO


def _replay(output: BinaryIO) -> None:
    output.seek(0)
    shutil.copyfileobj(output, sys.stderr.buffer, length=64 * 1024)


def main() -> int:
    """Run the configured pytest suite and report its result compactly."""
    with tempfile.TemporaryFile() as output:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest"],
                check=False,
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
