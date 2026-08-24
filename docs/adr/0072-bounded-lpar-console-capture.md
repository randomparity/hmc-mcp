# ADR 0072: Bounded, Non-Interactive LPAR Console Capture over mkvterm

## Status

Accepted

## Context

When a NIM install fails, the only place the reason exists is the partition
console — the partition sits at an SMS menu, an open-firmware prompt, or a BOS
installation error screen. Issue #385 recorded the gap and its hazards, and
required a design "written after a live-hardware prototype has answered the
empirical questions".

That gate is satisfied. The P1–P8 prototype (comment of 2026-08-22 on #385)
was run against live hardware — **HMC V10R3 M1060** (Build 2408210051, iFixes
MF71697/MF71699/MF71703), a Power9 8375-42A at firmware FW950.00 (level 39),
with a disposable lpar_id=3 partition plus supporting observations on running
AIX and VIOS partitions — and every case has a recorded result. This ADR cites
those results per design fact; the raw bytes quoted below are verbatim from
the prototype record.

## Prototype evidence (P1–P8), per design fact

**Transport (P1/P5).** `run_hmc_command` is a one-shot exec that collects
output until the remote command exits; `mkvterm` never exits, so it would hang
until `ssh_timeout`. The capture therefore runs on a new primitive:
`ssh.open_hmc_connection` opens one long-lived asyncssh connection (connect
still bounded by `ssh_timeout`) and the capture hosts `mkvterm` via
`connection.create_process(..., encoding=None)` — a byte stream, never str.

**Contention detection (P1).** With session A holding `mkvterm`, session B
received on **stdout**, with **exit code 0**:

```
\r\n A terminal session is already open for this partition. \r\n Only one open session is allowed for a partition. \r\n Exiting....
```

(raw hex in the prototype record; each line begins and ends with a space).
Exit code alone cannot detect contention. The implementation parses stdout for
the sentinel sentence `A terminal session is already open for this partition.`
and raises `ConsoleHeldError` — a distinct, documented `HMCError` subclass.
Because exit 0 accompanies both success and contention, success is *never*
inferred from the exit status; it is the absence of the sentinel while bytes
stream.

**Release semantics (P2/P3/P4).**

- P2: `rmvterm` printed `Close command sent`, exited 0, and freed the slot —
  but its exit code alone was explicitly shown not to be proof of release.
- P3: after SIGKILL of the SSH process, independent `mkvterm` attempts failed
  with the contention message at T=0s, T=30s, and T=5min+. The HMC does **not**
  auto-release a vterm after an abrupt drop, ever, within the tested window;
  an explicit reconnect-then-`rmvterm` recovered it immediately.
- P4: SIGTERM likewise left the vterm held; a cancellation handler that merely
  stops the task leaks it.

Design consequences, all implemented:

1. `rmvterm` runs on **every** exit path — normal stop, all three bounds,
   transport failure, error, and `CancelledError` — via try/finally plus a
   shielded release task that runs to completion before cancellation
   propagates (`_release_uncancellable`; repeated cancellation interrupts only
   the wait, never the release).
2. `released=True` requires proof per P2: after `rmvterm`, a fresh `mkvterm`
   probe from an independent connection must start without the sentinel. That
   proof probe acquires a vterm itself, so it tears its own session down
   (connection closed + `rmvterm`), since P3 says nothing releases it
   otherwise. If the probe sees the sentinel, or cannot start, or produces no
   output within its window, `released=False` is reported honestly and the
   caller is told the console may still be held. In the no-output unknown case
   `rmvterm` is issued anyway — biasing against leaking our own possibly
   acquired vterm — but the result stays unproven. On the probe-teardown
   failure path the capture's own `released=True` stands (it was proven) and
   the probe's possible leak is logged as an error.

**stdin sealing (P5/P7).** mkvterm's stdin is wired as the write socket to the
partition console: with stdin at EOF the HMC terminated the vterm with
`The write socket has closed. Exiting.` — which also rules out `DEVNULL`.
Bytes written to stdin were forwarded to the partitions (no echo back under a
non-PTY exec). The construction is therefore a pipe: the read end is handed to
`create_process(stdin=...)`, the write end is held open inside a private
`_SealedStdin` object and never written. There is no parameter, method, or
code path that can send a byte; the class exposes only the fd and `close()`.
Closing the write end happens solely during local teardown, after the stream
has ended.

**Client-side bounds (P8/P5).** All observed idle streams sent a fixed 24-byte
banner (`\r\n Open in progress  \r\n `) on open and then stayed open and
silent indefinitely — no keepalives, no HMC-side idle timeout, no close. All
three bounds are therefore enforced client-side:

- `duration_seconds` — wall-clock cap on the whole capture;
- `max_bytes` — cap on collected bytes;
- `idle_timeout_seconds` — time since the last received byte.

`stop_reason` distinguishes `duration`, `max_bytes`, `idle`,
`remote-close`, and `error`. Contention is not a stop reason: it raises
`ConsoleHeldError`.

**Byte integrity (P6).** Captured bytes are returned raw as `bytes`; decoding
is the caller's decision. Truncation at `max_bytes` backtracks to a boundary
that cannot split a multi-byte UTF-8 sequence (continuation bytes `0x80–0xBF`
pulled in behind their lead byte) or an incomplete ANSI escape sequence
(backtracking to before an `ESC` whose final byte `0x40–0x7E` — CSI, string,
and intermediate forms — has not arrived).

**Recorded caveat, kept honest:** every live observation was 7-bit ASCII from
*idle* partitions — no high bytes, no `0x1B`, single-chunk banner. The ANSI
truncation rule is protocol-derived (ECMA-48 shapes), not prototype-verified.

## Decision

Add a bounded capture, not a terminal:

- `hmc_mcp.ssh.open_hmc_connection` — the long-lived connection primitive.
- `hmc_mcp.console_capture.capture_lpar_console(config, system_name,
  lpar_name, *, duration_seconds, max_bytes, idle_timeout_seconds) ->
  ConsoleCapture` with `ConsoleCapture(system, lpar, data, stop_reason,
  released)` and `ConsoleHeldError`; exported through `hmc_mcp.api`.
- `hmc_capture_lpar_console` MCP tool (`server_console.py`, operation
  `lpar.capture_console`).

**Effect classification: `mutate`, deliberately not `read`.** The capture
writes nothing to the partition and changes no HMC configuration, but it
*steals time on a contended exclusive resource*: it holds the partition's
single vterm slot for the whole bounded window, locking out any operator
console during the capture, and a failed release breaks the console for
everyone until a manual `rmvterm`. `readOnlyHint=True` would let clients
auto-batch it alongside harmless reads; `destructiveHint=True` (the
`destructive` class) would overstate it — nothing is destroyed and another
holder's session is never force-closed. `mutate` (`readOnlyHint=False`,
`destructiveHint` left at the MCP default) reflects that operational risk
honestly. Target selectors are exactly the two identities the call acts on
(`system_name_or_uuid`, `lpar_name_or_uuid`), so `exhaustive_targets` holds
and policy `targets` tables can grant it narrowly.

