# Bounded LPAR reference-code read

**Issue:** #874 (epic #871) · **Branch:** `feat/lpar-refcode-read-874` from `main`

## Problem

The only boot-progress signal the package offers is state polling or holding the virtual terminal.
`lsrefcode -r lpar` needs no vterm and returns a partition's current SRC at the same poll that
reports `Running`. `rg -n lsrefcode src/` returns nothing: no wrapper exists.

## Scope

One read-only SSH function in a new `src/hmc_mcp/ssh/refcodes.py`, one MCP tool in
`server_tools/lpar/lifecycle_boot.py`, one `lpars refcodes` subcommand in
`cli_commands/lpar/inventory.py`, one `lpar.read_refcodes` record in
`docs/capabilities/operations.json`. A new focused `ssh/` module matches the existing
one-topic-per-module layout (`affinity`, `console`, `memory`, `sriov`); neither `io_inventory`
("physical I/O, Fibre Channel, SEA") nor `lpar` ("creation, ownership, validation, name
resolution") owns reference codes. No ownership transition and no caller migration: nothing calls
this code today. No new quoting or parsing primitive is written: `build_filter` (`ssh/commands.py:144`) emits
`lpar_names=<n>` and refuses a value carrying `,`, `=`, `"` or a control character, and
`parse_hmc_delimited_rows` (`:29`) parses `-F --header` output, refusing a mismatched header.

**The parsed field set** is `-F lpar_name,time_stamp,refcode --header`. Neither corpus page
enumerates `lsrefcode`'s `-F` attributes — the `-F` row at
`docs/refs/hmc-commands-p11/commands/lsrefcode.md:33` names none, and those three are the only
attribute names anywhere on either page (same file, `:54`), so the issue's "word fields as the
corpus names them" is not transcribable. Bare `-F --header` returns whatever attributes the
firmware defines, which is not a stable set; naming an unevidenced attribute fails the whole
command. The three evidenced names, with `parse_hmc_delimited_rows` failing closed on a header
mismatch, is the only option that is both evidenced and stable; #879 confirms it against hardware.

**The bound** is `count: int = 1`, refused outside `1..MAX_REFCODE_COUNT` (100) and refused for a
non-`int` (`bool` included) before any interpolation. The default matches the HMC's own — `-n`
omitted lists only the current code — while the command always passes `-n` so its shape is fixed.

### Failure model

- **Actors and deployments** — a local operator at the `hmc-mcp` CLI; an MCP client the operator
  has granted this tool. No anonymous or multi-tenant deployment.
- **Invariants at stake** — the read stays read-only and never holds a virtual terminal; a
  caller-supplied selector must not alter the command's structure; returned rows must not silently
  shift columns; a new public tool and CLI contract.
- **Accepted failure classes** — an HMC naming these three attributes differently raises rather
  than returning wrong rows (fails closed; no live evidence yet); a partition with no reference
  codes is indistinguishable from empty output and returns `[]`; `-n` above 100 is refused rather
  than streamed.
- **Covered elsewhere** — `lsrefcode -r sys`, `-s p|s`, FRU and LED inventory: #691.
  Multi-partition `lpar_names=p1,p2`: `build_filter` refuses an embedded comma; follow-up if #876
  needs it. Live observation and promotion to `current` maturity: #879. SSH transport, timeout and
  credential handling: `ssh/transport.py`, unchanged here.

### Threat model

- **Boundary added** — one: caller-supplied `system_name`, `lpar_name` and `count` reaching a
  remote shell command string. None is widened; `run_hmc_command` is unchanged.
- **Actor model** — the untrusted input is the selector text, not the caller, who is already
  authorized to run HMC commands. Trust sits in `ssh/transport.py` and the HMC's own
  authorization, as in every sibling SSH read.
- **Control per boundary** — `system_name` and the composed filter: `shlex.quote`, so each is one
  shell word. `lpar_name`: `build_filter`'s record-grammar check first, then `shlex.quote` — two
  parsers, two guards, neither substituting for the other. `count`: type and range check before
  interpolation, so no non-numeric text reaches the string. Refusals name the argument and the
  offending value and carry no credential.
- **Out of scope** — HMC-side authorization; SSH host-key policy; anything the
  `arbitrary-command` tool already permits an operator to do directly.

## Success

1. `hmc_read_lpar_refcodes(system_name_or_uuid, lpar_name_or_uuid, count=1)` issues `lsrefcode -r
   lpar -m <sys> --filter lpar_names=<n> -n <N> -F lpar_name,time_stamp,refcode --header` and
   returns one dict per row.
2. `hmc-mcp lpars refcodes <system> <lpar> [--count N] [--json]` mirrors it.
3. `lpar.read_refcodes` is recorded in `docs/capabilities/operations.json`; `just tool-docs-check`,
   `just doc-freshness` and `just capability-inventory` pass. It stays `unrecorded` in maturity.
4. `just verify` and `uv run --no-sync prek run --all-files` are green.

## Validation

Every entry is `Mode: focused-test`. Six live in the new `tests/unit/test_ssh_refcodes.py`: `count`
outside `1..100` or not an `int` is refused before any SSH traffic; the command string is exactly
Success 1's with every interpolated value quoted; shell metacharacters in a selector stay inside
one quoted word; a comma-, `=`- or quote-bearing selector raises `HMCCLIError`; blank and
header-only stdout both return `[]`; a non-zero `lsrefcode` exit surfaces as `HMCCLIError`. Three
more pin registration: `tests/unit/test_server_module_boundaries.py` on the handler's module, `just
capability-inventory` on the ledger record, `tests/app/test_application_boundaries.py` on the
default deployment exposing one more tool.
