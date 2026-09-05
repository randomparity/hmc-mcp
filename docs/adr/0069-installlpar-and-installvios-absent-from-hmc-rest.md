# ADR 0069: InstallLPAR and InstallVIOS Do Not Exist in the HMC REST API

## Status

Accepted

## Context

Issue #381 asks which parameters the HMC's `InstallLPAR` job actually accepts.
`install_lpar_job` / `install_vios_job` (`src/hmc_mcp/jobs.py:648-674` /
`:614`) send exactly six parameters — `nim_IP`, `nim_gateway`,
`nim_subnetmask`, `lpar_IP`/`vios_IP`, `vlanid`, `timeout` — and no record
explains where that set came from or whether the HMC accepts more. The issue
reserved ADR 0069 for the deliverable: a durable, firmware-anchored record of
the job parameter sets the HMC actually advertises.

A live-HMC survey was completed on 2026-08-22 (results recorded in the #381
comment thread). It answered a prior question the issue had left implicit:
before "which parameters does `InstallLPAR` accept", one must ask whether the
HMC advertises the operation at all. The answer is **no**, on every generation
and firmware level surveyed.

## Findings

### Surveyed versions (firmware-dependent facts)

Two HMCs, seven managed systems:

- **HMC V10R3 M1060** (`ltcvhmc1b`; managing P9 and P10): build level
  2408210051, MF71689 plus three iFix levels.
- **HMC V11R2 M1120** (`ltcvhmc11`; managing P9, P10, and P11): build level
  2607082225.

| Type-Model | Generation | Firmware |
|-----------|------------|----------|
| 8375-42A | Power9 | FW950.00 (level 39) |
| 9009-42A | Power9 | FW950.F0 (level 189) |
| 9080-M9S | Power10 | FW950.C1 (level 168) |
| 9080-HEX | Power10 | FW1110.01 (level 69) |
| 9105-22A | Power10 | fw1060.70 (level 170) |
| 9824-42A | Power10 | fw1110.00 (level 66) |
| 9043-MRU | Power11 | fw1110.11 (level 119) |

### Negative documentation finding

`InstallLPAR` and `InstallVIOS` are absent from both authoritative REST API
documentation sets:

- **Power10**: IBM Power10 HMC REST APIs (5765-VHP), captured 2025-03-26,
  topics `jobs-logicalpartition` and `jobs-virtualioserver`.
- **Power11**: IBM Power11 HMC REST APIs (9824-22A), captured 2026-08-22, last
  updated 2026-07-14; the What's-New history runs from publication through
  July 2026 with no install-job additions at any point.

The complete documented job inventories contain no install job. The P10
LogicalPartition list is ChangeDefaultProfileName,
ClearStatistics_SRIOVEthernetLogicalPort,
ClearStatistics_SRIOVFibreChannelOverEthernetLogicalPort,
GetNetworkBootDevices, Migrate, MigrateAbort, MigrateRecover, MigrateValidate,
PowerOff, PowerOn, SaveCurrentConfig — eleven jobs. The P10 VirtualIOServer
list is AddOpticalMedia, BackupIOConfig, BackupSSPConfig, BackupVIOS,
ChangeDefaultProfileName, ClearStatistics_SRIOVEthernetLogicalPort,
ClearStatistics_SRIOVFibreChannelOverEthernetLogicalPort, ConfigDevice,
FailoverVNICBackingDevices, GetFreePhysicalVolumes, GetNetworkBootDevices,
PowerOff, PowerOn, PrepareMaintenance, ValidateMaintenanceReadiness,
RenameVirtualTargetDevice, RestoreIOConfig, RestoreSSPConfig,
SaveCurrentConfig, UpdateVIOS, UpgradeVIOS — twenty-one jobs. The P11
LogicalPartition list is identical to P10; its VirtualIOServer list adds three
jobs — PerformVIOSCommand, ReadinessCheckForUpdate,
ReadinessCheckForUpgrade — none of which are install jobs. A later independent
audit of both doc sets confirmed zero occurrences of `InstallLPAR`,
`InstallVIOS`, `do/InstallLPAR`, `do/InstallVIOS`, `nim_IP`, `nim_gateway`, or
`nim_subnetmask`.

The closest documented substitute, `UpgradeVIOS`
(`/rest/api/uom/VirtualIOServer/{UUID}/do/UpgradeVIOS`), installs a VIOS image
from HMC/NFS/SFTP/USB onto free physical volumes named by a `Disks` parameter
— an image-based install, not a NIM boot, so not a drop-in substitute.

