# CLI guide

[Documentation index](index.md) · [CLI quick start](../README.md#cli-quick-start)

Configure a connection first using the [configuration guide](configuration.md).
Use `hmcpctl --help` and `hmcpctl <group> --help` to discover commands and options.
The [HMC CLI cheatsheet](hmc-cli-cheatsheet.md) covers the underlying IBM commands
used over SSH; this guide covers the `hmcpctl` application.
The [bare-CEC LPAR recipe](recipes/bare-cec-lpar.md) walks a dedicated-I/O partition from
create to delete.

## CLI usage

```bash
hmcpctl console info                 # connectivity check / HMC version
hmcpctl systems list                 # table of managed systems
hmcpctl systems show <uuid>
hmcpctl systems summary <uuid>       # one-call summary: state, MTMS, firmware, LPARs, free resources
hmcpctl systems health               # issue-only fleet health; add --json for automation
hmcpctl lpars list                   # all LPARs
hmcpctl lpars list --system <uuid>   # LPARs of one system
hmcpctl lpars show mylpar            # by name or UUID (JSON)
hmcpctl lpars state mylpar           # just "running", "not activated", ...
hmcpctl lpars summary mylpar         # one-call summary: state, RMC, memory, CPU, OS, adapters
hmcpctl lpars get-minimum-affinity-policy mylpar sys1 --json
hmcpctl lpars system-memopt-score sys1
hmcpctl lpars plan-memopt-scores sys1 --prioritize-name web --exclude-name batch
hmcpctl lpars plan-system-memopt-score sys1 --prioritize-id 3 --exclude-id 9 --json
hmcpctl lpars create web01 --system <uuid> --mem 8192 --vcpus 2 --procs 0.2
hmcpctl lpars modify web01 --mem 16384 --procs 2.0   # assign resources
hmcpctl lpars delete web01           # destroy (must be powered off)
hmcpctl lpars decommission web01 --system <uuid> --dry-run   # preview blast radius
hmcpctl lpars power-on mylpar        # submits a PowerOn job (asks first)
hmcpctl lpars power-off mylpar --immediate
hmcpctl adapters list mylpar                    # network adapters (default type)
hmcpctl adapters list mylpar --type VirtualSCSIClientAdapter
hmcpctl adapters add-network mylpar --vlan 100  # add a NIC on VLAN 100
hmcpctl adapters add-vscsi mylpar --vios-id 1 --vios-slot 5
hmcpctl adapters add-vfc mylpar --vios-id 1 --vios-slot 6
hmcpctl adapters delete mylpar --type ClientNetworkAdapter --uuid <adapter-uuid>
hmcpctl vios list
hmcpctl jobs list                    # recent jobs (default 20)
hmcpctl jobs list -n 5               # last 5 jobs
hmcpctl jobs show <job-uuid>
hmcpctl raw get /rest/api/uom/VirtualSwitch   # escape hatch, prints XML
```

Add `--json` to list commands for machine-readable output. Composite workflows
such as `hmcpctl lpars decommission` and `hmcpctl lpars provision` return a
stable envelope with `workflow_completed`, ordered `steps`, `warnings`, and the
blast-radius or provisioning summary, which is friendlier for automation than
raw HMC payloads. Every entry is the parsed uom resource:
`{UUID, title, link, ResourceType, Resource}` where `Resource` is the flattened
XML (namespace-stripped, HMC bookkeeping attributes removed).

The memory-affinity planning commands are read-only `lsmemopt` calculations.
Repeat `--prioritize-name`, `--prioritize-id`, `--exclude-name`, or `--exclude-id`
to describe a scenario, using names or IDs consistently. Calculated scores are
predictions, not guarantees of placement, and these commands never start optimization.

The minimum-affinity policy command is also read-only. It first checks whether the managed
system advertises `POWER11` processor compatibility, then explicitly requests
`min_affinity_score` and `min_affinity_score_action` through `lssyscfg`. The score is validated
as an integer from 0 through 100 and the action as `none`, `warn`, or `fail`. Systems without
that capability remain usable and return an actionable `capability-unavailable` reason. This
surface does not provide a setter; portable snapshots record the policy only when supported.

Portable snapshots capture one named LPAR profile together with source identity and separate
timestamped placement and affinity observations:

```bash
hmcpctl snapshot capture sys1 aix1 default --output aix1.snapshot.json
hmcpctl snapshot validate aix1.snapshot.json
hmcpctl snapshot inspect aix1.snapshot.json
```

Capture refuses to overwrite an existing local file. Validation and inspection are local and
perform no HMC I/O. Snapshots do not expose a replay command; observation data is diagnostic and
never part of the replayable profile configuration.

`hmcpctl lpars decommission` enforces the ADR 0011 ownership token even for
`--dry-run`; use `--ownership-override` only after explicit operator approval.

### End-to-end: give an LPAR a bootable disk

For the complete ISO provisioning and optical-boot lifecycle, see the [LPAR ISO installation recipe](recipes/lpar-iso-install.md).

```bash
# 1. create the partition
hmcpctl lpars create web01 --system <sys-uuid> --mem 8192 --vcpus 2 --procs 0.2

# 2. give it a vSCSI adapter paired to the VIOS (find IDs via `vios list`)
hmcpctl adapters add-vscsi web01 --vios-id 1 --vios-slot 5

# 3. carve a virtual disk out of a VIOS volume group
hmcpctl storage list-vgs <vios-uuid>                       # find the VG + free space
hmcpctl storage create-disk <vios-uuid> --vg <vg-uuid> --name web01_root --capacity-mib 51200

# 4. map the disk to the partition
hmcpctl storage map <vios-uuid> --lpar web01 --disk web01_root

# 5. (optionally) network + power on
hmcpctl adapters add-network web01 --vlan 100
hmcpctl lpars power-on web01
```

> **Note on the storage model**: an LPAR's vSCSI/vFC *adapter* (added with
> `adapters add-vscsi` / `add-vfc`) is just plumbing — it pairs the partition
> with a VIOS server slot. The actual *disk* lives on the VIOS or in a Shared
> Storage Pool: carve it out of a Volume Group (`storage create-disk`) or a
> Cluster/SSP (`cluster create-lu`), then connect it with a mapping
> (`storage map`). Both the VIOS **Volume Group / Virtual Disk** model and the
> **Cluster / SSP Logical Unit** model are wrapped. Once a disk is mapped,
> partitioning it into filesystems is the guest OS's job (NIM, cloud-init,
> `mkfs`), not the HMC's.
