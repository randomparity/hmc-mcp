"""Run pytest with compact success output and complete failure diagnostics."""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import BinaryIO

SERIAL = False
CHUNK_SIZE = 64 * 1024
INTERRUPT_GRACE_SECONDS = 300
TERMINATE_GRACE_SECONDS = 3
TEST_TIMEOUT_SECONDS = 17 * 60
_PYTEST_ENVIRONMENT_OVERRIDES = {
    "PYTEST_ADDOPTS",
    "COVERAGE_RCFILE",
    "COVERAGE_FILE",
    "HMCPCTL_TEST_TIMINGS",
}


def _resources_allow_parallel(
    proc: Path = Path("/proc"), root: Path = Path("/sys/fs/cgroup")
) -> bool:
    """Require two effective CPUs and 3 GiB available under every visible limit."""
    if sys.platform != "linux":
        return False
    try:
        cpus = len(os.sched_getaffinity(0))
        available = [
            line.split()
            for line in (proc / "meminfo").read_text().splitlines()
            if line.startswith("MemAvailable:")
        ]
        if len(available) != 1 or len(available[0]) != 3 or available[0][2] != "kB":
            return False
        memory = int(available[0][1]) * 1024
        memberships = [
            line[3:]
            for line in (proc / "self/cgroup").read_text().splitlines()
            if line.startswith("0::")
        ]
        if len(memberships) != 1 or not memberships[0].startswith("/"):
            return False
        root = root.resolve()
        group = (root / memberships[0].lstrip("/")).resolve()
        if not group.is_relative_to(root):
            return False
        while True:
            cpu = group / "cpu.max"
            if group != root or cpu.exists():
                quota, period_text = cpu.read_text().split()
                period = int(period_text)
                if period <= 0 or (quota != "max" and int(quota) <= 0):
                    return False
                if quota != "max":
                    cpus = min(cpus, int(quota) // period)
            limit_path = group / "memory.max"
            if group != root or limit_path.exists():
                limit = limit_path.read_text().strip()
                current = int((group / "memory.current").read_text())
                if current < 0 or (limit != "max" and int(limit) < 0):
                    return False
                if limit != "max":
                    memory = min(memory, max(0, int(limit) - current))
            if group == root:
                return cpus >= 2 and memory >= 3 * 1024**3
            group = group.parent
    except (OSError, ValueError):
        return False


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
    timings = os.environ.get("HMCPCTL_TEST_TIMINGS") == "1"
    command = [sys.executable, "-m", "pytest"]
    if timings:
        command.extend(["--durations=30", "--durations-min=0"])
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in _PYTEST_ENVIRONMENT_OVERRIDES
    }
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            command,
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
            if timings:
                try:
                    _replay(output)
                except KeyboardInterrupt:
                    return 130
            print("test: passed; configured coverage gate passed")
            return 0
        try:
            _replay(output)
        except KeyboardInterrupt:
            return 130
        if interrupted:
            return 130
        if timed_out:
            print(f"test: timed out after {TEST_TIMEOUT_SECONDS}s", file=sys.stderr)
            return 124
        return _exit_status(process.returncode)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--timings", action="store_true", help="retain pytest phase durations"
    )
    parser.add_argument(
        "--serial", action="store_true", help="disable parallel execution"
    )
    arguments = parser.parse_args()
    SERIAL = arguments.serial
    if arguments.timings:
        os.environ["HMCPCTL_TEST_TIMINGS"] = "1"
    raise SystemExit(main())
