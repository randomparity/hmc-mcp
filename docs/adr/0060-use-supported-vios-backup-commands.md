# ADR 0060: Use supported VIOS backup commands

## Status

Accepted on 2026-08-20. Authorization consequence approved on 2026-08-20 after whole-branch
review.

## Context

The three public VIOS backup tools call `lsviosbackup` and `chviosbackup`, commands absent on HMC
V10R3 SP1060 and V11R2 SP1120. The supported commands have materially different contracts:
`lsviosbk` filters by `vios_uuids`, `mkviosbk` requires system, VIOS, type, and output name, and
`rstviosbk` requires system, VIOS, restorable type, and input name. `rstviosbk` accepts only
`viosioconfig` and `ssp`; a full `vios` image is not restorable through that command.

The existing backup signature has no output name or managed-system selector. The existing restore
signature has no backup type and makes its managed-system selector optional. Correct command
construction therefore cannot preserve those contracts honestly.

IBM's V9.1.940 command inventory does not include the replacement commands. These three tools
therefore require HMC V10 or newer; the repository's general V8–V11 support remains unchanged for
other operations. No unverified older-HMC fallback is introduced.

## Decision

Replace the commands and public signatures together. Listing retains its current selector and runs
`lsviosbk --filter "vios_uuids=<uuid>" -F name,type --header`, then parses the explicit
comma-delimited projection rather than the old command's presumed fixed-width display. Backup
takes managed-system selector, VIOS selector, backup name, and an optional
`vios`/`viosioconfig`/`ssp` type, then runs
`mkviosbk -t <type> -m <system-name> --uuid <vios-uuid> -f <backup-name>`. Restore takes
managed-system selector, VIOS selector, backup name, a required `viosioconfig`/`ssp` type, and an
optional restart-if-required flag, then runs
`rstviosbk -t <type> -m <system-name> --uuid <vios-uuid> -f <backup-name> [-r]`, with `-r` only
when requested.

Make `backup_name` keyword-only for backup and `backup_type` keyword-only for restore. The old
maximum-arity positional calls otherwise bind successfully to different replacement parameters,
which could reinterpret mutating targets instead of failing closed. Keyword-only boundaries make
those legacy Python shapes fail binding without a compatibility shim. MCP callers are protected
separately by the replacement schema's newly required system, backup-name, and restore-type fields.

Preserve the managed-system selector's identity before SSH command construction. A caller-supplied
name remains the `-m` value. Resolve a caller-supplied UUID to its machine type, model, and serial
(MTMS), because IBM requires MTMS when user-defined system names collide; fail before SSH if that
unique CLI identity is unavailable. Resolve a VIOS name within the selected system to its UUID.
Shell-quote every dynamic CLI value. Apply the existing catalog-name refusal to both creation and
restore names. Replace the old interfaces outright: no alias, compatibility overload, or inferred
default restore type.

A direct managed-system name plus VIOS UUID is already CLI-ready and must reach SSH without
opening a REST client. When either selector needs REST resolution, build one configuration snapshot
for the call and use that same object for both REST and SSH so mutable profile state cannot route
the two legs to different HMCs.

Keep the new managed-system selector and VIOS selector as required authorization metadata on
`hmc_backup_vios`. Because backup is non-exhaustive, the existing policy model authorizes it only
through `targets = "all-targets"`; the grant may reach it by explicit tool name or effect class. A
targets table cannot authorize the tool even if it contains both selector kinds. Restore keeps its
previously accepted non-exhaustive target
classification and the same all-targets rule.

Backup is also non-exhaustive. An `ssp` backup covers the cluster and associated nodes beyond the
managed-system and VIOS selectors used to enter the command, so those selectors cannot claim to
enumerate every affected target.

## Consequences

The three tools issue commands supported by the verified HMC versions and expose every required
HMC input. Existing backup and restore callers must update their argument lists. Restore cannot
accidentally select SSP or VIOS I/O configuration semantics through an unstated default. Full-VIOS
backup remains available, while full-VIOS restore remains unavailable because it requires a
different operational workflow. Listing retains `list[dict[str, str]]` but now returns the actual
`name` and `type` projection documented by its header instead of invented fixed-width headings.

The already-accepted `exhaustive_targets=False` classification on restore remains unchanged: an
explicit `ssp` restore can affect the cluster beyond the selected VIOS. The restart flag authorizes
the HMC to restart the VIOS only after a failed restore attempt, matching `rstviosbk -r`.
Using MTMS for a UUID selector preserves uniqueness when user-defined names collide; a UUID whose
REST representation lacks a complete MTMS fails closed rather than degrading to a name.
Because backup is non-exhaustive, policy migration is required: VIOS-only and combined narrow
target tables cannot authorize it. Operators should use an explicitly named
`targets = "all-targets"` grant for least privilege; a broader effect/all-targets grant also reaches
the operation under the existing policy model. Both selectors remain required call inputs and
extracted metadata for authorization diagnostics and audit, but they are not independently
matchable grants.
Operators on HMC V8 or V9 receive a documented unsupported-version boundary for these tools rather
than an implied promise that the replacement commands exist there.

## Considered & rejected

- **Keep the public signatures and change only command names.** verified: IBM's `mkviosbk` and
  `rstviosbk` references require arguments the current signatures cannot supply, while live HMC
  V10R3 SP1060 and V11R2 SP1120 reject the current commands.
- **Infer the managed system from a VIOS UUID.** judgment: this adds fleet-wide discovery and an
  ambiguity/failure path to avoid one explicit selector that the HMC command already requires.
- **Resolve a system UUID to its user-defined name.** verified: IBM's `mkviosbk` and `rstviosbk`
  references require MTMS when user-defined names collide, so name conversion can discard the
  unique identity the caller supplied.
- **Default restore to `viosioconfig`.** judgment: a default would silently choose destructive
  semantics that the old interface never represented.
- **Accept `vios` restore and route it elsewhere.** verified: IBM's `rstviosbk` reference accepts
  only `viosioconfig` and `ssp`; full-image restore is a distinct workflow outside issue #289.
- **Retain an overload or compatibility shim.** judgment: two public contracts for one operation
  would preserve a path that cannot construct a valid HMC command.
- **Retain the fixed-width list parser.** verified: IBM's `lsviosbk` reference supports an explicit
  delimiter-separated `-F` projection and `--header`; no evidence establishes that the replacement
  command emits the old parser's two-or-more-space layout.
- **Add overwrite and full-backup attribute controls.** judgment: `--force` and `-a` are optional
  `mkviosbk` features not required to repair the broken tools.
