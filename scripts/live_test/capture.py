"""Record what hmcpctl sends to and receives from a live HMC.

A probe script wraps its calls in the context manager and labels phases::

    with capture(Path("hmc-captures/probe.capture.jsonl")) as cap:
        cap.step("create")
        ...  # drive hmcpctl against the HMC

Every REST request (`HMCClient._request`) and SSH command
(`asyncssh.SSHClientConnection.run`) becomes one JSON line. Both are patched on
the class, so modules that imported `run_hmc_command` by name are still seen.
Secrets are redacted before anything is written; see the issue #1161 design spec.
"""

from __future__ import annotations

import contextlib
import functools
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import asyncssh
import httpx

from hmcpctl.client.core import HMCClient

LOGON_REDACTED = "<redacted: logon>"
SECRET_REDACTED = "<redacted: secret>"

_SECRET_KEYWORDS = (
    "password",
    "passwd",
    "passphrase",
    "sftpkey",
    "private key",
    "x-api-session",
    "x_api_session",
)
_DROPPED_HEADERS = frozenset({"x-api-session", "cookie", "set-cookie", "authorization"})

#: Set while a context is live. Module-level because the patch it guards is global.
_ACTIVE = False


def destination_is_ignored(path: Path) -> bool:
    """Report whether Git would let *path* be committed from where it is written.

    Same rule as `scripts/live_test_runner.py`: exit 1 from `git check-ignore -q`
    means an unignored path inside a repository; exit 0 (ignored) and anything
    else (no repository, or outside one) leave nothing committable.
    """
    target = path.parent.resolve() / path.name
    proc = subprocess.run(
        ["git", "-C", str(target.parent), "check-ignore", "-q", str(target)],
        capture_output=True,
        check=False,
    )
    return proc.returncode != 1


def _text(value: str | bytes | None) -> str | None:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _redact(text: str | bytes | None, *, logon: bool = False) -> str | None:
    """Replace *text* wholesale when it is a logon exchange or names a secret."""
    value = _text(text)
    if value is None:
        return None
    if logon:
        return LOGON_REDACTED
    lowered = value.lower()
    if any(word in lowered for word in _SECRET_KEYWORDS):
        return SECRET_REDACTED
    return value


def _safe_headers(headers: Any) -> dict[str, str]:
    return {
        k: v
        for k, v in httpx.Headers(headers).items()
        if k.lower() not in _DROPPED_HEADERS
    }


def _exception_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


class Capture:
    """Writes records to one destination and labels them with the current step."""

    def __init__(self, path: Path, fd: int) -> None:
        self._path = path
        self._fd = fd
        self._step = "init"
        self._enabled = True

    def step(self, name: str) -> None:
        """Label the records that follow."""
        self._step = name

    def _emit(self, build: Callable[..., dict[str, Any]], *args: Any) -> None:
        """Write one record; on any recorder failure, disable and say so once."""
        if not self._enabled:
            return
        try:
            record = {"step": self._step, "t": time.time(), **build(*args)}
            os.write(self._fd, (json.dumps(record, default=str) + "\n").encode())
        except Exception as exc:  # noqa: BLE001 - recording must never alter the call
            self._enabled = False
            print(f"capture: recording to {self._path} stopped: {exc}", file=sys.stderr)

    def _rest_record(
        self, method: str, path: str, kwargs: dict[str, Any], outcome: Any
    ) -> dict[str, Any]:
        logon = "Logon" in path
        headers = httpx.Headers(kwargs.get("headers"))
        body = (
            json.dumps(kwargs["json"])
            if kwargs.get("json") is not None
            else kwargs.get("content")
        )
        record: dict[str, Any] = {
            "kind": "rest",
            "method": method,
            "path": path,
            "accept": headers.get("accept"),
            "content_type": headers.get("content-type"),
            "request_body": _redact(body, logon=logon),
        }
        if isinstance(outcome, BaseException):
            record["exception"] = _redact(_exception_text(outcome), logon=logon)
        else:
            record["status"] = outcome.status_code
            record["response_headers"] = _safe_headers(outcome.headers)
            record["body"] = _redact(outcome.text, logon=logon)
        return record

    def _ssh_record(self, command: Any, outcome: Any) -> dict[str, Any]:
        record: dict[str, Any] = {"kind": "ssh", "command": _redact(command)}
        if isinstance(outcome, BaseException) and not isinstance(
            outcome, asyncssh.ProcessError
        ):
            record["exception"] = _redact(_exception_text(outcome))
        else:
            record["exit_status"] = outcome.exit_status
            record["stdout"] = _redact(outcome.stdout)
            record["stderr"] = _redact(outcome.stderr)
        return record

    def wrap_request(self, original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        async def spy(client: Any, method: str, path: str, **kwargs: Any) -> Any:
            try:
                response = await original(client, method, path, **kwargs)
            except BaseException as exc:
                self._emit(self._rest_record, method, path, kwargs, exc)
                raise
            self._emit(self._rest_record, method, path, kwargs, response)
            return response

        return spy

    def wrap_run(self, original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        async def spy(conn: Any, *args: Any, **kwargs: Any) -> Any:
            command = args[0] if args else kwargs.get("command")
            try:
                result = await original(conn, *args, **kwargs)
            except BaseException as exc:
                self._emit(self._ssh_record, command, exc)
                raise
            self._emit(self._ssh_record, command, result)
            return result

        return spy


@contextlib.contextmanager
def capture(path: Path) -> Iterator[Capture]:
    """Record every HMC REST call and SSH command made inside the block to *path*."""
    global _ACTIVE
    if _ACTIVE:
        raise RuntimeError(
            "a capture is already active; nested capture() is unsupported"
        )
    if not path.parent.is_dir():
        raise ValueError(
            f"capture destination parent {path.parent} does not exist; create it first"
        )
    if not destination_is_ignored(path):
        raise ValueError(
            f"capture destination {path} is not git-ignored; use a name ending in "
            ".capture.jsonl or a path under hmc-captures/"
        )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    _ACTIVE = True
    original_request = HMCClient._request
    original_run = asyncssh.SSHClientConnection.run
    cap = Capture(path, fd)
    try:
        HMCClient._request = cap.wrap_request(original_request)  # type: ignore[method-assign]
        asyncssh.SSHClientConnection.run = cap.wrap_run(original_run)  # type: ignore[method-assign]
        yield cap
    finally:
        HMCClient._request = original_request  # type: ignore[method-assign]
        asyncssh.SSHClientConnection.run = original_run  # type: ignore[method-assign]
        os.close(fd)
        _ACTIVE = False
