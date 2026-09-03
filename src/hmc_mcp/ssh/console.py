"""Bounded, non-interactive LPAR console capture over the HMC ``mkvterm`` CLI.

The HMC exposes exactly one virtual terminal (vterm) per partition through the
``mkvterm``/``rmvterm`` CLI pair over SSH. ``mkvterm`` never exits on its own:
it streams the partition console until torn down, so it structurally cannot
run through :func:`hmc_mcp.ssh.transport.run_hmc_command` (a one-shot exec that collects
output until the remote command exits). This module adds a bounded capture on
top of :func:`hmc_mcp.ssh.transport.open_hmc_connection` and enforces issue #385's
design contract. Every invariant below traces to a recorded observation from
the P1-P8 live-hardware prototype on that issue (HMC V10R3 M1060); ADR 0072
records the design decision per prototype fact.

- **Contention** (P1): a held vterm is reported on *stdout* with exit code 0,
  so the exit status proves nothing; the sentinel sentence below is parsed
  instead and :class:`ConsoleHeldError` is raised. No ``rmvterm`` is issued on
  that path — it would release the *other* holder's session.
- **Mandatory release** (P2/P3/P4): the HMC does not auto-release a vterm,
  not after an abrupt disconnect and not after a graceful close. ``rmvterm``
  therefore runs on every exit path, cancellation included, and runs to
  completion before cancellation propagates. ``released`` is ``True`` only
  after an independent-session ``mkvterm`` probe proves the slot is free;
  ``rmvterm``'s own exit code is not proof (P2).
- **Sealed stdin** (P5/P7): mkvterm's stdin is the write socket to the
  partition console, and EOF on it terminates the vterm. The capture opens a
  pipe, hands mkvterm the read end, and holds the write end open without ever
  writing: no parameter, method, or code path can send a byte to the console.
- **Client-side bounds** (P8): an idle vterm stream stays open forever and
  carries no keepalives, so duration, max-bytes, and idle bounds are all
  enforced here, never expected from the HMC.

The captured bytes are returned raw: truncation backtracks to a boundary that
cannot split a multi-byte UTF-8 sequence or an incomplete ANSI escape
sequence, but decoding remains the caller's decision (issue #385).
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import shlex
from dataclasses import dataclass
from typing import Any, Literal

from hmc_mcp.client.core import HMCClient

from ..config import HMCConfig
from ..errors import HMCError
from .transport import HMCCLIError, open_hmc_connection, run_hmc_command

logger = logging.getLogger(__name__)

#: The terminal outcomes of a bounded capture. ``contention`` is not a stop
#: reason: it raises :class:`ConsoleHeldError` instead, per issue #385's
#: requirement that "another session holds the vterm" be a distinct error.
StopReason = Literal["duration", "max_bytes", "idle", "remote-close", "error"]

#: The distinctive sentence of the contention message (P1). The full recorded
#: stdout is three CRLF lines, each beginning and ending with a space::
#:
#:     b"\r\n A terminal session is already open for this partition. \r\n"
#:     b" Only one open session is allowed for a partition. \r\n"
#:     b" Exiting.... "
#:
#: Matching the sentence alone keeps detection robust against those cosmetic
#: quirks while staying anchored to the exact recorded bytes.
HELD_SENTINEL = b"A terminal session is already open for this partition."
ACQUIRED_SENTINEL = b"Open in progress"

#: How long the release probe waits for its own ``mkvterm`` to speak before
#: giving up (P1/P5: both the contention text and the HMC banner arrive
#: immediately on a healthy HMC).
_RELEASE_PROBE_SECONDS = 10.0

#: Upper caps for the MCP tool surface. The bounds are caller-chosen, but a
#: capture runs inside the MCP server process, so memory (``max_bytes``) and
#: wall clock (``duration_seconds``) get hard ceilings.
MAX_CAPTURE_BYTES = 1_048_576
MAX_CAPTURE_SECONDS = 3600.0

_CHUNK = 65_536
_ERROR_DETAIL_MAX_CHARS = 256


class ConsoleHeldError(HMCError):
    """Another session already holds the partition's vterm (issue #385).

    The HMC allows exactly one open vterm per partition (P1: signalled on
    stdout, always with exit code 0). This error is deliberately distinct
    from :class:`hmc_mcp.ssh.transport.HMCCLIError`: a capture never force-closes
    another holder's session, and no ``rmvterm`` is issued on this path.
    """


@dataclass(frozen=True)
class ConsoleCapture:
    """Raw captured console bytes plus the outcome facts (issue #385).

    ``released`` is honest, not optimistic: ``True`` only when an independent
    follow-up ``mkvterm`` proved the vterm slot free after the mandatory
    ``rmvterm`` (P2). ``False`` means the caller may have left the partition's
    console held and should treat further console access as broken.
    """

    system: str
    lpar: str
    data: bytes
    stop_reason: StopReason
    released: bool
    error: str | None = None


def _validate_bounds(
    duration_seconds: float, max_bytes: int, idle_timeout_seconds: float
) -> None:
    """Reject non-positive or over-ceiling bounds with actionable messages."""
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError(f"duration_seconds must be positive, got {duration_seconds}")
    if duration_seconds > MAX_CAPTURE_SECONDS:
        raise ValueError(
            f"duration_seconds must not exceed {MAX_CAPTURE_SECONDS:.0f}, "
            f"got {duration_seconds}"
        )
    if max_bytes <= 0:
        raise ValueError(f"max_bytes must be positive, got {max_bytes}")
    if max_bytes > MAX_CAPTURE_BYTES:
        raise ValueError(
            f"max_bytes must not exceed {MAX_CAPTURE_BYTES}, got {max_bytes}"
        )
    if not math.isfinite(idle_timeout_seconds) or idle_timeout_seconds <= 0:
        raise ValueError(
            f"idle_timeout_seconds must be positive, got {idle_timeout_seconds}"
        )


class _SealedStdin:
    """The write end of the capture's stdin pipe, sealed by construction (P7).

    mkvterm's stdin is the write socket to the partition console, and EOF on
    it terminates the vterm (P5) — which is why ``DEVNULL`` cannot be used.
    The pipe's read end is handed to the remote process and its write end is
    held open here, never written. The descriptor pair is private, no method
    sends data, and only :meth:`close` touches them: there is no API surface
    through which a byte could reach the console.
    """

    __slots__ = ("_read_fd", "_write_fd")

    def __init__(self) -> None:
        self._read_fd, self._write_fd = os.pipe()

    @property
    def read_fd(self) -> int:
        """The read end handed to the remote process."""
        return self._read_fd

    def transfer_read_end(self) -> None:
        """Record that asyncssh adopted the read end (it closes it now)."""
        self._read_fd = -1

    def close(self) -> None:
        """Close both ends. The read end is skipped once asyncssh owns it."""
        if self._read_fd != -1:
            try:
                os.close(self._read_fd)
            except OSError:
                pass
            self._read_fd = -1
        if self._write_fd != -1:
            try:
                os.close(self._write_fd)
            except OSError:
                pass
            self._write_fd = -1


def _utf8_safe_cut(data: bytes, cut: int) -> int:
    """Backtrack *cut* (with ``cut < len(data)``) off any UTF-8 sequence."""
    while cut > 0 and (data[cut] & 0xC0) == 0x80:
        cut -= 1
    if cut > 0 and data[cut - 1] >= 0xC0:
        # A lead byte immediately before the cut starts a sequence the cut
        # would split; move the cut in front of it.
        cut -= 1
    return cut


def _escape_complete(data: bytes, start: int, cut: int) -> bool:
    """True when the ESC sequence at *start* has its final byte before *cut*.

    ECMA-48 shapes, protocol-derived (P6): ESC + introducer + parameter/
    intermediate bytes + final byte (``0x40``-``0x7E``), with the DCS/SOS/PM/
    APC string forms terminated by ``ESC \\``.
    """
    index = start + 1
    if index >= cut:
        return False  # a bare ESC at the cut is incomplete by definition
    introducer = data[index]
    if introducer == 0x5B:  # CSI: parameter/intermediate bytes then final
        index += 1
        while index < cut and 0x20 <= data[index] <= 0x3F:
            index += 1
        return index < cut and 0x40 <= data[index] <= 0x7E
    if introducer in (0x50, 0x58, 0x5E, 0x5F):  # DCS/SOS/PM/APC strings
        return data.find(b"\x1b\\", index + 1, cut - 1) != -1
    if 0x20 <= introducer <= 0x2F:  # intermediates then a final 0x30-0x7E
        index += 1
        while index < cut and 0x20 <= data[index] <= 0x2F:
            index += 1
        return index < cut and 0x30 <= data[index] <= 0x7E
    return True  # ESC + one final byte (ESC 7, ESC c, ...)


def _ansi_safe_cut(data: bytes, cut: int) -> int:
    """Backtrack *cut* off an incomplete ANSI escape sequence.

    Protocol-derived, not prototype-verified (P6): every live observation was
    7-bit ASCII with no escapes; only a live install stream exercises this.
    """
    start = data.rfind(b"\x1b", 0, cut)
    if start == -1 or _escape_complete(data, start, cut):
        return cut
    return start


def _truncate(data: bytes, limit: int) -> bytes:
    """Cut *data* to at most *limit* bytes without splitting a sequence."""
    if len(data) <= limit:
        return bytes(data)
    cut = _utf8_safe_cut(data, limit)
    cut = _ansi_safe_cut(data, cut)
    return bytes(data[:cut])


async def _collect_output(
    process: Any,
    duration_seconds: float,
    max_bytes: int,
    idle_timeout_seconds: float,
    initial_data: bytes = b"",
) -> tuple[bytes, StopReason, str | None]:
    """Read the stream until one of the three client-side bounds fires (P8).

    Read errors (transport drops included) end the collection with
    ``stop_reason="error"`` and whatever bytes arrived; the release path
    still runs, since P3 showed the HMC never reclaims the vterm itself.
    """
    loop = asyncio.get_running_loop()
    buf = bytearray(initial_data)
    deadline = loop.time() + duration_seconds
    idle_deadline = loop.time() + idle_timeout_seconds
    while True:
        if len(buf) >= max_bytes:
            return bytes(buf), "max_bytes", None
        now = loop.time()
        if now >= deadline:
            return bytes(buf), "duration", None
        if now >= idle_deadline:
            return bytes(buf), "idle", None
        try:
            chunk = await asyncio.wait_for(
                process.stdout.read(_CHUNK),
                min(deadline, idle_deadline) - now,
            )
        except TimeoutError:
            continue  # loop top decides whether duration or idle fired
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a mid-capture read failure ends the capture as an error result, not a raise
            logger.error("console stream read failed mid-capture: %s", exc)
            return bytes(buf), "error", _error_detail(exc)
        if not chunk:
            return bytes(buf), "remote-close", None
        buf += chunk
        idle_deadline = loop.time() + idle_timeout_seconds


def _error_detail(error: Exception) -> str:
    """Return a bounded, single-line diagnostic safe for tool responses."""
    message = " ".join(
        "".join(
            character if character.isprintable() else " " for character in str(error)
        ).split()
    )
    detail = type(error).__name__
    if message:
        detail = f"{detail}: {message}"
    if len(detail) <= _ERROR_DETAIL_MAX_CHARS:
        return detail
    return detail[: _ERROR_DETAIL_MAX_CHARS - 1] + "…"


async def _release_uncancellable(
    config: HMCConfig, system_name: str, lpar_name: str
) -> bool:
    """Run the mandatory release to completion, surviving cancellation (P4).

    The release is a separate task awaited through a shield in a loop: each
    new cancellation request interrupts only the wait, never the release,
    because a leaked vterm blocks the operator's own console indefinitely
    (P3).
    """
    task = asyncio.create_task(_release_and_verify(config, system_name, lpar_name))
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            continue


async def _release_and_verify(
    config: HMCConfig, system_name: str, lpar_name: str
) -> bool:
    """Issue ``rmvterm``, then prove release with a fresh ``mkvterm`` (P2).

    ``rmvterm``'s exit code is not proof; only an independent-session
    ``mkvterm`` starting without the contention sentinel is.
    """
    quoted = f"rmvterm -m {shlex.quote(system_name)} -p {shlex.quote(lpar_name)}"
    try:
        await run_hmc_command(config, quoted)
    except HMCCLIError as exc:
        logger.warning(
            "rmvterm for %s/%s failed (%s); the probe below still decides "
            "'released' honestly",
            system_name,
            lpar_name,
            exc,
        )
    return await _probe_released(config, system_name, lpar_name)


async def _probe_released(config: HMCConfig, system_name: str, lpar_name: str) -> bool:
    """Prove the vterm slot is free by opening a fresh ``mkvterm`` (P2).

    Outcomes:

    - sentinel seen → still held → ``False`` (no ``rmvterm``: it would close
      whoever holds it);
    - the probe's ``mkvterm`` starts and stays alive → slot proven free →
      ``True``; the probe then tears its own session down — connection closed
      plus an ``rmvterm``, since the HMC does not auto-release (P3);
    - timeout with no output → state unknown → ``False``; no destructive cleanup
      is attempted because ownership of the slot was never established;
    - clean EOF without the sentinel → the remote ``mkvterm`` exited without
      acquiring → ``False`` (unproven, nothing of ours to release).
    """
    mkvterm_command = (
        f"mkvterm -m {shlex.quote(system_name)} -p {shlex.quote(lpar_name)}"
    )
    rmvterm_command = (
        f"rmvterm -m {shlex.quote(system_name)} -p {shlex.quote(lpar_name)}"
    )
    stdin = _SealedStdin()
    try:
        try:
            connection, process = await _open_capture_stream(
                config, mkvterm_command, stdin
            )
        except Exception as exc:  # noqa: BLE001 - a probe that cannot start mkvterm is unproven, not fatal
            logger.warning(
                "release probe for %s/%s could not start mkvterm: %s",
                system_name,
                lpar_name,
                exc,
            )
            return False
        saw_sentinel = False
        acquired_evidence = False
        remote_exited = False
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + _RELEASE_PROBE_SECONDS
            buf = bytearray()
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    chunk = await asyncio.wait_for(
                        process.stdout.read(_CHUNK), remaining
                    )
                except TimeoutError:
                    break
                except Exception:  # noqa: BLE001 - transport trouble ends the probe; there is nothing more to learn
                    break  # transport trouble: unproven, nothing more to learn
                if not chunk:
                    remote_exited = True
                    break
                buf += chunk
                if HELD_SENTINEL in buf:
                    saw_sentinel = True
                    break
                if ACQUIRED_SENTINEL in buf:
                    # The recorded HMC acquisition banner proves the probe's
                    # mkvterm owns the slot. Arbitrary partial output does not:
                    # it may be the beginning of the contention sentinel.
                    acquired_evidence = True
                    break
        finally:
            stdin.close()
            connection.close()
        if saw_sentinel:
            return False
        if remote_exited:
            logger.warning(
                "release probe for %s/%s exited without proof of release",
                system_name,
                lpar_name,
            )
            return False
        if acquired_evidence:
            try:
                await run_hmc_command(config, rmvterm_command)
            except HMCCLIError as exc:
                logger.error(
                    "release probe for %s/%s could not tear down its own "
                    "mkvterm (%s); the probe's vterm may be leaked",
                    system_name,
                    lpar_name,
                    exc,
                )
                return False
            return True
        # Timeout with no output: ownership is unknown. Another caller may have
        # acquired the slot after this probe disconnected, so rmvterm would race
        # with and terminate a session we do not own.
        logger.warning(
            "release probe for %s/%s produced no output within %ss; "
            "'released' is unproven",
            system_name,
            lpar_name,
            _RELEASE_PROBE_SECONDS,
        )
        return False
    finally:
        stdin.close()


async def _open_capture_stream(
    config: HMCConfig, command: str, stdin: _SealedStdin
) -> tuple[Any, Any]:
    """Open the connection and the sealed-stdin ``mkvterm`` process.

    Returns ``(connection, process)`` with ownership transferred to the
    caller. Any failure closes what was opened and re-raises.
    """
    connection = await open_hmc_connection(config)
    try:
        process = await connection.create_process(
            command, stdin=stdin.read_fd, encoding=None
        )
    except BaseException:
        connection.close()
        raise
    stdin.transfer_read_end()  # asyncssh adopted the read end via fdopen
    return connection, process


async def _acquire_capture_stream(
    config: HMCConfig, command: str, stdin: _SealedStdin
) -> tuple[Any, Any, bytes]:
    """Open ``mkvterm`` and wait for proof that this caller acquired the slot."""
    connection, process = await _open_capture_stream(config, command, stdin)
    try:
        async with asyncio.timeout(_RELEASE_PROBE_SECONDS):
            data = bytearray()
            while True:
                chunk = await process.stdout.read(_CHUNK)
                if not chunk:
                    raise HMCCLIError(
                        "mkvterm exited before confirming console acquisition"
                    )
                data += chunk
                if HELD_SENTINEL in data:
                    raise ConsoleHeldError(
                        "Another session already holds the console; "
                        "the capture never force-closes another holder's session."
                    )
                if ACQUIRED_SENTINEL in data:
                    return connection, process, bytes(data)
    except TimeoutError as exc:
        connection.close()
        raise HMCCLIError(
            "mkvterm did not confirm console acquisition within "
            f"{_RELEASE_PROBE_SECONDS:g} seconds"
        ) from exc
    except BaseException:
        connection.close()
        raise


async def _await_acquisition(
    config: HMCConfig, command: str, stdin: _SealedStdin
) -> tuple[Any, Any, bytes, bool]:
    """Shield the ownership handshake and report whether cancellation arrived."""
    task = asyncio.create_task(_acquire_capture_stream(config, command, stdin))
    cancelled = False
    while True:
        try:
            connection, process, data = await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
            continue
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
        return connection, process, data, cancelled


async def capture_lpar_console(
    hmc: HMCClient,
    system_name: str,
    lpar_name: str,
    *,
    duration_seconds: float,
    max_bytes: int,
    idle_timeout_seconds: float,
) -> ConsoleCapture:
    """Capture up to the given bounds from *lpar_name*'s console.

    Runs ``mkvterm`` over a dedicated SSH process session with stdin sealed
    (no byte can reach the partition console, P7), enforces the three
    client-side bounds (P8), then releases the vterm with ``rmvterm`` on
    every exit path and reports honestly whether the release was *proven*
    (P2/P3/P4).

    Raises:

        ConsoleHeldError: Another session holds the vterm (P1). Nothing was
            captured and no ``rmvterm`` was issued — releasing would close
            the other holder's session.

    Args:

        hmc: HMC client whose configuration supplies the SSH connection.
        system_name: Managed-system CLI name (UUIDs resolve upstream).
        lpar_name: Partition CLI name (UUIDs resolve upstream).
        duration_seconds: Wall-clock cap on the whole capture.
        max_bytes: Cap on collected bytes; the returned data backtracks to a
            boundary that cannot split a multi-byte UTF-8 sequence or an
            incomplete ANSI escape sequence.
        idle_timeout_seconds: Client-side cap on silence (time since the last
            received byte); the HMC never times an idle stream out itself.
    """
    config = hmc.config
    _validate_bounds(duration_seconds, max_bytes, idle_timeout_seconds)
    command = f"mkvterm -m {shlex.quote(system_name)} -p {shlex.quote(lpar_name)}"
    stdin = _SealedStdin()
    connection: Any = None
    try:
        connection, process, initial_data, cancelled = await _await_acquisition(
            config, command, stdin
        )
        if cancelled:
            released = await _release_uncancellable(config, system_name, lpar_name)
            logger.warning(
                "console acquisition on %s/%s was cancelled; released=%s",
                system_name,
                lpar_name,
                released,
            )
            raise asyncio.CancelledError
        try:
            data, stop_reason, error = await _collect_output(
                process,
                duration_seconds,
                max_bytes,
                idle_timeout_seconds,
                initial_data,
            )
        except asyncio.CancelledError:
            released = await _release_uncancellable(config, system_name, lpar_name)
            logger.warning(
                "console capture on %s/%s was cancelled; released=%s",
                system_name,
                lpar_name,
                released,
            )
            raise
        if HELD_SENTINEL in data:
            # P1 contention, possibly observed late: we never held the
            # vterm, so releasing would close the other holder's session.
            raise ConsoleHeldError(
                f"Another session already holds the console of "
                f"{lpar_name!r} on {system_name!r}; the capture never "
                "force-closes another holder's session."
            )
        released = await _release_uncancellable(config, system_name, lpar_name)
        result = ConsoleCapture(
            system=system_name,
            lpar=lpar_name,
            data=_truncate(data, max_bytes),
            stop_reason=stop_reason,
            released=released,
            error=error,
        )
        connection.close()
        return result
    finally:
        if connection is not None:
            connection.close()
        stdin.close()
