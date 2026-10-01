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
hmcpctl report utilization --csv fleet.csv   # CPU/memory allocation across every profile
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
hmcpctl adapters add-vscsi mylpar --vios-id 1 --vios-slot 5   # skip before storage map: it creates the pair
hmcpctl adapters add-vfc mylpar --vios-id 1 --vios-slot 6
hmcpctl adapters delete mylpar --type ClientNetworkAdapter --uuid <adapter-uuid>
hmcpctl vios list
hmcpctl jobs show <job-id>
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

A REST create gives the partition a current configuration, so no apply is needed and the
`apply_profile` step reports `skipped`. When the HMC creates the partition through `mksyscfg` (its
REST create was refused with HTTP 406 or a 400 `REST0001` payload rejection, or no memory or
processor value was given), `lpars create` then applies the new `default_profile` with `chsyscfg -o apply`, without powering
the partition on, and reports an `apply_profile` step. Until a profile is applied or the partition
is activated, it has no current configuration and REST adapter writes fail. `--no-apply` skips
the apply. Adapter changes made through REST after the apply live only in the current
configuration unless the partition's `CurrentProfileSync` is `On`. The adapter and mapping
commands print which after their result. `lpars power-on --partition-profile` warns once for
each current virtual SCSI, Fibre Channel or Ethernet client adapter the profile lacks, because
activating that profile removes it; the job is still submitted (#981).

A bounded console capture reads an LPAR's virtual console without sending it input:

```bash
hmcpctl lpars capture-console aix1 --system sys1 --duration 30 --output aix1.console.log
```

It stops at `--duration` seconds (at most 3600), `--max-bytes` (at most 1048576), or
`--idle-timeout` seconds of silence, writes the raw bytes to `--output` or to a redirected stdout,
and refuses an existing file or a terminal stdout. One stderr line reports the stop reason, byte
count and `released`. Exit codes ([ADR 0175](adr/0175-capture-console-exit-codes.md)): `0` the
capture finished and the console was released; `1` a lookup, SSH or HMC failure, a console held
by another session, a capture stopped by an error, or bytes that could not be written; `2` a
usage error; `3` the release was not proven, so run `rmvterm` on the HMC before another capture,
unless the line says another client ended the hold (`ConsoleHoldLostError`): leave that session.

`hmcpctl lpars decommission` enforces the ADR 0011 ownership token even for
`--dry-run`; use `--ownership-override` only after explicit operator approval.

### End-to-end: give an LPAR a bootable disk

For the complete ISO provisioning and optical-boot lifecycle, see the [LPAR ISO installation recipe](recipes/lpar-iso-install.md).

```bash
# 1. create the partition
hmcpctl lpars create web01 --system <sys-uuid> --mem 8192 --vcpus 2 --procs 0.2

# 2. carve a virtual disk out of a VIOS volume group
hmcpctl storage list-vgs <vios-uuid>                       # find the VG + free space
hmcpctl storage create-disk <vios-uuid> --vg <vg-uuid> --name web01_root --capacity-mib 51200

# 3. map the disk to the partition (the HMC creates the vSCSI adapter pair)
hmcpctl storage map <vios-uuid> --lpar web01 --disk web01_root

# 4. (optionally) network + power on
hmcpctl adapters add-network web01 --vlan 100
hmcpctl lpars power-on web01
```

> **Note on the storage model**: an LPAR's vSCSI/vFC *adapter* is just
> plumbing — it pairs the partition with a VIOS server slot. `storage map`
> creates its own vSCSI client/server adapter pair, so do not run
> `adapters add-vscsi` first; an adapter added that way is left unpaired.
> `lpars provision` and `storage attach-disk` rely on that pair too. The
> actual *disk* lives on the VIOS or in a Shared
> Storage Pool: carve it out of a Volume Group (`storage create-disk`) or a
> Cluster/SSP (`cluster create-lu`), then connect it with a mapping
> (`storage map`). Both the VIOS **Volume Group / Virtual Disk** model and the
> **Cluster / SSP Logical Unit** model are wrapped. Once a disk is mapped,
> partitioning it into filesystems is the guest OS's job (NIM, cloud-init,
> `mkfs`), not the HMC's.

## Fleet utilization report

`hmcpctl report utilization --csv PATH` reads every profile in `config.toml`, or only those
named with repeated `--profile NAME`, and writes one CSV of CPU and memory allocation. It only
reads; it changes nothing on any HMC. The accounting follows
[ADR 0184](adr/0184-fleet-utilization-accounting-model.md).

> **The report holds internal hostnames, system names and serial numbers. Never commit it or
> post it in a public place.** It is written with owner-only permissions.

The first column, `row_type`, says what each row is:

- `system`: one managed system. A system two HMCs manage appears once, with both profiles in
  `profiles`, and its most complete reading.
- `hmc`: the totals for one profile's HMC.
- `fleet`: the totals over the `system` rows.
- `failure`: a profile that could not be surveyed, with the reason in `notes`.

Each CPU (processor units) and memory (MiB) figure means:

- `installed`, `configurable`: the system's own totals.
- `vios`: the current configuration of the system's VIOS partitions.
- `client_active`: the current configuration of client partitions in any state except
  `not activated`.
- `idle_reserved`: the current configuration of `not activated` partitions. The hypervisor
  keeps it reserved, so it is allocated capacity that nothing is running on.
- `hypervisor` (memory only): memory the hypervisor itself uses.
- `other_reserved`: what remains of configurable capacity after free, the hypervisor and the
  partitions.
- `free`: what the system reports available.
- `allocated` and `util_pct`: configurable minus free, and that as a share of configurable.
- `dedicated`, `shared`, `shared_pools`: units held by dedicated and shared-processor
  partitions, and the shared pools in use.
- `profile_claims`, `profile_claim_mem_mib`, `profile_claim_cpu`: partitions that have never had
  a profile applied, and what their profiles would claim. The hypervisor reserves nothing for
  them, so these are not counted as allocated.

`unknown` means the HMC did not report a figure or a read failed; it is never written as 0. A
system row's `notes` names the failed read. A roll-up figure sums the systems that reported it,
and the roll-up row's `notes` names every column some of its systems lack, for example
`mem_vios_mib: 1 of 4 systems unknown`. A roll-up's percentage uses only the systems that
reported both configurable and free capacity.

The command surveys 4 profiles at a time (`--concurrency`) and gives each one 300 seconds
(`--hmc-timeout`) for logon and every read. A profile that runs out of time becomes a `failure`
row and keeps none of its readings. Ending its HMC session afterwards can take up to that
profile's `HMC_TIMEOUT` more. A system the HMC cannot list is absent from the report, with only
a warning on stderr. The CSV replaces `PATH` only once it is complete.

The command refuses to run when `HMC_HOST` is exported, or when a global connection option
(`--host`, `--user`, `--password`, `--verify-ssl`, `--profile`) is given. `HMC_HOST` would send
every profile to the same host, and the global options would be ignored.
