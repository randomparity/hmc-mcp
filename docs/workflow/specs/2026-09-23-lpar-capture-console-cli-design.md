# `hmcpctl lpars capture-console` (#959)

## Problem

Bounded console capture (`capture_lpar_console`, ADR 0072/0170) is reachable only through the
MCP tool `hmc_capture_lpar_console`, so the bare-CEC recipe documents a gap.

## Scope

- New `operations/lpar/console.py`: `capture_lpar_console_by_selector` takes the
  selector-to-CLI-name resolution now inline in the MCP tool, moved unchanged, and
  `console_capture_payload` builds the tool's dict. The MCP tool, the one migrated caller,
  delegates to both; no copy of the resolution remains.
- New `cli_commands/lpar/console.py`: `hmcpctl lpars capture-console LPAR --system/-s SYSTEM
  [--duration 30] [--max-bytes 65536] [--idle-timeout 10] [--output FILE]`. `--system` is
  required because `mkvterm` needs it and owning-system discovery is out of scope. Defaults and
  limits are the capture function's.
- Raw bytes go to stdout, or to `--output`, opened with exclusive create (`xb`) before the
  capture. An existing file is refused before the console is touched; the file this command
  created is removed if the capture raises. A terminal stdout is refused.
- One stderr line reports stop reason, byte count, `released`, and any error.
- Exit codes follow [ADR 0175](../../adr/0175-capture-console-exit-codes.md): 0, 1, 2, 3.
- Recipe step 5 captures by name with `--output`; its structural test expects the command.
  Excluded: optical/ISO (#776/PR #777), `--follow` (#957), bound changes.

### Failure model

1. Actors: a local operator or script running `hmcpctl` against their own HMC.
2. Invariants: no operator file is overwritten; an unproven release is never exit 0; stdin
   stays sealed (owned by `ssh/console.py`).
3. Accepted: console bytes are partition-controlled; they reach a file or pipe verbatim and a
   later consumer owns how it renders them.
4. Elsewhere: contention, release proof, cancellation (ADR 0170, #975); the SSH UUID-to-name
   lookup's attribute names (#776/PR #777).

Threat model: `--output` is an added boundary, an operator-chosen path written with the
operator's authority; control is `xb`. Partition bytes reaching stdout widen an existing one;
control is the terminal refusal (exit 2).

## Success

1. Each option reaches `capture_lpar_console_by_selector` unchanged; stdout or file bytes equal
   `capture.data`.
2. Contention exits 1 with the `ConsoleHeldError` message and writes no output file.
3. Exit codes match ADR 0175 for every row of its table.
4. The MCP tool's payload is unchanged.

## Validation

- focused-test: option forwarding, stdout bytes, `--output` write, existing-file refusal before
  capture, removal after a raised capture, terminal refusal, contention, exit codes 0/1/3 —
  `tests/app/test_cli_lpar_console.py`, red before the command exists.
- focused-test: shared resolution and payload — the existing MCP tool test in
  `tests/unit/test_console_capture.py` with patch targets moved to the operation module.
- focused-test: recipe — `tests/app/test_bare_cec_recipe.py` `EXPECTED_COMMANDS`.
- task-test-not-applicable: `docs/cli.md` and `CHANGELOG.md` prose; nothing executable reads
  those lines.