UUID resolution follows the established SSH-passthrough pattern (REST resolve
to names first, `lssyscfg` fallback), because whether `mkvterm`/`rmvterm`
accept UUIDs is unverified; CLI names are always what is sent. Every
interpolated name is `shlex.quote`d.

Tool-level ceilings (`MAX_CAPTURE_BYTES = 1 MiB`, `MAX_CAPTURE_SECONDS =
3600`) bound server-side memory and wall clock; defaults are
30 s / 64 KiB / 10 s idle. The MCP result carries `data_base64` (raw stream,
losslessly encoded — console content must not be injectable into whatever
renders the result), `stop_reason`, `released`, and counts.

## Issue hazard list, point by point

- **One vterm per partition; never force-close another's session; distinct
  documented contention error.** Satisfied: sentinel parsing (P1) →
  `ConsoleHeldError`; no `rmvterm` is ever issued on a contention path.
- **Release mandatory on every path including cancellation and transport
  failure; `released` reported honestly.** Satisfied: shielded
  run-to-completion release on all paths (P3/P4); proof by independent
  follow-up `mkvterm` (P2); `released=False` otherwise.
- **stdin closed by construction; no code path can write.** Satisfied: sealed
  pipe write-end held open, never written, no writing API surface (P5/P7);
  `DEVNULL` specifically rejected because EOF kills the vterm.
- **Raw terminal bytes; truncation preserves sequence integrity; return
  bytes.** Satisfied with the recorded assumption below.
- **All three bounds enforced; distinguishable stop reasons.** Satisfied
  (P8): client-side enforcement is the only kind that works.
- **Recorded prototype observations accompany the design, firmware level
  included.** Satisfied: this ADR + the P1–P8 comment on #385.

## Assumptions and open items

1. **Live-install stream shape is unverified (P6 partially answered).** Only
   idle-partition streams were observed live. During an active BOS install the
   stream will carry dense VT100/ANSI sequences in small bursts; the UTF-8 and
   ESC backtracking rules are protocol-derived, not prototype-verified. First
   live install capture should diff the truncation boundaries against the raw
   stream.
2. The sentinel sentence is matched as a substring; console output that
   legitimately contains that exact English sentence would be misread as
   contention. Accepted as negligible for an HMC diagnostic message.
3. Whether any HMC firmware offers a vterm-state query command (which would
   make release verification cheaper than a real `mkvterm` probe) remains
   unresearched.
4. Probe teardown failure leaves a possible leaked vterm, logged but not
   retried; a retry loop was rejected as unbounded cleanup against a possibly
   foreign holder.

## Consequences

- Failed NIM installs become diagnosable from the package: boot text, SMS
  menus, firmware prompts, and installer errors are capturable in bounded,
  sealed, non-interactive windows.
- The facade manifest grows by three exports and moves the frozen signature
  digest (minor release under ADR 0029); the live tool count rises to 138.
- Operators gain a new way to hold the console exclusively for a bounded
  window; the `mutate` classification keeps it out of auto-approved read
  batches, and policies grant it explicitly like any other tool.

## Considered & rejected

- **An interactive terminal.** The issue forbids it; a write path to an SMS/
  firmware/installer prompt is the exact hazard this design exists to avoid.
- **Reusing `run_hmc_command` with a large timeout.** It collects until the
  remote command exits; `mkvterm` never does. Structurally impossible.
- **`stdin=DEVNULL`.** P5 showed EOF terminates the vterm ("The write socket
  has closed"); only a held-open pipe keeps the stream alive without a write
  surface.
- **Trusting `rmvterm`'s exit code, or returning `released=True` after issuing
  it.** P2 demonstrated a 0 exit with no release-proof value; honesty requires
  the independent-session probe.
- **Reporting contention as a `stop_reason` instead of raising.** The issue's
  contract makes "another session holds the vterm" a distinct documented
  error; folding it into a successful result would hide the operational
  situation from callers that handle exceptions.
