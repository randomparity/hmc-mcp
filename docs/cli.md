# CLI guide

[Documentation index](index.md) · [CLI quick start](../README.md#cli-quick-start)

Configure a connection first using the [configuration guide](configuration.md).
Use `hmc-mcp --help` and `hmc-mcp <group> --help` to discover commands and options.
The [HMC CLI cheatsheet](hmc-cli-cheatsheet.md) covers the underlying IBM commands
used over SSH; this guide covers the `hmc-mcp` application.

## CLI usage

```bash
hmc-mcp console info                 # connectivity check / HMC version
hmc-mcp systems list                 # table of managed systems
hmc-mcp systems show <uuid>
hmc-mcp systems summary <uuid>       # one-call summary: state, MTMS, firmware, LPARs, free resources
hmc-mcp systems health               # exception-only fleet health; add --json for automation
hmc-mcp lpars list                   # all LPARs
hmc-mcp lpars list --system <uuid>   # LPARs of one system
hmc-mcp lpars show mylpar            # by name or UUID (JSON)
hmc-mcp lpars state mylpar           # just "running", "not activated", ...
hmc-mcp lpars summary mylpar         # one-call summary: state, RMC, memory, CPU, OS, adapters
hmc-mcp lpars get-minimum-affinity-policy mylpar sys1 --json
hmc-mcp lpars system-memopt-score sys1
hmc-mcp lpars plan-memopt-scores sys1 --prioritize-name web --exclude-name batch
hmc-mcp lpars plan-system-memopt-score sys1 --prioritize-id 3 --exclude-id 9 --json
hmc-mcp lpars create web01 --system <uuid> --mem 8192 --vcpus 2
hmc-mcp lpars modify web01 --mem 16384 --procs 2.0   # assign resources
hmc-mcp lpars delete web01           # destroy (must be powered off)
hmc-mcp lpars decommission web01 --system <uuid> --dry-run   # preview blast radius
hmc-mcp lpars power-on mylpar        # submits a PowerOn job (asks first)
hmc-mcp lpars power-off mylpar --immediate
hmc-mcp adapters list mylpar                    # network adapters (default type)
hmc-mcp adapters list mylpar --type VirtualSCSIClientAdapter
hmc-mcp adapters add-network mylpar --vlan 100  # add a NIC on VLAN 100
hmc-mcp adapters add-vscsi mylpar --vios-id 1 --vios-slot 5
hmc-mcp adapters add-vfc mylpar --vios-id 1 --vios-slot 6
hmc-mcp adapters delete mylpar --type ClientNetworkAdapter --uuid <adapter-uuid>
hmc-mcp vios list
hmc-mcp jobs list                    # recent jobs (default 20)
hmc-mcp jobs list -n 5               # last 5 jobs
hmc-mcp jobs show <job-uuid>
hmc-mcp raw get /rest/api/uom/VirtualSwitch   # escape hatch, prints XML
```

Add `--json` to list commands for machine-readable output. Composite workflows
such as `hmc-mcp lpars decommission` and `hmc-mcp lpars provision` return a
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
hmc-mcp snapshot capture sys1 aix1 default --output aix1.snapshot.json
hmc-mcp snapshot validate aix1.snapshot.json
hmc-mcp snapshot inspect aix1.snapshot.json
```

Capture refuses to overwrite an existing local file. Validation and inspection are local and
perform no HMC I/O. Snapshots do not expose a replay command; observation data is diagnostic and
never part of the replayable profile configuration.

`hmc-mcp lpars decommission` enforces the ADR 0011 ownership token even for
`--dry-run`; use `--ownership-override` only after explicit operator approval.

### End-to-end: give an LPAR a bootable disk

```bash
# 1. create the partition
hmc-mcp lpars create web01 --system <sys-uuid> --mem 8192 --vcpus 2

# 2. give it a vSCSI adapter paired to the VIOS (find IDs via `vios list`)
hmc-mcp adapters add-vscsi web01 --vios-id 1 --vios-slot 5

# 3. carve a virtual disk out of a VIOS volume group
hmc-mcp storage list-vgs <vios-uuid>                       # find the VG + free space
hmc-mcp storage create-disk <vios-uuid> --vg <vg-uuid> --name web01_root --capacity-mib 51200

# 4. map the disk to the partition
hmc-mcp storage map <vios-uuid> --lpar web01 --disk web01_root

# 5. (optionally) network + power on
hmc-mcp adapters add-network web01 --vlan 100
hmc-mcp lpars power-on web01
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
