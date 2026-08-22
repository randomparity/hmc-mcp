"""The audit record proven against a real ``hmc-mcp serve`` stdio subprocess.

ADR 0040's contract is a *sink*, so a unit test against a mock logger proves the
payload and almost nothing about delivery. This drives the real console script
over raw newline-delimited JSON-RPC — deliberately not a client library, so that
anything the server prints outside the protocol shows up as an unparseable line
on stdout and is observable.

Covers docs/workflow/specs/2026-08-19-authorization-audit-events-design.md.

Spec item -> node id:
  L1  test_a_permitted_call_emits_one_parseable_record_on_stderr
  L2  test_records_do_not_share_a_physical_line
  L3  test_stdout_carries_no_non_json_line
  L4  test_a_long_caller_value_arrives_truncated
  L5  test_a_failed_sink_leaves_the_denial_unchanged

POSIX-only, and the whole module rather than only Run B. Run B needs ``2>&-``, a
POSIX shell redirection; Run A's fixture steers ``config_dir()`` through ``HOME``,
which on win32 resolves from ``APPDATA`` while ``Path.home()`` reads
``USERPROFILE`` — so on Windows the fixture would write its sentinel-bearing
``config.toml`` over the developer's real one.
"""

from __future__ import annotations

import json
import os
import queue
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="the fixture and Run B are POSIX-only; see the docstring"
)

SENTINEL_PASSWORD = "SENTINEL-LIVE-CREDENTIAL"  # pragma: allowlist secret

POLICY = """\
[[policies.lab-scoped.grants]]
effects = ["read", "mutate", "destructive"]
connections = ["lab"]
targets = { lpar = ["db-01"], managed_system = ["sys-a"] }
"""

#: Every frame read waits at most this long. Without it a child that never answers
#: hangs `just verify` and every CI leg with no diagnostic — and this is the
#: suite's only long-lived `hmc-mcp serve` child.
DEADLINE = 30.0


def _config(user: str) -> str:
    return (
        "[profiles.lab]\n"
        'host = "lab.invalid"\n'
        f'user = "{user}"\n'
        f'password = "{SENTINEL_PASSWORD}"\n'  # pragma: allowlist secret
        "\n[profiles.prod]\n"
        'host = "prod.invalid"\n'
        f'user = "{user}"\n'
        f'password = "{SENTINEL_PASSWORD}"\n'  # pragma: allowlist secret
    )


@pytest.fixture
def fixture_home(tmp_path, monkeypatch):
    """A scratch HOME holding both files at the *resolved* config directory.

    Derived from ``config_dir()`` after redirecting HOME rather than hard-coded:
    that path is platform-dependent, and a fixture written to the macOS path on
    Linux produces no config at all — ``lab`` would resolve to UNRESOLVED and L1's
    expected allow would come back ``connection-not-granted``, a plausible-looking
    wrong answer, which is the worst outcome for a proof meant to be re-run.
    """
    from hmc_mcp.config import config_dir

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.toml").write_text(_config("SENTINEL-LIVE-USER"))
    (directory / "access-policy.toml").write_text(POLICY)
    return tmp_path, directory


@pytest.fixture
def child_env(fixture_home):
    """``os.environ`` copied with the four steering variables removed.

    A copy, not a from-scratch mapping: an explicitly built environment carries no
    ``PATH``, and the child is the ``hmc-mcp`` console script, so it would not be
    found at all.

    ``HMC_HOST`` matters as much as the config path and is easier to miss:
    ``selected_connection`` gates its whole TOML branch on it and returns the
    default connection for *any* token when it is set, before the file is read —
    and the grant names ``connections = ["lab"]``, which never contains that. A
    developer with it exported would see L1 come back ``connection-not-granted``.
    """
    home, directory = fixture_home
    env = dict(os.environ)
    for name in ("HMC_HOST", "HMC_PROFILE", "XDG_CONFIG_HOME", "APPDATA"):
        env.pop(name, None)
    env["HOME"] = str(home)
    # Set rather than popped. A permitted call emits its record and then runs the
    # real handler against `lab.invalid`, and the reply frame only arrives once
    # that transport attempt finishes — so the child's own timeout must be well
    # inside DEADLINE whatever the developer or CI runner exports. HMCConfig
    # defaults to 60s, twice the deadline; popping it would only restore that.
    env["HMC_TIMEOUT"] = "5"

    assert (directory / "config.toml").exists(), directory
    assert (directory / "access-policy.toml").exists(), directory
    for name in ("HMC_HOST", "HMC_PROFILE", "XDG_CONFIG_HOME", "APPDATA"):
        assert name not in env
    assert env["HMC_TIMEOUT"] == "5"
    return env


