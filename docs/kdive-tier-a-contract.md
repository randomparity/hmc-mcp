# kdive Tier A contract

[Documentation index](index.md)

What kdive's BYO-host provider may rely on when it adopts a partition somebody else
provisioned: power, state and RMC readback, and a bounded read-only console capture. Every
name on this page is checked against the installed package by
[its structural test](../tests/app/test_kdive_tier_a_contract.py).

## Import path and status

kdive imports the LPAR domain modules under `hmcpctl.operations.lpar`:

| Need | Import |
|------|--------|
| Power on, off, cycle, reset | `hmcpctl.operations.lpar.core.power_lpar`, returning `hmcpctl.operations.lpar.core.LparPowerResult` |
| Partition and RMC readback | `hmcpctl.operations.lpar.core.get_lpar` |
| Partition state only | `hmcpctl.operations.lpar.core.get_lpar_state` |
| Console capture | `hmcpctl.operations.lpar.console.capture_lpar_console_by_selector` |
| Ownership readback | `hmcpctl.operations.lpar.ownership.list_lpar_ownership` |

These are **pre-release** domain operations: importable, but not compatibility promises.
Only the six names in `hmcpctl.api` are stable, and ADR 0118 keeps these operations out of
that facade; see [Python library](python-api.md#domain-operations). Pin a commit, and expect
signatures to move before a release promises them.

The same operations are MCP tools: `hmc_power_on_lpar`, `hmc_power_off_lpar`,
`hmc_dump_restart_lpar`, `hmc_get_lpar`, `hmc_get_lpar_state`, `hmc_capture_lpar_console`, and
`hmc_list_lpar_ownership`.

## PowerAction in job terms

kdive's `PowerAction` maps onto the HMC's PowerOn and PowerOff jobs. The PowerOff values are
the job's own parameters ([ADR 0164](adr/0164-lpar-poweroff-operation-parameters.md); the
mapping comes from epic #871 and issue #872):

| `PowerAction` | Job | `power_lpar` arguments |
|---------------|-----|------------------------|
| `on` | PowerOn | `power_on=True` |
| `off` | PowerOff | `power_on=False`, `operation=shutdown`, `immediate=True` |
| `cycle` | PowerOff with restart | as `off`, plus `restart=True` |
| `reset` | PowerOff with restart | as `off`, plus `restart=True` |
| graceful off | PowerOff | `power_on=False`, `operation=osshutdown` |
| force crash | PowerOff | `power_on=False`, `operation=dumprestart`, `allow_dump_restart=True` |

- `cycle` and `reset` submit the same job; the HMC cannot tell them apart.
- `operation=osshutdown` asks the partition's operating system to shut down and needs an
  active RMC connection to it.
- `operation=dumprestart` crashes the partition and takes a platform dump. It is refused with
  a `ValueError` unless `allow_dump_restart=True`; nothing else asks for confirmation.
- Over MCP the force crash is `hmc_dump_restart_lpar`, not `hmc_power_off_lpar`, so an access
  policy grants it separately ([ADR 0188](adr/0188-dump-restart-is-its-own-operation.md)).
- The vendor's fourth value, `dumpretry`, is not accepted.
- `on` submits a job only from the 'not activated' state, unless `force=True`. An activated
  partition ('running', 'starting', 'open firmware') submits no job and reports
  `already_running`; any other state is refused with an HTTP 409 error naming it.
  With `force=True` the job is always submitted; outside 'not activated' the HMC fails it
  (HSCL3681).
- With `wait=True`, a wait that times out returns the last-seen, non-terminal job. Poll it
  with `hmcpctl.operations.jobs.get_job` (tool: `hmc_get_job`); do not resubmit, least of
  all an `operation=dumprestart`.

## Console capture

`hmcpctl.operations.lpar.console.capture_lpar_console_by_selector` holds the partition's one
virtual terminal for a bounded window (duration, byte and idle limits) and returns the raw
bytes.

- **Read-only by construction.** The capture's stdin is a sealed pipe that is never written;
  no parameter or code path sends a byte to the console
  ([ADR 0072](adr/0072-bounded-lpar-console-capture.md)). A partition at an SMS menu or an
  installer prompt receives no input from a capture.
- **Released.** `hmcpctl.ssh.console.ConsoleCapture.released` is true only after an
  independent `mkvterm` probe proves the terminal slot free. False means the console may
  still be held and further console access should be treated as broken, except when
  `hmcpctl.ssh.console.ConsoleCapture.error` names
  `hmcpctl.ssh.console.ConsoleHoldLostError`: another client ended the hold, and hmcpctl
  issued no `rmvterm` against it.
- **Contention.** When another session already holds the terminal, the capture raises
  `hmcpctl.ssh.console.ConsoleHeldError`, captures nothing, and never takes the terminal
  over. It raises the same error after acquisition when the HMC's contention sentence
  appears in the captured output; its own hold has then been released, and the message
  says whether that release was proven. Neither path returns captured bytes.
- **Writing is a different class.** `hmcpctl.ssh.console.WritableConsoleSession`
  ([ADR 0176](adr/0176-console-input-and-exclusive-raw-mode.md)) is the only type that can
  write to a console. The capture never constructs it, and this contract does not cover it.
  The continuous read-only session is recorded in
  [ADR 0170](adr/0170-continuous-console-session.md) and is not part of Tier A.

## Ownership opt-in

`HMC_AUTHORIZE_POWER_OPERATIONS` (profile key `authorize_power_operations`) is off by
default. Off, `power_lpar` reads no ownership and opens no SSH connection: it powers a
partition another agent owns. A provider that cares reads
`hmcpctl.operations.lpar.ownership.list_lpar_ownership` itself.

On, `power_lpar` reads the partition's ownership stamp and refuses a partition another agent
owns unless the caller passes `ownership_override=True`, which records an audited override.
When no managed-system selector is given it finds the owning system itself, which is a
fleet-wide search; an override skips that search. A failed ownership read fails the call
with no job submitted. See
[ADR 0092](adr/0092-uniform-lpar-ownership-authorization-rule.md).

## SSH host-key verification

Console capture and `power_lpar`'s guarded ownership check run over SSH;
`list_lpar_ownership` is a REST read. `HMC_SSH_VERIFY_HOST_KEY` (profile key
`ssh_verify_host_key`) defaults to true: the HMC's host key must already be in the process
user's `~/.ssh/known_hosts` ([SSH trust setup](HMC_HINTS.md#ssh-host-key-trust)). Setting it
false disables server authentication for sessions that carry the HMC credentials, and every
connection warns.
