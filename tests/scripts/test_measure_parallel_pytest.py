"""Exercise the temporary measurement harness with real child forests."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "measure_parallel_pytest.py"


def invoke(tmp_path, code, timeout=10):
    out = tmp_path / "sample"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--output",
            str(out),
            "--timeout",
            str(timeout),
            sys.executable,
            "-c",
            code,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return result, json.loads((out / "result.json").read_text())


@pytest.mark.parametrize("status", [0, 7])
def test_status_and_diagnostics(tmp_path, status):
    result, data = invoke(tmp_path, f"print('diagnostic'); raise SystemExit({status})")
    assert result.returncode == data["returncode"] == status
    assert "diagnostic" in (tmp_path / "sample" / "log.txt").read_text()
    assert data["survivors"] == 0


@pytest.mark.parametrize("interrupt", [False, True, "twice"])
def test_cleanup_nested_session(tmp_path, interrupt):
    marker = tmp_path / "child"
    parent_marker = tmp_path / "parent"
    code = (
        "import os,subprocess,sys,time,signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        f"open({str(parent_marker)!r},'w').write(str(os.getpid())); "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
        "start_new_session=True); "
        f"open({str(marker)!r},'w').write(str(p.pid)); time.sleep(60)"
    )
    out = tmp_path / "sample"
    process = subprocess.Popen(
        [
            sys.executable,
            str(SCRIPT),
            "--output",
            str(out),
            "--timeout",
            "1" if not interrupt else "10",
            sys.executable,
            "-c",
            code,
        ]
    )
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        pid = int(marker.read_text())
        if interrupt:
            process.send_signal(signal.SIGINT)
            if interrupt == "twice":
                time.sleep(0.1)
                process.send_signal(signal.SIGINT)
        assert process.wait(timeout=15) == (130 if interrupt else 124)
        assert not Path(f"/proc/{pid}").exists()
        assert not Path(f"/proc/{parent_marker.read_text()}").exists()
        assert json.loads((out / "result.json").read_text())["survivors"] == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for owned_marker in (marker, parent_marker):
            if owned_marker.exists():
                try:
                    os.kill(int(owned_marker.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass


def test_memory_is_sum_across_children(tmp_path):
    child = "import time; data=bytearray(32*1024*1024); time.sleep(2)"
    code = (
        "import subprocess,sys; "
        f"children=[subprocess.Popen([sys.executable,'-c',{child!r}]) for _ in range(2)]; "
        "[p.wait() for p in children]"
    )
    result, data = invoke(tmp_path, code)
    assert result.returncode == 0
    assert data["peak_forest_rss_bytes"] > 64 * 1024 * 1024
    assert data["peak_processes"] >= 3


def test_adopted_children_are_reaped_while_command_runs(tmp_path):
    marker = tmp_path / "orphan"
    grandchild = "import time; time.sleep(0.1)"
    child = (
        "import subprocess,sys; "
        f"p=subprocess.Popen([sys.executable,'-c',{grandchild!r}]); "
        f"open({str(marker)!r},'w').write(str(p.pid))"
    )
    command = (
        "import subprocess,sys,time; from pathlib import Path; "
        f"subprocess.run([sys.executable,'-c',{child!r}],check=True); "
        f"pid=int(Path({str(marker)!r}).read_text()); "
        "deadline=time.monotonic()+5\n"
        "while Path(f'/proc/{pid}').exists() and time.monotonic()<deadline:\n"
        "    time.sleep(0.01)\n"
        "assert not Path(f'/proc/{pid}').exists(), Path(f'/proc/{pid}/stat').read_text()\n"
        "raise SystemExit(7)\n"
    )
    result, data = invoke(tmp_path, command)
    assert result.returncode == data["returncode"] == 7
    assert data["survivors"] == 0


def test_failure_retains_normalized_evidence(tmp_path):
    out = tmp_path / "sample"
    xml = '<testsuite><testcase classname="private-class" name="private-name"><failure/></testcase></testsuite>'
    cov = {
        "totals": {"num_statements": 10, "num_branches": 2},
        "files": {
            "private-path": {"summary": {"num_statements": 10, "num_branches": 2}}
        },
    }
    code = (
        "from pathlib import Path; "
        f"Path({str(out / 'junit.xml')!r}).write_text({xml!r}); "
        f"Path({str(out / 'coverage.json')!r}).write_text({json.dumps(cov)!r}); "
        "raise SystemExit(7)"
    )
    result, data = invoke(tmp_path, code)
    assert result.returncode == 7
    assert len(data["tests"]) == 1 and data["tests"][0]["status"] == "failure"
    assert data["coverage"]["totals"]["num_branches"] == 2
    assert len(data["coverage"]["files"]) == 1
    assert "private-" not in json.dumps(data)
