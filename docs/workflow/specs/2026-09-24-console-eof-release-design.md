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

The probe's own teardown keeps `rmvterm`.

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
     waits for that time (bounded by `_EOF_RELEASE_SECONDS`).
   - Fallback `rmvterm` (step 5, or a stream that had already ended) keeps the #1004 in-transit
     window on those paths only.
   - The probe's teardown `rmvterm` keeps its own window after it acquires.
   - A firmware whose EOF does not end `mkvterm` within 20 s gets today's behaviour.
   - A stream that ends during the drain without releasing the hold gets no `rmvterm`, and
     `close()` returns the probe's `False`: in every captured run the exit after EOF meant release
     or a reported loss, and an `rmvterm` here could end another holder.
4. **Covered elsewhere:** vterm ownership query → operator; SysRq and `~.` → #879.

## Success

1. On a held session whose stream is open, `close()` sends EOF and waits for the stream to end.
   It then issues only the probe's own `rmvterm`, and it returns the probe's answer. The same holds
   for sealed and writable sessions, and for `suspend()`.
2. A lost-hold report that arrives while draining makes `close()` return `False` with no `rmvterm`.
3. The EOF wait timing out, an error sending EOF or reading, or a stream that ended before release
   each lead to `rmvterm`, then the probe.
4. The redacted fixture records the six runs, and a test pins its EOF exit message.
5. ADR 0072 carries the `#1058` amendment, and ADRs 0170, 0172, and 0176 carry pointers to it.

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
