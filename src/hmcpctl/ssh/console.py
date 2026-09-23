"""Read-only LPAR console sessions and bounded capture over the HMC ``mkvterm`` CLI.

The HMC exposes exactly one virtual terminal (vterm) per partition through the
``mkvterm``/``rmvterm`` CLI pair over SSH. ``mkvterm`` never exits on its own:
it streams the partition console until torn down, so it structurally cannot
run through :func:`hmcpctl.ssh.transport.run_hmc_command` (a one-shot exec that collects
output until the remote command exits). :class:`ConsoleSession` holds the vterm
on top of :func:`hmcpctl.ssh.transport.open_hmc_connection` with no duration or
byte cap and owns its acquisition and proven release (ADR 0170);
:func:`capture_lpar_console` is its bounded consumer and enforces issue #385's
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
import errno
import logging
import math
import os
import shlex
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Literal, Self

import asyncssh

from hmcpctl.client.core import HMCClient

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
    from :class:`hmcpctl.ssh.transport.HMCCLIError`: a capture never force-closes
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
            except OSError as exc:
                if exc.errno != errno.EBADF:
                    raise
            self._read_fd = -1
        if self._write_fd != -1:
            try:
                os.close(self._write_fd)
            except OSError as exc:
                if exc.errno != errno.EBADF:
                    raise
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
    if introducer in (0x50, 0x58, 0x5E, 0x5F, 0x5D):  # DCS/SOS/PM/APC/OSC strings
        return data.find(b"\x1b\\", index + 1, cut) != -1 or (
            introducer == 0x5D and data.find(b"\x07", index + 1, cut) != -1
        )
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
    start = data.find(b"\x1b", 0, cut)
    while start != -1:
        if not _escape_complete(data, start, cut):
            return start
        start = data.find(b"\x1b", start + 1, cut)
    return cut


def _truncate(data: bytes, limit: int) -> bytes:
    """Cut *data* to at most *limit* bytes without splitting a sequence."""
    if len(data) <= limit:
        return bytes(data)
    cut = _utf8_safe_cut(data, limit)
    cut = _ansi_safe_cut(data, cut)
    return bytes(data[:cut])


async def _collect_output(
    session: ConsoleSession,
    duration_seconds: float,
    max_bytes: int,
    idle_timeout_seconds: float,
) -> tuple[bytes, StopReason, str | None]:
    """Read the stream until one of the three client-side bounds fires (P8).

    Read errors (transport drops included) end the collection with
    ``stop_reason="error"`` and whatever bytes arrived; the release path
    still runs, since P3 showed the HMC never reclaims the vterm itself.
    """
    loop = asyncio.get_running_loop()
    buf = bytearray()
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
                session.read(),
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
        try:
            outcome = await _read_release_probe(process)
        finally:
            stdin.close()
            connection.close()
        if outcome == "held":
            return False
        if outcome == "remote-exited":
            logger.warning(
                "release probe for %s/%s exited without proof of release",
                system_name,
                lpar_name,
            )
            return False
        if outcome == "acquired":
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


async def _read_release_probe(
    process: Any,
) -> Literal["acquired", "held", "remote-exited", "unproven"]:
    """Classify one bounded probe stream without making ownership decisions."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _RELEASE_PROBE_SECONDS
    output = bytearray()
    while (remaining := deadline - loop.time()) > 0:
        try:
            chunk = await asyncio.wait_for(process.stdout.read(_CHUNK), remaining)
        except TimeoutError:
            break
        except Exception:  # noqa: BLE001 - transport trouble leaves ownership unproven
            break
        if not chunk:
            return "remote-exited"
        output += chunk
        if HELD_SENTINEL in output:
            return "held"
        if ACQUIRED_SENTINEL in output:
            return "acquired"
    return "unproven"


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
    except (asyncssh.Error, OSError) as exc:
        connection.close()
        raise HMCCLIError(
            f"Unable to create the HMC console process for {command!r}: {exc}"
        ) from exc
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
                try:
                    chunk = await process.stdout.read(_CHUNK)
                except (asyncssh.Error, OSError) as exc:
                    connection.close()
                    raise HMCCLIError(
                        "HMC console acquisition read failed before the console "
                        f"was confirmed: {exc}"
                    ) from exc
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
            if task.done():
                task.result()
            cancelled = True
            continue
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
        return connection, process, data, cancelled


