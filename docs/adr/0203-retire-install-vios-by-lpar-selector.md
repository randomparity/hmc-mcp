# 0203 — Retire the LogicalPartition-selector install tool

## Status

Accepted (2026-10-07), issue #1371. Amends
[ADR 0070](0070-installios-cli-bridge-for-install-tools.md), which kept two install tools on
the `installios` bridge; its mechanism is unchanged.

## Context

ADR 0070 bridged both install tools to the HMC CLI `installios` command and scoped the engine
to Virtual I/O Server images: the man page requires the `-p` partition to be of type Virtual
I/O Server. Two operations kept separate entry points onto that one engine:

- `install_vios` (`vios.install`, tool `hmc_install_vios`) resolves its target through the
  `VirtualIOServer` feed.
- `install_vios_by_lpar_selector` (`lpar.install_os`, tool
  `hmc_install_vios_by_lpar_selector`) resolves its target through the `LogicalPartition`
  feed only, and since #1247 refuses a name that appears only in the `VirtualIOServer` feed.

Both then read the resolved entry and refuse any `PartitionType` other than
`Virtual IO Server` before composing the command. A VIOS is listed only in the
`VirtualIOServer` feed (#1202), so no target passes both of the second operation's checks: it
can never submit an install. PR #1359 catalogued `lpar.install_os` as `absent`, missing
`vios-install-via-lpar-selector`, and left the contract change to this issue.

## Decision

Retire the operation. Remove the MCP tool `hmc_install_vios_by_lpar_selector`, the operation
`install_vios_by_lpar_selector` and its private resolver, and the `lpar.install_os` catalog and
maturity rows. `hmc_install_vios` (`vios.install`) is the one install tool; it already reaches
every target `installios` accepts. No alias or compatibility shim remains: the tool and the
operation are pre-release surfaces.

## Consequences

- The server exposes one tool fewer. A caller that named the retired tool gets an unknown-tool
  error and installs a VIOS with `hmc_install_vios`, whose arguments match except
  `vios_name_or_uuid` and `vios_ip` replace `lpar_name_or_uuid` and `lpar_ip`.
- `operations.vios.install` exports one operation, and ADR 0029's inventory says so.
- ADR 0092 §3.4a classifies only `install_vios`. Its §3.4b disposition of #366 no longer
  depends on ADR 0070's assumption 5: `install_vios` resolves only `VirtualIOServer`-feed
  targets, so even an `installios` widened beyond VIOS-type targets is not reachable against a
  `LogicalPartition` through any remaining install path. An LPAR-capable install path is new
  work that ADR 0092 §6 requires to be classified and guarded where it is introduced.
- `cli:commands/installios` stays bound, by `vios.install` alone.
- Positive live `vios.install` evidence is still a recorded gap; its prerequisites are a
  disposable VIOS-type partition, install media, a NIM network and a maintenance window.

## Considered & rejected

- **Redirect the selector to the `VirtualIOServer` feed.** verified: the operation would then
  resolve, read and submit exactly as `install_vios` does — `_submit_install` was shared and
  `install_vios`'s docstring already called the two "identical mechanism, contract, and return
  value" (`src/hmcpctl/operations/vios/install.py` at 9aa828b8, lines 356-364). It would be a
  pure alias under a name that promises LPAR targets.
- **Allow non-VIOS partitions.** verified: ADR 0070 scopes `installios` to Virtual I/O Server
  images, and the operation's own docstring restates that a general AIX or Linux NIM install
  stays on the NIM master (`src/hmcpctl/operations/vios/install.py` at 9aa828b8, lines
  268-272). Admitting a non-VIOS target would submit commands the engine refuses, and would
  move the operation into ADR 0092 §3.1 as an unguarded LPAR mutation.
- **Keep the tool and document that it always refuses.** judgment: a destructive-effect tool
  that can never act costs every caller a tool slot and a failed call, and the catalog would
  carry a permanently `absent` row.
