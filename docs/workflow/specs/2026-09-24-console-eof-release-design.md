# Console release through stdin EOF (#1058)

Decision record: the `#1058` amendment in ADR 0072's Status section.

## Problem

A held `ConsoleSession` releases with `rmvterm`. When another client's `rmvterm` has just ended the
hold, the HMC's lost-hold report reaches the holder about 1.5 s later (#1004). A `close()` in that
window still issues `rmvterm`, which can end the new holder's session.

Live capture (V10R3 M1060, 2026-09-24, six runs, `tests/fixtures/console/eof-release-transcript.json`):

- Single holder: after stdin EOF the vterm stays held for 10.1-10.5 s (a probe started 1 s after
  EOF found it held). Then the holder receives `The write socket has closed. Exiting.\n`, its
  stream ends, and `mkvterm` exits 0. A probe then acquires while the holder's SSH connection is
  still open.
- Takeover: B holds after `rmvterm` + `mkvterm`, then A sends EOF. In three orderings (A's EOF
  after B holds, between B's `rmvterm` and `mkvterm`, and 2 s before B's takeover), A exits the
  same way, B's stream stays open and silent, an independent probe finds the vterm held, and B's
  own close proves release.
- `mkvterm` sends `\r\n Open Completed. \r\n  ` about 0.5 s after the `Open in progress` banner.

In each captured ordering, EOF ended only the sender's own `mkvterm` and left the other holder's
session intact.

## Design

`_release_hold` (behind `close()` and `suspend()`) keeps its unread scan and lost-hold branch. After
them, a session whose stream has not ended releases this way:

1. Send EOF on the session's stdin: `_SealedStdin` closes its write end, `_ConsoleStdin` calls
   `write_eof()`. Both expose it as `release()`, a name outside the `write`/`send` prefixes that
   `test_read_only_surfaces_have_no_write_surface` forbids on the sealed surface.
2. Read the stream to its end, bounded by `_EOF_RELEASE_SECONDS = 20.0` (twice the observed
   latency), passing each chunk through the lost-hold watch.
