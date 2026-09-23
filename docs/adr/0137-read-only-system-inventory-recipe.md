# ADR 0137: Read-only system-inventory recipe

## Status

Accepted (2026-09-13)

## Context

Operators need evidence about one managed system before deciding whether any
later provisioning action is safe. Existing CLI read commands expose most of
that evidence, but no single capture workflow preserves it locally or makes
gaps visible.

## Decision

Publish a standalone recipe that writes JSON output from existing read-only CLI
commands to an operator-selected local directory. Use stable CLI commands where
available and label `raw get` XML captures as raw HMC responses. Every optional
or denied category is represented by an attempted-capture record rather than
silently omitted. The recipe never selects a resource or invokes a mutating
command. Capture system-wide NPIV/vFC ports through the stable CLI and use
documented full GETs for per-VIOS, per-volume-group, and per-SSP detail,
including physical volumes, vFC mappings, virtual disks, and logical units.
Exclude the POST-only GetFreePhysicalVolumes job.

## Consequences

The recipe is useful before future mutating workflows land and keeps its
evidence independent of them. Operators retain responsibility for protecting
the captured data and choosing candidate values. Some HMC details remain raw
XML until a stable CLI projection exists. Full SSP inventory is console-wide
because the existing list command has no managed-system selector.

## Considered & rejected

- **Require the ISO-install recipe first.** judgment: the inventory is a
  safety prerequisite for, not a dependent of, any later mutation workflow.
- **Use raw GET for all captures.** verified: `systems`, `lpars`, `vios`,
  `storage`, `network`, and `adapters` already provide CLI read commands in
  `src/hmc_mcp/cli_commands/`, so raw output would discard stable interfaces.
- **Use GetFreePhysicalVolumes to find candidate disks.** verified:
  `docs/refs/hmc-rest-api-p11/jobs/virtualioserver-jobs/148-getfreephysicalvolumes_virtualioserver-job.md`
  defines it as a POST job, outside this recipe's GET-only contract.
