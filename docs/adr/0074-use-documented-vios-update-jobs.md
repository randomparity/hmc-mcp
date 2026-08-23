# ADR 0074: Use the documented VIOS update jobs

## Status

Accepted

## Context

`hmc_vios_update` currently submits `Update` or `Upgrade` to matching `do/*`
paths and serializes the generic firmware `RepositorySource` vocabulary. IBM's
Power10 and Power11 REST references instead define `UpdateVIOS` and
`UpgradeVIOS`, with a shared VIOS parameter vocabulary and two deliberate
differences: only updates accept `IBMWebsite` and `RestartVIOS`, while only
upgrades accept `Disks`. Both jobs return an install log as `stdOut`.

The public tool already consolidates both operations behind `kind`; ADR 0004
settles that entry-point shape. The remaining decision is how to represent the
two related but non-identical request contracts without contaminating the
console or firmware contracts.

## Decision

Keep the consolidated `hmc_vios_update` entry point and replace its generic
repository argument with two VIOS-specific typed request shapes selected by
`kind`. Both shapes use IBM's documented parameter names. Runtime validation
requires `ResourceType`, rejects unknown keys, rejects update-only parameters
from upgrades and upgrade-only parameters from updates, and rejects
`IBMWebsite` for upgrades before opening an HMC connection.

The builders and REST paths use `UpdateVIOS` and `UpgradeVIOS`. A waited result
that contains a string-valued `stdOut` job parameter exposes a trimmed copy at
the result's top level while retaining the complete raw job structure. An
asynchronous submission remains unchanged because no operation result exists
yet.

## Consequences

VIOS callers must switch from the generic lowercase repository keys to IBM's
documented names. Firmware continues using `RepositorySource`; console updates
continue using `ConsoleUpdateSource`. The public schema describes the union of
the two VIOS request shapes, while runtime validation enforces the selected
operation's narrower contract. Consumers can read `stdOut` directly after a
wait without losing the source job payload used for diagnostics.

## Considered & rejected

- **Continue sharing `RepositorySource`.** verified: the Power10 and Power11
  `UpdateVIOS` and `UpgradeVIOS` captures list `ResourceType`, `SSHKey`, and
  `RemoteDirectory`, while `RepositorySource` emits lowercase generic keys;
  preserving it continues to create requests absent from both documented jobs.
- **Use one permissive VIOS source type for both operations.** judgment: it
  would advertise `IBMWebsite`, `RestartVIOS`, and `Disks` for operations that
  reject them and would move operation correctness entirely to prose.
- **Return only `stdOut` from waited calls.** judgment: discarding status,
  identifiers, and the raw job payload would make the operation less
  diagnosable and break the established wait-result behavior.
- **Add separate update and upgrade tools.** verified: ADR 0004 already governs
  the consolidated `kind` entry point; this defect does not supply new evidence
  that warrants superseding it.
- **Leave the implementation unchanged.** verified: neither Power10 nor
  Power11 reference captures define bare `Update` or `Upgrade` VIOS jobs, so
  the current requests cannot implement the documented operations.
