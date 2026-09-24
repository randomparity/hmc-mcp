# ADR 0176: Console input: a writable session, SysRq, and exclusive raw mode

## Status

Accepted (2026-09-23). Extends ADR 0170 within ADR 0172, ADR 0173 and ADR 0174. Amends ADR
0072's sealed-stdin rule (P5/P7) in part: the rule still holds for `ConsoleSession`, the bounded
capture, the release probe, and `hmc_capture_lpar_console`. It is lifted only inside a
`WritableConsoleSession`. Amends ADR 0170 rule 5 in part: the private stdin shape that
acquisition takes becomes `source` and `adopt(process)`, a rename that leaves the acquisition and
release logic unchanged.

## Context

kdive (#958) needs to write to a partition console in two ways. It injects SysRq while
collection keeps running (its shared "reading hold"), and it runs KGDB's remote protocol over an
exclusive channel (its "preempting hold"). mkvterm's stdin is the write socket to the console,
and EOF on it ends the vterm (P5). ADR 0072 therefore sealed stdin on every path.

The HMC documents one input sequence. `~.` ends the vterm session
(`docs/refs/hmc-commands-p11/commands/mkvterm.md`, DESCRIPTION). It documents no break or SysRq
sequence. The guest side is documented in Linux `drivers/tty/hvc/hvc_console.c` (`__hvc_poll`).
On the console hvc device, `^O` (`0x0f`) arms SysRq and the next byte goes to
`handle_sysrq()`. Whether the HMC vterm passes `^O` through unchanged is unverified until #879.

No authorization gate exists at the console layer. `take_over` (ADR 0172) is an unguarded opt-in
made when a session is built. `authorize_power_operations` switches on the ADR 0011 ownership
guard in the operations layer, and leasing between writers belongs to kdive (ADR-0539).

## Decision

1. **The capability is a type.** `WritableConsoleSession(ConsoleSession)` is the only class with
   write methods. It is built like a `ConsoleSession`, and constructing it is the authorization
   gate: nothing builds one on a caller's behalf. `ConsoleSession`, `ConsoleHandover`,
   `capture_lpar_console` and the MCP tool keep `_SealedStdin` and have no write surface.
2. **The stdin writer stays private.** A writable session's `mkvterm` gets an asyncssh stdin pipe.
   The session keeps the writer in a private `_ConsoleStdin`, which never sends EOF (P5). Every
   acquisition creates a new one: `open()`, `resume()`, and a reconnect.
3. **Typed writes only.** `write(data)` sends raw bytes while the collector owns the channel.
   `send_sysrq(key, *, prefix)` sends `prefix + key` as one write. `prefix` is required and has
   no default, because the HMC sequence is unverified (Context). `key` is one printable ASCII
   character. Inside `raw_mode()`, only the raw channel's `write(data)` works. `data` is
   non-empty `bytes`. asyncssh queues the whole buffer synchronously, and the drain only
   applies flow control, so a returned write proves the bytes were queued, not that the guest
   received them.
4. **Exclusive raw mode is a handover.** `async with session.raw_mode() as channel:` is ADR 0173
   mode (a) with a channel that adds `write`. The vterm stays held. The collector's `read()`
   waits, and the session's own `write()` and `send_sysrq()` raise `RuntimeError`. Leaving the
   block gives the channel back to the collector with no `rmvterm` and no `mkvterm`. Only one
   pause of either mode can be active at a time.
5. **When a write is refused.** A write raises `RuntimeError` unless the session is held,
   `close()` has not begun, no reconnect is in flight or unread, and the caller owns the channel.
   A suspended or dropped session holds no connection. Writes never start, wait for, or retry a
   reconnect. A transport error on a write propagates unwrapped, and only the collector's read
   turns a drop into a reconnect (ADR 0174). After a reconnect's `ConsoleGap`, writes go to the
   new stream. Under raw mode the session does not reconnect (ADR 0174 rule 6). The raw holder
   sees the drop, and the collector reconnects after the block exits.
6. **Every write is audited first.** Before the bytes are queued, the session emits a
   `console-write` record through `hmcpctl.audit.records`. The record carries `system`, `lpar`,
   `host`, `mode` (`shared` or `exclusive`), `input_kind` (`raw` or `sysrq`), `length`, and
   attribution. It never carries the bytes, not even the SysRq key, because console input can
   hold credentials and one content-free rule is easier to keep than an exception. A write that
   fails after its record is emitted stays recorded, as `install-attempted` does (ADR 0102).

## Consequences

- Input can end the console. `~.` in written bytes may end the vterm session, and whether the
  HMC recognizes it mid-line is unverified (#879). The collector then sees a remote close
  (`b""`). hmcpctl does not filter raw bytes, since KGDB traffic must pass through unchanged.
- `send_sysrq` does not make SysRq work. It frames one audited write. kdive supplies the prefix
  until #879 verifies one, and a later record may then add a default.
- Cancelling a write during its drain does not withdraw it. asyncssh has already queued the
  whole buffer (`SSHChannel.write`, asyncssh 2.24), so the bytes go out unless the connection
  dies. A caller must not retry a cancelled write on the assumption that nothing was sent,
  because a retried SysRq fires twice.
- A writable session does not consult the ADR 0011 ownership guard, even when
  `authorize_power_operations` is on. A SysRq write can therefore crash or reboot a partition
  whose power-off that guard would refuse. A caller that wants ownership enforcement calls
  `resolve_and_authorize_lpar_mutation` before constructing the session.
- Holders are not arbitrated. Two writers in one process each pass the gate; kdive's leases
  decide who writes (ADR-0539).
- Off the serve path hmcpctl installs no audit sink. Each record goes synchronously through the
  embedder's logging, or through `logging.lastResort`, and raw mode makes one such call per
  write. The embedder's logging configuration can suppress records with no drop count
  (`docs/authorization-audit.md`). What is guaranteed is one record emitted before each write,
  not its delivery.

## Considered & rejected

- **A `writable=True` flag on `ConsoleSession`.** judgment: fit. Every session would then carry
  write methods that raise at runtime. As a type, the read-only class has no write surface to
  reach, which matches how `_SealedStdin` works.
- **Reuse `authorize_power_operations`.** judgment: fit. That setting switches on the ADR 0011
  ownership guard, which needs REST and SSH reads from the operations layer. Leasing between
  writers is kdive's (ADR-0539), and no MCP or CLI entry point exists for ADR 0040 to gate.
- **A new `HMC_*` setting that allows writes.** judgment: cost. No served surface writes, and a
  library caller that builds the type has already decided.
- **Ship `^O` as the default SysRq prefix.** judgment: fit. The prefix is sourced from the guest
  kernel, not from the HMC, and the operator required an unverified sequence to stay explicit.
- **Write through the existing pipe (`_SealedStdin`'s write end).** judgment: fit. The pipe is a
  second buffer between the caller and the SSH channel, so a drain on it reflects the local
  pipe, not the channel's flow control. asyncssh's own stdin writer has neither problem.
- **Use ADR 0173 mode (b): `suspend()`, and let kdive run its own `mkvterm`.** judgment: fit.
  SysRq is injected while collection keeps running on the same stream. KGDB outside the session
  would reopen the release gap that mode (a) exists to remove (ADR 0173), and would lose the
  session's proven release (ADR 0170 rule 4).
- **Record the written bytes, or their hash, in the audit.** judgment: fit. Console input can
  carry credentials, and a hash of short input can be reversed by guessing.
- **Do nothing.** judgment: fit. kdive #1826 needs both holds, and shelling out to `mkvterm`
  would leave the release guarantees of ADR 0170 behind.