@pytest.fixture
def server_binary():
    """The ``hmc-mcp`` console script — and specifically *this* checkout's.

    "On PATH" and "the code on this branch" are different claims. A pipx- or
    uv-tool-installed `hmc-mcp` earlier on PATH, or a bare `pytest` outside the
    project venv, would otherwise give this proof a green run against foreign
    code — the plausible-looking wrong answer the fixture above exists to avoid.
    """
    path = shutil.which("hmc-mcp")
    assert path is not None, "the hmc-mcp console script must be on PATH"
    prefix = Path(sys.prefix).resolve()
    resolved = Path(path).resolve()
    assert resolved.is_relative_to(prefix), (
        f"{resolved} is not inside this interpreter's environment ({prefix}); "
        "the live proof would run against a different build of hmc-mcp"
    )
    return path


class _Server:
    """One live stdio server, driven over raw newline-delimited JSON-RPC."""

    def __init__(self, process: subprocess.Popen, log: Path | None = None):
        self.process = process
        self.log = log
        self._next_id = 0
        self._frames: queue.Queue = queue.Queue()
        self._pump_thread = threading.Thread(target=self._pump, daemon=True)
        self._pump_thread.start()

    def reap(self) -> None:
        """End the child, then release its streams after the stdout pump exits."""
        _reap(self.process, self._pump_thread)

    def _diagnosis(self) -> str:
        """The child's own stderr, so a failure here names its cause."""
        if self.log is None or not self.log.exists():
            return f"(exit={self.process.poll()}, no stderr captured)"
        tail = self.log.read_text(errors="replace")[-2000:]
        return f"(exit={self.process.poll()}) child stderr:\n{tail}"

    def send(self, method: str, params: dict | None = None, *, notify=False):
        self._next_id += 1
        frame = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            frame["params"] = params
        if not notify:
            frame["id"] = self._next_id
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(frame) + "\n")
        self.process.stdin.flush()
        return None if notify else self._read(frame["id"])

    def _pump(self) -> None:
        """Read frames on a daemon thread, so the deadline is wall-clock.

        Not ``select`` on the pipe: reads through a ``TextIOWrapper`` are block
        buffered whatever ``bufsize`` does for writes, so a read that pulls two
        frames leaves the second in Python's buffer where ``select`` cannot see
        it — and the next wait would then time out and report a harness stall as
        a server hang, inside ``just verify``.
        """
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self._frames.put(line)
        self._frames.put(None)

    def _read(self, expect_id: int) -> dict:
        """The reply to *expect_id*, or fail with the child's stderr."""
        while True:
            try:
                line = self._frames.get(timeout=DEADLINE)
            except queue.Empty:
                self.process.kill()
                raise AssertionError(
                    f"no JSON-RPC frame within {DEADLINE}s; the server is not "
                    f"answering {self._diagnosis()}"
                ) from None
            assert line is not None, (
                f"the server closed stdout without answering {self._diagnosis()}"
            )
            frame = json.loads(line)
            # Match the reply to the request rather than assuming strict
            # alternation: a notification or an out-of-order frame would
            # otherwise be returned as the answer to the wrong call.
            if frame.get("id") == expect_id:
                return frame

    def initialize(self) -> None:
        self.send(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "live-proof", "version": "0"},
            },
        )
        self.send("notifications/initialized", {}, notify=True)

    def call(self, tool: str, arguments: dict) -> dict:
        return self.send("tools/call", {"name": tool, "arguments": arguments})


