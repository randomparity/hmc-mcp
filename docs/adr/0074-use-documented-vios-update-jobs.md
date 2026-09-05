# ADR 0074: Use the documented VIOS update jobs

## Status

Accepted; entry-point decision superseded by [ADR 0103](0103-explicit-vios-update-and-upgrade-operations.md)

## Context

`hmc_vios_update` currently submits `Update` or `Upgrade` to matching `do/*`
paths and serializes the generic firmware `RepositorySource` vocabulary. IBM's
Power10 and Power11 REST references instead define `UpdateVIOS` and
`UpgradeVIOS`, with a shared VIOS parameter vocabulary and two deliberate
differences: only updates accept `IBMWebsite` and `RestartVIOS`, while only
upgrades accept `Disks`. Both jobs return an install log as `stdOut`.

The issue and frozen campaign scope retain the existing public tool and its
`kind` selector. ADR 0012 requires a stable result shape for each public tool;
retaining the entry point therefore also requires update and upgrade to share
one predictable outer result contract. The remaining decision is how to
represent the two related but non-identical requests without contaminating the
console or firmware contracts.

## Decision

Keep the consolidated `hmc_vios_update` entry point and replace its generic
repository argument with two VIOS-specific typed request shapes selected by
`kind`. Runtime validation enforces this exact contract before opening an HMC
connection:

| Operation | `ResourceType` values | Common optional parameters | Exclusive parameter |
| --- | --- | --- | --- |
| `UpdateVIOS` | `HMC`, `NFS`, `SFTP`, `USB`, `IBMWebsite` | `Name`, `ServerHostOrIP`, `UserName`, `Password`, `SSHKey`, `PassPhrase`, `RemoteDirectory`, `FileNames`, `MountLocation`, `MountOptions`, `USBDevice`, `SaveFile` | `RestartVIOS` |
| `UpgradeVIOS` | `HMC`, `NFS`, `SFTP`, `USB` | `Name`, `ServerHostOrIP`, `UserName`, `Password`, `SSHKey`, `PassPhrase`, `RemoteDirectory`, `FileNames`, `MountLocation`, `MountOptions`, `USBDevice`, `SaveFile` | `Disks` |

`ResourceType` is required. Each resource variant also exposes its source
requirements in the public schema: `Name` for HMC, server and remote directory
for NFS/SFTP, and device for USB; upgrade requires `Disks`. `SaveFile=true`
requires `Name`. Unknown keys, the other operation's exclusive parameter, and
`IBMWebsite` on upgrade are rejected.

The builders and REST paths use `UpdateVIOS` and `UpgradeVIOS`. A waited result
whose status is in the shared terminal-status set exposes the first non-empty
string-valued `stdOut` job parameter, in response order, as a trimmed top-level
copy while retaining the complete raw job structure. Empty or whitespace-only
values, non-string values, malformed result structures, and timed-out
nonterminal jobs add no projection; duplicate entries remain visible in the
raw job. A pre-existing top-level `stdOut` is retained and prevents the
convenience projection from overwriting it. An asynchronous submission remains
unchanged because no operation result exists yet.

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
- **Add separate update and upgrade tools.** judgment: issue #397 and the frozen
  campaign scope retain the consolidated `kind` entry point, and both operations
  can preserve ADR 0012's stable outer result contract; splitting the tool adds
  public surface without helping this correction.
- **Leave the implementation unchanged.** verified: neither Power10 nor
  Power11 reference captures define bare `Update` or `Upgrade` VIOS jobs, so
  the current requests cannot implement the documented operations.
