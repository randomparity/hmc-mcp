# ADR 0103: Explicit VIOS update and upgrade operations

## Status

Accepted (2026-08-27)

## Context

ADR 0074 kept VIOS updates and upgrades behind one `kind` selector even though the two
HMC jobs accept different request types. `UpdateVIOS` permits `IBMWebsite` and
`RestartVIOS`; `UpgradeVIOS` instead requires `Disks`. The combined Python and MCP
surfaces therefore advertised a union whose validity depended on a second argument,
and their implementations needed casts and runtime branching to recover the narrower
contract.

## Decision

Expose separate operations:

- `update_vios(..., repository: VIOSUpdateSource)` submits `UpdateVIOS`;
- `upgrade_vios(..., repository: VIOSUpgradeSource)` submits `UpgradeVIOS`.

The MCP surface follows the same split with `hmc_vios_update` and
`hmc_vios_upgrade`. Both retain the wait controls and terminal `stdOut` projection
defined by ADR 0074. This supersedes ADR 0074 only where it selected one consolidated
entry point; its request validation, job paths, and result contract remain in force.

## Consequences

Callers no longer combine a source union with a mode selector, and generated schemas
describe only valid operation/source pairs. The MCP tool count increases from 147 to
148. Existing access policies that should permit upgrades must grant
`hmc_vios_upgrade`; `hmc_vios_update` continues to mean software updates.

The reusable facade adds `upgrade_vios` and narrows `update_vios`; under ADR 0029 this
is a minor-version change during `0.x` and is recorded in the changelog manifest.