def _spawn(command: list[str], env: dict, stderr) -> subprocess.Popen:
    return subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=stderr,
        env=env,
        text=True,
        bufsize=1,
    )


@pytest.fixture
def run_a(child_env, server_binary, tmp_path):
    """Run A — observation. stderr to a file, stdout read frame by frame."""
    log = tmp_path / "stderr.log"
    with log.open("w") as sink:
        process = _spawn([server_binary, "serve", "--access-policy", "lab-scoped"],
                         child_env, sink)
        server = _Server(process, log)
        try:
            server.initialize()
            yield server, log
        finally:
            server.reap()


def _reap(process: subprocess.Popen, pump_thread: threading.Thread | None = None) -> None:
    """Terminate a child and deterministically close its parent-owned pipe wrappers."""
    try:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    finally:
        assert process.poll() is not None, "the server subprocess outlived the test"
        if pump_thread is not None:
            pump_thread.join(timeout=DEADLINE)
            assert not pump_thread.is_alive(), "stdout pump outlived the server"
        if process.stdout and not process.stdout.closed:
            process.stdout.close()
        # Popen exposes ``stderr`` only when it owns a PIPE. External log sinks
        # and ``DEVNULL`` remain the caller's responsibility.
        if process.stderr and not process.stderr.closed:
            process.stderr.close()


def _audit_lines(log: Path) -> list[dict]:
    """Only the stderr lines that parse as an audit record.

    stderr carries non-JSON from other writers by design — the fixture's
    ``.invalid`` hosts make even a permitted call fail at the transport, and that
    exception reaches the same FastMCP error path that renders a 41-line panel
    (#267). Filtering rather than counting total lines is also why L2 detects a
    missing terminator: three records without one cannot yield three parsing
    lines.
    """
    records = []
    for line in log.read_text(errors="replace").splitlines():
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("event") == "authorization":
            records.append(payload)
    return records


def _await_records(log: Path, ready) -> list[dict]:
    """Poll the sink until *ready* holds over the parsed audit records.

    The reply frame and the audit record are independent writes from the
    child: the JSON-RPC answer can be read off stdout before the record's
    flush reaches the stderr file, so a single read after a call races the
    record's arrival. CI hit exactly that once — L3 read only the earlier
    ``permitted`` record, so ``[-1]`` was not the denial. Waiting removes
    the race without changing what any test asserts.
    """
    deadline = time.monotonic() + DEADLINE
    while True:
        records = _audit_lines(log)
        if ready(records):
            return records
        assert time.monotonic() < deadline, (
            f"audit record did not land within {DEADLINE}s; log tail:\n"
            f"{log.read_text(errors='replace')[-2000:]}"
        )
        time.sleep(0.02)


def _await_last_record(log: Path, reason: str) -> dict:
    """The newest record once one carrying *reason* is the last to land."""
    return _await_records(
        log, lambda records: bool(records) and records[-1]["reason"] == reason
    )[-1]


PERMITTED = {
    "lpar_name_or_uuid": "db-01",
    "system_name_or_uuid": "sys-a",
    "profile": "lab",
}


def test_a_permitted_call_emits_one_parseable_record_on_stderr(run_a):
    """L1."""
    server, log = run_a
    server.call("hmc_power_off_lpar", PERMITTED)
    records = _await_records(log, lambda items: len(items) >= 1)
    assert len(records) == 1
    record = records[0]
    assert record["decision"] == "allow"
    assert record["reason"] == "permitted"
    assert record["policy"] == "lab-scoped"
    assert record["tool"] == "hmc_power_off_lpar"
    assert record["effect"] == "destructive"
    assert record["connection"]["resolved"] == "lab"
    # Named, never indexed: selected_targets preserves declaration order, so an
    # index assertion silently follows a signature change.
    entry = next(
        item for item in record["targets"] if item["argument"] == "lpar_name_or_uuid"
    )
    assert entry["value"] == "db-01"


