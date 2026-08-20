"""Run pytest with compact success output and complete failure diagnostics."""

import shutil
import subprocess
import sys
import tempfile


def main() -> int:
    """Run the configured pytest suite and report its result compactly."""
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(
            [sys.executable, "-m", "pytest"],
            check=False,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        if result.returncode == 0:
            print("test: passed; configured coverage gate passed")
            return 0

        output.seek(0)
        shutil.copyfileobj(output, sys.stderr.buffer, length=64 * 1024)
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