class ConsoleSession:
    """A read-only hold on one partition's vterm with no duration or byte cap.

    ADR 0170 is the contract. :meth:`open` returns once ``mkvterm`` proves
    this session acquired the vterm and raises :class:`ConsoleHeldError`,
    issuing no ``rmvterm``, when another session holds it (P1). :meth:`read`
    returns raw chunks, the acquisition bytes first and ``b""`` after the
    remote end closes; the session enforces no bound, so consumers wrap reads
    in their own timeouts. :meth:`close` is the only release: ``rmvterm``, an
    independent-session probe, then local teardown, run to completion even
    when the caller is cancelled (P2/P3/P4). Use the session as an async
    context manager so every exit that unwinds the owning coroutine closes it.

    Process exit: the library installs no ``atexit`` hook or signal handler.
    SIGKILL, ``os._exit``, a crash, default-action SIGTERM, a second SIGINT
    under :func:`asyncio.run`, an event loop stopped or shut down before
    :meth:`close` finishes, and a session dropped without :meth:`close` all
    leave the vterm held, because the HMC never releases it (P3). The next
    :meth:`open` then raises :class:`ConsoleHeldError`; ``rmvterm -m <system>
    -p <partition>`` recovers the console.

    stdin is sealed by construction (:class:`_SealedStdin`, P5/P7): no
    attribute or method of a session writes to the partition console.
    """

    def __init__(self, hmc: HMCClient, system_name: str, lpar_name: str) -> None:
        self._config = hmc.config
        self._system = system_name
        self._lpar = lpar_name
        self._state: Literal["new", "opening", "held", "unheld"] = "new"
        self._stdin: _SealedStdin | None = None
        self._connection: Any = None
        self._process: Any = None
        self._pending = b""
        self._close_task: asyncio.Task[bool] | None = None
        self._released: bool | None = None

    @property
    def released(self) -> bool | None:
        """``None`` before :meth:`close`; then ``True`` only on proven release."""
        return self._released

    async def open(self) -> None:
        """Acquire the vterm, or raise without releasing another holder's session.

        Raises:

            ConsoleHeldError: Another session holds the vterm (P1).
            HMCCLIError: ``mkvterm`` could not start or never confirmed acquisition.
            RuntimeError: The session was already opened or closed.
        """
        if self._state != "new" or self._close_task is not None:
            raise RuntimeError("a console session opens once and never after close()")
        self._state = "opening"
        command = f"mkvterm -m {shlex.quote(self._system)} -p {shlex.quote(self._lpar)}"
        self._stdin = _SealedStdin()
        try:
            connection, process, data, cancelled = await _await_acquisition(
                self._config, command, self._stdin
            )
        except BaseException:
            self._state = "unheld"
            self._stdin.close()
            raise
        self._connection, self._process = connection, process
        self._state = "held"
        self._pending = data
        if cancelled:
            released = await self.close()
            logger.warning(
                "console acquisition on %s/%s was cancelled; released=%s",
                self._system,
                self._lpar,
                released,
            )
            raise asyncio.CancelledError

    async def read(self) -> bytes:
        """Return the next raw chunk, or ``b""`` once the remote end has closed.

        Transport errors propagate unwrapped; the session still owes its release.
        """
        if self._state != "held" or self._close_task is not None:
            raise RuntimeError("the console session is not open")
        if self._pending:
            chunk, self._pending = self._pending, b""
            return chunk
        return await self._process.stdout.read(_CHUNK)

    async def close(self) -> bool:
        """Release the vterm once and report whether the release was proven.

        Later and concurrent calls await the same release. Cancelling the
        caller never interrupts the release; the cancellation is re-raised
        after it completes.
        """
        if self._state == "opening":
            raise RuntimeError(
                "close() cannot run while open() is in flight; cancel the opening task"
            )
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._teardown())
        cancelled = False
        while True:
            try:
                released = await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                if self._close_task.done():
                    self._close_task.result()  # the release itself was cancelled
                cancelled = True
                continue
            if cancelled:
                raise asyncio.CancelledError
            return released

    async def _teardown(self) -> bool:
        self._released = False
        try:
            if self._state == "held":
                self._released = await _release_and_verify(
                    self._config, self._system, self._lpar
                )
            return self._released
        finally:
            if self._connection is not None:
                self._connection.close()
            if self._stdin is not None:
                self._stdin.close()

    def _disown(self) -> None:
        """Owe no release: the capture's late-contention path (ADR 0072, ADR 0170)."""
        self._state = "unheld"

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        released = await self.close()
        if exc_type is asyncio.CancelledError:
            logger.warning(
                "console session on %s/%s was cancelled; released=%s",
                self._system,
                self._lpar,
                released,
            )

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> bytes:
        chunk = await self.read()
        if not chunk:
            raise StopAsyncIteration
        return chunk


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
    _validate_bounds(duration_seconds, max_bytes, idle_timeout_seconds)
    async with ConsoleSession(hmc, system_name, lpar_name) as session:
        data, stop_reason, error = await _collect_output(
            session, duration_seconds, max_bytes, idle_timeout_seconds
        )
        if HELD_SENTINEL in data:
            # P1 contention, possibly observed late: ADR 0072 keeps this path
            # free of rmvterm, which could close another holder's session.
            session._disown()
            raise ConsoleHeldError(
                f"Another session already holds the console of "
                f"{lpar_name!r} on {system_name!r}; the capture never "
                "force-closes another holder's session."
            )
    return ConsoleCapture(
        system=system_name,
        lpar=lpar_name,
        data=_truncate(data, max_bytes),
        stop_reason=stop_reason,
        released=session.released is True,
        error=error,
    )
