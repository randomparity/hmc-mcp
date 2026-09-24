# Boot order: clear refuses on the HMC (#1048)

## Problem

`lpars clear-boot-order` and `hmc_clear_lpar_boot_order` write an empty
`BootListInformation/PendingBootString` by the read-modify-write #980 introduced. V10R3 rejects
it with HTTP 500 `REST0126`, and the command has never cleared a pending boot order there.

Live probes, V10R3 M1060, 2026-09-24, on the authorized lab partition under the campaign lock.
Each attempt read `LogicalPartition/<uuid>?group=Advanced` before and after. The baseline was
restored with `set-boot-order` and read back.

| Attempt | Result |
|---|---|
| REST, `PendingBootString` element omitted | 200; value unchanged |
| REST, text `" "` | 500 `REST0126`; value unchanged |
| REST, `xsi:nil="true"` | 400 `REST0001`: the element's `{nillable}` property is false |
| CLI `chsyscfg ... boot_string=` | exit 1, the `REST0126` format message: REST relays `chsyscfg` |
| CLI `boot_string=none` | exit 0; stored the literal `none` |
| CLI `"boot_string="` (quoted pair) | exit 1, same format message |
| Activation (`chsysstate -o on`, profile) | pending string empty 15 s later, still empty after shutdown; `BootDeviceList` unchanged |

The IBM command reference lists `boot_string` for `chsyscfg` with no clear value. The REST
reference has no `PendingBootString` entry. The issue body records the earlier `boot_string=""`
result: a literal `"` was stored.

## Decision

**Clear refuses and writes nothing.** `clear_lpar_boot_order` keeps its parameters; its return
type becomes `NoReturn`. It resolves and authorizes the partition as it does today, then raises
`HMCError` with no status code. It does not call `set_pending_boot_string`. The message says:

- the HMC offers no value that clears a pending boot order (REST0126 on V10R3);
- a profile activation consumed the pending boot order when observed on V10R3; other activation
  paths, including the REST PowerOn job `lpars power-on` submits, are unverified (#879);
- `set-boot-order` replaces it.

The CLI command and MCP tool keep their names and arguments. Their help replaces the REST0126
note with this behavior, and the CLI drops its unreachable success message. The ISO recipe note
and `CHANGELOG.md` say the same, with the same bound on the activation claim. The client is
unchanged.

## Considered & rejected

- **Refuse before any request.** Operator decision (2026-09-24, relayed by the campaign): criterion 3
  is met by refusing before any write request, with authorization first. judgment: the MCP tool must open its client through
  `with_client(profile=...)` (ADR 0038, pinned by `tests/app/test_profile_routing.py`), so logon
  happens either way. Refusing before authorization would save only read-only resolve and
  ownership reads. It would also break ADR 0092's rule that every exposed LPAR mutator reaches
  an ownership helper. That rule's citation test and table are outside this change's surface.
- **Emulate clear by setting the pending string to `BootDeviceList`.** judgment: that sets a
  boot order the caller did not ask for, and an empty `BootDeviceList` (a never-booted
  partition) leaves nothing to write.
- **Remove the command.** judgment: that is a contract change the operator reserved.
- **Keep sending the empty value.** verified: 500 `REST0126` (issue body and the table above).

## Failure model

1. **Actors and deployments:** a local CLI operator and an agent-driven MCP client, against a
   V10R3 HMC as observed. V11R2 is unobserved.
2. **Invariants and assets:**
   - A refused clear changes no partition state. It sends no POST; the client's logon and
     logoff and the authorization reads still run.
   - The authorization order is unchanged: an unowned partition is still denied first.
3. **Accepted failure classes:**
   - An HMC release that does accept some clear form is refused anyway. The refusal is
     evidence-based for V10R3, and a later issue can lift it with new evidence.
   - Activation consumption was observed once, on one partition, with a profile activation.
     Other activation paths (REST PowerOn job, `--boot-mode sms`) are unobserved, and the docs
     say so.
   - `--ownership-override` on a refused clear still records the ownership-override audit
     event, because authorization runs first. The record overstates a bypass that wrote nothing.
4. **Covered elsewhere:** repeated consumption proof, #879. Generalizing the partition RMW, #1057.

## Success

1. `clear_lpar_boot_order` authorizes, then raises `HMCError` with the message above. It makes
   no client call after authorization, so no POST is sent.
2. The CLI exits 1 and prints the refusal. The MCP tool raises it.
3. The CLI help, MCP docstring, generated `docs/tools/boot_order.md`, ISO recipe and CHANGELOG
   describe the refusal and how a pending order ends.
4. Live: `clear-boot-order` on the lab partition refuses, and a read-back shows the pending
   string unchanged.

## Validation

- `focused-test` 1: `tests/lpar/test_boot_order.py` replaces
  `test_clear_lpar_boot_order_writes_an_empty_string` with a refusal test. It asserts the
  authorization call, the raised `HMCError` message, and `hmc.mock_calls == []`.
  Red against current code, which awaits the write. The clear arm of the 406-translation test
  is removed.
- `task-test-not-applicable` 2: the CLI and tool add no refusal logic. The operation's exception
  reaches the shared `run_cli_coroutine` → `fail` path (exit 1) and FastMCP's error path, and a
  test with a raising fake would pass before and after this change. The existing delegation test
  still pins the arguments; live 4 observes the exit.
- `task-test-not-applicable` 3: help and docs are prose. `just tool-docs-check` holds the
  generated page.
- Live 4: `hmcpctl lpars clear-boot-order` on minimus, then a `?group=Advanced` read.
- Guardrails: `just verify` and `uv run --no-sync prek run --all-files`.
