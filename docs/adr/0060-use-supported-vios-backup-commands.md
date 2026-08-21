# ADR 0060: Use supported VIOS backup commands

## Status

Accepted on 2026-08-20.

## Context

The three public VIOS backup tools call `lsviosbackup` and `chviosbackup`, commands absent on HMC
V10R3 SP1060 and V11R2 SP1120. The supported commands have materially different contracts:
`lsviosbk` filters by `vios_uuids`, `mkviosbk` requires system, VIOS, type, and output name, and
`rstviosbk` requires system, VIOS, restorable type, and input name. `rstviosbk` accepts only
`viosioconfig` and `ssp`; a full `vios` image is not restorable through that command.

The existing backup signature has no output name or managed-system selector. The existing restore
signature has no backup type and makes its managed-system selector optional. Correct command
construction therefore cannot preserve those contracts honestly.

## Decision

Replace the commands and public signatures together. Listing retains its current selector and runs
`lsviosbk --filter "vios_uuids=<uuid>"`. Backup takes managed-system selector, VIOS selector,
backup name, and an optional `vios`/`viosioconfig`/`ssp` type, then runs
`mkviosbk -t <type> -m <system-name> --uuid <vios-uuid> -f <backup-name>`. Restore takes
managed-system selector, VIOS selector, backup name, a required `viosioconfig`/`ssp` type, and an
optional restart-if-required flag, then runs
`rstviosbk -t <type> -m <system-name> --uuid <vios-uuid> -f <backup-name> [-r]`, with `-r` only
when requested.

Resolve a managed-system UUID to its HMC system name and resolve a VIOS name within that system to
its UUID before SSH command construction. Shell-quote every dynamic CLI value. Apply the existing
catalog-name refusal to both creation and restore names. Replace the old interfaces outright: no
alias, compatibility overload, or inferred default restore type.

## Consequences

The three tools issue commands supported by the verified HMC versions and expose every required
HMC input. Existing backup and restore callers must update their argument lists. Restore cannot
accidentally select SSP or VIOS I/O configuration semantics through an unstated default. Full-VIOS
backup remains available, while full-VIOS restore remains unavailable because it requires a
different operational workflow. Listing retains its existing result parser and return type.

The already-accepted `exhaustive_targets=False` classification on restore remains unchanged: an
explicit `ssp` restore can affect the cluster beyond the selected VIOS. The restart flag authorizes
the HMC to restart the VIOS only after a failed restore attempt, matching `rstviosbk -r`.

## Considered & rejected

- **Keep the public signatures and change only command names.** verified: IBM's `mkviosbk` and
  `rstviosbk` references require arguments the current signatures cannot supply, while live HMC
  V10R3 SP1060 and V11R2 SP1120 reject the current commands.
- **Infer the managed system from a VIOS UUID.** judgment: this adds fleet-wide discovery and an
  ambiguity/failure path to avoid one explicit selector that the HMC command already requires.
- **Default restore to `viosioconfig`.** judgment: a default would silently choose destructive
  semantics that the old interface never represented.
- **Accept `vios` restore and route it elsewhere.** verified: IBM's `rstviosbk` reference accepts
  only `viosioconfig` and `ssp`; full-image restore is a distinct workflow outside issue #289.
- **Retain an overload or compatibility shim.** judgment: two public contracts for one operation
  would preserve a path that cannot construct a valid HMC command.
- **Add overwrite and full-backup attribute controls.** judgment: `--force` and `-a` are optional
  `mkviosbk` features not required to repair the broken tools.
