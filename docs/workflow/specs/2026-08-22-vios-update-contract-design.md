# VIOS update and upgrade contract correction

## Goal and authority

Issue #397 requires `hmc_vios_update` to submit IBM's documented `UpdateVIOS`
and `UpgradeVIOS` jobs, use their documented parameters, retain fail-fast
validation, and expose the documented `stdOut` response. The campaign also
requires tests and user documentation and comparison with supplied Power10 and
Power11 reference captures. Live-HMC validation is attempted only when a safe
target and credentials are already available; its absence is reported rather
than engineered around.

ADR 0074 records the request-shape and result-projection decision. The issue
and frozen campaign scope retain the consolidated
`kind="update" | "upgrade"` entry point; its shared outer result remains
consistent with ADR 0012.

## Public contract

`hmc_vios_update(vios_name_or_uuid, repository, kind="update", wait=False,
timeout_seconds=300, poll_interval=5, profile=None)` remains the entry point.
`repository` is a VIOS-specific discriminated union whose discriminator is the
existing `kind` argument:

- Update accepts `ResourceType` values `HMC`, `NFS`, `SFTP`, `USB`, and
  `IBMWebsite`, plus optional `Name`, `ServerHostOrIP`, `UserName`, `Password`,
  `SSHKey`, `PassPhrase`, `RemoteDirectory`, `FileNames`, `MountLocation`,
  `MountOptions`, `USBDevice`, `SaveFile`, and `RestartVIOS`.
- Upgrade accepts `ResourceType` values `HMC`, `NFS`, `SFTP`, and `USB`, the
  same common optional fields, and `Disks`; it does not accept `RestartVIOS`.

`ResourceType` is required for both operations. Unknown parameter names,
`Disks` on update, `RestartVIOS` on upgrade, and `IBMWebsite` on upgrade raise
an actionable `ValueError` before client creation. IBM does not state universal
media-specific requirements for the remaining fields, so this change does not
invent them. Values are stringified and escaped by the existing XML builder.

Update builds `OperationName=UpdateVIOS` and submits to
`/rest/api/uom/VirtualIOServer/{encoded UUID}/do/UpdateVIOS`. Upgrade uses
`UpgradeVIOS` in both places. The resolved VIOS UUID is encoded as one URL path
segment before the fixed operation suffix is appended.

When `wait=False`, the tool returns the submission metadata unchanged. When
`wait=True`, it keeps the complete terminal job mapping. If
`Resource.Results.JobParameter` contains a mapping or list entry with
`ParameterName == "stdOut"` and a non-empty string `ParameterValue`, the
returned mapping also has top-level `stdOut` containing the trimmed first such
value in response order. Empty, non-string, malformed, or later duplicate
result entries do not add or replace that projection and do not hide the raw job.

## Components and data flow

`src/hmc_mcp/jobs.py` defines `VIOSUpdateSource` and `VIOSUpgradeSource`, their
runtime key/value sets, operation-specific validation, documented builders,
and a small result-parameter extractor. Existing `RepositorySource` remains
owned by firmware. `src/hmc_mcp/server_updates.py` selects the builder and
operation, validates before opening a client, encodes the resolved identifier,
submits through the shared wait lifecycle, and projects `stdOut` only from a
waited terminal result.

The Power10 captures
`140-updatevios_virtualioserver-job.md` and
`141-upgradevios_virtualioserver-job.md` and the Power11 captures
`160-updatevios_virtualioserver-job.md` and
`161-upgradevios_virtualioserver-job.md` agree on operation names, paths,
parameter sets, allowed resource types, and `stdOut`.

## Error handling

Validation order is `kind`, request shape, wait timing, client creation,
identifier resolution, submission, and optional polling. This keeps invalid
public inputs from reaching the HMC. Existing `HMCError` behavior for
submission and polling is unchanged. A terminal job with a malformed Results
shape remains observable in full and simply lacks the convenience projection.

## Trust boundaries and controls

- **Added boundary:** VIOS request dictionaries supplied by an authenticated
  local MCP caller enter job XML. Exact key sets, operation-specific enum
  checks, and the existing `build_job_request` escaping boundary control them.
- **Widened boundary:** the caller-supplied VIOS selector enters a REST path.
  The resolved UUID is percent-encoded as one path segment, matching the
  console update control introduced by #398.
- **Existing boundary:** an HMC-produced terminal job enters the result
  projection. Structural type checks accept only the exact `stdOut` name and a
  string value; the original payload remains available.

The trusted actor is the local operator authorized to invoke a destructive
tool. Anonymous and cross-tenant callers are outside this deployment model;
authorization policy remains owned by the existing tool registry. This change
does not conceal credentials placed in job parameters, add logging, change
transport security, provision a live target, or claim that client-side checks
replace HMC authorization.

## Verification

Focused tests must first fail on the old operation/path, old keys, schema,
operation-specific invalid inputs, path injection, and missing `stdOut`
projection. Builder tests then prove exact operation and parameter names;
application/system tests prove exact paths and validation-before-I/O; schema
tests prove the documented union; result tests cover list, singleton, empty,
and malformed result forms. README text names the documented contract and the
wait-only projection. `just test`, `just smoke`, and `just verify` must pass.

No live HMC is required to establish the static contract. If no suitable live
VIOS update target is available, the handoff states that live validation was
not run.