3. The stream ended with the connection open: issue no `rmvterm`, then run the probe as today.
4. A loss latched while draining: the #1004 path, so `False` with no `rmvterm` and no probe.
5. Timeout, an error sending EOF or reading, or a closed connection: `rmvterm`, then the probe
   (today's path).

A session whose stream already ended before release keeps today's `rmvterm` and probe path. That
covers a remote close seen by a read or by the unread scan. The flag `_stream_ended` records that
end and resets on each acquisition. It is separate from `_remote_closed`, which gates
`_may_reconnect()` and is set only on the reconnect path.

The probe's own teardown releases the same way (amended by #1072, below).

Documentation that the change falsifies moves with it. ADR 0072 gets an "Amended by #1058" Status
block covering P5, P7 (the write end now closes at release, to end the stream), P8, and
decision 1. ADR 0170 rule 4, ADR 0172 rule 3, and ADR 0176 rule 2 each
get a one-line pointer to it. The module and method docstrings, the `hmc_capture_lpar_console` tool description (and the
generated `docs/tools/` page), and `CHANGELOG.md` change too.

## Failure model

1. **Actors and deployments:** library callers, the MCP capture tool, and the CLI, each driving one
   session against a V10 HMC. Untrusted: other console clients and partition console output.
2. **Invariants and assets:** another client's live hold; ADR 0170's proven-release guarantee for a
   hold the session still owns.
3. **Accepted failure classes:**
   - `close()` and `suspend()` take about 10 s longer on the EOF path, and a cancelled caller
     waits for that time (bounded by `_EOF_RELEASE_SECONDS`). The probe's own EOF wait adds about
     8.5 s more to every proven release (#1072), bounded by the same constant.
   - Fallback `rmvterm` (step 5, or a stream that had already ended) keeps the #1004 in-transit
     window on those paths only.
   - The probe's fallback `rmvterm` (a stream that ended before EOF, its EOF wait timing out, an
     error sending EOF or reading, or a closed connection) keeps its own window after it acquires
     (#1072).
   - The probe holds the vterm about 12 s instead of about 3 s, so another client's `mkvterm`
     during a release proof is refused with the contention message for about 8.5 s longer
     (#1072). That client can acquire once the probe's `mkvterm` exits.
   - Cancelling the release task itself, or shutting the loop down, during the probe's EOF wait
     closes its connection with no `rmvterm`, which may leave the probe's hold in place (P3).
     `close()` and `suspend()` shield that task, so only a direct cancellation reaches it. The
     same class existed during the probe's `rmvterm`, for about 3 s instead of about 12 s (#1072).
   - A firmware whose EOF does not end `mkvterm` within 20 s gets today's behaviour.
   - A stream that ends during the drain without releasing the hold gets no `rmvterm`, and
     `close()` returns the probe's `False`: in every captured run the exit after EOF meant release
     or a reported loss, and an `rmvterm` here could end another holder. The probe's own EOF
     wait is in the same class (#1072).
4. **Covered elsewhere:** vterm ownership query → operator; SysRq and `~.` → #879.

## Success

1. On a held session whose stream is open, `close()` sends EOF and waits for the stream to end.
   It then issues no `rmvterm` once the probe's own stream ends too (#1072), and it returns the
   probe's answer. The same holds for sealed and writable sessions, and for `suspend()`.
2. A lost-hold report that arrives while draining makes `close()` return `False` with no `rmvterm`.
3. The EOF wait timing out, an error sending EOF or reading, or a stream that ended before release
   each lead to `rmvterm`, then the probe.
4. The redacted fixture records the six runs, and a test pins its EOF exit message.
5. ADR 0072 carries the `#1058` amendment, and ADRs 0170, 0172, and 0176 carry pointers to it.
6. The probe answers `True` with no `rmvterm` when its stream ends after EOF on an open
   connection or the lost-hold report arrives; a stream that ended before EOF, its EOF wait timing
   out, an error sending EOF or reading, or a closed connection lead to its `rmvterm` (#1072).
7. A second redacted fixture records the probe's cost and second-client runs, and a test pins
   them (#1072).

## Amendment: the probe's teardown (#1072)

`_probe_released` used to tear its own hold down by closing its connection, then issuing
`rmvterm`. A client whose `mkvterm` landed in that gap lost its session to the probe's
`rmvterm`. The probe now releases the way the session does:

1. After the probe's `mkvterm` proves acquisition, send EOF on its `_SealedStdin` (`release()`).
2. Read its stream to the end, bounded by `_EOF_RELEASE_SECONDS`, keeping the connection open.
3. The stream ended on an open connection, or `LOST_HOLD_SENTINEL` arrived (another client's
   `rmvterm` already ended the probe's hold, #1004): no `rmvterm`, and the probe answers `True`.
4. A stream that had already ended before EOF, timeout, an error sending EOF or reading, or a
   closed connection: `rmvterm`, as before, and a failed `rmvterm` still answers `False`. The first
   case matches the session: an end the probe did not ask for proves nothing about its hold (P3).

Live capture (V10R3 M1060, 2026-09-24, `tests/fixtures/console/probe-eof-release-transcript.json`):

- Cost: six EOF teardowns took 11.56-12.22 s per probe, against 3.13-3.16 s for five `rmvterm`
  teardowns, so each release proof takes about 8.5 s longer. The probe's `mkvterm` exited
  10.1 s after EOF in every run. A session `close()` whose stream is open now takes about 22 s
  (21.89 s recorded).
- Second client: while the probe held, each of B's `mkvterm` attempts found the vterm held. B
  acquired 2.9 s after the probe's `mkvterm` exited, the probe issued no `rmvterm`, B's stream
  stayed open and silent, an independent probe found the vterm held, and B's own close released
  it with no `rmvterm`.

Decision: adopt. The 8.5 s is the same kind of cost #1058 accepted to close the same kind of
window, and a release whose EOF waits both end now issues no `rmvterm` at all.

## Considered & rejected

- **Keep `rmvterm` (do nothing).** judgment: fit. The live result shows a release that cannot end
  another holder, and #1058 asks for it where it removes the window.
- **Skip the probe once `mkvterm` exits.** verified: in the takeover runs A received the same
  exit message after losing its hold (fixture runs `takeover`, `race`, `eof-first`), so the
  message does not prove that A released its own hold.
- **Send EOF, then close the connection without waiting.** judgment: cost. P3 shows that closing
  the connection does not release, and the combination of EOF and an immediate close was not
  captured.
- **Wait for the `The write socket has closed` text.** judgment: fit. It ties release to firmware
  wording, and the stream end plus the probe already decide the outcome.
- **Keep the probe's `rmvterm` teardown (#1072).** measured: it saves about 8.5 s per release
  proof, and keeps a window in which the probe's `rmvterm` ends a client that acquired after the
  probe closed its connection.
