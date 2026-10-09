"""Temporary Linux process-forest measurement for issue #1434."""

import argparse
import ctypes
import hashlib
import importlib.metadata
import json
import os
import platform
import signal
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path


def forest():
    records = {}
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_text().rsplit(")", 1)[1].split()
            records[int(path.parent.name)] = (int(fields[1]), int(fields[21]))
        except (FileNotFoundError, ProcessLookupError):
            continue
    owned = {os.getpid()}
    while True:
        expanded = owned | {p for p, (parent, _) in records.items() if parent in owned}
        if expanded == owned:
            break
        owned = expanded
    return {
        p: records[p][1] * os.sysconf("SC_PAGE_SIZE")
        for p in owned
        if p != os.getpid() and p in records
    }


def cleanup():
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in forest():
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                while os.waitpid(-1, os.WNOHANG)[0]:
                    pass
            except ChildProcessError:
                return
            time.sleep(0.02)
    if forest():
        raise RuntimeError("owned descendants survived cleanup")


def run(command, output, timeout):
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "cannot enable child subreaper")
    output.mkdir(parents=True, exist_ok=False)
    environment = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "PYTEST_ADDOPTS",
            "COVERAGE_FILE",
            "COVERAGE_RCFILE",
            "HMCPCTL_TEST_TIMINGS",
        }
    }
    started = time.monotonic()
    peak = count = 0
    reason = "exit"
    with (output / "log.txt").open("wb") as log:
        process = subprocess.Popen(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=environment,
            start_new_session=True,
        )
        try:
            while process.poll() is None:
                rss = forest()
                peak = max(peak, sum(rss.values()))
                count = max(count, len(rss))
                if time.monotonic() - started >= timeout:
                    reason = "timeout"
                    break
                time.sleep(0.1)
        except KeyboardInterrupt:
            reason = "interrupt"
        finally:
            handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
            try:
                finish(process)
            finally:
                signal.signal(signal.SIGINT, handler)
    result = {
        "returncode": process.returncode,
        "reason": reason,
        "wall_seconds": time.monotonic() - started,
        "peak_forest_rss_bytes": peak,
        "peak_processes": count,
        "sample_seconds": 0.1,
        "survivors": len(forest()),
        "affinity_cpus": len(os.sched_getaffinity(0)),
    }
    result.update(evidence(output))
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def finish(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    cleanup()


def evidence(output):
    result = {"python": platform.python_version(), "architecture": platform.machine()}
    result["commit"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()
    result["versions"] = {
        p: importlib.metadata.version(p) for p in ("pytest", "pytest-cov")
    }
    cgroup = Path("/sys/fs/cgroup") / Path(
        "/proc/self/cgroup"
    ).read_text().strip().split(":", 2)[2].lstrip("/")
    result["limits"] = {
        n: (cgroup / n).read_text().strip()
        for n in ("cpu.max", "memory.max", "memory.swap.max")
        if (cgroup / n).exists()
    }
    junit = output / "junit.xml"
    result["tests"] = None
    if junit.exists():
        cases = ET.parse(junit).getroot().iter("testcase")
        result["tests"] = sorted(
            [
                {
                    "id": hashlib.sha256(
                        (c.get("classname", "") + "::" + c.get("name", "")).encode()
                    ).hexdigest(),
                    "status": next(
                        (
                            n
                            for n in ("failure", "error", "skipped")
                            if c.find(n) is not None
                        ),
                        "passed",
                    ),
                }
                for c in cases
            ],
            key=lambda c: c["id"],
        )
    coverage = output / "coverage.json"
    result["coverage"] = None
    if coverage.exists():
        report = json.loads(coverage.read_text())
        result["coverage"] = {
            "totals": report["totals"],
            "files": {
                hashlib.sha256(name.encode()).hexdigest(): data["summary"]
                for name, data in report["files"].items()
            },
        }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=1020)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command or args.timeout <= 0:
        parser.error("provide a command and positive timeout")
    result = run(args.command, args.output, args.timeout)
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in {"tests", "coverage"}},
            sort_keys=True,
        )
    )
    raise SystemExit(
        124
        if result["reason"] == "timeout"
        else 130
        if result["reason"] == "interrupt"
        else result["returncode"]
        if result["returncode"] >= 0
        else 128 - result["returncode"]
    )