### Live probe results

PUT probes against both HMCs, across P9, P10, and P11 managed systems, in both
Not Activated and Running partition states, all return the same error:

```
HTTP 400
ReasonCode: The request body contains an unrecognized field.
Message: REST0006 No such Operation
```

for every attempt against `/rest/api/uom/LogicalPartition/{uuid}/do/InstallLPAR`
and `/rest/api/uom/VirtualIOServer/{uuid}/do/InstallVIOS`. Probe matrix as
recorded on #381: V10R3 M1060 against P9 (8375-42A, Not Activated,
`do/InstallLPAR` and `do/InstallVIOS`) and P10 (9080-M9S, Not Activated,
`do/InstallLPAR`); V11R2 M1120 against P11 (9043-MRU, Not Activated and
Running states, `do/InstallLPAR`) — all REST0006.

Because the operations do not exist, there are no `JobParameter` elements to
enumerate: the E4 parameter table, the E5 required-no-default /
optional-useful classification, and the E6 default comparison from the survey
plan are all not-applicable.

## Decision

ADR 0069 records the negative result: **the HMC REST API does not advertise
`InstallLPAR` or `InstallVIOS` at any surveyed firmware level** (V10R3 M1060,
V11R2 M1120; FW950.x through fw1110.x; Power9, Power10, Power11). No
follow-up issues for optional-useful parameters are warranted — there are no
parameters to classify. This ADR supersedes nothing.

**Provenance of the six-parameter set.** The parameters hmc-mcp sends appear
to have been sourced from the HMC CLI `installios` command's NIM parameters
(`nim_IP`, gateway, netmask, client IP, VLAN). That CLI command wraps the NIM
workflow but has no REST equivalent; the REST endpoint those names target
either never existed in the REST API or was removed before the Power10
documentation baseline. No IBM REST API reference confirming it ever existed
has been found.

**What the REST layer does support.** The documented `PowerOn` job accepts
`OperationType=netboot` together with `SlotPhysicalLocationCode`, `SubnetMask`,
`Gateway`, and `VLAN` (Power10 REST API reference). That kicks a network boot
from a specific adapter slot but does not drive the NIM install handshake —
that remains entirely on the NIM master. Any OS-install automation therefore
needs NIM-master credentials regardless of what the HMC is asked to do.

**Tool disposition is out of scope here.** `hmc_install_vios_by_lpar_selector`
(`src/hmc_mcp/server_tools/vios.py:193`, endpoint `:253`) and `hmc_install_vios`
(`src/hmc_mcp/server_tools/vios.py:127`, endpoint `:182`) POST to operations that no
surveyed HMC advertises — they are phantom tools, not under-parameterized
ones. Their removal or rework is tracked in issue #410 (operator decision
pending); this ADR deliberately changes no source code.

## Consequences

- Issue #381's question is answered negatively and can close once this record
  lands.
- ADR 0005 (`hmc_provision_lpar`) was checked for assumptions about REST
  install jobs: it composes create/adapter/storage steps and ends with a plain
  `PowerOn`, referencing no install job — unaffected by this finding.
- The undocumented-endpoint caveat stands: absence from the doc sets does not
 *prove* nonexistence (the package itself relies on undocumented
  `/rest/api/web/File/`), which is why the live PUT probes returning REST0006
  on both HMCs are the load-bearing evidence.
- Until #410 resolves, `hmc_install_vios_by_lpar_selector` and `hmc_install_vios` will fail
  at runtime with `REST0006 No such Operation` on any surveyed HMC; callers
  should treat them as non-functional rather than mis-parameterized.
- OS-install workflows built on this package must retain NIM-master SSH
  credentials; the HMC's only REST role in that flow is the `PowerOn/netboot`
  kick.

## Considered & rejected

- **Record only the live-probe result, skipping the doc-set inventories.**
  judgment: the probes prove nonexistence only on the two surveyed machines;
  the complete documented job inventories (with capture dates) are what make
  the negative finding durable against future firmware levels — a new install
  job would surface as a documented addition, checkable without an HMC.
- **Treat `UpgradeVIOS` as a replacement path for `hmc_install_vios`.**
  judgment: it installs a VIOS image onto named free physical volumes, not a
  NIM network boot; substituting it would silently change the installation
  model. Any such rework belongs to #410's decision, not this record.