def test_records_do_not_share_a_physical_line(run_a):
    """L2. The StreamHandler.terminator claim, live."""
    server, log = run_a
    for _ in range(3):
        server.call("hmc_power_off_lpar", PERMITTED)
    assert len(_await_records(log, lambda items: len(items) >= 3)) == 3


def test_stdout_carries_no_non_json_line(run_a):
    """L3. The #223 baseline this must not regress."""
    server, log = run_a
    # Every reply already parsed as JSON inside _Server._read, so reaching here
    # with a denial and a permit both answered is the assertion.
    server.call("hmc_power_off_lpar", PERMITTED)
    denied = server.call("hmc_power_off_lpar", {**PERMITTED, "profile": "prod"})
    assert denied["id"] is not None
    # Wait for the denial's record instead of trusting [-1]: the reply frame
    # and the record are independent writes, so an immediate read once saw
    # only the permitted record on CI. Awaiting the reason makes the ordering
    # claim (permit first, denial last) deterministic rather than lucky.
    _await_last_record(log, "connection-not-granted")
    assert [r["reason"] for r in _audit_lines(log)] == [
        "permitted",
        "connection-not-granted",
    ]


def test_a_long_caller_value_arrives_truncated(run_a):
    """L4."""
    server, log = run_a
    server.call(
        "hmc_power_off_lpar", {**PERMITTED, "lpar_name_or_uuid": "A" * 500}
    )
    record = _await_last_record(log, "target-not-granted")
    entry = next(
        item for item in record["targets"] if item["argument"] == "lpar_name_or_uuid"
    )
    assert len(entry["value"]) == 128


def test_a_failed_sink_leaves_the_denial_unchanged(child_env, server_binary, tmp_path):
    """L5 — Run B, a separate subprocess with fd 2 closed at interpreter start.

    The observation channel and the failure injection cannot coexist: every
    mechanism that makes the sink fail either closes stderr or empties it, which
    is the stream Run A reads. So this asserts on stdout alone, and on the parsed
    frame rather than its bytes — the two bodies come from separately launched
    processes and their key ordering is FastMCP's to change, while the denial
    *message* is what ADR 0038 and ADR 0039 fixed as the client contract.
    """
    log = tmp_path / "reference.log"
    with log.open("w") as sink:
        reference = _Server(
            _spawn([server_binary, "serve", "--access-policy", "lab-scoped"],
                   child_env, sink),
            log,
        )
        try:
            reference.initialize()
            expected = reference.call(
                "hmc_power_off_lpar", {**PERMITTED, "profile": "prod"}
            )
        finally:
            reference.reap()

    # shlex.quote, not " ".join: this repository's own path contains spaces, and
    # an unquoted one makes `sh -c` split it into words and fail to exec at all —
    # which looks exactly like the server refusing to start.
    quoted = shlex.join([server_binary, "serve", "--access-policy", "lab-scoped"])
    blinded = _Server(
        _spawn(["/bin/sh", "-c", f"exec {quoted} 2>&-"], child_env, subprocess.DEVNULL)
    )
    try:
        blinded.initialize()
        actual = blinded.call("hmc_power_off_lpar", {**PERMITTED, "profile": "prod"})
        assert _error_text(actual) == _error_text(expected)
        # Still serving: losing the sink cost the record and nothing else.
        listed = blinded.send("tools/list", {})
        assert "result" in listed
    finally:
        blinded.reap()


def _error_text(frame: dict) -> str:
    """The denial message out of a JSON-RPC reply, however FastMCP wrapped it."""
    if "error" in frame:
        return str(frame["error"].get("message", ""))
    content = frame.get("result", {}).get("content", [])
    return "".join(part.get("text", "") for part in content)
