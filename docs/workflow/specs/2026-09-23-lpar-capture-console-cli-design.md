# `hmcpctl lpars capture-console` (#959)

## Problem

Bounded console capture (`capture_lpar_console`, ADR 0072/0170) is reachable only through the
MCP tool `hmc_capture_lpar_console`, so the bare-CEC recipe documents a gap.

## Scope

- New `operations/lpar/console.py`: `capture_lpar_console_by_selector` takes the
  selector-to-CLI-name resolution now inline in the MCP tool, moved unchanged. The MCP tool, the
  one migrated caller, calls it and keeps building its own payload.
- New `cli_commands/lpar/console.py`: `hmcpctl lpars capture-console LPAR --system/-s SYSTEM
  [--duration 30] [--max-bytes 65536] [--idle-timeout 10] [--output FILE]`. `--system` is
  required because `mkvterm` needs it and owning-system discovery is out of scope. Defaults are
  the MCP tool's; limits are `MAX_CAPTURE_SECONDS`/`MAX_CAPTURE_BYTES`.
- Before any file or connection is opened, the command refuses (exit 2): a bound outside its
  limits, an existing `--output`, and, without `--output`, a terminal stdout.
- Raw bytes go to stdout, or to `--output`, opened with exclusive create (`xb`). If the capture
  raises, is interrupted, or the write fails, the file this command created is removed.
- When the capture returns, one stderr line reports stop reason, byte count, `released`, and any
  error; when it raises, stderr carries the `Error:` line only.
- Exit codes follow [ADR 0175](../../adr/0175-capture-console-exit-codes.md).
- Recipe step 5 captures by name with `--output`; its structural test expects the command.
  Excluded: optical/ISO (#776/PR #777), `--follow` (#957), bound changes.

### Failure model

1. Actors: a local operator or script running `hmcpctl` against their own HMC.
2. Invariants: no operator file is overwritten; an unproven release is never exit 0; stdin
   stays sealed (owned by `ssh/console.py`).
3. Accepted: console bytes are partition-controlled; they reach a file or pipe verbatim and a
   later consumer owns how it renders them. An interrupt exits through Python's
   `KeyboardInterrupt` status, outside ADR 0175; `released` is then logged, not an exit code.
4. Elsewhere: contention, release proof, vterm release on cancel (ADR 0170, #975); the SSH
   UUID-to-name lookup's attribute names (#776/PR #777).

Threat model: `--output` is an added boundary, an operator-chosen path written with the
operator's authority; control is `xb`. Partition bytes reaching stdout widen an existing one;
control is the terminal refusal.

## Success

1. Each option reaches `capture_lpar_console_by_selector` unchanged; stdout or file bytes equal
   `capture.data`, including on exits 1 and 3.
2. Contention exits 1 with the `ConsoleHeldError` message and leaves no output file.
3. Exit codes match ADR 0175 for every row of its table.
4. The MCP tool's payload is unchanged.

## Validation

- focused-test, `tests/app/test_cli_lpar_console.py`, red before the command exists: option
  forwarding; stdout and `--output` bytes; the three exit-2 refusals with no capture call;
  `--output` with a terminal stdout succeeds; file removal after a raised capture and a failed
  write; contention; exits 0/1/3 with bytes written; the stderr line's fields.
- focused-test: shared resolution — the existing MCP tool test in
  `tests/unit/test_console_capture.py` with resolution patch targets moved to the operation.
- focused-test: recipe — `tests/app/test_bare_cec_recipe.py` `EXPECTED_COMMANDS`.
- task-test-not-applicable: `docs/cli.md` and `CHANGELOG.md` prose; nothing executable reads
  those lines.
